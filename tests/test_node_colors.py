"""Curated-intent-color matching and lifecycle regressions; run with Python's unittest."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class Node:
    def __init__(self, node_type, color=None):
        self.type = node_type
        self.state = {} if color is None else {"graph.color": color}

    def setState(self, key, value):
        self.state[key] = value


class Action:
    def __init__(self, *args, **kwargs):
        pass


class NodeColorsTests(unittest.TestCase):
    def setUp(self):
        self.selected = []
        self.events = []
        self.info = {
            "Keyer": {"intents": ["keyer", "matte generation"]},
            "Mixed": {"intents": ["color", "ai/ml", "color correction"], "categories": ["Filter"]},
            "Unknown": {"categories": ["Image", "Silhouette"], "intents": ["color correction"]},
        }
        fx = types.SimpleNamespace(
            Node=Node, Action=Action, Color=lambda *args: args, prefs={},
            selection=lambda: self.selected,
            getNodeInfo=lambda node_type: self.info.get(node_type),
            beginUndo=lambda label: self.events.append(("begin", label)),
            endUndo=lambda: self.events.append(("end",)),
        )
        path = Path(__file__).resolve().parents[1] / "extensions/com.borisfx.nodecolors/colors.py"
        spec = importlib.util.spec_from_file_location("node_colors_test_subject", path)
        self.colors = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, fx=fx):
            spec.loader.exec_module(self.colors)
        self.fx = fx
        fx.prefs[self.colors.PREF] = self.colors.encode(self.colors.DEFAULTS)

    def test_creation_and_database_order(self):
        node = Node("Mixed")
        self.colors.object_created(node)
        self.assertEqual(node.state["graph.color"], tuple(c / 255 for c in (122, 169, 255)))

    def test_unmapped_keeps_default_or_custom_color(self):
        for node_type in ("Unknown", "Missing"):
            for original in (None, (0.1, 0.2, 0.3)):
                node = Node(node_type, original)
                before = dict(node.state)
                self.colors.object_created(node)
                self.assertEqual(node.state, before)
        self.colors.object_created(object())  # Shapes and sessions are ignored.

    def test_disabled_and_empty_mapping(self):
        for prefs in ({}, {self.colors.PREF: "{}"}):
            self.fx.prefs = prefs
            node = Node("Keyer")
            self.colors.object_created(node)
            self.assertEqual(node.state, {})

    def test_custom_mapping_and_invalid_entries(self):
        self.fx.prefs[self.colors.PREF] = json.dumps({
            "  KeYeR  ": "#123ABC", "Filter": "not a color", "Image": 27, "arbitrary description": "#abcdef",
        })
        self.assertEqual(self.colors.mappings(), {"keyer": "#123abc"})
        node = Node("Keyer")
        self.colors.object_created(node)
        self.assertEqual(node.state["graph.color"], tuple(c / 255 for c in (18, 58, 188)))

    def test_invalid_json_recovers_defaults(self):
        for value in ("oops", "null", "[]"):
            self.fx.prefs[self.colors.PREF] = value
            self.assertEqual(self.colors.mappings(), self.colors.DEFAULTS)

    def test_action_selected_nodes_one_undo_block(self):
        chosen = Node("Keyer")
        untouched = Node("Keyer")
        self.selected = [object(), chosen, Node("Unknown")]
        action = self.colors.UpdateNodeColors()
        self.assertTrue(action.available())
        action.execute()
        self.assertIn("graph.color", chosen.state)
        self.assertEqual(untouched.state, {})
        self.assertEqual(self.events, [("begin", "Update Node Colors"), ("end",)])

    def test_action_closes_undo_on_error(self):
        self.selected = [Node("Keyer")]
        with patch.object(self.colors, "apply_color", side_effect=RuntimeError("failed")):
            with self.assertRaises(RuntimeError):
                self.colors.UpdateNodeColors().execute()
        self.assertEqual(self.events[-1], ("end",))

    def test_legacy_preferences_are_separate(self):
        for pref in ("nodecolors.intentColors", "nodecolors.categoryColors"):
            self.fx.prefs = {pref: '{"color": "#123456"}'}
            self.assertIsNone(self.colors.mappings())

    def test_secondary_technology_tag_does_not_override_function(self):
        self.fx.prefs[self.colors.PREF] = '{"ai/ml": "#123456", "color": "#7aa9ff"}'
        node = Node("Mixed")
        self.colors.object_created(node)
        self.assertEqual(node.state["graph.color"], tuple(c / 255 for c in (122, 169, 255)))

    def test_unmapped_primary_group_does_not_fall_through(self):
        self.fx.prefs[self.colors.PREF] = '{"ai/ml": "#123456"}'
        node = Node("Mixed")
        self.colors.object_created(node)
        self.assertEqual(node.state, {})

    def test_merge_alias_and_case_insensitive_exact_matching(self):
        for tag in ("merge", " COMPOSITE "):
            self.info["Alias"] = {"intents": [tag]}
            self.assertEqual(self.colors.color_for(Node("Alias"), self.colors.DEFAULTS),
                             tuple(c / 255 for c in (75, 94, 198)))
        for tag in ("color correction", "3d track", "time remap", "keyer plugin"):
            self.info["Description"] = {"intents": [tag], "categories": ["Color"]}
            self.assertIsNone(self.colors.color_for(Node("Description"), self.colors.DEFAULTS))

    def test_neutral_image_tag_prevents_constant_from_becoming_color_op(self):
        self.info["Constant"] = {"intents": ["image", "color", "image generator"]}
        self.assertIsNone(self.colors.color_for(Node("Constant"), self.colors.DEFAULTS))

    def test_ai_ml_primary_group(self):
        self.info["AI"] = {"intents": ["AI/ML", "generative ai"]}
        self.assertEqual(self.colors.color_for(Node("AI"), self.colors.DEFAULTS),
                         tuple(c / 255 for c in (85, 184, 184)))

    def test_palette_is_curated(self):
        self.assertEqual(len(self.colors.GROUPS), 16)
        self.assertEqual(len(self.colors.LABELS), 16)
        self.assertEqual(len(self.colors.TAG_GROUP), 17)

    def test_action_no_selection(self):
        action = self.colors.UpdateNodeColors()
        self.assertFalse(action.available())
        action.execute()
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main()
