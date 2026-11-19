from __future__ import annotations

from io import BytesIO

from PIL import Image
from rich.style import Style
from rich.text import Text

# Luminance ramp from sparse (dark) to dense (bright)
ASCII_RAMP = " .,:;i1tfLCG08@"
DEFAULT_BG = (13, 17, 23)  # #0d1117


def _has_light_background(img: Image.Image) -> bool:
    """Check if the image corners are near-white so we can drop the solid box."""
    w, h = img.size
    corners = [
        img.getpixel((0, 0)),
        img.getpixel((w - 1, 0)),
        img.getpixel((0, h - 1)),
        img.getpixel((w - 1, h - 1)),
    ]
    light_corners = sum(1 for r, g, b in corners if r > 235 and g > 235 and b > 235)
    return light_corners >= 3


def render_avatar_halfblocks(
    image_bytes: bytes | None,
    width: int = 30,
    height_rows: int = 15,
) -> Text:
    """Convert raw image bytes into neofetch-style 24-bit colored ASCII art."""
    if not image_bytes:
        return Text("[No Avatar Available]", style="dim italic")

    try:
        img = Image.open(BytesIO(image_bytes)).convert("RGBA")
    except Exception:
        return Text("[Invalid Avatar Image]", style="dim italic")

    canvas = Image.new("RGBA", img.size, (*DEFAULT_BG, 255))
    canvas.alpha_composite(img)
    rgb_img = canvas.convert("RGB")

    # Terminal cells are ~2x taller than wide, so height_rows ~= width / 2
    resized = rgb_img.resize((width, height_rows), Image.Resampling.LANCZOS)
    strip_white_bg = _has_light_background(resized)
    pixels = resized.load()

    ramp_len = len(ASCII_RAMP) - 1
    output = Text()

    for y in range(height_rows):
        for x in range(width):
            r, g, b = pixels[x, y]

            # Drop near-white background pixels so the subject pops like neofetch
            if strip_white_bg and r > 240 and g > 240 and b > 240:
                output.append(" ")
                continue

            luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
            char_idx = int(luminance * ramp_len)
            # Ensure non-background pixels still get a visible character
            char = ASCII_RAMP[max(1, char_idx)]
            output.append(char, style=Style(color=f"rgb({r},{g},{b})", bold=True))

        if y < height_rows - 1:
            output.append("\n")

    return output
