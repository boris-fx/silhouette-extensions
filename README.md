# Silhouette Extensions

Add tools for titles, cleanup, image restoration, color sampling, and node-graph
organization to [Silhouette](https://borisfx.com/products/silhouette/). Browse the
extensions below, download the ones you need, and drag them onto Silhouette to
install. Each extension can be enabled or disabled individually.

## The extensions

| Extension | What it does | When to use it |
|---|---|---|
| [`com.borisfx.text`](extensions/com.borisfx.text) | Titles, labels, credits, and burn-ins | Add styled text over footage or transparency, with wrapping, alignment, and a soft shadow |
| [`com.borisfx.slate`](extensions/com.borisfx.slate) | Submission slates and shot overlays | Present shot details with a clip preview, or burn them into the image; empty fields fill from the project |
| [`com.borisfx.colorpatch`](extensions/com.borisfx.colorpatch) | Patches for markers, blemishes, and small objects | Fill a four-corner region or roto shape with color sampled from the surrounding image |
| [`com.borisfx.nafnet`](extensions/com.borisfx.nafnet) | AI image deblurring | Reduce blur with the included GoPro deblurring model, downloaded when you first need it |
| [`com.borisfx.stability`](extensions/com.borisfx.stability) | AI image generation and editing | Generate images, replace or erase objects, extend the frame, and remove backgrounds; requires a Stability AI account and API key |
| [`com.borisfx.colorchips`](extensions/com.borisfx.colorchips) | Color samples with names and values | Compare up to four areas of an image using movable or tracked swatch cards |
| [`com.borisfx.nodecolors`](extensions/com.borisfx.nodecolors) | Automatic node colors | Recognize keyers, filters, transforms, and other node roles at a glance; customize a compact palette or recolor a selection |
| [`com.borisfx.deepwater`](extensions/com.borisfx.deepwater) | Two cool blue-green interface themes | Choose Deep Water for a darker interface or Shallows for a lighter one |
| [`com.borisfx.samplepanel`](extensions/com.borisfx.samplepanel) | A movable, dockable sample panel | Try a simple click-counter panel; a developer sample for building custom tools |
| [`com.borisfx.testmenu`](extensions/com.borisfx.testmenu) | Sample menu commands | Try commands in the main menu and node-graph context menu; a developer sample rather than a production tool |
| [`com.borisfx.genai`](extensions/com.borisfx.genai) | A simulated video-generation workflow | Test submitting a clip and receiving a result without paid generation; returns the original clip rather than generating new imagery |

Each extension's page includes requirements, setup instructions, and details
about its controls.

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
