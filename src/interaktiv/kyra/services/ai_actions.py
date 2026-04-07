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
from interaktiv.kyra.services.deepl_translation import deepl_translate_text, deepl_translate_text_batch, get_glossary_entries
from interaktiv.kyra.api import Chat
from interaktiv.kyra.services.audit import log_ai_action
from interaktiv.kyra.services.base import ServiceBase
from passlib.exc import ExpectedTypeError
from plone.i18n.normalizer import idnormalizer
from plone.namedfile.file import NamedBlobImage, NamedBlobFile
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
TRANSLATION_MAX_CONCURRENCY_DEFAULT = 3
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
            try:
                children = getattr(current, "objectValues", lambda: [])()
                for child in children:
                    stack.append(child)
            except (RecursionError, Exception) as exc:
                logger.warning("Skipping children of %s: %s", getattr(current, "getId", lambda: "?")(), exc)

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
                # Always update LRFs — their translation is created at
                # site setup and would otherwise always be skipped.
                is_lrf = getattr(item, "portal_type", "") == "LRF"
                if existing and not overwrite and not is_lrf:
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
            # Document / general
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

                # Translate RichText fields (stored as RichTextValue objects)
                _RICHTEXT_FIELDS = ("detailed_description",)
                for rt_field in _RICHTEXT_FIELDS:
                    rt_val = getattr(item, rt_field, None)
                    if rt_val is None or not hasattr(existing, rt_field):
                        continue
                    raw_html = getattr(rt_val, "raw", None) or ""
                    if not isinstance(raw_html, str) or not raw_html.strip():
                        continue
                    futures.append(
                        (
                            "richtext",
                            executor.submit(
                                lambda txt, fn=rt_field, mt=getattr(rt_val, "mimeType", "text/html"): (
                                    setSite(portal),
                                    (fn, _translate_text_with_retry(
                                        translator, txt, source_lang, target_lang, True, False,
                                    ), mt),
                                )[1],
                                raw_html,
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
                        blocks_to_translate = {}
                        for block_id in new_block_ids:
                            new_block = copy.deepcopy(source_blocks[block_id])
                            blocks_copy[block_id] = new_block
                            blocks_to_translate[block_id] = new_block
                        # Remove blocks deleted from source
                        for removed_id in (existing_block_ids - source_block_ids):
                            blocks_copy.pop(removed_id, None)
                        logger.info(
                            "[KYRA AI TRANSLATE] incremental: %d existing kept, %d new to translate, %d removed",
                            len(existing_block_ids & source_block_ids),
                            len(new_block_ids),
                            len(existing_block_ids - source_block_ids),
                        )
                        # Batch translate new blocks
                        if blocks_to_translate:
                            _translate_blocks(translator, blocks_to_translate, source_lang, target_lang)
                    else:
                        # Full mode: translate all blocks using batch API
                        blocks_copy = copy.deepcopy(source_blocks)
                        _translate_blocks(translator, blocks_copy, source_lang, target_lang)

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
                    elif kind == "richtext" and isinstance(result, tuple) and len(result) == 3:
                        fn, translated_html, mime = result
                        if isinstance(translated_html, str) and translated_html.strip():
                            try:
                                from plone.app.textfield.value import RichTextValue
                                setattr(
                                    existing,
                                    fn,
                                    RichTextValue(translated_html, mime, "text/x-html-safe"),
                                )
                                logger.info("[KYRA AI TRANSLATE] richtext field %s translated", fn)
                            except Exception:
                                logger.debug("[KYRA AI] could not set richtext field %s", fn)

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
            _IMAGE_FIELDS = (
                "preview_image",
                "preview_image_link",
                "image",  # Project logo
                *(f"external_funding_provider_{i}_logo" for i in range(1, 4)),
                *(f"project_partner_{i}_logo" for i in range(1, 11)),
            )
            for preview_field in _IMAGE_FIELDS:
                if hasattr(item, preview_field):
                    try:
                        src_val = getattr(item, preview_field, None)
                    except Exception:
                        src_val = None
                    if src_val:
                        try:
                            # NamedBlobImage/NamedBlobFile contain ZODB Blobs —
                            # copy.deepcopy produces objects whose blob data is
                            # inaccessible.  Create a fresh instance instead.
                            if isinstance(src_val, NamedBlobImage):
                                new_val = NamedBlobImage(
                                    data=src_val.data,
                                    contentType=src_val.contentType,
                                    filename=src_val.filename,
                                )
                                setattr(existing, preview_field, new_val)
                            elif isinstance(src_val, NamedBlobFile):
                                new_val = NamedBlobFile(
                                    data=src_val.data,
                                    contentType=src_val.contentType,
                                    filename=src_val.filename,
                                )
                                setattr(existing, preview_field, new_val)
                            else:
                                # RelationValue or other — direct assignment
                                setattr(existing, preview_field, src_val)
                        except Exception:
                            try:
                                setattr(existing, preview_field, src_val)
                            except Exception:
                                logger.debug(
                                    "[KYRA AI TRANSLATE] could not copy %s for %s",
                                    preview_field,
                                    _rel_path(item),
                                )
            # Force image scale generation so @@images URLs work immediately
            try:
                from zope.component import getMultiAdapter
                images_view = getMultiAdapter(
                    (existing, existing.REQUEST), name="images"
                )
                for pf in ("preview_image",):
                    field_val = getattr(existing, pf, None)
                    if field_val is not None and hasattr(field_val, "getImageSize"):
                        w, h = field_val.getImageSize()
                        if w and h:
                            # Calling scale() with pre=False generates the
                            # actual scale data and stores it in annotations.
                            images_view.scale(pf, width=w, height=h, pre=False)
                            logger.info(
                                "[KYRA AI TRANSLATE] generated image scale for %s on %s",
                                pf, _rel_path(existing),
                            )
            except Exception as exc:
                logger.debug("[KYRA AI TRANSLATE] scale generation: %s", exc)
            # Copy non-translatable metadata (links, emails, dates, choices) as-is
            _META_COPY_FIELDS = (
                # LRF portal footer
                "portal_footer_newsletter",
                "portal_footer_directions",
                "portal_footer_contact_mail",
                # Event
                "start",
                "end",
                "whole_day",
                "open_end",
                # Award
                "award_date_year",
                "award_date_month",
                "award_type",
                # Project — dates, budget, choices
                "project_type",
                "project_start_month",
                "project_start_year",
                "project_end_month",
                "project_end_year",
                "project_budget",
                "involved_institutes",
            )
            for copy_field in _META_COPY_FIELDS:
                src_val = getattr(item, copy_field, None)
                if src_val and hasattr(existing, copy_field):
                    try:
                        setattr(existing, copy_field, src_val)
                    except Exception:
                        logger.debug(
                            "[KYRA AI TRANSLATE] could not copy field %s", copy_field
                        )

            # Rewrite internal link fields to point to translated content
            _META_LINK_FIELDS = (
                # Award
                "website_link",
                # Project
                "link_further_information",
                *(f"external_funding_provider_{i}_link" for i in range(1, 4)),
                *(f"project_partner_{i}_link" for i in range(1, 11)),
            )
            for link_field in _META_LINK_FIELDS:
                src_val = getattr(item, link_field, None)
                if not src_val or not isinstance(src_val, str) or not hasattr(existing, link_field):
                    continue
                translated_url = _resolve_internal_link_translation(src_val, target_lang)
                try:
                    setattr(existing, link_field, translated_url or src_val)
                    if translated_url:
                        logger.info(
                            "[KYRA AI TRANSLATE] link field %s: %s -> %s",
                            link_field, src_val, translated_url,
                        )
                except Exception:
                    logger.debug(
                        "[KYRA AI TRANSLATE] could not set link field %s", link_field
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
    "bg_color",
    "bgColor",
    "bg_image",
    "height",
    "width",
    "maxWidth",
    "columns_count",
    "count",
    "researchGroup",
    "openLinkInNewTab",
    "linkHref",
    "preview_image",
    "tpreview_image",
    "show_block_count",
    "show_arrows",
    "right_arrows",
    "b_size",
    "batch_size",
    "querystring",
    "query",
    "sort_on",
    "sort_order",
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
    "highlight": ["title", "text", "description", "html", "buttonText"],
    "@kitconcept/volto-introduction-block": ["title", "text", "description", "html"],
    "@kitconcept/volto-button-block": ["title", "text"],
    "@kitconcept/volto-light-theme": ["title", "text", "html"],
    "@eeacms/volto-block-divider": ["title", "description"],
    "headline": ["title"],
    "tabBlock": ["headline"],
    "sliderNew": [],
    "quote": ["author", "additional_information"],
    "carousel": ["headline"],
    "form": ["title", "description", "cancel_label", "send_message", "default_subject"],
    "introduction": ["heading"],
    "icon": ["heading"],
    "image": ["alt", "description", "rights"],
    "__button": ["title", "text"],
    "__grid": ["title", "headline", "description", "text"],
    "buttonBlock": ["title", "text"],
}

BLOCK_NESTED_ARRAYS = {
    "tabBlock": [("columns", ["title"])],
    "sliderNew": [("slides", ["head_title", "title", "description"])],
    "carousel": [("columns", ["title", "description"])],
}

BLOCKS_WITH_SLATE_VALUE = {"quote", "textPillWithStyle", "tabBlock", "highlight"}

BLOCK_SLATE_SUBOBJECTS = {
    "introduction": ["about", "topics"],
}

BLOCK_DYNAMIC_SLATE_FIELDS = {
    "tabBlock": ("text", "columns"),
}

BLOCK_RICHTEXT_HTML_FIELDS = {
    "icon": ["description"],
    "form": ["mail_header"],
}

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
    special_handled = set(BLOCK_TEXT_FIELDS.get(block_type, []))
    dynamic_def = BLOCK_DYNAMIC_SLATE_FIELDS.get(block_type)
    dynamic_prefix = dynamic_def[0] if dynamic_def else None

    for key, value in list(block.items()):
        if key in SKIP_TRANSLATION_FIELDS:
            continue
        if key in special_handled:
            continue
        if key in richtext_handled or key in slate_sub_handled:
            continue
        if dynamic_prefix and key.startswith(f"{dynamic_prefix}-"):
            continue
        if isinstance(value, str):
            if value.strip() and not _looks_like_url(value) and not _looks_like_non_text(value):
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
            if item.strip() and not _looks_like_url(item) and not _looks_like_non_text(item):
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
    elif btype == "slateTable":
        table = block.get("table")
        if isinstance(table, dict):
            rows = table.get("rows")
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    cells = row.get("cells")
                    if not isinstance(cells, list):
                        continue
                    for cell in cells:
                        if isinstance(cell, dict):
                            cell_value = cell.get("value")
                            if isinstance(cell_value, list):
                                for node in cell_value:
                                    _translate_slate_node(translator, node, source_lang, target_lang)

    slate_sub_fields = BLOCK_SLATE_SUBOBJECTS.get(btype, [])
    for field in slate_sub_fields:
        sub = block.get(field)
        if isinstance(sub, dict):
            _translate_slate_value(translator, sub, source_lang, target_lang)
        elif isinstance(sub, list):
            for node in sub:
                _translate_slate_node(translator, node, source_lang, target_lang)

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

    # Translate text fields inside image subobjects (skipped by generic recursion
    # because "image" is in SKIP_TRANSLATION_FIELDS)
    _IMAGE_TEXT_SUBFIELDS = ("alt", "title", "description", "rights", "caption")
    for img_key in ("image", "preview_image", "tpreview_image"):
        img_obj = block.get(img_key)
        if isinstance(img_obj, dict):
            for sf in _IMAGE_TEXT_SUBFIELDS:
                val = img_obj.get(sf)
                if isinstance(val, str) and val.strip() and not _looks_like_url(val):
                    img_obj[sf] = _translate_text(translator, val, source_lang, target_lang)

    _translate_block_strings(translator, block, source_lang, target_lang)
    _translate_block_special_fields(translator, block, source_lang, target_lang)


def _translate_blocks(translator: Chat, blocks: Dict[str, Any], source_lang: str, target_lang: str):
    """Translate all blocks using batched DeepL API calls for performance."""
    # Phase 1: Collect all translatable texts with write-back callbacks
    texts_to_translate: List[str] = []
    callbacks: List[Any] = []  # List of (write_back_fn,) tuples
    glossary = _get_glossary_map(source_lang, target_lang)

    def collect_text(text: str, write_back):
        """Register a text for batch translation."""
        if not isinstance(text, str) or not text.strip():
            return
        # Apply glossary substitution before batching
        substituted = _apply_glossary_substitution(text, glossary)
        texts_to_translate.append(substituted)
        callbacks.append(write_back)

    def collect_from_slate_node(node):
        if not isinstance(node, dict):
            return
        if "text" in node and isinstance(node["text"], str):
            original = node["text"]
            if original.strip():
                leading = original[: len(original) - len(original.lstrip())]
                trailing = original[len(original.rstrip()) :]
                def make_cb(n, l, t):
                    def cb(translated):
                        n["text"] = l + translated.strip() + t
                    return cb
                collect_text(original.strip(), make_cb(node, leading, trailing))
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                collect_from_slate_node(child)

    def collect_from_block(block):
        if not isinstance(block, dict):
            return
        btype = block.get("@type")

        # Slate/text blocks
        if btype in ("text",):
            html = block.get("text") or ""
            if html.strip():
                def cb(translated):
                    block["text"] = translated
                collect_text(html, cb)
        elif btype in ("slate",) or btype in BLOCKS_WITH_SLATE_VALUE:
            value = block.get("value")
            if isinstance(value, list):
                for node in value:
                    collect_from_slate_node(node)
        elif btype == "html":
            html = block.get("html") or ""
            if html.strip():
                def cb(translated):
                    block["html"] = translated
                collect_text(html, cb)
        elif btype == "slateTable":
            table = block.get("table")
            if isinstance(table, dict):
                rows = table.get("rows")
                if isinstance(rows, list):
                    for row in rows:
                        if not isinstance(row, dict):
                            continue
                        cells = row.get("cells")
                        if not isinstance(cells, list):
                            continue
                        for cell in cells:
                            if isinstance(cell, dict):
                                cell_value = cell.get("value")
                                if isinstance(cell_value, list):
                                    for node in cell_value:
                                        collect_from_slate_node(node)

        # Slate sub-objects (e.g. introduction.about, introduction.topics)
        slate_sub_fields = BLOCK_SLATE_SUBOBJECTS.get(btype, [])
        for field in slate_sub_fields:
            sub = block.get(field)
            if isinstance(sub, dict) and sub.get("value") and isinstance(sub["value"], list):
                for node in sub["value"]:
                    collect_from_slate_node(node)
            elif isinstance(sub, list):
                for item in sub:
                    if isinstance(item, dict) and item.get("value") and isinstance(item["value"], list):
                        for node in item["value"]:
                            collect_from_slate_node(node)

        # Image subfields (alt, title, description)
        for img_key in ("image", "preview_image", "tpreview_image"):
            img_obj = block.get(img_key)
            if isinstance(img_obj, dict):
                for sf in ("alt", "title", "description", "rights", "caption"):
                    val = img_obj.get(sf)
                    if isinstance(val, str) and val.strip() and not _looks_like_url(val):
                        def make_img_cb(obj, key):
                            def cb(translated):
                                obj[key] = translated
                            return cb
                        collect_text(val, make_img_cb(img_obj, sf))

        # String fields from BLOCK_TEXT_FIELDS
        special_fields = BLOCK_TEXT_FIELDS.get(btype, [])
        for key in special_fields:
            val = block.get(key)
            if isinstance(val, str) and val.strip() and not _looks_like_url(val) and not _looks_like_non_text(val):
                def make_field_cb(b, k):
                    def cb(translated):
                        b[k] = translated
                    return cb
                collect_text(val, make_field_cb(block, key))

        # Generic string fields not handled above
        richtext_handled = set(BLOCK_RICHTEXT_HTML_FIELDS.get(btype, []))
        slate_sub_handled = set(BLOCK_SLATE_SUBOBJECTS.get(btype, []))
        special_handled = set(special_fields)
        dynamic_def = BLOCK_DYNAMIC_SLATE_FIELDS.get(btype)
        dynamic_prefix = dynamic_def[0] if dynamic_def else None

        for key, value in list(block.items()):
            if key in SKIP_TRANSLATION_FIELDS or key in special_handled or key in richtext_handled or key in slate_sub_handled:
                continue
            if dynamic_prefix and key.startswith(f"{dynamic_prefix}-"):
                continue
            if key in ("image", "preview_image", "tpreview_image"):
                continue
            if isinstance(value, str):
                if value.strip() and not _looks_like_url(value) and not _looks_like_non_text(value):
                    def make_generic_cb(b, k):
                        def cb(translated):
                            b[k] = translated
                        return cb
                    collect_text(value, make_generic_cb(block, key))
            elif isinstance(value, dict):
                if value.get("@type"):
                    collect_from_block(value)
            elif isinstance(value, list):
                for idx, item in enumerate(value):
                    if isinstance(item, str) and item.strip() and not _looks_like_url(item) and not _looks_like_non_text(item):
                        if not (key == "items" and all(isinstance(x, str) and _is_block_id(x) for x in value)):
                            def make_list_cb(lst, i):
                                def cb(translated):
                                    lst[i] = translated
                                return cb
                            collect_text(item, make_list_cb(value, idx))
                    elif isinstance(item, dict):
                        if item.get("@type"):
                            collect_from_block(item)

        # Nested containers (columnsBlock, tabs, etc.)
        if block.get("data", {}).get("blocks"):
            for sub_block in block["data"]["blocks"].values():
                collect_from_block(sub_block)
        if block.get("blocks") and btype not in ("data",):
            for sub_block in block.get("blocks", {}).values():
                if isinstance(sub_block, dict):
                    collect_from_block(sub_block)
        if block.get("columns"):
            for col in block["columns"]:
                if isinstance(col, dict):
                    for sub_block in col.get("blocks", {}).values():
                        collect_from_block(sub_block)
        if block.get("tabs"):
            for tab in block["tabs"]:
                if isinstance(tab, dict):
                    for sub_block in tab.get("blocks", {}).values():
                        collect_from_block(sub_block)

    # Collect from all blocks
    for block in blocks.values():
        collect_from_block(block)

    if not texts_to_translate:
        return

    # Phase 2: Batch translate
    logger.info("[KYRA AI] Batch translating %d texts (%s->%s)", len(texts_to_translate), source_lang, target_lang)
    results = deepl_translate_text_batch(texts_to_translate, source_lang, target_lang)

    # Phase 3: Write back results
    for i, result in enumerate(results):
        if result is not None and result != texts_to_translate[i]:
            callbacks[i](result)

    logger.info("[KYRA AI] Batch translation complete: %d texts", len(texts_to_translate))


def _resolve_internal_link_translation(path: str, target_lang: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        portal_id = portal.getId()  # e.g. "Plone"
        lookup = path
        # Strip known URL prefixes (backend, frontend, production) to get the path
        if lookup.startswith("http"):
            if lookup.startswith(portal_url):
                lookup = lookup[len(portal_url):]
            else:
                # Strip any http(s)://host(:port) prefix (e.g. frontend URL)
                from urllib.parse import urlparse
                parsed = urlparse(lookup)
                lookup = parsed.path or ""
        # Strip common API/proxy prefixes and portal id from path
        for prefix in (f"/{portal_id}/", "/api/", "/++api++/"):
            if lookup.startswith(prefix):
                lookup = lookup[len(prefix) - 1:]  # keep leading /
                break
        uid = None
        if "resolveuid/" in lookup:
            uid = lookup.split("resolveuid/")[-1].split("/")[0].strip()
        elif re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", lookup.strip()):
            # Bare UUID without resolveuid/ prefix
            uid = lookup.strip()
        obj = None
        logger.info("[KYRA AI LINK] resolving: original=%s lookup=%s uid=%s portal_url=%s", path, lookup, uid, portal_url)
        if uid:
            obj = api.content.get(UID=uid)
        elif lookup.startswith("/"):
            clean = lookup.lstrip("/")
            # Try unrestricted traversal first (api.content.get uses restrictedTraverse)
            try:
                obj = portal.unrestrictedTraverse(clean, None)
            except Exception:
                obj = None
            # Fallback: catalog search by path
            if obj is None:
                try:
                    catalog = api.portal.get_tool("portal_catalog")
                    physical = f"/{portal_id}{lookup}"
                    brains = catalog.unrestrictedSearchResults(path={"query": physical, "depth": 0})
                    if brains:
                        obj = brains[0].getObject()
                except Exception:
                    pass
        if obj is None:
            logger.warning("[KYRA AI LINK] could not resolve object for path=%s lookup=%s uid=%s", path, lookup, uid)
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
        # If the original was a full URL (not a path), return a full URL
        # with the same scheme/host prefix so the field format is preserved.
        if path.startswith("http") and not path.startswith(portal_url):
            from urllib.parse import urlparse
            original_parsed = urlparse(path)
            prefix = f"{original_parsed.scheme}://{original_parsed.netloc}"
            result = prefix + trans_path
            logger.info("[KYRA AI LINK] resolved %s -> %s", path, result)
            return result
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


def _strip_images_suffix(path: str) -> Tuple[str, str]:
    """Split a path into (content_path, @@images/... suffix)."""
    if "/@@images/" in path:
        parts = path.split("/@@images/", 1)
        return parts[0], "/@@images/" + parts[1]
    if path.endswith("/@@images"):
        return path[: -len("/@@images")], "/@@images"
    return path, ""


def _rewrite_block_image_urls(block: Dict[str, Any], target_lang: str):
    """Rewrite image/URL references in blocks to point to translated content."""
    # Rewrite url if present
    url = block.get("url")
    if isinstance(url, str) and url.strip():
        content_path, images_suffix = _strip_images_suffix(url)
        translated_path = _resolve_internal_link_translation(content_path, target_lang)
        if translated_path:
            new_url = translated_path + images_suffix
            block["url"] = new_url
            logger.info("[KYRA AI IMAGE] rewrote block url: %s -> %s", url, new_url)
    # Rewrite @id if present
    at_id = block.get("@id")
    if isinstance(at_id, str) and at_id.strip():
        id_path, id_suffix = _strip_images_suffix(at_id)
        translated_id = _resolve_internal_link_translation(id_path, target_lang)
        if translated_id:
            block["@id"] = translated_id + id_suffix
    # Rewrite href if present
    href = block.get("href")
    if isinstance(href, str) and href.strip():
        href_path, href_suffix = _strip_images_suffix(href)
        translated_href = _resolve_internal_link_translation(href_path, target_lang)
        if translated_href:
            block["href"] = translated_href + href_suffix


def _rewrite_urls_recursive(obj: Any, target_lang: str):
    """Recursively walk blocks/dicts/lists and rewrite image URLs."""
    if isinstance(obj, dict):
        # If it looks like a block, rewrite its image URLs
        if obj.get("url") or obj.get("@id") or obj.get("href"):
            _rewrite_block_image_urls(obj, target_lang)
        # Recurse into nested blocks (e.g. grid columns have a "blocks" dict)
        nested_blocks = obj.get("blocks")
        if isinstance(nested_blocks, dict):
            for sub_block in nested_blocks.values():
                _rewrite_urls_recursive(sub_block, target_lang)
        # Recurse into other dict values
        for key, value in obj.items():
            if key in ("blocks",):
                continue  # already handled
            if isinstance(value, (dict, list)):
                _rewrite_urls_recursive(value, target_lang)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_urls_recursive(item, target_lang)


def _translate_links_in_blocks(blocks: Dict[str, Any], target_lang: str):
    for block in blocks.values():
        if not isinstance(block, dict):
            continue
        # Rewrite image/content URLs in all blocks recursively
        _rewrite_urls_recursive(block, target_lang)
        # Rewrite slate links
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
        # Rewrite links in slateTable cells
        if block.get("@type") == "slateTable":
            table = block.get("table")
            if isinstance(table, dict):
                for row in (table.get("rows") or []):
                    if isinstance(row, dict):
                        for cell in (row.get("cells") or []):
                            if isinstance(cell, dict):
                                _translate_links_in_value(cell.get("value"), target_lang)


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
            if plan:
                plan_actions = plan.get("actions") or []
                if plan_actions:
                    actions = plan_actions
            else:
                logger.warning("Plan %s not found on target, falling back to inline actions", plan_id)

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
