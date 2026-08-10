# Sample Panel — a dockable panel from an extension

The smallest useful example of an extension that contributes **UI**: it adds a
dockable panel built with PySide6. Loading the extension creates the panel and
its *Window* menu entry; disabling or uninstalling it removes both, with no
restart.

## What you get

A **Sample Panel** entry in the *Window* menu, docked on the right. The panel
holds a label and a button that counts its own clicks — just enough to prove
the widget is live and receiving events.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml` | manifest — declares the contributed panel id |
| `__init__.py`    | `autoload(__package__)` — imports every public top-level module |
| `panel.py`       | builds the widget and registers the panel |

## How it works

Three lines carry the whole example:

```python
fx.unregisterPanel(PANEL_ID)
fx.registerPanel(PANEL_ID, "Sample Panel", make_panel, area="right", sort=100)
```

- **`make_panel` is a factory, not a widget.** Silhouette calls it when the
  panel is first shown, so an extension that is loaded but never opened costs
  nothing. It returns a plain `QWidget`.
- **The id is the persistence key.** `PANEL_ID` is what the window layout
  remembers, so a panel keeps its dock slot across restarts. The title is
  display-only — change it freely; changing the id makes a new panel.
- **`unregisterPanel` first keeps re-import idempotent.** Re-enabling an
  extension re-runs the module, and unregistering an id that isn't registered
  is a no-op.
- **Teardown is automatic.** The loader tags every registration with the
  extension being imported, so the panel disappears when the extension is
  disabled — nothing to clean up by hand.

`area` accepts the usual dock sides (`"left"`, `"right"`, `"top"`,
`"bottom"`); `sort` orders the *Window* menu entry among its neighbours.

## Install

Install via *Preferences → Extensions → Install…* (folder or archive), or point
`SFX_EXTENSION_PATHS` (or the `extensions.paths` preference) at a checkout and
relaunch. Then open *Window → Sample Panel*.

## Where to go next

Panels are plain PySide6, so anything Qt can do works here — but keep object
model changes on the main thread and wrap edits that should be undoable in
`fx.beginUndo()` / `fx.endUndo()`. See the *Extensions* chapter of the
Silhouette scripting documentation for the full panel API.
