from __future__ import annotations

import hashlib
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import Literal

from PIL import Image, UnidentifiedImageError

INVOICE_FILE_MAX_BYTES = 20 * 1024 * 1024
INVOICE_IMAGE_MAX_PIXELS = 50_000_000

InvoiceMimeType = Literal[
    "application/pdf",
    "image/jpeg",
    "image/png",
    "image/webp",
]
InvoiceExtension = Literal["pdf", "jpg", "png", "webp"]

_IMAGE_FORMATS: dict[str, tuple[InvoiceMimeType, InvoiceExtension]] = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}


class InvoiceFileValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ValidatedInvoiceFile:
    content: bytes
    original_filename: str
    mime_type: InvoiceMimeType
    extension: InvoiceExtension
    byte_size: int
    sha256: str


def _safe_filename(filename: str | None) -> str:
    normalized = (filename or "").replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not normalized or len(normalized) > 255 or any(ord(char) < 32 for char in normalized):
        raise InvoiceFileValidationError("filename")
    return normalized


def _validate_image(content: bytes) -> tuple[InvoiceMimeType, InvoiceExtension]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                details = _IMAGE_FORMATS.get(image.format or "")
                if details is None:
                    raise InvoiceFileValidationError("format")
                if (
                    image.width < 1
                    or image.height < 1
                    or image.width * image.height > INVOICE_IMAGE_MAX_PIXELS
                ):
                    raise InvoiceFileValidationError("pixels")
                image.verify()
    except InvoiceFileValidationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise InvoiceFileValidationError("pixels") from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise InvoiceFileValidationError("decode") from exc
    return details


def validate_invoice_file(
    content: bytes,
    declared_mime_type: str | None,
    filename: str | None,
) -> ValidatedInvoiceFile:
    if not content:
        raise InvoiceFileValidationError("empty")
    if len(content) > INVOICE_FILE_MAX_BYTES:
        raise InvoiceFileValidationError("size")
    original_filename = _safe_filename(filename)
    declared = (declared_mime_type or "").split(";", 1)[0].strip().lower() or None

    if content.startswith(b"%PDF-"):
        if b"%%EOF" not in content[-4096:]:
            raise InvoiceFileValidationError("decode")
        mime_type: InvoiceMimeType = "application/pdf"
        extension: InvoiceExtension = "pdf"
    else:
        mime_type, extension = _validate_image(content)

    if declared is not None and declared not in {mime_type, "application/octet-stream"}:
        raise InvoiceFileValidationError("mime")
    return ValidatedInvoiceFile(
        content=content,
        original_filename=original_filename,
        mime_type=mime_type,
        extension=extension,
        byte_size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
