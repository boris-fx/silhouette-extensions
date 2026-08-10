"""The emulator "cloud" — a local stand-in for a real video-GenAI service.

fx-free, stdlib-only, runnable from a bare terminal.  It implements the task
API shape common to hosted video-generation services:

    POST   /v1/tasks            create a generation task   -> {"id": …}
    GET    /v1/tasks/{id}       poll it                    -> {"status": …}
    DELETE /v1/tasks/{id}       cancel (queued tasks only)
    GET    /v1/results/{id}.mp4 download the finished video

A created task takes ``video_url`` (+ prompt/model), FETCHES that URL — for
real, so when the worker exposes the input through a Cloudflare tunnel the
bytes genuinely round-trip the public edge — pretends to generate for
``gen_seconds``, then serves the input bytes back as the "generated" result.
Requests must carry ``Authorization: Bearer <anything non-empty>`` so the
auth plumbing is exercised too.  Billing is simulated as tokens
(bytes // 100) in the poll response's ``usage``.

Run standalone for API debugging:

    python3 _genai_service.py --port 8787 --gen-seconds 3
    curl -X POST localhost:8787/v1/tasks -H "Authorization: Bearer x" \\
         -d '{"model":"emulator-hd-1","video_url":"http://…/input.mp4"}'

When the worker spawns it (``--port 0``), the chosen port is announced on
stdout as ``{"event":"ready","port":N}``.

DURABLE TASKS (crash/pause-resume): the registry, the fetched input bytes, and
the generated results all persist under ``--state-dir`` (default
``~/.genai_emulator``), written on task state transitions.  A restarted
service instance recognizes the previous instance's task ids: finished
results serve again, and interrupted generations RESUME from the captured
input — so killing the app (which takes the spawned service with it) and
resuming later behaves like a real cloud, where the server-side job outlives
the client.  The one honest exception mirrors reality too: a task whose input
was never captured before the crash fails with ``InputExpired`` (the client's
upload share died with it) and must be re-submitted.  The worker spawns the
service with the state dir INSIDE the task's own store, so the simulated
server-side state survives exactly as long as the task it belongs to.
"""

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.request
import urllib.error
import uuid


class _Task(object):
    __slots__ = ("id", "status", "progress", "error", "result_path", "tokens",
                 "created", "body")

    def __init__(self, tid, body):
        self.id = tid
        self.status = "queued"       # queued -> running -> succeeded|failed|cancelled
        self.progress = 0.0
        self.error = None
        self.result_path = None
        self.tokens = 0
        self.created = time.time()
        self.body = body


