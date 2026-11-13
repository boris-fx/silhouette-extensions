# Deep Water Themes

Give Silhouette a cool blue-green interface. Choose **Deep Water** for a darker
palette or **Shallows** for a lighter alternative in brighter rooms.

## What you get

Two new entries in the **Theme** menu in Preferences:

- **Deep Water** — muted blue-greens with warm highlights for alerts.
- **Shallows** — a lighter version of Deep Water for bright rooms.

Contrast sits deliberately between the built-in *Classic* and *Low Contrast*:
flat enough that the UI stops competing with the image, not so flat that borders
vanish.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml`       | manifest |
| `themes/deep-water.json` | the full palette (26 roles) |
| `themes/shallows.json`   | 5 overrides + `"inherits": "deep-water"` |

There is no `__init__.py`, because there's nothing to import.

## How it works

Every enabled extension's `themes/` directory is added to the theme search
path, so dropping a `.json` in there is the whole mechanism — no registration
call, no Python.

A theme file is a name, a description, and a table of colour **roles**:

```json
{
    "name": "Deep Water",
    "description": "Cool desaturated blue-greens.",
    "colors": {
        "ViewBackground":   "#101c22",
        "WindowBackground": "#21353f",
        "ButtonFace":       "#3b5c69",
        "AccentPrimary":    "#5fb3a1"
    }
}
```

Two rules worth knowing:

- **The theme's id is its filename**, not its `name`. `deep-water.json` is the
  theme `deep-water`; `name` is only what the UI displays.
- **`"inherits"` names another theme by that id**, and the referenced theme is
  applied first, so you only restate the roles you want to change. That's what
  makes `shallows.json` five lines of colour instead of twenty-six. Because the
  reference is by theme id, it keeps working no matter which extension the
  parent lives in — including the built-in themes.

To build your own, copy `deep-water.json`, rename it, and edit. Start from a
theme whose contrast you like and override a few roles rather than writing all
26 from scratch.

## Install

Install via *Preferences → Extensions → Install…* (folder or archive), or point
`SFX_EXTENSION_PATHS` (or the `extensions.paths` preference) at a checkout and
relaunch. The two themes then appear in the **Theme** menu in Preferences.
