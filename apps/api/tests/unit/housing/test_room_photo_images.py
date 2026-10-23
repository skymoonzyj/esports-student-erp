from __future__ import annotations

import hashlib
from io import BytesIO

import pytest
from PIL import Image

from app.housing.room_photo_images import RoomPhotoValidationError, prepare_room_photo


def image_bytes(format: str = "PNG", size: tuple[int, int] = (40, 20)) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, "red").save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize(
    ("content", "mime"),
    [
        (b"", "image/png"),
        (b"not an image", "image/png"),
        (image_bytes(), "image/jpeg"),
        (image_bytes(), "text/plain"),
        (b"x" * (10 * 1024 * 1024 + 1), "image/png"),
        (image_bytes("GIF"), "image/gif"),
    ],
)
def test_rejects_invalid_upload_without_returning_bytes(content: bytes, mime: str) -> None:
    with pytest.raises(RoomPhotoValidationError):
        prepare_room_photo(content, mime)


def test_rejects_image_over_fifty_million_pixels() -> None:
    output = BytesIO()
    Image.new("1", (10_001, 5_000)).save(output, format="PNG")
    with pytest.raises(RoomPhotoValidationError):
        prepare_room_photo(output.getvalue(), "image/png")


def test_rejects_multiframe_png() -> None:
    output = BytesIO()
    Image.new("RGB", (20, 20), "red").save(
        output,
        format="PNG",
        save_all=True,
        append_images=[Image.new("RGB", (20, 20), "blue")],
        duration=100,
    )
    with pytest.raises(RoomPhotoValidationError):
        prepare_room_photo(output.getvalue(), "image/png")


def test_rejects_truncated_real_image() -> None:
    content = image_bytes()
    with pytest.raises(RoomPhotoValidationError):
        prepare_room_photo(content[:-20], "image/png")


def test_reencoded_original_must_still_fit_byte_limit(monkeypatch) -> None:
    import app.housing.room_photo_images as images

    output = BytesIO()
    Image.new("1", (300, 300)).save(output, format="PNG")
    content = output.getvalue()
    # A small palette input expands on RGB re-encoding. Lower the resource
    # limit rather than allocate a noisy fifty-million-pixel image in unit tests.
    monkeypatch.setattr(images, "MAX_PHOTO_BYTES", len(content))
    with pytest.raises(RoomPhotoValidationError):
        prepare_room_photo(content, "image/png")


def test_orientation_six_is_corrected_and_private_metadata_removed() -> None:
    output = BytesIO()
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "private photographer"
    exif[34853] = {1: "N", 2: (1.0, 2.0, 3.0)}
    Image.new("RGB", (60, 30), "red").save(
        output, format="JPEG", exif=exif, icc_profile=b"private ICC"
    )
    prepared = prepare_room_photo(output.getvalue(), "image/jpeg")
    for content in (prepared.content, prepared.thumbnail_content):
        with Image.open(BytesIO(content)) as decoded:
            decoded.load()
            assert decoded.size == (30, 60)
            assert not decoded.getexif()
            assert not {"exif", "icc_profile", "xmp"}.intersection(decoded.info)
    assert prepared.byte_size == len(prepared.content)
    assert prepared.sha256 == hashlib.sha256(prepared.content).hexdigest()


def test_transparent_png_retains_alpha() -> None:
    output = BytesIO()
    Image.new("RGBA", (20, 10), (200, 10, 50, 0)).save(output, format="PNG")
    prepared = prepare_room_photo(output.getvalue(), "image/png")
    with Image.open(BytesIO(prepared.content)) as decoded:
        assert decoded.mode == "RGBA"
        assert decoded.getpixel((0, 0)) == (200, 10, 50, 0)


@pytest.mark.parametrize(
    "format,mime", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")]
)
def test_thumbnail_long_edge_at_most_480(format: str, mime: str) -> None:
    prepared = prepare_room_photo(image_bytes(format, (1200, 600)), mime)
    with Image.open(BytesIO(prepared.thumbnail_content)) as decoded:
        decoded.load()
        assert decoded.size == (480, 240)
    assert prepared.thumbnail_byte_size == len(prepared.thumbnail_content)
    assert prepared.thumbnail_mime_type == mime


def test_thumbnail_does_not_upscale_and_missing_declared_mime_is_safe() -> None:
    prepared = prepare_room_photo(image_bytes(size=(20, 10)), None)
    with Image.open(BytesIO(prepared.thumbnail_content)) as decoded:
        assert decoded.size == (20, 10)
