import copy
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

from interaktiv.kyra import logger
from interaktiv.kyra.api import Chat
from interaktiv.kyra.services.ai_tag_mappings import _get_tag_mappings
from interaktiv.kyra.services.ai_translation_links import (
    _resolve_internal_link_translation,
    _translate_links_in_blocks,
)
from interaktiv.kyra.services.ai_translation_text import (
    _apply_glossary_substitution,
    _get_glossary_map,
    _is_block_id,
    _looks_like_non_text,
    _looks_like_url,
    _translate_text,
    _translate_text_with_retry,
)
from interaktiv.kyra.services.deepl_translation import (
    deepl_translate_text_batch,
)
from persistent.list import PersistentList
from persistent.mapping import PersistentMapping
from plone import api
from plone.i18n.normalizer import idnormalizer
from plone.namedfile.file import NamedBlobFile, NamedBlobImage
from zExceptions import BadRequest
from zope.component.hooks import setSite

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
    texts_to_translate: List[str] = []
    callbacks: List[Any] = []
    glossary = _get_glossary_map(source_lang, target_lang)

    def collect_text(text: str, write_back):
        """Register a text for batch translation."""
        if not isinstance(text, str) or not text.strip():
            return
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

        special_fields = BLOCK_TEXT_FIELDS.get(btype, [])
        for key in special_fields:
            val = block.get(key)
            if isinstance(val, str) and val.strip() and not _looks_like_url(val) and not _looks_like_non_text(val):
                def make_field_cb(b, k):
                    def cb(translated):
                        b[k] = translated
                    return cb
                collect_text(val, make_field_cb(block, key))

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

    for block in blocks.values():
        collect_from_block(block)

    if not texts_to_translate:
        return

    logger.info("[KYRA AI] Batch translating %d texts (%s->%s)", len(texts_to_translate), source_lang, target_lang)
    results = deepl_translate_text_batch(texts_to_translate, source_lang, target_lang)

    for i, result in enumerate(results):
        if result is not None and result != texts_to_translate[i]:
            callbacks[i](result)

    logger.info("[KYRA AI] Batch translation complete: %d texts", len(texts_to_translate))


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


def _apply_translation(obj, payload: Dict[str, Any]) -> Dict[str, Any]:
    from interaktiv.kyra.services.ai_actions import _max_translation_concurrency

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
            from plone.app.multilingual.interfaces import ITranslationManager

            manager = ITranslationManager(item)
        except Exception:
            manager = None

        if manager is not None:
            try:
                translations = manager.get_translations() or {}
                existing = translations.get(target_lang)
                # Always update LRFs -- their translation is created at
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
                        existing_blocks = getattr(existing, "blocks", {}) or {}
                        existing_block_ids = set(existing_blocks.keys())
                        source_block_ids = set(source_blocks.keys())
                        new_block_ids = source_block_ids - existing_block_ids

                        blocks_copy = copy.deepcopy(dict(existing_blocks))
                        blocks_to_translate = {}
                        for block_id in new_block_ids:
                            new_block = copy.deepcopy(source_blocks[block_id])
                            blocks_copy[block_id] = new_block
                            blocks_to_translate[block_id] = new_block
                        for removed_id in (existing_block_ids - source_block_ids):
                            blocks_copy.pop(removed_id, None)
                        logger.info(
                            "[KYRA AI TRANSLATE] incremental: %d existing kept, %d new to translate, %d removed",
                            len(existing_block_ids & source_block_ids),
                            len(new_block_ids),
                            len(existing_block_ids - source_block_ids),
                        )
                        if blocks_to_translate:
                            _translate_blocks(translator, blocks_to_translate, source_lang, target_lang)
                    else:
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
                "image",
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
                            # NamedBlobImage/NamedBlobFile contain ZODB Blobs --
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
                            images_view.scale(pf, width=w, height=h, pre=False)
                            logger.info(
                                "[KYRA AI TRANSLATE] generated image scale for %s on %s",
                                pf, _rel_path(existing),
                            )
            except Exception as exc:
                logger.debug("[KYRA AI TRANSLATE] scale generation: %s", exc)
            _META_COPY_FIELDS = (
                "portal_footer_newsletter",
                "portal_footer_directions",
                "portal_footer_contact_mail",
                "start",
                "end",
                "whole_day",
                "open_end",
                "award_date_year",
                "award_date_month",
                "award_type",
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

            _META_LINK_FIELDS = (
                "website_link",
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
