"""High-fidelity ASCII renderer for IR page layouts."""

from __future__ import annotations

import shutil
import textwrap
from html.parser import HTMLParser
from typing import Any

from .schema import PageState

# ─────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
    """Extract plain text from HTML, preserving block-level breaks."""

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("p", "h2", "h3", "li", "blockquote", "br", "tr"):
            if self._parts and not self._parts[-1].endswith("\n"):
                self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("p", "h2", "h3", "li", "blockquote"):
            if self._parts and not self._parts[-1].endswith("\n"):
                self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts).strip()


def _strip_html(html: str) -> str:
    p = _TextExtractor()
    p.feed(html)
    return p.get_text()


class _TableExtractor(HTMLParser):
    """Extract table rows/cells from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.header_flags: list[bool] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_thead = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "thead":
            self._in_thead = True
        elif tag == "tbody":
            self._in_thead = False
        elif tag == "tr":
            self._row = []
        elif tag in ("th", "td"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in ("th", "td") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self.header_flags.append(self._in_thead)
            self._row = None
        elif tag == "thead":
            self._in_thead = False

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


# ─────────────────────────────────────────────────────────
# Layout helpers
# ─────────────────────────────────────────────────────────


def _pad(text: str, width: int, align: str = "left") -> str:
    if len(text) > width:
        return text[:width]
    if align == "center":
        return text.center(width)
    if align == "right":
        return text.rjust(width)
    return text.ljust(width)


def _alignment(value: str | None, default: str = "left") -> str:
    if value in {"left", "center", "right"}:
        return value
    return default


def _width_for_preset(width: int, preset: str | None) -> tuple[int, str]:
    ratio = {
        "narrow": 0.62,
        "normal": 0.78,
        "wide": 0.92,
        "full": 1.0,
        "default": 1.0,
        None: 1.0,
    }.get(preset, 0.86)
    return max(24, min(width, int(width * ratio))), "center"


def _style_note(parts: list[str], width: int, indent: str = "  ") -> list[str]:
    parts = [part for part in parts if part]
    if not parts:
        return []
    return [_pad(indent + "(" + " · ".join(parts) + ")", width)]


def _wrap(text: str, width: int) -> list[str]:
    if not text.strip():
        return [""]
    lines: list[str] = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
        else:
            lines.extend(textwrap.wrap(para, width=width) or [""])
    return lines or [""]


def _merge_side_by_side(
    columns: list[list[str]], widths: list[int], gap: int = 1
) -> list[str]:
    """Merge column renders side by side."""
    if not columns:
        return []
    max_h = max(len(c) for c in columns)
    padded = []
    for col, w in zip(columns, widths):
        lines = col + [" " * w] * (max_h - len(col))
        padded.append([_pad(line, w) for line in lines])
    sep = " " * gap
    return [
        sep.join(padded[ci][ri] for ci in range(len(padded))) for ri in range(max_h)
    ]


# ─────────────────────────────────────────────────────────
# Block renderers
#
# Each returns list[str], every line exactly `w` characters.
# ─────────────────────────────────────────────────────────


def _render_block(block: dict[str, Any], w: int) -> list[str]:
    renderer = _RENDERERS.get(block.get("type", ""))
    if renderer is None:
        return [_pad(f"  [{block.get('type', '?')}]", w)]
    return renderer(block, w)


def _render_title(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"]["text"]
    if not text:
        return [" " * w]
    lines = [" " * w]
    for line in _wrap(text.upper(), w - 4):
        lines.append(_pad(line, w, "center"))
    lines.append(" " * w)
    return lines


def _render_description(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"]["text"]
    if not text:
        return [" " * w]
    m = 2
    return [_pad(" " * m + line, w) for line in _wrap(text, w - m * 2)]


def _render_heading(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"]["text"]
    level = b["attributes"]["level"]
    char = "━" if level == 2 else "─"
    prefix = char * 2 + " "
    suffix_w = max(0, w - len(prefix) - len(text) - 1)
    heading = prefix + text + " " + char * suffix_w
    return ["", _pad(heading, w), ""]


def _render_rich_text(b: dict[str, Any], w: int) -> list[str]:
    text = _strip_html(b["attributes"]["html"])
    content_width = b["attributes"].get("content_width", "default")
    inner_w, align = _width_for_preset(w, content_width)
    m = 2
    lines = [_pad(" " * m + line, inner_w) for line in _wrap(text, inner_w - m * 2)]
    return [_pad(line, w, align) for line in lines]


def _render_image(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    alt = attrs.get("alt_text", "")
    size = attrs.get("size", "large")
    alignment = attrs.get("alignment", "center")
    ratio = {"small": 0.3, "medium": 0.5, "large": 0.8}.get(size, 0.8)
    if alignment == "full":
        ratio = 1.0
    box_w = max(16, int(w * ratio))
    iw = box_w - 4
    label = f"[IMG] {alt}" if alt else "[IMG]"
    if len(label) > iw:
        label = label[: iw - 1] + "…"
    img = [
        "┌" + "─" * (box_w - 2) + "┐",
        "│ " + _pad("", iw) + " │",
        "│ " + _pad(label, iw, "center") + " │",
        "│ " + _pad("", iw) + " │",
        "└" + "─" * (box_w - 2) + "┘",
    ]
    al = "center" if alignment in ("center", "full") else alignment
    return [_pad(line, w, al) for line in img]


def _render_video(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    url = attrs.get("url", "")
    alignment = _alignment(attrs.get("alignment"), "center")
    box_w = max(16, int(w * 0.7))
    iw = box_w - 4
    short = url if len(url) <= iw else url[: iw - 1] + "…"
    vid = [
        "┌" + "─" * (box_w - 2) + "┐",
        "│ " + _pad("", iw) + " │",
        "│ " + _pad("▶  VIDEO", iw, "center") + " │",
    ]
    if url:
        vid.append("│ " + _pad(short, iw, "center") + " │")
    vid += [
        "│ " + _pad("", iw) + " │",
        "└" + "─" * (box_w - 2) + "┘",
    ]
    return [_pad(line, w, alignment) for line in vid]


def _render_button(b: dict[str, Any], w: int) -> list[str]:
    title = b["attributes"].get("title", "")
    align = b["attributes"].get("alignment", "center")
    btn = f"[ {title} ]"
    return ["", _pad(btn, w, align), ""]


def _render_divider(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"].get("text", "")
    hidden = b["attributes"].get("hidden", False)
    if hidden:
        return [" " * w]
    if text:
        half = max(2, (w - len(text) - 2) // 2)
        line = "─" * half + " " + text + " " + "─" * max(2, w - half - len(text) - 2)
    else:
        line = "─" * w
    return [_pad(line, w)]


def _render_teaser(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    align = _alignment(attrs.get("alignment"), "center")
    box_w = min(w, max(24, w - 4))
    iw = box_w - 4
    lines: list[str] = ["┌" + "─" * (box_w - 2) + "┐"]
    if attrs.get("preview_image"):
        lines.append("│ " + _pad("[image]", iw, "center") + " │")
        lines.append("│ " + " " * iw + " │")
    if attrs.get("eyebrow"):
        for tl in _wrap(attrs["eyebrow"], iw):
            lines.append("│ " + _pad(tl, iw) + " │")
    if attrs.get("title"):
        for tl in _wrap(attrs["title"], iw):
            lines.append("│ " + _pad(tl, iw) + " │")
    if attrs.get("description"):
        lines.append("│ " + " " * iw + " │")
        for dl in _wrap(attrs["description"], iw):
            lines.append("│ " + _pad(dl, iw) + " │")
    if attrs.get("show_button"):
        button = attrs.get("button_label") or "Mehr erfahren"
        style = attrs.get("button_style", "default")
        label = f"[ {button} ]" if style == "default" else f"[ {button} · {style} ]"
        lines.append("│ " + " " * iw + " │")
        for bl in _wrap(label, iw):
            lines.append("│ " + _pad(bl, iw, "center") + " │")
    if attrs.get("link"):
        arrow = "→ " + attrs["link"]
        if len(arrow) > iw:
            arrow = arrow[: iw - 1] + "…"
        lines.append("│ " + _pad(arrow, iw, "right") + " │")
    lines.append("└" + "─" * (box_w - 2) + "┘")
    return [_pad(line, w, align) for line in lines]


def _render_highlight(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    box_w = min(w, max(24, w - 2))
    iw = box_w - 4
    lines: list[str] = ["╔" + "═" * (box_w - 2) + "╗"]
    style_bits = []
    if attrs.get("background_color"):
        style_bits.append(f"background: {attrs['background_color']}")
    if attrs.get("image_url"):
        style_bits.append("image")
    for note in _style_note(style_bits, iw, ""):
        lines.append("║ " + _pad(note.strip(), iw, "center") + " ║")
        lines.append("║ " + " " * iw + " ║")
    if attrs.get("title"):
        for tl in _wrap(attrs["title"], iw):
            lines.append("║ " + _pad(tl, iw) + " ║")
    if attrs.get("html"):
        text = _strip_html(attrs["html"])
        lines.append("║ " + " " * iw + " ║")
        for tl in _wrap(text, iw):
            lines.append("║ " + _pad(tl, iw) + " ║")
    if attrs.get("show_button") and attrs.get("button_label"):
        lines.append("║ " + " " * iw + " ║")
        lines.append("║ " + _pad(f"[ {attrs['button_label']} ]", iw, "center") + " ║")
    lines.append("╚" + "═" * (box_w - 2) + "╝")
    return [_pad(line, w, "center") for line in lines]


def _render_table(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    html = attrs["html"]
    parser = _TableExtractor()
    parser.feed(html)
    rows = parser.rows
    header_flags = parser.header_flags

    if not rows:
        return [_pad("  (empty table)", w)]

    n_cols = max(len(r) for r in rows)
    col_w = [0] * n_cols
    for row in rows:
        for i, cell in enumerate(row):
            col_w[i] = max(col_w[i], len(cell))

    margin = 2
    available = w - margin * 2 - n_cols - 1
    total = sum(col_w)
    if total > available and total > 0:
        col_w = [max(3, int(c * available / total)) for c in col_w]

    def sep(left: str, mid: str, right: str, fill: str = "─") -> str:
        return left + mid.join(fill * cw for cw in col_w) + right

    def row_line(cells: list[str], hdr: bool) -> str:
        parts = []
        for i in range(n_cols):
            cell = cells[i] if i < len(cells) else ""
            cw = col_w[i] if i < len(col_w) else 3
            if len(cell) > cw:
                cell = cell[: cw - 1] + "…"
            parts.append(_pad(cell, cw, "center" if hdr else "left"))
        return "│" + "│".join(parts) + "│"

    style_bits = [
        name
        for name, enabled in (
            ("minimal", attrs.get("minimal_style")),
            ("compact", attrs.get("compact")),
            ("dark", attrs.get("dark_background")),
            ("striped", attrs.get("striped_rows")),
            ("fixed-width", attrs.get("fixed_column_width")),
            ("no-headers", attrs.get("hide_headers")),
        )
        if enabled
    ]
    out: list[str] = _style_note(style_bits, available + margin, "")
    out.append(sep("┌", "┬", "┐"))
    for ri, row in enumerate(rows):
        is_h = header_flags[ri] if ri < len(header_flags) else False
        out.append(row_line(row, is_h))
        if is_h and ri + 1 < len(rows):
            out.append(sep("╞", "╪", "╡", "═"))
        elif ri < len(rows) - 1:
            out.append(sep("├", "┼", "┤"))
    out.append(sep("└", "┴", "┘"))
    return [_pad(" " * margin + line, w) for line in out]


def _render_listing(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    lines: list[str] = []
    variant = attrs.get("display_variant", "standard")

    if attrs.get("heading"):
        level = attrs.get("heading_level", 2)
        prefix = "## " if level == 2 else "### "
        heading = prefix + str(attrs["heading"])
        lines.append(_pad("  " + heading, w))
    if variant != "standard":
        lines.extend(_style_note([f"listing: {variant}"], w))
    if attrs.get("heading") or variant != "standard":
        lines.append("")

    if not children:
        query = attrs.get("query", {})
        filters = query.get("filters", []) if isinstance(query, dict) else []
        summary_parts: list[str] = []
        for raw_filter in filters:
            if not isinstance(raw_filter, dict):
                continue
            if raw_filter.get("type") == "content_type":
                summary_parts.extend(
                    str(v) for v in raw_filter.get("content_types", [])
                )
            elif raw_filter.get("type") == "path":
                summary_parts.extend(str(v) for v in raw_filter.get("paths", []))
        summary = ", ".join(summary_parts) if summary_parts else "no resolved results"
        return lines + [_pad(f"  (listing: {summary})", w)]

    if variant in {"two_column_grid", "text_card_grid", "visual_card_grid"}:
        return lines + _render_listing_grid(
            children, w, visual=variant == "visual_card_grid"
        )

    if variant == "horizontal_list":
        return lines + _render_listing_horizontal(children, w)

    for child in children:
        item = child["attributes"]
        title = str(item.get("title", "")).strip() or "(untitled)"
        content_path = str(item.get("content_path", "")).strip()
        published = str(item.get("published", "")).strip()
        bullet = "◆" if variant == "event_list" else "•"
        line = f"  {bullet} {title}"
        if published:
            line += f" · {published[:10]}"
        lines.extend(_pad(rendered, w) for rendered in _wrap(line, w))

        if content_path:
            for rendered in _wrap("→ " + content_path, w - 6):
                lines.append(_pad("      " + rendered, w))

        description = str(item.get("description", "")).strip()
        show_description = variant in {
            "standard",
            "summary_list",
            "news_list",
            "event_list",
        }
        if description and show_description:
            for rendered in _wrap(description, w - 6):
                lines.append(_pad("      " + rendered, w))
        if variant in {"news_list", "event_list"}:
            lines.append("")

    return lines


def _listing_item_card(item: dict[str, Any], width: int, visual: bool) -> list[str]:
    iw = width - 4
    title = str(item.get("title", "")).strip() or "(untitled)"
    path = str(item.get("content_path", "")).strip()
    description = str(item.get("description", "")).strip()
    published = str(item.get("published", "")).strip()
    lines = ["┌" + "─" * (width - 2) + "┐"]
    if visual:
        lines.append("│ " + _pad("[image]", iw, "center") + " │")
        lines.append("│ " + " " * iw + " │")
    for rendered in _wrap(title, iw):
        lines.append("│ " + _pad(rendered, iw) + " │")
    meta = published[:10] if published else ""
    if meta:
        lines.append("│ " + _pad(meta, iw) + " │")
    if description:
        lines.append("│ " + " " * iw + " │")
        for rendered in _wrap(description, iw):
            lines.append("│ " + _pad(rendered, iw) + " │")
    if path:
        lines.append("│ " + _pad("→ " + path, iw, "right") + " │")
    lines.append("└" + "─" * (width - 2) + "┘")
    return lines


def _render_listing_grid(
    children: list[dict[str, Any]], w: int, *, visual: bool
) -> list[str]:
    gap = 1
    cols = 2 if w >= 72 and len(children) > 1 else 1
    item_w = (w - gap * (cols - 1)) // cols
    lines: list[str] = []
    for start in range(0, len(children), cols):
        row = children[start : start + cols]
        cards = [
            _listing_item_card(child["attributes"], item_w, visual) for child in row
        ]
        widths = [item_w] * len(cards)
        lines.extend(_merge_side_by_side(cards, widths, gap))
        if start + cols < len(children):
            lines.append("")
    return lines


def _render_listing_horizontal(children: list[dict[str, Any]], w: int) -> list[str]:
    gap = 1
    cols = min(3 if w >= 96 else 2, max(1, len(children)))
    item_w = (w - gap * (cols - 1)) // cols
    cards = []
    for child in children[:cols]:
        item = child["attributes"]
        title = str(item.get("title", "")).strip() or "(untitled)"
        path = str(item.get("content_path", "")).strip()
        card = [_pad(title, item_w)]
        if path:
            card.append(_pad("→ " + path, item_w))
        cards.append(card)
    lines = _merge_side_by_side(cards, [item_w] * len(cards), gap)
    if len(children) > cols:
        lines.append(_pad(f"  (+{len(children) - cols} more)", w))
    return lines


def _render_columns(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    if not children:
        return [_pad("(empty columns)", w, "center")]

    total_units = sum(c["attributes"]["width"] for c in children)
    gap = 1
    available = w - (len(children) - 1) * gap

    col_widths: list[int] = []
    remaining = available
    for i, child in enumerate(children):
        if i == len(children) - 1:
            cw = remaining
        else:
            cw = int(available * child["attributes"]["width"] / total_units)
        col_widths.append(max(8, cw))
        remaining -= col_widths[-1]

    # Render each column's content
    col_contents: list[list[str]] = []
    for i, child in enumerate(children):
        iw = col_widths[i] - 2
        content: list[str] = []
        for j, inner_block in enumerate(child.get("children", [])):
            if j > 0:
                content.append(" " * iw)
            content.extend(_render_block(inner_block, iw))
        if not content:
            content = [_pad("(empty)", iw, "center")]
        col_contents.append(content)

    max_h = max(len(c) for c in col_contents)

    # Build bordered columns padded to same height
    boxed: list[list[str]] = []
    for i, content in enumerate(col_contents):
        cw = col_widths[i]
        iw = cw - 2
        box: list[str] = ["┌" + "─" * iw + "┐"]
        for line in content:
            box.append("│" + _pad(line, iw) + "│")
        for _ in range(max_h - len(content)):
            box.append("│" + " " * iw + "│")
        box.append("└" + "─" * iw + "┘")
        boxed.append(box)

    merged = _merge_side_by_side(boxed, col_widths, gap)
    if attrs.get("reverse_stack_order"):
        merged = _style_note(["reverse stack order on narrow screens"], w) + merged
    return [_pad(line, w) for line in merged]


def _render_slider(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    lines: list[str] = []

    if attrs.get("show_arrows", True):
        nav = "◀ " + "─" * (w - 4) + " ▶"
        lines.append(_pad(nav, w))
    style_bits = []
    if attrs.get("autoplay"):
        style_bits.append(
            f"autoplay {attrs.get('autoplay_transition', 'slide')} "
            f"{attrs.get('autoplay_delay_ms', 0)}ms"
        )
    lines.extend(_style_note(style_bits, w))

    if children:
        slide = children[0]
        sa = slide["attributes"]
        box_w = w - 4
        iw = box_w - 4
        lines.append(_pad("  ┌" + "─" * (box_w - 2) + "┐", w))
        if sa.get("eyebrow"):
            lines.append(_pad("  │ " + _pad(sa["eyebrow"], iw) + " │", w))
        if sa.get("title"):
            for tl in _wrap(sa["title"], iw):
                lines.append(_pad("  │ " + _pad(tl, iw) + " │", w))
        if sa.get("description"):
            for dl in _wrap(sa["description"], iw):
                lines.append(_pad("  │ " + _pad(dl, iw) + " │", w))
        lines.append(_pad("  └" + "─" * (box_w - 2) + "┘", w))

    if len(children) > 1:
        dots = "● " + "○ " * (len(children) - 1)
        lines.append(_pad(dots.strip(), w, "center"))

    return lines


def _render_carousel(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    visible = min(attrs.get("visible_items", 3), max(len(children), 1))

    lines: list[str] = []
    if attrs.get("heading"):
        lines.append(_pad("  " + attrs["heading"], w))
    lines.extend(
        _style_note(
            [
                f"{visible} visible",
                (
                    "descriptions hidden"
                    if not attrs.get("show_descriptions", True)
                    else ""
                ),
            ],
            w,
        )
    )
    if attrs.get("heading") or not attrs.get("show_descriptions", True):
        lines.append("")

    shown = children[:visible]
    if not shown:
        return lines + [_pad("  (empty carousel)", w)]

    gap = 1
    available = w - (len(shown) - 1) * gap
    item_w = available // len(shown)

    item_renders: list[list[str]] = []
    item_widths: list[int] = []
    for i, item in enumerate(shown):
        iw = item_w if i < len(shown) - 1 else available - item_w * (len(shown) - 1)
        inner = iw - 4
        ia = item["attributes"]
        card: list[str] = ["┌" + "─" * (iw - 2) + "┐"]
        if ia.get("title"):
            for tl in _wrap(ia["title"], inner):
                card.append("│ " + _pad(tl, inner) + " │")
        if ia.get("description") and attrs.get("show_descriptions", True):
            for dl in _wrap(ia["description"], inner):
                card.append("│ " + _pad(dl, inner) + " │")
        if not ia.get("title") and not (
            ia.get("description") and attrs.get("show_descriptions", True)
        ):
            card.append("│ " + _pad("(empty)", inner, "center") + " │")
        card.append("└" + "─" * (iw - 2) + "┘")
        item_renders.append(card)
        item_widths.append(iw)

    merged = _merge_side_by_side(item_renders, item_widths, gap)
    lines.extend(_pad(line, w) for line in merged)

    if len(children) > visible:
        lines.append(_pad(f"  (+{len(children) - visible} more)", w))

    return lines


def _render_accordion(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    inner_w, content_align = _width_for_preset(
        w - 6, attrs.get("content_width", "default")
    )
    heading_align = _alignment(attrs.get("heading_alignment"), "left")

    lines: list[str] = []
    if attrs.get("heading"):
        prefix = "## " if attrs.get("heading_level", 2) == 2 else "### "
        lines.append(_pad(prefix + attrs["heading"], w, heading_align))
    if attrs.get("title"):
        lines.append(_pad(attrs["title"], w, heading_align))
    notes = [
        f"arrows {attrs.get('arrow_position', 'right')}",
        "filter" if attrs.get("show_filter") else "",
        "single panel" if attrs.get("single_panel_open") else "multiple panels",
    ]
    lines.extend(_style_note(notes, w))
    if attrs.get("heading") or attrs.get("title") or notes:
        lines.append("")

    start_collapsed = attrs.get("start_collapsed", True)
    for i, panel in enumerate(children):
        pa = panel["attributes"]
        # When not collapsed, show first panel expanded (single-panel mode)
        # or all panels expanded (multi-panel mode)
        single_panel_open = attrs.get("single_panel_open", False)
        if start_collapsed:
            is_open = False
        elif single_panel_open:
            is_open = i == 0
        else:
            is_open = True
        icon = "▼" if is_open else "▶"
        title = pa.get("title", "")
        if attrs.get("arrow_position") == "right":
            header = f"  {title} {icon}"
        else:
            header = f"  {icon} {title}"
        lines.append(_pad(header, w))
        if is_open:
            for inner_block in panel.get("children", []):
                for bl in _render_block(inner_block, inner_w):
                    lines.append(_pad("  │ " + _pad(bl, inner_w), w, content_align))
            lines.append(_pad("  │", w))

    return lines


def _render_quote(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    box_w = min(w, max(24, w - 4))
    iw = box_w - 4
    align = _alignment(attrs.get("alignment"), "center")
    border = "┃" if attrs.get("display_variant") == "testimonial" else "│"
    lines: list[str] = [border + " " + " " * iw + " " + border]
    if attrs.get("display_variant") == "testimonial" and attrs.get("image_url"):
        lines.append(border + " " + _pad("[portrait]", iw, "center") + " " + border)
        lines.append(border + " " + " " * iw + " " + border)
    source = _strip_html(attrs.get("attribution_html", ""))
    role = _strip_html(attrs.get("role_html", ""))
    if attrs.get("attribution_first") and source:
        lines.append(border + " " + _pad(f"— {source}", iw, "right") + " " + border)
        if role:
            lines.append(border + " " + _pad(role, iw, "right") + " " + border)
        lines.append(border + " " + " " * iw + " " + border)
    quote_text = _strip_html(attrs.get("html", ""))
    if quote_text:
        for ql in _wrap(f"\u201c{quote_text}\u201d", iw):
            lines.append(border + " " + _pad(ql, iw) + " " + border)
    if source and not attrs.get("attribution_first"):
        lines.append(border + " " + " " * iw + " " + border)
        lines.append(border + " " + _pad(f"— {source}", iw, "right") + " " + border)
        if role:
            lines.append(border + " " + _pad(role, iw, "right") + " " + border)
    extra = _strip_html(attrs.get("context_html", ""))
    if extra:
        for el in _wrap(extra, iw):
            lines.append(border + " " + _pad(el, iw, "right") + " " + border)
    lines.append(border + " " + " " * iw + " " + border)
    return [_pad(line, w, align) for line in lines]


def _render_statistic(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    if not children:
        return [_pad("  (empty statistic)", w)]

    horizontal = attrs.get("horizontal_layout", False)
    items_per_row = max(1, min(int(attrs.get("items_per_row", 1)), 4))
    value_style = {
        "mini": str.lower,
        "tiny": str.lower,
        "small": lambda text: text,
        "large": str.upper,
        "huge": str.upper,
    }.get(attrs.get("size", "small"), lambda text: text)

    if horizontal:
        gap = 2
        lines: list[str] = []
        for start in range(0, len(children), items_per_row):
            row = children[start : start + items_per_row]
            n = len(row)
            item_w = max(10, (w - (n - 1) * gap) // n)
            item_renders: list[list[str]] = []
            item_widths: list[int] = []
            for i, child in enumerate(row):
                ca = child["attributes"]
                iw = item_w if i < n - 1 else w - item_w * (n - 1) - (n - 1) * gap
                card: list[str] = []
                value = str(ca.get("value", ""))
                card.append(_pad(value_style(value), iw, "center"))
                if ca.get("label"):
                    card.append(_pad(ca["label"], iw, "center"))
                if ca.get("info"):
                    card.append(_pad(ca["info"], iw, "center"))
                item_renders.append(card)
                item_widths.append(iw)
            lines.extend(_merge_side_by_side(item_renders, item_widths, gap))
            if start + items_per_row < len(children):
                lines.append("")
    else:
        lines = []
        for child in children:
            ca = child["attributes"]
            lines.append(_pad(value_style(str(ca.get("value", ""))), w, "center"))
            if ca.get("label"):
                lines.append(_pad(ca["label"], w, "center"))
            if ca.get("info"):
                lines.append(_pad(ca["info"], w, "center"))
            lines.append("")
    if attrs.get("dark_background"):
        return ["▓" * w, *lines, "▓" * w]
    return lines


def _render_form(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    box_w = min(w, max(30, w - 4))
    iw = box_w - 4
    heading_align = _alignment(attrs.get("heading_alignment"), "left")

    lines: list[str] = ["┌" + "─" * (box_w - 2) + "┐"]
    if attrs.get("title"):
        for tl in _wrap(attrs["title"], iw):
            lines.append("│ " + _pad(tl, iw, heading_align) + " │")
    if attrs.get("description"):
        for dl in _wrap(attrs["description"], iw):
            lines.append("│ " + _pad(dl, iw, heading_align) + " │")
        lines.append("│ " + " " * iw + " │")

    for child in children:
        ct = child.get("type", "")
        if ct == "rich_text":
            text = _strip_html(child["attributes"].get("html", ""))
            if text:
                for tl in _wrap(text, iw):
                    lines.append("│ " + _pad(tl, iw) + " │")
        elif ct.startswith("form_"):
            ca = child["attributes"]
            label = ca.get("label", "")
            req = " *" if ca.get("required") else ""
            input_type = ca.get("input_type", "text")
            lines.append("│ " + _pad(f"{label}{req} ({input_type}):", iw) + " │")
            if ca.get("description"):
                for dl in _wrap(ca["description"], iw):
                    lines.append("│ " + _pad("  " + dl, iw) + " │")
            if ca.get("show_when"):
                rules = [
                    f"{rule.get('field_id')} {rule.get('operator')}"
                    + (
                        f" {rule.get('expected_value')}"
                        if rule.get("expected_value")
                        else ""
                    )
                    for rule in ca["show_when"]
                ]
                for dl in _wrap("shown when " + "; ".join(rules), iw):
                    lines.append("│ " + _pad("  " + dl, iw) + " │")
            if input_type == "textarea":
                shape = "[" + "·" * (min(iw, 30) - 2) + "]"
                lines.append("│ " + _pad(shape, iw) + " │")
                lines.append("│ " + _pad(shape, iw) + " │")
            elif input_type in {"select", "radio", "checkbox"}:
                options = ca.get("options", [])
                prefix = "☑" if input_type == "checkbox" else "○"
                for option in options[:4]:
                    lines.append("│ " + _pad(f"{prefix} {option}", iw) + " │")
            elif input_type == "attachment":
                lines.append("│ " + _pad("[ Choose file ]", iw) + " │")
            else:
                field_w = min(iw, 30)
                lines.append("│ " + _pad("[" + "·" * (field_w - 2) + "]", iw) + " │")
        lines.append("│ " + " " * iw + " │")

    btn_label = attrs.get("submit_button_label", "Submit")
    buttons = f"[ {btn_label} ]"
    if attrs.get("show_cancel_button") and attrs.get("cancel_button_label"):
        buttons += f"  [ {attrs['cancel_button_label']} ]"
    lines.append("│ " + _pad(buttons, iw, "center") + " │")
    lines.append("└" + "─" * (box_w - 2) + "┘")
    return [_pad(line, w, "center") for line in lines]


def _render_tabs(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])

    lines: list[str] = []
    if attrs.get("title"):
        lines.append(_pad("  " + attrs["title"], w))
    if attrs.get("description"):
        for line in _wrap(attrs["description"], w - 4):
            lines.append(_pad("  " + line, w))
    lines.extend(
        _style_note(
            [
                f"tabs: {attrs.get('display_variant', 'standard')}",
                "empty tabs hidden" if not attrs.get("show_empty_tabs", True) else "",
            ],
            w,
        )
    )

    tab_titles = [c["attributes"].get("title", "?") for c in children]
    variant = attrs.get("display_variant", "standard")
    if variant == "accordion":
        bar = "  ".join(f"▸ {t}" for t in tab_titles) if tab_titles else "(no tabs)"
    elif variant in {"horizontal_carousel", "vertical_carousel"}:
        bar = "‹ " + "  ".join(f"[{t}]" for t in tab_titles) + " ›"
    else:
        bar = "  ".join(f"[{t}]" for t in tab_titles) if tab_titles else "(no tabs)"
    lines.append(_pad(bar, w))
    lines.append("─" * w)

    # Show first tab's content
    if children:
        first = children[0]
        inner_w = w - 4
        for inner_block in first.get("children", []):
            for bl in _render_block(inner_block, inner_w):
                lines.append(_pad("  " + bl, w))
        if not first.get("children"):
            lines.append(_pad("  (empty)", w))

    return lines


# ─────────────────────────────────────────────────────────
# Dispatch table
# ─────────────────────────────────────────────────────────

_RENDERERS: dict[str, Any] = {
    "title": _render_title,
    "description": _render_description,
    "heading": _render_heading,
    "rich_text": _render_rich_text,
    "image": _render_image,
    "video": _render_video,
    "button": _render_button,
    "divider": _render_divider,
    "teaser": _render_teaser,
    "highlight": _render_highlight,
    "table": _render_table,
    "listing": _render_listing,
    "columns": _render_columns,
    "slider": _render_slider,
    "carousel": _render_carousel,
    "accordion": _render_accordion,
    "quote": _render_quote,
    "statistic": _render_statistic,
    "form": _render_form,
    "tabs": _render_tabs,
}


# ─────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────


def render_page(page_state: PageState, width: int | None = None) -> str:
    """Render a PageState as high-fidelity ASCII art.

    If *width* is None, uses the current terminal width (capped at 120).
    """
    if width is None:
        width = min(shutil.get_terminal_size().columns, 120)

    metadata = page_state.metadata
    blocks = [block.model_dump() for block in page_state.layout.root]

    lines: list[str] = []

    # Header
    title = metadata.title or "Untitled"
    header = f"─── {title} "
    header += "─" * max(0, width - len(header))
    lines.append(header)
    if metadata.description:
        for dl in _wrap(metadata.description, width - 4):
            lines.append(_pad("  " + dl, width))
    lines.append("")

    # Blocks
    for i, block in enumerate(blocks):
        lines.extend(_render_block(block, width))
        if i < len(blocks) - 1:
            lines.append("")

    # Footer
    lines.append("")
    lines.append("─" * width)

    return "\n".join(lines)
