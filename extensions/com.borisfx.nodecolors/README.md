# Node Colors

Make busy node graphs easier to read. Node Colors gives keyers, filters,
transforms, roto, paint, and other node roles consistent colors as you create
them. Customize a compact palette of 16 groups, or recolor selected nodes in
one undoable step.

## Requirements

- **Silhouette 2027 or newer.** No extra packages, accounts, or downloads are needed.
- Nodes need a matching intent tag to receive a color. Coverage in 2027 depends
  on the tags already supplied by each node. Expanded built-in tagging is on
  the **2027.5** development branch; the extension itself works with 2027.

## Get started

1. Drag the extension folder or downloaded archive onto Silhouette, or choose
   **Preferences → Extensions → Install → From Folder…**.
2. Create a node. If it has a recognized role, its color is assigned automatically.
3. To color existing nodes, select them and choose **Node → Update Colors**.
   A single Undo restores their previous colors.

## Customize your colors

Open **Preferences → Extensions**, find **Node Colors**, and open **Options**.

- Click a color swatch to choose a new color for that group.
- Use the filter field to find a group quickly.
- **Clear Selected** removes color assignments for the selected groups.
- **Restore Defaults** returns the palette to its starting colors.

Palette changes are saved immediately and apply to new nodes. Use **Update Colors**
to apply them to an existing selection. Clearing a mapping does not erase colors
already assigned to nodes. Disabling the extension stops automatic coloring;
existing node colors remain saved with the project.

## Palette

The table stays at 16 groups. Input, Image, and Utility start without a color
assignment, so those nodes retain their normal gray or a manually chosen tint.

| Group | Exact intent keywords | Default |
|---|---|---|
| Input | `input` | Default gray |
| Image | `image` | Default gray |
| Roto | `roto` | Green `#71c671` |
| Paint | `paint` | Green `#71c671` |
| Keyer | `keyer` | Green `#00ff00` |
| Channel | `channel` | Rose `#9e3c63` |
| Color | `color` | Blue `#7aa9ff` |
| Filter | `filter` | Orange `#cc804e` |
| Transform | `transform` | Purple `#a57aaa` |
| Warp | `warp` | Purple `#a57aaa` |
| Composite / Merge | `composite`, `merge` | Blue-violet `#4b5ec6` |
| Time | `time` | Khaki `#b0a45d` |
| 3D | `3d` | Dark green `#006413` |
| AI/ML | `ai/ml` | Teal `#55b8b8` |
| Utility | `utility` | Default gray |
| Output | `output` | Yellow `#bfbf00` |

The default palette uses familiar Nuke class colors where appropriate. AI/ML
uses teal; Warp shares Transform's purple. You can change any group's color.

## Why did a node keep its color?

A node may not yet have a recognized intent tag, or its group's color may be
cleared. The extension leaves these nodes unchanged. Nodes can appear on several
browser pages; their page placement does not determine their color.

When several recognized tags are present, the first one chooses the group. This
lets an ML denoiser use the same Filter color as other filters. If that group's
mapping is cleared, later tags do not override it.

## For node authors: intent tags

Use the exact keywords in the palette table in a node's intent list. Matching
ignores case and surrounding whitespace, but does not match parts of longer
phrases. `merge` and `composite` share one group. Put the primary role first,
followed by secondary classifications and search descriptions:

```python
intents=["filter", "ai/ml", "denoise", "degrain"]
intents=["keyer", "matte generation", "greenscreen"]
intents=["3d", "composite", "camera", "card"]
intents=["image", "color", "image generator"]  # a Constant
```

Register these keywords with `addNode(..., intents=[...])`. Descriptive tags do
not create additional palette rows. Built-in node metadata follows this convention
on the `silhouette-2027.5` branch; those additions are not required to load or use
the extension in 2027.

## Implementation

`object_created` and the selected-node action read `fx.getNodeInfo(node.type)`'s
`intents` and assign persisted `graph.color`. The preference
`nodecolors.intentGroupColors` stores only known group IDs; earlier free-form
intent/category preferences are separate and do not expand the curated table.
Named hooks replace themselves on reload and check for the option preference
before acting, because hooks survive disable while extension preferences do not.

Silhouette builds with the November 10, 2026 node-color changes tint both the
Clean node interior and thin outline. Older builds tint only the interior.

## Tests

From the repository root, run `python3 -B tests/test_node_colors.py`.

## Icon

`icon.svg` is the editable source for the 256 × 256 `icon.png` used by Silhouette
and the extension catalog. The connected colored nodes represent the extension's
automatic graph coloring.
