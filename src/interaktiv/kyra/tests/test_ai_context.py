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
            "block1": {"value": {"text": "<p>Block text</p>"}},
            "block2": {"value": {"text": "<strong>More text</strong>"}},
        }
        extracted = ai_context.extract_page_text(dummy)
        self.assertIn("Block text", extracted)
        self.assertIn("More text", extracted)

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
