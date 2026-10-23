"""Stability AI task provider — the main-side half of background generation.

Pairs with the fx-free worker (``_stability_worker.py``).  This side does all the
``fx`` work — preflight, render + scale the node's input image, resolve the
endpoint + arguments from ``stabilityai_config.json``, and (on completion) commit
the decoded PNGs into the node's ``AssetCatalog`` — while the worker only performs
the HTTP round-trips out-of-process.

The task NEVER mutates the object model.  ``on_result``
writes the sidecar catalog + raises the node's attention badge; the *user* picks an
asset (the Asset Browser's undoable ``selected`` write, or the *Use Latest*
completion action here).  No auto-select.
"""

import os
import json
import fx

from tasks import registry
from tools import extension
from . import _stability_worker as worker


MISSING_API_KEY_MESSAGE = (
    "Stability requires a Brand Studio account and an API key.  Add it via the "
    "<em>Options</em> button for the Stability AI extension on the "
    "<em>Preferences &gt; Extensions</em> page.")
MISSING_PROMPT_ERROR = "Please enter a prompt."

_WORKER = os.path.join(os.path.dirname(__file__), "_stability_worker.py")

# The REST host.  Not a preference: the endpoints and versions in
# stabilityai_config.json only mean anything against Stability's own service, so
# pointing the worker elsewhere is a development affordance, not a user setting.
API_HOST = "https://api.stability.ai"

# modes whose v2 endpoint params (from stabilityai_config.json) drive the args
_CLIP_PRESETS = ["FAST_BLUE", "FAST_GREEN", "NONE", "SIMPLE", "SLOW", "SLOWER", "SLOWEST"]


