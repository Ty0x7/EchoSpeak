"""Bounded project presentation metadata. Never moves the attached folder."""
import base64
import binascii
import io
import warnings


def display_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 120 or any(ord(c) < 32 for c in name):
        raise ValueError("Use a project name of 1 to 120 characters")
    return name


def logo_data_url(value: str) -> str:
    if not value:
        return ""
    if len(value) > 2_800_000:
        raise ValueError("Choose a PNG, JPEG or WebP logo smaller than 2 MB")
    header, _, encoded = value.partition(",")
    if header not in {"data:image/png;base64", "data:image/jpeg;base64", "data:image/webp;base64"}:
        raise ValueError("Choose a PNG, JPEG or WebP logo")
    from PIL import Image, ImageOps, UnidentifiedImageError
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError("Choose a logo smaller than 2 MB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"} or image.width * image.height > 16_000_000:
                    raise ValueError("Choose a PNG, JPEG or WebP logo up to 16 megapixels")
                image = ImageOps.exif_transpose(image).convert("RGBA")
                image.thumbnail((256, 256))
                output = io.BytesIO()
                # Re-encode pixels only: no original EXIF, scripts, animation or file path.
                image.save(output, "PNG", optimize=True)
        return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except (binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("This logo could not be read. Choose a valid PNG, JPEG or WebP image") from exc
