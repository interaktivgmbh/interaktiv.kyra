import copy
import json
import os
import random
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from AccessControl import Unauthorized
from interaktiv.kyra import logger
from interaktiv.kyra.services.ai_tag_mappings import _get_tag_mappings
from interaktiv.kyra.services.deepl_translation import deepl_translate_text, get_glossary_entries
from interaktiv.kyra.api import Chat
from interaktiv.kyra.services.audit import log_ai_action
from interaktiv.kyra.services.base import ServiceBase
from plone.i18n.normalizer import idnormalizer
from persistent.list import PersistentList
from persistent.mapping import PersistentMapping
from plone import api
from plone.base.interfaces import IPloneSiteRoot
from plone.restapi.deserializer import json_body
from plone.restapi.services import Service
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

ALLOWLIST = {"translate_content"}


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


def _derive_actions(translate_opts: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    target_lang = translate_opts.get("target_language") if translate_opts else None
    mode = translate_opts.get("mode") if translate_opts else None
    overwrite = bool(translate_opts.get("overwrite")) if translate_opts else False
    incremental = bool(translate_opts.get("incremental")) if translate_opts else False

    payload = {
        "target_language": target_lang or "en",
        "mode": mode or "single",
        "overwrite": overwrite,
    }
    if incremental:
        payload["incremental"] = True

    return [
        {
            "type": "translate_content",
            "payload": payload,
        }
    ]


def _preview_from_actions(actions: List[Dict[str, Any]]) -> Dict[str, Any]:
    summaries = []
    diffs = []
    for action in actions:
        action_type = action.get("type")
        payload = action.get("payload") or {}
        if action_type == "translate_content":
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

        if action_type == "translate_content":
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
    incremental = bool(payload.get("incremental"))
    translator = Chat()
    gateway_available = bool(translator.gateway_url and translator._get_headers())
    logger.info(
        "[KYRA AI TRANSLATE] start target=%s mode=%s overwrite=%s incremental=%s gateway=%s",
        target_language,
        mode,
        overwrite,
        incremental,
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
                    manager.add_translation(target_lang)
                    existing = manager.get_translation(target_lang)
                    if existing:
                        status = "created"
                        created += 1
                    else:
                        logger.warning(
                            "[KYRA AI TRANSLATE] manager.add_translation did not create translation for %s -> %s",
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
                if existing is not None and manager is not None:
                    try:
                        from plone.app.multilingual.interfaces import ILanguage
                        ILanguage(existing).set_language(target_lang)
                        manager.register_translation(target_lang, existing)
                        logger.info(
                            "[KYRA AI TRANSLATE] registered fallback copy as PAM translation %s -> %s",
                            _rel_path(item),
                            _rel_path(existing),
                        )
                    except Exception as exc:
                        logger.warning(
                            "[KYRA AI TRANSLATE] failed to register PAM translation: %s", exc
                        )
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

        _META_TEXT_FIELDS = (
            "preview_caption",
            "image_caption",
            "subtitle",
            "head_title",
            "footer_header",
            "footer_text",
            "short_header_text",
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

                for field_name in _META_TEXT_FIELDS:
                    source_val = getattr(item, field_name, None)
                    if not source_val or not isinstance(source_val, str) or not source_val.strip():
                        continue
                    if not hasattr(existing, field_name):
                        continue
                    futures.append(
                        (
                            "meta",
                            executor.submit(
                                lambda txt, fn=field_name: (
                                    setSite(portal),
                                    (fn, _translate_text_with_retry(
                                        translator, txt, source_lang, target_lang, True, True,
                                    )),
                                )[1],
                                source_val,
                            ),
                            None,
                        )
                    )

                if hasattr(item, "blocks") and hasattr(item, "blocks_layout"):
                    source_blocks = getattr(item, "blocks", {})
                    if incremental and existing is not None:
                        # Incremental mode: only translate NEW blocks
                        existing_blocks = getattr(existing, "blocks", {}) or {}
                        existing_block_ids = set(existing_blocks.keys())
                        source_block_ids = set(source_blocks.keys())
                        new_block_ids = source_block_ids - existing_block_ids

                        # Merge: start with existing translated blocks, add new ones
                        blocks_copy = copy.deepcopy(dict(existing_blocks))
                        for block_id in new_block_ids:
                            new_block = copy.deepcopy(source_blocks[block_id])
                            blocks_copy[block_id] = new_block
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
                                        new_block,
                                    ),
                                    None,
                                )
                            )
                        # Remove blocks deleted from source
                        for removed_id in (existing_block_ids - source_block_ids):
                            blocks_copy.pop(removed_id, None)
                        logger.info(
                            "[KYRA AI TRANSLATE] incremental: %d existing kept, %d new to translate, %d removed",
                            len(existing_block_ids & source_block_ids),
                            len(new_block_ids),
                            len(existing_block_ids - source_block_ids),
                        )
                    else:
                        # Full mode: translate all blocks
                        blocks_copy = copy.deepcopy(source_blocks)
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
                    elif kind == "meta" and isinstance(result, tuple) and len(result) == 2:
                        fn, translated = result
                        if isinstance(translated, str) and translated.strip():
                            try:
                                setattr(existing, fn, translated)
                                logger.info("[KYRA AI TRANSLATE] metadata field %s translated", fn)
                            except Exception:
                                logger.debug("[KYRA AI] could not set metadata field %s", fn)

            if blocks_copy is not None:
                _translate_links_in_blocks(blocks_copy, target_lang)
                existing.blocks = blocks_copy
                existing.blocks_layout = copy.deepcopy(getattr(item, "blocks_layout", {}))
                _ensure_blocks_struct(existing)
            if translated_title_value:
                try:
                    new_id = idnormalizer.normalize(translated_title_value)
                    current_id = getattr(existing, "getId", lambda: None)()
                    if new_id and current_id and new_id != current_id:
                        api.content.rename(obj=existing, new_id=new_id, safe_id=True)
                except Exception:
                    logger.debug("[KYRA AI] could not rename translation to match translated title")
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
            # Map tags/subjects using mapping table
            source_subjects = item.Subject() if callable(getattr(item, "Subject", None)) else ()
            if source_subjects and hasattr(existing, "setSubject"):
                tag_mappings = _get_tag_mappings()
                mapped_tags = []
                for tag in source_subjects:
                    lang_map = tag_mappings.get(tag, {})
                    translated_tag = lang_map.get(target_lang)
                    if translated_tag:
                        mapped_tags.append(translated_tag)
                existing.setSubject(mapped_tags)
                logger.info(
                    "[KYRA AI TRANSLATE] tags mapped: %d source -> %d translated",
                    len(source_subjects),
                    len(mapped_tags),
                )

            # Copy topic vocabulary terms (controlled vocabulary — no translation needed)
            try:
                source_topics = getattr(item, "topic", None)
                logger.info(
                    "[KYRA AI TRANSLATE] source topics: %s (type=%s, has_topic=%s)",
                    source_topics,
                    type(source_topics).__name__,
                    hasattr(existing, "topic"),
                )
                if source_topics is not None and len(source_topics) > 0:
                    existing.topic = set(source_topics)
                    existing._p_changed = True
                    logger.info(
                        "[KYRA AI TRANSLATE] copied %d topics to translation: %s",
                        len(source_topics),
                        source_topics,
                    )
            except Exception as exc:
                logger.warning(
                    "[KYRA AI TRANSLATE] could not copy topics: %s", exc
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

    Replaces longest terms first so that e.g. "Forschungszentrum Jülich"
    is matched before "Forschungszentrum".  Case-insensitive matching.
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

    # Pre-process: substitute glossary terms before translation
    glossary = _get_glossary_map(source_lang, target_lang)
    text = _apply_glossary_substitution(text, glossary)

    # Translate via DeepL
    try:
        deepl_result = deepl_translate_text(text, source_lang, target_lang)
        if deepl_result is not None:
            logger.info("[KYRA AI] DeepL translated %d chars (%s->%s)", len(deepl_result), source_lang, target_lang)
            return deepl_result
    except Exception as exc:
        logger.warning("[KYRA AI] DeepL translation failed: %s", exc)

    logger.warning("[KYRA AI] DeepL unavailable, returning original text (%s->%s)", source_lang, target_lang)
    return text


SKIP_TRANSLATION_FIELDS = {
    "@type",
    "@id",
    "type",
    "plaintext",
    "url",
    "href",
    "src",
    "target",
    "uid",
    "UID",
    "id",
    "image",
    "image_field",
    "scale",
    "size",
    "align",
    "align_text",
    "className",
    "gradient",
    "pattern",
    "rows",
    "value",
    "children",
    "blocks_layout",
    "gridCols",
    "gridSize",
    "grid",
    "styles",
    "style",
    "variation",
    "template",
    "theme",
    "widget",
    "field",
    "mode",
    "layout",
    "position",
    "color",
    "backgroundColor",
    "textAlign",
    "display",
    "hidden",
    "required",
    "fixed",
    "reversed",
    "inverted",
}

BLOCK_TEXT_FIELDS = {
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
    "headline": ["title"],
    "tabBlock": ["headline"],
    "sliderNew": [],
    "quote": ["author", "additional_information"],
    "parallaxBlock": ["text"],
    "highlightTeaser": ["title", "description", "linkTitle"],
    "highlightTeaserParallax": ["title", "description", "linkTitle"],
    "highlightTeaserWithoutButton": ["title", "description"],
    "teaserWithLink": ["title", "description", "button"],
    "introduction": ["heading"],
    "aktuelles": ["headline", "title", "head_title", "description", "ttitle", "thead_title", "tdescription"],
    "institutslider": ["title"],
    "members": ["title"],
    "memberList": ["headline"],
    "icon": ["heading"],
    "socialMedia": ["headline", "description", "ydescription"],
}

BLOCK_NESTED_ARRAYS = {
    "tabBlock": [("columns", ["title"])],
    "sliderNew": [("slides", ["head_title", "title", "description"])],
    "institutslider": [("slides", ["title", "description"])],
    "teaserWithLink": [("links", ["title"])],
}

BLOCKS_WITH_SLATE_VALUE = {"quote", "textPillWithStyle"}

BLOCK_SLATE_SUBOBJECTS = {
    "introduction": ["about", "topics"],
}

BLOCK_DYNAMIC_SLATE_FIELDS = {
    "tabBlock": ("text", "columns"),
}

BLOCK_RICHTEXT_HTML_FIELDS = {
    "icon": ["description"],
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
    block_type = block.get("@type", "")
    richtext_handled = set(BLOCK_RICHTEXT_HTML_FIELDS.get(block_type, []))
    slate_sub_handled = set(BLOCK_SLATE_SUBOBJECTS.get(block_type, []))
    dynamic_def = BLOCK_DYNAMIC_SLATE_FIELDS.get(block_type)
    dynamic_prefix = dynamic_def[0] if dynamic_def else None

    for key, value in list(block.items()):
        if key in SKIP_TRANSLATION_FIELDS:
            continue
        if key in richtext_handled or key in slate_sub_handled:
            continue
        if dynamic_prefix and key.startswith(f"{dynamic_prefix}-"):
            continue
        if isinstance(value, str):
            if value.strip() and not _looks_like_url(value):
                block[key] = _translate_text(translator, value, source_lang, target_lang)
        elif isinstance(value, dict):
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

    nested_defs = BLOCK_NESTED_ARRAYS.get(block_type, [])
    for array_field, subfields in nested_defs:
        items = block.get(array_field)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for sf in subfields:
                val = item.get(sf)
                if isinstance(val, str) and val.strip() and not _looks_like_url(val):
                    item[sf] = _translate_text(translator, val, source_lang, target_lang)


def _translate_slate_value(translator: Chat, block: Dict[str, Any], source_lang: str, target_lang: str):
    value = block.get("value")
    if isinstance(value, list):
        for node in value:
            _translate_slate_node(translator, node, source_lang, target_lang)


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
    elif btype in ("slate",) or btype in BLOCKS_WITH_SLATE_VALUE:
        _translate_slate_value(translator, block, source_lang, target_lang)
    elif btype == "html":
        html = block.get("html") or ""
        block["html"] = _translate_text(translator, html, source_lang, target_lang, strip_html=False)

    slate_sub_fields = BLOCK_SLATE_SUBOBJECTS.get(btype, [])
    for field in slate_sub_fields:
        sub = block.get(field)
        if isinstance(sub, dict):
            _translate_slate_value(translator, sub, source_lang, target_lang)

    dynamic_def = BLOCK_DYNAMIC_SLATE_FIELDS.get(btype)
    if dynamic_def:
        prefix, array_field = dynamic_def
        items = block.get(array_field)
        if isinstance(items, list):
            for idx in range(len(items)):
                key = f"{prefix}-{idx}"
                sub = block.get(key)
                if isinstance(sub, dict):
                    _translate_slate_value(translator, sub, source_lang, target_lang)

    richtext_fields = BLOCK_RICHTEXT_HTML_FIELDS.get(btype, [])
    for field in richtext_fields:
        obj = block.get(field)
        if isinstance(obj, dict) and isinstance(obj.get("data"), str) and obj["data"].strip():
            obj["data"] = _translate_text(translator, obj["data"], source_lang, target_lang, strip_html=False)

    _translate_block_strings(translator, block, source_lang, target_lang)
    _translate_block_special_fields(translator, block, source_lang, target_lang)


def _translate_blocks(translator: Chat, blocks: Dict[str, Any], source_lang: str, target_lang: str):
    for block in blocks.values():
        _translate_block_dict(translator, block, source_lang, target_lang)


def _resolve_internal_link_translation(path: str, target_lang: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        lookup = path
        if lookup.startswith("http") and lookup.startswith(portal_url):
            lookup = lookup[len(portal_url):]
        uid = None
        if "resolveuid/" in lookup:
            uid = lookup.split("resolveuid/")[-1].split("/")[0].strip()
        obj = None
        if uid:
            obj = api.content.get(UID=uid)
        elif lookup.startswith("/"):
            obj = api.content.get(path=lookup.lstrip("/"))
        if obj is None:
            logger.info("[KYRA AI LINK] could not resolve object for path=%s uid=%s", path, uid)
            return None
        from plone.app.multilingual.interfaces import ITranslationManager
        manager = ITranslationManager(obj)
        translated = manager.get_translation(target_lang)
        if translated is None:
            logger.info("[KYRA AI LINK] no translation found for %s -> %s", path, target_lang)
            return None
        trans_url = translated.absolute_url()
        trans_path = trans_url[len(portal_url):] if trans_url.startswith(portal_url) else trans_url
        if uid:
            trans_uid = getattr(translated, "UID", lambda: None)()
            if trans_uid:
                logger.info("[KYRA AI LINK] resolved %s -> resolveuid/%s", path, trans_uid)
                return f"../resolveuid/{trans_uid}"
        logger.info("[KYRA AI LINK] resolved %s -> %s", path, trans_path)
        return trans_path
    except Exception as exc:
        logger.warning("[KYRA AI LINK] error resolving %s: %s", path, exc)
        return None


def _translate_slate_link(node: Dict[str, Any], target_lang: str):
    node_type = node.get("type")
    logger.info("[KYRA AI LINK] processing slate node type=%s data=%s", node_type, {k: v for k, v in node.items() if k != "children"})
    if node_type == "link":
        data = node.get("data")
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str):
                translated_path = _resolve_internal_link_translation(url, target_lang)
                if translated_path:
                    data["url"] = translated_path
    elif node_type == "a":
        data = node.get("data") or {}
        link = data.get("link") or {}
        internal = link.get("internal") or {}
        internal_links = internal.get("internal_link")
        if isinstance(internal_links, list) and internal_links:
            item = internal_links[0]
            if isinstance(item, dict):
                path = item.get("@id")
                if isinstance(path, str):
                    translated_path = _resolve_internal_link_translation(path, target_lang)
                    if translated_path:
                        item["@id"] = translated_path
        url = node.get("url")
        if isinstance(url, str):
            translated_path = _resolve_internal_link_translation(url, target_lang)
            if translated_path:
                node["url"] = translated_path


def _translate_links_in_blocks(blocks: Dict[str, Any], target_lang: str):
    for block in blocks.values():
        if not isinstance(block, dict):
            continue
        _translate_links_in_value(block.get("value"), target_lang)
        for field in BLOCK_SLATE_SUBOBJECTS.get(block.get("@type", ""), []):
            sub = block.get(field)
            if isinstance(sub, dict):
                _translate_links_in_value(sub.get("value"), target_lang)
        dynamic_def = BLOCK_DYNAMIC_SLATE_FIELDS.get(block.get("@type", ""))
        if dynamic_def:
            prefix, array_field = dynamic_def
            items = block.get(array_field)
            if isinstance(items, list):
                for idx in range(len(items)):
                    sub = block.get(f"{prefix}-{idx}")
                    if isinstance(sub, dict):
                        _translate_links_in_value(sub.get("value"), target_lang)


def _translate_links_in_value(value: Any, target_lang: str):
    if not isinstance(value, list):
        return
    for node in value:
        _translate_links_in_node(node, target_lang)


def _translate_links_in_node(node: Any, target_lang: str):
    if not isinstance(node, dict):
        return
    if node.get("type") in ("link", "a"):
        _translate_slate_link(node, target_lang)
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            _translate_links_in_node(child, target_lang)


def _translate_slate_node(translator: Chat, node: Any, source_lang: str, target_lang: str):
    if not isinstance(node, dict):
        return
    if "text" in node and isinstance(node["text"], str):
        original = node["text"]
        if original.strip():
            leading = original[: len(original) - len(original.lstrip())]
            trailing = original[len(original.rstrip()) :]
            translated = _translate_text(translator, original.strip(), source_lang, target_lang)
            node["text"] = leading + translated.strip() + trailing
    children = node.get("children")
    if isinstance(children, list):
        for child in children:
            _translate_slate_node(translator, child, source_lang, target_lang)


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

        actions = _derive_actions(translate_opts=translate_opts)
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


_SYNC_TOLERANCE_SECONDS = 5


class AITranslationStatusService(Service):

    def reply(self):
        target = self.context
        if IPloneSiteRoot.providedBy(target):
            return {"translations": [], "outdated_count": 0}

        try:
            from plone.app.multilingual.interfaces import ITranslationManager
        except ImportError:
            return {"translations": [], "outdated_count": 0}

        try:
            manager = ITranslationManager(target)
        except TypeError:
            return {"translations": [], "outdated_count": 0}

        translations = manager.get_translations() or {}
        source_lang = target.Language() or "de"
        source_modified = target.modified()
        source_ts = source_modified.timeTime()

        result = []
        for lang, trans_obj in translations.items():
            if lang == source_lang:
                continue
            try:
                trans_modified = trans_obj.modified()
                trans_ts = trans_modified.timeTime()
                is_outdated = (source_ts - trans_ts) > _SYNC_TOLERANCE_SECONDS
                logger.info(
                    "[KYRA AI STATUS] lang=%s trans_modified=%s diff=%.1fs is_outdated=%s",
                    lang,
                    trans_modified.ISO8601(),
                    source_ts - trans_ts,
                    is_outdated,
                )
                result.append({
                    "language": lang,
                    "title": trans_obj.Title() or "",
                    "url": trans_obj.absolute_url(),
                    "modified": trans_modified.ISO8601(),
                    "is_outdated": is_outdated,
                })
            except Exception:
                continue

        return {
            "source_language": source_lang,
            "source_modified": source_modified.ISO8601(),
            "translations": result,
            "outdated_count": sum(1 for t in result if t["is_outdated"]),
        }
