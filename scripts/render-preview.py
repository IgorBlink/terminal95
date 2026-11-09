#!/usr/bin/env python3
"""Render the README's theme specimen from the bundled pixel font and palette.

Run: uv run --with pillow python scripts/render-preview.py
This is a reproducible theme preview, not a capture of an operating-system UI.
"""

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    root = Path(__file__).resolve().parents[1]
    palette = json.loads((root / "themes/palette.json").read_text())
    font = ImageFont.truetype(
        str(root / "assets/fonts/PxPlus_IBM_VGA_8x16.ttf"),
        size=32,
        layout_engine=ImageFont.Layout.BASIC,
    )
    canvas = Image.new("RGB", (960, 416), palette["background"])
    draw = ImageDraw.Draw(canvas)
    foreground = palette["foreground"]

    def row(y, text, *, selected=False, muted=False):
        if selected:
            draw.rectangle((0, y - 12, canvas.width, y + 43), fill=palette["selection_background"])
        draw.text((16, y), text, font=font, fill="#808080" if muted else foreground)

    row(12, "~/projects/terminal95", muted=True)
    row(76, "> ls", selected=True)
    row(140, "README.md  themes/  assets/  scripts/")
    row(220, "> codex95", selected=True)
    row(284, "> What would you like to build?")
    row(364, "> ", selected=True)
    draw.rectangle((48, 364, 63, 395), fill=foreground)

    output = root / "docs/terminal95-preview.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output, optimize=True)
    print(output.relative_to(root))


if __name__ == "__main__":
    main()
