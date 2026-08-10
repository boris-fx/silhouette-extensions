"""GenAI — the GenAI Emulator node: a full video-GenAI pipeline, no tokens.

The reference asset-generator VIDEO node (see this extension's README.md).
Clicking **Generate**:

  1. renders the input over the session work range to a temp movie
     (main thread, normal progress bar — cancellable),
  2. then a background task exposes that movie through a Cloudflare quick
     tunnel, submits it to the GenAI service, polls, and downloads the
     "generated" result into this node's asset catalog.

With the bundled emulator service the result is the input echoed back after a
simulated generation delay — the entire pipeline is real except the AI.  The
user picks a result in the Asset Browser (tasks never auto-select), and this
node plays the selected clip over the exact frames it was rendered from.

The movie render path (`query`/`process` + helpers) is the reusable part for
any node that plays a generated clip: the clip is anchored at its
`meta.frame`, mapped by real-time fps, its first/last frames held outside the
span, uniformly fit into the render frame, and its `fx.Source` cached per
asset so scrubbing doesn't reopen the movie.
"""

import fx
from fx import *

from pathlib import Path
import json

from tools import extension

from . import genai_render


_CONFIG = None


def _load_config():
    """Read genai_config.json once; return the model list."""
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    models = []
    try:
        path = extension.path(__file__, "genai_config.json")
        with open(path) as f:
            models = json.load(f).get("models", [])
    except Exception as e:
        print(f"[GenAI] could not read genai_config.json: {e}")

    if not models:
        models = [{"label": "ERROR - Missing Config", "id": "missing"}]

    _CONFIG = models
    return _CONFIG


