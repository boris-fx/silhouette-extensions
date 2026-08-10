"""GenAI emulator worker — the emulator SERVICE ADAPTER on the shared engine.

fx-free.  Runs in a plain ``fx.pythonExecutable()`` subprocess.  ALL of the
generic pipeline — resume checkpoint, PAUSE/CANCEL/orphan stop semantics,
upload orchestration, the submit/poll/download loops, download retry, and the
cancel-orphaned-jobs cleanup — lives in the shared ``genai.task_engine`` (see
its docstring for the contract and the crash/pause-resume guarantees).  This
module supplies only what is emulator/dialect-specific:

  * the SERVICE ADAPTER (submit / poll / cancel / download) speaking the
    generic task dialect the bundled emulator serves (_genai_service.py):
        POST /v1/tasks {model,prompt,video_url,…} -> {"id"}
        GET  /v1/tasks/{id} -> {"status","progress","result_url","usage"}
        DELETE /v1/tasks/{id}
  * ``GenAIAdapter``, which also carries the two seams a spawned-local-service
    needs and a real cloud does not: ``setup()`` boots the emulator (into the
    task store, so its durable registry survives a crash with the task) and
    ``teardown()`` stops it + the FileShare; ``expose_input`` publishes the
    input through the localhost/tunnel FileShare; ``durable_media_url = False``
    because that share URL dies with this process (the engine re-exposes any
    remaining submit on resume rather than reusing a checkpointed URL).

To integrate a real service, this is the whole template: write an adapter with
the same methods (a cloud drops setup/teardown and sets durable_media_url).

job.json (written by the provider; relpaths are relative to the store):
    { "service": {"url": ""|base, "gen_seconds": s},   # "" -> spawn emulator
      "prompt", "model", "args": {…}, "input": "inputs/input.mov",
      "samples": N, "poll_interval": s, "timeout": s,
      "tunnel": "auto"|"on"|"off", "cloudflared": path|"" }

Runnable from a bare terminal for debugging:  python3 _genai_worker.py <store>
"""

import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

# The shared engine + FileShare live in the resources/scripts/genai core
# package.  In-app the provider puts resources/scripts on the worker's
# PYTHONPATH (genai.worker_env); the fallback covers a bare-terminal / test
# run — from extensions/<id>/ the core is at ../../resources/scripts.
try:
    from genai import task_engine as engine
    from genai import expose
except ImportError:
    _ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          "..", "..", "resources", "scripts"))
    sys.path.insert(0, _ROOT)
    from genai import task_engine as engine
    from genai import expose


_UA = "silhouette-genai/1.0"


def _ssl_context():
    ca = os.environ.get("SFX_CA_BUNDLE")
    try:
        return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    except Exception:
        return ssl.create_default_context()


def _request(url, key, method="GET", body=None, context=None, timeout=None):
    """Authenticated JSON round-trip."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("User-Agent", _UA)
    with urllib.request.urlopen(req, context=context, timeout=timeout) as resp:
        raw = resp.read()
    return json.loads(raw.decode("utf-8")) if raw.strip() else {}


def _http_error_message(e):
    try:
        detail = e.read().decode("utf-8", "replace")
    except Exception:
        detail = ""
    msg = detail
    try:
        err = json.loads(detail).get("error") or {}
        msg = err.get("message") or detail
        if err.get("code"):
            msg = "%s: %s" % (err["code"], msg)
    except Exception:
        pass
    return "GenAI HTTP %s: %s" % (e.code, (msg or "").strip()[:500])


# ===========================================================================
# SERVICE ADAPTER — the only emulator/API-specific code.
# ===========================================================================

def submit_task(base, key, job, video_url, ctx):
    """Create one generation task; return its id."""
    body = {"model": job.get("model", ""),
            "prompt": job.get("prompt", ""),
            "video_url": video_url}
    body.update(job.get("args") or {})
    resp = _request(base + "/v1/tasks", key, method="POST", body=body, context=ctx)
    tid = resp.get("id")
    if not tid:
        raise RuntimeError("no task id in response: %s" % json.dumps(resp)[:300])
    return tid


def poll_task(base, key, tid, ctx):
    """Fetch one task's state, normalized for the engine."""
    r = _request(base + "/v1/tasks/" + tid, key, context=ctx)
    status = r.get("status", "")
    if status in ("", "queued", "running"):
        return {"state": "waiting", "progress": r.get("progress"),
                "result_url": None, "tokens": None, "message": None}
    if status == "succeeded":
        usage = r.get("usage") or {}
        return {"state": "succeeded", "progress": 1.0,
                "result_url": r.get("result_url"),
                "tokens": usage.get("tokens"), "message": None}
    err = r.get("error") or {}
    return {"state": "failed", "progress": None, "result_url": None,
            "tokens": None,
            "message": ("task %s: %s %s" % (status, err.get("code", ""),
                                            err.get("message", ""))).strip()}


