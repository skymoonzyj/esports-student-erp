"""Bounded, metadata-free housing photos; intentionally separate from asset photos."""

from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_PHOTO_BYTES = 10 * 1024 * 1024
MAX_PHOTO_PIXELS = 50_000_000
_MIME_BY_FORMAT = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


class RoomPhotoValidationError(ValueError):
    """An upload cannot be safely decoded and re-encoded as a housing photo."""


@dataclass(frozen=True)
class PreparedRoomPhoto:
    content: bytes
    mime_type: str
    byte_size: int
    sha256: str
    thumbnail_content: bytes
    thumbnail_mime_type: str
    thumbnail_byte_size: int


def _encode(image: Image.Image, format: str) -> bytes:
    output = BytesIO()
    options = {"quality": 90} if format in {"JPEG", "WEBP"} else {}
    image.save(output, format=format, **options)
    content = output.getvalue()
    if not content or len(content) > MAX_PHOTO_BYTES:
        raise RoomPhotoValidationError("encoded photo exceeds size limit")
    return content


def prepare_room_photo(content: bytes, declared_mime_type: str | None) -> PreparedRoomPhoto:
    if not content or len(content) > MAX_PHOTO_BYTES:
        raise RoomPhotoValidationError("photo must be between 1 byte and 10 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as probe:
                format = probe.format or ""
                mime = _MIME_BY_FORMAT.get(format)
                if mime is None or declared_mime_type not in {None, mime}:
                    raise RoomPhotoValidationError("unsupported or mismatched photo format")
                if probe.width * probe.height > MAX_PHOTO_PIXELS:
                    raise RoomPhotoValidationError("photo exceeds pixel limit")
                if getattr(probe, "n_frames", 1) != 1:
                    raise RoomPhotoValidationError("animated photos are not supported")
                probe.verify()
            with Image.open(BytesIO(content)) as decoded:
                decoded.load()
                oriented = ImageOps.exif_transpose(decoded)
                mode = (
                    "RGBA"
                    if format != "JPEG"
                    and ("A" in oriented.getbands() or "transparency" in oriented.info)
                    else "RGB"
                )
                converted = oriented.convert(mode)
                # New pixel storage intentionally drops EXIF, ICC, XMP and decoder info.
                pixels = Image.new(mode, converted.size)
                pixels.paste(converted)
                encoded = _encode(pixels, format)
                pixels.thumbnail((480, 480), Image.Resampling.LANCZOS)
                thumbnail = _encode(pixels, format)
    except RoomPhotoValidationError:
        raise
    except (
        UnidentifiedImageError,
        OSError,
        ValueError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as exc:
        raise RoomPhotoValidationError("invalid or unsafe photo") from exc
    return PreparedRoomPhoto(
        content=encoded,
        mime_type=mime,
        byte_size=len(encoded),
        sha256=hashlib.sha256(encoded).hexdigest(),
        thumbnail_content=thumbnail,
        thumbnail_mime_type=mime,
        thumbnail_byte_size=len(thumbnail),
    )
