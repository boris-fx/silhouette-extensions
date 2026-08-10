"""GenAI task provider — the main-side half of the pipeline.

Pairs with the fx-free worker (``_genai_worker.py``).  This side does all the
``fx`` work:

- ``build()`` **renders the node's input to a temp movie on the main thread**
  (``genai_render.render_movie`` — a normal blocking progress bar), writes
  ``job.json``, and hands off to the worker.  Everything slow-but-async
  (tunnel, submit, poll, download) happens there.
- ``on_result()`` commits the downloaded MP4s into the node's AssetCatalog
  (``publish()`` infers type=movie/fps/frames and posters the thumbnail) and
  surfaces the simulated token cost.  A task never mutates the object model —
  the *user* picks an asset.

Adapting to a real service: this file barely changes — the API key/server
prefs get re-labeled, and any service-specific request arguments are added to
``job["args"]`` here (they pass straight through to the worker's adapter).
"""

import os
import json
import fx

from tasks import registry
from tools import extension
from genai import worker_env         # PYTHONPATH so the worker imports genai.*

from . import genai_render


MISSING_API_KEY_MESSAGE = (
    "The GenAI emulator wants an API key (any non-empty value) so the real "
    "auth plumbing is exercised.  Add one via the <em>Options</em> button for "
    "the GenAI Emulator extension on the <em>Preferences &gt; Extensions</em> "
    "page.")
MISSING_INPUT_ERROR = "Connect an image to the input — it becomes the video to process."

_WORKER = os.path.join(os.path.dirname(__file__), "_genai_worker.py")


