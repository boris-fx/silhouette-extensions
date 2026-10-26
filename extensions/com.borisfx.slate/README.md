# Slate

A node that builds a VFX submission slate from the clip it's given, following
the two layouts in Netflix's published VFX submission guidelines (*Slates &
Overlays*). Pick one with **Mode**:

- **PIP** (the default) — a dark slate card: the shot details on the left, the
  clip as a picture-in-picture thumbnail top-right, a strip of colour bars, a
  second column of details, and a vendor-logo box bottom-right.
- **Overlay** — the clip full-frame, with the same details burnt into its four
  corners (light text with a drop shadow, so it reads over any plate).

## Wiring

```
  Source ──► Slate ──► Output
  Logo ──(Vendor Logo)──► Slate      optional
```

Connect your company logo to the **Vendor Logo** input and it's fitted into the
logo box, keeping its aspect ratio and transparency. With nothing connected, PIP
mode shows a white placeholder box.

## Properties

**Mode**, **Show**, **Submitting For** (WIP / FINAL), **Version Name**,
**Date**, **Vendor**, **Shot Name**, **Frames**, **Media Color**.

Leave a field blank and it fills itself in when the slate renders:

| Field | Blank means |
|-------|-------------|
| Show | the project's name |
| Shot Name | the session's name |
| Date | today's date |
| Frames | the session's range: `first - last (count)` in PIP, `first - current - last` in Overlay |

The rest stay empty until you type something.

## How it's built

Everything is drawn with `fx.draw`: the card and colour bars are filled
rectangles, and the text is shaped with `Font.layout()`, which handles
alignment, line spacing, right-to-left and complex scripts, and font fallback.
Each column of details is a single `layout()` call, with the fields joined by
line separators, so labels and values stay in step. The thumbnail and the logo
are resampled straight into their boxes with `Raster.resize(dod=...)` and then
composited (or copied, for an image without alpha). Sizes are fractions of the
frame, so the slate looks the same at any proxy level.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml` | manifest |
| `__init__.py`    | `autoload(__package__)` |
| `slate_node.py`  | the node (registered as `Slate`, label *Slate*) |
| `icon.png`       | the extension's icon: a miniature of the PIP slate |

Needs Silhouette 2027 or newer. The node was first written as a development
demo registered as `PySlateNode`; that name is kept as an alias, so projects
that used it still open.
