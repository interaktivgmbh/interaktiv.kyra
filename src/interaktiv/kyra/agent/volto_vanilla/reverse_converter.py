"""Convert IR Layout back to Volto page JSON."""

from __future__ import annotations

import random
import string
from uuid import uuid4 as _uuid4
from collections.abc import Callable
from html.parser import HTMLParser
from typing import Any

from .schema import (
    AccordionArrowPosition,
    FieldVisibilityOperator,
    HighlightBackgroundColor,
    ImageSize,
    Layout,
    ListingDisplayVariant,
    Metadata,
    QuoteDisplayVariant,
    SliderAutoplayTransition,
    TabsDisplayVariant,
)

# ---------------------------------------------------------------------------
# Highlight description color mapping (IR name → Volto CSS class)
# ---------------------------------------------------------------------------

_IR_COLOR_TO_VOLTO: dict[str, str] = {
    HighlightBackgroundColor.LIGHT_BLUE.value: "highlight-custom-color-1",
    HighlightBackgroundColor.DARK_TEAL.value: "highlight-custom-color-2",
    HighlightBackgroundColor.YELLOW.value: "highlight-custom-color-3",
    HighlightBackgroundColor.LIGHT_GREEN.value: "highlight-custom-color-4",
    HighlightBackgroundColor.OLIVE.value: "highlight-custom-color-5",
}

_IR_IMAGE_SIZE_TO_VOLTO: dict[str, str] = {
    ImageSize.SMALL.value: "s",
    ImageSize.MEDIUM.value: "m",
    ImageSize.LARGE.value: "l",
}

# ---------------------------------------------------------------------------
# Diff context — threaded through all converters when in diff mode
# ---------------------------------------------------------------------------


class _DiffCtx:
    """Holds indexes for diff-based conversion."""

    def __init__(
        self,
        volto_index: dict[str, dict[str, Any]],
        ir_index: dict[str, dict[str, Any]],
    ) -> None:
        self.volto_index = volto_index
        self.ir_index = ir_index


def _block_unchanged(new_ir: dict[str, Any], old_ir: dict[str, Any]) -> bool:
    """Check if a block's own data is unchanged (ignoring path, name, children)."""
    return (
        new_ir["type"] == old_ir["type"]
        and new_ir["attributes"] == old_ir["attributes"]
    )


def _subtree_unchanged(block: dict[str, Any], ctx: _DiffCtx) -> bool:
    """Check if a block and all its descendants are unchanged."""
    uid = block["id"]
    old = ctx.ir_index.get(uid)
    if old is None or not _block_unchanged(block, old):
        return False
    new_child_ids = [c["id"] for c in block.get("children", [])]
    old_child_ids = old.get("child_ids", [])
    if new_child_ids != old_child_ids:
        return False
    return all(_subtree_unchanged(c, ctx) for c in block.get("children", []))


def _try_preserve_container(
    block: dict[str, Any], ctx: _DiffCtx | None
) -> dict[str, Any] | None:
    """Return original Volto block if container and all descendants are unchanged."""
    if ctx is None:
        return None
    uid = block["id"]
    old_ir = ctx.ir_index.get(uid)
    if old_ir is None or not _block_unchanged(block, old_ir):
        return None
    new_child_ids = [c["id"] for c in block.get("children", [])]
    old_child_ids = old_ir.get("child_ids", [])
    if new_child_ids != old_child_ids:
        return None
    if not all(_subtree_unchanged(c, ctx) for c in block.get("children", [])):
        return None
    return ctx.volto_index.get(uid)


def _try_preserve_child(
    child: dict[str, Any], ctx: _DiffCtx | None
) -> dict[str, Any] | None:
    """Return original Volto block for an unchanged child."""
    if ctx is None:
        return None
    old = ctx.ir_index.get(child["id"])
    if old is not None and _block_unchanged(child, old):
        return ctx.volto_index.get(child["id"])
    return None


def _merge_original_block(
    block: dict[str, Any], ctx: _DiffCtx | None, updated: dict[str, Any]
) -> dict[str, Any]:
    """Preserve original Volto implementation details not represented in IR."""
    if ctx is None:
        return updated
    original = ctx.volto_index.get(block["id"])
    if original is None:
        return updated
    return {**original, **updated}


# ---------------------------------------------------------------------------
# Index builders
# ---------------------------------------------------------------------------


