import io
import json
import os
import re
import shutil
import tempfile

import pymupdf
from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename

import converters as cv
import image_tools
import ocr
import pdf_tools
import signing
from office import APPS as OFFICE_EXTS, office_available

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

IMAGE_FORMATS = ("png", "jpg", "jpeg", "webp", "tiff", "bmp", "gif", "avif")
PDF_TARGETS = {
    "docx": cv.pdf_to_docx,
    "xlsx": cv.pdf_to_xlsx,
    "pptx": cv.pdf_to_pptx,
    **{fmt: (lambda f: lambda s, o, **kw: cv.pdf_to_images(s, o, f, **kw))(fmt) for fmt in IMAGE_FORMATS},
    "svg": cv.pdf_to_svg,
    "txt": cv.pdf_to_txt,
    "html": cv.pdf_to_html,
}
TO_PDF = {".txt": cv.txt_to_pdf, ".md": cv.md_to_pdf, ".markdown": cv.md_to_pdf,
          ".html": cv.html_to_pdf, ".htm": cv.html_to_pdf}
TO_PDF.update({ext: cv.office_to_pdf for ext in OFFICE_EXTS})
TO_PDF.update({ext: cv.document_to_pdf for ext in cv.DOCUMENT_EXTS})


class BadRequest(Exception):
    pass


def _ext(name):
    return os.path.splitext(name)[1].lower()


def _require(srcs, allowed, what):
    bad = sorted({_ext(s) for s in srcs} - set(allowed))
    if bad:
        raise BadRequest(f"{what} doesn't accept {', '.join(bad)} files.")


def _as_pdf(src, out_dir):
    """Convert any supported file to PDF (used so merge accepts images/documents too)."""
    ext = _ext(src)
    if ext == ".pdf":
        return src
    if ext in cv.IMAGE_EXTS:
        return cv.images_to_pdf([src], out_dir)
    return TO_PDF[ext](src, out_dir)


def run(handler, label):
    """Save uploads to a temp dir, call handler(srcs, out_dir, opts) -> [paths], send back one file or a zip."""
    files = [f for f in request.files.getlist("files") if f.filename]
    if not files:
        return jsonify(error="No files uploaded."), 400
    try:
        opts = json.loads(request.form.get("options") or "{}")
    except ValueError:
        return jsonify(error="Invalid options."), 400

    work = tempfile.mkdtemp(prefix="pdfconv_")
    try:
        in_dir, out_dir = os.path.join(work, "in"), os.path.join(work, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)
        srcs = []
        for i, f in enumerate(files):
            # one folder per upload keeps original names without collisions
            sub = os.path.join(in_dir, str(i))
            os.makedirs(sub)
            stem = secure_filename(os.path.splitext(f.filename)[0]) or "file"
            ext = _ext(f.filename)
            path = os.path.join(sub, stem + (ext if re.fullmatch(r"\.[a-z0-9]{1,10}", ext) else ""))
            f.save(path)
            srcs.append(path)

        outputs = handler(srcs, out_dir, opts)
        if len(outputs) == 1:
            result = outputs[0]
        else:
            stem = os.path.splitext(os.path.basename(srcs[0]))[0] if len(srcs) == 1 else label
            result = cv._zip(outputs, os.path.join(out_dir, f"{stem}_{label}.zip" if len(srcs) == 1 else f"{label}.zip"))
        with open(result, "rb") as fh:
            data = fh.read()
        resp = send_file(io.BytesIO(data), as_attachment=True, download_name=os.path.basename(result))
        resp.headers["X-Original-Size"] = str(sum(os.path.getsize(s) for s in srcs))
        resp.headers["X-Result-Size"] = str(len(data))
        resp.headers["Access-Control-Expose-Headers"] = "X-Original-Size, X-Result-Size, Content-Disposition"
        return resp
    except (BadRequest, pdf_tools.UserError, ValueError) as e:
        return jsonify(error=str(e)), 400
    except Exception as e:
        app.logger.exception("operation failed")
        return jsonify(error=f"Failed: {e}"), 500
    finally:
        shutil.rmtree(work, ignore_errors=True)


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/formats")
def formats():
    return jsonify({
        "pdf_targets": list(PDF_TARGETS),
        "to_pdf": sorted(set(TO_PDF) | cv.IMAGE_EXTS),
        "images": sorted(cv.IMAGE_EXTS),
        "image_formats": list(IMAGE_FORMATS),
        "office": office_available(),
        "fonts": signing.available_fonts(),
    })


@app.post("/api/convert")
def convert():
    target = request.form.get("target", "pdf").lower()

    def handler(srcs, out_dir, opts):
        exts = {_ext(s) for s in srcs}
        if target != "pdf":
            if exts != {".pdf"}:
                raise BadRequest(f"Only PDF files can be converted to {target}.")
            if target not in PDF_TARGETS:
                raise BadRequest(f"Unsupported target: {target}")
            return [PDF_TARGETS[target](s, out_dir, **opts) for s in srcs]
        if exts <= cv.IMAGE_EXTS:
            return [cv.images_to_pdf(srcs, out_dir, **opts)]  # all images -> one PDF
        _require(srcs, TO_PDF, "Convert to PDF")
        return [TO_PDF[_ext(s)](s, out_dir, **opts) for s in srcs]

    return run(handler, "converted")