class GenAI(NodeProxy):
    """Simulate video generation end-to-end (no tokens)."""

    # Results (asset catalog) and resumable task state live INSIDE the project
    # bundle, so the project must be saved before Generate can run — same gate
    # as Paint (PythonNode reads this class attribute; Session/Project aggregate
    # it into the existing save-first prompts).
    requiresBundle = True

    def __init__(self, *args, **kwargs):
        self._models = _load_config()
        model_labels = [m.get("label", m.get("id", "")) for m in self._models]

        # ---- ports: the clip to process in, the generated movie out ----
        self.input  = self.addInput("input", "Input", aux=True)
        self.output = self.addOutput("output", "Output", cache=True)

        # ---- properties (deliberately minimal — the pipeline is the point) ----
        self.addProperty("prompt", {"label": "Prompt", "value": ""})
        self.addProperty("model", {"label": "Model", "items": model_labels,
                                   "value": 0})
        self.addProperty("tokens", {"label": "Tokens", "value": "",
                                    "type": "label", "transient": True})
        self.addProperty("generate", {"label": "Generate", "type": "button",
                                      "callback": self.on_submit})
        self.addProperty("samples", {"label": "Number of Videos", "value": 1,
                                     "type": "int", "range": [1, 4],
                                     "constant": True})

        # the selected asset id to emit ("" = none); the Asset Browser dock
        # writes this on a thumbnail click (undoable), which auto-invalidates
        # the output cache so process() emits the new clip.
        self.addProperty("selected", {"label": "Selected", "value": ""})

        # static marker: names the catalog category; the store resolves to
        # assets/genai/<node.id>/ and the Asset Browser auto-appears.
        self.addProperty("assetcatalog", {"label": "Asset Store", "value": "genai",
                                          "hidden": True})

    def updatePropertyState(self):
        # hide the inherited base 'node.format' property — re-applied every
        # call because the base (runs first) re-shows it for generators.
        self.node.property("node.format").hidden = True

    # =======================================================================
    # render: emit the selected clip at the render frame
    # =======================================================================

    def query(self, rc, options, type, port):
        info = self.input.query(rc, options, type) or {}
        sel = self.property("selected").value
        if not self.node.enabled or not sel:
            return info                                   # pass-through
        asset = self._catalog().get(sel)
        if not asset:
            return info
        info = dict(info)
        info["dod"] = self._fit_rect(rc, asset)
        info["channels"] = Channel_RGB                    # MP4: no alpha
        return info

    def process(self, rc, options, type, port):
        sel = self.property("selected").value
        if not self.node.enabled or not sel:
            return self.input.eval(rc, options, type)     # pass-through
        image = self._load_asset(sel, rc, options.frame)
        if image is None:
            return self.input.eval(rc, options, type)
        return image

    # ---- movie render helpers ----
    def _fit_rect(self, rc, asset):
        """Uniform-fit the asset into the render frame, centered (proxy space)."""
        rs = rc.proxy.renderSize
        res = asset.get("resolution") or []
        if len(res) == 2 and res[0] > 0 and res[1] > 0:
            s = min(float(rs.width) / res[0], float(rs.height) / res[1])
            w = max(1, int(round(res[0] * s)))
            h = max(1, int(round(res[1] * s)))
            x = int((rs.width - w) // 2)
            y = int((rs.height - h) // 2)
            return Recti(x, y, x + w, y + h)
        return Recti(size=rs)

    def _asset_frame(self, asset, frame):
        """Map the render frame onto the clip (movie frames, held ends)."""
        meta = asset.get("meta") or {}
        start = float(meta.get("frame", 0.0))
        session_fps = float(self.node.session.frameRate or 0.0)
        clip_fps = float(asset.get("fps") or 0.0)
        if session_fps > 0.0 and clip_fps > 0.0:
            mapped = (frame - start) * clip_fps / session_fps
        else:
            mapped = frame - start
        last = max(0, int(asset.get("frames") or 1) - 1)
        return float(min(max(int(round(mapped)), 0), last))

    def _source(self, sel, path):
        """The asset's fx.Source, cached per id (movie decoders are heavy)."""
        cache = getattr(self, "_sources", None)
        if cache is None:
            cache = self._sources = {}
        hit = cache.get(sel)
        if hit is not None and hit[0] == path:
            return hit[1]
        if len(cache) > 3:
            cache.clear()
        src = fx.Source(path)
        cache[sel] = (path, src)
        return src

    def _load_asset(self, sel, rc, frame):
        cat = self._catalog()
        asset = cat.get(sel)
        if not asset:
            return None
        path = cat.assetPath(sel)
        if not path or not Path(path).exists():
            return None
        src = self._source(sel, path)
        if src is None or src.duration == 0:
            return None
        f = self._asset_frame(asset, frame) if asset.get("type") == "movie" else 0.0
        raster = src.getImage(f)
        if raster is None:
            return None
        target = self._fit_rect(rc, asset)
        if target.valid and not self._same_rect(target, raster.dod):
            raster = raster.resize(dod=target)
        return raster

    @staticmethod
    def _same_rect(a, b):
        return (a.left == b.left and a.top == b.top and
                a.right == b.right and a.bottom == b.bottom)

    def _catalog(self):
        """This node's shared asset catalog (never call from __init__)."""
        return fx.AssetCatalog(self.node)

    # =======================================================================
    # generation — render out (blocking), then submit the background task
    # =======================================================================

    def on_submit(self):
        """Generate button callback (UI thread).

        Stage 1 (the input render) runs here on the main thread with a normal
        progress dialog; once the temp movie exists, the task is submitted and
        this returns — tunnel/submit/poll/download are all background."""
        # results + resumable task state persist inside the project bundle
        # (requiresBundle) — an unsaved project has nowhere to put them.
        project = fx.activeProject()
        if project is None or not project.is_saved:
            fx.displayError("Save the project first — GenAI results are stored "
                            "inside the project bundle.")
            return
        if not fx.buildPath(fx.prefs['genai.api_key']):
            if not fx.showExtensionOptions("com.borisfx.genai"):
                return
            if not fx.buildPath(fx.prefs['genai.api_key']):
                return
        if self._task_active() and not fx.askQuestion(
                "A generation is already running for this node.\n\n"
                "Start another one?", title="GenAI Emulator",
                okText="Generate", cancelText="Cancel"):
            return
        model_idx = int(self.property("model").getValue(0))
        caps = self._models[model_idx] if 0 <= model_idx < len(self._models) else {}
        # FRAME SPACES: session.workRange reports the timeline's DISPLAY numbering
        # (the internal work area shifted by session.startFrame — fxpysession.cpp
        # s_workRange), while node.eval() / options.frame — everything the render
        # and playback paths consume — run in INTERNAL 0-based frames.  Convert
        # here, or the clip is captured one startFrame late and anchored one
        # startFrame late on playback.
        session = self.node.session
        first, last = session.workRange               # the clip = the work range
        offset = float(session.startFrame or 0.0)
        first, last = first - offset, last - offset   # -> internal frames
        params = {
            "owner_id":  self.node.id,
            "model_id":  caps.get("id", ""),
            "prompt":    self.property("prompt").value,
            "samples":   int(self.property("samples").getValue(0)),
            "start":     float(first),
            "end":       float(last),
        }
        desc = params["prompt"].strip()
        label = "GenAI: " + (desc or "process clip")
        try:
            import tasks
            tasks.get().submit("genai", owner=self.node.id, label=label[:60], params=params)
        except genai_render.RenderCanceled:
            return                                    # user canceled the render — not an error
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                fx.displayError(str(e))
            except Exception:
                pass

    def _task_active(self):
        """True if a GenAI task for this node is queued / running / applying."""
        nid = self.node.id
        try:
            for t in fx.TaskMgr().tasks():
                if (t.get("ownerId") == nid and t.get("provider") == "genai"
                        and str(t.get("state", "")).lower() in ("queued", "running", "applying")):
                    return True
        except Exception:
            pass
        return False


addNode(name="com.borisfx.silhouette.genai", label="GenAI Emulator", type=GenAI,
        categories=["AI/ML", "Image"],
        intents=["generative ai", "gen ai", "video generation"],
        dataTypes=["image"])

if __name__ == '__main__':
	from tools.dev import test_node
	test_node(Node("com.borisfx.silhouette.genai"))
