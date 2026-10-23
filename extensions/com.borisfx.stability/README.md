# Stability AI — cloud image generation

A Silhouette node that generates and edits images with
[Stability AI](https://stability.ai/)'s hosted API: text-to-image, inpaint,
outpaint, erase, search-and-replace, search-and-recolor, and background removal.
Generation runs as a **background task** — Silhouette stays usable while the
request is in flight, and the results land in the node's asset catalog for you
to pick from.

It is also a compact, real-world example of a cloud-service node: a node, a
task provider, and an fx-free worker process, in about 1,100 lines.

## Requirements

- **Silhouette 2027** or newer (it uses the task manager, asset catalog, and
  Asset Browser).
- A **Stability AI API key**, which needs an account with Stability. Their
  account and sign-up process changes from time to time — check
  [platform.stability.ai](https://platform.stability.ai/) for the current
  route to a key. Generation is billed to that account.

## Installing

Install it like any extension from this repo: drag the
`com.borisfx.stability` folder onto Silhouette's window (or use
*Preferences → Extensions → Install → From Folder…*), or point Silhouette at
a checkout's `extensions/` folder.

Then enter your key: *Preferences → Extensions*, select **Stability AI**, and
click **Options**. If you skip this, the node's **Generate** button offers to
open that dialog for you.

The key is stored in the `ml.stability_api_key` preference. Silhouette versions
that shipped this extension built in used the same preference, so a key you
entered there carries over.

## Using the node

1. **Save the project.** Results are stored inside the project bundle, so an
   unsaved project has nowhere to put them.
2. Add **Stability AI** (node browser → *AI/ML* or *Image*). Connect an input
   for the modes that edit an image; *Generate* works without one.
3. Pick a **Mode** and **Model**, fill in the prompts the mode uses (the others
   are disabled), and click **Generate**.
4. Watch progress in the Tasks view. When the task finishes, the node gets an
   attention badge and the **Asset Browser** shows the new images — click one
   to make it the node's output, or use the task's **Use Latest** button.

For **Inpaint** and **Outpaint**, the input's alpha is the mask. Turn on
**Composite** to lay the result back over (inpaint) or under (outpaint) the
input.

The modes, their endpoints, and the model list come from
`stabilityai_config.json`, so following an API change is often a config edit
rather than a code change.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml`           | manifest |
| `__init__.py`              | registers the API-key option, then `autoload(__package__)` |
| `PyStabilityAI.py`         | the node: properties, mode-dependent enabling, rendering the selected asset, and the Generate button |
| `stability_provider.py`    | the task provider (main side): preflight, render the input, build the request, publish results to the asset catalog |
| `_stability_worker.py`     | the worker: a standard-library-only subprocess that does the HTTP |
| `stabilityai_config.json`  | the mode and model tables |

## How it's built

**Three pieces, split by what they may touch.** The *node* only gathers
parameters and submits a task. The *provider* runs on the main thread with full
`fx` access: it renders and scales the input, resolves the endpoint and
arguments from the config, and writes a fully-resolved `job.json`. The *worker*
runs in a plain Python subprocess with no `fx` at all — just `urllib` — so it
can't touch the project and survives the request taking a while. It reports
progress and results as newline-delimited JSON on stdout.

**The task never changes the project.** When generation finishes, the provider
writes the images and their metadata (prompt, model, seed, mode, the input's
DOD) into the node's asset catalog — a sidecar manifest, not node properties —
and raises the node's badge. Choosing which image to use is the *user's*
action, so it's undoable and nothing changes behind their back.

**The node's output is just "the selected asset".** The `selected` property
holds an asset id. Changing it invalidates the output; `process()` reads that
image off disk, reframes it to the stored input DOD, scales it to the proxy,
and optionally composites it with the input.

**Credentials are an extension option.** `fx.prefs.add(..., option=True)` keeps
the key off the global Preferences pages; it appears only behind this
extension's *Options* button, and `fx.showExtensionOptions()` opens that dialog
from code. The key reaches the worker in an environment variable, never in
`job.json`.

**TLS.** The bundled interpreter's `ssl` module has no usable CA store on
macOS, so the provider finds `certifi`'s bundle on the main side and passes its
path to the worker in `SFX_CA_BUNDLE`.

## Older projects

Earlier versions of Silhouette had a built-in Stability node. Projects that use
it still open and still show the images they generated, but that node can no
longer generate — add this extension's **Stability AI** node instead.
