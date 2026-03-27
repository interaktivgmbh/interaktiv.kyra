"""Convert raw Volto page JSON to the volto/vanilla IR PageState."""

from __future__ import annotations

from collections import defaultdict
from html import escape as _esc
from collections.abc import Callable
from typing import Any

from .schema import Layout, Metadata, PageState


class ConversionError(Exception):
    """Raised when Volto JSON cannot be converted to the IR."""


# ---------------------------------------------------------------------------
# Highlight description color mapping (Volto CSS class → IR name)
# ---------------------------------------------------------------------------

_VOLTO_COLOR_TO_IR: dict[str, str] = {
    "highlight-custom-color-1": "light-blue",
    "highlight-custom-color-2": "dark-teal",
    "highlight-custom-color-3": "yellow",
    "highlight-custom-color-4": "light-green",
    "highlight-custom-color-5": "olive",
}

# ---------------------------------------------------------------------------
# Column width mapping
# ---------------------------------------------------------------------------


class _NameCounter:
    """Generates sequential names like image_1, rich_text_2, etc."""

    def __init__(self) -> None:
        self._counts: dict[str, int] = defaultdict(int)

    def next(self, ir_type: str) -> str:
        self._counts[ir_type] += 1
        return f"{ir_type}_{self._counts[ir_type]}"


