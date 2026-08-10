# Test Menu — actions and menus from an extension

The smallest useful example of an extension that contributes **commands**: it
adds three `Action`s, two in a new top-level menu and one in a view's context
menu. Loading the extension creates the menu; unloading removes it, with no
restart.

## What you get

- A top-level **Test** menu with **Say Hello** and **Say Goodbye**.
- **Test Trees Action** in the *Trees* (node graph) context menu, which prints
  the active node.

All three print to the Python console, so you can see exactly when they run.

## Files

| Path | Purpose |
|------|---------|
| `extension.toml` | manifest — declares the three contributed action classes |
| `__init__.py`    | `autoload(__package__)` — imports every public top-level module |
| `test_menu.py`   | the three actions, registered with `addAction()` |

## How it works

An action is a class deriving from `fx.Action` that sets its label and
placement in `__init__` and does the work in `execute()`:

```python
class TestSayHello(Action):
    """Say hello from the Test menu."""

    def __init__(self):
        Action.__init__(self, "Say Hello", root="Test", order=1)

    def execute(self):
        print(f"[{self.extension}] Hello from the Test menu")

addAction(TestSayHello())
```

The placement rules worth knowing:

- **`root` chooses the menu.** A root that isn't an existing menu becomes a new
  top-level one — created when the extension loads, removed when its last
  action goes away. A root naming a view (`"Trees"`) puts the action in that
  view's context menu instead.
- **`hidden=True` keeps it out of the menubar** — the right choice for a
  context-menu-only action, as `TestTreesAction` shows.
- **A `|` in the label makes a submenu** (`"Utilities|Do Thing"`).
- **`order`** sorts actions within a menu.
- **The class docstring is the action's description**, so write it for the
  user; the class *name* is the action's stable id (`fx.action("TestSayHello")`
  returns the instance).
- **`self.extension`** is the id of the owning extension — handy for log
  prefixes like the ones above.

Two hooks these three actions don't need, but most real ones do: `available()`
returns whether the action can run right now (unavailable actions show as
disabled, and an `assert` message becomes the disabled reason), and object-model
changes inside `execute()` belong in `beginUndo()` / `endUndo()`.

Teardown is automatic: the loader tags each registration with the extension
being imported, so disabling the extension removes its actions and any menu
that existed only for them.

## Install

Install via *Preferences → Extensions → Install…* (folder or archive), or point
`SFX_EXTENSION_PATHS` (or the `extensions.paths` preference) at a checkout and
relaunch. The **Test** menu appears in the menubar.

## Where to go next

`execute()` runs on the main thread, so it may freely read and modify the
project. Actions are also the unit keyboard shortcuts bind to, and a `hidden`
action stays reachable by shortcut, by `fx.action(id)`, and from context menus.
See *Extensions → Actions* in the Silhouette scripting documentation.
