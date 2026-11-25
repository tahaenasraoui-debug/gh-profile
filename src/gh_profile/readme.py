from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from urllib.parse import quote, unquote, urlparse

from bs4 import BeautifulSoup, NavigableString, Tag
from rich.style import Style
from rich.text import Text

NAMED_COLORS: dict[str, str] = {
    "brightgreen": "#4c1",
    "green": "#97ca00",
    "yellowgreen": "#a4a61d",
    "yellow": "#dfb317",
    "orange": "#fe7d37",
    "red": "#e05d44",
    "blue": "#007ec6",
    "lightgrey": "#9f9f9f",
    "lightgray": "#9f9f9f",
    "grey": "#555555",
    "gray": "#555555",
    "blueviolet": "#8a2be2",
    "success": "#4c1",
    "important": "#fe7d37",
    "critical": "#e05d44",
    "informational": "#007ec6",
    "inactive": "#9f9f9f",
    "black": "#111111",
    "white": "#f0f0f0",
    "purple": "#6f42c1",
    "pink": "#ea4aaa",
}

BADGE_TOKEN_RE = re.compile(r"\[\[BADGE:([^|\]]+)\|([^|\]]+)\|([^\]]*)\]\]")

_ALT_BODY = r"(?:[^\[\]]|\[[^\[\]]*\])*"
_URL_BODY = r"(?:[^()\s]|\([^()\s]*\))+"
_LINKED_MD_IMG_RE = re.compile(
    rf"\[[ \t]*!\[({_ALT_BODY})\]\(({_URL_BODY})\)[ \t]*\]\(({_URL_BODY})\)"
)
_MD_IMG_RE = re.compile(rf"!\[({_ALT_BODY})\]\(((?:https?:)?//{_URL_BODY})\)")
_FENCE_RE = re.compile(
    r"(^[ \t]*(`{3,}|~{3,})[^\n]*\n(?:[\s\S]*?\n)?[ \t]*\2[ \t]*$)|```[^\n`]*```",
    re.MULTILINE,
)


@dataclass
class Badge:
    label: str
    color: str
    url: str | None = None


@dataclass
class ReadmeSegment:
    kind: str  # "markdown" or "badges"
    content: str | list[Badge]


def _encode_badge_token(badge: Badge) -> str:
    label = quote(badge.label, safe="")
    color = quote(badge.color, safe="")
    url = quote(badge.url or "", safe="")
    return f"[[BADGE:{label}|{color}|{url}]]"


def _decode_badge_token(label: str, color: str, url: str) -> Badge:
    decoded_url = unquote(url) or None
    return Badge(label=unquote(label), color=unquote(color), url=decoded_url)


def _normalize_color(raw: str | None) -> str:
    if not raw:
        return "#30363d"
    c = raw.strip().lstrip("#").lower()
    if c in NAMED_COLORS:
        return NAMED_COLORS[c]
    if re.fullmatch(r"[0-9a-f]{3}|[0-9a-f]{6}", c):
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        return f"#{c}"
    return "#30363d"


def _contrast_fg(hex_bg: str) -> str:
    c = hex_bg.lstrip("#")
    if len(c) != 6:
        return "#ffffff"
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#0d1117" if luminance > 0.6 else "#ffffff"


def _parse_shields_badge(src: str, alt: str = "") -> Badge | None:
    parsed = urlparse(src)
    host = parsed.netloc.lower()
    if "shields.io" not in host and "badgen.net" not in host:
        return None

    path = unquote(parsed.path).strip("/")
    color = "#30363d"
    label = alt.strip()

    if path.startswith("badge/"):
        spec = path[len("badge/") :]
        parts = re.split(r"(?<!-)-(?!-)", spec)
        parts = [p.replace("--", "-").replace("_", " ").strip() for p in parts if p]
        if len(parts) >= 3:
            label = f"{parts[0]}: {parts[1]}"
            color = _normalize_color(parts[-1])
        elif len(parts) == 2:
            label = parts[0]
            color = _normalize_color(parts[1])
        elif len(parts) == 1 and not label:
            label = parts[0]

    for pair in parsed.query.split("&"):
        if pair.startswith("color="):
            color = _normalize_color(unquote(pair.split("=", 1)[1]))
        elif pair.startswith("label=") and not label:
            label = unquote(pair.split("=", 1)[1])

    return Badge(label=label or "Badge", color=color)


