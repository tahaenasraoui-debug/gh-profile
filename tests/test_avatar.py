from __future__ import annotations

from io import BytesIO

from PIL import Image

from gh_profile.avatar import ASCII_RAMP, render_avatar_ascii, render_avatar_halfblocks


def _png_bytes(color: tuple[int, int, int, int], size: tuple[int, int] = (32, 32)) -> bytes:
    buf = BytesIO()
    img = Image.new("RGBA", size, color)
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_render_avatar_missing_and_invalid() -> None:
    assert render_avatar_ascii(None).plain == "[No Avatar Available]"
    assert render_avatar_ascii(b"").plain == "[No Avatar Available]"
    assert render_avatar_ascii(b"not-an-image").plain == "[Invalid Avatar Image]"


def test_render_avatar_zero_dimensions() -> None:
    data = _png_bytes((40, 120, 200, 255))
    assert render_avatar_ascii(data, width=0, height_rows=10).plain == ""
    assert render_avatar_ascii(data, width=10, height_rows=0).plain == ""


def test_render_avatar_ascii_dimensions_and_rgb_styles() -> None:
    data = _png_bytes((50, 150, 220, 255))
    art = render_avatar_ascii(data, width=12, height_rows=6)
    lines = art.plain.splitlines()
    assert len(lines) == 6
    assert all(len(line) == 12 for line in lines)
    assert all(ch in ASCII_RAMP[1:] for line in lines for ch in line)
    assert any(
        span.style and span.style.color is not None and span.style.bold
        for span in art.spans
    )


def test_render_avatar_strips_near_white_background() -> None:
    data = _png_bytes((255, 255, 255, 255))
    art = render_avatar_ascii(data, width=8, height_rows=4)
    lines = art.plain.splitlines()
    assert len(lines) == 4
    assert all(line == " " * 8 for line in lines)


def test_render_avatar_halfblocks_alias() -> None:
    data = _png_bytes((100, 180, 90, 255))
    assert render_avatar_halfblocks(data, width=10, height_rows=5).plain == (
        render_avatar_ascii(data, width=10, height_rows=5).plain
    )
