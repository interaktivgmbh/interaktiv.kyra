import re
from typing import Any, Dict


SUMMARY_KEYWORDS = (
    "summarize",
    "summary",
    "zusammenfassen",
    "zusammenfassung",
    "fasse",
    "zusammen",
    "wesentlichen informationen",
    "wesentliche informationen",
)

SITE_TITLE_KEYWORDS = (
    "site title",
    "site name",
    "website title",
    "webseitentitel",
    "haupttitel der website",
    "main seiten titel",
    "seitentitel der website",
)

PAGE_TITLE_KEYWORDS = ("page title", "titel der seite", "seitentitel", "seiten titel")

SMALLTALK_KEYWORDS = (
    "hallo",
    "hi",
    "hey",
    "wie geht",
    "hello",
    "was geht",
    "servus",
    "moin",
    "grüß",
    "gruss",
    "was kannst du",
    "what can you do",
    "wer bist du",
    "who are you",
    "hilfe",
    "help",
    "guten tag",
    "guten morgen",
    "guten abend",
    "good morning",
    "good evening",
    "danke",
    "thank",
)

OFFSITE_KEYWORDS = (
    "wetter",
    "weather",
    "forecast",
    "temperature",
    "temperatur",
    "new york",
    "paris",
    "london",
    "berlin",
    "time",
    "uhrzeit",
    "stock",
    "aktien",
    "news",
)

CONTENT_KEYWORDS = (
    "auf der seite",
    "auf dieser seite",
    "inhalt",
    "content",
    "quote",
    "zitat",
    "stay hungry",
    "stay foolish",
    "steve jobs",
    "wer hat",
    "who said",
    "welches zitat",
    "welches quote",
    "seitentitel",
    "page title",
    "titel der seite",
    "titel der webseite",
    "title of the page",
    "title of this page",
    "was steht",
    "what does the page",
    "worum geht",
    "what is this page about",
    "der titel",
    "the title",
    "welcher titel",
    "which title",
)

UPLOAD_KEYWORDS = (
    "attachment",
    "anhang",
    "upload",
    "hochgeladen",
    "datei",
    "file",
)


def _detect_summary_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in SUMMARY_KEYWORDS)


def _detect_smalltalk_intent(text: str) -> bool:
    lowered = (text or "").lower().strip().rstrip("?!.,")
    if not lowered:
        return False
    if len(lowered) > 80:
        return False
    return any(keyword in lowered for keyword in SMALLTALK_KEYWORDS)


def _detect_offsite_intent(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered:
        return False
    return any(keyword in lowered for keyword in OFFSITE_KEYWORDS)


def _detect_content_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in CONTENT_KEYWORDS)


def _detect_upload_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in UPLOAD_KEYWORDS)


def _detect_site_title_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in SITE_TITLE_KEYWORDS)


def _detect_page_title_intent(text: str) -> bool:
    lowered = (text or "").lower()
    return any(keyword in lowered for keyword in PAGE_TITLE_KEYWORDS)


def _is_not_found_error(message: str) -> bool:
    lowered = (message or "").lower()
    return "404" in lowered or "not found" in lowered


def _is_invalid_uuid_error_response(response: Any) -> bool:
    if not isinstance(response, dict):
        return False

    details = response.get("details") or []
    if isinstance(details, list):
        for detail in details:
            if not isinstance(detail, dict):
                continue
            message = detail.get("message") or ""
            if isinstance(message, str) and "invalid uuid" in message.lower():
                return True

    message = response.get("error") or response.get("message") or ""
    if isinstance(message, str) and "invalid uuid" in message.lower():
        return True

    return False


def _is_unusable_gateway_answer(text: str) -> bool:
    if not text:
        return True
    lowered = text.lower()
    if "please modify the text according to the instruction" in lowered:
        return True
    if "modifique el texto de acuerdo con las instrucciones" in lowered:
        return True
    if "modifiez le texte selon les instructions" in lowered:
        return True
    if "tinymce" in lowered:
        return True
    if "maintaining proper tinymce html formatting" in lowered:
        return True
    if lowered.strip() in ("please summarize the content of this page.",
                           "please summarize the page content clearly and concisely."):
        return True
    if "please summarize" in lowered and "page" in lowered:
        return True
    if "bitte" in lowered and "fassen" in lowered and "zusammen" in lowered:
        return True
    if "bitte fassen sie den inhalt zusammen" in lowered:
        return True
    if "please use the search bar" in lowered:
        return True
    if "please enter your search" in lowered or "search box" in lowered or "enter your search terms" in lowered:
        return True
    if "please enter your query" in lowered and "search" in lowered:
        return True
    if "cannot find" in lowered and "content" in lowered and "provided" in lowered:
        return True
    return False


def _is_grounded_answer(text: str, context_docs: Dict[str, Any]) -> bool:
    """Heuristic: answer should reference the current page title/URL or page text."""
    if not text:
        return False
    page_doc = context_docs.get("page_doc") or {}
    title = (page_doc.get("title") or "").strip()
    url = (page_doc.get("url") or "").strip()
    lowered = text.lower()
    if title and title.lower() in lowered:
        return True
    if url and url.lower() in lowered:
        return True

    page_text = (page_doc.get("text") or "").lower()
    if page_text:
        answer_tokens = {t for t in re.split(r"[^a-z0-9äöüß]+", lowered) if len(t) >= 4}
        page_tokens = {
            t for t in re.split(r"[^a-z0-9äöüß]+", page_text[:800]) if len(t) >= 4
        }
        if answer_tokens and page_tokens:
            if len(answer_tokens.intersection(page_tokens)) >= 3:
                return True
    return False


def _needs_grounded_response(last_query: str, mode: str, context_docs: Dict[str, Any]) -> bool:
    if mode == "summarize":
        return True
    if mode in ("search", "related"):
        return True
    if mode == "page":
        if _detect_smalltalk_intent(last_query):
            return False
        if _detect_content_intent(last_query):
            return True
        return False
    return False
