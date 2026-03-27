"""ASCII renderer for IR page layouts with ANSI color support."""

from __future__ import annotations

import os
import re
import shutil
import textwrap
from html.parser import HTMLParser
from typing import Any

# ─────────────────────────────────────────────────────────
# ANSI styling
# ─────────────────────────────────────────────────────────

_NO_COLOR = bool(os.environ.get("NO_COLOR"))
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _s(code: str) -> str:
    """Return ANSI code, or empty string if NO_COLOR is set."""
    return "" if _NO_COLOR else code


_R = _s("\033[0m")
_B = _s("\033[1m")
_D = _s("\033[2m")
_I = _s("\033[3m")
_CYAN = _s("\033[36m")
_YLW = _s("\033[33m")
_WHT = _s("\033[97m")


def _visible_len(text: str) -> int:
    """Length of text excluding ANSI escape sequences."""
    if _NO_COLOR or "\033" not in text:
        return len(text)
    return len(_ANSI_RE.sub("", text))


# ─────────────────────────────────────────────────────────
# HTML helpers
# ─────────────────────────────────────────────────────────


class _TextExtractor(HTMLParser):
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

_MARGIN = 2


def _pad(text: str, width: int, align: str = "left") -> str:
    if not text:
        return " " * width
    if _NO_COLOR or "\033" not in text:
        vis = len(text)
        if vis > width:
            return text[:width]
    else:
        plain = _ANSI_RE.sub("", text)
        vis = len(plain)
        if vis > width:
            return plain[:width]
    pad_n = width - vis
    if align == "center":
        left = pad_n // 2
        return " " * left + text + " " * (pad_n - left)
    if align == "right":
        return " " * pad_n + text
    return text + " " * pad_n


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


def _bdr(
    left: str, content: str, right: str, iw: int, align: str = "left", color: str = ""
) -> str:
    """Bordered line: │ content │ with colored borders."""
    c = color or _D
    return f"{c}{left}{_R} {_pad(content, iw, align)} {c}{right}{_R}"


def _box_top(w: int, char: str = "─", color: str = "") -> str:
    c = color or _D
    return f"{c}┌{char * (w - 2)}┐{_R}"


def _box_bottom(w: int, char: str = "─", color: str = "") -> str:
    c = color or _D
    return f"{c}└{char * (w - 2)}┘{_R}"


def _merge_side_by_side(
    columns: list[list[str]],
    widths: list[int],
    gap: int = 1,
) -> list[str]:
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
# ─────────────────────────────────────────────────────────


def _render_block(block: dict[str, Any], w: int) -> list[str]:
    renderer = _RENDERERS.get(block.get("type", ""))
    if renderer is None:
        return [_pad(f"  {_D}[{block.get('type', '?')}]{_R}", w)]
    return renderer(block, w)