class StabilityProvider(object):
    name = "stability"
    max_concurrent = 2          # cloud service — a couple can run in parallel

    # -- pre-flight (no task created on failure; the caller presents the error) --
    def preflight(self, params, owner):
        if not self._api_key():
            raise Exception(MISSING_API_KEY_MESSAGE)
        mode = params.get("mode_index", 0)
        engine = params.get("engine", "")
        prompt = (params.get("prompt") or "").strip()
        if mode == 0:
            if not prompt:
                raise Exception(MISSING_PROMPT_ERROR)
        elif mode not in (3, 6):        # not erase / background-removal
            if engine != "stable-diffusion-xl-1024-v1-0" and not prompt:
                raise Exception(MISSING_PROMPT_ERROR)

    def ensure_deps(self):
        pass                            # worker is stdlib-only (urllib)

    # -- build: render input + resolve the request, write job.json --------------
    def build(self, params, store, owner):
        node = fx.findObject(owner)
        if node is None:
            raise Exception("The Stability node is no longer available.")

        mode = params["mode_index"]
        mode_name = params["mode_name"]
        engine = params["engine"]
        prompt = params.get("prompt") or ""
        if not prompt:
            prompt = " "                # inpaint/outpaint accept an empty prompt
        image_strength = params["image_strength"]

        inputs = os.path.join(store, "inputs")
        os.makedirs(inputs, exist_ok=True)
        init_rel, mask_rel = "inputs/init.png", "inputs/mask.png"
        init_path = os.path.join(store, init_rel)
        mask_path = os.path.join(store, mask_rel)

        sizes = [0, 512, 768, 1024]
        size_index = params["size_index"]
        max_size = fx.Size(sizes[size_index], sizes[size_index])

        # render the node's input through the graph (clean process boundary: the
        # worker never touches fx — it just uploads what we write here).
        input_size = None
        dod = None
        scaled = None
        input_image = node.render(params.get("frame", 0.0), port=node.port("input"))
        if input_image:
            session = node.session
            input_image = input_image.crop(fx.Recti(0, 0, session.size.width, session.size.height))
            d = input_image.dod
            input_size = (d.size.width, d.size.height)
            dod = [d.left, d.top, d.right, d.bottom]
            max_size = fx.Size(d.size.width, d.size.height)
        elif size_index == 0:
            s = node.session.size
            input_size = (s.width, s.height)
            max_size = fx.Size(s.width, s.height)

        if engine == "stable-diffusion-xl-1024-v1-0" and mode_name in ("generate", "inpaint", "outpaint"):
            w, h = worker.compute_image_size_xl((max_size.width, max_size.height))
            max_size = fx.Size(worker.round64(w), worker.round64(h))

        # base args
        args = {
            "clip_guidance_preset": params.get("cgp", "NONE"),
            "steps": params["steps"],
            "samples": params["samples"],
            "seed": params["seed"],
            "cfg_scale": min(params["prompt_strength"],
                             35 if engine == "stable-diffusion-xl-1024-v1-0" else 10),
        }

        files = {}
        if input_image:
            scaled = input_image.resize(max_size)
            scaled.save("PNG", init_path, channels=3)
            if mode != 0:
                scaled.save("PNG", mask_path, channels=1)
        elif mode_name == "generate":
            args["aspect_ratio"] = self._aspect(input_size)

        # resolve the command + endpoint params from the config
        cfg = self._config()
        mode_info = next((m for m in cfg.get("modes", []) if m.get("mode") == mode_name), None)
        if not mode_info:
            raise Exception("Mode not found in stabilityai_config.json: " + mode_name)
        command = mode_info.get("url")
        for item in cfg.get("models", []):
            if item.get("engine") == engine:
                if mode_name in item:
                    command = item[mode_name]
                    if isinstance(command, dict) and engine == "stable-diffusion-xl-1024-v1-0":
                        command = command["image-to-image"] if scaled else command["text-to-image"]
                if "custom" in item:
                    args.update(item["custom"])
        if not command:
            raise Exception("Command not found for %s / %s" % (mode_name, engine))

        version = 1 if (str(command).startswith("v1")
                        and mode_name in ("generate", "inpaint", "outpaint")) else 2
        transport = "multipart"
        samples = params["samples"]

        if version == 1:
            samples = 1                 # v1 returns all artifacts in one call
            if mode_name == "generate":
                if scaled:
                    files["init_image"] = init_rel
                    args["text_prompts[0][text]"] = prompt
                    args["image_strength"] = image_strength / 100.0
                else:
                    transport = "json"
                    args["width"] = max_size.width
                    args["height"] = max_size.height
                    args["text_prompts"] = [{"text": prompt}]
            elif mode_name == "inpaint":
                files["init_image"] = init_rel
                files["mask_image"] = mask_rel
                args["text_prompts[0][text]"] = prompt
                args["mask_source"] = "MASK_IMAGE_WHITE"
            elif mode_name == "outpaint":
                files["init_image"] = init_rel
                files["mask_image"] = mask_rel
                args["text_prompts[0][text]"] = prompt
                args["mask_source"] = "MASK_IMAGE_BLACK"
        else:                           # v2 (per-sample loop, always multipart)
            p = mode_info.get("params", [])
            if "image" in p:
                if mode_name == "generate":
                    if scaled:
                        args["mode"] = "image-to-image"
                        args["strength"] = image_strength / 100.0
                        files["image"] = init_rel
                    else:
                        args["none"] = ""       # v2 text-to-image still posts multipart
                else:
                    files["image"] = init_rel
            if "mask" in p:
                if mode_name == "outpaint" and scaled:
                    scaled.invert().save("PNG", mask_path, channels=1)
                files["mask"] = mask_rel
            if "prompt" in p:
                args["prompt"] = prompt
            if "negprompt" in p:
                args["negative_prompt"] = params.get("negprompt", "")
            if "selprompt" in p:
                sel = params.get("selectprompt", "")
                args["search_prompt" if mode_name == "replace" else "select_prompt"] = sel

        job = {"host": API_HOST, "command": command, "version": version,
               "transport": transport, "args": args, "files": files, "samples": samples}
        with open(os.path.join(store, "job.json"), "w") as f:
            json.dump(job, f, indent=2)

        # stash what on_result needs (in-memory params, never serialized)
        params["input_size"] = list(input_size) if input_size else None
        params["dod"] = dod
        params["mode_index"] = mode
        params["meta_base"] = {"prompt": params.get("prompt", ""),
                               "negprompt": params.get("negprompt", ""),
                               "model": engine, "mode": mode_name,
                               "seed": params["seed"], "steps": params["steps"]}

        env = {"SFX_STABILITY_KEY": self._api_key()}
        ca = self._ca_bundle()
        if ca:
            env["SFX_CA_BUNDLE"] = ca        # worker's stdlib ssl verifies against this
        return {"argv": [fx.pythonExecutable(), _WORKER, store],
                "runner": "worker", "env": env}

    # -- commit (main thread, Applying / render-depth 0) ------------------------
    def on_result(self, task):
        node = fx.findObject(task.params.get("owner_id"))
        if node is None:
            raise Exception("The Stability node is no longer available.")

        manifest = task.result or []
        p = task.params
        input_size = p.get("input_size")
        meta_base = dict(p.get("meta_base") or {})
        if p.get("dod") is not None:
            meta_base["dod"] = p["dod"]
        bg_removal = p.get("mode_index") == 6

        cat = fx.AssetCatalog(node)
        os.makedirs(cat.storeDir, exist_ok=True)     # may not exist yet on a node's first generate
        tmp = os.path.join(cat.storeDir, "_incoming.png")
        ids = []                                     # ids THIS task published (for Use Latest)
        for entry in manifest:
            rel = entry.get("file")
            src = os.path.join(task.store, rel) if rel else None
            if not src or not os.path.exists(src):
                continue
            img = fx.Source(src).getImage(0.0)
            if img is None:
                continue
            if bg_removal:
                img = img.premult()
            if input_size:
                img = img.resize(fx.Size(input_size[0], input_size[1]))
            img.save("PNG", tmp, channels=3)
            aid = cat.publish(tmp, meta=dict(meta_base), provider="stability")
            if aid:
                ids.append(aid)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        cat.save()
        p["asset_ids"] = ids                         # scope Use Latest to THIS task's results

        # credits readout (best-effort; transient prop, not object-model state)
        try:
            with open(os.path.join(task.store, "credits.json")) as f:
                credits = json.load(f).get("credits")
            if credits is not None:
                node.property("credits").value = "{:.2f}".format(credits)
        except Exception:
            pass

        # returning attention -> the task layer raises the owner node's badge
        # (TaskManager._raise_alert); the user then picks an asset in the browser.
        return bool(ids)

    # -- completion actions (user-invoked => undoable) --------------------------
    def actions(self, task):
        if not task.result:
            return []
        return [
            ("Show Results", lambda t=task: self._show(t)),
            ("Use Latest",   lambda t=task: self._use_latest(t)),
        ]

    # -- helpers ----------------------------------------------------------------
    def _api_key(self):
        return fx.buildPath(fx.prefs['ml.stability_api_key'])

    def _ca_bundle(self):
        """certifi's CA bundle path for the worker's stdlib ssl (the bundled
        interpreter has no usable default CA store on macOS).  certifi is present
        in the main process as a requests dependency; the worker can't rely on it
        being importable, so we resolve the path here and pass it via env."""
        try:
            from tools.ensure import ensure
            certifi = ensure("certifi", required=False)
            return certifi.where() if certifi else None
        except Exception:
            return None

    def _config(self):
        with open(extension.path(__file__, "stabilityai_config.json")) as f:
            return json.load(f)

    @staticmethod
    def _aspect(input_size):
        valid = {2.33: "21:9", 1.78: "16:9", 1.5: "3:2", 1.25: "5:4", 1.0: "1:1",
                 0.8: "4:5", 0.67: "2:3", 0.56: "9:16", 0.43: "9:21"}
        if not input_size:
            return "1:1"
        ar = round(input_size[0] / input_size[1], 2)
        if ar in valid:
            return valid[ar]
        keys = list(valid.keys())
        if ar > keys[0]:
            return valid[keys[0]]
        if ar < keys[-1]:
            return valid[keys[-1]]
        return min(valid.items(), key=lambda kv: abs(kv[0] - ar))[1]

    def _show(self, task):
        node = fx.findObject(task.params.get("owner_id"))
        if node is not None:
            node.alert = False          # user attended to the result -> clear the badge
            fx.activate(node)
            fx.showView('assetBrowser')

    def _use_latest(self, task):
        node = fx.findObject(task.params.get("owner_id"))
        if node is None:
            return
        node.alert = False              # user attended to the result -> clear the badge
        # this task's OWN last result — not the catalog's global newest, which
        # could belong to a different generation that finished for this node.
        ids = task.params.get("asset_ids") or []
        newest = ids[-1] if ids else fx.AssetCatalog(node).newestId
        if newest:
            fx.beginUndo("Use Stability Result")
            try:
                fx.activate(node)
                node.property("selected").value = newest
            finally:
                fx.endUndo()


# Registered from the Stability extension: the loader auto-tags the owning
# extension id, so this provider is dropped when the extension is disabled/removed.
registry.register(StabilityProvider())
