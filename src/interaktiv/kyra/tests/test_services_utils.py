import unittest

from zExceptions import BadRequest

from interaktiv.kyra.services.ai_prompts import _build_payload, _serialize_prompt
from interaktiv.kyra.services.prompt_files import _serialize_file
from interaktiv.kyra.services.ai_assistant_run import _extract_text_from_data


class TestAIPromptsHelpers(unittest.TestCase):
    def test_build_payload_minimal_defaults(self):
        payload = _build_payload({"name": "Test", "text": "hello"})

        self.assertEqual(payload["name"], "Test")
        self.assertEqual(payload["prompt"], "hello")
        self.assertEqual(payload["actionType"], "replace")
        self.assertEqual(payload["metadata"]["action"], "replace")
        self.assertEqual(payload["metadata"]["categories"], [])

    def test_build_payload_with_categories_and_action(self):
        payload = _build_payload(
            {
                "name": "With meta",
                "prompt": "p",
                "categories": ["a", "b"],
                "actionType": "append",
                "description": "desc",
            }
        )

        self.assertEqual(payload["prompt"], "p")
        self.assertEqual(payload["description"], "desc")
        self.assertEqual(payload["categories"], ["a", "b"])
        self.assertEqual(payload["actionType"], "append")
        self.assertEqual(payload["metadata"]["categories"], ["a", "b"])
        self.assertEqual(payload["metadata"]["action"], "append")

    def test_build_payload_validation(self):
        with self.assertRaises(BadRequest):
            _build_payload("not-a-dict")

        with self.assertRaises(BadRequest):
            _build_payload({"text": "missing name"})

    def test_serialize_prompt_prefers_metadata(self):
        prompt = _serialize_prompt(
            {
                "_id": "123",
                "prompt": "body",
                "metadata": {"categories": ["x"], "action": "append"},
                "categories": ["ignore-me"],
                "actionType": "replace",
            }
        )

        self.assertEqual(prompt["id"], "123")
        self.assertEqual(prompt["text"], "body")
        self.assertEqual(prompt["categories"], ["x"])
        self.assertEqual(prompt["actionType"], "append")

    def test_serialize_prompt_without_metadata(self):
        prompt = _serialize_prompt(
            {
                "id": "123",
                "text": "body2",
                "categories": ["cat1", "cat2"],
            }
        )

        self.assertEqual(prompt["categories"], ["cat1", "cat2"])
        self.assertEqual(prompt["actionType"], "replace")


class TestPromptFilesHelpers(unittest.TestCase):
    def test_serialize_file_content_type_guess(self):
        file_data = {"id": "f1", "filename": "image.png", "size": 42}

        serialized = _serialize_file(file_data)

        self.assertEqual(serialized["id"], "f1")
        self.assertEqual(serialized["filename"], "image.png")
        self.assertEqual(serialized["size"], 42)
        self.assertEqual(serialized["content_type"], "image/png")


class TestAssistantRunHelpers(unittest.TestCase):
    def test_extract_text_prefers_response(self):
        self.assertEqual(
            _extract_text_from_data({"response": "hello", "other": "x"}), "hello"
        )
        self.assertEqual(_extract_text_from_data("plain string"), "plain string")
        self.assertEqual(_extract_text_from_data({"response": ""}), "")

