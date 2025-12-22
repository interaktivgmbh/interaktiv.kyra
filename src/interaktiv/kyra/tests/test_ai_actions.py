import unittest

from interaktiv.kyra.services.ai_actions import _apply_actions, _normalize_action
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone import api
from plone.app.testing import TEST_USER_ID, setRoles


class TestAIActions(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])
        self.doc = api.content.create(
            container=self.portal, type="Document", id="action-test", title="Old"
        )

    def test_apply_actions_updates_fields(self):
        actions = [
            {"type": "update_title", "payload": {"title": "New Title"}},
            {
                "type": "update_description",
                "payload": {"description": "New Description"},
            },
        ]
        changed = _apply_actions(self.doc, actions)
        self.assertIn("title", changed)
        self.assertIn("description", changed)
        self.assertEqual(self.doc.Title(), "New Title")
        self.assertEqual(self.doc.Description(), "New Description")

    def test_apply_actions_inserts_text_block(self):
        actions = [
            {"type": "insert_text_block", "payload": {"text": "Hallo, Test"}},
        ]
        changed = _apply_actions(self.doc, actions)
        self.assertIn("blocks", changed)
        self.assertTrue(getattr(self.doc, "blocks", None))
        self.assertTrue(getattr(self.doc, "blocks_layout", None))
        items = self.doc.blocks_layout.get("items") or []
        self.assertEqual(len(items), 1)

    def test_apply_actions_inserts_heading_block(self):
        actions = [
            {
                "type": "insert_heading_block",
                "payload": {"text": "Heading", "level": 3},
            }
        ]
        changed = _apply_actions(self.doc, actions)
        self.assertIn("blocks", changed)
        items = self.doc.blocks_layout.get("items") or []
        block = self.doc.blocks.get(items[0])
        self.assertEqual(block.get("@type"), "slate")
        self.assertEqual(block.get("value")[0].get("type"), "h3")

    def test_apply_actions_inserts_image_block(self):
        actions = [
            {
                "type": "insert_image_block",
                "payload": {"url": "https://example.com/test.jpg", "alt": "Alt"},
            }
        ]
        changed = _apply_actions(self.doc, actions)
        self.assertIn("blocks", changed)
        items = self.doc.blocks_layout.get("items") or []
        block = self.doc.blocks.get(items[0])
        self.assertEqual(block.get("@type"), "image")
        self.assertEqual(block.get("url"), "https://example.com/test.jpg")

    def test_normalize_heading_level_from_text(self):
        action = {
            "type": "insert_heading_block",
            "payload": {"text": "Heading h3"},
        }
        normalized = _normalize_action(action)
        self.assertEqual(normalized["payload"]["level"], 3)

    def test_normalize_ordered_list_from_text(self):
        action = {
            "type": "insert_list_block",
            "payload": {"text": "1. First 2. Second"},
        }
        normalized = _normalize_action(action)
        self.assertTrue(normalized["payload"]["ordered"])
        self.assertEqual(normalized["payload"]["items"], ["First", "Second"])

    def test_normalize_image_from_resolveuid_with_scale(self):
        action = {
            "type": "insert_image_block",
            "payload": {
                "url": "resolveuid/a58ccead718140c1baa98d43595fc3e6/@@images/image/preview"
            },
        }
        normalized = _normalize_action(action)
        self.assertEqual(
            normalized["payload"]["url"],
            "resolveuid/a58ccead718140c1baa98d43595fc3e6",
        )
        self.assertEqual(normalized["payload"]["scale"], "preview")
