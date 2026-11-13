"""Curated intent matching and the undoable node action; Qt is only needed by options."""
import json
import re

import fx

PREF = "nodecolors.intentGroupColors"

# Fixed palette schema: (id, label, exact intent keywords, default RGB).
# Nuke 17.0v4 supplied the matching class colors. AI/ML's teal is our own;
# Warp shares Transform's purple. None preserves the node's default tint.
GROUPS = (
    ("input", "Input", ("input",), None),
    ("image", "Image", ("image",), None),
    ("roto", "Roto", ("roto",), "#71c671"),
    ("paint", "Paint", ("paint",), "#71c671"),
    ("keyer", "Keyer", ("keyer",), "#00ff00"),
    ("channel", "Channel", ("channel",), "#9e3c63"),
    ("color", "Color", ("color",), "#7aa9ff"),
    ("filter", "Filter", ("filter",), "#cc804e"),
    ("transform", "Transform", ("transform",), "#a57aaa"),
    ("warp", "Warp", ("warp",), "#a57aaa"),
    ("merge", "Composite / Merge", ("merge", "composite"), "#4b5ec6"),
    ("time", "Time", ("time",), "#b0a45d"),
    ("3d", "3D", ("3d",), "#006413"),
    ("ai/ml", "AI/ML", ("ai/ml",), "#55b8b8"),
    ("utility", "Utility", ("utility",), None),
    ("output", "Output", ("output",), "#bfbf00"),
)
LABELS = {key: label for key, label, tags, color in GROUPS}
TAG_GROUP = {tag: key for key, label, tags, color in GROUPS for tag in tags}
DEFAULTS = {key: color for key, label, tags, color in GROUPS if color is not None}
_HEX = re.compile(r"#[0-9a-fA-F]{6}\Z")


def normalize(value):
    return value.strip().casefold()


def encode(mapping):
    return json.dumps(mapping, sort_keys=True)


def mappings():
    """None means unloaded. An empty table is a valid, fully unmapped palette."""
    if PREF not in fx.prefs.keys():
        return None
    try:
        data = json.loads(fx.prefs[PREF])
        if not isinstance(data, dict):
            return dict(DEFAULTS)
        return {normalize(group): color.lower() for group, color in data.items()
                if isinstance(group, str) and normalize(group) in LABELS
                and isinstance(color, str) and _HEX.fullmatch(color)}
    except (TypeError, ValueError):
        return dict(DEFAULTS)


def color_for(node, mapping):
    info = fx.getNodeInfo(node.type) or {}
    for intent in info.get("intents", []):
        group = TAG_GROUP.get(normalize(intent))
        if group is not None:
            color = mapping.get(group)
            # A recognized but unmapped group deliberately keeps the node's
            # existing/default tint, rather than falling through to a secondary
            # technology tag (or a descriptive keyword such as "color").
            if color is not None:
                return fx.Color(*(int(color[i:i + 2], 16) / 255.0 for i in (1, 3, 5)))
            return None
    return None


def apply_color(node, mapping):
    color = color_for(node, mapping)
    if color is not None:
        # Node.color has no color Property: graph.color is the persisted UI tint.
        # setState records a SetObjectStateCommand inside the caller's undo block.
        node.setState("graph.color", color)


def object_created(obj):
    # Named hooks are replaced on reload but outlive disable. Option preferences
    # are removed on unload, so their presence also guards the retained hook.
    if isinstance(obj, fx.Node):
        mapping = mappings()
        if mapping is not None:
            apply_color(obj, mapping)


class UpdateNodeColors(fx.Action):
    def __init__(self):
        super().__init__("Update Colors", root="Node")

    def available(self):
        return any(isinstance(obj, fx.Node) for obj in fx.selection())

    def execute(self):
        mapping = mappings()
        nodes = [obj for obj in fx.selection() if isinstance(obj, fx.Node)]
        if mapping is None or not nodes:
            return
        fx.beginUndo("Update Node Colors")
        try:
            for node in nodes:
                apply_color(node, mapping)
        finally:
            fx.endUndo()