def render_badges_row(badges: list[Badge]) -> Text:
    text = Text()
    for i, badge in enumerate(badges):
        fg = _contrast_fg(badge.color)
        text.append(
            f" {badge.label} ",
            style=Style(color=fg, bgcolor=badge.color, bold=True, link=badge.url or None),
        )
        if i < len(badges) - 1:
            text.append(" ")
    return text


def _normalize_inline(text: str) -> str:
    return re.sub(r"[ \t\r\n]+", " ", text).strip()


def _convert_list_item(li: Tag, indent: int, marker: str) -> list[str]:
    lines: list[str] = []
    child_indent = indent + len(marker)
    text_parts: list[str] = []
    emitted_marker = False

    def _flush_text() -> None:
        nonlocal emitted_marker
        combined = _normalize_inline("".join(text_parts))
        text_parts.clear()
        if not combined:
            return
        if not emitted_marker:
            lines.append(f"{' ' * indent}{marker}{combined}")
            emitted_marker = True
        else:
            lines.append(f"{' ' * child_indent}{combined}")

    def _walk(node: Tag | NavigableString) -> None:
        nonlocal emitted_marker
        if isinstance(node, Tag):
            if node.name in ("ul", "ol"):
                _flush_text()
                if not emitted_marker:
                    lines.append(f"{' ' * indent}{marker.rstrip()}")
                    emitted_marker = True
                nested = _convert_list(node, indent=child_indent)
                if nested:
                    lines.append(nested)
            elif node.name == "br":
                _flush_text()
            elif node.find(["ul", "ol"]):
                for child in node.children:
                    _walk(child)
                if node.name in ("p", "div"):
                    _flush_text()
            else:
                text_parts.append(node.get_text())
                if node.name in ("p", "div"):
                    _flush_text()
        else:
            text_parts.append(str(node))

    for child in li.children:
        _walk(child)
    _flush_text()
    if not emitted_marker:
        return []
    return lines


def _convert_list(lst: Tag, indent: int = 0) -> str:
    """Recursively convert HTML <ul>/<ol> elements into indented Markdown lists."""
    is_ordered = lst.name == "ol"
    index = 1
    if is_ordered and lst.get("start") is not None:
        try:
            index = int(str(lst["start"]))
        except ValueError:
            index = 1

    lines: list[str] = []
    for child in lst.children:
        if not isinstance(child, Tag):
            continue
        if child.name == "li":
            marker = f"{index}. " if is_ordered else "- "
            index += 1
            lines.extend(_convert_list_item(child, indent=indent, marker=marker))
        elif child.name in ("ul", "ol"):
            nested = _convert_list(child, indent=indent + 2)
            if nested:
                lines.append(nested)
    return "\n".join(lines)


