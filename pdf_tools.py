"""PDF merge / split / edit / compress with PyMuPDF."""
import os
import re
import shutil

import pymupdf


class UserError(ValueError):
    """Bad input from the user — shown as-is in the UI."""


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def _open(src, password=""):
    doc = pymupdf.open(src)
    if doc.needs_pass and not doc.authenticate(password or ""):
        doc.close()
        raise UserError(f"{os.path.basename(src)} is password-protected — enter its password.")
    return doc


def parse_ranges(spec, page_count):
    """'1-3, 5, 8-' -> [[0,1,2],[4],[7..last]] (1-based input, 0-based output). 'end' = last page."""
    groups = []
    for part in filter(None, (p.strip() for p in (spec or "").split(","))):
        m = re.fullmatch(r"(\d+|end)?\s*(-\s*(\d+|end)?)?", part, re.I)
        if not m or not (m.group(1) or m.group(3)):
            raise UserError(f"Invalid page range: '{part}'")

        def num(s, default):
            if not s:
                return default
            return page_count if s.lower() == "end" else int(s)

        a = num(m.group(1), 1)
        b = num(m.group(3), page_count) if m.group(2) else a
        if not (1 <= a <= page_count and 1 <= b <= page_count):
            raise UserError(f"Page range '{part}' is outside 1–{page_count}.")
        step = 1 if b >= a else -1  # '5-1' gives reversed order
        groups.append(list(range(a - 1, b - 1 + step, step)))
    return groups


def _save(doc, out, password=None, compress=True):
    kw = dict(garbage=4, deflate=True, clean=True) if compress else {}
    if password:
        kw.update(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password,
                  permissions=-1)
    doc.save(out, **kw)
    return out


# ---------------------------------------------------------------- merge / split

def merge(srcs, out_dir, bookmarks=True):
    out = os.path.join(out_dir, "merged.pdf")
    result = pymupdf.open()
    toc = []
    for s in srcs:
        with _open(s) as doc:
            if bookmarks:
                toc.append([1, _stem(s), result.page_count + 1])
            result.insert_pdf(doc)
    if bookmarks:
        result.set_toc(toc)
    _save(result, out)
    result.close()
    return out


def split(src, out_dir, mode="each", ranges="", every=1):
    """mode: each (one file per page) | ranges ('1-3,4-6' -> one file per range) | every (chunks of N pages)."""
    with _open(src) as doc:
        n = doc.page_count
        if mode == "ranges":
            groups = parse_ranges(ranges, n)
            if not groups:
                raise UserError("Enter page ranges, e.g. 1-3, 4-6, 7-end")
        elif mode == "every":
            k = max(1, int(every))
            groups = [list(range(i, min(i + k, n))) for i in range(0, n, k)]
        else:
            groups = [[i] for i in range(n)]

        outs = []
        for g in groups:
            label = f"p{g[0] + 1}" if len(g) == 1 else f"p{g[0] + 1}-{g[-1] + 1}"
            part = pymupdf.open()
            for i in g:
                part.insert_pdf(doc, from_page=i, to_page=i)
            outs.append(_save(part, os.path.join(out_dir, f"{_stem(src)}_{label}.pdf")))
            part.close()
    return outs


# ---------------------------------------------------------------- edit

def _hex_color(h):
    h = (h or "#888888").lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _stamp_watermark(page, text, size, opacity, color, angle):
    r = page.rect
    c = pymupdf.Point(r.x0 + r.width / 2, r.y0 + r.height / 2)
    w = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    page.insert_text((c.x - w / 2, c.y + size * 0.35), text, fontname="helv", fontsize=size,
                     color=color, fill_opacity=opacity, morph=(c, pymupdf.Matrix(angle)), overlay=True)


def _stamp_number(page, text, size, position, margin=28):
    r = page.rect
    w = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
    vert, horiz = position.split("-")
    x = {"left": margin, "center": (r.width - w) / 2, "right": r.width - margin - w}[horiz]
    y = margin + size if vert == "top" else r.height - margin
    page.insert_text((x, y), text, fontname="helv", fontsize=size, color=(0.2, 0.2, 0.2))


