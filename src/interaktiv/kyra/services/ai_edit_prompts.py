"""Prompt loading and progress messages for the integrated Layout Agent."""

from __future__ import annotations

import functools
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent.parent / "agent" / "prompts"

_PROMPT_FILES: dict[tuple[str, ...], str] = {
    (): "read.md",
    ("update",): "update.md",
    ("create", "delete", "move", "update"): "full.md",
}


@functools.lru_cache(maxsize=None)
def load_prompt(permissions: tuple[str, ...]) -> str:
    key = tuple(sorted(permissions))
    filename = _PROMPT_FILES.get(key)
    if filename is None:
        raise ValueError(f"No prompt configured for permissions {list(key)}")
    return (PROMPT_DIR / filename).read_text(encoding="utf-8")


# Progress messages: tool name -> {language: message}
TOOL_PROGRESS: dict[str, dict[str, str]] = {
    "get_layout": {"de": "Analysiere Seitenstruktur\u2026", "en": "Analyzing page structure\u2026"},
    "get_metadata": {"de": "Lese Seiteninformationen\u2026", "en": "Reading page information\u2026"},
    "search_stock_photos": {"de": "Suche passende Bilder\u2026", "en": "Searching for images\u2026"},
    "delete_element": {"de": "Entferne Inhalt\u2026", "en": "Removing content\u2026"},
    "move_element": {"de": "Verschiebe Inhalt\u2026", "en": "Moving content\u2026"},
    "copy_element": {"de": "Kopiere Inhalt\u2026", "en": "Copying content\u2026"},
    "update_metadata": {"de": "Aktualisiere Seiteninformationen\u2026", "en": "Updating page information\u2026"},
    "create_columns": {"de": "Erstelle Spalten\u2026", "en": "Creating columns\u2026"},
    "create_column": {"de": "Erstelle Spalten\u2026", "en": "Creating columns\u2026"},
    "create_slider": {"de": "Erstelle Slideshow\u2026", "en": "Creating slideshow\u2026"},
    "create_slide": {"de": "Erstelle Slideshow\u2026", "en": "Creating slideshow\u2026"},
    "create_carousel": {"de": "Erstelle Karussell\u2026", "en": "Creating carousel\u2026"},
    "create_carousel_item": {"de": "Erstelle Karussell\u2026", "en": "Creating carousel\u2026"},
    "create_accordion": {"de": "Erstelle Akkordeon\u2026", "en": "Creating accordion\u2026"},
    "create_accordion_panel": {"de": "Erstelle Akkordeon\u2026", "en": "Creating accordion\u2026"},
    "create_heading": {"de": "F\u00fcge \u00dcberschrift hinzu\u2026", "en": "Adding heading\u2026"},
    "create_rich_text": {"de": "Generiere Textinhalte\u2026", "en": "Generating text content\u2026"},
    "create_image": {"de": "F\u00fcge Bild ein\u2026", "en": "Adding image\u2026"},
    "create_video": {"de": "F\u00fcge Video ein\u2026", "en": "Adding video\u2026"},
    "create_highlight": {"de": "Erstelle hervorgehobenen Bereich\u2026", "en": "Creating featured section\u2026"},
    "create_table": {"de": "Erstelle Tabelle\u2026", "en": "Creating table\u2026"},
    "create_divider": {"de": "Strukturiere Abschnitte\u2026", "en": "Structuring sections\u2026"},
    "create_button": {"de": "F\u00fcge Button hinzu\u2026", "en": "Adding button\u2026"},
    "create_teaser": {"de": "Erstelle Vorschaukarte\u2026", "en": "Creating preview card\u2026"},
    "create_title": {"de": "Setze Seitentitel\u2026", "en": "Setting page title\u2026"},
    "create_description": {"de": "Setze Seitenbeschreibung\u2026", "en": "Setting page description\u2026"},
    "update_columns": {"de": "Passe Spalten an\u2026", "en": "Adjusting columns\u2026"},
    "update_column": {"de": "Passe Spalten an\u2026", "en": "Adjusting columns\u2026"},
    "update_slider": {"de": "Passe Slideshow an\u2026", "en": "Adjusting slideshow\u2026"},
    "update_slide": {"de": "Passe Slideshow an\u2026", "en": "Adjusting slideshow\u2026"},
    "update_carousel": {"de": "Passe Karussell an\u2026", "en": "Adjusting carousel\u2026"},
    "update_carousel_item": {"de": "Passe Karussell an\u2026", "en": "Adjusting carousel\u2026"},
    "update_accordion": {"de": "Passe Akkordeon an\u2026", "en": "Adjusting accordion\u2026"},
    "update_accordion_panel": {"de": "Passe Akkordeon an\u2026", "en": "Adjusting accordion\u2026"},
    "update_heading": {"de": "Passe \u00dcberschrift an\u2026", "en": "Adjusting heading\u2026"},
    "update_rich_text": {"de": "Passe Textinhalte an\u2026", "en": "Adjusting text content\u2026"},
    "update_image": {"de": "Passe Bild an\u2026", "en": "Adjusting image\u2026"},
    "update_video": {"de": "Passe Video an\u2026", "en": "Adjusting video\u2026"},
    "update_highlight": {"de": "Passe hervorgehobenen Bereich an\u2026", "en": "Adjusting featured section\u2026"},
    "update_table": {"de": "Passe Tabelle an\u2026", "en": "Adjusting table\u2026"},
    "update_divider": {"de": "Passe Trennlinie an\u2026", "en": "Adjusting divider\u2026"},
    "update_button": {"de": "Passe Button an\u2026", "en": "Adjusting button\u2026"},
    "update_teaser": {"de": "Passe Vorschaukarte an\u2026", "en": "Adjusting preview card\u2026"},
    "update_title": {"de": "Aktualisiere Seitentitel\u2026", "en": "Updating page title\u2026"},
    "update_description": {"de": "Aktualisiere Seitenbeschreibung\u2026", "en": "Updating page description\u2026"},
}

FALLBACK_PROGRESS: dict[str, str] = {"de": "Arbeite\u2026", "en": "Working\u2026"}


def progress_message(tool_name: str, language: str) -> str:
    entry = TOOL_PROGRESS.get(tool_name)
    if entry:
        return entry.get(language, entry["de"])
    return FALLBACK_PROGRESS.get(language, FALLBACK_PROGRESS["de"])
