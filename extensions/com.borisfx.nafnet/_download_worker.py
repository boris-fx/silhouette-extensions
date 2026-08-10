"""Model-checkpoint download worker - fx-free, stdlib only.

Runs in a plain ``fx.pythonExecutable()`` subprocess (NOT Silhouette): it
imports only the standard library, so it is safe out-of-process and never
blocks the UI.  The provider (main side) writes a fully-resolved ``job.json``
into the task store; this worker performs the HTTP transfer and leaves the
verified file in ``<store>/outputs/`` for the provider's ``on_result`` to
install.

Protocol (newline-JSON on stdout, parsed by ``tasks/manager.py``):

    {"event":"progress","value":0.42,"status":"27.1 of 64.3 MB"}
    {"event":"result","manifest":[{"file":"outputs/<filename>","sha256":"..."}]}
    {"event":"error","message":"..."}
    {"event":"incomplete","message":"..."}      # stopped with resumable work

job.json (written by the provider):

    {"url": "...", "filename": "...", "sha256": "...", "bytes": N}

Resume: the partial file (``outputs/<filename>.part``) IS the checkpoint - its
size is the offset for an HTTP Range request, so a paused or interrupted
download continues where it stopped (a server that ignores Range gets a clean
restart).  Stopping is cooperative: the task manager drops a PAUSE or CANCEL
marker file into the store before signaling, and the transfer loop checks for
them between chunks - which also covers Windows, where terminate() doesn't
reach a console worker.  Integrity: the completed file must match job.json's
sha256 before it is renamed into place; a mismatch restarts from zero once,
then fails.

TLS: the bundled interpreter has no usable default CA store on macOS, so the
provider resolves certifi's bundle and passes its path via SFX_CA_BUNDLE.
"""

import hashlib
import json
import os
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request

_CHUNK = 256 * 1024             # read size; also the stop-marker poll interval
_TRIES = 4                      # network attempts (each resumes from the .part)
_BACKOFF = [2.0, 5.0, 10.0]     # seconds between attempts
_TIMEOUT = 60                   # per socket operation, not the whole transfer
_UA = "silhouette-model-download/1.0"

_terminated = False             # SIGTERM seen (unix); markers cover Windows


def _on_term(signum, frame):
    global _terminated
    _terminated = True


def _emit(**evt):
    try:
        sys.stdout.write(json.dumps(evt) + "\n")
        sys.stdout.flush()
    except (BrokenPipeError, OSError):
        # the app died mid-line: stop politely, the checkpoint stays resumable
        global _terminated
        _terminated = True


def _stop(store):
    """'cancel', 'pause', or None.  CANCEL wins: cancel() writes its marker
    before signaling, so a SIGTERM with CANCEL present is a cancel, not a pause."""
    if os.path.exists(os.path.join(store, "CANCEL")):
        return "cancel"
    if _terminated or os.path.exists(os.path.join(store, "PAUSE")):
        return "pause"
    return None


def _stopped(kind):
    """Settle a stop request - exit 0 either way; the manager's pausing/
    canceling state decides the terminal state.  ``incomplete`` covers a stop
    it didn't request (an external kill), which must settle Paused."""
    if kind == "pause":
        _emit(event="incomplete", message="Download paused - resume to continue")
    return 0


def _wait(seconds, store):
    """Interruptible retry backoff - returns a stop kind, or None after sleeping."""
    end = time.time() + seconds
    while time.time() < end:
        kind = _stop(store)
        if kind:
            return kind
        time.sleep(0.25)
    return None


def _ssl_context():
    ca = os.environ.get("SFX_CA_BUNDLE")
    try:
        return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    except Exception:
        return ssl.create_default_context()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _brief(e):
    if isinstance(e, urllib.error.HTTPError):
        return "HTTP %d %s" % (e.code, e.reason)
    return str(e) or e.__class__.__name__


