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
        glossary = {"Forschungszentrum": "Research Center"}
        result = _apply_glossary_substitution(
            "Das Forschungszentrum ist groß.", glossary
        )
        self.assertEqual(result, "Das Research Center ist groß.")

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
            "Forschungszentrum Jülich": "Jülich Research Centre",
            "Forschungszentrum": "Research Center",
        }
        result = _apply_glossary_substitution(
            "Das Forschungszentrum Jülich forscht.", glossary
        )
        self.assertEqual(result, "Das Jülich Research Centre forscht.")

    def test_no_match(self):
        glossary = {"Forschung": "Research"}
        text = "Hallo Welt"
        result = _apply_glossary_substitution(text, glossary)
        self.assertEqual(result, text)

    def test_multiple_occurrences(self):
        glossary = {"FZJ": "Forschungszentrum Jülich"}
        result = _apply_glossary_substitution("FZJ ist FZJ.", glossary)
        self.assertEqual(
            result, "Forschungszentrum Jülich ist Forschungszentrum Jülich."
        )

    def test_special_regex_characters(self):
        glossary = {"C++": "C plus plus"}
        result = _apply_glossary_substitution("Wir nutzen C++ hier.", glossary)
        self.assertEqual(result, "Wir nutzen C plus plus hier.")
