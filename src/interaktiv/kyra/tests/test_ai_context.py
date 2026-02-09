import unittest

from plone import api
from interaktiv.kyra.services import ai_context
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone.app.testing import TEST_USER_ID, setRoles


class TestAIContext(unittest.TestCase):
    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING

    def setUp(self):
        self.app = self.layer["app"]
        self.portal = self.layer["portal"]
        self.request = self.layer["request"]
        setRoles(self.portal, TEST_USER_ID, ["Manager", "Site Administrator"])

    def test_extract_page_text_includes_blocks(self):
        class Dummy:
            def Title(self):
                return "Dummy Title"

            def Description(self):
                return "Dummy Description"

        dummy = Dummy()
        dummy.blocks = {
            "block1": {"@type": "slate", "plaintext": "Block text"},
            "block2": {"@type": "slate", "plaintext": "More text"},
        }
        dummy.blocks_layout = {"items": ["block1", "block2"]}
        extracted, quotes = ai_context.extract_page_text(dummy)
        self.assertIn("Block text", extracted)
        self.assertIn("More text", extracted)

    def test_extract_page_text_excludes_metadata(self):
        class Dummy:
            def Title(self):
                return "Test Page"

            def Description(self):
                return ""

        dummy = Dummy()
        dummy.blocks = {
            "b1": {
                "@type": "slate",
                "styles": {"align": "center"},
                "value": [{"children": [{"text": "Hello World"}]}],
                "plaintext": "Hello World",
            },
            "b2": {
                "@type": "image",
                "url": "http://example.com/resolveuid/abc123",
                "alt": "A nice photo",
                "size": "l",
            },
        }
        dummy.blocks_layout = {"items": ["b1", "b2"]}
        extracted, _ = ai_context.extract_page_text(dummy)
        self.assertIn("Hello World", extracted)
        self.assertIn("[Image: A nice photo]", extracted)
        self.assertIn("Test Page", extracted)
        # Must NOT contain block metadata or technical labels
        self.assertNotIn("center", extracted)
        self.assertNotIn("resolveuid", extracted)
        self.assertNotIn("@type", extracted)
        self.assertNotIn("Title:", extracted)
        self.assertNotIn("---", extracted)

    def test_extract_page_text_handles_teaser_block(self):
        class Dummy:
            def Title(self):
                return "Page"

            def Description(self):
                return ""

        dummy = Dummy()
        dummy.blocks = {
            "t1": {
                "@type": "teaser",
                "title": "Teaser Title",
                "description": "Teaser description text",
                "head_title": "Head",
            },
        }
        dummy.blocks_layout = {"items": ["t1"]}
        extracted, _ = ai_context.extract_page_text(dummy)
        self.assertIn("Teaser Title", extracted)
        self.assertIn("Teaser description text", extracted)

    def test_build_context_documents_uses_frontend_page_content(self):
        sample_page = api.content.create(
            container=self.portal,
            type="Document",
            id="ai-frontend-content-test",
            title="Frontend Content Test",
            description="Test page",
        )
        frontend_text = "Title: Frontend Content Test\nType: Document\n---\nThis is clean frontend text."
        context = {
            "page": {"uid": sample_page.UID()},
            "page_content": frontend_text,
            "mode": "page",
        }
        result = ai_context.build_context_documents(context)
        page_doc = result["page_doc"]
        self.assertIn("clean frontend text", page_doc["text"])
        # Metadata labels should be stripped
        self.assertNotIn("Title:", page_doc["text"])
        self.assertNotIn("Type:", page_doc["text"])
        self.assertNotIn("---", page_doc["text"])

    def test_catalog_related_docs_returns_results(self):
        api.content.create(
            container=self.portal,
            type="Document",
            id="related-item",
            title="Related item",
            description="This is related",
        )
        docs = ai_context.catalog_related_docs(
            query="related", exclude_uid=self.portal.UID(), limit=3
        )
        self.assertTrue(len(docs) >= 1)

    def test_build_context_documents_includes_page(self):
        sample_page = api.content.create(
            container=self.portal,
            type="Document",
            id="ai-page-sample",
            title="AI Page Sample",
            description="<p>Sample</p>",
            text="<p>Alpha <strong>Beta</strong> Gamma.</p>",
        )
        context = {
            "page": {"uid": sample_page.UID(), "url": sample_page.absolute_url()},
            "mode": "page",
        }
        result = ai_context.build_context_documents(context)
        self.assertEqual(result["page_doc"]["id"], sample_page.UID())
        self.assertTrue(result["page_doc"]["text"])

    def test_build_context_documents_includes_site_docs(self):
        sample_page = api.content.create(
            container=self.portal,
            type="Document",
            id="ai-site-page",
            title="Site Page Sample",
            description="Site sample",
            text="<p>Site page text.</p>",
        )
        api.content.create(
            container=self.portal,
            type="Document",
            id="ai-site-section",
            title="Site Section",
            description="Overview",
            text="<p>Site section text.</p>",
        )
        context = {
            "page": {"uid": sample_page.UID(), "url": sample_page.absolute_url()},
            "mode": "page",
        }
        result = ai_context.build_context_documents(context)
        site_docs = result.get("site_docs") or []
        self.assertTrue(isinstance(site_docs, list))
        self.assertTrue(len(site_docs) >= 1)
        self.assertNotEqual(result["documents"][0]["id"], site_docs[0]["id"])
