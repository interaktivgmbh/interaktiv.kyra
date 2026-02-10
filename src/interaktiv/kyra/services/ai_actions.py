import copy
import json
import os
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from AccessControl import Unauthorized
from interaktiv.kyra import logger
from interaktiv.kyra.api import Chat
from interaktiv.kyra.api.prompts import Prompts
from interaktiv.kyra.services.audit import log_ai_action
from interaktiv.kyra.services.base import ServiceBase
from plone.i18n.normalizer import idnormalizer
from persistent.list import PersistentList
from persistent.mapping import PersistentMapping
from plone import api
from plone.base.interfaces import IPloneSiteRoot
from plone.restapi.deserializer import json_body
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.component.hooks import setSite
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse

PLAN_STORAGE_KEY = "interaktiv.kyra.ai_actions_plans"
TRANSLATION_MAX_CONCURRENCY_DEFAULT = 16
TRANSLATION_TIMEOUT_DEFAULT = 60
TRANSLATION_RETRIES_DEFAULT = 2
TRANSLATION_BACKOFF_BASE = 0.5
TRANSLATION_BACKOFF_FACTOR = 2.0
ALLOWLIST = {
    "update_title",
    "update_description",
    "update_language",
    "translate_content",
    "insert_text_block",
    "insert_heading_block",
    "insert_list_block",
    "insert_quote_block",
    "insert_image_block",
    "insert_block",
}

PLAN_PROMPT_ID = "kyra-actions-plan"
PLAN_PROMPT_CACHE_KEY = "interaktiv.kyra.ai_actions_plan_prompt_id_v3"
TRANSLATE_PROMPT_CACHE_KEY = "interaktiv.kyra.ai_translate_prompt_id_v1"
TRANSLATION_MAX_CONCURRENCY_DEFAULT = 16
TRANSLATION_TIMEOUT_DEFAULT = 60
TRANSLATION_RETRIES_DEFAULT = 2
TRANSLATION_BACKOFF_BASE = 0.5
TRANSLATION_BACKOFF_FACTOR = 2.0
TRANSLATION_MAX_CONCURRENCY_DEFAULT = 16
TRANSLATION_TIMEOUT_DEFAULT = 60
TRANSLATION_RETRIES_DEFAULT = 2
TRANSLATION_BACKOFF_BASE = 0.5
TRANSLATION_BACKOFF_FACTOR = 2.0

UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
RESOLVEUID_RE = re.compile(r"resolveuid/([0-9a-fA-F-]{32,36})")
IMAGES_SCALE_RE = re.compile(r"@@images/([^/]+)/([^/?#]+)")

MAX_GRID_COLUMNS = 6
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "ein": 1, "eine": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fuenf": 5, "fünf": 5, "sechs": 6,
}
_NUMBER_WORD_PATTERN = "|".join(re.escape(w) for w in _NUMBER_WORDS)


def _parse_grid_columns(text: str) -> int:
    """Parse column count from free text (EN + DE). Returns 1-6, default 3."""
    if not isinstance(text, str):
        return 3
    lower = text.lower()
    # digit + column/Spalte variants: "3 columns", "3-spaltig", "3 Spalten"
    m = re.search(r"(\d+)\s*[-\s]?\s*(?:columns?|spalte(?:n|ig)?|spaltig)", lower)
    if m:
        return max(1, min(MAX_GRID_COLUMNS, int(m.group(1))))
    # number word + column/Spalte: "three columns", "drei Spalten"
    m = re.search(
        rf"({_NUMBER_WORD_PATTERN})\s*[-\s]?\s*(?:columns?|spalte(?:n|ig)?|spaltig)", lower
    )
    if m:
        return max(1, min(MAX_GRID_COLUMNS, _NUMBER_WORDS.get(m.group(1), 3)))
    # compound German: "dreispaltig", "vierspaltig"
    m = re.search(rf"({_NUMBER_WORD_PATTERN})spaltig", lower)
    if m:
        return max(1, min(MAX_GRID_COLUMNS, _NUMBER_WORDS.get(m.group(1), 3)))
    return 3


def _wants_grid(text: str) -> bool:
    """Detect grid/column layout intent in EN or DE."""
    if not isinstance(text, str):
        return False
    lower = text.lower()
    if re.search(r"\bgrid\b", lower):
        return True
    if re.search(r"\braster\b", lower):
        return True
    if re.search(r"spalten[-\s]?layout", lower):
        return True
    if re.search(r"\d+\s*[-]?\s*spaltig", lower):
        return True
    if re.search(rf"(?:{_NUMBER_WORD_PATTERN})spaltig", lower):
        return True
    if re.search(r"\d+\s*[-\s]?\s*(?:columns?|spalte(?:n)?)\b", lower):
        return True
    if re.search(rf"(?:{_NUMBER_WORD_PATTERN})\s*[-\s]?\s*(?:columns?|spalte(?:n)?)\b", lower):
        return True
    return False


def _get_int_env(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, "").strip())
        return value if value > 0 else default
    except Exception:
        return default


def _max_translation_concurrency() -> int:
    return _get_int_env("KYRA_TRANSLATE_MAX_CONCURRENCY", TRANSLATION_MAX_CONCURRENCY_DEFAULT)


def _translation_timeout_seconds() -> int:
    return _get_int_env("KYRA_TRANSLATE_TIMEOUT", TRANSLATION_TIMEOUT_DEFAULT)


def _translation_retries() -> int:
    return _get_int_env("KYRA_TRANSLATE_RETRIES", TRANSLATION_RETRIES_DEFAULT)


def _extract_value_after(label: str, text: str) -> Optional[str]:
    lower = text.lower()
    idx = lower.find(label)
    if idx == -1:
        return None
    value = text[idx + len(label):].strip()
    for sep in (";", "\n"):
        if sep in value:
            value = value.split(sep)[0].strip()
    return value or None


