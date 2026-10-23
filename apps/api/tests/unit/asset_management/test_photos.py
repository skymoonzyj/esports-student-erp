from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from app.asset_management.photos import (
    ASSET_PHOTO_MAX_BYTES,
    AssetPhotoValidationError,
    validate_asset_photo,
)


def image_bytes(image_format: str) -> bytes:
    output = BytesIO()
    Image.new("RGB", (3, 2), color=(20, 40, 80)).save(output, format=image_format)
    return output.getvalue()


def test_validates_real_image_format_and_metadata() -> None:
    content = image_bytes("PNG")
    photo = validate_asset_photo(content, "image/png")
    assert photo.mime_type == "image/png"
    assert photo.extension == "png"
    assert photo.byte_size == len(content)
    assert len(photo.sha256) == 64


@pytest.mark.parametrize(
    ("content_type", "expected_code"),
    [("image/jpeg", "mime"), ("image/png", "decode")],
)
def test_rejects_declared_or_actual_type_mismatch(content_type: str, expected_code: str) -> None:
    content = image_bytes("PNG") if expected_code == "mime" else b"not an image"
    with pytest.raises(AssetPhotoValidationError) as error:
        validate_asset_photo(content, content_type)
    assert error.value.code == expected_code


def test_rejects_oversized_photo() -> None:
    with pytest.raises(AssetPhotoValidationError) as error:
        validate_asset_photo(b"x" * (ASSET_PHOTO_MAX_BYTES + 1), "image/png")
    assert error.value.code == "size"
