from __future__ import annotations

from markdown_it import MarkdownIt

from gh_profile.readme import Badge, preprocess_readme, render_badges_row


def test_empty_readme_returns_no_segments() -> None:
    assert preprocess_readme("") == []
    assert preprocess_readme("   \n\n  ") == []


def test_linked_markdown_shields_badges_leave_no_stray_brackets() -> None:
    raw = (
        "[![Python](https://img.shields.io/badge/Python-3.10-blue)](https://python.org) "
        "[![CI](https://img.shields.io/badge/CI-passing-brightgreen)]"
        "(https://github.com/org/repo/actions)\n"
        "[![Wiki](https://img.shields.io/badge/Docs-Read-orange)]"
        "(https://en.wikipedia.org/wiki/Python_(programming_language))"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].kind == "badges"
    badges = segments[0].content
    assert isinstance(badges, list)
    assert badges == [
        Badge(label="Python: 3.10", color="#007ec6", url="https://python.org"),
        Badge(label="CI: passing", color="#4c1", url="https://github.com/org/repo/actions"),
        Badge(
            label="Docs: Read",
            color="#fe7d37",
            url="https://en.wikipedia.org/wiki/Python_(programming_language)",
        ),
    ]


def test_linked_markdown_non_badge_image_does_not_leave_broken_brackets() -> None:
    raw = (
        "[![Screenshot](https://example.com/shot.png)](https://example.com)\n"
        "[![](https://example.com/spacer.png)](https://example.com/target)"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].kind == "markdown"
    assert segments[0].content == "[*Screenshot*](https://example.com)"


def test_linked_html_img_badges_preserve_href_and_render_links() -> None:
    raw = (
        '<p align="center">\n'
        '  <a href="https://github.com/user">'
        '<img src="https://img.shields.io/badge/GitHub-100000" alt="GitHub"></a>\n'
        '  <a href="https://docs.example.com">'
        '<img src="https://badgen.net/badge/Docs/v1/green" alt="Docs"></a>\n'
        "</p>"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].kind == "badges"
    badges = segments[0].content
    assert isinstance(badges, list)
    assert badges[0].label == "GitHub"
    assert badges[0].url == "https://github.com/user"
    assert badges[1].label == "Docs"
    assert badges[1].url == "https://docs.example.com"

    rendered = render_badges_row(badges)
    links = [span.style.link for span in rendered.spans if span.style and span.style.link]
    assert links == ["https://github.com/user", "https://docs.example.com"]


def test_badge_labels_with_special_characters_roundtrip() -> None:
    badge_md = (
        "[![C++ | Rust [v1]](https://img.shields.io/github/stars/rust-lang/rust)]"
        "(https://rust-lang.org?x=1|2&y=3)"
    )
    segments = preprocess_readme(badge_md)
    assert len(segments) == 1
    assert segments[0].kind == "badges"
    badges = segments[0].content
    assert isinstance(badges, list)
    assert badges == [
        Badge(
            label="C++ | Rust [v1]",
            color="#30363d",
            url="https://rust-lang.org?x=1|2&y=3",
        )
    ]


def test_nested_html_lists_preserve_hierarchy() -> None:
    raw = (
        "<ul>\n"
        "  <li><strong>Backend</strong>\n"
        "    <ul>\n"
        "      <li>Python</li>\n"
        "      <li>Go\n"
        "        <ol>\n"
        "          <li>APIs</li>\n"
        "          <li>CLIs</li>\n"
        "        </ol>\n"
        "      </li>\n"
        "    </ul>\n"
        "  </li>\n"
        "  <li>Frontend</li>\n"
        "</ul>"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].kind == "markdown"
    md_text = segments[0].content
    assert isinstance(md_text, str)
    assert md_text == (
        "- **Backend**\n"
        "  - Python\n"
        "  - Go\n"
        "    1. APIs\n"
        "    2. CLIs\n"
        "- Frontend"
    )

    tokens = MarkdownIt().parse(md_text)
    list_opens = [t.type for t in tokens if t.type in ("bullet_list_open", "ordered_list_open")]
    assert list_opens == ["bullet_list_open", "bullet_list_open", "ordered_list_open"]


def test_ordered_list_start_attribute() -> None:
    raw = '<ol start="3"><li>Third</li><li>Fourth</li></ol>'
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].content == "3. Third\n4. Fourth"


def test_code_blocks_are_preserved_and_collision_safe() -> None:
    raw = (
        "Literal placeholder: @@CODEBLOCK_0@@ and @@CODEBLOCK_1@@.\n\n"
        "```markdown\n"
        "[![Shields](https://img.shields.io/badge/Example-blue)](https://example.com)\n"
        "<div>raw html</div>\n"
        "@@CODEBLOCK_1@@\n"
        "```\n\n"
        "````markdown\n"
        "```python\n"
        "print('nested fence')\n"
        "```\n"
        "````"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].kind == "markdown"
    content = segments[0].content
    assert isinstance(content, str)
    assert "Literal placeholder: @@CODEBLOCK_0@@ and @@CODEBLOCK_1@@." in content
    assert "[![Shields](https://img.shields.io/badge/Example-blue)](https://example.com)" in content
    assert "<div>raw html</div>" in content
    assert "print('nested fence')" in content


def test_html_tables_and_card_grids() -> None:
    raw = (
        "<table>\n"
        "  <tr><th>Tool</th><th>Role</th></tr>\n"
        "  <tr><td><code>ruff</code></td><td><strong>Linter</strong></td></tr>\n"
        "</table>"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].content == "| Tool | Role |\n| --- | --- |\n| `ruff` | **Linter** |"

    card_html = (
        "<table><tr>"
        "<td><b>Project A</b><br/>Fast terminal tool</td>"
        "<td><b>Project B</b><br/>Async client library</td>"
        "</tr></table>"
    )
    card_segments = preprocess_readme(card_html)
    assert len(card_segments) == 1
    assert card_segments[0].content == (
        "> **Project A**\n"
        "> Fast terminal tool\n\n"
        "> **Project B**\n"
        "> Async client library"
    )


def test_linked_html_img_badge_wrapped_in_strong_without_alt() -> None:
    raw = (
        '<a href="https://ci.example.com"><strong>'
        '<img src="https://img.shields.io/badge/Build-passing-brightgreen">'
        "</strong></a>"
    )
    segments = preprocess_readme(raw)
    assert len(segments) == 1
    assert segments[0].kind == "badges"
    assert segments[0].content == [
        Badge(label="Build: passing", color="#4c1", url="https://ci.example.com")
    ]