def _convert_table(table: Tag) -> str:
    """
    Convert an HTML <table> into clean Markdown:
    - Compact key/value tables -> Markdown table
    - Multi-line card grids (e.g. project cards) -> separated blockquotes
    """
    rows: list[list[str]] = []
    has_header = bool(table.find("th"))

    for tr in table.find_all("tr"):
        cells = []
        for cell in tr.find_all(["th", "td"], recursive=False):
            # Preserve internal newlines already inserted by <br> / <p>
            raw_text = cell.get_text()
            lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
            cells.append("\n".join(lines))
        if any(cells):
            rows.append(cells)

    if not rows:
        return ""

    # Detect card-grid tables (cells with multiple lines or long descriptions)
    is_card_grid = any("\n" in cell or len(cell) > 100 for row in rows for cell in row)

    if is_card_grid:
        blocks: list[str] = []
        for row in rows:
            for cell in row:
                if not cell:
                    continue
                quoted = "\n".join(f"> {line}" for line in cell.splitlines())
                blocks.append(quoted)
        return "\n\n" + "\n\n".join(blocks) + "\n\n"

    # Otherwise render as a proper Markdown table
    col_count = max(len(r) for r in rows)
    normalized = [r + [""] * (col_count - len(r)) for r in rows]

    def _fmt_row(r: list[str]) -> str:
        escaped = [c.replace("|", "\\|").replace("\n", " ") for c in r]
        return "| " + " | ".join(escaped) + " |"

    md_lines: list[str] = []
    if has_header:
        md_lines.append(_fmt_row(normalized[0]))
        md_lines.append("| " + " | ".join(["---"] * col_count) + " |")
        for r in normalized[1:]:
            md_lines.append(_fmt_row(r))
    else:
        # Headerless 2-col table (like ~/skills): render as clean bullet rows
        if col_count == 2:
            for k, v in normalized:
                if k and v:
                    md_lines.append(f"- **{k}:** {v}")
                elif k or v:
                    md_lines.append(f"- {k or v}")
        else:
            md_lines.append("| " + " | ".join([" "] * col_count) + " |")
            md_lines.append("| " + " | ".join(["---"] * col_count) + " |")
            for r in normalized:
                md_lines.append(_fmt_row(r))

    return "\n\n" + "\n".join(md_lines) + "\n\n"


