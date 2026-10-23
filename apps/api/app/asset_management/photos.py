from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, UnidentifiedImageError

ASSET_PHOTO_MAX_BYTES = 10 * 1024 * 1024
ASSET_PHOTO_MAX_PIXELS = 50_000_000

_FORMATS: dict[str, tuple[Literal["image/jpeg", "image/png", "image/webp"], Literal["jpg", "png", "webp"]]] = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


class AssetPhotoValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ValidatedAssetPhoto:
    content: bytes
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    extension: Literal["jpg", "png", "webp"]
    byte_size: int
    sha256: str


def validate_asset_photo(content: bytes, declared_mime_type: str | None) -> ValidatedAssetPhoto:
    if not content:
        raise AssetPhotoValidationError("empty")
    if len(content) > ASSET_PHOTO_MAX_BYTES:
        raise AssetPhotoValidationError("size")
    declared = (declared_mime_type or "").split(";", 1)[0].strip().lower() or None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                details = _FORMATS.get(image.format or "")
                if details is None:
                    raise AssetPhotoValidationError("format")
                if image.width < 1 or image.height < 1 or image.width * image.height > ASSET_PHOTO_MAX_PIXELS:
                    raise AssetPhotoValidationError("pixels")
                image.verify()
    except AssetPhotoValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise AssetPhotoValidationError("pixels") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise AssetPhotoValidationError("decode") from exc
    mime_type, extension = details
    if declared is not None and declared != mime_type:
        raise AssetPhotoValidationError("mime")
    return ValidatedAssetPhoto(
        content=content,
        mime_type=mime_type,
        extension=extension,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
