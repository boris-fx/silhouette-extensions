"""PyStabilityAI — the Stability AI image-generation node.

Generation runs **in the background**: `on_submit()` gathers the node's
parameters and submits a `stability` task to Silhouette's task manager
(`tasks`), then returns immediately.  The provider (`stability_provider.py`)
renders the input, runs the HTTP in an fx-free worker process
(`_stability_worker.py`), and on completion commits the results into this
node's asset catalog and raises the node's attention badge — the user then
picks an asset in the Asset Browser (the task never auto-selects).

The node:

  * Ports — an optional/aux IMAGE `input` and an IMAGE `output`.
  * Properties driven by this extension's `stabilityai_config.json`: the
    `mode`/`model` enums, prompts, per-mode strength sliders, size/steps/seed/
    samples, a Generate button, and a transient Credits readout.
  * `process()` reads the selected asset off disk, reframes it to the stored
    input DOD, scales to the render proxy, and optionally composites it with the
    input for inpaint/outpaint.

Generated results are tracked by a shared **asset catalog** (`fx.AssetCatalog`,
the manifest) instead of a bare PNG scan: each result is published with rich
metadata (prompt/model/seed/mode/resolution + the input DOD for reframing) into
`assets/stability/<node.id>/manifest.json` in the project bundle.  The `selected`
property holds the chosen asset's **string id** (stable across delete/reorder),
and the Asset Browser dock reads the same catalog to show thumbnails + write the
selection.  The catalog is a self-contained model, not a node property, so a
background task can update it without touching undo or the project-dirty flag.

Stores from early versions (bare `stability/<node.id>/NNNN.png`) are migrated
into the catalog on first access (`_migrate_legacy`).

When the input is RGBA and Composite is *off*, the result keeps the asset's own
channels; only the Composite path (inpaint/outpaint) carries the input's alpha.
"""

import fx
from fx import *

from pathlib import Path
import json

from tools import extension


# ---------------------------------------------------------------------------
# config (the mode/model tables + per-mode capability flags) — loaded once
# ---------------------------------------------------------------------------

# capability flags parsed from each mode's "params" list; they drive which
# fields are enabled per mode.
_FLAG_PROMPT    = 0x001
_FLAG_NEGPROMPT = 0x002
_FLAG_SELPROMPT = 0x004
_FLAG_IMAGE     = 0x008
_FLAG_MASK      = 0x010
_FLAG_GENSTR    = 0x020
_FLAG_PAINTSTR  = 0x040
_FLAG_COMPOSITE = 0x080

_PARAM_FLAGS = {
    "prompt":    _FLAG_PROMPT,
    "negprompt": _FLAG_NEGPROMPT,
    "selprompt": _FLAG_SELPROMPT,
    "image":     _FLAG_IMAGE,
    "mask":      _FLAG_MASK,
    "genstr":    _FLAG_GENSTR,
    "paintstr":  _FLAG_PAINTSTR,
    "composite": _FLAG_COMPOSITE,
}

_CONFIG = None


