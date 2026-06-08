import re
from typing import Any, Dict, Optional, Tuple

from interaktiv.kyra import logger
from plone import api
from plone.base.interfaces import IPloneSiteRoot


BLOCK_SLATE_SUBOBJECTS = {
    "introduction": ["about", "topics"],
}

BLOCK_DYNAMIC_SLATE_FIELDS = {
    "tabBlock": ("text", "columns"),
}


def _resolve_internal_link_translation(path: str, target_lang: str) -> Optional[str]:
    if not isinstance(path, str) or not path.strip():
        return None
    try:
        portal = api.portal.get()
        portal_url = portal.absolute_url()
        portal_id = portal.getId()
        lookup = path
        if lookup.startswith("http"):
            if lookup.startswith(portal_url):
                lookup = lookup[len(portal_url):]
            else:
                from urllib.parse import urlparse
                parsed = urlparse(lookup)
                lookup = parsed.path or ""
        for prefix in (f"/{portal_id}/", "/api/", "/++api++/"):
            if lookup.startswith(prefix):
                lookup = lookup[len(prefix) - 1:]
                break
        uid = None
        if "resolveuid/" in lookup:
            uid = lookup.split("resolveuid/")[-1].split("/")[0].strip()
        elif re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", lookup.strip()):
            uid = lookup.strip()
        obj = None
        logger.info("[KYRA AI LINK] resolving: original=%s lookup=%s uid=%s portal_url=%s", path, lookup, uid, portal_url)
        if uid:
            obj = api.content.get(UID=uid)
        elif lookup.startswith("/"):
            clean = lookup.lstrip("/")
            try:
                obj = portal.unrestrictedTraverse(clean, None)
            except Exception:
                obj = None
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
    url = block.get("url")
    if isinstance(url, str) and url.strip():
        content_path, images_suffix = _strip_images_suffix(url)
        translated_path = _resolve_internal_link_translation(content_path, target_lang)
        if translated_path:
            new_url = translated_path + images_suffix
            block["url"] = new_url
            logger.info("[KYRA AI IMAGE] rewrote block url: %s -> %s", url, new_url)
    at_id = block.get("@id")
    if isinstance(at_id, str) and at_id.strip():
        id_path, id_suffix = _strip_images_suffix(at_id)
        translated_id = _resolve_internal_link_translation(id_path, target_lang)
        if translated_id:
            block["@id"] = translated_id + id_suffix
    href = block.get("href")
    if isinstance(href, str) and href.strip():
        href_path, href_suffix = _strip_images_suffix(href)
        translated_href = _resolve_internal_link_translation(href_path, target_lang)
        if translated_href:
            block["href"] = translated_href + href_suffix


def _rewrite_urls_recursive(obj: Any, target_lang: str):
    """Recursively walk blocks/dicts/lists and rewrite image URLs."""
    if isinstance(obj, dict):
        if obj.get("url") or obj.get("@id") or obj.get("href"):
            _rewrite_block_image_urls(obj, target_lang)
        nested_blocks = obj.get("blocks")
        if isinstance(nested_blocks, dict):
            for sub_block in nested_blocks.values():
                _rewrite_urls_recursive(sub_block, target_lang)
        for key, value in obj.items():
            if key in ("blocks",):
                continue
            if isinstance(value, (dict, list)):
                _rewrite_urls_recursive(value, target_lang)
    elif isinstance(obj, list):
        for item in obj:
            _rewrite_urls_recursive(item, target_lang)


def _translate_links_in_blocks(blocks: Dict[str, Any], target_lang: str):
    for block in blocks.values():
        if not isinstance(block, dict):
            continue
        _rewrite_urls_recursive(block, target_lang)
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