def _total_from(resp, offset):
    """The full file size: Content-Range's total on a 206, else offset +
    Content-Length.  0 = unknown (progress goes indeterminate)."""
    content_range = resp.headers.get("Content-Range") or ""
    if "/" in content_range:
        try:
            return int(content_range.rsplit("/", 1)[1])
        except ValueError:
            pass
    try:
        n = int(resp.headers.get("Content-Length") or 0)
    except ValueError:
        n = 0
    return offset + n if n else 0


def _download(url, part, ctx, store, total_hint):
    """One transfer attempt into the .part file.  Returns 'done', 'cancel',
    or 'pause'; raises on a network error (the caller retries)."""
    offset = os.path.getsize(part) if os.path.exists(part) else 0
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if offset:
        req.add_header("Range", "bytes=%d-" % offset)
    try:
        resp = urllib.request.urlopen(req, timeout=_TIMEOUT, context=ctx)
    except urllib.error.HTTPError as e:
        if e.code == 416 and offset:
            return "done"           # nothing left to send - verification decides
        raise
    with resp:
        if offset and resp.status == 206:
            mode = "ab"             # server honored the Range - append
        else:
            mode, offset = "wb", 0  # full body (or Range ignored) - restart
        total = _total_from(resp, offset) or int(total_hint or 0)
        done = offset
        last = -1.0
        with open(part, mode) as f:
            while True:
                kind = _stop(store)
                if kind:
                    return kind
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                value = (float(done) / total) if total else -1.0
                if value < 0.0 or value - last >= 0.005:
                    last = value
                    status = ("%.1f of %.1f MB" % (done / 1e6, total / 1e6)
                              if total else "%.1f MB" % (done / 1e6))
                    _emit(event="progress", value=min(value, 1.0), status=status)
    if total and done < total:
        raise OSError("connection closed early (%d of %d bytes)" % (done, total))
    return "done"


def main(store):
    try:
        signal.signal(signal.SIGTERM, _on_term)
    except Exception:
        pass

    try:
        with open(os.path.join(store, "job.json")) as f:
            job = json.load(f)
    except Exception as e:
        _emit(event="error", message="bad job.json: %s" % e)
        return 1
    url = job.get("url") or ""
    if not url:
        _emit(event="error", message="job.json has no download url")
        return 1
    filename = job.get("filename") or os.path.basename(url.split("?")[0]) or "model.bin"
    want = (job.get("sha256") or "").strip().lower()

    outputs = os.path.join(store, "outputs")
    os.makedirs(outputs, exist_ok=True)
    final = os.path.join(outputs, filename)
    part = final + ".part"
    manifest = [{"file": "outputs/" + filename, "sha256": want}]

    # a relaunch after the file completed but before the result was consumed
    if os.path.exists(final) and (not want or _sha256(final) == want):
        _emit(event="result", manifest=manifest)
        return 0

    ctx = _ssl_context()
    restarted = False               # one free restart after a checksum mismatch
    error = None
    _emit(event="status", status="Connecting…")
    attempt = 0
    while attempt < _TRIES:
        attempt += 1
        kind = _stop(store)
        if kind:
            return _stopped(kind)
        try:
            outcome = _download(url, part, ctx, store, job.get("bytes"))
        except Exception as e:
            error = e
            if attempt < _TRIES:
                _emit(event="status", status="Retrying - %s" % _brief(e))
                kind = _wait(_BACKOFF[min(attempt - 1, len(_BACKOFF) - 1)], store)
                if kind:
                    return _stopped(kind)
            continue
        if outcome in ("cancel", "pause"):
            return _stopped(outcome)
        if want:
            _emit(event="status", status="Verifying checksum…")
            if _sha256(part) != want:
                try:
                    os.remove(part)
                except OSError:
                    pass
                if restarted:
                    _emit(event="error", message="Checksum mismatch after "
                          "download - the published file may be corrupt.")
                    return 1
                restarted = True
                attempt = 0         # full retry budget for the clean restart
                continue
        os.replace(part, final)
        _emit(event="progress", value=1.0, status="Downloaded")
        _emit(event="result", manifest=manifest)
        return 0
    _emit(event="error", message="Download failed: %s" % _brief(error))
    return 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.stderr.write("usage: _download_worker.py <task-store-dir>\n")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
