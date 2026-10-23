#!/usr/bin/env python3
"""Build the extensions website (GitHub Pages) into a folder.

    python3 scripts/build_site.py                 # -> _site/
    python3 scripts/build_site.py --out /tmp/site

For every extension under extensions/ it writes:

    index.html              a card per extension
    <id>/index.html         the extension's README, with a download button
    downloads/<id>.zip      the extension folder, zipped, ready to drag onto Silhouette

Cards follow the order of the extensions table in the top-level README.md and
take their two blurbs from it ("What it contributes", "What it demonstrates"),
so that table is the one place to curate.  An extension missing from the table
still gets a card, using the first sentence of its manifest description.

An extension's optional `icon.png` (square, 256x256) is shown beside its name
on its card and page, the same icon Silhouette shows on its Extensions page;
without one, the card shows the name's first letter on a plain tile.  An
optional `screenshot.png` (or .jpg) is shown on the card and page too.

Nothing this writes is committed: the Pages workflow runs it on every push to
main and publishes the result.  Needs Python 3.11+ and the `markdown` package.
"""

import argparse
import html
import os
import posixpath
import re
import shutil
import sys
import textwrap
import tomllib
import zipfile

import markdown

from package import EXCLUDE_DIRS, EXCLUDE_EXT, EXCLUDE_NAMES

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXTENSIONS = os.path.join(ROOT, "extensions")
ASSETS = os.path.join(HERE, "site")

REPO = os.environ.get("GITHUB_REPOSITORY", "boris-fx/silhouette-extensions")
BRANCH = os.environ.get("GITHUB_REF_NAME", "main")
REPO_URL = "https://github.com/" + REPO

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
SCREENSHOTS = ("screenshot.png", "screenshot.jpg")
ICON = "icon.png"

# what each [contributes] key is called on a card
CONTRIBUTES = {
    "nodes": ("node", "nodes"),
    "themes": ("theme", "themes"),
    "actions": ("menu command", "menu commands"),
    "panels": ("panel", "panels"),
}


# ---------------------------------------------------------------------------
# reading the repo
# ---------------------------------------------------------------------------

class Extension:
    def __init__(self, folder):
        self.folder = folder
        with open(os.path.join(folder, "extension.toml"), "rb") as f:
            m = tomllib.load(f)
        self.id = m["id"]
        self.name = m.get("name", self.id)
        self.version = m.get("version", "")
        self.author = m.get("author", "")
        self.requires = m.get("silhouette", "").lstrip(">=") or None
        self.description = _paragraphs(m.get("description", ""))
        self.contributes = m.get("contributes", {})
        self.screenshot = next((s for s in SCREENSHOTS
                                if os.path.isfile(os.path.join(folder, s))), None)
        self.icon = ICON if os.path.isfile(os.path.join(folder, ICON)) else None
        readme = os.path.join(folder, "README.md")
        self.readme = open(readme, encoding="utf-8").read() if os.path.isfile(readme) else ""
        self.adds = self.shows = None          # blurbs from the README table
        self.zip_size = 0

    @property
    def summary(self):
        if self.shows:
            return self.shows
        first = self.description[0] if self.description else ""
        return html.escape(re.split(r"(?<=[.!?])\s", first, maxsplit=1)[0])

    def contributes_text(self):
        parts = []
        for key, items in self.contributes.items():
            one, many = CONTRIBUTES.get(key, (key.rstrip("s"), key))
            parts.append("%d %s" % (len(items), one if len(items) == 1 else many))
        return ", ".join(parts)


def _paragraphs(text):
    """A manifest's hard-wrapped triple-quoted description -> list of paragraphs."""
    blocks = re.split(r"\n\s*\n", textwrap.dedent(text).strip())
    return [" ".join(b.split()) for b in blocks if b.strip()]


def _inline(md_text):
    """Render one table cell's worth of Markdown, without the <p> wrapper."""
    out = markdown.markdown(md_text.strip())
    return re.sub(r"^<p>(.*)</p>$", r"\1", out, flags=re.S)


def read_table_order():
    """-> [(id, contributes_html, demonstrates_html)] from the README's table."""
    rows = []
    path = os.path.join(ROOT, "README.md")
    for line in open(path, encoding="utf-8"):
        m = re.match(r"\|\s*\[`?([\w.\-]+)`?\]\(extensions/[\w.\-]+/?\)\s*\|(.*)\|\s*$", line)
        if m:
            cells = [c.strip() for c in m.group(2).split("|")]
            cells += [""] * (2 - len(cells))
            rows.append((m.group(1), _inline(cells[0]), _inline(cells[1])))
    return rows


