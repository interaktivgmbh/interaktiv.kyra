import unittest
from unittest.mock import Mock, patch

from interaktiv.kyra.services.ai_chat import AIChatService
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestAIChatService(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.app = self.layer["app"]
        self.portal = self.layer["portal"]
        self.request = self.layer["request"]
        setRoles(self.portal, TEST_USER_ID, ["Manager", "Site Administrator"])

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply__success(self, mock_api, mock_json_body):
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Hello"}]
        }
        mock_api.return_value.chat.send.return_value = {"response": "Hi there!"}

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertEqual(result["message"]["content"], "Hi there!")
        self.assertEqual(result["capabilities"]["features"], ["chat"])

    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_stream_events__token_and_done(self, mock_api):
        mock_api.return_value.chat.stream.return_value = (
            None,
            "404 Not Found",
        )
        mock_api.return_value.prompts.apply.return_value = {"response": "Hi"}

        service = AIChatService(self.portal, self.request)
        events = list(service._stream_events({"messages": []}, None))

        self.assertTrue(any("event: token" in event for event in events))
        self.assertTrue(any("event: done" in event for event in events))
