"""OCR for scanned PDFs and images using RapidOCR (no system install needed)."""
import os
import threading

import numpy as np
import pymupdf

_engine = None
_lock = threading.Lock()  # the ONNX engine isn't safe to share across threads


def _get_engine():
    global _engine
    if _engine is None:
        from rapidocr import RapidOCR  # slow import; only load when OCR is used
        _engine = RapidOCR()
    return _engine


def _stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def ocr_page(page, dpi=200):
    """Return [(Rect in page coordinates, text)] for each line of text found on the page."""
    dpi = int(dpi)  # form values arrive as strings
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)[:, :, ::-1]  # RGB -> BGR
    with _lock:
        result = _get_engine()(np.ascontiguousarray(img))
    if result.boxes is None:
        return []
    scale = 72 / dpi
    lines = []
    for box, text in zip(result.boxes, result.txts):
        xs, ys = box[:, 0] * scale, box[:, 1] * scale
        lines.append((pymupdf.Rect(xs.min(), ys.min(), xs.max(), ys.max()), text))
    # reading order: top to bottom, then left to right
    lines.sort(key=lambda l: (round(l[0].y0 / 8), l[0].x0))
    return lines


def _has_text(page, min_chars=20):
    return len(page.get_text().strip()) >= min_chars


def _open_any(src):
    """Open a PDF, or wrap an image as a one-page PDF."""
    if os.path.splitext(src)[1].lower() == ".pdf":
        return pymupdf.open(src)
    from converters import images_to_pdf
    out_dir = os.path.dirname(src)
    return pymupdf.open(images_to_pdf([src], out_dir, page_size="fit", margin="none"))


def make_searchable(src, out_dir, dpi=200, skip_text_pages=True, **_):
    """Add an invisible, selectable text layer over each scanned page."""
    out = os.path.join(out_dir, _stem(src) + "_searchable.pdf")
    with _open_any(src) as doc:
        for page in doc:
            if skip_text_pages and _has_text(page):
                continue  # already has real text
            page.remove_rotation()  # so OCR coordinates match drawing coordinates
            for rect, text in ocr_page(page, dpi):
                size = max(rect.height * 0.85, 1)
                natural = pymupdf.get_text_length(text, fontname="helv", fontsize=size)
                if not natural:
                    continue
                origin = pymupdf.Point(rect.x0, rect.y1 - rect.height * 0.2)
                # stretch horizontally so the selection box matches the printed words
                page.insert_text(origin, text, fontname="helv", fontsize=size, render_mode=3,
                                 morph=(origin, pymupdf.Matrix(rect.width / natural, 1)))
        doc.save(out, garbage=3, deflate=True)
    return out


def extract_text(src, out_dir, dpi=200, skip_text_pages=True, **_):
    """OCR to a plain .txt file (pages separated by form feeds)."""
    out = os.path.join(out_dir, _stem(src) + "_ocr.txt")
    with _open_any(src) as doc, open(out, "w", encoding="utf-8") as f:
        for i, page in enumerate(doc):
            if i:
                f.write("\n\f\n")
            if skip_text_pages and _has_text(page):
                f.write(page.get_text())
                continue
            row_y, row = None, []
            for rect, text in ocr_page(page, dpi):  # join words that sit on the same line
                if row_y is not None and abs(rect.y0 - row_y) > rect.height * 0.5:
                    f.write("  ".join(row) + "\n")
                    row = []
                row_y = rect.y0 if not row else row_y
                row.append(text)
            if row:
                f.write("  ".join(row) + "\n")
    return out