def preprocess_readme(raw_markdown: str) -> list[ReadmeSegment]:
    """
    Clean HTML tags and shields.io badges from a GitHub profile README
    while preserving tables, cards, lists, and inline formatting.
    """
    normalized = raw_markdown.replace("\r\n", "\n").replace("\r", "\n")

    sentinel = uuid.uuid4().hex
    while sentinel in normalized:
        sentinel = uuid.uuid4().hex

    code_blocks: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        code_blocks.append(match.group(0))
        return f"CODEBLOCK-{sentinel}-{len(code_blocks) - 1}"

    protected = _FENCE_RE.sub(_stash_code, normalized)

    # Convert linked Markdown image badges first: [![alt](badge)](url)
    def _linked_md_badge_sub(match: re.Match[str]) -> str:
        alt, img_url, link_url = match.group(1), match.group(2), match.group(3)
        badge = _parse_shields_badge(img_url, alt)
        if badge:
            badge.url = link_url
            return _encode_badge_token(badge)
        alt_clean = alt.strip()
        if alt_clean:
            return f"[*{alt_clean}*]({link_url})"
        return ""

    protected = _LINKED_MD_IMG_RE.sub(_linked_md_badge_sub, protected)

    # Convert standalone Markdown image badges: ![alt](https://img.shields.io/...)
    def _md_badge_sub(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        badge = _parse_shields_badge(url, alt)
        if badge:
            return _encode_badge_token(badge)
        return f"*{alt}*" if alt else ""

    protected = _MD_IMG_RE.sub(_md_badge_sub, protected)

    soup = BeautifulSoup(protected, "html.parser")

    # 1. Replace <img> tags first
    for img in soup.find_all("img"):
        src = str(img.get("src", ""))
        alt = str(img.get("alt", "")).strip()
        badge = _parse_shields_badge(src, alt)
        if badge:
            img.replace_with(f" {_encode_badge_token(badge)} ")
        elif alt:
            img.replace_with(f"*{alt}*")
        else:
            img.decompose()

    # 2. Convert inline formatting BEFORE lists & tables so formatting is preserved
    for tag in soup.find_all(["strong", "b"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            tag.replace_with("")
        elif BADGE_TOKEN_RE.search(text):
            tag.replace_with(text)
        else:
            tag.replace_with(f"**{text}**")

    for tag in soup.find_all(["em", "i"]):
        text = tag.get_text(" ", strip=True)
        if not text:
            tag.replace_with("")
        elif BADGE_TOKEN_RE.search(text):
            tag.replace_with(text)
        else:
            tag.replace_with(f"*{text}*")

    for code in soup.find_all("code"):
        text = code.get_text(strip=True)
        if text and "\n" not in text:
            code.replace_with(f"`{text}`")

    for a in soup.find_all("a"):
        inner = a.get_text(" ", strip=True)
        href = str(a.get("href", "") or "").strip()
        if BADGE_TOKEN_RE.search(inner):
            def _attach_href(match: re.Match[str], link: str = href) -> str:
                badge = _decode_badge_token(match.group(1), match.group(2), match.group(3))
                if link and not badge.url:
                    badge.url = link
                return _encode_badge_token(badge)

            updated = BADGE_TOKEN_RE.sub(_attach_href, inner)
            non_badge = BADGE_TOKEN_RE.sub("", updated).strip()
            if non_badge and href:
                badge_only = " ".join(
                    _encode_badge_token(_decode_badge_token(*m))
                    for m in BADGE_TOKEN_RE.findall(updated)
                )
                a.replace_with(f" {badge_only} [{non_badge}]({href}) ")
            else:
                a.replace_with(f" {updated} ")
        elif inner and href:
            a.replace_with(f"[{inner}]({href})")
        else:
            a.replace_with(inner)

    for br in soup.find_all("br"):
        br.replace_with("\n")

    for hr in soup.find_all("hr"):
        hr.replace_with("\n\n---\n\n")

    # 3. Convert HTML headings and nested lists
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(" ", strip=True)
            h.replace_with(f"\n\n{'#' * level} {text}\n\n")

    for lst in soup.find_all(["ul", "ol"]):
        if lst.find_parent(["ul", "ol"]) is None:
            lst.replace_with("\n" + _convert_list(lst) + "\n")

    for li in soup.find_all("li"):
        text = _normalize_inline(li.get_text(" ", strip=True))
        li.replace_with(f"- {text}\n" if text else "")

    # 4. Convert HTML tables (preserving bold, code, and line breaks inside cells)
    for table in soup.find_all("table"):
        table.replace_with(_convert_table(table))

    # 5. Unwrap layout containers with paragraph spacing
    for tag_name in ("div", "p", "center", "section", "details", "summary"):
        for tag in soup.find_all(tag_name):
            if isinstance(tag, Tag):
                tag.insert_before("\n\n")
                tag.insert_after("\n\n")
                tag.unwrap()

    for span in soup.find_all("span"):
        if isinstance(span, Tag):
            span.unwrap()

    cleaned = re.sub(r"\n{3,}", "\n\n", soup.decode(formatter=None)).strip()

    if code_blocks:
        restore_re = re.compile(rf"CODEBLOCK-{re.escape(sentinel)}-(\d+)")
        cleaned = restore_re.sub(lambda m: code_blocks[int(m.group(1))], cleaned)

    # Split into alternating Markdown and Badge-row segments
    segments: list[ReadmeSegment] = []
    md_buffer: list[str] = []

    def _flush_md() -> None:
        text = "\n".join(md_buffer).strip()
        if text:
            segments.append(ReadmeSegment(kind="markdown", content=text))
        md_buffer.clear()

    for line in cleaned.splitlines():
        found_badges = BADGE_TOKEN_RE.findall(line)
        if found_badges:
            remainder = BADGE_TOKEN_RE.sub("", line).strip()
            if remainder:
                md_buffer.append(remainder)
            _flush_md()
            badges = [_decode_badge_token(lbl, col, url) for lbl, col, url in found_badges]
            if (
                segments
                and segments[-1].kind == "badges"
                and isinstance(segments[-1].content, list)
            ):
                segments[-1].content.extend(badges)
            else:
                segments.append(ReadmeSegment(kind="badges", content=badges))
        else:
            md_buffer.append(line)

    _flush_md()
    return segments
