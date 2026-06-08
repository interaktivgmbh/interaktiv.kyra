import random
import re
import time
from typing import Dict, Optional

from interaktiv.kyra import logger
from interaktiv.kyra.api import Chat
from interaktiv.kyra.services.deepl_translation import (
    deepl_translate_text,
    get_glossary_entries,
)

TRANSLATION_BACKOFF_BASE = 0.5
TRANSLATION_BACKOFF_FACTOR = 2.0

URL_PATTERN = re.compile(r"^(https?://|/|resolveuid|data:)", re.IGNORECASE)
NON_TEXT_PATTERN = re.compile(
    r"^(#[0-9a-fA-F]{3,8}|[0-9]+(%|px|em|rem|vh|vw|pt)?|rgba?\(.*\)|true|false|none|null|default|left|right|center|top|bottom|auto)$",
    re.IGNORECASE,
)


def _looks_like_url(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return bool(URL_PATTERN.match(text.strip()))


def _looks_like_non_text(text: str) -> bool:
    """Return True for values that are clearly not translatable text (colors, CSS values, booleans)."""
    if not isinstance(text, str):
        return False
    return bool(NON_TEXT_PATTERN.match(text.strip()))


def _is_block_id(value: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", value, re.IGNORECASE))


def _get_glossary_map(source_lang: str, target_lang: str) -> Dict[str, str]:
    """Return glossary entries for the given language pair, or empty dict."""
    try:
        entries = get_glossary_entries(source_lang, target_lang) or {}
        logger.info("[KYRA AI] glossary map for %s->%s: %d entries %s", source_lang, target_lang, len(entries), entries)
        return entries
    except Exception as exc:
        logger.warning("[KYRA AI] glossary lookup failed for %s->%s: %s", source_lang, target_lang, exc)
        return {}


def _apply_glossary_substitution(text: str, glossary: Dict[str, str]) -> str:
    """Replace glossary source terms in *text* with their target terms.

    Replaces longest terms first so that e.g. "Renewable Energies"
    is matched before "Renewable".  Case-insensitive matching.
    """
    if not glossary or not text:
        return text
    original = text
    sorted_entries = sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True)
    for src, tgt in sorted_entries:
        pattern = re.compile(re.escape(src), re.IGNORECASE)
        text = pattern.sub(tgt, text)
    if text != original:
        logger.info("[KYRA AI] glossary substitution applied: %r -> %r", original[:200], text[:200])
    return text


def _translate_text(
    translator: Chat,
    text: str,
    source_lang: str,
    target_lang: str,
    use_prompt: bool = True,
    strip_html: bool = True,
) -> str:
    if not isinstance(text, str) or not text.strip():
        return text or ""

    glossary = _get_glossary_map(source_lang, target_lang)
    text = _apply_glossary_substitution(text, glossary)

    try:
        deepl_result = deepl_translate_text(text, source_lang, target_lang)
        if deepl_result is not None:
            logger.info("[KYRA AI] DeepL translated %d chars (%s->%s)", len(deepl_result), source_lang, target_lang)
            return deepl_result
    except Exception as exc:
        logger.warning("[KYRA AI] DeepL translation failed: %s", exc)

    logger.warning("[KYRA AI] DeepL unavailable, returning original text (%s->%s)", source_lang, target_lang)
    return text


def _translate_text_with_retry(
    translator: Chat,
    text: str,
    source_lang: str,
    target_lang: str,
    use_prompt: bool = True,
    strip_html: bool = True,
) -> str:
    from interaktiv.kyra.services.ai_actions import _translation_retries, _translation_timeout_seconds

    retries = _translation_retries()
    timeout = _translation_timeout_seconds()
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return _translate_text(
                translator,
                text,
                source_lang,
                target_lang,
                use_prompt=use_prompt,
                strip_html=strip_html,
            )
        except Exception as exc:
            last_exc = exc
            delay = TRANSLATION_BACKOFF_BASE * (TRANSLATION_BACKOFF_FACTOR ** attempt)
            delay = min(delay, timeout)
            delay = delay + random.uniform(0, 0.25)
            logger.warning(
                "[KYRA AI] translate retry attempt=%s/%s delay=%.2fs error=%s",
                attempt + 1,
                retries,
                delay,
                exc,
            )
            time.sleep(delay)
    logger.warning("[KYRA AI] translate failed after retries: %s", last_exc)
    return text
