# Color Patch

Fill a region with colour sampled around it — a quick clean-plate patch for tracking
markers, blemishes, signs and small objects. Two modes:

- **Corners** — four points; each averages the block of pixels under it, and the quad
  between them is filled with a smooth bilinear blend of the four samples.
- **Shapes** — a shape picked from the **Transform** input becomes an N-gon patch, filled
  on the GPU with the mean value interpolation of colours sampled round its edge.

## Wiring

```
  Source ──► Color Patch ──► Output
  Roto / Tracker ──(objects)──► Color Patch (Transform)     Corners: optional tracking
                                                             Shapes: the shape to fill
  Matte ──► Color Patch (Obey Matte)                         optional
```

## Properties

**Mode** — Corners or Shapes.

**Corners** — `Transform` (below), then `Top Left`, `Top Right`, `Bottom Right`,
`Bottom Left`: four viewer points, a box in the middle of the frame by default. The quad may be any shape
(including non-convex); each pixel's blend comes from the inverse bilinear map of
the quad, so every corner gets exactly its own sample.

**Sample Offsets** (inside Corners) — per corner, where its colour is sampled relative to it (shown in
pixels). The gradient still runs exactly between the corners, but a corner placed on
an edge can take its colour from just off it, so the edge's own colour isn't smeared
into the patch. Drag them with the **Edit** tool (below).

**Shapes** (Shapes mode):
- **Shape** — the shape to fill, picked from the shapes on the Transform input (those
  inside layers too, listed as `Layer/Shape`). With none picked the input passes
  through untouched. Only closed shapes with at least three points fill; the pick's
  blend mode, inversion and opacity are ignored, and it fills even while hidden.
- **Sample** — *Along Edge* samples all the way round the shape, every Edge Spacing
  pixels, so the fill meets the image everywhere on its edge (a fast stand-in for a
  Laplace fill). *At Points* samples only at the shape's control points, for a smooth
  gradient between their colours.
- **Edge Spacing** — pixels between samples along the edge (Along Edge).
- **Sample Offset** — where the colours come from, in pixels from the shape's edge
  along its normals: positive outside the shape, negative inside it. The patch still
  covers exactly the shape. A roto drawn tight round the subject can take its colours
  from clean ground beyond the subject's fringe (a positive offset with Fill: Inside).
  A negative offset suits a loose shape, or Fill: Outside taken from deeper inside the
  kept subject. Away from 0 the fill no longer matches the plate exactly at the shape's
  edge, so pair it with a little **Softness**.

A patch takes at most 4096 samples. Along Edge widens its spacing to fit; At Points
uses that many of the control points, spread evenly round the shape.
- **Fill** — *Inside* replaces the shape, from samples just outside it. *Outside*
  keeps the shape and replaces the rest of the frame, from samples just inside its edge.

**Sample Size** — the width in pixels of the square block each sample averages. A
block off the frame samples the nearest block inside it. In Shapes mode each block
sits just far enough off the sampling line (the edge moved by Sample Offset), on the
side being kept, that none of its pixels cross a straight stretch of it: half its width for an edge running along the pixel grid, and up
to about 0.71 of its width for a diagonal one. Each block also averages only the pixels
wholly on the kept side of the sampling line, so at a sharp corner, where no block
placement can stay clear, it still never picks up the object being covered. That side
is the shape grown or shrunk by exactly the offset, with rounded corners. A sample whose
block has no such pixels is skipped.

**Softness** — blurs the patch's edge, centred on the quad or shape. The same control
as Roto's softness: shown in pixels, stored relative to the frame height (0–100 % of
it), default 0.

**Obey Matte** — the standard group: the patch is applied only where the matte
allows (channel, invert, opacity). Applied by the node itself, as Python nodes don't
get the native obey-matte pass.

## Edit tool

In Corners mode the viewer's **Edit** tool (the node's default tool) draws the quad, the
corner points and the square each corner samples, with a line back to its corner when it's
offset. Drag a corner point to move the corner; drag inside a square to move only where
that corner samples. The corner wins where the two overlap (a square surrounds its corner
until it's offset); **Shift**-drag a corner point to move its sample instead of the corner.
One undo entry per drag.

Everything is drawn where it renders. With a node-level **Transform** picked, that is
the tracked quad: the tool draws the corner points itself and maps each drag back
through the inverse transform, so a corner lands under the pointer on the tracked
frame. (The tool is an `fx.EditTool` subclass, and the pickers are Python object
properties, so it needs a Silhouette build with both.)

## Tracking

Corners: connect a Tracker's or Roto node's output to the **Transform** input, then
either
- pick a tracker or layer in **Corners > Transform** to move all four corners (and
  their sample offsets) together, or
- pick one from an individual corner's transform menu in the Property View, so that
  corner follows the track. Its sample offset turns and scales with the track as well
  as moving with it, and dragging the sample maps back through the track.

Both can be used together: the node-level Transform applies after a corner's own link.

Shapes: the picked shape follows its own layers' transforms (tracked layers included),
as Roto draws it. Disconnecting the Transform input clears both picks.

## How Shapes mode fills

Each shape is flattened to a polyline, and the sample centres form a polygon whose
vertices carry the sampled colours. Every pixel gets the blend given by **mean value
coordinates** (Hormann & Floater 2006), which interpolate the vertex colours smoothly
inside any simple polygon, convex or not. Outside, the signed weights would nearly
cancel and overshoot, so the exterior weights every edge by the angle it subtends: a
convex blend that still meets the edge exactly. Inside with a negative Sample Offset
uses the same weights, since part of the patch then lies outside the ring of samples.
(The shader sums any number of boundary
loops, MVC's form for a region with holes, though one shape needs one.) The
interpolation is one WGSL compute
dispatch per patch (`fx.WGPUShader`), with the samples passed in a table texture;
sampling, coverage and compositing stay in numpy.

Shapes mode needs the GPU: with no GPU device (or the GPU preference off) it reports
why and passes the input through.

## Notes

- The output is always RGBA. The patch is composited through its matte (the
  softened coverage times the Obey Matte) on all four channels, carrying the
  sampled alpha. An RGB input is promoted with a transparent alpha (not opaque), so
  its output alpha is exactly the patch's matte.
- The edges are anti-aliased with an `fx.draw` coverage mask. The output DOD is the
  input's grown to cover the quad or the picked shape plus the softness blur (the whole
  frame for Outside).
