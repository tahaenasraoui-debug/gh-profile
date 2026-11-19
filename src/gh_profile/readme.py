from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup, Tag
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

BADGE_TOKEN_RE = re.compile(r"\[\[BADGE:([^|\]]+)\|([^\]]+)\]\]")


@dataclass
class Badge:
    label: str
    color: str


@dataclass
class ReadmeSegment:
    kind: str  # "markdown" or "badges"
    content: str | list[Badge]


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
            style=Style(color=fg, bgcolor=badge.color, bold=True),
        )
        if i < len(badges) - 1:
            text.append(" ")
    return text


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
    code_blocks: list[str] = []

    def _stash_code(match: re.Match[str]) -> str:
        code_blocks.append(match.group(0))
        return f"@@CODEBLOCK_{len(code_blocks) - 1}@@"

    protected = re.sub(r"```[\s\S]*?```", _stash_code, raw_markdown)

    # Convert Markdown image badges: ![alt](https://img.shields.io/...)
    def _md_badge_sub(match: re.Match[str]) -> str:
        alt, url = match.group(1), match.group(2)
        badge = _parse_shields_badge(url, alt)
        if badge:
            return f"[[BADGE:{badge.label}|{badge.color}]]"
        return f"*{alt}*" if alt else ""

    protected = re.sub(r"!\[([^\]]*)\]\((https?://[^)\s]+)\)", _md_badge_sub, protected)

    soup = BeautifulSoup(protected, "html.parser")

    # 1. Replace <img> tags first
    for img in soup.find_all("img"):
        src = str(img.get("src", ""))
        alt = str(img.get("alt", "")).strip()
        badge = _parse_shields_badge(src, alt)
        if badge:
            img.replace_with(f" [[BADGE:{badge.label}|{badge.color}]] ")
        elif alt:
            img.replace_with(f"*{alt}*")
        else:
            img.decompose()

    # 2. Convert inline formatting BEFORE tables so cell formatting is preserved
    for tag in soup.find_all(["strong", "b"]):
        text = tag.get_text(" ", strip=True)
        tag.replace_with(f"**{text}**" if text else "")

    for tag in soup.find_all(["em", "i"]):
        text = tag.get_text(" ", strip=True)
        tag.replace_with(f"*{text}*" if text else "")

    for code in soup.find_all("code"):
        text = code.get_text(strip=True)
        if text and "\n" not in text:
            code.replace_with(f"`{text}`")

    for a in soup.find_all("a"):
        inner = a.get_text(" ", strip=True)
        href = a.get("href", "")
        if "[[BADGE:" in inner:
            a.replace_with(f" {inner} ")
        elif inner and href:
            a.replace_with(f"[{inner}]({href})")
        else:
            a.replace_with(inner)

    for br in soup.find_all("br"):
        br.replace_with("\n")

    for hr in soup.find_all("hr"):
        hr.replace_with("\n\n---\n\n")

    # 3. Convert HTML headings and lists
    for level in range(1, 7):
        for h in soup.find_all(f"h{level}"):
            text = h.get_text(" ", strip=True)
            h.replace_with(f"\n\n{'#' * level} {text}\n\n")

    for li in soup.find_all("li"):
        text = li.get_text(" ", strip=True)
        li.replace_with(f"- {text}\n")

    for lst in soup.find_all(["ul", "ol"]):
        lst.insert_before("\n")
        lst.insert_after("\n")
        lst.unwrap()

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

    cleaned = str(soup)

    for idx, block in enumerate(code_blocks):
        cleaned = cleaned.replace(f"@@CODEBLOCK_{idx}@@", block)

    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

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
            badges = [Badge(label=lbl, color=col) for lbl, col in found_badges]
            if segments and segments[-1].kind == "badges":
                assert isinstance(segments[-1].content, list)
                segments[-1].content.extend(badges)
            else:
                segments.append(ReadmeSegment(kind="badges", content=badges))
        else:
            md_buffer.append(line)

    _flush_md()
    return segments
