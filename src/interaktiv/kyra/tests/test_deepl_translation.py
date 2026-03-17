import unittest
from unittest.mock import MagicMock, Mock, patch

from interaktiv.kyra.services.deepl_translation import (
    GLOSSARY_ENTRIES_KEY,
    GLOSSARY_IDS_KEY,
    GLOSSARY_NAME,
    _deepl_source_lang,
    _deepl_target_lang,
    _internal_lang,
    _pair_key,
    add_glossary_entry,
    get_glossary_entries,
    remove_glossary_entry,
)
from interaktiv.kyra.testing import INTERAKTIV_KYRA_FUNCTIONAL_TESTING
from plone import api
from plone.app.testing import TEST_USER_ID, setRoles
from zope.annotation.interfaces import IAnnotations


class TestLanguageMappings(unittest.TestCase):

    def test_deepl_target_lang_en(self):
        self.assertEqual(_deepl_target_lang("en"), "EN-US")

    def test_deepl_target_lang_de(self):
        self.assertEqual(_deepl_target_lang("de"), "DE")

    def test_deepl_target_lang_pt(self):
        self.assertEqual(_deepl_target_lang("pt"), "PT-PT")

    def test_deepl_source_lang_en(self):
        self.assertEqual(_deepl_source_lang("en"), "EN")

    def test_deepl_source_lang_de(self):
        self.assertEqual(_deepl_source_lang("de"), "DE")

    def test_deepl_source_lang_pt(self):
        self.assertEqual(_deepl_source_lang("pt"), "PT")

    def test_deepl_source_lang_unknown_fallback(self):
        self.assertEqual(_deepl_source_lang("xx"), "XX")

    def test_internal_lang_from_regional(self):
        self.assertEqual(_internal_lang("EN-US"), "en")

    def test_internal_lang_from_non_regional(self):
        self.assertEqual(_internal_lang("DE"), "de")

    def test_internal_lang_pt_regional(self):
        self.assertEqual(_internal_lang("PT-PT"), "pt")

    def test_internal_lang_zh_hans(self):
        self.assertEqual(_internal_lang("ZH-HANS"), "zh")

    def test_internal_lang_unknown_fallback(self):
        self.assertEqual(_internal_lang("XX"), "xx")

    def test_internal_lang_case_insensitive(self):
        self.assertEqual(_internal_lang("en-us"), "en")
        self.assertEqual(_internal_lang("De"), "de")


class TestPairKey(unittest.TestCase):

    def test_pair_key_basic(self):
        self.assertEqual(_pair_key("de", "en"), "de:en")

    def test_pair_key_strips_whitespace(self):
        self.assertEqual(_pair_key(" de ", " en "), "de:en")

    def test_pair_key_lowercases(self):
        self.assertEqual(_pair_key("DE", "EN"), "de:en")


