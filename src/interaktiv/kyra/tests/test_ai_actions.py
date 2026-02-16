import unittest

from interaktiv.kyra.services.ai_actions import _apply_actions
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

    def test_apply_actions_rejects_unknown_type(self):
        actions = [
            {"type": "update_title", "payload": {"title": "New Title"}},
        ]
        with self.assertRaises(Exception):
            _apply_actions(self.doc, actions)
