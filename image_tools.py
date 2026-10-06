"""Image resize / compress / enhance with Pillow."""
import io
import os

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from pillow_heif import register_heif_opener

register_heif_opener()

# output extension -> (Pillow format, supports quality, supports alpha)
FORMATS = {
    "jpg": ("JPEG", True, False), "jpeg": ("JPEG", True, False), "png": ("PNG", False, True),
    "webp": ("WEBP", True, True), "avif": ("AVIF", True, True), "tiff": ("TIFF", False, True),
    "bmp": ("BMP", False, False), "gif": ("GIF", False, True),
}
# input formats Pillow can read but we don't write -> fall back to this
WRITE_FALLBACK = {"heic": "jpg", "heif": "jpg", "jfif": "jpg", "tif": "tiff", "ico": "png"}


def _out_fmt(src, fmt):
    if fmt and fmt != "keep":
        return fmt
    ext = os.path.splitext(src)[1].lower().lstrip(".")
    return WRITE_FALLBACK.get(ext, ext if ext in FORMATS else "png")


def _open(src):
    img = Image.open(src)
    img = ImageOps.exif_transpose(img)  # respect camera rotation
    if img.mode not in ("RGB", "RGBA", "L"):
        img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    return img


def _encode(img, fmt, quality=90, optimize=True):
    pil_fmt, has_q, has_alpha = FORMATS[fmt]
    if img.mode == "RGBA" and not has_alpha:
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.getchannel("A"))
        img = bg
    kw = {}
    if has_q:
        kw["quality"] = int(quality)
    if pil_fmt == "JPEG":
        kw.update(optimize=optimize, progressive=True)
    elif pil_fmt == "PNG":
        kw.update(optimize=optimize)
    elif pil_fmt == "WEBP":
        kw.update(method=6)
    buf = io.BytesIO()
    img.save(buf, pil_fmt, **kw)
    return buf.getvalue()


def _write(src, out_dir, suffix, fmt, data):
    stem = os.path.splitext(os.path.basename(src))[0]
    out = os.path.join(out_dir, f"{stem}{suffix}.{fmt}")
    with open(out, "wb") as f:
        f.write(data)
    return out


def resize(src, out_dir, mode="percent", percent=50, width=0, height=0, fit="contain",
           fmt="keep", quality=90):
    """mode: percent | dimensions. fit: contain (keep aspect, fit inside), exact (stretch), cover (crop to fill)."""
    img = _open(src)
    w0, h0 = img.size
    if mode == "percent":
        size = (max(1, round(w0 * percent / 100)), max(1, round(h0 * percent / 100)))
        img = img.resize(size, Image.LANCZOS)
    else:
        width, height = int(width or 0), int(height or 0)
        if not width and not height:
            raise ValueError("Enter a width and/or height.")
        if not width or not height:  # one side given -> scale proportionally
            r = (width / w0) if width else (height / h0)
            img = img.resize((max(1, round(w0 * r)), max(1, round(h0 * r))), Image.LANCZOS)
        elif fit == "exact":
            img = img.resize((width, height), Image.LANCZOS)
        elif fit == "cover":
            img = ImageOps.fit(img, (width, height), Image.LANCZOS)
        else:
            img = ImageOps.contain(img, (width, height), Image.LANCZOS)
    fmt = _out_fmt(src, fmt)
    return _write(src, out_dir, f"_{img.width}x{img.height}", fmt, _encode(img, fmt, quality))


def compress(src, out_dir, level="medium", target_kb=0, max_dim=0, fmt="keep"):
    """Shrink an image. target_kb > 0 searches for the highest quality that fits."""
    original_size = os.path.getsize(src)
    img = _open(src)
    max_dim = int(max_dim or 0)
    if max_dim and max(img.size) > max_dim:
        img = ImageOps.contain(img, (max_dim, max_dim), Image.LANCZOS)
    fmt = _out_fmt(src, fmt)
    _, has_q, _ = FORMATS[fmt]

    if has_q:
        if target_kb:
            target = int(target_kb) * 1024
            lo, hi, best = 5, 95, None
            while lo <= hi:  # binary search on quality
                q = (lo + hi) // 2
                data = _encode(img, fmt, q)
                if len(data) <= target:
                    best, lo = data, q + 1
                else:
                    hi = q - 1
            data = best or _encode(img, fmt, 5)
        else:
            data = _encode(img, fmt, {"low": 85, "medium": 70, "high": 50}[level])
    elif fmt == "png" and level != "low":
        # lossy PNG: reduce to a 256-colour palette
        q = img.convert("RGBA").quantize(colors=256 if level == "medium" else 128, method=Image.FASTOCTREE)
        data = _encode(q, fmt)
    else:
        data = _encode(img, fmt)

    norm = {"jpeg": "jpg", "jfif": "jpg", "tif": "tiff"}
    src_ext = os.path.splitext(src)[1].lower().lstrip(".")
    same_fmt = norm.get(src_ext, src_ext) == norm.get(fmt, fmt)
    if same_fmt and len(data) >= original_size and not max_dim:
        with open(src, "rb") as f:  # couldn't beat the original
            data = f.read()
    return _write(src, out_dir, "_compressed", fmt, data)


def enhance(src, out_dir, upscale=2, sharpen=1.0, denoise=False, auto_contrast=True,
            color=1.1, brightness=1.0, fmt="keep", quality=95):
    img = _open(src)
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    rgb = img.convert("RGB") if img.mode != "L" else img

    if denoise:
        rgb = rgb.filter(ImageFilter.MedianFilter(3))
    upscale = float(upscale or 1)
    if upscale > 1:
        size = (round(rgb.width * upscale), round(rgb.height * upscale))
        rgb = rgb.resize(size, Image.LANCZOS)
        if alpha is not None:
            alpha = alpha.resize(size, Image.LANCZOS)
    if auto_contrast:
        rgb = ImageOps.autocontrast(rgb, cutoff=0.5, preserve_tone=True)
    if brightness != 1:
        rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
    if color != 1 and rgb.mode == "RGB":
        rgb = ImageEnhance.Color(rgb).enhance(color)
    if sharpen > 0:
        rgb = rgb.filter(ImageFilter.UnsharpMask(radius=1.5 + upscale / 2, percent=int(60 * sharpen), threshold=2))

    if alpha is not None:
        rgb = rgb.convert("RGBA")
        rgb.putalpha(alpha)
    fmt = _out_fmt(src, fmt)
    return _write(src, out_dir, "_enhanced", fmt, _encode(rgb, fmt, quality))