class GenAIProvider(object):
    name = "genai"
    max_concurrent = 3
    # The worker checkpoints its store (state.json: media_url/task_ids/done) and
    # re-attaches to the server-side task ids on relaunch, so a stopped task
    # resumes without re-submitting — pause/resume and crash recovery both ride
    # on it.  NOTE: with the default per-run spawned emulator "the server side"
    # dies with the worker; run _genai_service.py standalone and set the Server
    # option to exercise real resume semantics (see the worker docstring).
    resumable = True

    # -- pre-flight (no task created on failure; the caller presents the error) --
    def preflight(self, params, owner):
        if not self._api_key():
            raise Exception(MISSING_API_KEY_MESSAGE)

    def ensure_deps(self):
        pass                            # worker is stdlib-only (urllib)

    # -- build: render the input movie (main thread) + write job.json -----------
    def build(self, params, store, owner):
        node = fx.findObject(owner)
        if node is None:
            raise Exception("The GenAI node is no longer available.")
        port = node.port("input")
        if port is None or not port.connected:
            raise Exception(MISSING_INPUT_ERROR)

        caps = self._model(params.get("model_id", ""))
        start = float(params.get("start", 0.0))
        end = float(params.get("end", start))

        # clamp the range to the model's clip limit (like a real service's
        # duration bounds) — generation cost scales with length.
        fps = float(node.session.frameRate or 24.0)
        max_frames = max(1, int(round(float(caps.get("max_seconds", 15)) * fps)))
        end = min(end, start + max_frames - 1)

        # ---- stage 1: render out (main thread, blocking progress; the user
        # can cancel — that aborts the submit silently via RenderCanceled).
        # NOTE the .mov extension: the movie writer's host contract requires
        # the module's default extension (see genai_render.render_movie); the
        # container written is ISO/MPEG-4-compatible, which video services
        # accept as an upload.
        inputs = os.path.join(store, "inputs")
        os.makedirs(inputs, exist_ok=True)
        video_rel = "inputs/input.mov"
        info = genai_render.render_movie(node, port, os.path.join(store, video_rel),
                                         start, end)

        cfg = self._config().get("service", {})
        job = {"service": {"url": self._server(),
                           "gen_seconds": cfg.get("gen_seconds", 6.0)},
               "prompt": (params.get("prompt") or "").strip(),
               "model": caps.get("id", ""),
               "args": {},                       # service-specific extras go here
               "input": video_rel,               # engine reads job["input"]
               "samples": max(1, int(params.get("samples", 1))),
               "poll_interval": cfg.get("poll_interval", 2.0),
               "timeout": cfg.get("timeout", 900.0),
               "tunnel": cfg.get("tunnel", "auto"),
               "cloudflared": fx.buildPath(fx.prefs['genai.cloudflared'])}
        with open(os.path.join(store, "job.json"), "w") as f:
            json.dump(job, f, indent=2)

        # what on_result stamps into each published asset.  "frame" anchors the
        # clip on the timeline: the render came from [start, end], so the node
        # plays the result exactly over those frames.
        params["meta_base"] = {"prompt": job["prompt"],
                               "model": caps.get("id", ""),
                               "frame": info["start"],
                               "source_frames": info["frames"],
                               "source_fps": info["fps"]}

        # same env derivation as a resume relaunch (secrets ride the process env,
        # never job.json/task.json; SFX_CA_BUNDLE for the worker's stdlib ssl)
        return {"argv": self.resume_argv(params, store),
                "runner": "worker", "env": self.resume_env(params)}

    # -- commit (main thread, Applying / render-depth 0) ------------------------
    def on_result(self, task):
        node = fx.findObject(task.params.get("owner_id"))
        if node is None:
            raise Exception("The GenAI node is no longer available.")

        manifest = task.result or []
        meta_base = dict(task.params.get("meta_base") or {})

        cat = fx.AssetCatalog(node)
        os.makedirs(cat.storeDir, exist_ok=True)
        store_root = os.path.realpath(task.store)
        ids = []
        failures = []
        for i, entry in enumerate(manifest):
            rel = entry.get("file")
            src = os.path.realpath(os.path.join(task.store, rel)) if rel else ""
            # confine to the task store — reject a malformed/absolute manifest path
            try:
                inside = bool(rel) and os.path.commonpath([store_root, src]) == store_root
            except ValueError:
                inside = False
            if not inside:
                failures.append("unsafe result path: %r" % rel)
                continue
            if not os.path.isfile(src):
                failures.append("missing result file: %s" % rel)
                continue
            meta = dict(meta_base)
            for key in ("task_id", "tokens"):
                if entry.get(key) is not None:
                    meta[key] = entry[key]
            # deterministic id: a held/retried publish replaces the same asset
            # instead of duplicating it; built
            # from the SAFE uid+index, never a server-supplied string (the id
            # becomes a store filename)
            aid_key = "%s_%d" % (getattr(task, "uid", "") or task.id, i)
            try:
                aid = cat.publish(src, meta=meta, provider="genai",
                                  id="ga_" + aid_key)
            except Exception as e:
                failures.append("publish failed for %s: %s" % (rel, e))
                continue
            # publish() doesn't reliably surface a copy failure — confirm the
            # asset actually landed on disk before counting it.
            path = cat.assetPath(aid) if aid else None
            if aid and path and os.path.exists(path):
                ids.append(aid)
            else:
                failures.append("published asset for %s is not on disk" % rel)
        if not cat.save():
            raise Exception("GenAI: could not save the asset catalog "
                            "(disk/permission error).")
        task.params["asset_ids"] = ids

        try:
            tokens = sum(int(e.get("tokens") or 0) for e in manifest)
            if tokens:
                node.property("tokens").value = "{:,} (simulated)".format(tokens)
        except Exception:
            pass

        # ANY unpublished result is a failure — raising makes the manager HOLD
        # the store and retry the whole manifest next open (idempotent ids).
        if failures:
            raise Exception("GenAI: %d of %d results could not be published — %s"
                            % (len(failures), len(manifest), "; ".join(failures[:3])))

        # returning attention -> the task layer raises the owner node's badge;
        # the user then picks an asset in the Asset Browser.
        return bool(ids)

    # -- completion actions (user-invoked => undoable) --------------------------
    def actions(self, task):
        if not task.result:
            return []
        return [
            ("Show Results", lambda t=task: self._show(t)),
            ("Use Latest",   lambda t=task: self._use_latest(t)),
        ]

    # -- resume (recovery / un-pause relaunch) -----------------------------------
    def resume_env(self, params):
        """The secrets env for a relaunch — re-derived, never persisted.  Raises
        if the key is gone (the manager then surfaces the task as Interrupted)."""
        key = self._api_key()
        if not key:
            raise Exception("The GenAI emulator API key is no longer configured.")
        env = {"SFX_GENAI_KEY": key}
        ca = self._ca_bundle()
        if ca:
            env["SFX_CA_BUNDLE"] = ca        # worker's stdlib ssl verifies against this
        env.update(worker_env())             # resources/scripts on the worker's PYTHONPATH
        return env

    def resume_argv(self, params, store):
        """A fresh worker argv for a relaunch — module-relative, so it survives
        an app update or extension move that would stale a persisted argv."""
        return [fx.pythonExecutable(), _WORKER, store]

    # -- helpers ----------------------------------------------------------------
    def _api_key(self):
        return fx.buildPath(fx.prefs['genai.api_key'])

    def _server(self):
        return fx.buildPath(fx.prefs['genai.server'])     # "" -> spawn the emulator

    def _ca_bundle(self):
        try:
            from tools.ensure import ensure
            certifi = ensure("certifi", required=False)
            return certifi.where() if certifi else None
        except Exception:
            return None

    def _config(self):
        with open(extension.path(__file__, "genai_config.json")) as f:
            return json.load(f)

    def _model(self, model_id):
        models = self._config().get("models", [])
        for m in models:
            if m.get("id") == model_id:
                return m
        return models[0] if models else {}

    def _show(self, task):
        node = fx.findObject(task.params.get("owner_id"))
        if node is not None:
            node.alert = False
            fx.activate(node)
            fx.showView('assetBrowser')

    def _use_latest(self, task):
        node = fx.findObject(task.params.get("owner_id"))
        if node is None:
            return
        node.alert = False
        ids = task.params.get("asset_ids") or []
        newest = ids[-1] if ids else fx.AssetCatalog(node).newestId
        if newest:
            fx.beginUndo("Use GenAI Result")
            try:
                fx.activate(node)
                node.property("selected").value = newest
            finally:
                fx.endUndo()


# Registered from the GenAI extension: the loader auto-tags the owning
# extension id, so this provider is dropped when the extension is disabled.
registry.register(GenAIProvider())
