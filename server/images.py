"""Image validation independent of model inference."""
import io
import warnings

from PIL import Image, ImageOps

_MAX_PIXELS = 20_000_000


class ImageError(Exception):
    pass


def validate_image(raw: bytes, mode: str = "RGB"):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > _MAX_PIXELS:
                    raise ImageError("IMAGE_TOO_LARGE")
                return ImageOps.exif_transpose(source).convert(mode)
    except ImageError:
        raise
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageError("BAD_IMAGE") from exc



def evaluation_image(raw: bytes, mime: str) -> tuple[bytes, str]:
    """Bound Router payload size. Originals remain untouched for hashes/receipts.

    Preserve small payloads; downscale to a 1024px long edge and compress large payloads.
    Composite alpha onto white before JPEG conversion to preserve visible content.
    """
    image = validate_image(raw, mode="RGBA")
    needs_resize = max(image.size) > 1024
    try:
        if max(image.size) <= 1024 and len(raw) <= 300_000:
            return raw, mime
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        with Image.new("RGB", image.size, "white") as background:
            background.paste(image, mask=image.getchannel("A"))
            out = io.BytesIO()
            background.save(out, format="JPEG", quality=85)
        compressed = out.getvalue()
        if not needs_resize and len(compressed) >= len(raw):
            return raw, mime
        return compressed, "image/jpeg"
    finally:
        image.close()
