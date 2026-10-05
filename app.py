import io
import json
import os
import re
import shutil
import tempfile

from flask import Flask, jsonify, request, send_file
from werkzeug.utils import secure_filename

import converters as cv
from office import APPS as OFFICE_EXTS, office_available

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024  # 200 MB

IMAGE_FORMATS = ("png", "jpg", "jpeg", "webp", "tiff", "bmp", "gif", "avif")
PDF_TARGETS = {
    "docx": cv.pdf_to_docx,
    "xlsx": cv.pdf_to_xlsx,
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
    except (BadRequest, ValueError) as e:
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



if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False, threaded=True)
