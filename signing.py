"""Visual signatures: stamp a signature image or typed signature onto PDF pages."""
import datetime
import io
import os

import pymupdf
from PIL import Image, ImageDraw, ImageFont, ImageOps

from pdf_tools import UserError, _open, _save, _stem, parse_ranges

FONT_DIR = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
FONTS = {  # id -> (file, label); only fonts present on this machine are offered
    "segoe-script": ("segoesc.ttf", "Segoe Script"),
    "segoe-print": ("segoepr.ttf", "Segoe Print"),
    "ink-free": ("Inkfree.ttf", "Ink Free"),
    "brush-script": ("BRUSHSCI.TTF", "Brush Script"),
    "freestyle": ("FREESCPT.TTF", "Freestyle Script"),
}


def available_fonts():
    return [{"id": k, "label": label} for k, (f, label) in FONTS.items()
            if os.path.exists(os.path.join(FONT_DIR, f))]


def _hex(h):
    h = (h or "#1a237e").lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def typed_signature(text, font="segoe-script", color="#1a237e"):
    """Render text in a handwriting font to a tightly-cropped transparent PNG image."""
    path = os.path.join(FONT_DIR, FONTS.get(font, FONTS["segoe-script"])[0])
    fnt = ImageFont.truetype(path, 160) if os.path.exists(path) else ImageFont.load_default(160)
    l, t, r, b = ImageDraw.Draw(Image.new("L", (1, 1))).textbbox((0, 0), text, font=fnt)
    img = Image.new("RGBA", (r - l + 40, b - t + 40), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20 - l, 20 - t), text, font=fnt, fill=_hex(color) + (255,))
    return img


def image_signature(path, remove_background=True, color=None):
    """Load a signature image; optionally make the paper transparent and recolour the ink."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    if remove_background:
        # ink darkness becomes opacity: white paper -> fully transparent, dark ink -> solid
        lum = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        alpha = lum.point(lambda v: 0 if v > 200 else min(255, int((200 - v) * 1.8)))
        if img.getchannel("A").getextrema()[0] < 255:  # keep existing transparency too
            alpha = Image.composite(alpha, Image.new("L", img.size, 0), img.getchannel("A"))
        ink = Image.new("RGBA", img.size, _hex(color) + (255,)) if color else img
        img = ink.copy()
        img.putalpha(alpha)
    box = img.getchannel("A").getbbox()
    if not box:
        raise UserError("The signature image looks empty — try turning off 'remove background'.")
    return img.crop(box)


def _target_pages(spec, n):
    if spec in (None, "", "last"):
        return [n - 1]
    if spec == "first":
        return [0]
    if spec == "all":
        return list(range(n))
    return sorted({i for g in parse_ranges(spec, n) for i in g})


def sign(src, out_dir, sig_img, o):
    """o: pages, x, y (centre, 0-1 of page), width (0-1 of page width), add_date, date_format, caption."""
    buf = io.BytesIO()
    sig_img.save(buf, "PNG")
    png, aspect = buf.getvalue(), sig_img.height / sig_img.width
    x, y, wfrac = float(o.get("x", 0.75)), float(o.get("y", 0.88)), float(o.get("width", 0.28))

    with _open(src, o.get("current_password")) as doc:
        for i in _target_pages(o.get("pages"), doc.page_count):
            page = doc[i]
            page.remove_rotation()  # place in what-you-see coordinates
            pr = page.rect
            w = pr.width * wfrac
            h = w * aspect
            cx = min(max(pr.width * x, w / 2), pr.width - w / 2)  # keep fully on the page
            cy = min(max(pr.height * y, h / 2), pr.height - h / 2)
            rect = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            page.insert_image(rect, stream=png, keep_proportion=True, overlay=True)

            lines = []
            if o.get("caption"):
                lines.append(o["caption"])
            if o.get("add_date"):
                lines.append(datetime.date.today().strftime(o.get("date_format") or "%d %b %Y"))
            size = max(7, min(11, w / 14))
            for n, line in enumerate(lines):
                tw = pymupdf.get_text_length(line, fontname="helv", fontsize=size)
                ty = rect.y1 + size * (1.3 + n * 1.25)
                if ty > pr.height - 4:  # no room below; put it above instead
                    ty = rect.y0 - size * (0.6 + (len(lines) - 1 - n) * 1.25)
                page.insert_text((cx - tw / 2, ty), line, fontname="helv", fontsize=size, color=(0.25, 0.25, 0.25))
        return _save(doc, os.path.join(out_dir, _stem(src) + "_signed.pdf"))
