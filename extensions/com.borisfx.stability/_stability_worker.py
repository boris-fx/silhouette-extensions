"""Stability AI background worker — fx-free, stdlib only.

Runs in a plain ``fx.pythonExecutable()`` subprocess (NOT Silhouette headless):
it imports no ``fx`` / ``gui`` / ``requests``, only the standard library, so it is
safe to run out-of-process and survives the main app exiting.  The Stability
provider (``stability_provider.py``, main side) does all the ``fx`` work — render + scale
the input, resolve the command/args, and commit results to the AssetCatalog — and
hands this worker a fully-resolved ``job.json``; the worker only performs the HTTP
round-trip(s) and writes the raw decoded PNGs.

Protocol (newline-JSON on stdout, parsed by ``tasks/manager.py``):
    {"event":"progress","value":0.4,"status":"Generating 2/4…"}
    {"event":"result","manifest":[{"file":"outputs/0.png"}, …]}
    {"event":"error","message":"Stability HTTP 401: …"}

The API key arrives via the ``SFX_STABILITY_KEY`` environment variable (never in
job.json).  Credits (best-effort) are written to ``<store>/credits.json`` for the
main side to read — the manager's protocol only carries the manifest.

job.json (written by the provider; relpaths are relative to the task store):
    { "host", "command", "version": 1|2, "transport": "json"|"multipart",
      "args": {...}, "files": {field: relpath}, "samples": N }

This module also holds the pure (fx-free) size helpers the provider imports.
"""

import sys
import os
import json
import base64
import uuid
import ssl
import urllib.request
import urllib.error


# Stability sits behind Cloudflare, which bans urllib's default "Python-urllib/x"
# User-Agent (error 1010 "browser_signature_banned").  The "python-requests/x" UA
# is allowed, so mimic it.
_UA = "python-requests/2.32.3"


def _ssl_context():
    """Verify TLS against the CA bundle the main side passed in SFX_CA_BUNDLE.

    The bundled interpreter's stdlib ``ssl`` has no usable default CA store on
    macOS (``requests`` works there only because it ships ``certifi``), so
    the provider resolves ``certifi.where()`` on the main side and hands us the
    path — we build the default context from it.  Falls back to the platform
    default if it's absent."""
    ca = os.environ.get("SFX_CA_BUNDLE")
    try:
        return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()
    except Exception:
        return ssl.create_default_context()


# ---------------------------------------------------------------------------
# pure size helpers (no fx)
# ---------------------------------------------------------------------------

def round64(v):
    return 64 * int(int(v + 0.5) / 64)


def compute_image_size_xl(size):
    """Snap to the nearest SDXL-valid (w,h)."""
    import math
    valid_sizes = [(1024, 1024), (1152, 896), (1216, 832), (1344, 768), (1536, 640)]
    closest, best = None, float("inf")
    for s in valid_sizes:
        d = math.sqrt((size[0] - s[0]) ** 2 + (size[1] - s[1]) ** 2)
        if d < best:
            best, closest = d, s
    return closest


# ---------------------------------------------------------------------------
# stdout protocol
# ---------------------------------------------------------------------------

def _emit(**evt):
    sys.stdout.write(json.dumps(evt) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# HTTP (stdlib urllib — no requests)
# ---------------------------------------------------------------------------

def _multipart_body(boundary, fields, files):
    """Encode form fields + files as multipart/form-data."""
    out = []
    b = boundary.encode("utf-8")

    def add_field(name, value):
        out.append(b"--" + b + b"\r\n")
        out.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % name).encode("utf-8"))
        out.append(("%s" % value).encode("utf-8") + b"\r\n")

    for name, value in fields.items():
        if isinstance(value, (list, tuple)):
            for v in value:
                add_field(name, v)
        else:
            add_field(name, value)

    for field, path in files.items():
        with open(path, "rb") as f:
            content = f.read()
        fn = os.path.basename(path)
        out.append(b"--" + b + b"\r\n")
        out.append(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
                    % (field, fn)).encode("utf-8"))
        out.append(b"Content-Type: application/octet-stream\r\n\r\n")
        out.append(content + b"\r\n")

    out.append(b"--" + b + b"--\r\n")
    return b"".join(out)


def _post(url, key, transport, fields, files, context):
    if transport == "json":
        data = json.dumps(fields).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
    else:
        boundary = "----sfx" + uuid.uuid4().hex
        data = _multipart_body(boundary, fields, files or {})
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary)
    req.add_header("Accept", "application/json")
    req.add_header("Authorization", "Bearer " + key)
    req.add_header("User-Agent", _UA)
    with urllib.request.urlopen(req, context=context) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _get_credits(host, key, context):
    try:
        req = urllib.request.Request(host + "/v1/user/balance", method="GET")
        req.add_header("Accept", "application/json")
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("User-Agent", _UA)
        with urllib.request.urlopen(req, context=context) as resp:
            return json.loads(resp.read().decode("utf-8")).get("credits")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main(store):
    with open(os.path.join(store, "job.json")) as f:
        job = json.load(f)
    key = os.environ.get("SFX_STABILITY_KEY", "")
    host = job["host"].rstrip("/")
    url = host + "/" + job["command"].lstrip("/")
    version = int(job.get("version", 2))
    transport = job.get("transport", "multipart")
    args = dict(job.get("args", {}))
    samples = max(1, int(job.get("samples", 1)))

    files = {k: os.path.join(store, v) for k, v in (job.get("files") or {}).items()}
    outdir = os.path.join(store, "outputs")
    os.makedirs(outdir, exist_ok=True)
    ctx = _ssl_context()

    def canceled():
        return os.path.exists(os.path.join(store, "CANCEL"))

    def write_png(b64, idx):
        path = os.path.join(outdir, "%d.png" % idx)
        with open(path, "wb") as fh:
            fh.write(base64.b64decode(b64))
        return {"file": "outputs/%d.png" % idx}

    manifest = []
    try:
        if version == 1:
            # one call returns an "artifacts" list (text-to-image is json, the
            # image-driven modes are multipart).
            _emit(event="progress", value=0.05, status="Generating…")
            resp = _post(url, key, transport, args, files, ctx)
            for art in resp.get("artifacts", []):
                if art.get("base64"):
                    manifest.append(write_png(art["base64"], len(manifest)))
            _emit(event="progress", value=1.0, status="Done")
        else:
            # v2: one image per call, looped for the requested sample count.
            for i in range(samples):
                if canceled():
                    break
                _emit(event="progress", value=float(i) / samples,
                      status="Generating %d/%d…" % (i + 1, samples))
                resp = _post(url, key, "multipart", args, files, ctx)
                if resp.get("image"):
                    manifest.append(write_png(resp["image"], len(manifest)))
            _emit(event="progress", value=1.0, status="Done")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:
            detail = ""
        _emit(event="error", message="Stability HTTP %s: %s" % (e.code, detail[:500]))
        return 1
    except Exception as e:
        _emit(event="error", message=str(e))
        return 1

    credits = _get_credits(host, key, ctx)
    if credits is not None:
        try:
            with open(os.path.join(store, "credits.json"), "w") as f:
                json.dump({"credits": credits}, f)
        except Exception:
            pass

    _emit(event="result", manifest=manifest)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
