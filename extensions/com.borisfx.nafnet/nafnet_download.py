"""NAFNet checkpoint download - the main-side half of the model fetch.

The node's *Download Model* button submits a background task
(``tasks.get().submit``); the fx-free worker (``_download_worker.py``)
performs the HTTP transfer into the task store, and ``on_result`` here (main
thread) moves the verified checkpoint into this extension's ``models/``
folder, then badges and re-renders every NAFNet node - a frame rendered while
the checkpoint was missing is a cached pass-through that must invalidate.

The task is **user-global** (``owner=""``): a model belongs to the extension,
not to any node or project, so it needs no saved project, persists under the
user task root, and survives an app restart - recovery relaunches the worker,
which resumes from the partial file.
"""

import json
import os
import shutil

import fx
from tasks import registry
from tools import extension

NODE_TYPE = "com.borisfx.silhouette.nafnet"  # the addNode() name in nafnet_node.py
PROVIDER = "nafnet-download"

_WORKER = os.path.join(os.path.dirname(__file__), "_download_worker.py")
_ACTIVE = ("queued", "running", "applying", "paused")   # non-terminal states


# -- model table (shared with the node) ---------------------------------------

def models():
    with open(extension.path(__file__, "nafnet_config.json")) as f:
        return json.load(f)["models"]


def weight_filename(cfg):
    return cfg["weight"]


def weight_path(cfg):
    return extension.path(__file__, "models", weight_filename(cfg))


def installed(cfg):
    return os.path.exists(weight_path(cfg))


def downloadable(cfg):
    return bool((cfg.get("download") or {}).get("url"))


def active(model_id):
    """True while a download task for this model is queued/running/paused."""
    import tasks
    try:
        for t in fx.TaskMgr().tasks():
            if t.get("provider") != PROVIDER:
                continue
            if str(t.get("state", "")).lower() not in _ACTIVE:
                continue
            ctx = tasks.get().task_ctx(t.get("id"))
            if ctx and (ctx.params or {}).get("model") == model_id:
                return True
    except Exception:
        pass
    return False


def button_enabled(cfg):
    """The Download Model button is live only while there's something to do."""
    return downloadable(cfg) and not installed(cfg) and not active(cfg["id"])


def request(cfg):
    """Submit the download task for one model config (UI thread).  Raises on a
    setup failure (no published url / already installed / folder not writable)
    - the button callback presents the message."""
    import tasks
    label = "Download NAFNet model: %s" % cfg.get("label", cfg["id"])
    tid = tasks.get().submit(PROVIDER, owner="", label=label[:60],
                             params={"model": cfg["id"]})
    _refresh_buttons()      # every instance's button reflects the new task
    return tid


def _cfg(model_id):
    for m in models():
        if m.get("id") == model_id:
            return m
    return None


# -- the task provider ---------------------------------------------------------

