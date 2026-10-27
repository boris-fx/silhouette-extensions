# Silhouette Extensions

Example extensions for [Silhouette](https://borisfx.com/products/silhouette/) —
small, complete, and meant to be read, copied, and taken apart. Each one is a
working answer to "how do I add *that* to Silhouette?", from a colour theme with
no code at all to a full asynchronous pipeline with a background worker.

Silhouette extensions are plain folders: a manifest, and whatever Python,
themes, or assets the extension contributes. There is no build step and no
compiled component — edit a file, relaunch, and your change is live.

## The extensions

Roughly in order of how much there is to learn:

| Extension | What it contributes | What it demonstrates |
|---|---|---|
| [`com.borisfx.deepwater`](extensions/com.borisfx.deepwater) | Two blue-green UI themes | Themes from an extension — **no Python at all**; one theme inheriting another's palette |
| [`com.borisfx.testmenu`](extensions/com.borisfx.testmenu) | Three actions | `fx.Action`: a new top-level menu, a view context menu, availability and undo |
| [`com.borisfx.samplepanel`](extensions/com.borisfx.samplepanel) | A dockable panel | `fx.registerPanel` with PySide6; lazy widget creation, layout persistence, clean teardown |
| [`com.borisfx.slate`](extensions/com.borisfx.slate) | *Slate* node | Text layout with `Font.layout()` (alignment, multi-line columns), drawing a whole frame with `fx.draw`, an optional aux input, and fields that fill themselves in from the project |
| [`com.borisfx.colorchips`](extensions/com.borisfx.colorchips) | *Color Chips* node | Drawing with `fx.draw` (shapes, text, a soft shadow) over the image, viewer points that can follow a tracker, and colour maths with numpy |
| [`com.borisfx.colorpatch`](extensions/com.borisfx.colorpatch) | *Color Patch* node | GPU compute with `fx.WGPUShader` (a WGSL fill fed a table texture), picking a roto shape or a tracker on an input with object properties (`"type": "object"`), and extending the viewer's Edit tool with `fx.EditTool` |
| [`com.borisfx.nafnet`](extensions/com.borisfx.nafnet) | *NAFNet Restore* node | A real PyTorch node: on-demand dependencies, a vendored model, and **model weights fetched by a resumable background task** |
| [`com.borisfx.stability`](extensions/com.borisfx.stability) | *Stability AI* node | A real cloud service: image generation as a background task, results in an asset catalog, an API key kept as an extension option. Needs your own Stability API key |
| [`com.borisfx.genai`](extensions/com.borisfx.genai) | *GenAI Emulator* node | The full asynchronous pipeline — render out, expose, submit, poll, download, publish — against a bundled fake "cloud", so it costs nothing to run |

Every extension has its own README explaining what it does and why it's built
the way it is. Start there.

## Installing

The easiest way is the website, **[boris-fx.github.io/silhouette-extensions](https://boris-fx.github.io/silhouette-extensions/)**:
each extension has a one-click download.  Drag the zip (or the folder it unzips
to) onto Silhouette's window, or skip the download and drag the site's
**Download** button straight onto Silhouette.  Or, from this repo:

- **Drop a folder** — download this repo (*Code → Download ZIP*) and unzip
  it, then drag the extension's folder from `extensions/` onto Silhouette's
  window, or use *Preferences → Extensions → Install → From Folder…*.
  Silhouette copies it into your extensions folder, so you can delete the
  download afterwards.
- **From a checkout** — clone this repo and point Silhouette at the
  `extensions/` folder, either with the `SFX_EXTENSION_PATHS` environment
  variable or the `extensions.paths` preference, then relaunch. This is the one
  to use while developing: your edits load on the next relaunch.

Extensions can be enabled, disabled, and removed individually on the
*Preferences → Extensions* page.

Requires Silhouette 2027 or newer.

## Model weights are not in this repository

The ML extensions don't ship their checkpoints — they're hundreds of megabytes
and don't belong in git. Instead the node reports a render error until its model
is installed, and its **Download Model** button fetches the weights as a
background task you can watch, pause, resume, and cancel in the Tasks view. See
that extension's README for the manual-install path too, for offline machines.

## Sharing an extension as one file

Silhouette also installs a `.zip` or `.sfxext` archive of an extension folder.
`scripts/package.py` builds one, leaving out model weights and build noise:

```sh
./scripts/package.py extensions/com.borisfx.testmenu    # -> dist/com.borisfx.testmenu-1.0.0.sfxext
./scripts/package.py --all                              # every extension
```

## Questions, ideas, and bugs

- **Questions** about installing, using, or writing extensions →
  [Discussions: Q&A](https://github.com/boris-fx/silhouette-extensions/discussions/categories/q-a)
- **An extension you'd like to see** →
  [Discussions: Ideas](https://github.com/boris-fx/silhouette-extensions/discussions/categories/ideas)
- **Something you built** →
  [Discussions: Show and tell](https://github.com/boris-fx/silhouette-extensions/discussions/categories/show-and-tell)
- **A bug in one of these extensions**, or proposing a new one for this repo →
  [Issues](https://github.com/boris-fx/silhouette-extensions/issues/new/choose)

## Writing your own

The fastest start is to copy the extension closest to what you want and rename
it — the id in `extension.toml`, the folder, and any id references in the code.
Then read the *Extensions* chapter of Silhouette's scripting documentation
(*Help → Scripting*) for the full API: actions, nodes, panels, preferences,
themes, and task providers.

Two things worth knowing before you start:

- **The id is forever.** It keys preferences, saved layouts, and installed
  copies. Pick `com.yourcompany.thing` and leave it alone.
- **Everything an extension registers is torn down when it unloads**, because
  the loader tags registrations with the extension that made them. You don't
  write cleanup code; you do have to make module-level registration idempotent,
  since re-enabling an extension re-imports it.

## License

These extensions are released under the [BSD 3-Clause License](LICENSE) — copy
them, adapt them, ship what you build.

Some extensions vendor upstream model source, which keeps its own license and
attribution; the file header names the upstream in each case. See
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md). Model weights are not
distributed here and carry their own terms — check the upstream project before
using a checkpoint commercially.