def _build_volto_index(volto: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Flatten all Volto blocks (including nested) into uid → block dict."""
    index: dict[str, dict[str, Any]] = {}
    blocks = volto.get("blocks", {})

    for uid, block in blocks.items():
        index[uid] = block
        _index_volto_children(block, index)

    return index


def _index_volto_children(
    block: dict[str, Any], index: dict[str, dict[str, Any]]
) -> None:
    block_type = block.get("@type", "")

    if block_type in ("columnsBlock", "accordion"):
        data = block.get("data", {})
        for container_uid, container_data in data.get("blocks", {}).items():
            index[container_uid] = container_data
            for inner_uid, inner_block in container_data.get("blocks", {}).items():
                index[inner_uid] = inner_block
                _index_volto_children(inner_block, index)

    elif block_type == "slider":
        for slide in block.get("slides", []):
            slide_id = slide.get("@id", "")
            if slide_id:
                index[slide_id] = slide

    elif block_type == "carousel":
        for item in block.get("columns", []):
            item_id = item.get("@id", "")
            if item_id:
                index[item_id] = item


def _build_ir_index(layout: Layout) -> dict[str, dict[str, Any]]:
    """Flatten IR layout into id → block dict (attributes only, no children)."""
    index: dict[str, dict[str, Any]] = {}
    for block in layout.root:
        _index_ir_block(block.model_dump(), index)
    return index


def _index_ir_block(block: dict[str, Any], index: dict[str, dict[str, Any]]) -> None:
    uid = block["id"]
    children = block.get("children", [])
    entry: dict[str, Any] = {
        "type": block["type"],
        "id": uid,
        "attributes": block["attributes"],
    }
    if children:
        entry["child_ids"] = [c["id"] for c in children]
    index[uid] = entry
    for child in children:
        _index_ir_block(child, index)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_METADATA_KEYS = set(Metadata.model_fields)


def layout_to_volto(
    layout: Layout,
    metadata: Metadata,
    *,
    original_volto: dict[str, Any] | None = None,
    original_layout: Layout | None = None,
) -> dict[str, Any]:
    """Convert IR Layout + Metadata back to Volto page structure.

    If original_volto and original_layout are provided, performs diff-based
    conversion: unchanged blocks are preserved verbatim from the original Volto
    JSON (keeping slate keys, extra metadata, styles, etc.).

    Metadata fields are written from the Metadata object (not copied through
    from original_volto), so agent changes are reflected. Empty metadata fields
    are omitted from the output.
    """
    ctx: _DiffCtx | None = None
    if original_volto is not None and original_layout is not None:
        ctx = _DiffCtx(
            volto_index=_build_volto_index(original_volto),
            ir_index=_build_ir_index(original_layout),
        )

    blocks: dict[str, Any] = {}
    items: list[str] = []

    for block in layout.root:
        block_dict = block.model_dump()
        uid = block_dict["id"]
        volto = _reverse_block(block_dict, ctx)
        blocks[uid] = volto
        items.append(uid)

    result: dict[str, Any]
    if original_volto is not None:
        result = {
            k: v
            for k, v in original_volto.items()
            if k not in ("blocks", "blocks_layout") and k not in _METADATA_KEYS
        }
    else:
        result = {}

    # Write metadata from IR (not from original_volto).
    # Empty fields are omitted per agreed protocol.
    if metadata.link:
        result["link"] = metadata.link
    if metadata.title:
        result["title"] = metadata.title
    if metadata.description:
        result["description"] = metadata.description
    if metadata.preview_image:
        result["preview_image"] = metadata.preview_image
    if metadata.subjects:
        result["subjects"] = metadata.subjects
    if metadata.start is not None:
        result["start"] = metadata.start.isoformat()
    if metadata.end is not None:
        result["end"] = metadata.end.isoformat()

    result["blocks"] = blocks
    result["blocks_layout"] = {"items": items}
    return result


# ---------------------------------------------------------------------------
# Block dispatch
# ---------------------------------------------------------------------------


def _reverse_block(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    uid = block["id"]
    ir_type = block["type"]

    # Diff mode: check if this leaf block is unchanged
    if ctx is not None and ir_type not in _CONTAINER_TYPES:
        old_ir = ctx.ir_index.get(uid)
        if old_ir is not None and _block_unchanged(block, old_ir):
            original = ctx.volto_index.get(uid)
            if original is not None:
                return original

    converter = _REVERSE_CONVERTERS.get(ir_type)
    if converter is None:
        msg = f"Unsupported IR block type: {ir_type!r}"
        raise ValueError(msg)
    return converter(block, ctx)


_CONTAINER_TYPES: set[str] = {
    "slider",
    "carousel",
    "columns",
    "accordion",
    "listing",
    "statistic",
    "form",
    "tabs",
}


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _build_href(url: str) -> list[dict[str, str]]:
    if url:
        return [{"@id": url}]
    return []


def _styles_from_attrs(
    attrs: dict[str, Any], mapping: dict[str, str]
) -> dict[str, Any]:
    styles: dict[str, Any] = {}
    for ir_key, volto_key in mapping.items():
        value = attrs.get(ir_key)
        if value and value != "default":
            styles[volto_key] = value
    return styles


_LISTING_DISPLAY_VARIANT_TO_VOLTO: dict[str, str] = {
    ListingDisplayVariant.STANDARD.value: "default",
    ListingDisplayVariant.SUMMARY_LIST.value: "summary",
    ListingDisplayVariant.NEWS_LIST.value: "news",
    ListingDisplayVariant.TWO_COLUMN_GRID.value: "grid2",
    ListingDisplayVariant.TEXT_CARD_GRID.value: "textCards",
    ListingDisplayVariant.VISUAL_CARD_GRID.value: "visualGrid",
    ListingDisplayVariant.EVENT_LIST.value: "events",
    ListingDisplayVariant.HORIZONTAL_LIST.value: "horizontalList",
}


def _listing_display_variant_to_volto(value: Any) -> str:
    if isinstance(value, ListingDisplayVariant):
        value = value.value
    if not isinstance(value, str):
        return "default"
    return _LISTING_DISPLAY_VARIANT_TO_VOLTO.get(value, "default")


_QUOTE_DISPLAY_VARIANT_TO_VOLTO: dict[str, str] = {
    QuoteDisplayVariant.STANDARD.value: "default",
    QuoteDisplayVariant.TESTIMONIAL.value: "testimonial",
}


def _quote_display_variant_to_volto(value: Any) -> str:
    if isinstance(value, QuoteDisplayVariant):
        value = value.value
    if not isinstance(value, str):
        return "default"
    return _QUOTE_DISPLAY_VARIANT_TO_VOLTO.get(value, "default")


_TABS_DISPLAY_VARIANT_TO_VOLTO: dict[str, str] = {
    TabsDisplayVariant.STANDARD.value: "default",
    TabsDisplayVariant.ACCORDION.value: "accordion",
    TabsDisplayVariant.RESPONSIVE_TABS.value: "horizontal-responsive",
    TabsDisplayVariant.HORIZONTAL_CAROUSEL.value: "carousel-horizontal",
    TabsDisplayVariant.VERTICAL_CAROUSEL.value: "carousel-vertical",
}


def _tabs_display_variant_to_volto(value: Any) -> str:
    if isinstance(value, TabsDisplayVariant):
        value = value.value
    if not isinstance(value, str):
        return "default"
    return _TABS_DISPLAY_VARIANT_TO_VOLTO.get(value, "default")


_FIELD_VISIBILITY_OPERATOR_TO_VOLTO: dict[str, str] = {
    FieldVisibilityOperator.FILLED.value: "is_not_empty",
    FieldVisibilityOperator.EMPTY.value: "is_empty",
    FieldVisibilityOperator.EQUALS.value: "equals",
    FieldVisibilityOperator.NOT_EQUALS.value: "not_equals",
    FieldVisibilityOperator.CONTAINS.value: "contains",
    FieldVisibilityOperator.NOT_CONTAINS.value: "not_contains",
}


def _show_when_rules_to_volto(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        field_id = item.get("field_id")
        operator = item.get("operator")
        if isinstance(operator, FieldVisibilityOperator):
            operator = operator.value
        if not isinstance(field_id, str) or not field_id:
            continue
        if not isinstance(operator, str):
            continue
        condition = _FIELD_VISIBILITY_OPERATOR_TO_VOLTO.get(operator)
        if condition is None:
            continue
        condition_data: dict[str, Any] = {
            "field_id": field_id,
            "condition": condition,
            "field": {
                "value": field_id,
                "text": field_id,
            },
            "value_condition": item.get("expected_value"),
        }
        result.append(condition_data)
    return result


def _random_key(length: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


# ---------------------------------------------------------------------------
# HTML → Slate
# ---------------------------------------------------------------------------

# Maps HTML formatting tags to Slate inline element type names.
_INLINE_TAGS: dict[str, str] = {
    "strong": "strong",
    "b": "strong",
    "em": "em",
    "i": "em",
    "u": "u",
    "del": "del",
    "s": "del",
    "sub": "sub",
    "sup": "sup",
}

_BLOCK_TAGS: set[str] = {"p", "h2", "h3", "blockquote", "ul", "ol", "li"}


class _SlateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.result: list[dict[str, Any]] = []
        self._stack: list[dict[str, Any]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INLINE_TAGS:
            node: dict[str, Any] = {"type": _INLINE_TAGS[tag], "children": []}
            self._push(node)
            return

        if tag == "a":
            href = dict(attrs).get("href", "")
            node = {
                "type": "link",
                "data": {"url": href},
                "children": [],
            }
            self._push(node)
            return

        if tag == "br":
            # Soft line break → \n in Slate text node
            text_node: dict[str, Any] = {"text": "\n"}
            if self._stack:
                self._stack[-1]["children"].append(text_node)
            else:
                self.result.append(text_node)
            return

        if tag in _BLOCK_TAGS:
            node = {"type": tag, "children": []}
            self._push(node)
            return

    def handle_endtag(self, tag: str) -> None:
        if tag in _INLINE_TAGS or tag in ("a", *_BLOCK_TAGS):
            self._pop(tag)

    def handle_data(self, data: str) -> None:
        text_node: dict[str, Any] = {"text": data}
        if self._stack:
            self._stack[-1]["children"].append(text_node)
        else:
            self.result.append(text_node)

    def _push(self, node: dict[str, Any]) -> None:
        if self._stack:
            self._stack[-1]["children"].append(node)
        else:
            self.result.append(node)
        self._stack.append(node)

    def _pop(self, tag: str) -> None:
        if not self._stack:
            return

        node = self._stack.pop()

        # li nodes need to wrap children in a lic node
        if tag == "li":
            lic = {"type": "lic", "children": node.get("children", [])}
            node["children"] = [lic]

        # Ensure non-empty children
        if not node.get("children"):
            node["children"] = [{"text": ""}]


def _html_to_slate(html: str) -> list[dict[str, Any]]:
    parser = _SlateParser()
    parser.feed(html)
    return parser.result


def _slate_to_plaintext(nodes: list[dict[str, Any]]) -> str:
    """Extract plain text from a Slate AST."""
    parts: list[str] = []
    for node in nodes:
        if "text" in node:
            parts.append(node["text"])
        else:
            parts.append(_slate_to_plaintext(node.get("children", [])))
    return "".join(parts)


# ---------------------------------------------------------------------------
# HTML table → Slate table
# ---------------------------------------------------------------------------


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[dict[str, Any]] = []
        self._in_thead = False
        self._in_tbody = False
        self._current_row: list[dict[str, Any]] | None = None
        self._current_cell_html: list[str] | None = None
        self._current_cell_type: str = "data"
        self._cell_tag: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_tbody = True
        elif tag == "tr":
            self._current_row = []
        elif tag in ("th", "td"):
            self._cell_tag = tag
            self._current_cell_type = (
                "header" if self._in_thead or tag == "th" else "data"
            )
            self._current_cell_html = []
        elif self._current_cell_html is not None:
            attr_str = "".join(f' {k}="{v}"' for k, v in attrs if v is not None)
            self._current_cell_html.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag: str) -> None:
        if tag == "thead":
            self._in_thead = False
        elif tag == "tbody":
            self._in_tbody = False
        elif tag == "tr":
            if self._current_row is not None:
                self.rows.append({"key": _random_key(), "cells": self._current_row})
                self._current_row = None
        elif tag in ("th", "td"):
            if self._current_cell_html is not None and self._current_row is not None:
                inner_html = "".join(self._current_cell_html)
                value = (
                    _html_to_slate(inner_html) if inner_html.strip() else [{"text": ""}]
                )
                # Wrap bare text nodes in paragraphs
                wrapped: list[dict[str, Any]] = []
                for node in value:
                    if "text" in node:
                        wrapped.append({"type": "p", "children": [node]})
                    else:
                        wrapped.append(node)
                self._current_row.append(
                    {
                        "key": _random_key(),
                        "type": self._current_cell_type,
                        "value": wrapped,
                    }
                )
            self._current_cell_html = None
            self._cell_tag = None
        elif self._current_cell_html is not None:
            self._current_cell_html.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._current_cell_html is not None:
            self._current_cell_html.append(data)


def _html_table_to_slate(html: str) -> dict[str, Any]:
    parser = _TableParser()
    parser.feed(html)
    return {"rows": parser.rows}


# ---------------------------------------------------------------------------
# Column width reverse mapping
# ---------------------------------------------------------------------------

_WIDTH_TO_GRID_COL: dict[tuple[int, int], str] = {
    (1, 1): "halfWidth",
    (1, 2): "halfWidth",
    (1, 3): "oneThird",
    (1, 4): "oneThirdSmall",
    (2, 2): "halfWidth",
    (2, 3): "twoThirds",
    (2, 4): "halfWidthBig",
    (3, 3): "halfWidth",
    (3, 4): "threeFourths",
}


# ---------------------------------------------------------------------------
# Type-specific reverse converters
# ---------------------------------------------------------------------------


def _reverse_title(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    return {"@type": "title"}


def _reverse_description(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    return {"@type": "description"}


def _reverse_heading(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    return {
        "@type": "heading",
        "heading": attrs["text"],
        "tag": f"h{attrs['level']}",
        "alignment": "left",
    }


def _reverse_rich_text(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    html = attrs["html"]
    value = _html_to_slate(html)
    return _merge_original_block(
        block,
        ctx,
        {
            "@type": "slate",
            "value": value,
            "plaintext": _slate_to_plaintext(value),
            "styles": _styles_from_attrs(attrs, {"content_width": "blockWidth"}),
        },
    )


def _reverse_image(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    result: dict[str, Any] = {
        "@type": "image",
        "url": attrs["image_url"],
        "alt": attrs["alt_text"],
        "align": attrs["alignment"],
        "size": _IR_IMAGE_SIZE_TO_VOLTO.get(attrs["size"], "l"),
        "openLinkInNewTab": attrs["open_link_in_new_tab"],
    }
    href = _build_href(attrs["link"])
    if href:
        result["href"] = href
    return result


def _reverse_divider(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    return {
        "@type": "dividerBlock",
        "text": block["attributes"]["text"],
    }


def _reverse_video(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    return {
        "@type": "video",
        "url": attrs["url"],
        "preview_image": attrs["preview_image"],
        "align": attrs["alignment"],
    }


def _reverse_button(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    return {
        "@type": "__button",
        "title": attrs["title"],
        "href": _build_href(attrs["link"]),
        "inneralign": attrs["alignment"],
        "openLinkInNewTab": attrs["open_link_in_new_tab"],
    }


def _reverse_teaser(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    return _merge_original_block(
        block,
        ctx,
        {
            "@type": "teaser",
            "href": _build_href(attrs["link"]),
            "overwrite": attrs["use_custom_content"],
            "title": attrs["title"],
            "head_title": attrs["eyebrow"],
            "description": attrs["description"],
            "preview_image": _build_href(attrs["preview_image"]),
            "showButton": attrs.get("show_button", False),
            "buttonText": attrs.get("button_label", ""),
            "styles": _styles_from_attrs(
                attrs,
                {
                    "alignment": "align",
                    "button_style": "buttonColor",
                },
            ),
        },
    )


def _reverse_highlight(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    styles: dict[str, Any] = {}
    ir_color = attrs.get("background_color")
    if isinstance(ir_color, HighlightBackgroundColor):
        ir_color = ir_color.value
    if ir_color and ir_color in _IR_COLOR_TO_VOLTO:
        styles["descriptionColor"] = _IR_COLOR_TO_VOLTO[ir_color]
    return {
        "@type": "highlight",
        "styles": styles,
        "button": attrs.get("show_button", True),
        "url": attrs["image_url"],
        "title": attrs["title"],
        "value": _html_to_slate(attrs["html"]),
        "buttonText": attrs["button_label"],
        "buttonLink": _build_href(attrs["button_link"]),
    }


def _reverse_table(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    table = _html_table_to_slate(attrs["html"])
    return {
        "@type": "slateTable",
        "table": {
            **table,
            "basic": attrs["minimal_style"],
            "celled": attrs["show_cell_borders"],
            "compact": attrs["compact"],
            "fixed": attrs["fixed_column_width"],
            "hideHeaders": attrs["hide_headers"],
            "inverted": attrs["dark_background"],
            "striped": attrs["striped_rows"],
        },
    }


def _listing_query_to_volto(query: dict[str, Any]) -> dict[str, Any]:
    def _date_value(value: Any) -> Any:
        return value.isoformat() if hasattr(value, "isoformat") else value

    volto_filters: list[dict[str, Any]] = []
    for item in query.get("filters", []):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type == "path" and item.get("paths"):
            volto_filters.append(
                {
                    "i": "path",
                    "o": "plone.app.querystring.operation.string.absolutePath",
                    "v": item["paths"][0],
                }
            )
        elif item_type == "content_type" and item.get("content_types"):
            volto_filters.append(
                {
                    "i": "portal_type",
                    "o": "plone.app.querystring.operation.selection.any",
                    "v": item["content_types"],
                }
            )
        elif item_type == "subject" and item.get("subjects"):
            operator = item.get("operator", "any")
            volto_filters.append(
                {
                    "i": "Subject",
                    "o": (
                        "plone.app.querystring.operation.selection.all"
                        if operator == "all"
                        else "plone.app.querystring.operation.selection.any"
                    ),
                    "v": item["subjects"],
                }
            )
        elif item_type == "date":
            if item.get("after"):
                volto_filters.append(
                    {
                        "i": item.get("field", "effective"),
                        "o": "plone.app.querystring.operation.date.largerThan",
                        "v": _date_value(item["after"]),
                    }
                )
            if item.get("before"):
                volto_filters.append(
                    {
                        "i": item.get("field", "effective"),
                        "o": "plone.app.querystring.operation.date.lessThan",
                        "v": _date_value(item["before"]),
                    }
                )

    result: dict[str, Any] = {
        "limit": str(query.get("limit", 10)),
        "query": volto_filters,
        "sort_order": query.get("sort_order", "ascending"),
    }
    if query.get("sort_on"):
        result["sort_on"] = query["sort_on"]
    return result


def _reverse_listing(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    querystring = _listing_query_to_volto(attrs["query"])
    if ctx is not None:
        old_ir = ctx.ir_index.get(block["id"])
        original = ctx.volto_index.get(block["id"])
        if (
            old_ir is not None
            and original is not None
            and old_ir.get("attributes", {}).get("query") == attrs["query"]
            and isinstance(original.get("querystring"), dict)
        ):
            querystring = original["querystring"]
    return _merge_original_block(
        block,
        ctx,
        {
            "@type": "listing",
            "headline": attrs["heading"],
            "headlineTag": f"h{attrs['heading_level']}",
            "querystring": querystring,
            "variation": _listing_display_variant_to_volto(attrs["display_variant"]),
        },
    )


def _reverse_slider(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    slides: list[dict[str, Any]] = []
    for child in block.get("children", []):
        original = _try_preserve_child(child, ctx)
        if original is not None:
            slides.append(original)
            continue

        child_attrs = child["attributes"]
        slides.append(
            {
                "@id": child["id"],
                "href": _build_href(child_attrs["link"]),
                "head_title": child_attrs["eyebrow"],
                "title": child_attrs["title"],
                "description": child_attrs["description"],
                "preview_image": _build_href(child_attrs["preview_image"]),
            }
        )
    return {
        "@type": "slider",
        "slides": slides,
        "autoplayEnabled": attrs["autoplay"],
        "autoplayDelay": attrs["autoplay_delay_ms"],
        "autoplayJump": attrs["autoplay_transition"]
        == SliderAutoplayTransition.JUMP.value,
        "hideArrows": not attrs["show_arrows"],
    }


def _reverse_carousel(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    columns: list[dict[str, Any]] = []
    for child in block.get("children", []):
        original = _try_preserve_child(child, ctx)
        if original is not None:
            columns.append(original)
            continue

        child_attrs = child["attributes"]
        columns.append(
            {
                "@id": child["id"],
                "href": _build_href(child_attrs["link"]),
                "title": child_attrs["title"],
                "description": child_attrs["description"],
                "preview_image": _build_href(child_attrs["preview_image"]),
            }
        )
    return {
        "@type": "carousel",
        "headline": attrs["heading"],
        "items_to_show": str(attrs["visible_items"]),
        "hideDescription": not attrs["show_descriptions"],
        "columns": columns,
    }


def _reverse_columns(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    children = block.get("children", [])
    total_width = sum(c["attributes"]["width"] for c in children)

    col_blocks: dict[str, Any] = {}
    col_items: list[str] = []
    grid_cols: list[str] = []

    for child in children:
        col_uid = child["id"]
        col_items.append(col_uid)

        width = child["attributes"]["width"]
        grid_col = _WIDTH_TO_GRID_COL.get((width, total_width))
        if grid_col is None:
            msg = f"Cannot map column width ({width}, {total_width}) to grid col"
            raise ValueError(msg)
        grid_cols.append(grid_col)

        # Convert inner blocks
        inner_blocks: dict[str, Any] = {}
        inner_items: list[str] = []
        for inner_child in child.get("children", []):
            inner_uid = inner_child["id"]
            inner_blocks[inner_uid] = _reverse_block(inner_child, ctx)
            inner_items.append(inner_uid)

        col_blocks[col_uid] = {
            "blocks": inner_blocks,
            "blocks_layout": {"items": inner_items},
        }

    return {
        "@type": "columnsBlock",
        "gridCols": grid_cols,
        "data": {
            "blocks": col_blocks,
            "blocks_layout": {"items": col_items},
        },
    }


def _reverse_accordion(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    children = block.get("children", [])

    panel_blocks: dict[str, Any] = {}
    panel_items: list[str] = []

    for child in children:
        panel_uid = child["id"]
        panel_items.append(panel_uid)

        inner_blocks: dict[str, Any] = {}
        inner_items: list[str] = []
        for inner_child in child.get("children", []):
            inner_uid = inner_child["id"]
            inner_blocks[inner_uid] = _reverse_block(inner_child, ctx)
            inner_items.append(inner_uid)

        panel_blocks[panel_uid] = {
            "title": child["attributes"]["title"],
            "blocks": inner_blocks,
            "blocks_layout": {"items": inner_items},
        }

    result: dict[str, Any] = {
        "@type": "accordion",
        "headline": attrs["heading"],
        "title": attrs["title"],
        "title_size": f"h{attrs.get('heading_level', 2)}",
        "right_arrows": attrs["arrow_position"] == AccordionArrowPosition.RIGHT.value,
        "non_exclusive": not attrs["single_panel_open"],
        "filtering": attrs["show_filter"],
        "collapsed": attrs["start_collapsed"],
        "styles": _styles_from_attrs(
            attrs,
            {
                "heading_alignment": "headlineAlign",
                "content_width": "blockWidth",
            },
        ),
        "data": {
            "blocks": panel_blocks,
            "blocks_layout": {"items": panel_items},
        },
    }
    return _merge_original_block(block, ctx, result)


def _plaintext_to_slate(text: str) -> list[dict[str, Any]]:
    """Wrap plain text in a minimal Slate paragraph."""
    if not text:
        return []
    return [{"type": "p", "children": [{"text": text}]}]


def _reverse_quote(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    attrs = block["attributes"]
    result: dict[str, Any] = {
        "@type": "quote",
        "value": _html_to_slate(attrs["html"]),
        "source": _html_to_slate(attrs["attribution_html"]),
        "extra": _html_to_slate(attrs["context_html"]),
        "variation": _quote_display_variant_to_volto(
            attrs.get("display_variant", "standard")
        ),
        "reversed": attrs.get("attribution_first", False),
    }
    if attrs.get("alignment") and attrs["alignment"] != "default":
        result["position"] = attrs["alignment"]
    if attrs.get("role_html"):
        result["title"] = _html_to_slate(attrs["role_html"])
    if attrs.get("image_url"):
        result["image"] = _build_href(attrs["image_url"])
    return result


_INT_TO_VOLTO_WIDTHS: dict[int, str] = {1: "one", 2: "two", 3: "three", 4: "four"}


_IR_FIELD_TYPE_TO_VOLTO: dict[str, str] = {
    "form_field:text": "text",
    "form_field:textarea": "textarea",
    "form_field:number": "number",
    "form_field:email": "from",
    "form_field:date": "date",
    "form_field:attachment": "attachment",
    "form_choice:select": "select",
    "form_choice:radio": "single_choice",
    "form_choice:checkbox": "multiple_choice",
}


def _reverse_statistic(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    children = block.get("children", [])

    items: list[dict[str, Any]] = []
    for child in children:
        ca = child["attributes"]
        item: dict[str, Any] = {
            "@id": child["id"],
            "value": _plaintext_to_slate(ca["value"]),
            "label": _plaintext_to_slate(ca.get("label", "")),
            "info": _plaintext_to_slate(ca.get("info", "")),
        }
        if ca.get("link"):
            item["href"] = ca["link"]
        if ca.get("prefix"):
            item["prefix"] = ca["prefix"]
        if ca.get("suffix"):
            item["suffix"] = ca["suffix"]
        items.append(item)

    animation: dict[str, Any] = {}
    if attrs.get("animation_enabled"):
        animation = {
            "enabled": True,
            "duration": str(attrs.get("animation_duration", 5)),
            "decimals": str(attrs.get("animation_decimals", 0)),
        }

    return {
        "@type": "statistic_block",
        "horizontal": attrs.get("horizontal_layout", False),
        "inverted": attrs.get("dark_background", False),
        "size": attrs.get("size", "small"),
        "widths": _INT_TO_VOLTO_WIDTHS.get(attrs.get("items_per_row", 1), "one"),
        "animation": animation,
        "items": items,
    }


def _reverse_form(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    children = block.get("children", [])
    original_form = ctx.volto_index.get(block["id"]) if ctx is not None else None
    original_subblocks: dict[str, dict[str, Any]] = {}
    if isinstance(original_form, dict):
        for sub in original_form.get("subblocks", []):
            if isinstance(sub, dict) and isinstance(sub.get("id"), str):
                original_subblocks[sub["id"]] = sub

    subblocks: list[dict[str, Any]] = []
    for child in children:
        ir_type = child["type"]
        ca = child["attributes"]

        if ir_type == "rich_text":
            original = original_subblocks.get(child["id"], {})
            subblocks.append(
                {
                    **original,
                    "id": child["id"],
                    "field_type": "static_text",
                    "label": "",
                    "value": ca.get("html", ""),
                }
            )
            continue

        lookup_key = ir_type
        if ir_type in ("form_field", "form_choice"):
            lookup_key = f"{ir_type}:{ca.get('input_type', 'text')}"
        volto_type = _IR_FIELD_TYPE_TO_VOLTO.get(lookup_key, "text")
        sub: dict[str, Any] = {
            **original_subblocks.get(child["id"], {}),
            "id": child["id"],
            "field_id": child["id"],
            "field_type": volto_type,
            "label": ca.get("label", ""),
        }

        sub["description"] = ca.get("description", "")
        sub["required"] = ca.get("required", False)

        if ir_type == "form_field" and ca.get("use_as_reply_to"):
            sub["use_as_reply_to"] = True

        if ir_type == "form_choice":
            sub["input_values"] = ca.get("options", [])
            if ca.get("default"):
                sub["default_value"] = ca["default"]

        sub["visibility_conditions"] = _show_when_rules_to_volto(ca.get("show_when"))
        subblocks.append(sub)

    for label, value in attrs.get("hidden_fields", {}).items():
        subblocks.append(
            {
                "id": str(_uuid4()),
                "field_id": str(_uuid4()),
                "field_type": "hidden",
                "label": label,
                "value": value,
            }
        )

    result = {
        "@type": "form",
        "title": attrs.get("title", ""),
        "description": attrs.get("description", ""),
        "submit_label": attrs.get("submit_button_label", "Submit"),
        "show_cancel": attrs.get("show_cancel_button", False),
        "cancel_label": attrs.get("cancel_button_label", ""),
        "default_to": attrs.get("recipient_address", ""),
        "default_subject": attrs.get("email_subject", ""),
        "default_from": "noreply@plone.org",
        "send": True,
        "captcha": "honeypot",
        "styles": _styles_from_attrs(attrs, {"heading_alignment": "headlineAlign"}),
        "subblocks": subblocks,
    }
    return _merge_original_block(block, ctx, result)


def _reverse_tabs(block: dict[str, Any], ctx: _DiffCtx | None) -> dict[str, Any]:
    preserved = _try_preserve_container(block, ctx)
    if preserved is not None:
        return preserved

    attrs = block["attributes"]
    children = block.get("children", [])

    tab_blocks: dict[str, Any] = {}
    tab_items: list[str] = []

    for child in children:
        tab_uid = child["id"]
        tab_items.append(tab_uid)

        inner_blocks: dict[str, Any] = {}
        inner_items: list[str] = []
        for inner_child in child.get("children", []):
            inner_uid = inner_child["id"]
            inner_blocks[inner_uid] = _reverse_block(inner_child, ctx)
            inner_items.append(inner_uid)

        tab_blocks[tab_uid] = {
            "@type": "tab",
            "title": child["attributes"]["title"],
            "blocks": inner_blocks,
            "blocks_layout": {"items": inner_items},
        }

    return {
        "@type": "tabs_block",
        "title": attrs.get("title", ""),
        "description": attrs.get("description", ""),
        "variation": _tabs_display_variant_to_volto(
            attrs.get("display_variant", "standard")
        ),
        "hideEmptyTabs": not attrs.get("show_empty_tabs", True),
        "data": {
            "blocks": tab_blocks,
            "blocks_layout": {"items": tab_items},
        },
    }


# ---------------------------------------------------------------------------
# Reverse converter dispatch table
# ---------------------------------------------------------------------------

type _ReverseConverterFn = Callable[[dict[str, Any], _DiffCtx | None], dict[str, Any]]

_REVERSE_CONVERTERS: dict[str, _ReverseConverterFn] = {
    "title": _reverse_title,
    "description": _reverse_description,
    "heading": _reverse_heading,
    "rich_text": _reverse_rich_text,
    "image": _reverse_image,
    "divider": _reverse_divider,
    "video": _reverse_video,
    "button": _reverse_button,
    "teaser": _reverse_teaser,
    "highlight": _reverse_highlight,
    "table": _reverse_table,
    "listing": _reverse_listing,
    "slider": _reverse_slider,
    "carousel": _reverse_carousel,
    "columns": _reverse_columns,
    "accordion": _reverse_accordion,
    "quote": _reverse_quote,
    "statistic": _reverse_statistic,
    "form": _reverse_form,
    "tabs": _reverse_tabs,
}