class Emulator(object):
    def __init__(self, gen_seconds=6.0, state_dir=None):
        self.gen_seconds = float(gen_seconds)
        self.tasks = {}
        self.lock = threading.Lock()
        # Durable state: the registry + captured inputs + results.  The worker
        # points this INSIDE the task's store so the simulated server-side
        # state survives a crash/pause exactly as long as the task does.
        self.state_dir = os.path.abspath(state_dir or
                                         os.path.expanduser("~/.genai_emulator"))
        self.results = os.path.join(self.state_dir, "results")
        self.inputs = os.path.join(self.state_dir, "inputs")
        os.makedirs(self.results, exist_ok=True)
        os.makedirs(self.inputs, exist_ok=True)
        self.state_path = os.path.join(self.state_dir, "tasks.json")
        self._load()

    # -- durable registry --------------------------------------------------------

    def _save_locked(self):
        """Persist the registry (caller holds the lock).  Written on task state
        TRANSITIONS only — progress ticks stay in memory.  Atomic per-pid temp
        so a killed service never leaves a torn registry."""
        recs = [{"id": t.id, "status": t.status, "error": t.error,
                 "tokens": t.tokens, "created": t.created, "body": t.body}
                for t in self.tasks.values()]
        tmp = "%s.%d.tmp" % (self.state_path, os.getpid())
        try:
            with open(tmp, "w") as f:
                json.dump({"tasks": recs}, f, indent=2)
            os.replace(tmp, self.state_path)
        except Exception:
            pass                             # harness persistence is best-effort

    def _load(self):
        """Reload a previous instance's registry and CONTINUE where it died:
        finished results serve again; interrupted generations restart from the
        captured input; a task whose input was never captured fails with
        InputExpired (the client's upload share died with the client)."""
        try:
            with open(self.state_path) as f:
                recs = (json.load(f) or {}).get("tasks") or []
        except Exception:
            recs = []
        now = time.time()
        resume = []
        for rec in recs:
            tid = rec.get("id")
            if not tid:
                continue
            if now - float(rec.get("created") or now) > 172800:   # 48 h: prune
                for p in (os.path.join(self.results, tid + ".mp4"),
                          os.path.join(self.inputs, tid + ".mp4")):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
                continue
            t = _Task(tid, rec.get("body") or {})
            t.status = rec.get("status", "queued")
            t.error = rec.get("error")
            t.tokens = int(rec.get("tokens") or 0)
            t.created = float(rec.get("created") or now)
            if t.status == "succeeded":
                path = os.path.join(self.results, tid + ".mp4")
                if os.path.exists(path):
                    t.result_path = path
                    t.progress = 1.0
                else:
                    t.status = "failed"
                    t.error = {"code": "ResultLost",
                               "message": "the stored result file is gone"}
            elif t.status in ("queued", "running"):
                resume.append(t)             # restart below, once all registered
            self.tasks[tid] = t
        for t in resume:
            threading.Thread(target=self._resume_task, args=(t,),
                             daemon=True).start()
        with self.lock:
            self._save_locked()

    # -- the "generation" ------------------------------------------------------

    def run_task(self, task):
        """Fetch the input video, pretend to generate, keep the echo as result."""
        with self.lock:
            if task.status != "queued":
                return                       # cancelled while queued
            task.status = "running"
            self._save_locked()

        url = task.body.get("video_url")
        data, err = None, None
        # retry the fetch: a tunnel hostname may still be propagating through
        # the resolver this process happens to use (see _expose.py's notes).
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "genai-emulator/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = resp.read()
                break
            except Exception as e:
                err = e
                time.sleep(3.0)
        if data is None:
            with self.lock:
                task.status = "failed"
                task.error = {"code": "FetchFailed",
                              "message": "could not fetch video_url: %s" % err}
                self._save_locked()
            return

        # capture the input durably the moment it's fetched: a restarted
        # instance resumes generation from THIS file — the client's share URL
        # never survives the client.
        inpath = os.path.join(self.inputs, "%s.mp4" % task.id)
        try:
            tmp = "%s.%d.tmp" % (inpath, os.getpid())
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, inpath)
        except Exception:
            pass                             # generation still runs in-memory

        self._generate(task, data)

    def _resume_task(self, task):
        """Continue an interrupted (queued/running) task after a restart."""
        inpath = os.path.join(self.inputs, "%s.mp4" % task.id)
        try:
            with open(inpath, "rb") as f:
                data = f.read()
        except OSError:
            with self.lock:
                task.status = "failed"
                task.error = {"code": "InputExpired",
                              "message": "the input upload expired before it "
                                         "was captured — submit again"}
                self._save_locked()
            return
        with self.lock:
            task.status = "running"          # a loaded 'queued' task restarts too
            self._save_locked()
        self._generate(task, data)

    def _generate(self, task, data):
        """The simulated generation (input bytes already in hand)."""
        t0 = time.time()
        while time.time() - t0 < self.gen_seconds:
            with self.lock:
                if task.status != "running":
                    return                   # (future: running-cancel support)
                task.progress = min(0.95, (time.time() - t0) / self.gen_seconds)
            time.sleep(0.25)

        path = os.path.join(self.results, "%s.mp4" % task.id)
        with open(path, "wb") as f:
            f.write(data)                    # the "generated" video: an echo
        with self.lock:
            task.result_path = path
            task.tokens = len(data) // 100
            task.progress = 1.0
            task.status = "succeeded"
            self._save_locked()

    # -- API operations ---------------------------------------------------------

    def create(self, body):
        if not body.get("video_url"):
            return 400, {"error": {"code": "InvalidParameter",
                                   "message": "video_url is required"}}
        tid = "task-" + uuid.uuid4().hex[:12]
        task = _Task(tid, body)
        with self.lock:
            self.tasks[tid] = task
            self._save_locked()
        threading.Thread(target=self.run_task, args=(task,), daemon=True).start()
        return 200, {"id": tid}

    def get(self, tid):
        with self.lock:
            task = self.tasks.get(tid)
            if task is None:
                return 404, {"error": {"code": "NotFound", "message": "no such task"}}
            out = {"id": task.id, "status": task.status,
                   "progress": round(task.progress, 3),
                   "model": task.body.get("model", "")}
            if task.status == "succeeded":
                out["result_url"] = "/v1/results/%s.mp4" % task.id
                out["usage"] = {"tokens": task.tokens}
            if task.error:
                out["error"] = task.error
            return 200, out

    def cancel(self, tid):
        with self.lock:
            task = self.tasks.get(tid)
            if task is None:
                return 404, {"error": {"code": "NotFound", "message": "no such task"}}
            if task.status == "queued":      # mirrors the real services:
                task.status = "cancelled"    # only queued tasks can cancel
                self._save_locked()
            return 200, {}

    def result(self, tid):
        with self.lock:
            task = self.tasks.get(tid)
            path = task.result_path if task else None
        return path