def _derive_actions(goal: str, target=None, kyra=None, translate_opts: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    translate_intent = False
    inferred_lang = None
    if _is_translation_goal(goal):
        translate_intent = True
        inferred_lang = _guess_target_language(goal)
    target_lang = translate_opts.get("target_language") if translate_opts else None
    mode = translate_opts.get("mode") if translate_opts else None
    overwrite = bool(translate_opts.get("overwrite")) if translate_opts else False
    effective_lang = target_lang or inferred_lang or "en"

    if kyra is not None:
        actions = _derive_actions_from_gateway(goal, target, kyra)
        if actions:
            if translate_opts or translate_intent:
                actions = _remove_action(actions, "update_language")
                actions.append(
                    {
                        "type": "translate_content",
                        "payload": {
                            "target_language": effective_lang,
                            "mode": mode or "single",
                            "overwrite": overwrite,
                        },
                    }
                )
            if not _has_teaser_action(actions):
                teaser_actions = [
                    a
                    for a in _derive_actions_from_patterns(goal, target)
                    if a.get("type") == "insert_block"
                       and isinstance(a.get("payload", {}).get("block"), dict)
                       and a["payload"]["block"].get("@type") == "teaser"
                ]
                actions.extend(teaser_actions)
            # Enrich with derived helpers (grid/video/html/teaser) and cleanup
            actions = _maybe_add_grid_action(goal, actions)
            actions = _maybe_add_video_action(goal, actions)
            actions = _maybe_add_html_action(goal, actions)
            return _prune_text_when_teaser(
                goal,
                _merge_text_into_html(
                    _dedupe_teasers(
                        _normalize_teaser_overwrite(
                            _maybe_add_teaser_action(goal, target, actions)
                        )
                    )
                ),
            )

    actions: List[Dict[str, Any]] = []
    if translate_opts or translate_intent:
        actions.append(
            {
                "type": "translate_content",
                "payload": {
                    "target_language": effective_lang,
                    "mode": mode or "single",
                    "overwrite": overwrite,
                },
            }
        )
    title = _extract_value_after("title:", goal)
    description = _extract_value_after("description:", goal)
    language = _extract_value_after("language:", goal)

    if title:
        actions.append({"type": "update_title", "payload": {"title": title}})
    if description:
        actions.append(
            {"type": "update_description", "payload": {"description": description}}
        )
    if language and not translate_intent and not translate_opts:
        actions.append({"type": "update_language", "payload": {"language": language}})

    if not actions:
        actions.extend(_derive_actions_from_patterns(goal, target))

    actions = _maybe_add_grid_action(goal, actions)
    actions = _maybe_add_video_action(goal, actions)
    actions = _maybe_add_html_action(goal, actions)
    return _prune_text_when_teaser(
        goal,
        _merge_text_into_html(
            _dedupe_teasers(
                _normalize_teaser_overwrite(_maybe_add_teaser_action(goal, target, actions))
            )
        ),
    )


def _build_plan_prompt_payload() -> Dict[str, Any]:
    return {
        "name": "Kyra Actions Planner",
        "prompt": (
            "You are a planning assistant for Plone editor actions.\n"
            "Given a user request and the current page"
            " metadata, return JSON only with an action plan.\n\n"
            "Allowed action types:\n"
            "- update_title (payload: {\"title\": \"...\"})\n"
            "- update_description (payload: {\"description\": \"...\"})\n"
            "- update_language (payload: {\"language\": \"...\"})\n\n"
            "- insert_text_block (payload: {\"text\": \"...\"})\n\n"
            "- insert_heading_block (payload: {\"text\": \"...\", \"level\": 2})\n"
            "- insert_list_block (payload: {\"items\": [\"...\"], \"ordered\": false})\n"
            "- insert_quote_block (payload: {\"text\": \"...\", \"citation\": \"...\"})\n"
            "- insert_image_block (payload: {\"url\": \"...\" OR \"uid\": \"...\", \"alt\": \"...\", \"scale\": \"large\"})\n\n"
            "- insert_block (payload: {\"block\": {\"@type\": \"...\", ...}})  # for advanced blocks like video, listing, teaser, map\n\n"
            "Grid blocks:\n"
            "  For grid/column layouts, use insert_block with @type \"gridBlock\".\n"
            "  Extract the number of columns from the user request (1-6, default 3).\n"
            "  Payload: {\"block\": {\"@type\": \"gridBlock\", \"columns\": <N>}}\n"
            "  Examples: \"3 Spalten\" -> 3, \"dreispaltig\" -> 3, \"4-column grid\" -> 4\n\n"
            "If the request asks to improve the description but no new text is given,\n"
            "rewrite the current description into a clearer, shorter version. If the\n"
            "current description is empty, draft a concise one-sentence description.\n"
            "If the request is unclear or unsupported, return an empty actions array.\n"
            "Return JSON in this shape:\n"
            "{\"actions\": [{\"type\": \"...\", \"payload\": {...}}], "
            "\"summary\": \"...\"}\n\n"
            "INPUT:\n{{input}}\n\n"
            "Return JSON only. Do not wrap in code fences."
        ),
        "categories": ["Actions"],
        "actionType": "replace",
        "metadata": {"categories": ["Actions"], "action": "replace"},
    }


def _build_plan_input(goal: str, target=None) -> str:
    lines = [f"Request: {goal.strip()}"]
    if target is not None:
        title = getattr(target, "Title", lambda: "")() or ""
        description = getattr(target, "Description", lambda: "")() or ""
        language = getattr(target, "Language", lambda: "")() or ""
        if title:
            lines.append(f"Current title: {title}")
        if description:
            lines.append(f"Current description: {description}")
        if language:
            lines.append(f"Current language: {language}")
    if _wants_grid(goal):
        columns = _parse_grid_columns(goal)
        lines.append(f"Hint: User wants a grid layout with {columns} columns.")
    return "\n".join(lines)


def _extract_text_from_gateway(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("response", "result", "content", "text", "output"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if isinstance(data, str):
        return data
    return ""


def _parse_actions_payload(payload: Any) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if isinstance(payload, dict):
        payload_actions = payload.get("actions")
        if isinstance(payload_actions, list):
            actions = payload_actions
    elif isinstance(payload, list):
        actions = payload
    if not isinstance(actions, list):
        return []
    return [action for action in actions if isinstance(action, dict)]


def _is_recoverable_prompt_error(message: str) -> bool:
    lowered = (message or "").lower()
    return (
        "404" in lowered
        or "not found" in lowered
        or "invalid uuid" in lowered
        or "validation error" in lowered
    )


def _get_cached_prompt_id() -> Optional[str]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    value = annotations.get(PLAN_PROMPT_CACHE_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _set_cached_prompt_id(prompt_id: str) -> None:
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        return
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[PLAN_PROMPT_CACHE_KEY] = prompt_id


def _get_cached_translate_prompt_id() -> Optional[str]:
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    value = annotations.get(TRANSLATE_PROMPT_CACHE_KEY)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _set_cached_translate_prompt_id(prompt_id: str) -> None:
    if not isinstance(prompt_id, str) or not prompt_id.strip():
        return
    portal = api.portal.get()
    annotations = IAnnotations(portal)
    annotations[TRANSLATE_PROMPT_CACHE_KEY] = prompt_id


def _canonical_action_type(action_type: str) -> str:
    action_type = (action_type or "").strip()
    mapping = {
        "add_text_block": "insert_text_block",
        "append_text_block": "insert_text_block",
        # generic block insertion
        "add_block": "insert_block",
        "insert_generic_block": "insert_block",
        "add_heading_block": "insert_heading_block",
        "insert_heading": "insert_heading_block",
        "add_heading": "insert_heading_block",
        "heading_block": "insert_heading_block",
        "add_list_block": "insert_list_block",
        "insert_list": "insert_list_block",
        "add_list": "insert_list_block",
        "bullet_list": "insert_list_block",
        "ordered_list": "insert_list_block",
        "add_quote": "insert_quote_block",
        "insert_quote": "insert_quote_block",
        "quote_block": "insert_quote_block",
        "add_image_block": "insert_image_block",
        "insert_image": "insert_image_block",
        "image_block": "insert_image_block",
        "add_image": "insert_image_block",
        "translate": "translate_content",
        "translate_content": "translate_content",
    }
    return mapping.get(action_type, action_type)


def _remove_action(actions: List[Dict[str, Any]], action_type: str) -> List[Dict[str, Any]]:
    return [a for a in actions if a.get("type") != action_type]


def _is_translation_goal(goal: str) -> bool:
    if not isinstance(goal, str):
        return False
    return bool(re.search(r"\b(translate|übersetz|uebersetz|translation)\b", goal, re.IGNORECASE))


def _guess_target_language(goal: str) -> Optional[str]:
    if not isinstance(goal, str):
        return None
    goal_low = goal.lower()
    mapping = {
        "english": "en",
        "englisch": "en",
        "en": "en",
        "german": "de",
        "deutsch": "de",
        "de": "de",
        "french": "fr",
        "français": "fr",
        "fr": "fr",
        "spanish": "es",
        "español": "es",
        "es": "es",
    }
    for key, code in mapping.items():
        if re.search(rf"\b{re.escape(key)}\b", goal_low):
            return code
    return None


def _extract_heading_level_from_text(text: str, default: int = 2) -> int:
    if not isinstance(text, str) or not text.strip():
        return default
    match = re.search(r"\b(?:h|heading\s*level|level)\s*([1-6])\b", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\b([1-6])\s*(?:st|nd|rd|th)?\s*heading\b", text, re.IGNORECASE)
    if match:
        try:
            value = int(match.group(1))
            return min(max(value, 1), 6)
        except (TypeError, ValueError):
            return default
    return default


def _split_list_items(text: str) -> List[str]:
    if not isinstance(text, str):
        return []
    if "\n" in text or ";" in text:
        parts = re.split(r"[;\n]+", text)
    else:
        parts = re.split(r"\s*\d+[.)]\s*", text)
        if len(parts) <= 1:
            parts = [text]
    cleaned = []
    for part in parts:
        if not isinstance(part, str):
            continue
        value = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", part).strip()
        if value:
            cleaned.append(value)
    return cleaned


def _detect_ordered_list(text: str, items: List[str]) -> bool:
    if isinstance(text, str) and re.search(r"\b(ordered|nummeriert|numbered)\b", text, re.IGNORECASE):
        return True
    if isinstance(text, str) and re.search(r"\d+[.)]", text):
        return True
    for item in items:
        if isinstance(item, str) and re.match(r"^\d+[.)]", item.strip()):
            return True
    return False


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1].strip()
    return value


def _clean_heading_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = re.sub(r"\(\s*h[1-6]\s*\)", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\b(?:h|level)\s*[1-6]\b", "", text, flags=re.IGNORECASE)
    return text.strip()


def _highlighted_text(text: str) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    trimmed = text.strip()
    quoted = re.findall(r'"([^"]+)"', trimmed)
    if not quoted:
        quoted = re.findall(r"'([^']+)'", trimmed)
    if quoted:
        return quoted[0].strip()
    match = re.search(r"\bteaser\b.*?[:\-]\s*(.+?)(?:\n|$)", trimmed, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    sentences = re.split(r"\n+", trimmed)
    return sentences[0].strip()


def _extract_first_url(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text:
        return None
    match = re.search(r"https?://[^\s\"'<>]+", text)
    if match:
        return match.group(0).rstrip(".,;")
    return None


def _extract_teaser_description(text: str) -> Optional[str]:
    if not isinstance(text, str) or not text.strip():
        return None
    description = _extract_value_after("description:", text)
    if description:
        return description
    desc_match = re.search(
        r"(?:descr|description|beschreibung|text|summary)\s*(?:for|to|:)\s*(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if desc_match:
        return desc_match.group(1).strip()
    quoted = re.findall(r'"([^"]+)"', text) or re.findall(r"'([^']+)'", text)
    if len(quoted) >= 2:
        return quoted[-1].strip()
    return None


def _extract_teaser_href(text: str, target=None) -> Optional[str]:
    href = _extract_first_url(text)
    if href:
        return href
    link_match = re.search(
        r"(?:target|link(?:ed)?|href)\s*(?:to|:)?\s*([^\s]+)",
        text,
        re.IGNORECASE,
    )
    if link_match:
        candidate = link_match.group(1).rstrip(".,;")
        if candidate.startswith("http") or candidate.startswith("/"):
            return candidate
    target_url = None
    if target is not None:
        if isinstance(target, dict):
            target_url = target.get("url") or target.get("href")
        else:
            attr = getattr(target, "absolute_url", None)
            if callable(attr):
                try:
                    target_url = attr()
                except Exception:
                    target_url = ""
    if isinstance(target_url, str) and target_url.strip():
        return target_url.strip()
    return None


def _teaser_custom_requested(text: str) -> bool:
    if not isinstance(text, str):
        return False
    if re.search(r"(title|headline|überschrift|custom|overwrite)", text, re.IGNORECASE):
        return True
    if re.search(r"(description|beschreibung)", text, re.IGNORECASE):
        return True
    return False


def _has_teaser_action(actions: List[Dict[str, Any]]) -> bool:
    for action in actions or []:
        if (
            action.get("type") == "insert_block"
            and isinstance(action.get("payload"), dict)
            and isinstance(action["payload"].get("block"), dict)
            and action["payload"]["block"].get("@type") == "teaser"
        ):
            return True
    return False


def _has_block_type(actions: List[Dict[str, Any]], block_type: str) -> bool:
    for action in actions or []:
        if (
            action.get("type") == "insert_block"
            and isinstance(action.get("payload"), dict)
            and isinstance(action["payload"].get("block"), dict)
            and action["payload"]["block"].get("@type") == block_type
        ):
            return True
    return False


def _maybe_add_teaser_action(goal: str, target, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(goal, str):
        return actions
    if _has_teaser_action(actions):
        return actions
    if not re.search(r"\bteaser\b", goal, re.IGNORECASE):
        return actions

    teaser_href = _extract_teaser_href(goal, target)
    if not teaser_href:
        return actions

    teaser_title = _highlighted_text(goal)
    teaser_description = _extract_teaser_description(goal)
    teaser_payload: Dict[str, Any] = {"@type": "teaser", "href": teaser_href}
    custom_requested = _teaser_custom_requested(goal) or bool(teaser_description)
    if custom_requested:
        teaser_payload["overwrite"] = True
        teaser_payload["_custom_requested"] = True
    if custom_requested and teaser_title:
        teaser_payload["title"] = teaser_title
    if custom_requested and teaser_description:
        teaser_payload["description"] = teaser_description

    actions.append({"type": "insert_block", "payload": {"block": teaser_payload}})
    return actions


def _maybe_add_video_action(goal: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(goal, str):
        return actions
    if _has_block_type(actions, "video"):
        return actions
    url_match = re.search(r"https?://\S+", goal)
    is_video = bool(re.search(r"\bvideo\b", goal, re.IGNORECASE))
    url_is_video = url_match and re.search(
        r"(youtube|youtu\.be|vimeo|\.mp4|\.mov|\.webm)",
        url_match.group(0),
        re.IGNORECASE,
    )
    if is_video or url_is_video:
        block = {"@type": "video"}
        if url_match:
            block["url"] = url_match.group(0)
        actions.append({"type": "insert_block", "payload": {"block": block}})
    return actions


def _maybe_add_html_action(goal: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(goal, str):
        return actions
    if _has_block_type(actions, "html"):
        return actions
    if not re.search(r"\bhtml\b", goal, re.IGNORECASE):
        return actions
    html_match = re.search(r"(?:html block|html)\s*[:\-]?\s+(.+)$", goal, re.IGNORECASE)
    html_content = ""
    if html_match:
        html_content = html_match.group(1)
    actions.append(
        {"type": "insert_block", "payload": {"block": {"@type": "html", "html": html_content}}}
    )
    return actions


def _normalize_teaser_overwrite(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    for action in actions or []:
        if (
            action.get("type") == "insert_block"
            and isinstance(action.get("payload"), dict)
            and isinstance(action["payload"].get("block"), dict)
            and action["payload"]["block"].get("@type") == "teaser"
        ):
            block = action["payload"]["block"]
            title = block.get("title")
            desc = block.get("description")
            custom_flag = block.pop("_custom_requested", False)
            if not custom_flag:
                # By default, do not customize teaser content; keep only the link.
                block.pop("overwrite", None)
                block.pop("title", None)
                block.pop("description", None)
            else:
                has_custom = (
                                 isinstance(title, str) and title.strip()
                             ) or (isinstance(desc, str) and desc.strip())
                if not has_custom:
                    block.pop("overwrite", None)
    return actions


def _dedupe_teasers(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = False
    deduped: List[Dict[str, Any]] = []
    for action in actions or []:
        if (
            action.get("type") == "insert_block"
            and isinstance(action.get("payload"), dict)
            and isinstance(action["payload"].get("block"), dict)
            and action["payload"]["block"].get("@type") == "teaser"
        ):
            if seen:
                continue
            seen = True
        deduped.append(action)
    return deduped


def _prune_text_when_teaser(goal: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(goal, str):
        return actions
    if not re.search(r"\bteaser\b", goal, re.IGNORECASE):
        return actions
    if not _has_teaser_action(actions):
        return actions
    pruned: List[Dict[str, Any]] = []
    for action in actions or []:
        if action.get("type") == "insert_text_block":
            continue
        pruned.append(action)
    return pruned


def _merge_text_into_html(actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    has_html = _has_block_type(actions, "html")
    if not has_html:
        return actions

    merged: List[Dict[str, Any]] = []
    pending_text = None

    for action in actions or []:
        if action.get("type") == "insert_text_block":
            pending_text = (action.get("payload") or {}).get("text")
            continue

        if (
            action.get("type") == "insert_block"
            and isinstance(action.get("payload"), dict)
            and isinstance(action["payload"].get("block"), dict)
            and action["payload"]["block"].get("@type") == "html"
        ):
            block = action["payload"]["block"]
            html_content = block.get("html") or ""
            if not html_content and isinstance(pending_text, str) and pending_text.strip():
                block["html"] = pending_text.strip()
                pending_text = None
            merged.append(action)
        else:
            merged.append(action)

    return merged


def _normalize_image_reference(payload: Dict[str, Any], action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    url = (
        payload.get("url")
        or payload.get("src")
        or payload.get("href")
        or action.get("url")
        or action.get("src")
    )
    uid = (
        payload.get("uid")
        or payload.get("resolveuid")
        or payload.get("image_uid")
        or payload.get("imageUid")
        or action.get("uid")
        or action.get("resolveuid")
    )
    image_field = payload.get("image_field") or payload.get("field") or "image"
    scale = payload.get("scale") or payload.get("size") or payload.get("image_scale")
    is_internal = False

    if not url and isinstance(uid, str) and uid.strip():
        url = f"resolveuid/{uid.strip()}"
        is_internal = True

    if isinstance(url, str) and UUID_RE.match(url.strip()):
        url = f"resolveuid/{url.strip()}"
        is_internal = True

    if isinstance(url, str):
        url = url.strip()
        images_match = IMAGES_SCALE_RE.search(url)
        if images_match:
            image_field = images_match.group(1) or image_field
            scale = images_match.group(2) or scale
            is_internal = True
        resolve_match = RESOLVEUID_RE.search(url)
        if resolve_match:
            url = f"resolveuid/{resolve_match.group(1)}"
            is_internal = True

    if isinstance(url, str) and url:
        if not scale and is_internal:
            scale = "large"
        return {
            "url": url,
            "image_field": image_field or "image",
            "scale": scale,
            "size": scale,
        }
    return None


def _normalize_action(action: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    action_type = (
        action.get("type") or action.get("action") or action.get("name") or ""
    )
    action_type = _canonical_action_type(action_type)
    if action_type not in ALLOWLIST:
        return None

    payload = action.get("payload")
    if not isinstance(payload, dict):
        payload = {}

    if action_type == "update_title":
        title = payload.get("title") or action.get("title") or payload.get("value")
        if isinstance(title, str) and title.strip():
            return {"type": "update_title", "payload": {"title": title.strip()}}
    elif action_type == "update_description":
        description = (
            payload.get("description")
            or action.get("description")
            or payload.get("value")
        )
        if isinstance(description, str) and description.strip():
            return {
                "type": "update_description",
                "payload": {"description": description.strip()},
            }
    elif action_type == "update_language":
        language = payload.get("language") or action.get("language") or payload.get("value")
        if isinstance(language, str) and language.strip():
            return {
                "type": "update_language",
                "payload": {"language": language.strip()},
            }
    elif action_type == "insert_text_block":
        text = payload.get("text") or action.get("text") or payload.get("value")
        if isinstance(text, str) and text.strip():
            return {
                "type": "insert_text_block",
                "payload": {"text": text.strip()},
            }
    elif action_type == "insert_heading_block":
        text = (
            payload.get("text")
            or payload.get("title")
            or action.get("text")
            or action.get("title")
        )
        level = (
            payload.get("level")
            or payload.get("heading_level")
            or action.get("level")
        )
        try:
            level_int = int(level) if level is not None else None
        except (TypeError, ValueError):
            level_int = None
        if level_int is None:
            level_int = _extract_heading_level_from_text(text or "", 2)
        level_int = min(max(level_int, 1), 6)
        if isinstance(text, str) and text.strip():
            cleaned = _clean_heading_text(text)
            return {
                "type": "insert_heading_block",
                "payload": {
                    "text": cleaned or text.strip(),
                    "level": level_int,
                },
            }
    elif action_type == "insert_list_block":
        items = payload.get("items") or action.get("items")
        raw_text = payload.get("text") or action.get("text") or ""
        if isinstance(items, str):
            items = _split_list_items(items)
        if items is None:
            items = _split_list_items(raw_text) if isinstance(raw_text, str) else []
        if not isinstance(items, list):
            items = []
        items = [
            re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", item).strip()
            for item in items
            if isinstance(item, str) and item.strip()
        ]
        ordered = payload.get("ordered")
        if ordered is None:
            ordered = action.get("ordered")
        if ordered is None:
            ordered = _detect_ordered_list(raw_text, items)
        ordered = bool(ordered)
        if items:
            return {
                "type": "insert_list_block",
                "payload": {"items": items, "ordered": ordered},
            }
    elif action_type == "insert_quote_block":
        text = payload.get("text") or payload.get("quote") or action.get("text")
        citation = payload.get("citation") or payload.get("author") or action.get("citation")
        if isinstance(text, str) and text.strip():
            normalized = {"text": text.strip()}
            if isinstance(citation, str) and citation.strip():
                normalized["citation"] = citation.strip()
            return {"type": "insert_quote_block", "payload": normalized}
    elif action_type == "insert_image_block":
        alt = payload.get("alt") or payload.get("title") or action.get("alt")
        caption = payload.get("caption") or action.get("caption")
        normalized = _normalize_image_reference(payload, action)
        if normalized:
            if not normalized.get("scale"):
                normalized.pop("scale", None)
                normalized.pop("size", None)
            if isinstance(alt, str) and alt.strip():
                normalized["alt"] = alt.strip()
            if isinstance(caption, str) and caption.strip():
                normalized["caption"] = caption.strip()
            return {"type": "insert_image_block", "payload": normalized}
    elif action_type == "insert_block":
        block = payload.get("block") if isinstance(payload, dict) else None
        if block is None and isinstance(payload, dict):
            block = payload
        if isinstance(block, dict) and isinstance(block.get("@type"), str) and block.get("@type").strip():
            block_type = block["@type"].strip()
            if block_type in ("gridBlock", "grid"):
                block["@type"] = "gridBlock"
                columns = block.get("columns")
                if isinstance(columns, (int, str)):
                    try:
                        columns = max(1, min(MAX_GRID_COLUMNS, int(columns)))
                    except (TypeError, ValueError):
                        columns = 3
                else:
                    columns = 3
                if "blocks" not in block or "blocks_layout" not in block:
                    heading = block.get("heading") or block.get("title")
                    body = block.get("body") or block.get("text")
                    block = _build_grid_block(columns, heading, body)
                else:
                    block["columns"] = columns
            return {"type": "insert_block", "payload": {"block": block}}
    elif action_type == "translate_content":
        target_language = payload.get("target_language") or action.get("target_language")
        mode = payload.get("mode") or action.get("mode") or "single"
        overwrite = bool(payload.get("overwrite") or action.get("overwrite"))
        if isinstance(target_language, str) and target_language.strip():
            return {
                "type": "translate_content",
                "payload": {
                    "target_language": target_language.strip(),
                    "mode": mode if mode in ("single", "subtree") else "single",
                    "overwrite": overwrite,
                },
            }
    return None


def _extract_json_from_text(text: str) -> Optional[Any]:
    text = text.strip()
    if not text:
        return None
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.startswith("json"):
            text = text[4:].strip()
    if text.startswith("{") or text.startswith("["):
        try:
            return json.loads(text)
        except Exception:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start: end + 1])
        except Exception:
            return None
    return None


def _apply_plan_prompt(kyra, goal: str, target=None) -> Any:
    apply_payload = {
        "query": goal,
        "input": _build_plan_input(goal, target),
    }

    cached_id = _get_cached_prompt_id()
    if cached_id:
        response = kyra.prompts.apply(cached_id, apply_payload)
        if not (isinstance(response, dict) and response.get("error")):
            return response
        if not _is_recoverable_prompt_error(str(response.get("error"))):
            return response

    created = kyra.prompts.create(_build_plan_prompt_payload())
    if isinstance(created, dict) and created.get("error"):
        return created

    new_id = created.get("id") or created.get("_id")
    if new_id:
        _set_cached_prompt_id(new_id)
        return kyra.prompts.apply(new_id, apply_payload)

    return {"error": "AI Gateway did not return a prompt id"}


def _derive_actions_from_gateway(goal: str, target, kyra) -> List[Dict[str, Any]]:
    response = _apply_plan_prompt(kyra, goal, target)
    if isinstance(response, dict) and response.get("error"):
        return []

    payload: Any = None
    if isinstance(response, dict):
        if isinstance(response.get("actions"), list):
            payload = response
        else:
            for key in ("result", "response", "data"):
                value = response.get(key)
                if isinstance(value, (dict, list)):
                    payload = value
                    break
                if isinstance(value, str):
                    payload = _extract_json_from_text(value)
                    break
    if payload is None:
        response_text = _extract_text_from_gateway(response)
        payload = _extract_json_from_text(response_text)
    if payload is None:
        return []

    raw_actions = _parse_actions_payload(payload)
    normalized: List[Dict[str, Any]] = []
    for action in raw_actions:
        normalized_action = _normalize_action(action)
        if normalized_action is not None:
            normalized.append(normalized_action)
    return normalized


def _derive_actions_from_patterns(goal: str, target=None) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    text = goal.strip()

    title_match = re.search(
        r"(?:title|titel)\s*(?:to|auf)?\s+(.+?)(?:\s+and\b|$)",
        text,
        re.IGNORECASE,
    )
    if not title_match:
        title_match = re.search(
            r"(?:title|titel)\s*:\s*([^\n]+)", text, re.IGNORECASE
        )
    if title_match:
        title = _strip_wrapping_quotes(title_match.group(1))
        if title:
            actions.append({"type": "update_title", "payload": {"title": title}})

    desc_match = re.search(
        r"(?:description|beschreibung)\s*(?:to|auf)?\s+(.+?)(?:\s+and\b|$)",
        text,
        re.IGNORECASE,
    )
    if not desc_match:
        desc_match = re.search(
            r"(?:description|beschreibung)\s*:\s*([^\n]+)",
            text,
            re.IGNORECASE,
        )
    if desc_match:
        description = _strip_wrapping_quotes(desc_match.group(1))
        if description:
            actions.append(
                {
                    "type": "update_description",
                    "payload": {"description": description},
                }
            )

    teaser_handled = False
    if re.search(r"\bteaser\b", text, re.IGNORECASE):
        teaser_title = _highlighted_text(text)
        teaser_description = _extract_teaser_description(text)
        teaser_href = _extract_teaser_href(text, target)
        if teaser_href:
            teaser_payload: Dict[str, Any] = {"@type": "teaser", "href": teaser_href}
            custom_requested = _teaser_custom_requested(text) or bool(teaser_description)
            if custom_requested:
                teaser_payload["overwrite"] = True
                teaser_payload["_custom_requested"] = True
                if teaser_title:
                    teaser_payload["title"] = teaser_title
                if teaser_description:
                    teaser_payload["description"] = teaser_description
            actions.append({"type": "insert_block", "payload": {"block": teaser_payload}})
            teaser_handled = True

    text_block_match = None
    if not teaser_handled:
        text_block_match = re.search(
            r"(?:text block|textblock)\s*[:\-]?\s+(.+)$", text, re.IGNORECASE
        )
        if not text_block_match:
            text_block_match = re.search(
                r"(?:add|insert|fuege|füge).*?(?:text block|textblock)\s*[:\-]?\s+(.+)$",
                text,
                re.IGNORECASE,
            )
        if text_block_match:
            block_text = _strip_wrapping_quotes(text_block_match.group(1))
            if block_text:
                actions.append(
                    {
                        "type": "insert_text_block",
                        "payload": {"text": block_text},
                    }
                )

    heading_match = re.search(
        r"(?:heading|headline|überschrift)\s*[:\-]?\s+(.+)$",
        text,
        re.IGNORECASE,
    )
    if heading_match:
        heading_text = _clean_heading_text(
            _strip_wrapping_quotes(heading_match.group(1))
        )
        level = _extract_heading_level_from_text(text, 2)
        if heading_text:
            actions.append(
                {
                    "type": "insert_heading_block",
                    "payload": {"text": heading_text, "level": level},
                }
            )

    list_match = re.search(
        r"(?:list|liste)\s*[:\-]?\s+(.+)$", text, re.IGNORECASE
    )
    if list_match:
        items_text = list_match.group(1).strip()
        items = _split_list_items(items_text)
        ordered = _detect_ordered_list(text, items)
        if items:
            actions.append(
                {
                    "type": "insert_list_block",
                    "payload": {"items": items, "ordered": ordered},
                }
            )

    quote_match = re.search(
        r"(?:quote|zitat)\s*[:\-]?\s+(.+)$", text, re.IGNORECASE
    )
    if quote_match:
        quote_text = _strip_wrapping_quotes(quote_match.group(1))
        if quote_text:
            actions.append(
                {
                    "type": "insert_quote_block",
                    "payload": {"text": quote_text},
                }
            )

    image_match = re.search(
        r"(?:image|bild)\s*[:\-]?\s+(.+)$", text, re.IGNORECASE
    )
    if image_match:
        image_text = image_match.group(1).strip()
        url_match = re.search(r"https?://\S+", image_text)
        resolveuid_match = RESOLVEUID_RE.search(image_text)
        uid_match = UUID_RE.search(image_text)
        if url_match:
            actions.append(
                {
                    "type": "insert_image_block",
                    "payload": {"url": url_match.group(0)},
                }
            )
        elif resolveuid_match:
            actions.append(
                {
                    "type": "insert_image_block",
                    "payload": {"url": f"resolveuid/{resolveuid_match.group(1)}"},
                }
            )
        elif uid_match:
            actions.append(
                {
                    "type": "insert_image_block",
                    "payload": {"uid": uid_match.group(0)},
                }
            )

    video_url_match = re.search(r"https?://\S+", text)
    wants_video = re.search(r"\bvideo\b", text, re.IGNORECASE)
    url_is_video = video_url_match and re.search(
        r"(youtube|youtu\.be|vimeo|\.mp4|\.mov|\.webm)",
        video_url_match.group(0),
        re.IGNORECASE,
    )
    if wants_video or url_is_video:
        block = {"@type": "video"}
        if video_url_match:
            block["url"] = video_url_match.group(0)
        actions.append({"type": "insert_block", "payload": {"block": block}})

    if re.search(r"\bmap\b|\bkart", text, re.IGNORECASE):
        actions.append({"type": "insert_block", "payload": {"block": {"@type": "maps"}}})

    if re.search(r"\bhtml\b", text, re.IGNORECASE):
        html_match = re.search(
            r"(?:html block|html)\s*[:\-]?\s+(.+)$", text, re.IGNORECASE
        )
        html_content = ""
        if html_match:
            html_content = html_match.group(1)
        actions.append(
            {
                "type": "insert_block",
                "payload": {"block": {"@type": "html", "html": html_content}},
            }
        )

    if re.search(r"\blisting\b", text, re.IGNORECASE):
        variation = "default"
        if re.search(r"image", text, re.IGNORECASE):
            variation = "listingImage"
        actions.append(
            {
                "type": "insert_block",
                "payload": {"block": {"@type": "listing", "variation": variation}},
            }
        )

    # Teaser block
    if re.search(r"\bteaser\b", text, re.IGNORECASE):
        teaser_title = _highlighted_text(text)
        teaser_description = _extract_teaser_description(text)
        teaser_href = _extract_teaser_href(text, target)
        if teaser_href:
            teaser_payload: Dict[str, Any] = {"@type": "teaser", "href": teaser_href}
            if teaser_title or teaser_description:
                teaser_payload["overwrite"] = True
            if teaser_title:
                teaser_payload["title"] = teaser_title
            if teaser_description:
                teaser_payload["description"] = teaser_description
            actions.append({"type": "insert_block", "payload": {"block": teaser_payload}})

    # Grid block (default Volto grid block id: gridBlock)
    if _wants_grid(text):
        columns = _parse_grid_columns(text)
        heading_text = None
        body_text = None
        quoted = re.findall(r'"([^"]+)"', text)
        if len(quoted) >= 2:
            heading_text, body_text = quoted[0], quoted[1]
        elif len(quoted) == 1:
            heading_text = quoted[0]
        actions.append(
            {
                "type": "insert_block",
                "payload": {"block": _build_grid_block(columns, heading_text, body_text)},
            }
        )

    return actions


def _maybe_add_grid_action(goal: str, actions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure grid action matches the user's requested column count and content.

    If the gateway already proposed a grid, replace it with one that has the
    correct column count and content extracted from the goal text.  If no grid
    exists yet, append one.
    """
    if not isinstance(goal, str):
        return actions
    if not _wants_grid(goal):
        return actions

    columns = _parse_grid_columns(goal)

    heading_text = None
    body_text = None
    quoted = re.findall(r'"([^"]+)"', goal)
    if len(quoted) >= 2:
        heading_text, body_text = quoted[0], quoted[1]
    elif len(quoted) == 1:
        heading_text = quoted[0]

    correct_grid = {
        "type": "insert_block",
        "payload": {"block": _build_grid_block(columns, heading_text, body_text)},
    }

    # Replace existing grid action if present, otherwise append
    replaced = False
    for i, a in enumerate(actions):
        if (
            a.get("type") == "insert_block"
            and isinstance((a.get("payload") or {}).get("block"), dict)
            and a["payload"]["block"].get("@type") in ("grid", "gridBlock")
        ):
            actions[i] = correct_grid
            replaced = True
            break

    if not replaced:
        actions.append(correct_grid)

    # Remove standalone heading/text/list/quote blocks whose content is
    # already embedded inside the grid columns, so only the grid is inserted.
    grid_texts = set()
    if heading_text:
        grid_texts.add(heading_text.strip().lower())
    if body_text:
        grid_texts.add(body_text.strip().lower())

    if grid_texts:
        actions = [
            a for a in actions
            if not _is_redundant_block_for_grid(a, grid_texts)
        ]

    return actions


def _is_redundant_block_for_grid(
    action: Dict[str, Any], grid_texts: set
) -> bool:
    """Return True if this action is a standalone block whose content
    duplicates text already placed inside grid columns."""
    atype = action.get("type") or ""
    payload = action.get("payload") or {}

    if atype in ("insert_heading_block", "insert_text_block", "insert_quote_block"):
        text = (payload.get("text") or "").strip().lower()
        if text and text in grid_texts:
            return True
    elif atype == "insert_list_block":
        items = payload.get("items") or []
        joined = " ".join(str(i) for i in items).strip().lower()
        if joined and joined in grid_texts:
            return True
    return False


def _preview_from_actions(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    summaries = []
    diffs = []
    for action in actions:
        action_type = action.get("type")
        payload = action.get("payload") or {}
        if action_type == "update_title":
            summaries.append("Update title")
            diffs.append(f"- title: (current)\n+ title: {payload.get('title')}")
        elif action_type == "update_description":
            summaries.append("Update description")
            diffs.append(
                f"- description: (current)\n+ description: {payload.get('description')}"
            )
        elif action_type == "update_language":
            summaries.append("Update language")
            diffs.append(
                f"- language: (current)\n+ language: {payload.get('language')}"
            )
        elif action_type == "insert_text_block":
            summaries.append("Insert text block")
            diffs.append(f"+ block: {payload.get('text')}")
        elif action_type == "insert_heading_block":
            summaries.append("Insert heading block")
            diffs.append(
                f"+ heading (h{payload.get('level', 2)}): {payload.get('text')}"
            )
        elif action_type == "insert_list_block":
            summaries.append("Insert list block")
            items = payload.get("items") or []
            ordered = payload.get("ordered", False)
            label = "ordered list" if ordered else "list"
            diffs.append(f"+ {label}: {', '.join(items)}")
        elif action_type == "insert_quote_block":
            summaries.append("Insert quote block")
            diffs.append(f"+ quote: {payload.get('text')}")
        elif action_type == "insert_image_block":
            summaries.append("Insert image block")
            scale = payload.get("scale") or payload.get("size")
            scale_text = f" ({scale})" if scale else ""
            diffs.append(f"+ image: {payload.get('url')}{scale_text}")
        elif action_type == "insert_block":
            block = payload.get("block") or {}
            block_type = block.get("@type")
            if block_type in ("gridBlock", "grid"):
                cols = block.get("columns", 3)
                summaries.append(f"Insert {cols}-column grid block")
                diffs.append(f"+ grid: {cols} columns")
            elif block_type == "teaser":
                summaries.append("Insert teaser block")
                title = block.get("title") or ""
                href = block.get("href") or ""
                diffs.append(f"+ teaser: {title} -> {href}")
            elif block_type == "video":
                summaries.append("Insert video block")
                diffs.append(f"+ video: {block.get('url')}")
            elif block_type == "html":
                summaries.append("Insert html block")
                snippet = (block.get("html") or "")[:40]
                diffs.append(f"+ html: {snippet}")
        elif action_type == "translate_content":
            summaries.append("Translate content")
            diffs.append(
                f"+ translate -> {payload.get('target_language')} ({payload.get('mode', 'single')})"
            )

    return {
        "summary": ", ".join(summaries) if summaries else "No changes proposed",
        "diff": "\n".join(diffs),
        "human_steps": summaries,
    }


def _build_translation_stub(actions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for action in actions:
        if action.get("type") == "translate_content":
            payload = action.get("payload") or {}
            return {
                "target_language": payload.get("target_language"),
                "mode": payload.get("mode", "single"),
                "overwrite": bool(payload.get("overwrite")),
            }
    return None


def _resolve_target(context, data: Dict[str, Any]):
    if context is not None and not IPloneSiteRoot.providedBy(context):
        return context

    page = data.get("page") or {}
    uid = page.get("uid")
    url = page.get("url")

    if uid:
        obj = api.content.get(UID=uid)
        if obj is not None:
            return obj

    if url and isinstance(url, str):
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        if url.startswith("http") and url.startswith(portal_url):
            url = url[len(portal_url):]
        if url.startswith("/"):
            return api.content.get(path=url.lstrip("/"))

    return None


def _ensure_editor(obj):
    if api.user.is_anonymous():
        raise Unauthorized("Login required")
    if obj is None:
        raise BadRequest("Missing target page")
    if not api.user.has_permission("Modify portal content", obj=obj):
        raise Unauthorized("Insufficient permissions")


def _apply_actions(obj, actions: List[Dict[str, Any]]) -> List[str]:
    changed: List[str] = []
    translation_report: Optional[Dict[str, Any]] = None
    for action in actions:
        action_type = action.get("type")
        payload = action.get("payload") or {}
        if action_type not in ALLOWLIST:
            raise BadRequest(f"Action type '{action_type}' is not allowed")

        if action_type == "update_title":
            title = payload.get("title")
            if not isinstance(title, str) or not title.strip():
                raise BadRequest("update_title requires a non-empty title")
            if hasattr(obj, "setTitle"):
                obj.setTitle(title)
            else:
                setattr(obj, "title", title)
            changed.append("title")

        elif action_type == "update_description":
            description = payload.get("description")
            if not isinstance(description, str):
                raise BadRequest("update_description requires a description")
            if hasattr(obj, "setDescription"):
                obj.setDescription(description)
            else:
                setattr(obj, "description", description)
            changed.append("description")

        elif action_type == "update_language":
            language = payload.get("language")
            if not isinstance(language, str) or not language.strip():
                raise BadRequest("update_language requires a language")
            if hasattr(obj, "setLanguage"):
                obj.setLanguage(language)
            else:
                setattr(obj, "language", language)
            changed.append("language")

        elif action_type == "insert_text_block":
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                raise BadRequest("insert_text_block requires text")
            _insert_text_block(obj, text.strip())
            changed.append("blocks")
        elif action_type == "insert_heading_block":
            text = payload.get("text")
            level = payload.get("level", 2)
            if not isinstance(text, str) or not text.strip():
                raise BadRequest("insert_heading_block requires text")
            _insert_heading_block(obj, text.strip(), level)
            changed.append("blocks")
        elif action_type == "insert_list_block":
            items = payload.get("items")
            ordered = payload.get("ordered", False)
            if not isinstance(items, list) or not items:
                raise BadRequest("insert_list_block requires items")
            _insert_list_block(obj, items, ordered)
            changed.append("blocks")
        elif action_type == "insert_quote_block":
            text = payload.get("text")
            citation = payload.get("citation")
            if not isinstance(text, str) or not text.strip():
                raise BadRequest("insert_quote_block requires text")
            _insert_quote_block(obj, text.strip(), citation)
            changed.append("blocks")
        elif action_type == "insert_image_block":
            url = payload.get("url")
            alt = payload.get("alt")
            caption = payload.get("caption")
            image_field = payload.get("image_field") or payload.get("field")
            scale = payload.get("scale") or payload.get("size")
            if not isinstance(url, str) or not url.strip():
                raise BadRequest("insert_image_block requires url")
            _insert_image_block(
                obj,
                url.strip(),
                alt,
                caption,
                image_field=image_field,
                scale=scale,
            )
            changed.append("blocks")
        elif action_type == "insert_block":
            block = payload.get("block") if isinstance(payload, dict) else None
            if not isinstance(block, dict):
                raise BadRequest("insert_block requires block payload")
            if not isinstance(block.get("@type"), str) or not block.get("@type").strip():
                raise BadRequest("insert_block block requires @type")
            _insert_block(obj, block)
            changed.append("blocks")
        elif action_type == "translate_content":
            translation_report = _apply_translation(obj, payload)
            changed.append("translation")

    obj.reindexObject()
    if translation_report is not None:
        obj._v_last_translation_report = translation_report
    return changed


def _apply_translation(obj, payload: Dict[str, Any]) -> Dict[str, Any]:
    target_language = payload.get("target_language")
    mode = payload.get("mode", "single")
    overwrite = bool(payload.get("overwrite"))
    translator = Chat()
    gateway_available = bool(translator.gateway_url and translator._get_headers())
    logger.info(
        "[KYRA AI TRANSLATE] start target=%s mode=%s overwrite=%s gateway=%s",
        target_language,
        mode,
        overwrite,
        "yes" if gateway_available else "no",
    )

    if not isinstance(target_language, str) or not target_language.strip():
        raise BadRequest("translate_content requires target_language")

    portal = api.portal.get()
    source_lang = getattr(obj, "Language", lambda: "")() or api.portal.get_default_language()
    supported_langs = []
    try:
        pl = api.portal.get_tool("portal_languages")
        supported_langs = pl.getSupportedLanguages() or []
    except Exception:
        supported_langs = []
    if source_lang and source_lang.strip().lower() == target_language.strip().lower():
        return {
            "created": 0,
            "updated": 0,
            "skipped": 1,
            "failed": 0,
            "details": [
                {
                    "source": getattr(obj, "absolute_url", lambda: "")(),
                    "target": None,
                    "status": "skip",
                    "note": "Source and target language are identical",
                }
            ],
            "source_language": source_lang,
            "target_language": target_language,
            "mode": mode,
        }

    target_lang = target_language.strip()
    details: List[Dict[str, Any]] = []

    def _rel_path(o):
        url = getattr(o, "absolute_url", lambda: "")()
        portal_url = portal.absolute_url()
        return url[len(portal_url) :] if url.startswith(portal_url) else url

    def _ensure_lang_root(lang: str):
        root = getattr(portal, lang, None)
        if root:
            return root
        try:
            root = api.content.create(container=portal, type="LRF", id=lang, title=lang)
            return root
        except Exception:
            return None

    def _ensure_container(target_root, path_segments):
        container = target_root
        for seg in path_segments:
            existing = getattr(container, seg, None)
            if existing is None:
                existing = api.content.create(
                    container=container, type="Folder", id=seg, title=seg
                )
            container = existing
        return container

    targets = [obj]
    if mode == "subtree" and hasattr(obj, "objectValues"):
        targets = []
        stack = [obj]
        while stack:
            current = stack.pop()
            targets.append(current)
            children = getattr(current, "objectValues", lambda: [])()
            for child in children:
                stack.append(child)

    created = 0
    updated = 0
    skipped = 0
    failed = 0

    target_root = _ensure_lang_root(target_lang)
    if not target_root:
        return {
            "created": 0,
            "updated": 0,
            "skipped": 0,
            "failed": len(targets),
            "details": [
                {
                    "source": _rel_path(obj),
                    "target": None,
                    "status": "failed",
                    "error": f"Target language root {target_lang} missing",
                }
            ],
            "source_language": source_lang,
            "target_language": target_lang,
            "mode": mode,
        }

    for item in targets:
        rel = _rel_path(item)
        rel_parts = [p for p in rel.split("/") if p]
        # Try to infer source language from path if missing
        if (not source_lang or not source_lang.strip()) and rel_parts:
            if rel_parts[0] in supported_langs:
                source_lang = rel_parts[0]
        if rel_parts and source_lang and rel_parts[0] == source_lang:
            rel_parts = rel_parts[1:]

        existing = None
        status = "updated"

        manager = None
        try:
            from plone.app.multilingual.interfaces import ITranslationManager  # type: ignore

            manager = ITranslationManager(item)
        except Exception:
            manager = None

        if manager is not None:
            try:
                translations = manager.get_translations() or {}
                existing = translations.get(target_lang)
                if existing and not overwrite:
                    details.append(
                        {
                            "source": _rel_path(item),
                            "target": _rel_path(existing),
                            "status": "skip",
                            "note": "Translation exists; overwrite disabled",
                        }
                    )
                    skipped += 1
                    continue
                if existing is None:
                    existing = manager.add_translation(target_lang)
                    if existing:
                        status = "created"
                        created += 1
                    else:
                        logger.warning(
                            "[KYRA AI TRANSLATE] manager.add_translation returned None for %s -> %s",
                            _rel_path(item),
                            target_lang,
                        )
                else:
                    status = "updated"
                    updated += 1
            except Exception:
                existing = None

        if existing is None:
            container = target_root
            if len(rel_parts) > 1:
                try:
                    container = _ensure_container(target_root, rel_parts[:-1])
                except Exception as exc:
                    details.append(
                        {
                            "source": _rel_path(item),
                            "target": None,
                            "status": "failed",
                            "error": f"Could not ensure container: {exc}",
                        }
                    )
                    failed += 1
                    continue

            target_id = rel_parts[-1] if rel_parts else item.getId()
            translated_title_for_id = _translate_text(
                translator, getattr(item, "Title", lambda: "")(), source_lang, target_lang
            )
            norm_id = idnormalizer.normalize(translated_title_for_id) if translated_title_for_id else ""
            if norm_id:
                target_id = norm_id
            existing = getattr(container, target_id, None)

            if existing and not overwrite:
                details.append(
                    {
                        "source": _rel_path(item),
                        "target": _rel_path(existing),
                        "status": "skip",
                        "note": "Translation exists; overwrite disabled",
                    }
                )
                skipped += 1
                continue

            if existing is None:
                try:
                    existing = api.content.copy(source=item, target=container, id=target_id)
                    created += 1
                    status = "created"
                except Exception:
                    try:
                        existing = api.content.create(
                            container=container,
                            type=item.portal_type,
                            id=target_id,
                            title=getattr(item, "Title", lambda: "")(),
                        )
                        created += 1
                        status = "created"
                    except Exception as exc:
                        details.append(
                            {
                                "source": _rel_path(item),
                                "target": None,
                                "status": "failed",
                                "error": str(exc),
                            }
                        )
                        failed += 1
                        continue
            else:
                status = "updated"
                updated += 1
        else:
            logger.info(
                "[KYRA AI TRANSLATE] using existing translation via PAM %s -> %s status=%s",
                _rel_path(item),
                _rel_path(existing),
                status,
            )

        try:
            blocks_copy = None
            source_title = getattr(item, "Title", lambda: "")()
            source_description = getattr(item, "Description", lambda: "")()
            futures: List[Tuple[str, Any, Any]] = []
            max_workers = max(1, _max_translation_concurrency())
            translated_title_value: Optional[str] = None
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                portal = api.portal.get()
                if hasattr(existing, "setTitle"):
                    futures.append(
                        (
                            "title",
                            executor.submit(
                                lambda txt: (setSite(portal), _translate_text_with_retry(
                                    translator,
                                    txt,
                                    source_lang,
                                target_lang,
                                True,
                                True,
                            ))[1],
                                source_title,
                            ),
                            existing.setTitle,
                        )
                    )
                if hasattr(existing, "setDescription"):
                    futures.append(
                        (
                            "description",
                            executor.submit(
                                lambda txt: (setSite(portal), _translate_text_with_retry(
                                    translator,
                                    txt,
                                source_lang,
                                target_lang,
                                True,
                                True,
                            ))[1],
                                source_description,
                            ),
                            existing.setDescription,
                        )
                    )

                if hasattr(item, "blocks") and hasattr(item, "blocks_layout"):
                    blocks_copy = copy.deepcopy(getattr(item, "blocks", {}))
                    for block in blocks_copy.values():
                        futures.append(
                            (
                                "block",
                                executor.submit(
                                    lambda b: (setSite(portal), _translate_block_dict(
                                        translator,
                                        b,
                                        source_lang,
                                        target_lang,
                                    ))[1],
                                    block,
                                ),
                                None,
                            )
                        )

                for kind, future, setter in futures:
                    try:
                        result = future.result()
                    except Exception as exc:
                        logger.warning("[KYRA AI] translate task failed kind=%s error=%s", kind, exc)
                        continue
                    if kind in ("title", "description") and callable(setter):
                        try:
                            original = source_title if kind == "title" else source_description
                            value_to_set = result if isinstance(result, str) and result.strip() else original
                            setter(value_to_set)
                            if kind == "title":
                                translated_title_value = value_to_set
                        except Exception:
                            logger.debug("[KYRA AI] could not apply %s", kind)

            if blocks_copy is not None:
                existing.blocks = blocks_copy
                existing.blocks_layout = copy.deepcopy(getattr(item, "blocks_layout", {}))
            if translated_title_value:
                try:
                    new_id = idnormalizer.normalize(translated_title_value)
                    current_id = getattr(existing, "getId", lambda: None)()
                    if new_id and current_id and new_id != current_id:
                        api.content.rename(obj=existing, new_id=new_id, safe_id=True)
                except Exception:
                    logger.debug("[KYRA AI] could not rename translation to match translated title")
            # carry over preview image fields when present
            for preview_field in ("preview_image", "preview_image_link"):
                if hasattr(item, preview_field):
                    try:
                        src_val = getattr(item, preview_field, None)
                    except Exception:
                        src_val = None
                    if src_val:
                        try:
                            setattr(existing, preview_field, copy.deepcopy(src_val))
                        except Exception:
                            try:
                                setattr(existing, preview_field, src_val)
                            except Exception:
                                logger.debug(
                                    "[KYRA AI TRANSLATE] could not copy %s for %s",
                                    preview_field,
                                    _rel_path(item),
                                )
            if hasattr(existing, "setLanguage"):
                existing.setLanguage(target_lang)
            existing.reindexObject()
            logger.info(
                "[KYRA AI TRANSLATE] applied %s -> %s status=%s overwrite=%s gateway=%s",
                _rel_path(item),
                _rel_path(existing),
                status,
                overwrite,
                "yes" if gateway_available else "no",
            )
        except Exception as exc:
            status = "failed"
            failed += 1
            details.append(
                {
                    "source": _rel_path(item),
                    "target": _rel_path(existing),
                    "status": status,
                    "error": str(exc),
                }
            )
            continue

        # Link translations in both directions if possible
        try:
            from plone.app.multilingual.interfaces import ITranslationManager

            mgr_source = ITranslationManager(item)
            mgr_source.register_translation(target_lang, existing)
            try:
                mgr_target = ITranslationManager(existing)
                mgr_target.register_translation(source_lang, item)
            except Exception:
                pass
        except Exception:
            pass

        note = (
            "Translated content applied (gateway used)"
            if gateway_available
            else "Copied content (gateway unavailable)"
        )
        details.append(
            {
                "source": _rel_path(item),
                "target": _rel_path(existing),
                "status": status,
                "note": note,
            }
        )

    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "details": details,
        "source_language": source_lang,
        "target_language": target_lang,
        "mode": mode,
    }


def _ensure_blocks_struct(obj):
    blocks = getattr(obj, "blocks", None)
    layout = getattr(obj, "blocks_layout", None)

    if blocks is None:
        blocks = PersistentMapping()
        setattr(obj, "blocks", blocks)
    if layout is None or not isinstance(layout, dict):
        layout = PersistentMapping()
        layout["items"] = PersistentList()
        setattr(obj, "blocks_layout", layout)

    if "items" not in layout or not isinstance(layout.get("items"), list):
        layout["items"] = PersistentList(list(layout.get("items") or []))

    return blocks, layout


def _clone_chat(translator: Chat) -> Chat:
    worker = Chat()
    worker.gateway_url = translator.gateway_url
    worker.token = translator.token
    worker.domain_id = getattr(translator, "domain_id", None)
    return worker


def _translate_text_with_retry(
    translator: Chat,
    text: str,
    source_lang: str,
    target_lang: str,
    use_prompt: bool = True,
    strip_html: bool = True,
) -> str:
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
    # If gateway credentials are missing, return the original text
    if not (translator.gateway_url and translator._get_headers()):
        return text

    # strip HTML before sending (model still instructed to preserve formatting)
    text_for_translation = _html_to_text(text).strip() or text

    if use_prompt:
        prompt_client = Prompts()
        prompt_id = _get_cached_translate_prompt_id()
        logger.info(
            "[KYRA AI] Translate text start | prompt_id=%s gateway=%s",
            prompt_id or "none",
            translator.gateway_url,
        )
        prompt_payload = {
            "name": "Kyra Translate",
            "prompt": (
                "You are a translation engine. Translate the user input into the target language. "
                "The target language is provided inside the input, prefixed by 'TARGET: <lang>'. "
                "Always translate into that target language, preserve meaning and inline formatting/HTML, "
                "do not add explanations, and return only the translated text."
            ),
            "categories": ["Translation"],
            "actionType": "replace",
            "metadata": {"categories": ["Translation"], "action": "replace"},
        }
        def _apply_prompt(pid: str, txt: str, tgt: str) -> Optional[str]:
            try:
                enriched = f"TARGET: {tgt}\n{text_for_translation}"
                resp = prompt_client.apply(pid, {"query": enriched, "input": enriched, "params": {"language": tgt}})
                if isinstance(resp, dict):
                    if resp.get("error"):
                        logger.warning("[KYRA AI] Translate prompt apply error: %s", resp.get("error"))
                        return None
                    for key in ("response", "result", "content", "text", "output"):
                        val = resp.get(key)
                        if isinstance(val, str) and val.strip():
                            logger.info("[KYRA AI] Translation prompt %s len=%s", key, len(val.strip()))
                            return val.strip()
                    logger.warning("[KYRA AI] Translate prompt apply returned no text: %s", resp)
                    return None
            except Exception as exc:
                logger.warning("[KYRA AI] Translate prompt apply failed: %s", exc)
                return None

        translated = None
        if prompt_id:
            translated = _apply_prompt(prompt_id, text_for_translation, target_lang)
            if translated is None:
                prompt_id = None

        if prompt_id is None:
            try:
                created = prompt_client.create(prompt_payload)
                new_id = created.get("id") or created.get("_id")
                if new_id:
                    _set_cached_translate_prompt_id(new_id)
                    translated = _apply_prompt(new_id, text_for_translation, target_lang)
                else:
                    logger.warning("[KYRA AI] Translate prompt create returned no id: %s", created)
            except Exception as exc:
                logger.warning("[KYRA AI] Translate prompt create failed: %s", exc)
                translated = None

        if translated:
            logger.info("[KYRA AI] Translate prompt success len=%s", len(translated))
            cleaned = _strip_basic_html(translated) if strip_html else translated
            if _is_boilerplate_translation(_strip_basic_html(cleaned)) if strip_html else _is_boilerplate_translation(cleaned):
                logger.warning("[KYRA AI] Translation looks like boilerplate, using original text")
                return text_for_translation
            return cleaned

    # Fallback: try chat endpoint (may 404 on some gateways)
    payload = {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a translation engine. "
                    f"Translate the user text from {source_lang or 'auto'} to {target_lang}. "
                    "Preserve meaning and inline formatting/HTML, but do not add explanations. "
                    "Return only the translated text."
                ),
            },
            {"role": "user", "content": text},
        ],
        "context": {
            "mode": "translation",
            "source_language": source_lang or "",
            "target_language": target_lang,
        },
        "params": {"language": target_lang},
    }
    try:
        response = translator.send(payload)
        if isinstance(response, dict):
            if response.get("error"):
                logger.warning(f"[KYRA AI] Translation gateway error: {response.get('error')}")
                return text
            msg = response.get("message")
            if isinstance(msg, dict):
                content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    logger.info("[KYRA AI] Translation message.content len=%s", len(content.strip()))
                    cleaned = _strip_basic_html(content.strip()) if strip_html else content.strip()
                    if _is_boilerplate_translation(_strip_basic_html(cleaned)) if strip_html else _is_boilerplate_translation(cleaned):
                        logger.warning("[KYRA AI] Translation looks like boilerplate, using original text")
                        return text_for_translation
                    return cleaned
            for key in ("result", "response", "content", "text", "output"):
                value = response.get(key)
                if isinstance(value, str) and value.strip():
                    logger.info("[KYRA AI] Translation %s len=%s", key, len(value.strip()))
                    cleaned = _strip_basic_html(value.strip()) if strip_html else value.strip()
                    if _is_boilerplate_translation(_strip_basic_html(cleaned)) if strip_html else _is_boilerplate_translation(cleaned):
                        logger.warning("[KYRA AI] Translation looks like boilerplate, using original text")
                        return text_for_translation
                    return cleaned
            # sometimes nested under "data"
            data = response.get("data")
            if isinstance(data, dict):
                for key in ("content", "text", "output"):
                    val = data.get(key)
                    if isinstance(val, str) and val.strip():
                        logger.info("[KYRA AI] Translation data.%s len=%s", key, len(val.strip()))
                        cleaned = _strip_basic_html(val.strip()) if strip_html else val.strip()
                        if _is_boilerplate_translation(_strip_basic_html(cleaned)) if strip_html else _is_boilerplate_translation(cleaned):
                            logger.warning("[KYRA AI] Translation looks like boilerplate, using original text")
                            return text_for_translation
                        return cleaned
            logger.warning("[KYRA AI] Translation gateway empty response: %s", response)
    except Exception as exc:
        logger.warning("[KYRA AI] Translation failed, returning original text: %s", exc)
        return text
    return text


def _strip_basic_html(value: str) -> str:
    if not isinstance(value, str):
        return ""
    # Remove all HTML tags, keep inner text
    cleaned = re.sub(r"<br\\s*/?>", "\\n", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", "", cleaned)
    return cleaned.strip()


def _html_to_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = re.sub(r"<br\\s*/?>", "\\n", value, flags=re.IGNORECASE)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\\s+", " ", cleaned)
    return cleaned.strip()


def _is_boilerplate_translation(text: str) -> bool:
    if not isinstance(text, str):
        return False
    lowered = text.lower()
    return (
        "tinymce" in lowered
        or "modify the text according to the instruction" in lowered
        or "bitte ändern sie den text gemäß der anweisung" in lowered
    )


SKIP_TRANSLATION_FIELDS = {
    "@type",
    "type",
    "plaintext",
    "url",
    "href",
    "src",
    "target",
    "uid",
    "image_field",
    "scale",
    "size",
    "align",
    "align_text",
    "className",
    "gradient",
    "pattern",
    "columns",
    "rows",
    "value",  # slate handled separately
    "children",  # handled via slate recursion
    "blocks_layout",
    # layout / sizing keys that should never be translated or altered
    "gridCols",
    "gridSize",
    "grid",
}

BLOCK_TEXT_FIELDS = {
    # Default Volto / custom blocks
    "heading": ["heading"],
    "gridBlock": ["title", "headline", "description", "text", "html"],
    "columnsBlock": ["title", "description", "text", "html"],
    "accordion": ["headline", "title", "text", "description", "subtitle", "html"],
    "slider": ["title", "text", "description", "html"],
    "@kitconcept/volto-columns-block": ["title", "description", "text", "html"],
    "@kitconcept/volto-grid-block": ["title", "headline", "description", "text", "body"],
    "@eeacms/volto-columns-block": ["title", "description", "text", "html"],
    "@eeacms/volto-accordion-block": ["title", "text", "description", "subtitle", "html"],
    "@kitconcept/volto-slider-block": ["title", "text", "description", "html"],
    "@kitconcept/volto-carousel-block": ["title", "text", "description", "html"],
    "@kitconcept/volto-heading-block": ["title", "text", "html"],
    "@kitconcept/volto-highlight-block": ["title", "text", "description", "html"],
    "@kitconcept/volto-introduction-block": ["title", "text", "description", "html"],
    "@kitconcept/volto-button-block": ["title", "text"],
    "@kitconcept/volto-light-theme": ["title", "text", "html"],
    "@eeacms/volto-block-divider": ["title", "description"],
}

URL_PATTERN = re.compile(r"^(https?://|/|resolveuid|data:)", re.IGNORECASE)


def _looks_like_url(text: str) -> bool:
    if not isinstance(text, str):
        return False
    return bool(URL_PATTERN.match(text.strip()))


def _is_block_id(value: str) -> bool:
    return bool(re.match(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$", value, re.IGNORECASE))


def _translate_block_strings(
    translator: Chat,
    block: Dict[str, Any],
    source_lang: str,
    target_lang: str,
    parent_key: Optional[str] = None,
):
    for key, value in list(block.items()):
        if key in SKIP_TRANSLATION_FIELDS:
            continue
        if isinstance(value, str):
            if value.strip() and not _looks_like_url(value):
                block[key] = _translate_text(translator, value, source_lang, target_lang)
        elif isinstance(value, dict):
            # If this is a nested block (has @type), translate it as a block
            if value.get("@type"):
                _translate_block_dict(translator, value, source_lang, target_lang)
            else:
                _translate_block_strings(translator, value, source_lang, target_lang, key)
        elif isinstance(value, list):
            _translate_block_list(translator, value, source_lang, target_lang, key)


def _translate_block_list(
    translator: Chat,
    lst: List[Any],
    source_lang: str,
    target_lang: str,
    parent_key: Optional[str] = None,
):
    if parent_key == "items" and all(isinstance(item, str) and _is_block_id(item) for item in lst):
        return
    for idx, item in enumerate(lst):
        if isinstance(item, str):
            if item.strip() and not _looks_like_url(item):
                lst[idx] = _translate_text(translator, item, source_lang, target_lang)
        elif isinstance(item, dict):
            if item.get("@type"):
                _translate_block_dict(translator, item, source_lang, target_lang)
            else:
                _translate_block_strings(translator, item, source_lang, target_lang, parent_key)


def _translate_block_special_fields(
    translator: Chat,
    block: Dict[str, Any],
    source_lang: str,
    target_lang: str,
):
    block_type = block.get("@type", "")
    fields = BLOCK_TEXT_FIELDS.get(block_type, [])
    for key in fields:
        value = block.get(key)
        if isinstance(value, str) and value.strip() and not _looks_like_url(value):
            block[key] = _translate_text(translator, value, source_lang, target_lang)
        elif isinstance(value, dict):
            _translate_block_strings(translator, value, source_lang, target_lang)
        elif isinstance(value, list):
            _translate_block_list(translator, value, source_lang, target_lang, key)


def _translate_block_dict(
    translator: Chat,
    block: Dict[str, Any],
    source_lang: str,
    target_lang: str,
):
    if not isinstance(block, dict):
        return
    btype = block.get("@type")
    if btype in ("text",):
        html = block.get("text") or ""
        block["text"] = _translate_text(translator, html, source_lang, target_lang)
    elif btype in ("slate",):
        value = block.get("value")
        if isinstance(value, list):
            for node in value:
                _translate_slate_node(translator, node, source_lang, target_lang)
    elif btype == "html":
        html = block.get("html") or ""
        block["html"] = _translate_text(translator, html, source_lang, target_lang, strip_html=False)

    _translate_block_strings(translator, block, source_lang, target_lang)
    _translate_block_special_fields(translator, block, source_lang, target_lang)


def _translate_blocks(translator: Chat, blocks: Dict[str, Any], source_lang: str, target_lang: str):
    for block in blocks.values():
        _translate_block_dict(translator, block, source_lang, target_lang)


def _translate_slate_node(translator: Chat, node: Any, source_lang: str, target_lang: str):
    if not isinstance(node, dict):
        return
    if "text" in node and isinstance(node["text"], str):
        node["text"] = _translate_text(translator, node["text"], source_lang, target_lang)
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            _translate_slate_node(translator, child, source_lang, target_lang)


def _detect_text_block_type(blocks: Dict[str, Any]) -> str:
    for block in blocks.values():
        if not isinstance(block, dict):
            continue
        block_type = block.get("@type")
        if block_type in ("slate", "text"):
            return block_type
    return "slate"


def _build_text_block(text: str, block_type: str) -> Dict[str, Any]:
    if block_type == "text":
        return {"@type": "text", "text": f"<p>{text}</p>"}
    return {
        "@type": "slate",
        "plaintext": text,
        "value": [
            {
                "type": "p",
                "children": [{"text": text}],
            }
        ],
    }


def _build_heading_block(text: str, level: int) -> Dict[str, Any]:
    level = min(max(int(level), 1), 6)
    return {
        "@type": "slate",
        "plaintext": text,
        "value": [
            {
                "type": f"h{level}",
                "children": [{"text": text}],
            }
        ],
    }


def _build_list_block(items: List[str], ordered: bool) -> Dict[str, Any]:
    list_type = "ol" if ordered else "ul"
    children = []
    for item in items:
        if not isinstance(item, str) or not item.strip():
            continue
        children.append(
            {
                "type": "li",
                "children": [{"text": item.strip()}],
            }
        )
    return {
        "@type": "slate",
        "plaintext": " ".join(items),
        "value": [
            {
                "type": list_type,
                "children": children,
            }
        ],
    }


def _build_quote_block(text: str, citation: Optional[str]) -> Dict[str, Any]:
    value = [
        {
            "type": "blockquote",
            "children": [{"text": text}],
        }
    ]
    if isinstance(citation, str) and citation.strip():
        value.append(
            {
                "type": "p",
                "children": [{"text": f"— {citation.strip()}"}],
            }
        )
    return {
        "@type": "slate",
        "plaintext": text,
        "value": value,
    }


def _build_image_block(
    url: str,
    alt: Optional[str],
    caption: Optional[str],
    image_field: Optional[str],
    scale: Optional[str],
) -> Dict[str, Any]:
    block: Dict[str, Any] = {
        "@type": "image",
        "url": url,
        "image_field": image_field or "image",
    }
    if isinstance(scale, str) and scale.strip():
        block["scale"] = scale.strip()
        block["size"] = scale.strip()
    if isinstance(alt, str) and alt.strip():
        block["alt"] = alt.strip()
    if isinstance(caption, str) and caption.strip():
        block["caption"] = caption.strip()
    return block


def _build_grid_block(columns: int, heading: Optional[str] = None, body: Optional[str] = None) -> Dict[str, Any]:
    cols = max(1, min(MAX_GRID_COLUMNS, int(columns) if isinstance(columns, int) else 3))
    ids = [str(uuid.uuid4()) for _ in range(cols)]

    # Build a slate block per column, honoring allowedBlocks (slate)
    blocks: Dict[str, Any] = {}
    for bid in ids:
        nodes = []
        if heading:
            nodes.append({"type": "h2", "children": [{"text": heading}]})
        if body:
            nodes.append({"type": "p", "children": [{"text": body}]})
        if not nodes:
            nodes = [{"type": "p", "children": [{"text": ""}]}]
        plaintext = " ".join([heading or "", body or ""]).strip()
        blocks[bid] = {
            "@type": "slate",
            "plaintext": plaintext,
            "value": nodes,
        }

    return {
        "@type": "gridBlock",
        "columns": cols,
        "blocks": blocks,
        "blocks_layout": {"items": ids},
    }


def _insert_block(obj, block: Dict[str, Any]) -> None:
    blocks, layout = _ensure_blocks_struct(obj)
    block_id = str(uuid.uuid4())
    blocks[block_id] = block
    items = layout.get("items")
    if isinstance(items, list):
        items.append(block_id)
    else:
        layout["items"] = PersistentList(list(items or []) + [block_id])


def _insert_text_block(obj, text: str) -> None:
    blocks, _layout = _ensure_blocks_struct(obj)
    block_type = _detect_text_block_type(blocks)
    _insert_block(obj, _build_text_block(text, block_type))


def _insert_heading_block(obj, text: str, level: int) -> None:
    _insert_block(obj, _build_heading_block(text, level))


def _insert_list_block(obj, items: List[str], ordered: bool) -> None:
    _insert_block(obj, _build_list_block(items, ordered))


def _insert_quote_block(obj, text: str, citation: Optional[str]) -> None:
    _insert_block(obj, _build_quote_block(text, citation))


def _insert_image_block(
    obj,
    url: str,
    alt: Optional[str],
    caption: Optional[str],
    image_field: Optional[str],
    scale: Optional[str],
) -> None:
    _insert_block(obj, _build_image_block(url, alt, caption, image_field, scale))


def _store_plan(obj, plan_id: str, actions: List[Dict[str, Any]], user_id: str) -> None:
    annotations = IAnnotations(obj)
    plans = annotations.get(PLAN_STORAGE_KEY)
    if plans is None:
        plans = {}
        annotations[PLAN_STORAGE_KEY] = plans
    plans[plan_id] = {
        "actions": actions,
        "user_id": user_id,
        "created": datetime.utcnow().isoformat(),
        "page_uid": getattr(obj, "UID", lambda: None)(),
    }


def _load_plan(obj, plan_id: str) -> Optional[Dict[str, Any]]:
    annotations = IAnnotations(obj)
    plans = annotations.get(PLAN_STORAGE_KEY) or {}
    return plans.get(plan_id)


@implementer(IPublishTraverse)
class AIActionsService(ServiceBase):
    """POST /++api++/@ai-actions/plan and /++api++/@ai-actions/apply"""

    def __init__(self, context, request):
        super().__init__(context, request)
        self.subpath = None

    def publishTraverse(self, request, name):
        if self.subpath is None:
            self.subpath = name
            return self
        raise BadRequest("Too many path segments")

    def reply(self):
        if self.subpath == "plan":
            return self._handle_plan()
        if self.subpath == "apply":
            return self._handle_apply()
        raise BadRequest("Unknown action endpoint")

    def _handle_plan(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        goal = data.get("goal") or ""
        translate_opts = data.get("translation") if isinstance(data.get("translation"), dict) else None
        if not isinstance(goal, str):
            goal = ""
        if not goal.strip() and not translate_opts:
            raise BadRequest("Missing goal")

        target = _resolve_target(self.context, data)
        _ensure_editor(target)

        actions = _derive_actions(goal, target, self.kyra, translate_opts=translate_opts)
        preview = _preview_from_actions(actions)

        plan_id = str(uuid.uuid4())
        user_id = api.user.get_current().getId()
        _store_plan(target, plan_id, actions, user_id)

        return {
            "plan_id": plan_id,
            "actions": actions,
            "preview": preview,
            "translation_report": _build_translation_stub(actions),
        }

    def _handle_apply(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        target = _resolve_target(self.context, data)
        _ensure_editor(target)

        inline_actions = data.get("actions")
        plan_id = data.get("plan_id")
        actions = inline_actions

        if plan_id:
            plan = _load_plan(target, plan_id)
            if not plan:
                raise BadRequest("Unknown plan_id")
            plan_actions = plan.get("actions") or []
            if plan_actions:
                actions = plan_actions

        if not isinstance(actions, list) or not actions:
            raise BadRequest("Missing actions to apply")

        changed = _apply_actions(target, actions)
        log_ai_action(target, actions, plan_id=plan_id)

        report = getattr(target, "_v_last_translation_report", None)
        if hasattr(target, "__delattr__"):
            try:
                delattr(target, "_v_last_translation_report")
            except Exception:
                pass

        return {
            "result": "ok",
            "changed": changed,
            "reload": True,
            "report": report,
        }