def cancel_task(base, key, tid, ctx):
    """Best-effort server-side cancel (services typically only cancel queued)."""
    _request(base + "/v1/tasks/" + tid, key, method="DELETE", context=ctx, timeout=2.5)


def download_result(base, key, result_url, path, ctx):
    """Stream the finished video to disk (result_url may be relative)."""
    url = urllib.parse.urljoin(base + "/", result_url)
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("User-Agent", _UA)
    with urllib.request.urlopen(req, context=ctx) as resp, open(path, "wb") as fh:
        shutil.copyfileobj(resp, fh, 1 << 20)


# -- emulator bootstrap (a real service adapter has no setup/teardown) -------

def start_emulator(gen_seconds, state_dir):
    """Spawn the bundled local service; return (proc, base_url).

    ``state_dir`` roots the service's durable task registry.  Pointing it
    inside the TASK STORE makes the simulated cloud survive a crash/pause with
    the task: the respawned instance recognizes the checkpointed task ids and
    finishes their interrupted generations — the behavior a real remote
    service gives for free by simply staying up."""
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_genai_service.py")
    proc = subprocess.Popen([sys.executable, script, "--port", "0", "--announce",
                             "--gen-seconds", str(gen_seconds),
                             "--state-dir", state_dir],
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    deadline = engine.time.time() + 15
    while engine.time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        try:
            evt = json.loads(line)
            if evt.get("event") == "ready":
                return proc, "http://127.0.0.1:%d" % evt["port"]
        except Exception:
            continue
    proc.terminate()
    raise RuntimeError("the local GenAI emulator service failed to start")


class GenAIAdapter(object):
    """The engine's service adapter, bound to one job's emulator config.

    A STATIC service URL (job.service.url — the standalone/debug case) is
    resolved in __init__ so the finally can cancel against it even on a
    paused-cancel early return; the spawned-emulator case has no base until
    setup(), and cancel() no-ops until then (its durable registry keeps the
    tasks regardless).  This preserves both pre-engine behaviors exactly."""

    durable_media_url = False       # the FileShare URL is process-bound

    def __init__(self, job, key, ctx):
        self.job = job
        self.key = key
        self.ctx = ctx
        self.base = ((job.get("service") or {}).get("url", "") or "").rstrip("/")
        self._store = None
        self._service_proc = None
        self._share = None

    def setup(self, job, store):
        self._store = store
        if not self.base:           # no static URL -> spawn the bundled emulator
            engine.emit(event="progress", value=-1.0,
                        status="Starting emulator service…")
            self._service_proc, base = start_emulator(
                (job.get("service") or {}).get("gen_seconds", 6.0),
                os.path.join(store, "emulator"))
            self.base = base.rstrip("/")

    def _ensure_share(self):
        if self._share is None:
            self._share = expose.FileShare(self._store,
                                           tunnel=self.job.get("tunnel", "auto"),
                                           cloudflared=self.job.get("cloudflared", ""),
                                           ssl_context=self.ctx)
            self._share.start()
        return self._share

    def expose_input(self, path):
        share = self._ensure_share()
        url = share.add(path)
        engine.emit(event="progress", value=-1.0,
                    status="Input exposed via %s…"
                           % ("Cloudflare tunnel" if share.tunneled else "local URL"))
        return url

    def upload(self, path):
        # no separate upload endpoint — secondary media (none in emulator jobs
        # today) would ride the same FileShare
        return self._ensure_share().add(path)

    def submit(self, job, media_url, keyframes, sample_index, uploads):
        return submit_task(self.base, self.key, job, media_url, self.ctx)

    def poll(self, tid):
        return poll_task(self.base, self.key, tid, self.ctx)

    def cancel(self, tid):
        if not self.base:           # no service was reached this run
            return
        cancel_task(self.base, self.key, tid, self.ctx)

    def download(self, url, path):
        download_result(self.base, self.key, url, path, self.ctx)

    def error_message(self, e):
        return _http_error_message(e)

    def teardown(self):
        # runs in the engine's finally, AFTER the cancels (the service must
        # still be up to receive them)
        if self._share is not None:
            self._share.stop()
        if self._service_proc is not None:
            self._service_proc.terminate()
            try:
                self._service_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._service_proc.kill()


def main(store):
    with open(os.path.join(store, "job.json")) as f:
        job = json.load(f)
    key = os.environ.get("SFX_GENAI_KEY", "")
    return engine.run(store, job, GenAIAdapter(job, key, _ssl_context()))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