def serve(port=0, gen_seconds=6.0, announce=False, state_dir=None):
    import http.server

    emu = Emulator(gen_seconds, state_dir=state_dir)

    class Handler(http.server.BaseHTTPRequestHandler):
        def _json(self, code, obj):
            body = json.dumps(obj).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authed(self):
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer ") or not auth[7:].strip():
                self._json(401, {"error": {"code": "Unauthorized",
                                           "message": "missing bearer token"}})
                return False
            return True

        def do_POST(self):
            if self.path.rstrip("/") != "/v1/tasks":
                return self._json(404, {"error": {"code": "NotFound", "message": self.path}})
            if not self._authed():
                return
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json(400, {"error": {"code": "BadJSON", "message": "unparseable body"}})
            self._json(*emu.create(body))

        def do_GET(self):
            m = re.match(r"^/v1/results/([\w-]+)\.mp4$", self.path)
            if m:
                path = emu.result(m.group(1))
                if not path or not os.path.exists(path):
                    return self._json(404, {"error": {"code": "NotFound", "message": "no result"}})
                self.send_response(200)
                self.send_header("Content-Type", "video/mp4")
                self.send_header("Content-Length", str(os.path.getsize(path)))
                self.end_headers()
                with open(path, "rb") as f:
                    while True:
                        chunk = f.read(1 << 20)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                return
            m = re.match(r"^/v1/tasks/([\w-]+)$", self.path)
            if not m:
                return self._json(404, {"error": {"code": "NotFound", "message": self.path}})
            if not self._authed():
                return
            self._json(*emu.get(m.group(1)))

        def do_DELETE(self):
            m = re.match(r"^/v1/tasks/([\w-]+)$", self.path)
            if not m:
                return self._json(404, {"error": {"code": "NotFound", "message": self.path}})
            if not self._authed():
                return
            self._json(*emu.cancel(m.group(1)))

        def log_message(self, fmt, *args):
            sys.stderr.write("[emulator] %s\n" % (fmt % args))

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)

    # Parent-death watchdog (worker mode): the worker spawns us as a child, but
    # on POSIX killing the worker (app teardown / kill -9) does NOT kill us and
    # our finally-based cleanup is bypassed — we'd keep running and, worse, a
    # recovery-spawned SECOND service would race us on the same state_dir
    # (tasks.json has only an in-process lock).  So when our parent dies we get
    # reparented to init/launchd (ppid == 1): detect that and exit.  Only armed
    # when we actually have a parent to outlive (worker mode, ppid != 1 at
    # start); a standalone `--port 8787` run (or one already under init) skips
    # it and runs until Ctrl-C.
    start_ppid = os.getppid()
    if announce and start_ppid > 1:
        def _watch_parent():
            while os.getppid() == start_ppid:
                time.sleep(1.0)
            os._exit(0)                       # parent gone -> vanish with it
        threading.Thread(target=_watch_parent, daemon=True).start()

    if announce:
        sys.stdout.write(json.dumps({"event": "ready",
                                     "port": httpd.server_address[1]}) + "\n")
        sys.stdout.flush()
    else:
        sys.stderr.write("[emulator] listening on 127.0.0.1:%d\n" % httpd.server_address[1])
    httpd.serve_forever()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Local video-GenAI emulator service")
    ap.add_argument("--port", type=int, default=0, help="0 picks a free port")
    ap.add_argument("--gen-seconds", type=float, default=6.0,
                    help="simulated generation time per task")
    ap.add_argument("--announce", action="store_true",
                    help="print a ready line with the chosen port (worker mode)")
    ap.add_argument("--state-dir", default=None,
                    help="durable task registry + inputs + results "
                         "(default ~/.genai_emulator); a restarted instance "
                         "resumes interrupted tasks from here")
    args = ap.parse_args()
    serve(args.port, args.gen_seconds, announce=args.announce or args.port == 0,
          state_dir=args.state_dir)