def edit(src, out_dir, o):
    """Apply customisations in a fixed order: pages -> rotate -> watermark -> numbers -> metadata -> compress/encrypt."""
    with _open(src, o.get("current_password")) as doc:
        if o.get("page_order"):
            doc.select([i for g in parse_ranges(o["page_order"], doc.page_count) for i in g])
        if o.get("remove_pages"):
            drop = {i for g in parse_ranges(o["remove_pages"], doc.page_count) for i in g}
            if len(drop) >= doc.page_count:
                raise UserError("Can't remove every page.")
            doc.delete_pages(sorted(drop))

        if int(o.get("rotate") or 0):
            targets = parse_ranges(o.get("rotate_pages"), doc.page_count) if o.get("rotate_pages") else [range(doc.page_count)]
            for i in {i for g in targets for i in g}:
                page = doc[i]
                page.set_rotation((page.rotation + int(o["rotate"])) % 360)

        wm, numbers = (o.get("watermark") or "").strip(), o.get("page_numbers")
        if wm or numbers:
            start, fmt = int(o.get("number_start") or 1), o.get("number_format") or "{n}"
            total = doc.page_count + start - 1
            for idx, page in enumerate(doc):
                page.remove_rotation()  # draw in visual coordinates
                if wm:
                    _stamp_watermark(page, wm, float(o.get("wm_size") or 60), float(o.get("wm_opacity") or 0.2),
                                     _hex_color(o.get("wm_color")), float(o.get("wm_angle") or 45))
                if numbers:
                    _stamp_number(page, fmt.replace("{n}", str(idx + start)).replace("{total}", str(total)),
                                  float(o.get("number_size") or 10), o.get("number_position") or "bottom-center")

        meta = {k: o[k] for k in ("title", "author", "subject", "keywords") if o.get(k)}
        if meta:
            doc.set_metadata({**doc.metadata, **meta})
        if o.get("compress") and o["compress"] != "none":
            _shrink(doc, o["compress"], o.get("grayscale"))
        return _save(doc, os.path.join(out_dir, _stem(src) + "_edited.pdf"), password=o.get("new_password"))


# ---------------------------------------------------------------- compress

LEVELS = {  # image dpi threshold/target, jpeg quality
    "low": (250, 200, 80),
    "medium": (170, 150, 65),
    "high": (110, 96, 45),
}


def _shrink(doc, level, grayscale=False):
    threshold, target, quality = LEVELS[level]
    doc.rewrite_images(dpi_threshold=threshold, dpi_target=target, quality=quality, set_to_gray=bool(grayscale))
    try:
        doc.subset_fonts()
    except Exception:
        pass  # some fonts can't be subset; not fatal


def compress(src, out_dir, level="medium", grayscale=False):
    out = os.path.join(out_dir, _stem(src) + "_compressed.pdf")
    with _open(src) as doc:
        _shrink(doc, level, grayscale)
        _save(doc, out)
    if os.path.getsize(out) >= os.path.getsize(src):
        shutil.copyfile(src, out)  # already optimal; don't make it bigger
    return out


# ---------------------------------------------------------------- extract images

RAW_OK = {"jpeg", "jpg", "png", "gif", "bmp", "tiff", "webp"}  # formats we can hand back untouched


def extract_images(src, out_dir, min_size=64, fmt="keep", password=""):
    """Save each embedded image once, at its original resolution. Images smaller than min_size px are skipped."""
    outs, seen = [], set()
    with _open(src, password) as doc:
        for pno, page in enumerate(doc, 1):
            for n, img in enumerate(page.get_images(full=True), 1):
                xref, smask = img[0], img[1]
                if xref in seen:
                    continue  # same image used on several pages
                seen.add(xref)
                info = doc.extract_image(xref)
                if not info or min(info["width"], info["height"]) < int(min_size or 0):
                    continue
                ext = "jpg" if info["ext"] == "jpeg" else info["ext"]
                base = os.path.join(out_dir, f"{_stem(src)}_p{pno}_{n}")

                if fmt == "keep" and not smask and ext in RAW_OK:
                    with open(f"{base}.{ext}", "wb") as f:
                        f.write(info["image"])
                    outs.append(f"{base}.{ext}")
                    continue

                # needs re-encoding: transparency mask, CMYK/JPX/JBIG2 sources, or a chosen format
                pix = pymupdf.Pixmap(doc, xref)
                if smask:
                    try:
                        pix = pymupdf.Pixmap(pix, pymupdf.Pixmap(doc, smask))
                    except Exception:
                        pass  # mask doesn't match; keep the image without it
                if pix.colorspace and pix.colorspace.n > 3:
                    pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                out_ext = "png" if fmt == "keep" else fmt
                if out_ext in ("jpg", "jpeg") and pix.alpha:
                    pix = pymupdf.Pixmap(pix, 0)  # JPEG has no alpha
                path = f"{base}.{out_ext}"
                if out_ext in ("png", "jpg", "jpeg"):
                    pix.save(path)
                else:
                    pix.pil_save(path, format={"webp": "WEBP", "tiff": "TIFF"}.get(out_ext, out_ext.upper()))
                outs.append(path)
    if not outs:
        raise UserError(f"No images found in {os.path.basename(src)}"
                        + (f" (images under {min_size}px are skipped)." if min_size else "."))
    return outs
