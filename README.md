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
| [`com.borisfx.nafnet`](extensions/com.borisfx.nafnet) | *NAFNet Restore* node | A real PyTorch node: on-demand dependencies, a vendored model, and **model weights fetched by a resumable background task** |
| [`com.borisfx.genai`](extensions/com.borisfx.genai) | *GenAI Emulator* node | The full asynchronous pipeline — render out, expose, submit, poll, download, publish — against a bundled fake "cloud", so it costs nothing to run |

Every extension has its own README explaining what it does and why it's built
the way it is. Start there.

## Installing

Either:

- **From an archive** — grab a `.sfxext` from the
  [Releases](https://github.com/boris-fx/silhouette-extensions/releases) page,
  then *Preferences → Extensions → Install…*, or just drag it onto Silhouette's
  window.
- **From a checkout** — clone this repo and point Silhouette at the
  `extensions/` folder, either with the `SFX_EXTENSION_PATHS` environment
  variable or the `extensions.paths` preference, then relaunch. This is the one
  to use while developing: your edits load on the next relaunch.

Extensions can be enabled, disabled, and removed individually on the
*Preferences → Extensions* page.

Requires Silhouette 2026.5 or newer.

## Model weights are not in this repository

The ML extensions don't ship their checkpoints — they're hundreds of megabytes
and don't belong in git. Instead the node reports a render error until its model
is installed, and its **Download Model** button fetches the weights as a
background task you can watch, pause, resume, and cancel in the Tasks view. See
that extension's README for the manual-install path too, for offline machines.

## Packaging

`scripts/package.py` builds installable archives:

```sh
./scripts/package.py --all                              # every extension -> dist/
./scripts/package.py extensions/com.borisfx.testmenu    # just one
```

Each archive nests under a single folder named for the extension id, which is
the layout Silhouette's installer expects. Weights and build noise are excluded.

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