def _load_config():
    """Read stabilityai_config.json once; return (modes, models, mode_flags).

    Falls back to a single "ERROR - Missing Config" entry on any failure, so the
    node still constructs."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    modes, models, flags = [], [], []
    try:
        path = extension.path(__file__, "stabilityai_config.json")
        with open(path) as f:
            cfg = json.load(f)
        modes = cfg.get("modes", [])
        models = cfg.get("models", [])
        for m in modes:
            flag = 0
            for param in m.get("params", []):
                flag |= _PARAM_FLAGS.get(param, 0)
            flags.append(flag)
    except Exception as e:
        print(f"[PyStabilityAI] could not read stabilityai_config.json: {e}")

    if not modes or not models:
        modes = [{"label": "missing", "mode": "missing"}]
        models = [{"label": "ERROR - Missing Config", "engine": "missing"}]
        flags = [0]

    _CONFIG = (modes, models, flags)
    return _CONFIG


class PyStabilityAI(NodeProxy):
    """Generate / edit images with Stability AI, in Python (background task)."""

    # Results (asset catalog: $(SFX_PROJECT_ROOT)/stability/...) and task state
    # live INSIDE the project bundle, so the project must be saved before
    # Generate can run — same gate as Paint (PythonNode reads this class attribute; Session/Project aggregate it into
    # the existing save-first prompts).
    requiresBundle = True

    def __init__(self, *args, **kwargs):
        modes, models, flags = _load_config()
        self._modes = modes
        self._models = models
        self._mode_flags = flags

        mode_labels = [m.get("label", m.get("mode", "")) for m in modes]
        model_labels = [m.get("label", m.get("engine", "")) for m in models]

        # per-mode default model index (index of the mode's "default" engine in
        # the model list) — used as the model default.  Changing the mode does
        # not reset the model; the user picks it.
        self._model_defaults = []
        for m in modes:
            default_engine = m.get("default", "")
            idx = next((i for i, mm in enumerate(models)
                        if mm.get("engine") == default_engine), 0)
            self._model_defaults.append(idx)

        # ---- ports: optional/aux image input, cached image output ----
        self.input  = self.addInput("input", "Input", aux=True)
        self.output = self.addOutput("output", "Output", cache=True)

        # ---- properties ----
        # mode is supervised so updatePropertyState() runs on change and toggles
        # which fields are enabled for the selected mode.
        self.addProperty("mode",  {"label": "Mode",  "items": mode_labels,
                                    "value": 0, "supervise": True})
        self.addProperty("model", {"label": "Model", "items": model_labels,
                                   "value": self._model_defaults[0] if self._model_defaults else 0})

        self.addProperty("prompt",       {"label": "Prompt",          "value": ""})
        self.addProperty("negprompt",    {"label": "Negative Prompt", "value": ""})
        self.addProperty("selectprompt", {"label": "Select Prompt",   "value": ""})

        self.addProperty("credits", {"label": "Credits", "value": "",
                                     "type": "label", "transient": True})

        self.addProperty("generate", {"label": "Generate", "type": "button",
                                      "callback": self.on_submit})

        self.addProperty("composite", {"label": "Composite", "value": False})

        # options group
        self.addProperty("options", {"label": "Options", "type": "group", "expand": True})
        self.addProperty("size", {"label": "Max Size", "items": ["Auto", "512", "768", "1024"],
                                  "value": 0, "group": "options"})
        self.addProperty("cgp", {"label": "CLIP Guidance Preset",
                                 "items": ["FAST_BLUE", "FAST_GREEN", "NONE", "SIMPLE",
                                           "SLOW", "SLOWER", "SLOWEST"],
                                 "value": 0, "group": "options", "hidden": True})
        self.addProperty("prompt_strength", {"label": "Prompt Strength", "value": 9.0,
                                             "range": [0.0, 35.0], "constant": True,
                                             "group": "options"})
        self.addProperty("generate_image_strength", {"label": "Image Strength", "value": 60.0,
                                                     "range": [0.0, 100.0], "constant": True,
                                                     "group": "options"})
        self.addProperty("inpaint_image_strength", {"label": "Image Strength", "value": 80.0,
                                                    "range": [0.0, 100.0], "constant": True,
                                                    "group": "options"})
        self.addProperty("steps", {"label": "Steps", "value": 30, "type": "int",
                                   "range": [10, 150], "constant": True, "group": "options"})
        self.addProperty("seed", {"label": "Seed", "value": 0, "type": "int",
                                  "range": [0, 2147483647], "constant": True, "group": "options"})
        self.addProperty("samples", {"label": "Number of images", "value": 4, "type": "int",
                                     "range": [1, 10], "constant": True, "group": "options"})

        # the selected asset id to emit ("" = none).  A normal (non-transient)
        # string property: editing it auto-invalidates the output cache, so
        # process() re-runs and emits the newly-selected asset for free.  The
        # Asset Browser dock writes this on a thumbnail click.  It holds a string
        # asset id (not an index), so it stays valid across delete / reorder /
        # catalog rebuild.
        self.addProperty("selected", {"label": "Selected", "value": ""})

        # static marker: advertises this as an asset-generator node and names its
        # catalog category.  The store resolves to assets/<category>/<node.id>/
        # and the Asset Browser auto-appears for nodes carrying this property.
        # Set once in __init__; never mutated.  All per-asset metadata
        # (resolution, the input DOD, prompt/model/seed) lives in the catalog's
        # manifest.json sidecar — NOT in node properties — so it can be updated
        # asynchronously when generation finishes without touching undo.
        # Declares this node an asset owner and names its catalog category: the
        # store resolves to assets/stability/<node.id>/ and the Asset Browser
        # activates for it.  NODE STATE, not a property — it is metadata about
        # the node, like graph.pos, not a parameter.  Set through the proxy
        # because __init__ must not touch self.node (refcount 0 during
        # construction).
        self.setState("assetcatalog", "stability")

    # ---- supervised-property reactivity ----
    def updatePropertyState(self):
        """Enable/disable fields for the current mode."""
        # hide the inherited base 'node.format' property — re-applied every call
        # because the base Node.updatePropertyState() (runs first) re-shows it for
        # generators, and this node is a generator (its only input is aux).
        self.node.property("node.format").hidden = True
        idx = int(self.property("mode").getValue(0))
        if not (0 <= idx < len(self._mode_flags)):
            return
        flags = self._mode_flags[idx]
        self.property("prompt").enabled                  = bool(flags & _FLAG_PROMPT)
        self.property("negprompt").enabled               = bool(flags & _FLAG_NEGPROMPT)
        self.property("selectprompt").enabled            = bool(flags & _FLAG_SELPROMPT)
        self.property("generate_image_strength").enabled = bool(flags & _FLAG_GENSTR)
        self.property("inpaint_image_strength").enabled  = bool(flags & _FLAG_PAINTSTR)
        self.property("composite").enabled               = bool(flags & _FLAG_COMPOSITE)

    # =======================================================================
    # render: emit the selected sample
    # =======================================================================

    # QUERY pass — declare the output's DOD + channels for the selected asset.
    def query(self, rc, options, type, port):
        info = self.input.query(rc, options, type) or {}
        sel = self.property("selected").value             # asset id ("" = none)
        if not self.node.enabled or not sel:
            return info                                   # pass-through

        asset = self._catalog().get(sel)
        if not asset:
            return info                                   # unknown id -> pass-through

        input_rgba = info.get("channels") == Channel_RGBA
        info = dict(info)
        info["dod"] = self._asset_dod(rc, asset)
        info["channels"] = Channel_RGBA if self._will_composite(input_rgba) else Channel_RGB
        return info

    # PROCESS pass — read the asset off disk, reframe/scale, optionally comp.
    def process(self, rc, options, type, port):
        # disabled -> pass the input straight through.
        if not self.node.enabled:
            return self.input.eval(rc, options, type)

        sel = self.property("selected").value             # asset id ("" = none)
        input_img = self.input.eval(rc, options, type)    # optional; None if unconnected
        if not sel:
            return input_img                              # nothing generated/selected yet

        image = self._load_asset(sel, rc)
        if image is None:
            return input_img

        input_rgba = input_img is not None and input_img.channelMask == Channel_RGBA
        if self._will_composite(input_rgba):
            roi = options.roi
            mode_name = self._mode_name()
            # composite() requires matching format + channels, and we edit the
            # sample's alpha in place below (copyChannel) — so it must be a
            # uniquely-owned raster, never one handed straight back from getImage()
            # (which may be cache-backed).  convert() allocates a fresh raster
            # whenever it changes the format/channels; if nothing needs converting,
            # clone() to get our own copy.  The promoted alpha isn't opaque, so the
            # copyChannel that carries the input's alpha (the matte) must follow.
            if image.format != input_img.format or image.channelMask != input_img.channelMask:
                image = image.convert(input_img.format, input_img.channelMask)
            else:
                image = image.clone()
            image.copyChannel(input_img, 3, roi=roi)          # carry the input alpha (the matte)
            if mode_name == "inpaint":
                image = input_img.composite(image, roi=roi)   # result over input
            elif mode_name == "outpaint":
                image = image.composite(input_img, roi=roi)   # input over result
        return image

    # ---- render helpers ----
    def _will_composite(self, input_rgba):
        if not input_rgba or not bool(self.property("composite").getValue(0)):
            return False
        return self._mode_name() in ("inpaint", "outpaint")

    def _mode_name(self):
        idx = int(self.property("mode").getValue(0))
        return self._modes[idx]["mode"] if 0 <= idx < len(self._modes) else ""

    # ---- asset catalog ----
    def _catalog(self):
        """Resolve this node's shared asset catalog (the manifest).

        fx.AssetCatalog(node) reads the 'assetcatalog' node state for the category,
        resolves the store at assets/<category>/<node.id>/, and returns the one
        instance shared with the render path and the Asset Browser.  Must NOT be
        called from __init__ (it reads self.node)."""
        cat = fx.AssetCatalog(self.node)
        self._migrate_legacy(cat)
        return cat

    def _migrate_legacy(self, cat):
        """One-time: fold any early-version stability/<id>/*.png into the store."""
        if getattr(self, "_migrated", False) or cat.count > 0:
            self._migrated = True
            return
        self._migrated = True
        legacy = Path(fx.resolvePath(f"$(SFX_PROJECT_ROOT)/stability/{self.node.id}"))
        store = Path(cat.storeDir)
        if not legacy.exists() or legacy == store:
            return
        import shutil
        store.mkdir(parents=True, exist_ok=True)
        copied = 0
        for p in legacy.glob("*.png"):
            try:
                int(p.stem)                       # skip temp.png / temp_mask.png
            except ValueError:
                continue
            dst = store / p.name
            if not dst.exists():
                shutil.copy2(p, dst)
                copied += 1
        if copied:
            cat.ingest()
            cat.save()

    @staticmethod
    def _asset_recti(asset):
        """The asset's session-space DOD, if its descriptor recorded one."""
        meta = asset.get("meta") or {}
        d = meta.get("dod")
        if d and len(d) == 4:
            r = Recti(d[0], d[1], d[2], d[3])     # left, top, right, bottom
            if r.valid:
                return r
        return None

    def _asset_dod(self, rc, asset):
        dod = self._asset_recti(asset)
        if dod is not None:
            return rc.proxy.rect(dod)             # session-space DOD scaled to the proxy
        return Recti(size=rc.proxy.renderSize)    # generator: full render frame

    @staticmethod
    def _same_rect(a, b):
        return (a.left == b.left and a.top == b.top and
                a.right == b.right and a.bottom == b.bottom)

    def _load_asset(self, sel, rc):
        """Load the selected asset's media, reframe to its DOD, scale to proxy."""
        cat = self._catalog()
        asset = cat.get(sel)
        if not asset:
            return None
        path = cat.assetPath(sel)
        if not path or not Path(path).exists():
            return None
        src = fx.Source(path)
        if src.duration == 0:
            return None
        raster = src.getImage(0.0)                # full-res RGB
        if raster is None:
            return None

        proxy = rc.proxy
        dod = self._asset_recti(asset)
        if dod is not None:
            target = proxy.rect(dod)              # session-space -> proxy-space, positioned
            if target.valid and not self._same_rect(target, raster.dod):
                raster = raster.resize(dod=target)
        elif proxy.scale != 1.0:
            nat = raster.dod                      # no stored DOD: just scale to the proxy
            raster = raster.resize(Size(max(1, round(nat.width * proxy.scale)),
                                        max(1, round(nat.height * proxy.scale))))
        return raster

    # =======================================================================
    # generation (a background task)
    # =======================================================================

    def on_submit(self):
        """Generate button callback (UI thread) — submit a background task.

        Gathers the node's parameters and hands them to the async TaskManager,
        then returns immediately (no blocking, no modal progress).  The
        `stability` provider renders the input, runs the HTTP in an fx-free
        worker process, and commits the results into this node's asset catalog —
        raising the attention badge so the user picks an asset in the Asset
        Browser (the task never auto-selects)."""
        # results + task state persist inside the project bundle (requiresBundle)
        # — an unsaved project has nowhere to put them.
        project = fx.activeProject()
        if project is None or not project.is_saved:
            fx.displayError("Save the project first — Stability results are "
                            "stored inside the project bundle.")
            return
        # No API key yet → explain what's needed and offer to open this
        # extension's Options, rather than dropping the user into a config
        # dialog unannounced.  The "Enter API Key" button leads to
        # the config; canceling at either step (or leaving the key blank —
        # they may not have one yet) aborts silently rather than erroring.
        # The message is rich text (QMessageBox renders it and opens the links
        # in the browser; the dialog stays up) — so line breaks are <br/>,
        # not \n.
        if not fx.buildPath(fx.prefs['ml.stability_api_key']):
            if not fx.askQuestion(
                    'Stability requires a <a href="https://brandstudio.com/">'
                    'Brand Studio</a> account and an API key.<br/>'
                    '<a href="https://platform.stability.ai/docs/terms-of-service">'
                    'Terms of Service</a><br/><br/>'
                    'Enter your API key to start generating.',
                    title="Stability AI",
                    okText="Enter API Key", cancelText="Cancel"):
                return
            if not fx.showExtensionOptions("com.borisfx.stability"):
                return
            if not fx.buildPath(fx.prefs['ml.stability_api_key']):
                return
        # guard an accidental re-click: if a generation is already running for
        # this node, confirm before stacking another.  (Deliberate variations are
        # fine — Stability runs a couple concurrently — so this only prompts when
        # one is actually in flight for THIS node, rather than disabling the button.)
        if self._task_active() and not fx.askQuestion(
                "A Stability generation is already running for this node.\n\n"
                "Start another one?", title="Stability AI",
                okText="Generate", cancelText="Cancel"):
            return
        mode = int(self.property("mode").getValue(0))
        model_idx = int(self.property("model").getValue(0))
        clip_presets = ["FAST_BLUE", "FAST_GREEN", "NONE", "SIMPLE",
                        "SLOW", "SLOWER", "SLOWEST"]
        cgp = int(self.property("cgp").getValue(0))
        params = {
            "owner_id":        self.node.id,
            "mode_index":      mode,
            "mode_name":       self._modes[mode]["mode"] if 0 <= mode < len(self._modes) else "",
            "engine":          self._models[model_idx]["engine"] if 0 <= model_idx < len(self._models) else "",
            "prompt":          self.property("prompt").value,
            "negprompt":       self.property("negprompt").value,
            "selectprompt":    self.property("selectprompt").value,
            "prompt_strength": self.property("prompt_strength").value,
            "image_strength":  (self.property("generate_image_strength").value if mode == 0
                                else self.property("inpaint_image_strength").value),
            "steps":           int(self.property("steps").getValue(0)),
            "seed":            int(self.property("seed").getValue(0)),
            "samples":         int(self.property("samples").getValue(0)),
            "cgp":             clip_presets[cgp] if 0 <= cgp < len(clip_presets) else "NONE",
            "size_index":      int(self.property("size").getValue(0)),
            "frame":           getattr(getattr(fx, "player", None), "frame", 0.0),
        }
        # the prompt property is disabled for modes that don't use it (Erase,
        # Remove Background) — show the mode's display label instead of a stale
        # prompt left over from a previous mode.
        prompt_used = self.property("prompt").enabled
        desc = params["prompt"].strip() if (prompt_used and params["prompt"].strip()) else ""
        label = "Stability: " + (desc or self._modes[mode].get("label") or params["mode_name"] or "generate")
        try:
            import tasks
            tasks.get().submit("stability", owner=self.node.id, label=label[:60], params=params)
        except Exception as e:
            # the provider's preflight raises (missing key/prompt) before any task
            # is created; surface it as a dialog (the task system shows async
            # failures itself, via the dock/spinner/toast).
            import traceback
            traceback.print_exc()
            try:
                fx.displayError(str(e))
            except Exception:
                pass

    def _task_active(self):
        """True if a Stability task for this node is queued / running / applying."""
        nid = self.node.id
        try:
            for t in fx.TaskMgr().tasks():
                if (t.get("ownerId") == nid and t.get("provider") == "stability"
                        and str(t.get("state", "")).lower() in ("queued", "running", "applying")):
                    return True
        except Exception:
            pass
        return False


# register the node type.  The categories / intents / dataTypes match the older
# built-in Stability node's, so this one is found by the same category browsing
# and intent search.
addNode(name="PyStabilityAI", label="Stability AI", type=PyStabilityAI,
        categories=["AI/ML", "Image"],
        intents=["generative ai", "gen ai"],
        dataTypes=["image"])

if __name__ == '__main__':                                                                
	from tools.dev import test_node
	test_node(Node("PyStabilityAI"))
