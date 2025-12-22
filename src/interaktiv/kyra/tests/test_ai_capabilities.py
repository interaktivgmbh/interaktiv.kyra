import unittest

from interaktiv.kyra.services.ai_capabilities import _capabilities_for
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone import api
from plone.app.testing import TEST_USER_ID, logout, setRoles


class TestAICapabilities(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])
        self.doc = api.content.create(
            container=self.portal, type="Document", id="cap-test", title="Cap"
        )

    def test_capabilities_anonymous(self):
        logout()
        result = _capabilities_for(self.doc)
        self.assertTrue(result["is_anonymous"])
        self.assertFalse(result["can_edit"])

    def test_capabilities_editor(self):
        result = _capabilities_for(self.doc)
        self.assertFalse(result["is_anonymous"])
        self.assertTrue(result["can_edit"])