def load_extensions():
    found = {}
    for name in sorted(os.listdir(EXTENSIONS)):
        folder = os.path.join(EXTENSIONS, name)
        if os.path.isfile(os.path.join(folder, "extension.toml")):
            ext = Extension(folder)
            found[ext.id] = ext
    ordered = []
    for ident, adds, shows in read_table_order():
        ext = found.pop(ident, None)
        if ext:
            ext.adds, ext.shows = adds, shows
            ordered.append(ext)
    return ordered + sorted(found.values(), key=lambda e: e.name.lower())


# ---------------------------------------------------------------------------
# downloads
# ---------------------------------------------------------------------------

def write_zip(ext, out_dir):
    """Zip the extension folder under a single top-level folder named for its
    id, leaving out weights and build noise (the same rules as package.py)."""
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, ext.id + ".zip")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dirpath, dirnames, filenames in os.walk(ext.folder):
            dirnames[:] = sorted(d for d in dirnames if d not in EXCLUDE_DIRS)
            for name in sorted(filenames):
                if name in EXCLUDE_NAMES or os.path.splitext(name)[1] in EXCLUDE_EXT:
                    continue
                full = os.path.join(dirpath, name)
                z.write(full, os.path.join(ext.id, os.path.relpath(full, ext.folder)))
    ext.zip_size = os.path.getsize(out)


def _size(n):
    return "%d KB" % max(1, round(n / 1024)) if n < 1024 * 1024 else "%.1f MB" % (n / 1048576)


# ---------------------------------------------------------------------------
# README -> page body
# ---------------------------------------------------------------------------

def render_readme(ext, page_dir):
    text = ext.readme
    text = re.sub(r"\A\s*# .*\n+", "", text)     # the page header already names it
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists"])

    def fix(m):
        attr, url = m.group(1), html.unescape(m.group(2))
        if re.match(r"^([a-z][a-z0-9+.-]*:|#|/)", url, re.I):
            return m.group(0)                     # absolute, anchor, or site-rooted
        path, _, frag = url.partition("#")
        local = os.path.normpath(os.path.join(ext.folder, path))
        if attr == "src" and os.path.splitext(path)[1].lower() in IMAGE_EXT and os.path.isfile(local):
            dest = os.path.join(page_dir, path)
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(local, dest)
            return m.group(0)                     # served next to the page
        repo_path = posixpath.normpath(posixpath.join("extensions", ext.id, path))
        if repo_path.startswith(".."):
            return m.group(0)
        kind = "raw" if attr == "src" else "blob"
        url = "%s/%s/%s/%s" % (REPO_URL, kind, BRANCH, repo_path) + ("#" + frag if frag else "")
        return '%s="%s"' % (attr, html.escape(url))

    return re.sub(r'(href|src)="([^"]*)"', fix, body)


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

def page(title, body, root, description=""):
    return """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="icon" href="{root}favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="{root}style.css">
</head>
<body>
<header class="site">
  <div class="wrap">
    <a class="brand" href="{root}">Silhouette Extensions</a>
    <nav><a href="{repo}/discussions">Discussions</a><a href="{repo}/blob/{branch}/CONTRIBUTING.md">Contribute</a><a href="{repo}">GitHub</a></nav>
  </div>
</header>
<main class="wrap">
{body}
</main>
<footer class="site">
  <div class="wrap">
    Extensions for <a href="https://borisfx.com/products/silhouette/">Silhouette</a> by Boris FX.
    Released under the <a href="{repo}/blob/{branch}/LICENSE">BSD 3-Clause License</a>.
  </div>
</footer>
</body>
</html>
""".format(title=html.escape(title), description=html.escape(description), root=root,
           body=body, repo=REPO_URL, branch=BRANCH)


def meta_line(ext):
    bits = ["v" + html.escape(ext.version)]
    if ext.requires:
        bits.append("Silhouette %s+" % html.escape(ext.requires))
    if ext.contributes_text():
        bits.append(html.escape(ext.contributes_text()))
    return " · ".join(bits)


def icon_html(ext, prefix, cls):
    """The extension's icon, or a lettered tile standing in for it."""
    if ext.icon:
        return '<img class="%s" src="%s%s" alt="" width="64" height="64">' % (cls, prefix, ext.icon)
    letter = html.escape((ext.name.strip() or ext.id)[:1].upper())
    return '<span class="%s tile" aria-hidden="true">%s</span>' % (cls, letter)


def download_button(ext, root):
    return ('<a class="button" href="{root}downloads/{id}.zip" download>'
            'Download <span class="size">{size} zip</span></a>').format(
                root=root, id=html.escape(ext.id), size=_size(ext.zip_size))


INSTALL_STEPS = """<ol class="steps">
  <li><strong>Download</strong> the extension.</li>
  <li><strong>Drag it onto Silhouette's window</strong>: the .zip, or the folder it
      unzips to.  Or use <em>Preferences → Extensions → Install</em>.</li>
  <li><strong>Confirm</strong>, and it's ready.  Disable or remove it any time on the
      same Preferences page.</li>
</ol>"""