@app.post("/api/merge")
def merge():
    def handler(srcs, out_dir, opts):
        if len(srcs) < 2:
            raise BadRequest("Add at least two files to merge.")
        _require(srcs, {".pdf"} | set(TO_PDF) | cv.IMAGE_EXTS, "Merge")
        conv_dir = os.path.join(out_dir, "conv")
        os.makedirs(conv_dir)
        return [pdf_tools.merge([_as_pdf(s, conv_dir) for s in srcs], out_dir, opts.get("bookmarks", True))]

    return run(handler, "merged")


@app.post("/api/split")
def split():
    def handler(srcs, out_dir, opts):
        _require(srcs, {".pdf"}, "Split")
        return [p for s in srcs for p in pdf_tools.split(s, out_dir, opts.get("mode", "each"),
                                                         opts.get("ranges", ""), opts.get("every", 1))]

    return run(handler, "split")


@app.post("/api/edit")
def edit():
    def handler(srcs, out_dir, opts):
        _require(srcs, {".pdf"}, "Edit PDF")
        return [pdf_tools.edit(s, out_dir, opts) for s in srcs]

    return run(handler, "edited")


@app.post("/api/compress")
def compress():
    def handler(srcs, out_dir, opts):
        _require(srcs, {".pdf"} | cv.IMAGE_EXTS, "Compress")
        level = opts.get("level", "medium")
        return [pdf_tools.compress(s, out_dir, level, opts.get("grayscale")) if _ext(s) == ".pdf"
                else image_tools.compress(s, out_dir, level, opts.get("target_kb"), opts.get("max_dim"),
                                          opts.get("format", "keep"))
                for s in srcs]

    return run(handler, "compressed")


@app.post("/api/resize")
def resize():
    def handler(srcs, out_dir, opts):
        _require(srcs, cv.IMAGE_EXTS, "Resize")
        return [image_tools.resize(s, out_dir, **opts) for s in srcs]

    return run(handler, "resized")


@app.post("/api/enhance")
def enhance():
    def handler(srcs, out_dir, opts):
        _require(srcs, cv.IMAGE_EXTS, "Enhance")
        return [image_tools.enhance(s, out_dir, **opts) for s in srcs]

    return run(handler, "enhanced")


@app.post("/api/ocr")
def run_ocr():
    def handler(srcs, out_dir, opts):
        _require(srcs, {".pdf"} | cv.IMAGE_EXTS, "OCR")
        fn = ocr.extract_text if opts.get("output") == "txt" else ocr.make_searchable
        return [fn(s, out_dir, **opts) for s in srcs]

    return run(handler, "ocr")


@app.post("/api/extract-images")
def extract_images():
    def handler(srcs, out_dir, opts):
        _require(srcs, {".pdf"}, "Extract images")
        return [p for s in srcs for p in pdf_tools.extract_images(
            s, out_dir, opts.get("min_size", 64), opts.get("format", "keep"), opts.get("current_password", ""))]

    return run(handler, "images")


@app.post("/api/sign")
def sign():
    sig_file = request.files.get("signature")

    def handler(srcs, out_dir, opts):
        _require(srcs, {".pdf"}, "Sign")
        if opts.get("mode") == "type":
            if not (opts.get("text") or "").strip():
                raise BadRequest("Type your name to create a signature.")
            sig = signing.typed_signature(opts["text"].strip(), opts.get("font"), opts.get("color"))
        else:
            if not sig_file or not sig_file.filename:
                raise BadRequest("Upload a signature image, or switch to a typed signature.")
            if _ext(sig_file.filename) not in cv.IMAGE_EXTS:
                raise BadRequest("The signature must be an image (PNG, JPG…).")
            sig_path = os.path.join(out_dir, "signature" + _ext(sig_file.filename))
            sig_file.save(sig_path)
            sig = signing.image_signature(sig_path, opts.get("remove_background", True),
                                          opts.get("color") if opts.get("recolor") else None)
            os.remove(sig_path)
        return [signing.sign(s, out_dir, sig, opts) for s in srcs]

    return run(handler, "signed")


@app.post("/api/preview")
def preview():
    """Render one page of an uploaded PDF as PNG (used to place signatures)."""
    f = request.files.get("file")
    if not f:
        return jsonify(error="No file uploaded."), 400
    try:
        doc = pymupdf.open(stream=f.read(), filetype="pdf")
    except Exception:
        return jsonify(error="Couldn't read that PDF."), 400
    with doc:
        if doc.needs_pass and not doc.authenticate(request.form.get("password", "")):
            return jsonify(error="This PDF is password-protected."), 400
        page = doc[min(max(int(request.form.get("page", 1)), 1), doc.page_count) - 1]
        zoom = 700 / max(page.rect.width, 1)  # ~700px wide
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        resp = send_file(io.BytesIO(pix.tobytes("png")), mimetype="image/png")
        resp.headers["X-Page-Count"] = str(doc.page_count)
        return resp


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