class DownloadProvider(object):
    """Fetch one model checkpoint into the extension, as a background task."""

    name = PROVIDER
    resumable = True        # the worker's .part file is its checkpoint
    max_concurrent = 1      # one large transfer at a time

    def ensure_deps(self):
        pass                # the worker is stdlib-only

    # -- pre-flight (no task created on failure; the caller shows the error) --
    def preflight(self, params, owner):
        cfg = _cfg(params.get("model"))
        if cfg is None:
            raise Exception("Unknown model id: %r" % (params.get("model"),))
        label = cfg.get("label", cfg["id"])
        download = cfg.get("download") or {}
        if not download.get("url"):
            raise Exception("No download is published for '%s' - see the "
                            "extension's README for manual installation." % label)
        if installed(cfg):
            raise Exception("The '%s' checkpoint is already installed." % label)
        if active(cfg["id"]):
            raise Exception("A download for '%s' is already running - "
                            "see the Tasks view." % label)
        models_dir = extension.path(__file__, "models")
        try:
            os.makedirs(models_dir, exist_ok=True)
        except OSError as e:
            raise Exception("Can't create the extension's models folder "
                            "(%s): %s" % (models_dir, e))
        if not os.access(models_dir, os.W_OK):
            raise Exception("The extension's models folder is not writable: %s"
                            % models_dir)
        need = int(download.get("bytes") or 0)
        if need:
            free = shutil.disk_usage(models_dir).free
            if free < need + (64 << 20):
                raise Exception("Not enough disk space for '%s': %d MB needed, "
                                "%d MB free." % (label, need >> 20, free >> 20))

    # -- build: write job.json; the worker does the rest -----------------------
    def build(self, params, store, owner):
        cfg = _cfg(params.get("model"))
        if cfg is None:
            raise Exception("Unknown model id: %r" % (params.get("model"),))
        download = cfg.get("download") or {}
        job = {"url": download.get("url", ""),
               "filename": weight_filename(cfg),
               "sha256": (download.get("sha256") or "").lower(),
               "bytes": int(download.get("bytes") or 0)}
        with open(os.path.join(store, "job.json"), "w") as f:
            json.dump(job, f, indent=2)
        return {"argv": self.resume_argv(params, store), "runner": "worker",
                "env": self.resume_env(params)}

    def resume_argv(self, params, store):
        # module-relative worker path - survives app restarts and extension moves
        return [fx.pythonExecutable(), _WORKER, store]

    def resume_env(self, params):
        env = {}
        ca = _ca_bundle()
        if ca:
            env["SFX_CA_BUNDLE"] = ca   # the worker's stdlib ssl verifies against it
        return env

    # -- commit (main thread, render depth 0) ----------------------------------
    def on_result(self, task):
        cfg = _cfg(task.params.get("model"))
        if cfg is None:
            raise Exception("The downloaded model is no longer in nafnet_config.json.")
        store = os.path.normpath(task.store)
        manifest = task.result or []
        rel = manifest[0].get("file") if manifest and isinstance(manifest[0], dict) else ""
        src = os.path.normpath(os.path.join(store, rel)) if rel else ""
        if not src.startswith(store + os.sep) or not os.path.exists(src):
            raise Exception("Download finished but the checkpoint is missing "
                            "from the task store.")
        dest = weight_path(cfg)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.move(src, dest)      # same-volume rename in the common case
        _refresh_nodes()
        return True                 # attention -> success toast with the actions

    # -- completion actions (user-invoked, from the dock row / toast) ----------
    def actions(self, task):
        if not task.result:
            return []
        return [("Show Node", lambda t=task: _show_node())]


# -- node refresh ---------------------------------------------------------------

def _iter_nodes():
    project = fx.activeProject()
    if project is None:
        return
    for session in project.sessions:
        for node in session.nodes:
            if node.type == NODE_TYPE:
                yield node


def _refresh_nodes():
    """After an install: badge + re-render every NAFNet node.  The checkpoint
    is extension-global, so every instance is affected.  Alerting is manual
    here because the task is ownerless - the task layer's owner-badge path
    doesn't apply."""
    for node in _iter_nodes():
        try:
            node.alert = True
            # Two calls, and the ORDER MATTERS.  A frame rendered while the model
            # was missing cached the pass-through, and each call only does half
            # the job: invalidating one of the node's own properties routes
            # through the Python node's invalidateOutputs(), which drops the
            # cached raster (and re-runs updatePropertyState, clearing the cached
            # net + refreshing this button) but posts no UI event; node.invalidate()
            # posts the event that makes the viewer re-request the frame but does
            # NOT drop the cached raster.  Flush first, then notify - the reverse
            # order just redraws the stale pixels.
            node.property("model").invalidate()
            node.invalidate()
        except Exception:
            pass
    _refresh_buttons()


def _refresh_buttons():
    """Recompute each instance's Download Model button for ITS selected model.
    updatePropertyState only re-runs on property edits, so a task starting or
    finishing must push the new state."""
    table = models()
    for node in _iter_nodes():
        try:
            idx = int(node.property("model").getValue(0))
            if 0 <= idx < len(table):
                node.property("download").enabled = button_enabled(table[idx])
        except Exception:
            pass


def _show_node():
    """Completion action: activate a NAFNet node (activation clears its badge)."""
    for node in _iter_nodes():
        fx.activate(node)
        return


def _ca_bundle():
    """certifi's CA-bundle path for the worker's stdlib ssl - the bundled
    interpreter has no usable default CA store on macOS.  certifi ships with
    the app; resolve the path here, main-side, and pass it via env."""
    try:
        from tools.ensure import ensure
        certifi = ensure("certifi", required=False)
        return certifi.where() if certifi else None
    except Exception:
        return None


# The loader auto-tags the owning extension id, so the provider is dropped
# when the extension is disabled or removed.
registry.register(DownloadProvider())
