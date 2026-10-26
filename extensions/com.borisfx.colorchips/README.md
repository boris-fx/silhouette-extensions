# Color Chips

Sample colours off the image and label them with swatch cards. Each of up to four
chips averages the block of pixels under its point and hangs a card beside it: the
swatch, the nearest [XKCD colour-survey](https://xkcd.com/color/rgb/) name, and the
sRGB value (plus alpha, when the image has an alpha channel). In the spirit of
Lewis Saunders' Ls_Chips Flame Matchbox — a fresh implementation; no code is shared.

## Wiring

```
  Source ──► Color Chips ──► Output
  Tracker ──(objects)──► Color Chips (Transform)      optional
```

## Properties

**Chip 1 – 4** — `Enable` (chip 1 is on by default), `Position` (a viewer point),
`Nearest Name`, `Override` / `Override Color`. With Nearest Name on, the card shows
the named colour itself; off, the exact sample, named only on a (near-)exact match
and otherwise by its hex code. A point outside the frame hides its chip.

**Options** — `Block Size` (pixels averaged per chip, on a fixed grid), `Background`
(Image / Blocky / Palette / Diagram / None), `Show Color Name`, `Show Color Value`,
`Readout` (0-255 / 0-1 / Hex), `Chip Size`, `Shadow`.

- **Blocky** shows the image averaged at the Block Size — exactly what each chip samples.
- **Palette** replaces the image with every named colour, which the chips then sample.
- **Diagram** is a Voronoi diagram of the chip colours.
- **None** outputs just the chips over a transparent background (they still sample the input).

`Show Color Name` and `Show Color Value` (both on by default) toggle the card's two
text lines; the label strip shrinks to fit, and with both off each card is the bare
swatch. `Readout` is greyed out while the value is hidden.

## Tracking

Connect a Tracker's or Roto node's output to the **Transform** input, then pick a
tracker or layer from a chip Position's transform menu in the Property View. The
point (and its card) follows the track; dragging it on a tracked frame is mapped back
through the track.

## Colour handling

A float project is treated as scene-linear, an 8-bit one as sRGB: samples are
encoded to sRGB for naming and read-out, and names are matched by perceptual
distance (CIELAB ΔE). Cards and positions honour the session's pixel aspect.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml`       | manifest |
| `__init__.py`          | `autoload(__package__)` |
| `colorchips_node.py`   | the node (registered as `ColorChips`, label *Color Chips*) |
| `data/xkcd_rgb.txt`    | the 949 XKCD colour-survey names, CC0 (https://creativecommons.org/publicdomain/zero/1.0/) |
| `icon.png`             | the extension's icon: one of the node's chips |

Needs Silhouette 2027 or newer.  numpy ships with Silhouette.
