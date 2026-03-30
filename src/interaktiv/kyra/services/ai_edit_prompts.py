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

# Additional prompt files that are always appended to the main prompt.
_EXTRA_PROMPTS: list[str] = ["agent.md", "notes.md"]


@functools.lru_cache(maxsize=None)
def load_prompt(permissions: tuple[str, ...]) -> str:
    key = tuple(sorted(permissions))
    filename = _PROMPT_FILES.get(key)
    if filename is None:
        raise ValueError(f"No prompt configured for permissions {list(key)}")
    parts = [(PROMPT_DIR / filename).read_text(encoding="utf-8")]
    for extra in _EXTRA_PROMPTS:
        extra_path = PROMPT_DIR / extra
        if extra_path.exists():
            parts.append(extra_path.read_text(encoding="utf-8"))
    return "\n\n".join(parts)


# Progress messages: tool name -> {language: message}
TOOL_PROGRESS: dict[str, dict[str, str]] = {
    # Read
    "get_layout": {"de": "Schaue mir die Seite an\u2026", "en": "Looking at the page\u2026"},
    "get_metadata": {"de": "Lese Seiteninfos\u2026", "en": "Reading page info\u2026"},
    # Site browsing
    "list_children": {"de": "Schaue mich um\u2026", "en": "Looking around\u2026"},
    "search_content": {"de": "Suche auf der Website\u2026", "en": "Searching the site\u2026"},
    "get_breadcrumb": {"de": "Schaue, wo die Seite liegt\u2026", "en": "Checking page location\u2026"},
    "search_documents": {"de": "Durchsuche Dokumente\u2026", "en": "Searching documents\u2026"},
    "read_document_pages": {"de": "Lese Dokument\u2026", "en": "Reading document\u2026"},
    "view_image": {"de": "Schaue mir das Bild an\u2026", "en": "Looking at the image\u2026"},
    # Stock photos
    "search_stock_photos": {"de": "Suche passende Fotos\u2026", "en": "Searching for photos\u2026"},
    # Structural ops
    "delete_element": {"de": "Entferne Inhalt\u2026", "en": "Removing content\u2026"},
    "move_element": {"de": "Verschiebe Inhalt\u2026", "en": "Moving content\u2026"},
    "copy_element": {"de": "Kopiere Inhalt\u2026", "en": "Copying content\u2026"},
    "swap_elements": {"de": "Tausche Inhalte\u2026", "en": "Swapping content\u2026"},
    "update_metadata": {"de": "Aktualisiere Seiteninfos\u2026", "en": "Updating page info\u2026"},
    # Create — layout containers
    "create_columns": {"de": "Baue Spalten-Layout\u2026", "en": "Building column layout\u2026"},
    "create_column": {"de": "Baue Spalten-Layout\u2026", "en": "Building column layout\u2026"},
    "create_slider": {"de": "Baue Slideshow\u2026", "en": "Building slideshow\u2026"},
    "create_slide": {"de": "Baue Slideshow\u2026", "en": "Building slideshow\u2026"},
    "create_carousel": {"de": "Baue Karussell\u2026", "en": "Building carousel\u2026"},
    "create_carousel_item": {"de": "Baue Karussell\u2026", "en": "Building carousel\u2026"},
    "create_accordion": {"de": "Baue Akkordeon\u2026", "en": "Building accordion\u2026"},
    "create_accordion_panel": {"de": "Baue Akkordeon\u2026", "en": "Building accordion\u2026"},
    "create_tabs": {"de": "Baue Tabs\u2026", "en": "Building tabs\u2026"},
    "create_tab": {"de": "Baue Tabs\u2026", "en": "Building tabs\u2026"},
    # Create — content
    "create_heading": {"de": "F\u00fcge \u00dcberschrift ein\u2026", "en": "Adding heading\u2026"},
    "create_rich_text": {"de": "Schreibe Text\u2026", "en": "Writing text\u2026"},
    "create_image": {"de": "F\u00fcge Bild ein\u2026", "en": "Adding image\u2026"},
    "create_video": {"de": "F\u00fcge Video ein\u2026", "en": "Adding video\u2026"},
    "create_highlight": {"de": "Erstelle Highlight\u2026", "en": "Creating highlight\u2026"},
    "create_table": {"de": "Erstelle Tabelle\u2026", "en": "Creating table\u2026"},
    "create_divider": {"de": "F\u00fcge Trennlinie ein\u2026", "en": "Adding divider\u2026"},
    "create_button": {"de": "F\u00fcge Button ein\u2026", "en": "Adding button\u2026"},
    "create_teaser": {"de": "Erstelle Teaser\u2026", "en": "Creating teaser\u2026"},
    "create_title": {"de": "Setze Seitentitel\u2026", "en": "Setting page title\u2026"},
    "create_description": {"de": "Setze Seitenbeschreibung\u2026", "en": "Setting page description\u2026"},
    "create_quote": {"de": "F\u00fcge Zitat ein\u2026", "en": "Adding quote\u2026"},
    "create_statistic": {"de": "Erstelle Kennzahlen\u2026", "en": "Creating statistics\u2026"},
    "create_statistic_item": {"de": "Erstelle Kennzahlen\u2026", "en": "Creating statistics\u2026"},
    "create_listing": {"de": "Erstelle Listing\u2026", "en": "Creating listing\u2026"},
    "create_form": {"de": "Erstelle Formular\u2026", "en": "Creating form\u2026"},
    "create_form_field": {"de": "Erstelle Formular\u2026", "en": "Creating form\u2026"},
    "create_form_choice": {"de": "Erstelle Formular\u2026", "en": "Creating form\u2026"},
    # Update — layout containers
    "update_columns": {"de": "Passe Spalten an\u2026", "en": "Adjusting columns\u2026"},
    "update_column": {"de": "Passe Spalten an\u2026", "en": "Adjusting columns\u2026"},
    "update_slider": {"de": "Passe Slideshow an\u2026", "en": "Adjusting slideshow\u2026"},
    "update_slide": {"de": "Passe Slideshow an\u2026", "en": "Adjusting slideshow\u2026"},
    "update_carousel": {"de": "Passe Karussell an\u2026", "en": "Adjusting carousel\u2026"},
    "update_carousel_item": {"de": "Passe Karussell an\u2026", "en": "Adjusting carousel\u2026"},
    "update_accordion": {"de": "Passe Akkordeon an\u2026", "en": "Adjusting accordion\u2026"},
    "update_accordion_panel": {"de": "Passe Akkordeon an\u2026", "en": "Adjusting accordion\u2026"},
    "update_tabs": {"de": "Passe Tabs an\u2026", "en": "Adjusting tabs\u2026"},
    "update_tab": {"de": "Passe Tabs an\u2026", "en": "Adjusting tabs\u2026"},
    # Update — content
    "update_heading": {"de": "Passe \u00dcberschrift an\u2026", "en": "Adjusting heading\u2026"},
    "update_rich_text": {"de": "\u00dcberarbeite Text\u2026", "en": "Editing text\u2026"},
    "update_image": {"de": "Passe Bild an\u2026", "en": "Adjusting image\u2026"},
    "update_video": {"de": "Passe Video an\u2026", "en": "Adjusting video\u2026"},
    "update_highlight": {"de": "Passe Highlight an\u2026", "en": "Adjusting highlight\u2026"},
    "update_table": {"de": "Passe Tabelle an\u2026", "en": "Adjusting table\u2026"},
    "update_divider": {"de": "Passe Trennlinie an\u2026", "en": "Adjusting divider\u2026"},
    "update_button": {"de": "Passe Button an\u2026", "en": "Adjusting button\u2026"},
    "update_teaser": {"de": "Passe Teaser an\u2026", "en": "Adjusting teaser\u2026"},
    "update_title": {"de": "\u00c4ndere Seitentitel\u2026", "en": "Changing page title\u2026"},
    "update_description": {"de": "\u00c4ndere Seitenbeschreibung\u2026", "en": "Changing page description\u2026"},
    "update_quote": {"de": "Passe Zitat an\u2026", "en": "Adjusting quote\u2026"},
    "update_statistic": {"de": "Passe Kennzahlen an\u2026", "en": "Adjusting statistics\u2026"},
    "update_statistic_item": {"de": "Passe Kennzahlen an\u2026", "en": "Adjusting statistics\u2026"},
    "update_listing": {"de": "Passe Listing an\u2026", "en": "Adjusting listing\u2026"},
    "update_form": {"de": "Passe Formular an\u2026", "en": "Adjusting form\u2026"},
    "update_form_field": {"de": "Passe Formular an\u2026", "en": "Adjusting form\u2026"},
    "update_form_choice": {"de": "Passe Formular an\u2026", "en": "Adjusting form\u2026"},
}

FALLBACK_PROGRESS: dict[str, str] = {"de": "Arbeite\u2026", "en": "Working\u2026"}


def progress_message(tool_name: str, language: str = "de") -> str:
    entry = TOOL_PROGRESS.get(tool_name)
    if entry:
        return entry.get(language, entry["de"])
    return FALLBACK_PROGRESS.get(language, FALLBACK_PROGRESS["de"])
