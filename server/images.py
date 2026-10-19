"""Image validation independent of model inference."""
import io
import warnings

from PIL import Image, ImageOps

_MAX_PIXELS = 20_000_000


class ImageError(Exception):
    pass


def validate_image(raw: bytes):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > _MAX_PIXELS:
                    raise ImageError("IMAGE_TOO_LARGE")
                return ImageOps.exif_transpose(source).convert("RGB")
    except ImageError:
        raise
    except (OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError) as exc:
        raise ImageError("BAD_IMAGE") from exc


