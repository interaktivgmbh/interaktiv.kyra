import unittest

import plone.api as api
from interaktiv.kyra.api.chat import Chat
from interaktiv.kyra.registry.ai_assistant import IAIAssistantSchema
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestChatAPI(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.app = self.layer["app"]
        self.portal = self.layer["portal"]
        self.request = self.layer["request"]
        setRoles(self.portal, TEST_USER_ID, ["Manager", "Site Administrator"])

        api.portal.set_registry_record(
            name="gateway_url",
            interface=IAIAssistantSchema,
            value="http://localhost:8080/api/prompts",
        )

    def test_chat_url__uses_gateway_url(self):
        chat = Chat()
        self.assertEqual(chat._chat_url(), "http://localhost:8080/api/chat")
