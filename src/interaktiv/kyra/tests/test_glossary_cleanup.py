"""Pure-logic tests for the safer glossary cleanup and DeepL usage warning.

These do not require the Plone test layer; they cover the helpers introduced to
(a) only delete Kyra-owned DeepL glossaries and (b) warn when the DeepL
character quota is nearly exhausted.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from interaktiv.kyra.services.deepl_translation import (
    GLOSSARY_NAME,
    _is_kyra_glossary,
    _usage_warn_threshold,
    _warn_on_high_usage,
)


def _glossary(glossary_id="g1", name=GLOSSARY_NAME):
    return SimpleNamespace(glossary_id=glossary_id, name=name)


class TestKyraGlossaryOwnership(unittest.TestCase):
    def test_matches_by_name(self):
        g = _glossary(glossary_id="other", name=GLOSSARY_NAME)
        self.assertTrue(_is_kyra_glossary(g, set()))

    def test_matches_by_known_id(self):
        g = _glossary(glossary_id="known-123", name="Some Foreign Glossary")
        self.assertTrue(_is_kyra_glossary(g, {"known-123"}))

    def test_does_not_match_foreign_glossary(self):
        g = _glossary(glossary_id="foreign-1", name="Marketing Team Glossary")
        self.assertFalse(_is_kyra_glossary(g, {"known-123"}))

    def test_missing_attributes_are_safe(self):
        self.assertFalse(_is_kyra_glossary(object(), set()))


class TestUsageWarnThreshold(unittest.TestCase):
    def test_default_threshold(self):
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("KYRA_DEEPL_USAGE_WARN_THRESHOLD", None)
            self.assertEqual(_usage_warn_threshold(), 0.8)

    def test_env_override(self):
        with patch.dict("os.environ", {"KYRA_DEEPL_USAGE_WARN_THRESHOLD": "0.9"}):
            self.assertEqual(_usage_warn_threshold(), 0.9)

    def test_invalid_env_falls_back(self):
        with patch.dict("os.environ", {"KYRA_DEEPL_USAGE_WARN_THRESHOLD": "nonsense"}):
            self.assertEqual(_usage_warn_threshold(), 0.8)

    def test_out_of_range_env_falls_back(self):
        with patch.dict("os.environ", {"KYRA_DEEPL_USAGE_WARN_THRESHOLD": "5"}):
            self.assertEqual(_usage_warn_threshold(), 0.8)


class TestWarnOnHighUsage(unittest.TestCase):
    @patch("interaktiv.kyra.services.deepl_translation.logger")
    def test_warns_when_over_threshold(self, mock_logger):
        usage = SimpleNamespace(character=SimpleNamespace(count=900, limit=1000))
        _warn_on_high_usage(usage)
        self.assertTrue(mock_logger.warning.called)

    @patch("interaktiv.kyra.services.deepl_translation.logger")
    def test_no_warning_below_threshold(self, mock_logger):
        usage = SimpleNamespace(character=SimpleNamespace(count=100, limit=1000))
        _warn_on_high_usage(usage)
        self.assertFalse(mock_logger.warning.called)

    @patch("interaktiv.kyra.services.deepl_translation.logger")
    def test_no_crash_on_missing_usage(self, mock_logger):
        _warn_on_high_usage(SimpleNamespace(character=None))
        _warn_on_high_usage(SimpleNamespace())
        self.assertFalse(mock_logger.warning.called)


if __name__ == "__main__":
    unittest.main()
