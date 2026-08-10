# NAFNet Restore — Silhouette Python node

A reference Python **node extension** for Silhouette: single-image restoration
with [NAFNet](https://github.com/megvii-research/NAFNet) (Simple Baselines for
Image Restoration, ECCV 2022). Companion to `com.borisfx.xvfi` — the same
end-to-end pattern (on-demand PyTorch, a vendored model, on-demand model
weights fetched by a background task) applied to a single-image model.

## What the node does

**NAFNet Restore** (node browser → *AI/ML* / *Image*). A per-frame filter — each
frame is restored independently (no temporal window). The **Model** popup
selects the task; the default is **GoPro deblurring** (NAFNet-width64).

NAFNet is plain standard PyTorch (no custom ops), so it runs on **CUDA** (Linux/
Windows NVIDIA) or **CPU**.  Apple **MPS** is **not used**: in-process, pip torch
is bound to the ML SDK's libtorch, whose MPS backend hard-crashes in `F.pad`
(`constant_pad_nd_mps`, `MPSAllocator.mm:654`) — confirmed from the full crash
stack, and NAFNet pads every forward.  That's a genuine SDK MPS-backend bug, so
Mac runs on **CPU** until the SDK libtorch is fixed.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml`          | manifest |
| `__init__.py`             | `autoload(__package__)` |
| `nafnet_node.py`          | the node (registered as `com.borisfx.silhouette.nafnet`, label *NAFNet Restore*) |
| `nafnet_download.py`      | the model-download task provider (main side) |
| `_download_worker.py`     | the download worker — fx-free, stdlib-only subprocess |
| `nafnet_config.json`      | model table: popup entries, NAFNet args, checkpoint filename, download info |
| `vendor/nafnet/nafnet.py` | model source, vendored from upstream (NAFNet_arch + arch_util + local_arch, assembled) |
| `models/`                 | checkpoints (see below) |

## Checkpoints — downloaded on demand

The `.pth` files (~260 MB for GoPro-width64) are **not** shipped with the
extension. Until the selected model's checkpoint is installed, the node reports
a render error naming the fix and produces no output — it can't restore without
the weights, and passing the input through would look like a result. Click
**Download Model** to fetch it:

- The download runs as a **background task** — watch, pause, resume, or cancel
  it in the Tasks view. It survives quitting Silhouette (it resumes from the
  partial file on relaunch) and verifies the file's SHA-256 before installing.
- When it finishes, the checkpoint lands in this extension's `models/` folder,
  every NAFNet node gets an attention badge and re-renders, and the task offers
  a *Show Node* button.

Downloads come from the URL in `nafnet_config.json`'s per-model `download`
block (`url` + `sha256` + `bytes`). A model without that block is
manual-install only.

**Manual install** (offline machines, or models with no published download):
drop the checkpoint into `models/` with the exact filename the config expects —
the node picks it up on the next render:

| Model | File (`models/…`) | Upstream download |
|-------|-------------------|-------------------|
| Deblur — GoPro (width64) | `NAFNet-GoPro-width64.pth` | [Google Drive](https://drive.google.com/file/d/1S0PVRbyTakYY9a82kujgZLbMihfNBLfC/view) |

Google Drive blocks a plain `curl` for large files — download via the browser
(or `gdown 1S0PVRbyTakYY9a82kujgZLbMihfNBLfC`). Once present, the folder is
self-contained: copy the whole extension and no network is needed at runtime.

To add another NAFNet task (e.g. **SIDD denoising**), add an entry to
`nafnet_config.json` with that model's `width`/`enc_blk_nums`/`middle_blk_num`/
`dec_blk_nums` (from its `options/test/…yml`) and its checkpoint filename, then
either add a `download` block for it or drop the `.pth` in `models/`.

Two practical notes:

- **Corporate networks.** The downloader verifies TLS against the CA bundle
  Silhouette ships (certifi). Behind a TLS-inspecting proxy, add your proxy's
  root certificate to that bundle, or install the checkpoint manually.
- **Updating the extension** replaces its folder, including `models/` — the
  node will ask to download again.

## Install

Install the extension via *Preferences → Extensions → Install…* (folder or
archive), or point `SFX_EXTENSION_PATHS` (or the `extensions.paths` preference)
at a checkout and relaunch. **NAFNet Restore** appears under *AI/ML*.

## Notes for node authors

- PyTorch is loaded on demand via `tools.ensure` (auto-pinned to the host's
  libtorch); never `import torch` at module top level.
- torch's first import must be on the main thread, so it's warmed up in
  `setup()` (called when the node is added to a Session), not in `process()`
  (render thread) — a first import there crashes.
- NAFNet trains on 256² patches; the `NAFNetLocal` variant (used here) replaces
  global average pooling with a fixed local window (TLC) so full-frame results
  don't degrade. The checkpoint is identical to plain NAFNet.
- **Large assets belong in a background task, not in `process()`.**
  `nafnet_download.py` is a complete task provider: a button property submits
  the task, an fx-free worker (`_download_worker.py`) does the transfer with
  pause/resume via the task store's marker files, and `on_result` (main thread)
  installs the file, badges the nodes, and invalidates their cached output.
  Until then `process()` reports `rc.setError(...)` and returns `None` — a node
  that can't perform its function should say so, not emit something that passes
  for a result. The check runs before any upstream evaluation, so a missing
  model costs nothing to render. See the scripting documentation's *Extensions*
  chapter for the provider contract.
- **Refreshing a cached output from outside the node takes two calls.** Once a
  frame has rendered, its raster sits in the port cache, so anything that
  changes the output *without* a property edit — a task that just delivered a
  model or a result — has to flush that cache and then ask the UI to
  re-request. `fx.Node.invalidate()` posts the UI event but leaves the cached
  raster; invalidating one of the node's own properties
  (`node.property("model").invalidate()`) drops the raster and re-runs
  `updatePropertyState()`, but posts no event. `on_result` does both, flush
  first — see `_refresh_nodes()`. From *inside* the node class, the single call
  `self.invalidateOutputs()` covers it.
