import unittest

from interaktiv.kyra.services.ai_actions import _apply_glossary_substitution


class TestGlossarySubstitution(unittest.TestCase):
    """Test pre-translation glossary substitution logic."""

    def test_empty_glossary(self):
        result = _apply_glossary_substitution("Hallo Welt", {})
        self.assertEqual(result, "Hallo Welt")

    def test_empty_text(self):
        result = _apply_glossary_substitution("", {"Hallo": "Hello"})
        self.assertEqual(result, "")

    def test_none_text(self):
        result = _apply_glossary_substitution(None, {"Hallo": "Hello"})
        self.assertIsNone(result)

    def test_single_substitution(self):
        glossary = {"Nachhaltigkeit": "Sustainability"}
        result = _apply_glossary_substitution(
            "Die Nachhaltigkeit ist wichtig.", glossary
        )
        self.assertEqual(result, "Die Sustainability ist wichtig.")

    def test_multiple_substitutions(self):
        glossary = {
            "Forschung": "Research",
            "Energie": "Energy",
        }
        result = _apply_glossary_substitution(
            "Forschung und Energie sind wichtig.", glossary
        )
        self.assertEqual(result, "Research und Energy sind wichtig.")

    def test_case_insensitive(self):
        glossary = {"Forschung": "Research"}
        result = _apply_glossary_substitution("forschung ist toll.", glossary)
        self.assertEqual(result, "Research ist toll.")

    def test_longest_match_first(self):
        glossary = {
            "Erneuerbare Energien": "Renewable Energies",
            "Erneuerbare": "Renewable",
        }
        result = _apply_glossary_substitution(
            "Die Erneuerbare Energien sind wichtig.", glossary
        )
        self.assertEqual(result, "Die Renewable Energies sind wichtig.")

    def test_no_match(self):
        glossary = {"Forschung": "Research"}
        text = "Hallo Welt"
        result = _apply_glossary_substitution(text, glossary)
        self.assertEqual(result, text)

    def test_multiple_occurrences(self):
        glossary = {"Plone": "Plone CMS"}
        result = _apply_glossary_substitution("Plone ist Plone.", glossary)
        self.assertEqual(
            result, "Plone CMS ist Plone CMS."
        )

    def test_special_regex_characters(self):
        glossary = {"C++": "C plus plus"}
        result = _apply_glossary_substitution("Wir nutzen C++ hier.", glossary)
        self.assertEqual(result, "Wir nutzen C plus plus hier.")