def index_page(exts):
    cards = []
    for ext in exts:
        shot = ('<img class="shot" src="{id}/{s}" alt="" loading="lazy">'
                .format(id=html.escape(ext.id), s=ext.screenshot) if ext.screenshot else "")
        adds = '<p class="adds">%s</p>' % ext.adds if ext.adds else ""
        cards.append("""<article class="card">
  {shot}
  <div class="title">
    {icon}
    <div>
      <h2><a href="{id}/">{name}</a></h2>
      <p class="meta">{meta}</p>
    </div>
  </div>
  {adds}
  <p class="summary">{summary}</p>
  <div class="actions">{download}<a class="more" href="{id}/">Details</a></div>
</article>""".format(shot=shot, icon=icon_html(ext, html.escape(ext.id) + "/", "icon"),
                     id=html.escape(ext.id), name=html.escape(ext.name),
                     meta=meta_line(ext), adds=adds, summary=ext.summary,
                     download=download_button(ext, "")))

    body = """<section class="hero">
  <h1>Add to Silhouette</h1>
  <p class="lede">Nodes, themes, panels, and menu commands for Silhouette, written
  in Python and free to use, copy, and take apart.  Each one is a working
  example of the extension API, so it's also the quickest way to learn it.</p>
</section>
<section class="install">
  <h2>Installing an extension</h2>
  {steps}
</section>
<section class="grid">
{cards}
</section>
<section class="write-your-own">
  <h2>Writing your own</h2>
  <p>Start from the extension closest to what you want, give it a new id, and
  read the <em>Extensions</em> chapter of Silhouette's scripting documentation
  (<em>Help → Scripting</em>).  To share yours here, see
  <a href="{repo}/blob/{branch}/CONTRIBUTING.md">Contributing</a>.</p>
</section>
<section class="write-your-own">
  <h2>Questions and ideas</h2>
  <p>Ask how to install, use, or write an extension, suggest one you'd like to
  see, or show off what you've built, in
  <a href="{repo}/discussions">Discussions</a>.  Found a bug in one of these?
  <a href="{repo}/issues/new/choose">Open an issue</a>.</p>
</section>""".format(steps=INSTALL_STEPS, cards="\n".join(cards), repo=REPO_URL, branch=BRANCH)
    return page("Silhouette Extensions", body, "",
                "Free extensions for Silhouette: nodes, themes, panels, and menu commands.")


def extension_page(ext, page_dir):
    shot = ('<img class="shot" src="%s" alt="">' % ext.screenshot) if ext.screenshot else ""
    body = """<p class="crumbs"><a href="../">All extensions</a></p>
<section class="ext-header">
  <div class="title">
    {icon}
    <div>
      <h1>{name}</h1>
      <p class="meta"><code>{id}</code> · {meta}</p>
    </div>
  </div>
  <p class="lede">{summary}</p>
  <div class="actions">{download}<a class="more" href="{repo}/tree/{branch}/extensions/{id}">Source on GitHub</a></div>
</section>
{shot}
<section class="install compact">
  <h2>Installing</h2>
  {steps}
</section>
<article class="readme">
{readme}
</article>""".format(name=html.escape(ext.name), id=html.escape(ext.id), meta=meta_line(ext),
                     icon=icon_html(ext, "", "icon large"),
                     summary=ext.summary, download=download_button(ext, "../"), shot=shot,
                     steps=INSTALL_STEPS, readme=render_readme(ext, page_dir),
                     repo=REPO_URL, branch=BRANCH)
    return page("%s · Silhouette Extensions" % ext.name, body, "../",
                re.sub(r"<[^>]+>", "", ext.summary))


# ---------------------------------------------------------------------------

def build(out):
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)
    for name in os.listdir(ASSETS):
        shutil.copy2(os.path.join(ASSETS, name), out)

    exts = load_extensions()
    for ext in exts:
        write_zip(ext, os.path.join(out, "downloads"))
        page_dir = os.path.join(out, ext.id)
        os.makedirs(page_dir)
        for extra in (ext.screenshot, ext.icon):
            if extra:
                shutil.copy2(os.path.join(ext.folder, extra), page_dir)
        with open(os.path.join(page_dir, "index.html"), "w", encoding="utf-8") as f:
            f.write(extension_page(ext, page_dir))
        print("%-28s %-8s %8s" % (ext.id, ext.version, _size(ext.zip_size)))
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_page(exts))
    print("-> %s" % os.path.relpath(out))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=os.path.join(ROOT, "_site"), help="output folder")
    build(os.path.abspath(ap.parse_args().out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
