# Text

Create titles, labels, credits and burn-ins over a clip or transparency. Use the
rich-text editor to mix fonts, sizes, bold, italic, underline and colours within
the same message. The node starts with centred white text.

## Wiring

```
  Source ──► Text ──► Output
```

The image input is optional. With nothing connected, Text generates an RGBA
image over transparency. **Text Only** produces the text and shadow over
transparency even when an image is connected.

## Editing the message

Open the **Text** property and enable **Formatted Text** to use the font, size,
style and colour controls. Select part of the message to format just that text.
Plain text is also supported, including multiple lines.

**Global Font Scale** changes the size of the whole message while keeping its
relative character sizes. **Opacity** controls the combined text and shadow.
**Premultiply** is enabled by default; disable it when straight RGBA is needed.

## Position and Box modes

**Position** anchors the text on its on-screen point control. The default
position is `(0, 0)`, the centre of the session. Horizontal and vertical
alignment both default to **Center**, so the text centres itself on that point.
Choose Left/Center/Right/Justify and Top/Center/Bottom to change the anchor.
**Rotate**, **Scale** and **Center (Pivot)** transform this layout around its
pivot. Position moves both the layout and its pivot.

**Box** lays out text in a fixed rectangle in the session. Its **Left/Top** and
**Right/Bottom** fields display pixels while the rectangle is stored in session
coordinates. The default is a box in the middle of the image,
`Rect(-0.25, -0.25, 0.5, 0.5)` (x, y, width, height). Alignment places the text
inside it. Position and transform controls are disabled in this mode.

Both modes provide **Word Wrap**, **Clip to Box**, **Line Spacing** and
**Character Spacing**. Clipping is optional; output always stays inside the
session's image format.

## Drop shadow

Enable **Drop Shadow**, then set its colour, opacity, horizontal/vertical offset
and softness. Offsets and softness are in full-resolution pixels, and scale with
the proxy. Positive vertical offset moves the shadow down.

## Installation and requirements

Requires Silhouette 2027 or newer. Install the extension folder or its download
archive by dragging it onto Silhouette, or use **Preferences > Extensions >
Install**. Add **Text** from the node browser's **Image** category.

The node uses Silhouette's bundled PySide6 and numpy. Numpy is loaded through
`tools.ensure` when a node is added to a session; a missing package can be
installed through **Preferences > Packages**. No external service, model or
account is needed. It also works in command-line renders.

The Text node's saved type is `Text`, including projects made with the earlier
Script Editor version. Use this extension as the source of that node; remove the
earlier development copy from `resources/scripts/nodes` if you installed it
there yourself.

## Live editing

Open `text_node.py` in Silhouette's Script Editor and run it to create a Text
node, or hot-swap the selected Text node while keeping its values and matching
connections. Normal extension loading only registers the node.

## How it is built

A render-local `QTextDocument` lays out and paints the message into a float RGBA
image. Numpy performs premultiplied source-over composition and the shadow blur.
The renderer preserves HDR input values and handles proxy resolution, tiled
rendering and non-square pixels. Compiled dependencies are imported in the
node's main-thread `setup()`; Qt documents, painters and images are local to each
render call.

| Path | Purpose |
|------|---------|
| `extension.toml` | extension metadata and node contribution |
| `__init__.py` | loads the extension's top-level Python modules |
| `text_node.py` | the Text node |
| `icon.png` / `icon.svg` | extension icon and its vector source |
| `tests/text_node_regression.py` | headless rendering and layout regression checks |

With the extension loaded, run the rendering checks in a headless Silhouette:

```python
import runpy
checks = runpy.run_path("/path/to/com.borisfx.text/tests/text_node_regression.py")
checks["run"]()
```

They use detached sessions and cover rich text, alignment, world-coordinate
position/box layout, spacing, clipping, transforms, shadows, proxy/tiled renders,
RGB/RGBA backgrounds, opacity, premultiplication and HDR composition.
