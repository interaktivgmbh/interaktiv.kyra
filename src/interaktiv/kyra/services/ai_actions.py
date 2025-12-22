import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from AccessControl import Unauthorized
from interaktiv.kyra.services.audit import log_ai_action
from interaktiv.kyra.services.base import ServiceBase
from persistent.list import PersistentList
from persistent.mapping import PersistentMapping
from plone import api
from plone.base.interfaces import IPloneSiteRoot
from plone.restapi.deserializer import json_body
from zExceptions import BadRequest
from zope.annotation.interfaces import IAnnotations
from zope.interface import implementer
from zope.publisher.interfaces import IPublishTraverse

PLAN_STORAGE_KEY = "interaktiv.kyra.ai_actions_plans"
ALLOWLIST = {
    "update_title",
    "update_description",
    "update_language",
    "insert_text_block",
    "insert_heading_block",
    "insert_list_block",
    "insert_quote_block",
    "insert_image_block",
}

PLAN_PROMPT_ID = "kyra-actions-plan"
PLAN_PROMPT_CACHE_KEY = "interaktiv.kyra.ai_actions_plan_prompt_id_v3"

UUID_RE = re.compile(r"^[0-9a-fA-F-]{32,36}$")
RESOLVEUID_RE = re.compile(r"resolveuid/([0-9a-fA-F-]{32,36})")
IMAGES_SCALE_RE = re.compile(r"@@images/([^/]+)/([^/?#]+)")


def _extract_value_after(label: str, text: str) -> Optional[str]:
    lower = text.lower()
    idx = lower.find(label)
    if idx == -1:
        return None
    value = text[idx + len(label) :].strip()
    for sep in (";", "\n"):
        if sep in value:
            value = value.split(sep)[0].strip()
    return value or None


def _derive_actions(goal: str, target=None, kyra=None) -> List[Dict[str, Any]]:
    if kyra is not None:
        actions = _derive_actions_from_gateway(goal, target, kyra)
        if actions:
            return actions

    actions: List[Dict[str, Any]] = []
    title = _extract_value_after("title:", goal)
    description = _extract_value_after("description:", goal)
    language = _extract_value_after("language:", goal)

    if title:
        actions.append({"type": "update_title", "payload": {"title": title}})
    if description:
        actions.append(
            {"type": "update_description", "payload": {"description": description}}
        )
    if language:
        actions.append({"type": "update_language", "payload": {"language": language}})

    if not actions:
        actions.extend(_derive_actions_from_patterns(goal))

    return actions


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


def _canonical_action_type(action_type: str) -> str:
    action_type = (action_type or "").strip()
    mapping = {
        "add_text_block": "insert_text_block",
        "append_text_block": "insert_text_block",
        "insert_block": "insert_text_block",
        "add_block": "insert_text_block",
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
    }
    return mapping.get(action_type, action_type)


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
            return json.loads(text[start : end + 1])
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


def _derive_actions_from_patterns(goal: str) -> List[Dict[str, Any]]:
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

    return actions


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

    return {
        "summary": ", ".join(summaries) if summaries else "No changes proposed",
        "diff": "\n".join(diffs),
        "human_steps": summaries,
    }


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
            url = url[len(portal_url) :]
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

    obj.reindexObject()
    return changed


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
        if not isinstance(goal, str) or not goal.strip():
            raise BadRequest("Missing goal")

        target = _resolve_target(self.context, data)
        _ensure_editor(target)

        actions = _derive_actions(goal, target, self.kyra)
        preview = _preview_from_actions(actions)

        plan_id = str(uuid.uuid4())
        user_id = api.user.get_current().getId()
        _store_plan(target, plan_id, actions, user_id)

        return {
            "plan_id": plan_id,
            "actions": actions,
            "preview": preview,
        }

    def _handle_apply(self):
        data = json_body(self.request) or {}
        if not isinstance(data, dict):
            raise BadRequest("JSON object expected")

        target = _resolve_target(self.context, data)
        _ensure_editor(target)

        actions = data.get("actions")
        plan_id = data.get("plan_id")

        if plan_id:
            plan = _load_plan(target, plan_id)
            if not plan:
                raise BadRequest("Unknown plan_id")
            actions = plan.get("actions") or []

        if not isinstance(actions, list) or not actions:
            raise BadRequest("Missing actions to apply")

        changed = _apply_actions(target, actions)
        log_ai_action(target, actions, plan_id=plan_id)

        return {
            "result": "ok",
            "changed": changed,
            "reload": True,
        }
