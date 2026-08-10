#!/usr/bin/env python3
"""Package an extension folder into an installable .sfxext archive.

    ./scripts/package.py extensions/com.borisfx.testmenu      # -> dist/com.borisfx.testmenu-1.0.0.sfxext
    ./scripts/package.py --all                                # every extension in extensions/

A .sfxext is a zip whose single top-level entry is the extension folder, named
for the extension id — the layout Silhouette's installer expects.  Silhouette
installs one from *Preferences > Extensions > Install...* or by drag-and-drop
onto the application window.

Build noise is excluded (__pycache__, .DS_Store, .pyc) and so are model weights
(*.pt/*.pth/...): extensions fetch those on demand, and an archive carrying a
250 MB checkpoint is nobody's idea of a download.
"""

import argparse
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXTENSIONS = os.path.join(ROOT, "extensions")
DIST = os.path.join(ROOT, "dist")

EXCLUDE_DIRS = {"__pycache__", ".git"}
EXCLUDE_EXT = {".pyc", ".pyo", ".pt", ".pth", ".onnx", ".safetensors"}
EXCLUDE_NAMES = {".DS_Store"}


def read_manifest(folder):
    """-> (id, version) from extension.toml.  Hand-parsed: this script has to run
    on any Python 3, and tomllib only exists from 3.11."""
    path = os.path.join(folder, "extension.toml")
    if not os.path.isfile(path):
        raise SystemExit("no extension.toml in %s" % folder)
    ident = version = None
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("[") or "=" not in line:
                continue                       # a table header ends the top level
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip()
            if value.startswith('"""'):
                continue                       # multi-line description
            value = value.strip('"').strip("'")
            if key == "id" and ident is None:
                ident = value
            elif key == "version" and version is None:
                version = value
    if not ident:
        raise SystemExit("no id in %s" % path)
    return ident, version or "0.0.0"


def archive(folder):
    folder = os.path.abspath(folder.rstrip(os.sep))
    ident, version = read_manifest(folder)
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "%s-%s.sfxext" % (ident, version))

    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(folder):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for name in sorted(filenames):
                if name in EXCLUDE_NAMES or os.path.splitext(name)[1] in EXCLUDE_EXT:
                    continue
                full = os.path.join(dirpath, name)
                # every entry nests under a single top-level folder named for the
                # id, which is what the installer keys on — not the folder on disk
                rel = os.path.relpath(full, folder)
                z.write(full, os.path.join(ident, rel))
                count += 1
    size = os.path.getsize(out)
    print("%-34s %2d files  %6.1f KB  -> %s"
          % (ident + "-" + version, count, size / 1024.0, os.path.relpath(out, ROOT)))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folders", nargs="*", help="extension folder(s) to package")
    ap.add_argument("--all", action="store_true", help="package everything in extensions/")
    args = ap.parse_args()

    folders = list(args.folders)
    if args.all:
        folders += [os.path.join(EXTENSIONS, d) for d in sorted(os.listdir(EXTENSIONS))
                    if os.path.isfile(os.path.join(EXTENSIONS, d, "extension.toml"))]
    if not folders:
        ap.error("name an extension folder, or pass --all")

    for folder in folders:
        archive(folder)
    return 0


if __name__ == "__main__":
    sys.exit(main())