def _render_title(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"]["text"]
    if not text:
        return [" " * w]
    lines = [""]
    for line in _wrap(text.upper(), w - 4):
        lines.append(_pad(f"{_B}{_WHT}{line}{_R}", w, "center"))
    lines.append("")
    return lines


def _render_description(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"]["text"]
    if not text:
        return [" " * w]
    m = 2
    return [_pad(f"  {_D}{_I}{line}{_R}", w) for line in _wrap(text, w - m * 2)]


def _render_heading(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"]["text"]
    level = b["attributes"]["level"]
    char = "━" if level == 2 else "─"
    prefix = char * 2 + " "
    suffix_w = max(0, w - len(prefix) - len(text) - 1)
    heading = f"{_D}{prefix}{_R}{_B}{text}{_R} {_D}{char * suffix_w}{_R}"
    return ["", _pad(heading, w), ""]


def _render_rich_text(b: dict[str, Any], w: int) -> list[str]:
    text = _strip_html(b["attributes"]["html"])
    m = 2
    return [_pad(" " * m + line, w) for line in _wrap(text, w - m * 2)]


def _render_image(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    alt = attrs.get("alt_text", "")
    size = attrs.get("size", "l")
    alignment = attrs.get("alignment", "center")
    ratio = {"s": 0.3, "m": 0.5, "l": 0.8}.get(size, 0.8)
    if alignment == "full":
        ratio = 1.0
    box_w = max(16, int(w * ratio))
    iw = box_w - 4
    label = f"[IMG] {alt}" if alt else "[IMG]"
    if len(label) > iw:
        label = label[: iw - 1] + "…"
    img = [
        _box_top(box_w),
        _bdr("│", "", "│", iw),
        _bdr("│", f"{_D}{label}{_R}", "│", iw, "center"),
        _bdr("│", "", "│", iw),
        _box_bottom(box_w),
    ]
    al = "center" if alignment in ("center", "full") else alignment
    return [_pad(line, w, al) for line in img]


def _render_video(b: dict[str, Any], w: int) -> list[str]:
    url = b["attributes"].get("url", "")
    box_w = max(16, int(w * 0.7))
    iw = box_w - 4
    short = url if len(url) <= iw else url[: iw - 1] + "…"
    vid = [
        _box_top(box_w),
        _bdr("│", "", "│", iw),
        _bdr("│", f"{_B}▶  VIDEO{_R}", "│", iw, "center"),
    ]
    if url:
        vid.append(_bdr("│", f"{_CYAN}{short}{_R}", "│", iw, "center"))
    vid += [
        _bdr("│", "", "│", iw),
        _box_bottom(box_w),
    ]
    return [_pad(line, w, "center") for line in vid]


def _render_button(b: dict[str, Any], w: int) -> list[str]:
    title = b["attributes"].get("title", "")
    align = b["attributes"].get("inner_alignment", "center")
    btn = f"{_B}[ {title} ]{_R}"
    return ["", _pad(btn, w, align), ""]


def _render_divider(b: dict[str, Any], w: int) -> list[str]:
    text = b["attributes"].get("text", "")
    if text:
        half = max(2, (w - len(text) - 2) // 2)
        rest = max(2, w - half - len(text) - 2)
        line = f"{_D}{'─' * half}{_R} {_B}{text}{_R} {_D}{'─' * rest}{_R}"
    else:
        line = f"{_D}{'─' * w}{_R}"
    return [_pad(line, w)]


def _render_teaser(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    box_w = min(w, max(24, w - 4))
    iw = box_w - 4
    lines: list[str] = [_box_top(box_w)]
    if attrs.get("head_title"):
        for tl in _wrap(attrs["head_title"], iw):
            lines.append(_bdr("│", f"{_D}{tl}{_R}", "│", iw))
    if attrs.get("title"):
        for tl in _wrap(attrs["title"], iw):
            lines.append(_bdr("│", f"{_B}{tl}{_R}", "│", iw))
    if attrs.get("description"):
        lines.append(_bdr("│", "", "│", iw))
        for dl in _wrap(attrs["description"], iw):
            lines.append(_bdr("│", dl, "│", iw))
    if attrs.get("link"):
        arrow = f"→ {attrs['link']}"
        if len(arrow) > iw:
            arrow = arrow[: iw - 1] + "…"
        lines.append(_bdr("│", f"{_CYAN}{arrow}{_R}", "│", iw, "right"))
    lines.append(_box_bottom(box_w))
    return [_pad(line, w, "center") for line in lines]


def _render_highlight(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    box_w = min(w, max(24, w - 2))
    iw = box_w - 4
    y = _YLW
    lines: list[str] = [f"{y}╔{'═' * (box_w - 2)}╗{_R}"]
    if attrs.get("title"):
        for tl in _wrap(attrs["title"], iw):
            lines.append(_bdr("║", f"{_B}{tl}{_R}", "║", iw, color=y))
    if attrs.get("html"):
        text = _strip_html(attrs["html"])
        lines.append(_bdr("║", "", "║", iw, color=y))
        for tl in _wrap(text, iw):
            lines.append(_bdr("║", tl, "║", iw, color=y))
    if attrs.get("button_text"):
        lines.append(_bdr("║", "", "║", iw, color=y))
        btn = f"{_B}[ {attrs['button_text']} ]{_R}"
        lines.append(_bdr("║", btn, "║", iw, "center", color=y))
    lines.append(f"{y}╚{'═' * (box_w - 2)}╝{_R}")
    return [_pad(line, w, "center") for line in lines]


def _render_table(b: dict[str, Any], w: int) -> list[str]:
    html = b["attributes"]["html"]
    parser = _TableExtractor()
    parser.feed(html)
    rows = parser.rows
    header_flags = parser.header_flags

    if not rows:
        return [_pad(f"  {_D}(empty table){_R}", w)]

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
        inner = mid.join(fill * cw for cw in col_w)
        return f"{_D}{left}{inner}{right}{_R}"

    def row_line(cells: list[str], hdr: bool) -> str:
        parts: list[str] = []
        for i in range(n_cols):
            cell = cells[i] if i < len(cells) else ""
            cw = col_w[i] if i < len(col_w) else 3
            if len(cell) > cw:
                cell = cell[: cw - 1] + "…"
            if hdr:
                parts.append(f"{_B}{_pad(cell, cw, 'center')}{_R}")
            else:
                parts.append(_pad(cell, cw))
        delim = f"{_D}│{_R}"
        return delim + delim.join(parts) + delim

    out: list[str] = [sep("┌", "┬", "┐")]
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
    headline = attrs.get("headline", "")
    if headline:
        lines.append(_pad(f"  {_B}{headline}{_R}", w))
        lines.append("")

    if not children:
        lines.append(_pad(f"  {_D}(no items){_R}", w))
        return lines

    for item in children:
        ia = item["attributes"]
        title = ia.get("title", "")
        desc = ia.get("description", "")
        path = ia.get("content_path", "")
        suffix = f"  {_CYAN}→ {path}{_R}" if path else ""
        title_line = f"  • {_B}{title}{_R}{suffix}"
        if _visible_len(title_line) > w:
            plain = f"  • {title}  → {path}"
            title_line = plain[: w - 1] + "…"
        lines.append(_pad(title_line, w))
        if desc:
            for dl in _wrap(desc, w - 6):
                lines.append(_pad(f"    {_D}{dl}{_R}", w))

    return lines


def _render_columns(b: dict[str, Any], w: int) -> list[str]:
    children = b.get("children", [])
    if not children:
        return [_pad(f"{_D}(empty columns){_R}", w, "center")]

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

    col_contents: list[list[str]] = []
    for i, child in enumerate(children):
        iw = col_widths[i] - 2
        content: list[str] = []
        for j, inner_block in enumerate(child.get("children", [])):
            if j > 0:
                content.append(" " * iw)
            content.extend(_render_block(inner_block, iw))
        if not content:
            content = [_pad(f"{_D}(empty){_R}", iw, "center")]
        col_contents.append(content)

    max_h = max(len(c) for c in col_contents)

    boxed: list[list[str]] = []
    for i, content in enumerate(col_contents):
        cw = col_widths[i]
        iw = cw - 2
        box: list[str] = [f"{_D}┌{'─' * iw}┐{_R}"]
        for line in content:
            box.append(f"{_D}│{_R}{_pad(line, iw)}{_D}│{_R}")
        for _ in range(max_h - len(content)):
            box.append(f"{_D}│{_R}{' ' * iw}{_D}│{_R}")
        box.append(f"{_D}└{'─' * iw}┘{_R}")
        boxed.append(box)

    merged = _merge_side_by_side(boxed, col_widths, gap)
    return [_pad(line, w) for line in merged]


def _render_slider(b: dict[str, Any], w: int) -> list[str]:
    children = b.get("children", [])
    lines: list[str] = []

    nav = f"{_D}◀ {'─' * (w - 4)} ▶{_R}"
    lines.append(_pad(nav, w))

    if children:
        slide = children[0]
        sa = slide["attributes"]
        box_w = w - 4
        iw = box_w - 4
        lines.append(_pad(f"  {_D}┌{'─' * (box_w - 2)}┐{_R}", w))
        if sa.get("head_title"):
            ht = sa["head_title"]
            lines.append(_pad(f"  {_D}│{_R} {_pad(f'{_D}{ht}{_R}', iw)} {_D}│{_R}", w))
        if sa.get("title"):
            for tl in _wrap(sa["title"], iw):
                lines.append(
                    _pad(f"  {_D}│{_R} {_pad(f'{_B}{tl}{_R}', iw)} {_D}│{_R}", w)
                )
        if sa.get("description"):
            for dl in _wrap(sa["description"], iw):
                lines.append(_pad(f"  {_D}│{_R} {_pad(dl, iw)} {_D}│{_R}", w))
        lines.append(_pad(f"  {_D}└{'─' * (box_w - 2)}┘{_R}", w))

    if len(children) > 1:
        dots = f"{_B}●{_R} " + f"{_D}○{_R} " * (len(children) - 1)
        lines.append(_pad(dots.strip(), w, "center"))

    return lines


def _render_carousel(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    visible = min(attrs.get("visible_items", 3), max(len(children), 1))

    lines: list[str] = []
    if attrs.get("headline"):
        lines.append(_pad(f"  {_B}{attrs['headline']}{_R}", w))
        lines.append("")

    shown = children[:visible]
    if not shown:
        return lines + [_pad(f"  {_D}(empty carousel){_R}", w)]

    gap = 1
    available = w - (len(shown) - 1) * gap
    item_w = available // len(shown)

    item_renders: list[list[str]] = []
    item_widths: list[int] = []
    for i, item in enumerate(shown):
        iw = item_w if i < len(shown) - 1 else available - item_w * (len(shown) - 1)
        inner = iw - 4
        ia = item["attributes"]
        card: list[str] = [_box_top(iw)]
        if ia.get("title"):
            for tl in _wrap(ia["title"], inner):
                card.append(_bdr("│", f"{_B}{tl}{_R}", "│", inner))
        if ia.get("description") and not attrs.get("hide_description"):
            for dl in _wrap(ia["description"], inner):
                card.append(_bdr("│", dl, "│", inner))
        if not ia.get("title") and not (
            ia.get("description") and not attrs.get("hide_description")
        ):
            card.append(_bdr("│", f"{_D}(empty){_R}", "│", inner, "center"))
        card.append(_box_bottom(iw))
        item_renders.append(card)
        item_widths.append(iw)

    merged = _merge_side_by_side(item_renders, item_widths, gap)
    lines.extend(_pad(line, w) for line in merged)

    if len(children) > visible:
        lines.append(_pad(f"  {_D}(+{len(children) - visible} more){_R}", w))

    return lines


def _render_accordion(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])

    lines: list[str] = []
    if attrs.get("headline"):
        lines.append(_pad(f"  {_B}{attrs['headline']}{_R}", w))
    if attrs.get("title"):
        lines.append(_pad(f"  {_B}{attrs['title']}{_R}", w))
    if attrs.get("headline") or attrs.get("title"):
        lines.append("")

    collapsed = attrs.get("collapsed", True)
    for i, panel in enumerate(children):
        pa = panel["attributes"]
        exclusive = attrs.get("exclusive", False)
        if collapsed:
            is_open = False
        elif exclusive:
            is_open = i == 0
        else:
            is_open = True
        icon = "▼" if is_open else "▶"
        panel_title = pa.get("title", "")
        lines.append(_pad(f"  {_B}{icon} {panel_title}{_R}", w))
        if is_open:
            inner_w = w - 6
            for inner_block in panel.get("children", []):
                for bl in _render_block(inner_block, inner_w):
                    lines.append(_pad(f"  {_D}│{_R} " + _pad(bl, inner_w), w))
            lines.append(_pad(f"  {_D}│{_R}", w))

    return lines


def _render_quote(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    box_w = min(w, max(24, w - 4))
    iw = box_w - 4
    lines: list[str] = [_bdr("│", "", "│", iw)]
    quote_text = _strip_html(attrs.get("html", ""))
    if quote_text:
        for ql in _wrap(f"\u201c{quote_text}\u201d", iw):
            lines.append(_bdr("│", f"{_I}{ql}{_R}", "│", iw))
    source = _strip_html(attrs.get("source_html", ""))
    if source:
        lines.append(_bdr("│", "", "│", iw))
        lines.append(_bdr("│", f"{_B}— {source}{_R}", "│", iw, "right"))
    extra = _strip_html(attrs.get("extra_html", ""))
    if extra:
        for el in _wrap(extra, iw):
            lines.append(_bdr("│", f"{_D}{el}{_R}", "│", iw, "right"))
    lines.append(_bdr("│", "", "│", iw))
    return [_pad(line, w, "center") for line in lines]


def _render_statistic(b: dict[str, Any], w: int) -> list[str]:
    children = b.get("children", [])
    if not children:
        return [_pad(f"  {_D}(empty statistic){_R}", w)]

    horizontal = b["attributes"].get("horizontal", False)

    if horizontal:
        gap = 2
        n = len(children)
        item_w = max(10, (w - (n - 1) * gap) // n)
        item_renders: list[list[str]] = []
        item_widths: list[int] = []
        for i, child in enumerate(children):
            ca = child["attributes"]
            iw = item_w if i < n - 1 else w - item_w * (n - 1) - (n - 1) * gap
            card: list[str] = []
            val = ca.get("value", "")
            card.append(_pad(f"{_B}{_WHT}{val}{_R}", iw, "center"))
            if ca.get("label"):
                card.append(_pad(ca["label"], iw, "center"))
            if ca.get("info"):
                card.append(_pad(f"{_D}{ca['info']}{_R}", iw, "center"))
            item_renders.append(card)
            item_widths.append(iw)
        return _merge_side_by_side(item_renders, item_widths, gap)
    else:
        lines: list[str] = []
        for child in children:
            ca = child["attributes"]
            val = ca.get("value", "")
            lines.append(_pad(f"{_B}{_WHT}{val}{_R}", w, "center"))
            if ca.get("label"):
                lines.append(_pad(ca["label"], w, "center"))
            if ca.get("info"):
                lines.append(_pad(f"{_D}{ca['info']}{_R}", w, "center"))
            lines.append("")
        return lines


def _render_form(b: dict[str, Any], w: int) -> list[str]:
    attrs = b["attributes"]
    children = b.get("children", [])
    box_w = min(w, max(30, w - 4))
    iw = box_w - 4

    lines: list[str] = [_box_top(box_w)]
    if attrs.get("title"):
        for tl in _wrap(attrs["title"], iw):
            lines.append(_bdr("│", f"{_B}{tl}{_R}", "│", iw))
        lines.append(_bdr("│", "", "│", iw))

    for child in children:
        ct = child.get("type", "")
        if ct == "rich_text":
            text = _strip_html(child["attributes"].get("html", ""))
            if text:
                for tl in _wrap(text, iw):
                    lines.append(_bdr("│", tl, "│", iw))
        elif ct.startswith("form_"):
            ca = child["attributes"]
            label = ca.get("label", "")
            req = " *" if ca.get("required") else ""
            lines.append(_bdr("│", f"{label}{req}:", "│", iw))
            field_w = min(iw, 30)
            field = f"{_D}[{'·' * (field_w - 2)}]{_R}"
            lines.append(_bdr("│", field, "│", iw))
        lines.append(_bdr("│", "", "│", iw))

    btn_label = attrs.get("submit_label", "Submit")
    btn = f"{_B}[ {btn_label} ]{_R}"
    lines.append(_bdr("│", btn, "│", iw, "center"))
    lines.append(_box_bottom(box_w))
    return [_pad(line, w, "center") for line in lines]


def _render_tabs(b: dict[str, Any], w: int) -> list[str]:
    children = b.get("children", [])
    lines: list[str] = []

    tab_titles = [c["attributes"].get("title", "?") for c in children]
    if tab_titles:
        tabs: list[str] = []
        for j, t in enumerate(tab_titles):
            if j == 0:
                tabs.append(f"{_B}[{t}]{_R}")
            else:
                tabs.append(f"{_D}[{t}]{_R}")
        bar = "  ".join(tabs)
    else:
        bar = f"{_D}(no tabs){_R}"
    lines.append(_pad(bar, w))
    lines.append(f"{_D}{'─' * w}{_R}")

    if children:
        first = children[0]
        inner_w = w - 4
        for inner_block in first.get("children", []):
            for bl in _render_block(inner_block, inner_w):
                lines.append(_pad("  " + bl, w))
        if not first.get("children"):
            lines.append(_pad(f"  {_D}(empty){_R}", w))

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


def render_page(data: list[dict[str, Any]], width: int | None = None) -> str:
    """Render a list of block dicts as styled ASCII art."""
    if width is None:
        width = min(shutil.get_terminal_size().columns, 120)

    inner_w = width - 2 * _MARGIN
    margin = " " * _MARGIN

    lines: list[str] = []
    for i, block in enumerate(data):
        for line in _render_block(block, inner_w):
            lines.append(margin + line)
        if i < len(data) - 1:
            lines.append("")

    if not lines:
        lines.append(_pad(f"{_D}(empty page){_R}", width, "center"))

    return "\n".join(lines)


def render_page_with_metadata(
    blocks: list[dict[str, Any]],
    metadata: dict[str, Any],
    width: int | None = None,
) -> str:
    """Render page blocks (metadata is ignored, kept for API compat)."""
    return render_page(blocks, width)
