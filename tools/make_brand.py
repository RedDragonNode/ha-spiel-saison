"""Erzeugt Icon, Logo und Banner der Integration (Stil wie bei den anderen HA-Integrationen).

Aufruf aus dem Projektordner:  python tools/make_brand.py
Benötigt nur Pillow. Die Schrift ist Segoe UI Black (Windows); mit --font lässt sich eine andere angeben.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = ROOT / "custom_components" / "spiel_saison" / "brand"
ADDON_DIR = ROOT / "spiel_saison_recorder"
DOCS_DIR = ROOT / "docs"

BASE = 512  # Zeichenfläche des Icons
SS = 4  # Supersampling für glatte Kanten

BG_TOP = (39, 37, 87)  # #272557
BG_BOTTOM = (26, 26, 58)  # #1A1A3A
TEAL = (32, 201, 190)  # #20C9BE
NAVY = (39, 69, 127)  # #27457F
DARK = (18, 19, 56)  # #121338
CORE_DARK = (13, 14, 42)  # #0D0E2A
ORANGE = (255, 138, 31)  # #FF8A1F
LAVENDER = (143, 130, 240)  # #8F82F0
WORD_LAVENDER = (123, 108, 240)  # #7B6CF0
RED = (255, 59, 85)  # #FF3B55
WHITE = (255, 255, 255)


def diagonal_gradient(size: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    """Diagonaler Verlauf von oben links nach unten rechts, klein berechnet und hochskaliert."""
    small = 256
    img = Image.new("RGB", (small, small))
    px = img.load()
    for y in range(small):
        for x in range(small):
            t = (x + y) / (2 * (small - 1))
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(top, bottom, strict=True))
    return img.resize((size, size), Image.BICUBIC)


def circle(draw: ImageDraw.ImageDraw, cx: float, cy: float, r: float, fill) -> None:
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=fill)


def draw_icon() -> Image.Image:
    """Quadratisches Icon: Saison-Ring aus vier Vierteln mit Abspiel-Dreieck, darunter drei Spielpartner-Pillen."""
    s = BASE * SS

    def p(v: float) -> float:
        return v * SS

    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    bg = diagonal_gradient(s, BG_TOP, BG_BOTTOM).convert("RGBA")
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, s - 1, s - 1), radius=p(112), fill=255)
    canvas.paste(bg, (0, 0), mask)

    d = ImageDraw.Draw(canvas)
    cx, cy = 232, 214

    # Saison-Ring: vier Viertel, ein Viertel je Jahreszeit (Pillow zählt Winkel im Uhrzeigersinn ab 3 Uhr)
    box = (p(cx - 138), p(cy - 138), p(cx + 138), p(cy + 138))
    for start, color in ((270, TEAL), (0, ORANGE), (90, LAVENDER), (180, NAVY)):
        d.pieslice(box, start, start + 90, fill=color)
    # feine Trennlinien zwischen den Vierteln
    for x0, y0, x1, y1 in ((cx, cy - 138, cx, cy + 138), (cx - 138, cy, cx + 138, cy)):
        d.line((p(x0), p(y0), p(x1), p(y1)), fill=DARK, width=int(p(6)))
    circle(d, p(cx), p(cy), p(100), DARK)
    circle(d, p(cx), p(cy), p(84), CORE_DARK)

    # Abspiel-Dreieck: Spiel und Aufnahme
    d.polygon([(p(cx - 24), p(cy - 44)), (p(cx - 24), p(cy + 44)), (p(cx + 48), p(cy))], fill=WHITE)
    circle(d, p(cx - 14), p(cy - 22), p(5), (210, 255, 250))

    # Drei Pillen unten: mehrere Spielpartner, jeder mit eigener Saison
    pills = ((92, 380, 252, ORANGE), (92, 410, 212, TEAL), (92, 440, 172, WHITE))
    for x0, y, x1, color in pills:
        d.rounded_rectangle((p(x0), p(y), p(x1 + 100), p(y + 16)), radius=p(8), fill=color)
    for _x0, y, _x1, color in pills:
        circle(d, p(420), p(y + 8), p(8), color)

    # Abzeichen oben rechts: Aufnahme läuft
    bx, by = 408, 112
    circle(d, p(bx), p(by), p(56), (15, 16, 48))
    circle(d, p(bx), p(by), p(36), RED)
    circle(d, p(bx - 11), p(by - 11), p(8), (255, 205, 215))

    return canvas.resize((BASE, BASE), Image.LANCZOS)


def draw_banner(icon: Image.Image, font_path: str) -> Image.Image:
    """Breiter Banner: Icon links, Name zweizeilig rechts, transparenter Hintergrund."""
    height = 440
    pad = 40
    icon_size = height - 2 * pad
    icon_small = icon.resize((icon_size, icon_size), Image.LANCZOS)

    font = ImageFont.truetype(font_path, 150)
    lines = [("Spiel", WORD_LAVENDER), ("Saison", ORANGE)]
    boxes = [font.getbbox(text) for text, _ in lines]
    text_w = max(b[2] - b[0] for b in boxes)
    line_h = [b[3] - b[1] for b in boxes]
    gap = 26
    block_h = sum(line_h) + gap
    text_x = pad + icon_size + 56
    width = text_x + text_w + pad + 20

    banner = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    banner.alpha_composite(icon_small, (pad, pad))
    d = ImageDraw.Draw(banner)
    y = (height - block_h) // 2
    for (text, color), box, h in zip(lines, boxes, line_h, strict=True):
        d.text((text_x - box[0], y - box[1]), text, font=font, fill=color)
        y += h + gap
    return banner


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--font", default=r"C:\Windows\Fonts\seguibl.ttf")
    args = parser.parse_args()

    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    icon = draw_icon()  # 512 x 512
    icon.save(BRAND_DIR / "icon@2x.png", optimize=True)
    icon.resize((256, 256), Image.LANCZOS).save(BRAND_DIR / "icon.png", optimize=True)
    icon.save(DOCS_DIR / "icon.png", optimize=True)
    icon.resize((128, 128), Image.LANCZOS).save(ADDON_DIR / "icon.png", optimize=True)

    banner = draw_banner(icon, args.font)
    banner.save(DOCS_DIR / "banner.png", optimize=True)
    for name, h in (("logo.png", 256), ("logo@2x.png", 512)):
        w = round(banner.width * h / banner.height)
        banner.resize((w, h), Image.LANCZOS).save(BRAND_DIR / name, optimize=True)
    banner.resize((round(banner.width * 100 / banner.height), 100), Image.LANCZOS).save(
        ADDON_DIR / "logo.png", optimize=True
    )

    print(
        "Fertig:",
        *sorted(
            p.relative_to(ROOT).as_posix()
            for p in (*BRAND_DIR.glob("*.png"), *DOCS_DIR.glob("*.png"), ADDON_DIR / "icon.png", ADDON_DIR / "logo.png")
        ),
        sep="\n  ",
    )


if __name__ == "__main__":
    main()
