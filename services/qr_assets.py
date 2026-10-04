"""Print-quality QR assets for posters and slides.

The stored QR PNG is ~330 px: fine on screen, blurry on an A0 poster. These
helpers render the same target URL as a vector SVG, a high-resolution PNG,
and a "poster label" (QR + "Scan to view in 3D & AR" + title + link) in
both formats. All are cheap (milliseconds) and generated on request.
"""
from __future__ import annotations

import io
import os
from xml.sax.saxutils import escape

import qrcode
from PIL import Image, ImageDraw, ImageFont

LABEL_HEADLINE = "Scan to view in 3D & AR"
# DejaVu Sans (bundled, free licence): covers Turkish and other Latin
# accents and dashes, which Pillow's built-in font draws as boxes.
_FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")


def _font(size: int, bold: bool = False):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(os.path.join(_FONT_DIR, name), size)
    except OSError:
        return ImageFont.load_default(size=size)
_INK = "#202020"
_MUTED = "#6b6b6b"
_ACCENT = "#ff682c"


def qr_matrix(url: str) -> list[list[bool]]:
    """Module matrix (quiet zone included), error correction M like the
    stored QR images, so all variants scan the same way."""
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(url)
    qr.make(fit=True)
    return qr.get_matrix()


def _modules_path(matrix, x0=0, y0=0, unit=1) -> str:
    """One SVG path of unit squares; horizontal runs merged to keep it small."""
    parts = []
    for row, cells in enumerate(matrix):
        col = 0
        while col < len(cells):
            if cells[col]:
                start = col
                while col < len(cells) and cells[col]:
                    col += 1
                parts.append(f"M{x0 + start * unit},{y0 + row * unit}h{(col - start) * unit}v{unit}h-{(col - start) * unit}z")
            else:
                col += 1
    return "".join(parts)


def qr_svg(url: str) -> str:
    matrix = qr_matrix(url)
    size = len(matrix)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size * 10}" height="{size * 10}" '
        'shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="#fff"/>'
        f'<path fill="#000" d="{_modules_path(matrix)}"/></svg>\n'
    )


def qr_png(url: str, target_px: int = 2000) -> bytes:
    """Square PNG of about ``target_px`` (whole pixels per module, so edges
    stay sharp when printed)."""
    matrix = qr_matrix(url)
    unit = max(1, target_px // len(matrix))
    side = unit * len(matrix)
    image = Image.new("1", (side, side), 1)
    draw = ImageDraw.Draw(image)
    for row, cells in enumerate(matrix):
        for col, dark in enumerate(cells):
            if dark:
                draw.rectangle([col * unit, row * unit, (col + 1) * unit - 1, (row + 1) * unit - 1], fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(300, 300))
    return buffer.getvalue()


def _short(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _display_url(url: str) -> str:
    return url.split("://", 1)[-1].rstrip("/")


def qr_label_svg(url: str, title: str) -> str:
    """Vector poster label: QR on top, headline, title and link below.
    Editable in Illustrator / Inkscape / PowerPoint."""
    matrix = qr_matrix(url)
    size = len(matrix)
    width = 600
    unit = 520 / size
    qr_x = (width - unit * size) / 2
    qr_y = 24
    text_y = qr_y + unit * size + 40
    title_line = escape(_short(title, 46))
    link_line = escape(_short(_display_url(url), 60))
    height = text_y + 110
    font = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height:.0f}" width="{width}" height="{height:.0f}">'
        f'<rect width="{width}" height="{height:.0f}" fill="#fff"/>'
        f'<path fill="#000" shape-rendering="crispEdges" d="{_modules_path(matrix, qr_x, qr_y, unit)}"/>'
        f'<rect x="{width / 2 - 24}" y="{text_y - 28}" width="48" height="4" rx="2" fill="{_ACCENT}"/>'
        f'<text x="{width / 2}" y="{text_y + 6}" text-anchor="middle" font-family="{font}" font-size="30" font-weight="700" fill="{_INK}">{escape(LABEL_HEADLINE)}</text>'
        f'<text x="{width / 2}" y="{text_y + 44}" text-anchor="middle" font-family="{font}" font-size="21" fill="{_INK}">{title_line}</text>'
        f'<text x="{width / 2}" y="{text_y + 76}" text-anchor="middle" font-family="{font}" font-size="16" fill="{_MUTED}">{link_line}</text>'
        "</svg>\n"
    )


def qr_label_png(url: str, title: str, width: int = 1800) -> bytes:
    """Raster poster label at print resolution (same layout as the SVG)."""
    scale = width / 600
    matrix = qr_matrix(url)
    size = len(matrix)
    unit = int(520 * scale) // size
    qr_side = unit * size
    qr_x = (width - qr_side) // 2
    qr_y = int(24 * scale)
    text_y = qr_y + qr_side + int(40 * scale)
    height = text_y + int(110 * scale)
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    for row, cells in enumerate(matrix):
        for col, dark in enumerate(cells):
            if dark:
                x, y = qr_x + col * unit, qr_y + row * unit
                draw.rectangle([x, y, x + unit - 1, y + unit - 1], fill="black")
    bar_w, bar_h = int(48 * scale), int(4 * scale)
    draw.rounded_rectangle(
        [(width - bar_w) // 2, text_y - int(28 * scale), (width + bar_w) // 2, text_y - int(28 * scale) + bar_h],
        radius=bar_h // 2,
        fill=_ACCENT,
    )
    lines = (
        (LABEL_HEADLINE, 28, _INK, 6, True),
        (_short(title, 46), 20, _INK, 44, False),
        (_short(_display_url(url), 60), 15, _MUTED, 76, False),
    )
    for text, font_size, color, offset, bold in lines:
        font = _font(int(font_size * scale), bold)
        draw.text((width / 2, text_y + offset * scale), text, font=font, fill=color, anchor="ms")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(300, 300))
    return buffer.getvalue()
