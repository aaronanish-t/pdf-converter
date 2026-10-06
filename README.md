# PDF Toolkit

Local web app to convert, merge, split, edit and compress PDFs, and resize, compress or enhance images.

## Run

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python app.py
```

Open http://localhost:5000

## Supported conversions

**PDF →** DOCX, XLSX (detected tables), TXT, HTML, PNG, JPG/JPEG, WEBP, TIFF, BMP, GIF, AVIF, SVG
(multi-page image exports are zipped, one file per page)

**→ PDF**
- Images: PNG, JPG/JPEG, JFIF, WEBP, BMP, GIF, TIFF, HEIC/HEIF, AVIF, ICO (multiple images merge into one PDF)
- Office: DOC/DOCX, RTF, ODT, XLS/XLSX, ODS, PPT/PPTX, ODP (uses installed Microsoft Office; DOCX/XLSX fall back to a pure-Python converter without it)
- Text: TXT, Markdown, HTML
- Other: SVG, EPUB, XPS, FB2, MOBI, CBZ

## PDF tools

- **Merge** — combine PDFs (plus images/documents, converted on the fly) in any order, with optional bookmarks
- **Split** — every page, custom ranges (`1-3, 4-6, 7-end`), or every N pages
- **Edit** — reorder/remove pages, rotate, text watermark (colour/opacity/size/angle), page numbers,
  set/remove password (AES-256), title/author metadata, compression
- **Compress** — light/balanced/strong; downsamples images and subsets fonts. Never returns a bigger file.
- **Page setup** for →PDF conversions: A3/A4/A5/Letter/Legal, orientation, margins

## Image tools

- **Resize** — by percentage or pixels (fit inside / fill & crop / stretch), any output format
- **Compress** — quality level or a target size in KB, max dimension, convert to WEBP/AVIF
- **Enhance** — 2–4× Lanczos upscale, sharpen, denoise, auto-contrast, colour and brightness

## API

All endpoints take a multipart form with one or more `files` and an optional `options` JSON string,
and return the result file (a `.zip` when there are several outputs).

| Endpoint | Notes |
|---|---|
| `POST /api/convert` | plus `target` (`pdf`, `docx`, `png`, …) |
| `POST /api/merge` | `{"bookmarks": true}` |
| `POST /api/split` | `{"mode": "each" \| "ranges" \| "every", "ranges": "1-3,4-end", "every": 2}` |
| `POST /api/edit` | see `pdf_tools.edit` |
| `POST /api/compress` | `{"level": "low" \| "medium" \| "high", "target_kb": 200}` |
| `POST /api/resize` | `{"mode": "percent", "percent": 50}` |
| `POST /api/enhance` | `{"upscale": 2, "sharpen": 1}` |