_GRID_COL_WIDTHS: dict[str, int] = {
    "oneThirdSmall": 1,
    "oneThird": 1,
    "halfWidth": 2,
    "halfWidthBig": 2,
    "twoThirds": 2,
    "threeFourths": 3,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def volto_to_page_state(data: dict[str, Any]) -> PageState:
    """Convert a raw Volto page dict to an IR PageState (metadata + layout).

    Args:
        data: A single Volto page object (not the outer array).

    Returns:
        A validated PageState instance.

    Raises:
        ConversionError: If the data contains unsupported block types or
            is structurally invalid.
    """
    blocks = data.get("blocks")
    if not isinstance(blocks, dict):
        raise ConversionError("Missing or invalid 'blocks' in page data")

    layout_items = data.get("blocks_layout", {}).get("items")
    if not isinstance(layout_items, list):
        raise ConversionError("Missing or invalid 'blocks_layout.items'")

    page_title = data.get("title", "")

    metadata = Metadata(
        link=data.get("link", ""),
        title=page_title,
        description=data.get("description", ""),
        preview_image=data.get("preview_image", ""),
        subjects=data.get("subjects", []),
    )

    page_description = data.get("description", "")
    names = _NameCounter()
    ir_blocks = _convert_blocks(
        blocks, layout_items, "/", page_title, page_description, names
    )
    layout = Layout.model_validate(ir_blocks)

    return PageState(metadata=metadata, layout=layout)


# ---------------------------------------------------------------------------
# Block conversion dispatch
# ---------------------------------------------------------------------------


def _convert_blocks(
    blocks: dict[str, Any],
    layout_items: list[str],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for uid in layout_items:
        raw = blocks.get(uid)
        if raw is None:
            continue
        result.append(
            _convert_block(uid, raw, path, page_title, page_description, names)
        )
    return result


def _convert_block(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    block_type = raw.get("@type", "")

    converter = _CONVERTERS.get(block_type)
    if converter is None:
        raise ConversionError(f"Unsupported block type: {block_type!r}")

    return converter(uid, raw, path, page_title, page_description, names)


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------


def _child_path(parent_path: str, parent_name: str) -> str:
    if parent_path == "/":
        return "/" + parent_name
    return parent_path + "/" + parent_name


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def _extract_href(href: Any) -> str:
    """Extract URL from a Volto href array."""
    if isinstance(href, list) and href:
        item = href[0]
        if isinstance(item, dict):
            return item.get("@id", "")
    return ""


def _extract_preview_image(preview_image: Any) -> str:
    """Extract image URL from a Volto preview_image array."""
    return _extract_href(preview_image)


def _string_or_empty(value: Any) -> str:
    """Normalize nullable string-like Volto values."""
    return value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Slate → HTML
# ---------------------------------------------------------------------------


def _slate_to_html(nodes: list[dict[str, Any]]) -> str:
    return "".join(_slate_node_to_html(n) for n in nodes)


def _slate_to_plaintext(nodes: list[dict[str, Any]]) -> str:
    """Extract plain text from a Slate AST."""
    parts: list[str] = []
    for node in nodes:
        if "text" in node:
            parts.append(node["text"])
        else:
            parts.append(_slate_to_plaintext(node.get("children", [])))
    return "".join(parts).strip()


_SLATE_TAG_MAP: dict[str, str | None] = {
    "p": "p",
    "h2": "h2",
    "h3": "h3",
    "blockquote": "blockquote",
    "ul": "ul",
    "ol": "ol",
    "li": "li",
    "lic": None,  # list item content wrapper — just pass through
    # Inline formatting elements (Slate stores marks as element nodes)
    "strong": "strong",
    "em": "em",
    "u": "u",
    "del": "del",
    "sub": "sub",
    "sup": "sup",
}


def _slate_node_to_html(node: dict[str, Any]) -> str:
    # Text node
    if "text" in node:
        return _slate_text_to_html(node)

    # Element node
    node_type = node.get("type", "")
    children_html = "".join(_slate_node_to_html(c) for c in node.get("children", []))

    # Links
    if node_type in ("link", "a"):
        url = _slate_link_url(node)
        return f'<a href="{_esc(url)}">{children_html}</a>'

    # Block-level and list elements
    tag = _SLATE_TAG_MAP.get(node_type)
    if tag is not None:
        return f"<{tag}>{children_html}</{tag}>"
    if node_type in _SLATE_TAG_MAP:  # tag is None → lic
        return children_html

    # Unknown element type — pass through children
    return children_html


def _slate_text_to_html(node: dict[str, Any]) -> str:
    text = _esc(node.get("text", ""))
    if not text:
        return ""

    # Slate soft line breaks (Shift+Enter) are stored as \n in text nodes.
    text = text.replace("\n", "<br>")

    if node.get("bold"):
        text = f"<strong>{text}</strong>"
    if node.get("italic"):
        text = f"<em>{text}</em>"
    if node.get("underline"):
        text = f"<u>{text}</u>"
    if node.get("strikethrough"):
        text = f"<del>{text}</del>"
    if node.get("sub"):
        text = f"<sub>{text}</sub>"
    if node.get("sup"):
        text = f"<sup>{text}</sup>"

    return text


def _slate_link_url(node: dict[str, Any]) -> str:
    data = node.get("data", {})
    # External link
    if "url" in data:
        return data["url"]
    # Internal link
    internal = data.get("link", {}).get("internal", {}).get("internal_link", [])
    if internal and isinstance(internal, list):
        return internal[0].get("@id", "")
    return ""


# ---------------------------------------------------------------------------
# Slate table → HTML
# ---------------------------------------------------------------------------


def _slate_table_to_html(table: dict[str, Any]) -> str:
    rows = table.get("rows", [])
    parts: list[str] = ["<table>"]

    header_rows: list[list[dict[str, Any]]] = []
    data_rows: list[list[dict[str, Any]]] = []

    for row in rows:
        cells = row.get("cells", [])
        if cells and cells[0].get("type") == "header":
            header_rows.append(cells)
        else:
            data_rows.append(cells)

    if header_rows:
        parts.append("<thead>")
        for cells in header_rows:
            parts.append("<tr>")
            for cell in cells:
                content = _slate_to_html(cell.get("value", []))
                parts.append(f"<th>{content}</th>")
            parts.append("</tr>")
        parts.append("</thead>")

    if data_rows:
        parts.append("<tbody>")
        for cells in data_rows:
            parts.append("<tr>")
            for cell in cells:
                content = _slate_to_html(cell.get("value", []))
                parts.append(f"<td>{content}</td>")
            parts.append("</tr>")
        parts.append("</tbody>")

    parts.append("</table>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Type-specific converters
#
# Each returns a plain dict matching the IR schema. Validation happens once
# at the end via Layout.model_validate().
# ---------------------------------------------------------------------------


def _convert_title(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "title",
        "id": uid,
        "path": path,
        "name": names.next("title"),
        "attributes": {"text": page_title},
    }


def _convert_description(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "description",
        "id": uid,
        "path": path,
        "name": names.next("description"),
        "attributes": {"text": page_description},
    }


def _convert_slate(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "rich_text",
        "id": uid,
        "path": path,
        "name": names.next("rich_text"),
        "attributes": {"html": _slate_to_html(raw.get("value", []))},
    }


def _convert_heading(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    tag = raw.get("tag", "h2")
    level = 3 if tag == "h3" else 2
    return {
        "type": "heading",
        "id": uid,
        "path": path,
        "name": names.next("heading"),
        "attributes": {
            "text": raw.get("heading", ""),
            "level": level,
        },
    }


def _convert_html(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "rich_text",
        "id": uid,
        "path": path,
        "name": names.next("rich_text"),
        "attributes": {"html": raw.get("html", "")},
    }


def _convert_image(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "image",
        "id": uid,
        "path": path,
        "name": names.next("image"),
        "attributes": {
            "image_url": raw.get("url", ""),
            "alt_text": raw.get("alt", ""),
            "alignment": raw.get("align", "center"),
            "size": raw.get("size", "l"),
            "link": _extract_href(raw.get("href")),
            "open_link_in_new_tab": raw.get("openLinkInNewTab", False),
        },
    }


def _convert_divider(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "divider",
        "id": uid,
        "path": path,
        "name": names.next("divider"),
        "attributes": {
            "text": raw.get("text", ""),
        },
    }


def _convert_video(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "video",
        "id": uid,
        "path": path,
        "name": names.next("video"),
        "attributes": {
            "url": raw.get("url", ""),
            "preview_image": raw.get("preview_image", ""),
            "alignment": raw.get("align", "center"),
        },
    }


def _convert_button(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "button",
        "id": uid,
        "path": path,
        "name": names.next("button"),
        "attributes": {
            "title": raw.get("title", ""),
            "link": _extract_href(raw.get("href")),
            "inner_alignment": raw.get("inneralign", "left"),
            "open_link_in_new_tab": raw.get("openLinkInNewTab", False),
        },
    }


def _convert_teaser(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "teaser",
        "id": uid,
        "path": path,
        "name": names.next("teaser"),
        "attributes": {
            "link": _extract_href(raw.get("href")),
            "overwrite": raw.get("overwrite", False),
            "title": raw.get("title", ""),
            "head_title": _string_or_empty(raw.get("head_title", "")),
            "description": raw.get("description", ""),
            "preview_image": _extract_preview_image(raw.get("preview_image")),
        },
    }


def _convert_highlight(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    volto_color = (raw.get("styles") or {}).get("descriptionColor", "")
    ir_color = _VOLTO_COLOR_TO_IR.get(volto_color)
    return {
        "type": "highlight",
        "id": uid,
        "path": path,
        "name": names.next("highlight"),
        "attributes": {
            "image_url": raw.get("url", ""),
            "title": raw.get("title", ""),
            "html": _slate_to_html(raw.get("value", [])),
            "button_show": bool(raw.get("button", True)),
            "button_text": raw.get("buttonText", ""),
            "button_link": _extract_href(raw.get("buttonLink")),
            "description_color": ir_color,
        },
    }


def _convert_table(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    table = raw.get("table", {})
    return {
        "type": "table",
        "id": uid,
        "path": path,
        "name": names.next("table"),
        "attributes": {
            "html": _slate_table_to_html(table),
            "minimal_style": table.get("basic", False),
            "show_cell_borders": table.get("celled", False),
            "compact": table.get("compact", False),
            "fixed_column_width": table.get("fixed", False),
            "hide_headers": table.get("hideHeaders", False),
            "inverted_colors": table.get("inverted", False),
            "striped_rows": table.get("striped", False),
        },
    }


def _convert_slider(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    slider_name = names.next("slider")
    child_path = _child_path(path, slider_name)
    slides: list[dict[str, Any]] = []

    for slide_raw in raw.get("slides", []):
        slide_id = slide_raw.get("@id", "")
        slides.append(
            {
                "type": "slide",
                "id": slide_id,
                "path": child_path,
                "name": names.next("slide"),
                "attributes": {
                    "link": _extract_href(slide_raw.get("href")),
                    "head_title": slide_raw.get("head_title", ""),
                    "title": slide_raw.get("title", ""),
                    "description": slide_raw.get("description", ""),
                    "preview_image": _extract_preview_image(
                        slide_raw.get("preview_image")
                    ),
                },
            }
        )

    return {
        "type": "slider",
        "id": uid,
        "path": path,
        "name": slider_name,
        "attributes": {
            "autoplay": raw.get("autoplayEnabled", False),
            "autoplay_delay": raw.get("autoplayDelay", 5000),
            "autoplay_jump": raw.get("autoplayJump", False),
            "hide_arrows": raw.get("hideArrows", False),
        },
        "children": slides,
    }


def _convert_carousel(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    carousel_name = names.next("carousel")
    child_path = _child_path(path, carousel_name)
    items: list[dict[str, Any]] = []

    for item_raw in raw.get("columns", []):
        item_id = item_raw.get("@id", "")
        items.append(
            {
                "type": "carousel_item",
                "id": item_id,
                "path": child_path,
                "name": names.next("carousel_item"),
                "attributes": {
                    "link": _extract_href(item_raw.get("href")),
                    "title": item_raw.get("title", ""),
                    "description": item_raw.get("description", ""),
                    "preview_image": _extract_preview_image(
                        item_raw.get("preview_image")
                    ),
                },
            }
        )

    visible_items_raw = raw.get("items_to_show", "3")
    try:
        visible_items = int(visible_items_raw)
    except (ValueError, TypeError):
        visible_items = 3

    return {
        "type": "carousel",
        "id": uid,
        "path": path,
        "name": carousel_name,
        "attributes": {
            "headline": raw.get("headline", ""),
            "visible_items": visible_items,
            "hide_description": raw.get("hideDescription", False),
        },
        "children": items,
    }


def _convert_columns(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    data = raw.get("data", {})
    col_blocks = data.get("blocks", {})
    col_layout = data.get("blocks_layout", {}).get("items", [])
    grid_cols: list[str] = raw.get("gridCols", [])

    columns_name = names.next("columns")
    child_path = _child_path(path, columns_name)
    columns: list[dict[str, Any]] = []

    for i, col_uid in enumerate(col_layout):
        col_data = col_blocks.get(col_uid, {})

        # Determine width from gridCols
        grid_col = grid_cols[i] if i < len(grid_cols) else "halfWidth"
        width = _GRID_COL_WIDTHS.get(grid_col)
        if width is None:
            raise ConversionError(f"Unknown column width: {grid_col!r}")

        # Recursively convert inner blocks
        col_name = names.next("column")
        inner_blocks = col_data.get("blocks", {})
        inner_layout = col_data.get("blocks_layout", {}).get("items", [])
        inner_path = _child_path(child_path, col_name)

        columns.append(
            {
                "type": "column",
                "id": col_uid,
                "path": child_path,
                "name": col_name,
                "attributes": {"width": width},
                "children": _convert_blocks(
                    inner_blocks,
                    inner_layout,
                    inner_path,
                    page_title,
                    page_description,
                    names,
                ),
            }
        )

    return {
        "type": "columns",
        "id": uid,
        "path": path,
        "name": columns_name,
        "attributes": {"reverse_wrap": False},
        "children": columns,
    }


def _convert_accordion(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    data = raw.get("data", {})
    panel_blocks = data.get("blocks", {})
    panel_layout = data.get("blocks_layout", {}).get("items", [])

    accordion_name = names.next("accordion")
    child_path = _child_path(path, accordion_name)
    panels: list[dict[str, Any]] = []

    for panel_uid in panel_layout:
        panel_data = panel_blocks.get(panel_uid, {})

        panel_name = names.next("accordion_panel")
        inner_blocks = panel_data.get("blocks", {})
        inner_layout = panel_data.get("blocks_layout", {}).get("items", [])
        inner_path = _child_path(child_path, panel_name)

        panels.append(
            {
                "type": "accordion_panel",
                "id": panel_uid,
                "path": child_path,
                "name": panel_name,
                "attributes": {
                    "title": panel_data.get("title", ""),
                },
                "children": _convert_blocks(
                    inner_blocks,
                    inner_layout,
                    inner_path,
                    page_title,
                    page_description,
                    names,
                ),
            }
        )

    return {
        "type": "accordion",
        "id": uid,
        "path": path,
        "name": accordion_name,
        "attributes": {
            "headline": raw.get("headline", ""),
            "title": raw.get("title", ""),
            "right_arrows": raw.get("right_arrows", True),
            "exclusive": not raw.get("non_exclusive", True),
            "collapsed": raw.get("collapsed", True),
            "filtering": raw.get("filtering", False),
        },
        "children": panels,
    }


def _convert_quote(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    return {
        "type": "quote",
        "id": uid,
        "path": path,
        "name": names.next("quote"),
        "attributes": {
            "html": _slate_to_html(raw.get("value", [])),
            "source_html": _slate_to_html(raw.get("source", [])),
            "extra_html": _slate_to_html(raw.get("extra", [])),
            "variation": raw.get("variation", "default"),
            "position": raw.get("position"),
            "reversed": raw.get("reversed", False),
            "title_html": (
                _slate_to_html(raw.get("title", []))
                if isinstance(raw.get("title"), list)
                else ""
            ),
            "image_url": _extract_href(raw.get("image")),
        },
    }


_VOLTO_WIDTHS_TO_INT: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
}


def _convert_statistic(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    container_name = names.next("statistic")
    child_path = _child_path(path, container_name)

    animation = raw.get("animation") or {}
    widths_str = raw.get("widths", "one")
    widths_int = _VOLTO_WIDTHS_TO_INT.get(widths_str, 1)

    children: list[dict[str, Any]] = []
    for item in raw.get("items", []):
        children.append(
            {
                "type": "statistic_item",
                "id": item.get("@id", ""),
                "path": child_path,
                "name": names.next("statistic_item"),
                "attributes": {
                    "value": _slate_to_plaintext(item.get("value", [])),
                    "label": _slate_to_plaintext(item.get("label", [])),
                    "info": _slate_to_plaintext(item.get("info", [])),
                    "link": item.get("href", ""),
                    "prefix": item.get("prefix", ""),
                    "suffix": item.get("suffix", ""),
                },
            }
        )

    return {
        "type": "statistic",
        "id": uid,
        "path": path,
        "name": container_name,
        "attributes": {
            "horizontal": raw.get("horizontal", False),
            "inverted": raw.get("inverted", False),
            "size": raw.get("size", "small"),
            "widths": widths_int,
            "animation_enabled": bool(animation.get("enabled", False)),
            "animation_duration": float(animation.get("duration", 5)),
            "animation_decimals": int(animation.get("decimals", 0)),
        },
        "children": children,
    }


_VOLTO_FIELD_KIND: dict[str, str] = {
    "text": "text",
    "textarea": "textarea",
    "number": "number",
    "from": "email",
    "date": "date",
    "attachment": "attachment",
    "select": "select",
    "single_choice": "radio",
    "multiple_choice": "checkbox",
    "checkbox": "checkbox",
}

_VOLTO_FIELD_TYPE_TO_IR: dict[str, str] = {
    "text": "form_field",
    "textarea": "form_field",
    "number": "form_field",
    "from": "form_field",
    "select": "form_choice",
    "single_choice": "form_choice",
    "multiple_choice": "form_choice",
    "checkbox": "form_choice",
    "date": "form_field",
    "attachment": "form_field",
    "static_text": "rich_text",
}


def _convert_form(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    container_name = names.next("form")
    child_path = _child_path(path, container_name)

    children: list[dict[str, Any]] = []
    metadata: dict[str, str] = {}
    for sub in raw.get("subblocks", []):
        field_type = sub.get("field_type", "text")

        if field_type == "hidden":
            metadata[sub.get("label", "")] = sub.get("value", "")
            continue

        ir_type = _VOLTO_FIELD_TYPE_TO_IR.get(field_type, "form_field")

        if ir_type == "rich_text":
            children.append(
                {
                    "type": "rich_text",
                    "id": sub.get("id", ""),
                    "path": child_path,
                    "name": names.next("rich_text"),
                    "attributes": {"html": sub.get("value", "")},
                }
            )
            continue

        attrs: dict[str, Any] = {
            "label": sub.get("label", ""),
            "description": sub.get("description", ""),
            "required": sub.get("required", False),
        }

        if ir_type == "form_field":
            attrs["kind"] = _VOLTO_FIELD_KIND.get(field_type, "text")
            attrs["send_copy"] = sub.get("use_as_reply_to", False)

        if ir_type == "form_choice":
            attrs["kind"] = _VOLTO_FIELD_KIND.get(field_type, "select")
            attrs["options"] = sub.get("input_values", [])
            attrs["default"] = sub.get("default_value", "")

        children.append(
            {
                "type": ir_type,
                "id": sub.get("id", ""),
                "path": child_path,
                "name": names.next(ir_type),
                "attributes": attrs,
            }
        )

    return {
        "type": "form",
        "id": uid,
        "path": path,
        "name": container_name,
        "attributes": {
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "submit_label": raw.get("submit_label", "Submit"),
            "show_cancel": raw.get("show_cancel", False),
            "cancel_label": raw.get("cancel_label", ""),
            "recipient_email": raw.get("default_to", ""),
            "subject": raw.get("default_subject", ""),
            "metadata": metadata,
        },
        "children": children,
    }


def _convert_tabs(
    uid: str,
    raw: dict[str, Any],
    path: str,
    page_title: str,
    page_description: str,
    names: _NameCounter,
) -> dict[str, Any]:
    container_name = names.next("tabs")
    child_path = _child_path(path, container_name)

    data = raw.get("data", {})
    tab_blocks = data.get("blocks", {})
    tab_items = data.get("blocks_layout", {}).get("items", [])

    children: list[dict[str, Any]] = []
    for tab_uid in tab_items:
        tab_raw = tab_blocks.get(tab_uid)
        if tab_raw is None:
            continue

        tab_name = names.next("tab")
        inner_path = _child_path(child_path, tab_name)

        inner_blocks = tab_raw.get("blocks", {})
        inner_items = tab_raw.get("blocks_layout", {}).get("items", [])
        inner_children = _convert_blocks(
            inner_blocks, inner_items, inner_path, page_title, page_description, names
        )

        children.append(
            {
                "type": "tab",
                "id": tab_uid,
                "path": child_path,
                "name": tab_name,
                "attributes": {
                    "title": tab_raw.get("title", ""),
                },
                "children": inner_children,
            }
        )

    return {
        "type": "tabs",
        "id": uid,
        "path": path,
        "name": container_name,
        "attributes": {
            "title": raw.get("title", ""),
            "description": raw.get("description", ""),
            "variation": raw.get("variation", "default"),
            "hide_empty_tabs": raw.get("hideEmptyTabs", False),
        },
        "children": children,
    }


# ---------------------------------------------------------------------------
# Converter dispatch table
# ---------------------------------------------------------------------------

type _ConverterFn = Callable[
    [str, dict[str, Any], str, str, str, _NameCounter], dict[str, Any]
]

_CONVERTERS: dict[str, _ConverterFn] = {
    "title": _convert_title,
    "description": _convert_description,
    "slate": _convert_slate,
    "heading": _convert_heading,
    "introduction": _convert_slate,
    "html": _convert_html,
    "image": _convert_image,
    "dividerBlock": _convert_divider,
    "video": _convert_video,
    "__button": _convert_button,
    "teaser": _convert_teaser,
    "highlight": _convert_highlight,
    "slateTable": _convert_table,
    "slider": _convert_slider,
    "carousel": _convert_carousel,
    "columnsBlock": _convert_columns,
    "accordion": _convert_accordion,
    "quote": _convert_quote,
    "statistic_block": _convert_statistic,
    "form": _convert_form,
    "tabs_block": _convert_tabs,
}
