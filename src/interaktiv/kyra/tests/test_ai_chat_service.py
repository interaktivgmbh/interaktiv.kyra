import unittest
from unittest.mock import Mock, patch

from interaktiv.kyra.services.ai_chat import AIChatService
from interaktiv.kyra.services import ai_context
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone import api
from plone.app.testing import TEST_USER_ID, setRoles


class TestAIChatService(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.app = self.layer["app"]
        self.portal = self.layer["portal"]
        self.request = self.layer["request"]
        setRoles(self.portal, TEST_USER_ID, ["Manager", "Site Administrator"])
        self.sample_doc = api.content.create(
            container=self.portal,
            type="Document",
            id="ai-chat-sample",
            title="AI Chat Sample",
            description="Sample page for AI chat",
            text="<p>Alpha Beta Gamma. <strong>Delta</strong> text.</p>",
        )

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

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_uses_context_and_citations(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "page",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Tell me about this page"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {"response": "Hi there!"}
        service = AIChatService(self.portal, self.request)
        result = service.reply()

        payload = mock_api.return_value.chat.send.call_args[0][0]
        self.assertTrue(payload.get("context_documents"))
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertIn(
            "Use ONLY the provided context documents", payload["messages"][0]["content"]
        )
        self.assertTrue(result.get("citations"))
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())
        self.assertTrue(result.get("used_context"))

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_fallback_cleans_html(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "page",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Summarize this page"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {"error": "404 Not Found"}

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertTrue(result["message"]["content"].startswith("Summary of"))
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_unusable_gateway_answer_uses_local_fallback(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "page",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Summarize this page"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {
            "message": {
                "role": "assistant",
                "content": "Please modify the text according to the instruction and user query, maintaining proper TinyMCE HTML formatting:",
            }
        }

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertTrue(result["message"]["content"].startswith("Summary of"))
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_unusable_gateway_summarize_phrase(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "summarize",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Summarize this page"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {
            "message": {
                "role": "assistant",
                "content": "Please summarize the content of this page.",
            }
        }

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertTrue(result["message"]["content"].startswith("Summary of"))
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_not_grounded_triggers_fallback(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "page",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Fasse den Inhalt zusammen"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {
            "message": {
                "role": "assistant",
                "content": "Bitte fassen Sie den Inhalt zusammen.",
            }
        }

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertTrue(result["message"]["content"].startswith("Summary of"))
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_fallback_search_mode(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "search",
            "query": "career development",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Find posts about careers"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {"error": "404 Not Found"}

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertIn("search results", result["message"]["content"].lower())
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())

    @patch("interaktiv.kyra.services.ai_chat.json_body")
    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_reply_fallback_custom_query(self, mock_api, mock_json_body):
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "page",
        }
        mock_json_body.return_value = {
            "messages": [{"role": "user", "content": "Was kannst du tun?"}],
            "context": context,
        }
        mock_api.return_value.chat.send.return_value = {"error": "404 Not Found"}

        service = AIChatService(self.portal, self.request)
        result = service.reply()

        self.assertIn("was kannst du", result["message"]["content"].lower())
        self.assertEqual(result["citations"][0]["source_id"], self.sample_doc.UID())

    @patch("interaktiv.kyra.services.base.KyraAPI")
    def test_stream_events__token_and_done(self, mock_api):
        mock_api.return_value.chat.stream.return_value = (
            None,
            "404 Not Found",
        )
        mock_api.return_value.prompts.apply.return_value = {"response": "Hi"}

        service = AIChatService(self.portal, self.request)
        context = {
            "page": {
                "uid": self.sample_doc.UID(),
                "url": self.sample_doc.absolute_url(),
            },
            "mode": "page",
        }
        context_docs = ai_context.build_context_documents(context)
        capabilities = {"is_anonymous": True, "can_edit": False, "features": ["chat"]}
        original_data = {"messages": [{"role": "user", "content": "Hi"}]}
        events = list(
            service._stream_events(
                {"messages": []},
                None,
                context_docs,
                capabilities,
                "",
                [{"role": "user", "content": "Hi"}],
                original_data,
            )
        )

        self.assertTrue(any("event: done" in event for event in events))