class TestGlossaryStore(unittest.TestCase):

    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])
        annotations = IAnnotations(self.portal)
        annotations[GLOSSARY_ENTRIES_KEY] = {}

    def test_add_entry(self):
        result = add_glossary_entry("Forschung", "Research", "de", "en")
        self.assertEqual(result, {"Forschung": "Research"})

    def test_add_multiple_entries(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        result = add_glossary_entry("Energie", "Energy", "de", "en")
        self.assertEqual(result, {"Forschung": "Research", "Energie": "Energy"})

    def test_add_entry_strips_whitespace(self):
        result = add_glossary_entry("  Forschung  ", "  Research  ", "de", "en")
        self.assertEqual(result, {"Forschung": "Research"})

    def test_add_entry_overwrites_existing(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        result = add_glossary_entry("Forschung", "Studies", "de", "en")
        self.assertEqual(result, {"Forschung": "Studies"})

    def test_get_entries(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        add_glossary_entry("Energie", "Energy", "de", "en")
        entries = get_glossary_entries("de", "en")
        self.assertEqual(entries, {"Forschung": "Research", "Energie": "Energy"})

    def test_get_entries_empty(self):
        entries = get_glossary_entries("de", "en")
        self.assertEqual(entries, {})

    def test_get_entries_wrong_pair(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        entries = get_glossary_entries("en", "de")
        self.assertEqual(entries, {})

    def test_remove_entry(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        add_glossary_entry("Energie", "Energy", "de", "en")
        result = remove_glossary_entry("Forschung", "de", "en")
        self.assertEqual(result, {"Energie": "Energy"})

    def test_remove_last_entry(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        result = remove_glossary_entry("Forschung", "de", "en")
        self.assertEqual(result, {})

    def test_remove_nonexistent_entry(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        result = remove_glossary_entry("Nichtda", "de", "en")
        self.assertEqual(result, {"Forschung": "Research"})

    def test_separate_language_pairs(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        add_glossary_entry("Research", "Forschung", "en", "de")
        de_en = get_glossary_entries("de", "en")
        en_de = get_glossary_entries("en", "de")
        self.assertEqual(de_en, {"Forschung": "Research"})
        self.assertEqual(en_de, {"Research": "Forschung"})

    def test_entries_persisted_in_annotations(self):
        add_glossary_entry("Forschung", "Research", "de", "en")
        annotations = IAnnotations(self.portal)
        store = annotations[GLOSSARY_ENTRIES_KEY]
        self.assertIn("de:en", store)
        self.assertEqual(store["de:en"]["Forschung"], "Research")


class TestSyncGlossaryToDeepL(unittest.TestCase):

    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])
        annotations = IAnnotations(self.portal)
        annotations[GLOSSARY_ENTRIES_KEY] = {}
        annotations[GLOSSARY_IDS_KEY] = {}

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_sync_creates_bilingual_glossary(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import sync_glossary_to_deepl

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.list_glossaries.return_value = []
        mock_client.list_multilingual_glossaries.return_value = []

        mock_usage = MagicMock()
        mock_usage.character.count = 100
        mock_usage.character.limit = 500000
        mock_client.get_usage.return_value = mock_usage

        mock_glossary = MagicMock()
        mock_glossary.glossary_id = "new-glossary-id-123"
        mock_client.create_glossary.return_value = mock_glossary

        add_glossary_entry("Forschung", "Research", "de", "en")

        result = sync_glossary_to_deepl()

        self.assertEqual(result, "new-glossary-id-123")
        mock_client.create_glossary.assert_called_once_with(
            GLOSSARY_NAME,
            source_lang="DE",
            target_lang="EN",
            entries={"Forschung": "Research"},
        )

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_sync_creates_multiple_glossaries_per_pair(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import sync_glossary_to_deepl

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.list_glossaries.return_value = []
        mock_client.list_multilingual_glossaries.return_value = []

        mock_usage = MagicMock()
        mock_usage.character.count = 0
        mock_usage.character.limit = 500000
        mock_client.get_usage.return_value = mock_usage

        mock_glossary_de_en = MagicMock()
        mock_glossary_de_en.glossary_id = "id-de-en"
        mock_glossary_en_de = MagicMock()
        mock_glossary_en_de.glossary_id = "id-en-de"
        mock_client.create_glossary.side_effect = [mock_glossary_de_en, mock_glossary_en_de]

        add_glossary_entry("Forschung", "Research", "de", "en")
        add_glossary_entry("Research", "Forschung", "en", "de")

        result = sync_glossary_to_deepl()

        self.assertEqual(mock_client.create_glossary.call_count, 2)
        annotations = IAnnotations(self.portal)
        ids = annotations.get(GLOSSARY_IDS_KEY, {})
        self.assertEqual(ids.get("de:en"), "id-de-en")
        self.assertEqual(ids.get("en:de"), "id-en-de")

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_sync_deletes_old_glossaries_before_creating(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import sync_glossary_to_deepl

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        old_glossary = MagicMock()
        old_glossary.glossary_id = "old-id"
        old_glossary.name = GLOSSARY_NAME
        mock_client.list_glossaries.return_value = [old_glossary]
        mock_client.list_multilingual_glossaries.return_value = []

        mock_usage = MagicMock()
        mock_usage.character.count = 0
        mock_usage.character.limit = 500000
        mock_client.get_usage.return_value = mock_usage

        new_glossary = MagicMock()
        new_glossary.glossary_id = "new-id"
        mock_client.create_glossary.return_value = new_glossary

        add_glossary_entry("Forschung", "Research", "de", "en")

        sync_glossary_to_deepl()

        mock_client.delete_glossary.assert_called_once_with("old-id")
        mock_client.create_glossary.assert_called_once()

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_sync_returns_none_without_client(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import sync_glossary_to_deepl

        mock_get_client.return_value = None
        result = sync_glossary_to_deepl()
        self.assertIsNone(result)

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_sync_returns_none_without_entries(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import sync_glossary_to_deepl

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.list_glossaries.return_value = []
        mock_client.list_multilingual_glossaries.return_value = []

        mock_usage = MagicMock()
        mock_usage.character.count = 0
        mock_usage.character.limit = 500000
        mock_client.get_usage.return_value = mock_usage

        result = sync_glossary_to_deepl()
        self.assertIsNone(result)


class TestPullEntriesFromDeepL(unittest.TestCase):

    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])
        annotations = IAnnotations(self.portal)
        annotations[GLOSSARY_ENTRIES_KEY] = {}

    @patch("interaktiv.kyra.services.deepl_translation._get_glossary_store")
    def test_pull_imports_new_entries(self, mock_store):
        from interaktiv.kyra.services.deepl_translation import _pull_entries_from_deepl

        mock_store.side_effect = lambda: IAnnotations(self.portal).setdefault(
            GLOSSARY_ENTRIES_KEY, {}
        )

        mock_client = MagicMock()
        mock_glossary = MagicMock()
        mock_glossary.glossary_id = "remote-id"
        mock_glossary.source_lang = "DE"
        mock_glossary.target_lang = "EN"
        mock_glossary.name = "Remote Glossary"
        mock_client.list_glossaries.return_value = [mock_glossary]
        mock_client.get_glossary_entries.return_value = {
            "Wasserstoff": "Hydrogen",
            "Energie": "Energy",
        }

        imported = _pull_entries_from_deepl(mock_client)

        self.assertEqual(imported, 2)

    def test_pull_does_not_overwrite_existing(self):
        from interaktiv.kyra.services.deepl_translation import _pull_entries_from_deepl

        add_glossary_entry("Forschung", "Studies", "de", "en")

        mock_client = MagicMock()
        mock_glossary = MagicMock()
        mock_glossary.glossary_id = "remote-id"
        mock_glossary.source_lang = "DE"
        mock_glossary.target_lang = "EN"
        mock_glossary.name = "Remote"
        mock_client.list_glossaries.return_value = [mock_glossary]
        mock_client.get_glossary_entries.return_value = {
            "Forschung": "Research",
            "Energie": "Energy",
        }

        imported = _pull_entries_from_deepl(mock_client)

        self.assertEqual(imported, 1)
        entries = get_glossary_entries("de", "en")
        self.assertEqual(entries["Forschung"], "Studies")
        self.assertEqual(entries["Energie"], "Energy")

    def test_pull_handles_empty_glossary_list(self):
        from interaktiv.kyra.services.deepl_translation import _pull_entries_from_deepl

        mock_client = MagicMock()
        mock_client.list_glossaries.return_value = []

        imported = _pull_entries_from_deepl(mock_client)
        self.assertEqual(imported, 0)


class TestDeepLTranslateText(unittest.TestCase):

    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_translate_returns_none_without_client(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import deepl_translate_text

        mock_get_client.return_value = None
        result = deepl_translate_text("Hallo Welt", "de", "en")
        self.assertIsNone(result)

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_translate_empty_text(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import deepl_translate_text

        result = deepl_translate_text("", "de", "en")
        self.assertEqual(result, "")

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_translate_success(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import deepl_translate_text

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.translate_text.return_value = "Hello World"

        result = deepl_translate_text("Hallo Welt", "de", "en")

        self.assertEqual(result, "Hello World")
        call_kwargs = mock_client.translate_text.call_args[1]
        self.assertEqual(call_kwargs["text"], "Hallo Welt")
        self.assertEqual(call_kwargs["target_lang"], "EN-US")
        self.assertEqual(call_kwargs["source_lang"], "DE")

    @patch("interaktiv.kyra.services.deepl_translation._get_glossary_id_for_pair")
    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_translate_passes_glossary_id(self, mock_get_client, mock_get_gid):
        from interaktiv.kyra.services.deepl_translation import deepl_translate_text

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.translate_text.return_value = "Hello World"
        mock_get_gid.return_value = "glossary-123"

        deepl_translate_text("Hallo", "de", "en")

        call_kwargs = mock_client.translate_text.call_args[1]
        self.assertEqual(call_kwargs["glossary"], "glossary-123")

    @patch("interaktiv.kyra.services.deepl_translation._get_deepl_client")
    def test_translate_returns_none_on_exception(self, mock_get_client):
        from interaktiv.kyra.services.deepl_translation import deepl_translate_text

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.translate_text.side_effect = Exception("API error")

        result = deepl_translate_text("Hallo", "de", "en")
        self.assertIsNone(result)


class TestImportGlossaryFromCsv(unittest.TestCase):

    layer = INTERAKTIV_KYRA_FUNCTIONAL_TESTING
    product_name = "interaktiv.kyra"

    def setUp(self):
        self.portal = self.layer["portal"]
        setRoles(self.portal, TEST_USER_ID, ["Manager"])
        annotations = IAnnotations(self.portal)
        annotations[GLOSSARY_ENTRIES_KEY] = {}

    @patch("interaktiv.kyra.services.deepl_translation.sync_glossary_to_deepl")
    def test_csv_import_basic(self, mock_sync):
        from interaktiv.kyra.services.deepl_translation import import_glossary_from_csv

        mock_sync.return_value = "glossary-id"
        csv_data = "Forschung,Research\nEnergie,Energy\n"

        entries, gid = import_glossary_from_csv(csv_data, "de", "en")

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries["Forschung"], "Research")
        self.assertEqual(entries["Energie"], "Energy")

    @patch("interaktiv.kyra.services.deepl_translation.sync_glossary_to_deepl")
    def test_csv_import_skips_empty_rows(self, mock_sync):
        from interaktiv.kyra.services.deepl_translation import import_glossary_from_csv

        mock_sync.return_value = None
        csv_data = "Forschung,Research\n\n,\nEnergie,Energy\n"

        entries, _ = import_glossary_from_csv(csv_data, "de", "en")

        self.assertEqual(len(entries), 2)

    @patch("interaktiv.kyra.services.deepl_translation.sync_glossary_to_deepl")
    def test_csv_import_triggers_sync(self, mock_sync):
        from interaktiv.kyra.services.deepl_translation import import_glossary_from_csv

        mock_sync.return_value = "new-id"
        import_glossary_from_csv("Forschung,Research\n", "de", "en")

        mock_sync.assert_called_once()
