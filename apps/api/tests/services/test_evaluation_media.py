from __future__ import annotations

import base64
import io
import os
import tempfile
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from PIL import Image, PngImagePlugin
from pydantic import ValidationError
from sqlalchemy import Engine, create_engine, delete, event, func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.core.config import Settings
from app.evaluations import media_service as media_service_module
from app.evaluations import media_store as media_store_module
from app.evaluations.errors import MediaAssetNotFound, MediaValidationFailed
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.media_service import EvaluationMediaService
from app.evaluations.media_store import PrivateMediaStore, StoredImageFiles, ValidatedImage
from app.identity.models import Person
from app.rbac.models import UserRole
from app.rbac.seed import ROLES
from app.teaching.seed import INITIAL_PROJECTS

TEST_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
NOW = datetime(2026, 8, 1, 8, tzinfo=UTC)


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "test",
        "database_url": "postgresql+psycopg://erp@127.0.0.1:55433/erp_e2e",
        "session_cookie_secure": False,
        "field_encryption_master_key": TEST_KEY,
        "field_lookup_key": TEST_KEY,
        "session_token_pepper": TEST_KEY,
        "evaluation_tool_service_token": "evaluation-local-service-token-2026-change-me",
        "erp_web_base_url": "https://erp.example.test",
        "evaluation_tool_standalone_base_url": "https://score.example.test",
    }
    values.update(updates)
    return Settings(**values)


def test_development_media_roots_resolve_from_repository_not_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.chdir(tmp_path)

    media_root, quarantine_root = _settings().evaluation_media_paths()

    assert media_root == (REPOSITORY_ROOT / "runtime/evaluation-media").resolve()
    assert quarantine_root == (REPOSITORY_ROOT / "runtime/evaluation-quarantine").resolve()


def test_development_rejects_non_ignored_relative_media_root() -> None:
    settings = _settings(evaluation_media_root=Path("private-media"))

    with pytest.raises(ValueError, match="Git-ignored runtime directory"):
        settings.evaluation_media_paths()


@pytest.mark.parametrize(
    ("media_root", "quarantine_root", "message"),
    [
        (Path("runtime/media"), Path("/srv/erp-quarantine"), "absolute"),
        (Path("/srv/erp-media"), Path("runtime/quarantine"), "absolute"),
        (Path("/srv/erp-media"), Path("/srv/erp-media"), "distinct"),
    ],
)
def test_production_requires_absolute_distinct_media_roots(
    media_root: Path,
    quarantine_root: Path,
    message: str,
) -> None:
    settings = _settings(
        app_env="production",
        session_cookie_secure=True,
        evaluation_media_root=media_root,
        evaluation_media_quarantine_root=quarantine_root,
    )

    with pytest.raises(ValueError, match=message):
        settings.evaluation_media_paths()


@pytest.mark.parametrize(
    "forbidden_root",
    [
        REPOSITORY_ROOT / "apps/web/evaluation-media",
        REPOSITORY_ROOT / "static/evaluation-media",
        REPOSITORY_ROOT / "public/evaluation-media",
    ],
)
def test_media_roots_reject_web_and_static_directories(forbidden_root: Path) -> None:
    settings = _settings(
        evaluation_media_root=forbidden_root,
        evaluation_media_quarantine_root=REPOSITORY_ROOT / "runtime/evaluation-quarantine",
    )

    with pytest.raises(ValueError, match="private application storage"):
        settings.evaluation_media_paths()


def test_media_configuration_freezes_image_only_limits() -> None:
    settings = _settings()

    assert settings.evaluation_media_max_bytes == 20 * 1024 * 1024
    assert settings.evaluation_media_max_pixels == 50_000_000
    assert settings.evaluation_media_preview_max_edge == 1600
    assert not any(
        "video" in field_name
        for field_name in Settings.model_fields
        if field_name.startswith("evaluation_media_")
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"evaluation_media_max_bytes": 20 * 1024 * 1024 + 1},
        {"evaluation_media_max_pixels": 50_000_001},
        {"evaluation_media_preview_max_edge": 0},
        {"evaluation_media_preview_max_edge": 1601},
    ],
)
def test_media_configuration_rejects_limits_outside_database_contract(
    updates: dict[str, int],
) -> None:
    with pytest.raises(ValidationError):
        _settings(**updates)


def _synthetic_image(
    image_format: str,
    *,
    size: tuple[int, int] = (48, 32),
    color: tuple[int, int, int] = (18, 70, 130),
) -> bytes:
    image = Image.new("RGB", size, color)
    output = io.BytesIO()
    exif = Image.Exif()
    exif[0x010E] = "private synthetic metadata"
    if image_format == "PNG":
        png_info = PngImagePlugin.PngInfo()
        png_info.add_text("learner", "synthetic-private-value")
        image.save(
            output,
            format=image_format,
            exif=exif,
            icc_profile=b"synthetic-icc",
            pnginfo=png_info,
        )
    else:
        image.save(
            output,
            format=image_format,
            exif=exif,
            icc_profile=b"synthetic-icc",
        )
    return output.getvalue()


def _png_with_corrupt_idat_crc() -> bytes:
    payload = bytearray(_synthetic_image("PNG"))
    chunk_type_offset = payload.index(b"IDAT")
    chunk_size = int.from_bytes(
        payload[chunk_type_offset - 4 : chunk_type_offset],
        byteorder="big",
    )
    crc_offset = chunk_type_offset + 4 + chunk_size
    payload[crc_offset] ^= 0xFF
    return bytes(payload)


def _png_with_corrupt_iend_crc() -> bytes:
    payload = bytearray(_synthetic_image("PNG"))
    assert payload[-8:-4] == b"IEND"
    payload[-1] ^= 0xFF
    return bytes(payload)


def _png_with_duplicate_iend() -> bytes:
    payload = _synthetic_image("PNG")
    assert payload[-8:-4] == b"IEND"
    return payload + payload[-12:]


def _png_with_corrupt_first_iend_and_appended_valid_iend() -> bytes:
    payload = bytearray(_synthetic_image("PNG"))
    assert payload[-8:-4] == b"IEND"
    valid_iend = bytes(payload[-12:])
    payload[-1] ^= 0xFF
    return bytes(payload) + valid_iend


def _store(
    tmp_path: Path,
    *,
    max_bytes: int = 20 * 1024 * 1024,
    max_pixels: int = 50_000_000,
    preview_max_edge: int = 1600,
) -> PrivateMediaStore:
    return PrivateMediaStore(
        root=tmp_path / "private-media",
        quarantine_root=tmp_path / "private-quarantine",
        max_bytes=max_bytes,
        max_pixels=max_pixels,
        preview_max_edge=preview_max_edge,
    )


@pytest.mark.parametrize(
    ("image_format", "filename", "mime_type", "extension"),
    [
        ("JPEG", "match.JPG", "image/jpeg", "jpg"),
        ("PNG", "match.png", "image/png", "png"),
        ("WEBP", "match.webp", "image/webp", "webp"),
    ],
)
def test_allowed_images_use_decoded_type_and_metadata_free_webp_preview(
    tmp_path: Path,
    image_format: str,
    filename: str,
    mime_type: str,
    extension: str,
) -> None:
    payload = _synthetic_image(image_format)
    store = _store(tmp_path, preview_max_edge=24)

    validated = store.stage(io.BytesIO(payload), declared_filename=filename)
    files = store.persist(validated, blob_id=uuid4())

    assert validated.sha256
    assert validated.mime_type == mime_type
    assert validated.extension == extension
    assert validated.byte_size == len(payload)
    assert (validated.width, validated.height) == (48, 32)
    assert not validated.quarantine_path.exists()
    assert files.storage_key.startswith("objects/")
    assert files.storage_key.endswith(f".{extension}")
    assert files.preview_storage_key.startswith("previews/")
    assert files.preview_storage_key.endswith(".webp")
    assert files.created_original is True
    assert files.created_preview is True

    store.finalize(files)
    assert len(list(store.root.rglob(".pending-*"))) == 2
    store.release(files)
    assert not list(store.root.rglob(".pending-*"))
    original_path = store.root / files.storage_key
    preview_path = store.root / files.preview_storage_key
    resolved_original = store.resolve(files.storage_key)
    resolved_preview = store.resolve(files.preview_storage_key)
    assert resolved_original.read_bytes() == payload
    assert original_path.stat().st_mode & 0o777 == 0o600
    assert preview_path.stat().st_mode & 0o777 == 0o600
    assert original_path.parent.stat().st_mode & 0o777 == 0o700
    assert preview_path.parent.stat().st_mode & 0o777 == 0o700
    with Image.open(resolved_preview) as preview:
        assert preview.format == "WEBP"
        assert max(preview.size) <= 24
        assert not preview.getexif()
        assert not {"exif", "icc_profile", "xmp", "comment"} & set(preview.info)
    resolved_original.close()
    resolved_preview.close()


@pytest.mark.parametrize(
    ("payload_factory", "filename", "max_bytes", "max_pixels", "reason"),
    [
        (lambda: _synthetic_image("PNG"), "mismatch.jpg", 1_000_000, 2_000, "type_mismatch"),
        (lambda: _synthetic_image("JPEG")[:-8], "truncated.jpg", 1_000_000, 2_000, "decode_failed"),
        (lambda: _synthetic_image("PNG")[:-1], "truncated.png", 1_000_000, 2_000, "decode_failed"),
        (lambda: b"not an image", "random.png", 1_000_000, 2_000, "decode_failed"),
        (_png_with_corrupt_idat_crc, "broken-crc.png", 1_000_000, 2_000, "decode_failed"),
        (_png_with_corrupt_iend_crc, "broken-iend.png", 1_000_000, 2_000, "decode_failed"),
        (_png_with_duplicate_iend, "duplicate-iend.png", 1_000_000, 2_000, "decode_failed"),
        (
            _png_with_corrupt_first_iend_and_appended_valid_iend,
            "hidden-broken-iend.png",
            1_000_000,
            2_000,
            "decode_failed",
        ),
        (lambda: b"x" * 65, "too-large.png", 64, 2_000, "byte_limit"),
        (
            lambda: _synthetic_image("PNG", size=(11, 10)),
            "too-many-pixels.png",
            1_000_000,
            100,
            "pixel_limit",
        ),
    ],
)
def test_invalid_images_share_one_public_error_and_keep_only_safe_internal_reason(
    tmp_path: Path,
    payload_factory,
    filename: str,
    max_bytes: int,
    max_pixels: int,
    reason: str,
) -> None:
    store = _store(tmp_path, max_bytes=max_bytes, max_pixels=max_pixels)

    with pytest.raises(MediaValidationFailed) as error:
        store.stage(io.BytesIO(payload_factory()), declared_filename=filename)

    assert error.value.code == "media_validation_failed"
    assert str(error.value) == "media validation failed"
    assert error.value.internal_reason == reason
    assert filename not in str(error.value)
    assert list((tmp_path / "private-quarantine").iterdir()) == []


def test_stage_fdopen_failure_closes_descriptor_and_removes_quarantine_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    descriptor, filename = tempfile.mkstemp(dir=store.quarantine_root)
    quarantine_path = Path(filename)
    descriptor_closed = False
    monkeypatch.setattr(
        store,
        "_quarantine_file",
        lambda: (descriptor, quarantine_path),
    )

    def reject_fdopen(*args: object, **kwargs: object):
        raise OSError("forced staged stream open failure")

    monkeypatch.setattr(os, "fdopen", reject_fdopen)

    try:
        with pytest.raises(OSError, match="forced staged stream open failure"):
            store.stage(io.BytesIO(b"payload"), declared_filename="synthetic.png")
        try:
            os.fstat(descriptor)
        except OSError:
            descriptor_closed = True
        file_exists = quarantine_path.exists()
    finally:
        if not descriptor_closed:
            os.close(descriptor)
        quarantine_path.unlink(missing_ok=True)

    assert descriptor_closed is True
    assert file_exists is False


def test_stream_failure_is_generic_and_removes_quarantine_file(tmp_path: Path) -> None:
    class FailingStream:
        def read(self, _size: int) -> bytes:
            raise OSError("private/source/path.png")

    store = _store(tmp_path)

    with pytest.raises(MediaValidationFailed) as error:
        store.stage(FailingStream(), declared_filename="declared.png")

    assert str(error.value) == "media validation failed"
    assert error.value.internal_reason == "stream_failed"
    assert "private/source" not in str(error.value)
    assert list(store.quarantine_root.iterdir()) == []


def test_private_store_uses_owner_only_root_permissions(tmp_path: Path) -> None:
    store = _store(tmp_path)

    assert store.root.stat().st_mode & 0o777 == 0o700
    assert store.quarantine_root.stat().st_mode & 0o777 == 0o700
    assert os.access(store.root, os.W_OK)
    assert os.access(store.quarantine_root, os.W_OK)


def test_store_fails_closed_without_secure_descriptor_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(media_store_module, "_SECURE_DESCRIPTOR_IO", False)

    store = _store(tmp_path)

    assert store.ready is False


def test_write_probe_close_failure_still_removes_probe_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "private-root"
    root.mkdir()
    real_open = os.open
    real_close = os.close
    probe_descriptor: int | None = None

    def capture_probe_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal probe_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith(".write-probe-"):
            probe_descriptor = descriptor
        return descriptor

    def reject_probe_close(descriptor: int) -> None:
        if descriptor == probe_descriptor:
            raise OSError("forced write probe close failure")
        real_close(descriptor)

    monkeypatch.setattr(os, "open", capture_probe_open)
    monkeypatch.setattr(os, "close", reject_probe_close)

    assert PrivateMediaStore._prepare_root(root) == (False, None)
    assert not list(root.glob(".write-probe-*"))
    assert probe_descriptor is not None
    real_close(probe_descriptor)


def test_store_fails_closed_when_root_is_replaced_after_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = (tmp_path / "private-root").resolve()
    quarantine_root = (tmp_path / "private-quarantine").resolve()
    displaced = tmp_path / "prepared-root-displaced"
    real_prepare_root = PrivateMediaStore._prepare_root
    root_replaced = False

    def replace_after_preparation(path: Path):
        nonlocal root_replaced
        result = real_prepare_root(path)
        if path == root and not root_replaced:
            path.rename(displaced)
            path.mkdir(mode=0o755)
            root_replaced = True
        return result

    monkeypatch.setattr(
        PrivateMediaStore,
        "_prepare_root",
        staticmethod(replace_after_preparation),
    )

    store = PrivateMediaStore(
        root=root,
        quarantine_root=quarantine_root,
        max_bytes=20 * 1024 * 1024,
        max_pixels=50_000_000,
        preview_max_edge=1600,
    )

    assert root_replaced is True
    assert store.media_writable is False
    assert store.ready is False
    assert store._root_descriptor is None


def test_storage_keys_require_matching_shards_and_reject_traversal(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    blob_id = uuid4()
    identity = str(blob_id)
    valid_key = f"objects/{blob_id.hex[:2]}/{blob_id.hex[2:4]}/{identity}.png"

    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    files = store.persist(validated, blob_id=blob_id)
    store.finalize(files)
    store.release(files)
    resolved = store.resolve(valid_key)
    assert resolved.read_bytes() == _synthetic_image("PNG")
    resolved.close()
    invalid_keys = [
        f"/objects/{blob_id.hex[:2]}/{blob_id.hex[2:4]}/{identity}.png",
        f"objects/../{blob_id.hex[2:4]}/{identity}.png",
        f"objects\\{blob_id.hex[:2]}\\{blob_id.hex[2:4]}\\{identity}.png",
        f"objects/%2e%2e/{blob_id.hex[2:4]}/{identity}.png",
        f"objects/ff/ff/{identity}.png",
        f"previews/{blob_id.hex[:2]}/{blob_id.hex[2:4]}/{identity}.jpg",
    ]
    for storage_key in invalid_keys:
        with pytest.raises(ValueError, match="invalid private media storage key"):
            store.resolve(storage_key)


def test_storage_key_resolution_rejects_symlink_escape(tmp_path: Path) -> None:
    store = _store(tmp_path)
    blob_id = uuid4()
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "objects").mkdir()
    (store.root / "objects" / blob_id.hex[:2]).symlink_to(
        outside,
        target_is_directory=True,
    )
    storage_key = f"objects/{blob_id.hex[:2]}/{blob_id.hex[2:4]}/{blob_id}.png"

    with pytest.raises(ValueError, match="invalid private media storage key"):
        store.resolve(storage_key)


def test_resolved_existing_file_stays_bound_after_shard_replacement(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    nominal_path = store.root / files.storage_key
    expected = nominal_path.read_bytes()
    resolved = store.resolve(files.storage_key)
    outside = tmp_path / "outside-resolved-read"
    outside.mkdir()
    displaced = tmp_path / "displaced-resolved-shard"

    nominal_path.parent.rename(displaced)
    nominal_path.parent.symlink_to(outside, target_is_directory=True)
    outside_payload = b"outside-resolved-payload"
    (outside / nominal_path.name).write_bytes(outside_payload)

    resolved_payload = resolved.read_bytes()
    assert resolved_payload == expected
    assert resolved_payload != outside_payload

    close_resolved = getattr(resolved, "close", None)
    if callable(close_resolved):
        close_resolved()
    store.remove_if_created(files)


def test_resolve_rejects_shard_replaced_after_parent_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    outside = tmp_path / "outside-resolve-open"
    outside.mkdir()
    displaced = tmp_path / "displaced-resolve-open"
    real_parent_descriptor = store._parent_descriptor
    shard_replaced = False

    def replace_shard_after_open(
        candidate_key: str,
        *,
        create: bool,
    ) -> tuple[int, str]:
        nonlocal shard_replaced
        descriptor, filename = real_parent_descriptor(
            candidate_key,
            create=create,
        )
        if candidate_key == files.storage_key and not shard_replaced:
            candidate = store.root / candidate_key
            candidate.parent.rename(displaced)
            candidate.parent.symlink_to(outside, target_is_directory=True)
            shard_replaced = True
        return descriptor, filename

    monkeypatch.setattr(store, "_parent_descriptor", replace_shard_after_open)

    with pytest.raises(ValueError, match="invalid private media storage key"):
        store.resolve(files.storage_key)

    assert shard_replaced is True
    assert list(outside.iterdir()) == []
    store.remove_if_created(files)


def test_persist_rejects_destination_shard_replaced_after_descriptor_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    blob_id = uuid4()
    storage_key, _ = store._keys(blob_id, validated.extension)
    outside = tmp_path / "outside-media"
    outside.mkdir()
    displaced = tmp_path / "displaced-shard"
    real_parent_descriptor = store._parent_descriptor
    shard_replaced = False

    def replace_shard_after_open(
        candidate_key: str,
        *,
        create: bool,
    ) -> tuple[int, str]:
        nonlocal shard_replaced
        descriptor, filename = real_parent_descriptor(
            candidate_key,
            create=create,
        )
        if candidate_key == storage_key and not create and not shard_replaced:
            candidate = store.root / candidate_key
            candidate.parent.rename(displaced)
            candidate.parent.symlink_to(outside, target_is_directory=True)
            shard_replaced = True
        return descriptor, filename

    monkeypatch.setattr(store, "_parent_descriptor", replace_shard_after_open)

    with pytest.raises((OSError, RuntimeError, ValueError)):
        store.persist(validated, blob_id=blob_id)

    assert shard_replaced is True
    assert not validated.quarantine_path.exists()
    assert list(outside.iterdir()) == []
    assert list(displaced.iterdir()) == []


def test_persist_rejects_destination_shard_moved_during_publish_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="publish-race.png",
    )
    blob_id = uuid4()
    storage_key, _ = store._keys(blob_id, validated.extension)
    target_path = store.root / storage_key
    outside = tmp_path / "outside-publish-link"
    outside.mkdir()
    displaced = tmp_path / "displaced-publish-link"
    real_link = os.link
    shard_replaced = False

    def replace_shard_before_link(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal shard_replaced
        if (
            isinstance(source, str)
            and source.startswith(".pending-")
            and destination == target_path.name
            and not shard_replaced
        ):
            target_path.parent.rename(displaced)
            target_path.parent.symlink_to(outside, target_is_directory=True)
            shard_replaced = True
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(os, "link", replace_shard_before_link)
    persisted: StoredImageFiles | None = None
    try:
        with pytest.raises(RuntimeError, match="private media ownership was lost"):
            persisted = store.persist(validated, blob_id=blob_id)
    finally:
        if persisted is not None:
            store.remove_if_created(persisted)

    assert shard_replaced is True
    assert list(outside.iterdir()) == []
    assert list(displaced.iterdir()) == []
    assert not list(store.root.rglob(".pending-*"))
    assert not validated.quarantine_path.exists()


def test_finalize_rechecks_shard_after_owned_entry_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="finalize-race.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    ownership = files.original_ownership
    assert ownership is not None
    target_path = store.root / files.storage_key
    outside = tmp_path / "outside-finalize-check"
    outside.mkdir()
    displaced = tmp_path / "displaced-finalize-check"
    real_entry_owned = store._entry_owned
    shard_replaced = False

    def replace_shard_before_entry_validation(
        candidate: object,
        name: str,
        *,
        parent_descriptor: int,
    ) -> bool:
        nonlocal shard_replaced
        if candidate is ownership and name == ownership.filename and not shard_replaced:
            target_path.parent.rename(displaced)
            target_path.parent.symlink_to(outside, target_is_directory=True)
            shard_replaced = True
        return real_entry_owned(
            candidate,
            name,
            parent_descriptor=parent_descriptor,
        )

    monkeypatch.setattr(store, "_entry_owned", replace_shard_before_entry_validation)

    with pytest.raises(RuntimeError, match="private media ownership was lost"):
        store.finalize(files)
    store.remove_if_created(files)

    assert shard_replaced is True
    assert list(outside.iterdir()) == []
    assert list(displaced.iterdir()) == []
    assert not list(store.root.rglob(".pending-*"))


def test_discard_staged_preserves_replacement_and_removes_owned_inode(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    moved_owned = validated.quarantine_path.with_name(f"moved-{validated.quarantine_path.name}")
    shared = tmp_path / "preexisting-shared-staged.image"
    shared.write_bytes(b"preexisting-shared-staged")
    validated.quarantine_path.rename(moved_owned)
    os.link(shared, validated.quarantine_path)

    store.discard_staged(validated)

    assert validated.quarantine_path.read_bytes() == shared.read_bytes()
    assert not moved_owned.exists()


def test_discard_staged_reports_scan_failure_and_can_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="retry-discard.png",
    )
    real_listdir = os.listdir

    def reject_descriptor_scan(path) -> list[str]:
        if isinstance(path, int):
            raise OSError("forced quarantine ownership scan failure")
        return real_listdir(path)

    try:
        with monkeypatch.context() as failed_scan:
            failed_scan.setattr(os, "listdir", reject_descriptor_scan)
            with pytest.raises(
                OSError,
                match="forced quarantine ownership scan failure",
            ):
                store.discard_staged(validated)
            assert validated.quarantine_path.is_file()
            assert len(list(store.quarantine_root.glob(".owned-*"))) == 1
    finally:
        store.discard_staged(validated)

    assert list(store.quarantine_root.iterdir()) == []


@pytest.mark.parametrize("moved_target", ["original", "preview"])
def test_rollback_removes_owned_file_moved_across_private_directories(
    tmp_path: Path,
    moved_target: str,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(45, 105, 165))),
        declared_filename="moved.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    target_key = files.storage_key if moved_target == "original" else files.preview_storage_key
    other_key = files.preview_storage_key if moved_target == "original" else files.storage_key
    target_path = store.root / target_key
    relocated_directory = store.root / "relocated-owned-files"
    relocated_directory.mkdir(mode=0o700)
    relocated_path = relocated_directory / target_path.name
    replacement = b"replacement-at-original-path"
    target_path.rename(relocated_path)
    target_path.write_bytes(replacement)

    store.remove_if_created(files)

    assert target_path.read_bytes() == replacement
    assert not relocated_path.exists()
    assert not (store.root / other_key).exists()
    assert not list(store.root.rglob(".pending-*"))


def test_rollback_atomically_preserves_replacement_swapped_before_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(50, 110, 170))),
        declared_filename="atomic-replacement.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    ownership = files.original_ownership
    assert ownership is not None
    parent_descriptor = ownership.parent_descriptor
    assert parent_descriptor is not None
    target_path = store.root / files.storage_key
    moved_name = f"moved-during-unlink-{target_path.name}"
    moved_path = target_path.with_name(moved_name)
    replacement_source = target_path.with_name("replacement-source")
    replacement = b"replacement-swapped-before-unlink"
    replacement_source.write_bytes(replacement)
    real_unlink = os.unlink
    replacement_installed = False

    def install_replacement_before_owned_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal replacement_installed
        if isinstance(path, str) and dir_fd is not None and not replacement_installed:
            try:
                details = os.stat(path, dir_fd=dir_fd, follow_symlinks=False)
            except FileNotFoundError:
                details = None
            if (
                details is not None
                and details.st_dev == ownership.device
                and details.st_ino == ownership.inode
            ):
                try:
                    target_details = os.stat(
                        target_path.name,
                        dir_fd=parent_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    target_details = None
                if (
                    target_details is not None
                    and target_details.st_dev == ownership.device
                    and target_details.st_ino == ownership.inode
                ):
                    os.rename(
                        target_path.name,
                        moved_name,
                        src_dir_fd=parent_descriptor,
                        dst_dir_fd=parent_descriptor,
                    )
                os.link(
                    replacement_source.name,
                    target_path.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                replacement_installed = True
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", install_replacement_before_owned_unlink)

    store.remove_if_created(files)

    assert replacement_installed is True
    assert target_path.read_bytes() == replacement
    assert not moved_path.exists()
    assert not list(store.root.rglob(".pending-*"))


@pytest.mark.parametrize("unlinked_target", ["original", "preview"])
def test_rollback_preserves_replacement_after_owned_file_is_unlinked(
    tmp_path: Path,
    unlinked_target: str,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(55, 115, 175))),
        declared_filename="unlinked.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    ownerships = [files.original_ownership, files.preview_ownership]
    descriptors = [
        ownership.parent_descriptor
        for ownership in ownerships
        if ownership is not None and ownership.parent_descriptor is not None
    ]
    assert len(list(store.root.rglob(".pending-*"))) == 2
    target_key = files.storage_key if unlinked_target == "original" else files.preview_storage_key
    other_key = files.preview_storage_key if unlinked_target == "original" else files.storage_key
    target_path = store.root / target_key
    replacement = b"replacement-after-unlink"
    target_path.unlink()
    target_path.write_bytes(replacement)

    store.remove_if_created(files)
    store.release(files)

    assert target_path.read_bytes() == replacement
    assert not (store.root / other_key).exists()
    assert not list(store.root.rglob(".pending-*"))
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_publish_anchor_setup_failure_closes_opened_parent_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(65, 125, 185))),
        declared_filename="anchor-failure.png",
    )
    root_descriptor = store._root_descriptor
    assert root_descriptor is not None
    real_parent_descriptor = store._parent_descriptor
    real_dup = os.dup
    opened_parent: int | None = None

    def capture_parent(
        storage_key: str,
        *,
        create: bool,
    ) -> tuple[int, str]:
        nonlocal opened_parent
        result = real_parent_descriptor(storage_key, create=create)
        if not create and opened_parent is None:
            opened_parent = result[0]
        return result

    def reject_root_duplicate(descriptor: int) -> int:
        if opened_parent is not None and descriptor == root_descriptor:
            raise OSError("forced ownership root duplicate failure")
        return real_dup(descriptor)

    monkeypatch.setattr(store, "_parent_descriptor", capture_parent)
    monkeypatch.setattr(os, "dup", reject_root_duplicate)

    with pytest.raises(OSError, match="forced ownership root duplicate failure"):
        store.persist(validated, blob_id=uuid4())

    assert opened_parent is not None
    with pytest.raises(OSError):
        os.fstat(opened_parent)


def test_rollback_closes_both_ownerships_when_first_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(75, 135, 195))),
        declared_filename="rollback-cleanup.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    original = files.original_ownership
    preview = files.preview_ownership
    assert original is not None
    assert preview is not None
    real_remove_owned_links = store._remove_owned_links

    failure_injected = False

    def reject_original_cleanup(ownership, budget=None) -> None:
        nonlocal failure_injected
        if ownership is original and not failure_injected:
            failure_injected = True
            raise OSError("forced original ownership cleanup failure")
        real_remove_owned_links(ownership, budget)

    monkeypatch.setattr(store, "_remove_owned_links", reject_original_cleanup)

    store.remove_if_created(files)

    assert failure_injected is True
    assert original.parent_descriptor is None
    assert original.anchor_descriptor is None
    assert preview.parent_descriptor is None
    assert preview.anchor_descriptor is None
    assert not (store.root / files.preview_storage_key).exists()


def test_release_closes_both_ownerships_when_first_anchor_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(85, 145, 205))),
        declared_filename="release-cleanup.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    original = files.original_ownership
    preview = files.preview_ownership
    assert original is not None
    assert preview is not None
    real_unlink_owned = store._unlink_entry_if_owned

    failure_injected = False

    def reject_first_anchor(
        parent_descriptor: int,
        *,
        name: str,
        device: int,
        inode: int,
    ) -> None:
        nonlocal failure_injected
        if name == original.anchor_name and not failure_injected:
            failure_injected = True
            raise OSError("forced original anchor cleanup failure")
        real_unlink_owned(
            parent_descriptor,
            name=name,
            device=device,
            inode=inode,
        )

    monkeypatch.setattr(store, "_unlink_entry_if_owned", reject_first_anchor)

    store.release(files)

    assert failure_injected is True
    assert original.parent_descriptor is None
    assert original.anchor_descriptor is None
    assert preview.parent_descriptor is None
    assert preview.anchor_descriptor is None
    assert (store.root / files.storage_key).is_file()
    assert (store.root / files.preview_storage_key).is_file()


def test_release_retries_anchor_renamed_before_transient_unlink_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(90, 150, 210))),
        declared_filename="release-rename-retry.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    original = files.original_ownership
    assert original is not None
    real_unlink = os.unlink
    failure_injected = False

    def fail_first_settling_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal failure_injected
        if (
            isinstance(path, str)
            and path.startswith(".settling-")
            and dir_fd == original.anchor_descriptor
            and not failure_injected
        ):
            failure_injected = True
            raise OSError("forced settling anchor unlink failure")
        real_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail_first_settling_unlink)

    store.release(files)

    assert failure_injected is True
    assert not list(store.root.glob(".pending-*"))
    assert not list(store.root.glob(".settling-*"))
    assert original.parent_descriptor is None
    assert original.anchor_descriptor is None
    assert (store.root / files.storage_key).is_file()
    assert (store.root / files.preview_storage_key).is_file()


@pytest.mark.parametrize("settlement", ["release", "remove"])
def test_pending_ownership_can_settle_after_store_close(
    tmp_path: Path,
    settlement: str,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(95, 155, 215))),
        declared_filename="store-close.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    ownerships = [files.original_ownership, files.preview_ownership]
    descriptors = [
        descriptor
        for ownership in ownerships
        if ownership is not None
        for descriptor in (
            ownership.parent_descriptor,
            ownership.anchor_descriptor,
        )
        if descriptor is not None
    ]
    paths = (store.root / files.storage_key, store.root / files.preview_storage_key)

    store.close()
    if settlement == "release":
        store.release(files)
    else:
        store.remove_if_created(files)

    assert not list(store.root.rglob(".pending-*"))
    assert [path.exists() for path in paths] == [
        settlement == "release",
        settlement == "release",
    ]
    for descriptor in descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_rollback_keeps_stable_anchors_when_bounded_traversal_cannot_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(105, 165, 225))),
        declared_filename="scan-failure.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    real_listdir = os.listdir

    def reject_scan(path) -> list[str]:
        if isinstance(path, int):
            raise OSError("forced private root traversal failure")
        return real_listdir(path)

    try:
        with monkeypatch.context() as failed_scan:
            failed_scan.setattr(os, "listdir", reject_scan)
            with pytest.raises(OSError, match="forced private root traversal failure"):
                store.remove_if_created(files)

            assert len(list(store.root.rglob(".pending-*"))) == 2
            for ownership in (files.original_ownership, files.preview_ownership):
                assert ownership is not None
                assert ownership.parent_descriptor is not None
                assert ownership.anchor_descriptor is not None
    finally:
        store.remove_if_created(files)

    assert not list(store.root.rglob(".pending-*"))


def test_rollback_never_scans_directory_moved_outside_private_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(115, 175, 235))),
        declared_filename="outside-scan.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    original_path = store.root / files.storage_key
    movable = store.root / "movable-owned-directory"
    outside = tmp_path / "moved-outside-private-root"
    movable.mkdir(mode=0o700)
    moved_owned = movable / original_path.name
    original_path.rename(moved_owned)
    real_open_descendant = store._open_descendant_directory
    directory_moved = False

    def move_directory_after_open(
        root_descriptor: int,
        components: tuple[str, ...],
    ) -> int:
        nonlocal directory_moved
        descriptor = real_open_descendant(root_descriptor, components)
        if components == (movable.name,) and not directory_moved:
            movable.rename(outside)
            movable.symlink_to(outside, target_is_directory=True)
            directory_moved = True
        return descriptor

    monkeypatch.setattr(
        store,
        "_open_descendant_directory",
        move_directory_after_open,
    )

    with pytest.raises(
        RuntimeError,
        match="private media ownership links remain",
    ):
        store.remove_if_created(files)

    assert directory_moved is True
    assert (outside / moved_owned.name).is_file()
    assert len(list(store.root.rglob(".pending-*"))) == 1
    assert not (store.root / files.preview_storage_key).exists()

    movable.unlink()
    outside.rename(movable)
    store.remove_if_created(files)

    assert not moved_owned.exists()
    assert not list(store.root.rglob(".pending-*"))


def test_rollback_finds_owned_file_below_eight_private_directories(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(125, 185, 245))),
        declared_filename="deep-owned.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    original_path = store.root / files.storage_key
    deep_directory = store.root
    for depth in range(10):
        deep_directory /= f"level-{depth}"
        deep_directory.mkdir(mode=0o700)
    moved_owned = deep_directory / original_path.name
    original_path.rename(moved_owned)

    store.remove_if_created(files)

    assert not moved_owned.exists()
    assert not list(store.root.rglob(".pending-*"))


def test_quarantine_permission_failure_closes_descriptor_and_removes_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    real_open = os.open
    opened_descriptor: int | None = None

    def capture_staged_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal opened_descriptor
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if isinstance(path, str) and path.startswith("staged-"):
            opened_descriptor = descriptor
        return descriptor

    def reject_permission(_descriptor: int, _mode: int) -> None:
        raise OSError("forced quarantine permission failure")

    monkeypatch.setattr(os, "open", capture_staged_open)
    monkeypatch.setattr(os, "fchmod", reject_permission)

    with pytest.raises(OSError, match="forced quarantine permission failure"):
        store._quarantine_file()

    assert opened_descriptor is not None
    descriptor_closed = False
    try:
        os.fstat(opened_descriptor)
    except OSError:
        descriptor_closed = True
    finally:
        if not descriptor_closed:
            os.close(opened_descriptor)

    assert descriptor_closed is True
    assert list(store.quarantine_root.iterdir()) == []


def test_preview_setup_failure_closes_descriptor_and_removes_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    real_temporary_file = store._temporary_quarantine_file
    preview_descriptor: int | None = None

    def capture_preview_file(*, prefix: str, suffix: str) -> tuple[int, Path]:
        nonlocal preview_descriptor
        result = real_temporary_file(prefix=prefix, suffix=suffix)
        preview_descriptor = result[0]
        return result

    def reject_duplicate(_descriptor: int) -> int:
        raise OSError("forced preview descriptor setup failure")

    monkeypatch.setattr(store, "_temporary_quarantine_file", capture_preview_file)
    monkeypatch.setattr(os, "dup", reject_duplicate)

    with pytest.raises(OSError, match="forced preview descriptor setup failure"):
        store._preview_temp(validated)

    assert preview_descriptor is not None
    descriptor_closed = False
    try:
        os.fstat(preview_descriptor)
    except OSError:
        descriptor_closed = True
    finally:
        if not descriptor_closed:
            os.close(preview_descriptor)

    assert descriptor_closed is True
    quarantine_entries = set(store.quarantine_root.iterdir())
    assert validated.quarantine_path in quarantine_entries
    assert len(quarantine_entries) == 2
    store.discard_staged(validated)


def test_preview_creation_failure_removes_staged_and_temporary_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )

    def reject_preview_save(*args: object, **kwargs: object) -> None:
        raise OSError("forced preview failure")

    monkeypatch.setattr(Image.Image, "save", reject_preview_save)

    with pytest.raises(OSError, match="forced preview failure"):
        store.persist(validated, blob_id=uuid4())

    assert not validated.quarantine_path.exists()
    assert list(store.quarantine_root.iterdir()) == []
    assert not any(path.is_file() for path in store.root.rglob("*"))


def test_preview_cleanup_failure_compensates_published_files_and_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    blob_id = uuid4()
    storage_key, preview_storage_key = store._keys(blob_id, validated.extension)
    real_preview_temp = store._preview_temp
    real_publish = store._publish_descriptor
    real_remove_quarantine_inode = store._remove_quarantine_inode
    preview_identity: tuple[int, int] | None = None
    published = []
    ownership_descriptors: list[int] = []
    failure_injected = False

    def capture_preview_temp(
        image: ValidatedImage,
        *,
        source_descriptor: int | None = None,
    ) -> tuple[int, Path]:
        nonlocal preview_identity
        result = real_preview_temp(
            image,
            source_descriptor=source_descriptor,
        )
        details = os.fstat(result[0])
        preview_identity = (details.st_dev, details.st_ino)
        return result

    def capture_publish(source: int, candidate_key: str):
        ownership = real_publish(source, candidate_key)
        published.append(ownership)
        ownership_descriptors.extend(
            descriptor
            for descriptor in (
                ownership.parent_descriptor,
                ownership.anchor_descriptor,
            )
            if descriptor is not None
        )
        return ownership

    def reject_first_preview_cleanup(*, device: int, inode: int) -> None:
        nonlocal failure_injected
        if preview_identity == (device, inode) and not failure_injected:
            failure_injected = True
            raise OSError("forced preview quarantine cleanup failure")
        real_remove_quarantine_inode(device=device, inode=inode)

    monkeypatch.setattr(store, "_preview_temp", capture_preview_temp)
    monkeypatch.setattr(store, "_publish_descriptor", capture_publish)
    monkeypatch.setattr(
        store,
        "_remove_quarantine_inode",
        reject_first_preview_cleanup,
    )

    try:
        with pytest.raises(
            OSError,
            match="forced preview quarantine cleanup failure",
        ):
            store.persist(validated, blob_id=blob_id)

        assert failure_injected is True
        assert len(published) == 2
        assert not any(path.is_file() for path in store.root.rglob("*"))
        assert list(store.quarantine_root.iterdir()) == []
        for descriptor in ownership_descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        if len(published) == 2:
            store.remove_if_created(
                StoredImageFiles(
                    storage_key=storage_key,
                    preview_storage_key=preview_storage_key,
                    created_original=True,
                    created_preview=True,
                    original_ownership=published[0],
                    preview_ownership=published[1],
                )
            )


def test_destination_preparation_failure_removes_staged_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )

    def reject_parent_creation(_path: Path) -> None:
        raise OSError("forced destination preparation failure")

    monkeypatch.setattr(store, "_private_parent", reject_parent_creation)

    with pytest.raises(OSError, match="forced destination preparation failure"):
        store.persist(validated, blob_id=uuid4())

    assert not validated.quarantine_path.exists()
    assert list(store.quarantine_root.iterdir()) == []
    assert not any(path.is_file() for path in store.root.rglob("*"))


@pytest.mark.parametrize("failing_target", ["original", "preview"])
def test_persist_chmod_failure_removes_every_file_created_by_the_call(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failing_target: str,
) -> None:
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG")),
        declared_filename="synthetic.png",
    )
    blob_id = uuid4()
    storage_key, preview_storage_key = store._keys(blob_id, validated.extension)
    original_path = store.root / storage_key
    preview_path = store.root / preview_storage_key
    rejected_key = storage_key if failing_target == "original" else preview_storage_key
    real_fchmod = os.fchmod
    real_publish = store._publish_descriptor
    active_key: str | None = None

    def publish_with_active_key(source: int, candidate_key: str):
        nonlocal active_key
        active_key = candidate_key
        try:
            return real_publish(source, candidate_key)
        finally:
            active_key = None

    def reject_final_chmod(descriptor: int, mode: int) -> None:
        if active_key == rejected_key:
            raise OSError(f"forced {failing_target} chmod failure")
        real_fchmod(descriptor, mode)

    monkeypatch.setattr(store, "_publish_descriptor", publish_with_active_key)
    monkeypatch.setattr(os, "fchmod", reject_final_chmod)

    with pytest.raises(OSError, match=f"forced {failing_target} chmod failure"):
        store.persist(validated, blob_id=blob_id)

    assert not validated.quarantine_path.exists()
    assert not original_path.exists()
    assert not preview_path.exists()
    assert list(store.quarantine_root.iterdir()) == []


@dataclass(frozen=True)
class MediaScenario:
    db: Session
    store: PrivateMediaStore
    service: EvaluationMediaService
    admin: UserAccount
    unauthorized: UserAccount


def _person(db: Session, *, name: str) -> Person:
    marker = uuid4().hex
    person = Person(
        full_name=name,
        identity_ciphertext=f"media-cipher:{marker}",
        identity_digest=(marker * 2)[:64],
        phone_ciphertext=None,
        phone_digest=None,
        email=f"media-{marker[:12]}@example.test",
        sex="female",
        date_of_birth=date(1990, 1, 2),
    )
    db.add(person)
    db.flush()
    return person


def _account(db: Session, person: Person, *, key: str) -> UserAccount:
    marker = uuid4().hex
    account = UserAccount(
        person_id=person.id,
        login_phone_digest=(marker * 2)[:64],
        email_normalized=f"{key}-{marker[:8]}@example.test",
        password_hash="media-test-password-hash",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    return account


@pytest.fixture
def media_scenario(
    postgres_engine: Engine,
    tmp_path: Path,
) -> Iterator[MediaScenario]:
    store = _store(tmp_path)
    with Session(postgres_engine) as db:
        admin_person = _person(db, name="媒体管理员")
        unauthorized_person = _person(db, name="无媒体权限客服")
        admin = _account(db, admin_person, key="media-admin")
        unauthorized = _account(db, unauthorized_person, key="media-customer")
        db.add_all(
            [
                UserRole(user_id=admin.id, role_id=ROLES["admin"].id),
                UserRole(
                    user_id=unauthorized.id,
                    role_id=ROLES["customer_service"].id,
                ),
            ]
        )
        db.commit()
        admin_id = admin.id
        unauthorized_id = unauthorized.id
        person_ids = (admin_person.id, unauthorized_person.id)
        try:
            yield MediaScenario(
                db=db,
                store=store,
                service=EvaluationMediaService(db, store=store),
                admin=admin,
                unauthorized=unauthorized,
            )
        finally:
            try:
                db.close()
            finally:
                store.close()

    account_ids = (admin_id, unauthorized_id)
    with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
        blob_ids = list(
            cleanup_db.scalars(
                select(EvaluationMediaAsset.blob_id)
                .where(EvaluationMediaAsset.uploaded_by_user_id.in_(account_ids))
                .distinct()
            )
        )
        cleanup_db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
        try:
            cleanup_db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(account_ids)))
        finally:
            cleanup_db.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
        cleanup_db.execute(
            delete(EvaluationMediaAsset).where(
                EvaluationMediaAsset.uploaded_by_user_id.in_(account_ids)
            )
        )
        if blob_ids:
            cleanup_db.execute(
                delete(EvaluationMediaBlob).where(
                    EvaluationMediaBlob.id.in_(blob_ids),
                    EvaluationMediaBlob.id.not_in(select(EvaluationMediaAsset.blob_id)),
                )
            )
        cleanup_db.execute(delete(UserRole).where(UserRole.user_id.in_(account_ids)))
        cleanup_db.execute(delete(UserAccount).where(UserAccount.id.in_(account_ids)))
        cleanup_db.execute(delete(Person).where(Person.id.in_(person_ids)))


def _stage(
    scenario: MediaScenario,
    *,
    color: tuple[int, int, int] = (18, 70, 130),
) -> ValidatedImage:
    return scenario.store.stage(
        io.BytesIO(_synthetic_image("PNG", color=color)),
        declared_filename="synthetic.png",
    )


def _register(
    scenario: MediaScenario,
    *,
    color: tuple[int, int, int] = (18, 70, 130),
):
    return scenario.service.register_validated_image(
        _stage(scenario, color=color),
        uploaded_by_user_id=scenario.admin.id,
        project_id=INITIAL_PROJECTS[0].id,
    )


def _capture_persisted_files(
    scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> list[StoredImageFiles]:
    persisted: list[StoredImageFiles] = []
    real_persist = scenario.store.persist

    def capture(image: ValidatedImage, *, blob_id: UUID) -> StoredImageFiles:
        files = real_persist(image, blob_id=blob_id)
        persisted.append(files)
        return files

    monkeypatch.setattr(scenario.store, "persist", capture)
    return persisted


def _stored_paths(
    scenario: MediaScenario,
    files: StoredImageFiles,
) -> tuple[Path, Path]:
    return (
        scenario.store.root / files.storage_key,
        scenario.store.root / files.preview_storage_key,
    )


def _capture_removed_files(
    scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> list[StoredImageFiles]:
    removed: list[StoredImageFiles] = []
    real_remove = scenario.store.remove_if_created

    def capture(
        files: StoredImageFiles,
        *,
        budget=None,
    ) -> None:
        removed.append(files)
        real_remove(files, budget=budget)

    monkeypatch.setattr(scenario.store, "remove_if_created", capture)
    return removed


def test_identical_content_reuses_blob_but_returns_distinct_opaque_assets(
    media_scenario: MediaScenario,
) -> None:
    first = _register(media_scenario)
    second = _register(media_scenario)

    assert first.id != second.id
    assert first.project_id == second.project_id == INITIAL_PROJECTS[0].id
    assert not hasattr(first, "blob_id")
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaBlob.id))) == 1
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaAsset.id))) == 2
    rows = list(
        media_scenario.db.scalars(select(EvaluationMediaAsset).order_by(EvaluationMediaAsset.id))
    )
    assert rows[0].blob_id == rows[1].blob_id
    assert list(media_scenario.store.quarantine_root.iterdir()) == []


@pytest.mark.parametrize("transaction_kind", ["autobegin", "root", "savepoint"])
def test_connection_bound_session_owned_transactions_remain_supported(
    tmp_path: Path,
    transaction_kind: str,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    store = _store(tmp_path)
    staged = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(5, 65, 125))),
        declared_filename="session-owned.png",
    )

    with engine.connect() as connection, Session(bind=connection) as session:
        if transaction_kind == "autobegin":
            session.execute(text("SELECT 1"))
        else:
            session.begin()
            session.execute(text("SELECT 1"))
            if transaction_kind == "savepoint":
                session.begin_nested()
                session.execute(text("SELECT 1"))

        media_service_module._reject_external_connection_transaction(
            session,
            store=store,
            image=staged,
        )

        assert staged.quarantine_path.is_file()
        session.rollback()
    store.discard_staged(staged)


def test_failed_transaction_settlement_retries_on_session_reuse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    store = _store(tmp_path)
    validated = store.stage(
        io.BytesIO(_synthetic_image("PNG", color=(15, 75, 135))),
        declared_filename="retry-settlement.png",
    )
    files = store.persist(validated, blob_id=uuid4())
    store.finalize(files)
    original = files.original_ownership
    assert original is not None
    real_remove_owned_links = store._remove_owned_links
    failures_remaining = 2

    def fail_first_settlement_attempts(ownership, budget=None) -> None:
        nonlocal failures_remaining
        if ownership is original and failures_remaining:
            failures_remaining -= 1
            raise OSError("forced transient transaction settlement failure")
        real_remove_owned_links(ownership, budget)

    monkeypatch.setattr(
        store,
        "_remove_owned_links",
        fail_first_settlement_attempts,
    )

    with Session(engine) as session:
        transaction = session.begin()
        pending = media_service_module._track_media_files(
            session,
            store=store,
            files=files,
        )

        with pytest.raises(
            OSError,
            match="forced transient transaction settlement failure",
        ):
            transaction.rollback()

        compensations = session.info[media_service_module._MEDIA_TRANSACTION_COMPENSATIONS]
        assert pending.settled is False
        assert transaction in compensations.pending
        assert original.parent_descriptor is not None
        assert original.anchor_descriptor is not None

        retry_transaction = session.begin()
        session.execute(text("SELECT 1"))

        assert pending.settled is True
        assert transaction not in compensations.pending
        assert not (store.root / files.storage_key).exists()
        assert not list(store.root.rglob(".pending-*"))
        retry_transaction.rollback()


def test_transaction_settlement_shares_one_scan_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _store(tmp_path)
    stored: list[StoredImageFiles] = []
    for color in ((25, 85, 145), (35, 95, 155)):
        validated = store.stage(
            io.BytesIO(_synthetic_image("PNG", color=color)),
            declared_filename="bounded-release.png",
        )
        files = store.persist(validated, blob_id=uuid4())
        store.finalize(files)
        stored.append(files)
    pending = [
        media_service_module._PendingMediaFiles(store=store, files=files) for files in stored
    ]
    real_listdir = os.listdir
    bounded_scans = 0

    def count_bounded_scan(path):
        nonlocal bounded_scans
        bounded_scans += 1
        return real_listdir(path)

    try:
        with monkeypatch.context() as bounded:
            bounded.setattr(
                media_store_module,
                "_OWNERSHIP_SCAN_MAX_ENTRIES",
                11,
            )
            bounded.setattr(os, "listdir", count_bounded_scan)
            with pytest.raises(
                RuntimeError,
                match="private media ownership scan limit exceeded",
            ):
                media_service_module._settle_all_media_files(
                    pending,
                    committed=True,
                )

        assert [entry.settled for entry in pending] == [True, False]
        assert bounded_scans == 2
        assert len(list(store.root.glob(".pending-*"))) == 2
    finally:
        media_service_module._settle_all_media_files(
            pending,
            committed=True,
        )

    assert all(
        (store.root / key).is_file()
        for files in stored
        for key in (files.storage_key, files.preview_storage_key)
    )
    assert not list(store.root.glob(".pending-*"))


@pytest.mark.parametrize("join_transaction_mode", ["create_savepoint", "rollback_only"])
def test_external_connection_transaction_fails_closed_before_media_persistence(
    media_scenario: MediaScenario,
    postgres_engine: Engine,
    join_transaction_mode: str,
) -> None:
    staged = _stage(media_scenario, color=(10, 70, 130))
    admin_id = media_scenario.admin.id

    with postgres_engine.connect() as connection:
        external_transaction = connection.begin()
        with Session(
            bind=connection,
            join_transaction_mode=join_transaction_mode,
        ) as external_db:
            service = EvaluationMediaService(external_db, store=media_scenario.store)
            assert (
                external_db.scalar(
                    select(func.count(EvaluationMediaAsset.id)).where(
                        EvaluationMediaAsset.uploaded_by_user_id == admin_id
                    )
                )
                == 0
            )

            with pytest.raises(RuntimeError, match="externally managed transaction"):
                service.register_validated_image(
                    staged,
                    uploaded_by_user_id=admin_id,
                    project_id=INITIAL_PROJECTS[0].id,
                )

            assert (
                external_db.scalar(
                    select(func.count(EvaluationMediaAsset.id)).where(
                        EvaluationMediaAsset.uploaded_by_user_id == admin_id
                    )
                )
                == 0
            )
        external_transaction.rollback()

    with Session(postgres_engine) as verification_db:
        assert (
            verification_db.scalar(
                select(func.count(EvaluationMediaAsset.id)).where(
                    EvaluationMediaAsset.uploaded_by_user_id == admin_id
                )
            )
            == 0
        )
        assert verification_db.scalar(select(func.count(EvaluationMediaBlob.id))) == 0
    assert not staged.quarantine_path.exists()
    assert list(media_scenario.store.quarantine_root.iterdir()) == []
    assert not any(path.is_file() for path in media_scenario.store.root.rglob("*"))


def test_caller_owned_root_rollback_removes_registered_files(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_id = media_scenario.admin.id
    media_scenario.db.commit()
    persisted = _capture_persisted_files(media_scenario, monkeypatch)
    removed = _capture_removed_files(media_scenario, monkeypatch)

    transaction = media_scenario.db.begin()
    registered = media_scenario.service.register_validated_image(
        _stage(media_scenario, color=(20, 80, 140)),
        uploaded_by_user_id=admin_id,
        project_id=INITIAL_PROJECTS[0].id,
    )
    assert len(persisted) == 1
    paths = _stored_paths(media_scenario, persisted[0])
    assert all(path.is_file() for path in paths)

    transaction.rollback()

    assert removed == persisted
    assert all(not path.exists() for path in paths)
    assert (
        media_scenario.db.scalar(
            select(func.count(EvaluationMediaAsset.id)).where(
                EvaluationMediaAsset.id == registered.id,
            )
        )
        == 0
    )


def test_nested_savepoint_commit_promotes_cleanup_to_parent_rollback(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_id = media_scenario.admin.id
    media_scenario.db.commit()
    persisted = _capture_persisted_files(media_scenario, monkeypatch)
    removed = _capture_removed_files(media_scenario, monkeypatch)

    parent = media_scenario.db.begin()
    savepoint = media_scenario.db.begin_nested()
    media_scenario.service.register_validated_image(
        _stage(media_scenario, color=(30, 90, 150)),
        uploaded_by_user_id=admin_id,
        project_id=INITIAL_PROJECTS[0].id,
    )
    assert len(persisted) == 1
    paths = _stored_paths(media_scenario, persisted[0])

    savepoint.commit()

    assert removed == []
    assert all(path.is_file() for path in paths)

    parent.rollback()

    assert removed == persisted
    assert all(not path.exists() for path in paths)


def test_caller_owned_root_commit_releases_files_without_deleting_them(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_id = media_scenario.admin.id
    media_scenario.db.commit()
    persisted = _capture_persisted_files(media_scenario, monkeypatch)

    transaction = media_scenario.db.begin()
    registered = media_scenario.service.register_validated_image(
        _stage(media_scenario, color=(40, 100, 160)),
        uploaded_by_user_id=admin_id,
        project_id=INITIAL_PROJECTS[0].id,
    )
    assert len(persisted) == 1
    files = persisted[0]
    paths = _stored_paths(media_scenario, files)
    assert files.original_ownership is not None
    assert files.preview_ownership is not None
    assert files.original_ownership.parent_descriptor is not None
    assert files.preview_ownership.parent_descriptor is not None
    assert files.original_ownership.anchor_descriptor is not None
    assert files.preview_ownership.anchor_descriptor is not None

    transaction.commit()

    assert files.original_ownership.parent_descriptor is None
    assert files.preview_ownership.parent_descriptor is None
    assert files.original_ownership.anchor_descriptor is None
    assert files.preview_ownership.anchor_descriptor is None
    assert all(path.is_file() for path in paths)
    assert (
        media_scenario.db.scalar(
            select(func.count(EvaluationMediaAsset.id)).where(
                EvaluationMediaAsset.id == registered.id,
            )
        )
        == 1
    )


def test_post_commit_release_failure_never_removes_durable_files_and_retries(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_id = media_scenario.admin.id
    media_scenario.db.commit()
    persisted = _capture_persisted_files(media_scenario, monkeypatch)
    removed = _capture_removed_files(media_scenario, monkeypatch)
    real_release = media_scenario.store.release
    release_attempts = 0

    def reject_first_two_releases(
        files: StoredImageFiles,
        *,
        budget=None,
    ) -> None:
        nonlocal release_attempts
        release_attempts += 1
        if release_attempts <= 2:
            raise OSError("forced post-commit release failure")
        real_release(files, budget=budget)

    monkeypatch.setattr(media_scenario.store, "release", reject_first_two_releases)

    with pytest.raises(OSError, match="forced post-commit release failure"):
        media_scenario.service.register_validated_image(
            _stage(media_scenario, color=(45, 105, 165)),
            uploaded_by_user_id=admin_id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    assert len(persisted) == 1
    files = persisted[0]
    paths = _stored_paths(media_scenario, files)
    assert release_attempts == 2
    assert removed == []
    assert all(path.is_file() for path in paths)
    assert (
        media_scenario.db.scalar(
            select(func.count(EvaluationMediaAsset.id)).where(
                EvaluationMediaAsset.uploaded_by_user_id == admin_id,
            )
        )
        == 1
    )
    assert release_attempts == 3
    assert files.original_ownership is not None
    assert files.preview_ownership is not None
    assert files.original_ownership.parent_descriptor is None
    assert files.preview_ownership.parent_descriptor is None
    assert files.original_ownership.anchor_descriptor is None
    assert files.preview_ownership.anchor_descriptor is None
    assert all(path.is_file() for path in paths)
    retry_transaction = media_scenario.db.get_transaction()
    assert retry_transaction is not None
    retry_transaction.rollback()


@pytest.mark.parametrize("caller_owned_transaction", [False, True])
def test_earlier_after_commit_failure_never_removes_durable_files(
    media_scenario: MediaScenario,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caller_owned_transaction: bool,
) -> None:
    admin_id = media_scenario.admin.id
    media_scenario.db.commit()
    persisted = _capture_persisted_files(media_scenario, monkeypatch)
    removed = _capture_removed_files(media_scenario, monkeypatch)
    observer_calls = 0

    def reject_first_after_commit(db: Session) -> None:
        nonlocal observer_calls
        if db.get_nested_transaction() is not None:
            return
        observer_calls += 1
        if observer_calls == 1:
            raise OSError("forced earlier after-commit failure")

    event.listen(media_scenario.db, "after_commit", reject_first_after_commit)
    try:
        if caller_owned_transaction:
            transaction = media_scenario.db.begin()
            media_scenario.service.register_validated_image(
                _stage(media_scenario, color=(47, 107, 167)),
                uploaded_by_user_id=admin_id,
                project_id=INITIAL_PROJECTS[0].id,
            )
            with pytest.raises(OSError, match="forced earlier after-commit failure"):
                transaction.commit()
        else:
            with pytest.raises(OSError, match="forced earlier after-commit failure"):
                media_scenario.service.register_validated_image(
                    _stage(media_scenario, color=(47, 107, 167)),
                    uploaded_by_user_id=admin_id,
                    project_id=INITIAL_PROJECTS[0].id,
                )
    finally:
        event.remove(media_scenario.db, "after_commit", reject_first_after_commit)

    assert observer_calls == 1
    assert len(persisted) == 1
    files = persisted[0]
    paths = _stored_paths(media_scenario, files)
    media_scenario.db.close()

    assert removed == []
    assert all(path.is_file() for path in paths)
    assert files.original_ownership is not None
    assert files.preview_ownership is not None
    assert files.original_ownership.parent_descriptor is None
    assert files.preview_ownership.parent_descriptor is None
    assert files.original_ownership.anchor_descriptor is None
    assert files.preview_ownership.anchor_descriptor is None

    with Session(postgres_engine) as verification_db:
        assert (
            verification_db.scalar(
                select(func.count(EvaluationMediaAsset.id)).where(
                    EvaluationMediaAsset.uploaded_by_user_id == admin_id,
                )
            )
            == 1
        )
    assert all(path.is_file() for path in paths)


def test_session_close_implicit_rollback_removes_registered_files(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin_id = media_scenario.admin.id
    media_scenario.db.commit()
    persisted = _capture_persisted_files(media_scenario, monkeypatch)
    removed = _capture_removed_files(media_scenario, monkeypatch)

    media_scenario.db.begin()
    registered = media_scenario.service.register_validated_image(
        _stage(media_scenario, color=(50, 110, 170)),
        uploaded_by_user_id=admin_id,
        project_id=INITIAL_PROJECTS[0].id,
    )
    assert len(persisted) == 1
    paths = _stored_paths(media_scenario, persisted[0])
    assert all(path.is_file() for path in paths)

    media_scenario.db.close()

    assert removed == persisted
    assert all(not path.exists() for path in paths)
    assert (
        media_scenario.db.scalar(
            select(func.count(EvaluationMediaAsset.id)).where(
                EvaluationMediaAsset.id == registered.id,
            )
        )
        == 0
    )


@pytest.mark.parametrize(
    ("status_field", "status_value"),
    [
        ("storage_status", "missing"),
        ("validation_status", "invalid"),
        ("retention_status", "archived"),
    ],
)
def test_sha_race_with_unavailable_blob_is_generic_and_preserves_shared_files(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
    status_field: str,
    status_value: str,
) -> None:
    _register(media_scenario)
    blob = media_scenario.db.scalar(select(EvaluationMediaBlob))
    assert blob is not None
    setattr(blob, status_field, status_value)
    media_scenario.db.commit()
    files_before = {
        path.relative_to(media_scenario.store.root)
        for path in media_scenario.store.root.rglob("*")
        if path.is_file()
    }
    staged = _stage(media_scenario)
    real_scalar = media_scenario.db.scalar
    lookup_count = 0

    def hide_blob_from_initial_lookup(*args: object, **kwargs: object):
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count == 1:
            return None
        return real_scalar(*args, **kwargs)

    monkeypatch.setattr(
        media_scenario.db,
        "scalar",
        hide_blob_from_initial_lookup,
    )

    with pytest.raises(MediaValidationFailed) as error:
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    files_after = {
        path.relative_to(media_scenario.store.root)
        for path in media_scenario.store.root.rglob("*")
        if path.is_file()
    }
    assert error.value.internal_reason == "content_unavailable"
    assert str(error.value) == "media validation failed"
    assert error.value.__cause__ is None
    assert lookup_count == 2
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaBlob.id))) == 1
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaAsset.id))) == 1
    assert files_after == files_before
    assert not staged.quarantine_path.exists()


def test_sha_race_with_disappearing_winner_is_generic_and_cleans_new_files(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(media_scenario)
    files_before = {
        path.relative_to(media_scenario.store.root)
        for path in media_scenario.store.root.rglob("*")
        if path.is_file()
    }
    staged = _stage(media_scenario)
    real_scalar = media_scenario.db.scalar
    lookup_count = 0

    def hide_blob_from_both_lookups(*args: object, **kwargs: object):
        nonlocal lookup_count
        lookup_count += 1
        if lookup_count <= 2:
            return None
        return real_scalar(*args, **kwargs)

    monkeypatch.setattr(
        media_scenario.db,
        "scalar",
        hide_blob_from_both_lookups,
    )

    with pytest.raises(MediaValidationFailed) as error:
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    files_after = {
        path.relative_to(media_scenario.store.root)
        for path in media_scenario.store.root.rglob("*")
        if path.is_file()
    }
    assert error.value.internal_reason == "content_unavailable"
    assert error.value.__cause__ is None
    assert lookup_count == 2
    assert files_after == files_before
    assert not staged.quarantine_path.exists()


def test_reuse_rechecks_blob_status_inside_asset_transaction(
    media_scenario: MediaScenario,
    postgres_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(media_scenario)
    blob = media_scenario.db.scalar(select(EvaluationMediaBlob))
    assert blob is not None
    assert blob.storage_status == "available"
    staged = _stage(media_scenario)
    real_register = media_scenario.service._register_with_blob
    transitioned = False

    def transition_before_registration(
        selected_blob: EvaluationMediaBlob,
        *,
        uploaded_by_user_id,
        project_id,
    ):
        nonlocal transitioned
        with Session(postgres_engine) as transition_db, transition_db.begin():
            current = transition_db.get(EvaluationMediaBlob, selected_blob.id)
            assert current is not None
            current.storage_status = "missing"
        transitioned = True
        assert selected_blob.storage_status == "available"
        return real_register(
            selected_blob,
            uploaded_by_user_id=uploaded_by_user_id,
            project_id=project_id,
        )

    monkeypatch.setattr(
        media_scenario.service,
        "_register_with_blob",
        transition_before_registration,
    )

    with pytest.raises(MediaValidationFailed) as error:
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    assert error.value.internal_reason == "content_unavailable"
    assert transitioned is True
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaAsset.id))) == 1
    assert not staged.quarantine_path.exists()


def test_database_failure_removes_only_files_created_by_that_registration(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _register(media_scenario)
    files_before = {
        path.relative_to(media_scenario.store.root)
        for path in media_scenario.store.root.rglob("*")
        if path.is_file()
    }
    staged = _stage(media_scenario, color=(150, 20, 40))

    real_flush = media_scenario.db.flush

    def reject_persisted_blob_flush(*args: object, **kwargs: object) -> None:
        if not staged.quarantine_path.exists():
            raise RuntimeError("forced media database failure")
        real_flush(*args, **kwargs)

    monkeypatch.setattr(media_scenario.db, "flush", reject_persisted_blob_flush)
    with pytest.raises(RuntimeError, match="forced media database failure"):
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    files_after = {
        path.relative_to(media_scenario.store.root)
        for path in media_scenario.store.root.rglob("*")
        if path.is_file()
    }
    assert files_after == files_before
    assert not staged.quarantine_path.exists()


@pytest.mark.parametrize("replaced_target", ["original", "preview"])
def test_database_failure_preserves_replacement_and_removes_owned_moved_file(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
    replaced_target: str,
) -> None:
    staged = _stage(media_scenario, color=(160, 35, 90))
    shared = media_scenario.store.root / "preexisting-shared-object"
    shared.write_bytes(b"preexisting-shared-object")
    real_persist = media_scenario.store.persist
    real_flush = media_scenario.db.flush
    target_path: Path | None = None
    moved_owned: Path | None = None

    def persist_then_replace(
        image: ValidatedImage,
        *,
        blob_id,
    ) -> StoredImageFiles:
        nonlocal target_path, moved_owned
        files = real_persist(image, blob_id=blob_id)
        target_key = (
            files.storage_key if replaced_target == "original" else files.preview_storage_key
        )
        target_path = media_scenario.store.root / target_key
        moved_owned = target_path.with_name(f"moved-{target_path.name}")
        target_path.rename(moved_owned)
        os.link(shared, target_path)
        return files

    def reject_blob_flush(*args: object, **kwargs: object) -> None:
        if target_path is not None:
            raise RuntimeError("forced database failure after replacement")
        real_flush(*args, **kwargs)

    monkeypatch.setattr(media_scenario.store, "persist", persist_then_replace)
    monkeypatch.setattr(media_scenario.db, "flush", reject_blob_flush)

    with pytest.raises(RuntimeError, match="forced database failure after replacement"):
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    assert target_path is not None
    assert moved_owned is not None
    assert target_path.read_bytes() == shared.read_bytes()
    assert not moved_owned.exists()
    assert not staged.quarantine_path.exists()
    assert list(media_scenario.store.quarantine_root.iterdir()) == []


def test_registration_rejects_shard_replaced_before_filesystem_finalize(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    staged = _stage(media_scenario, color=(35, 160, 90))
    outside = tmp_path / "outside-before-finalize"
    outside.mkdir()
    displaced = tmp_path / "displaced-before-finalize"
    real_persist = media_scenario.store.persist

    def persist_then_replace_shard(
        image: ValidatedImage,
        *,
        blob_id,
    ) -> StoredImageFiles:
        files = real_persist(image, blob_id=blob_id)
        original_path = media_scenario.store.root / files.storage_key
        original_path.parent.rename(displaced)
        original_path.parent.symlink_to(outside, target_is_directory=True)
        return files

    monkeypatch.setattr(
        media_scenario.store,
        "persist",
        persist_then_replace_shard,
    )

    with pytest.raises(RuntimeError, match="private media ownership was lost"):
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    assert list(outside.iterdir()) == []
    assert list(displaced.iterdir()) == []
    assert not staged.quarantine_path.exists()
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaBlob.id))) == 0
    assert media_scenario.db.scalar(select(func.count(EvaluationMediaAsset.id))) == 0


def test_registration_discards_staged_input_when_store_persist_fails(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = _stage(media_scenario, color=(90, 40, 170))

    def reject_persist(*args: object, **kwargs: object) -> StoredImageFiles:
        raise RuntimeError("forced media persistence failure")

    monkeypatch.setattr(media_scenario.store, "persist", reject_persist)

    with pytest.raises(RuntimeError, match="forced media persistence failure"):
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    assert not staged.quarantine_path.exists()
    assert list(media_scenario.store.quarantine_root.iterdir()) == []


def test_registration_discards_staged_input_when_blob_lookup_fails(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staged = _stage(media_scenario, color=(30, 145, 80))

    def reject_blob_lookup(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced media blob lookup failure")

    monkeypatch.setattr(media_scenario.db, "scalar", reject_blob_lookup)

    with pytest.raises(RuntimeError, match="forced media blob lookup failure"):
        media_scenario.service.register_validated_image(
            staged,
            uploaded_by_user_id=media_scenario.admin.id,
            project_id=INITIAL_PROJECTS[0].id,
        )

    assert not staged.quarantine_path.exists()
    assert list(media_scenario.store.quarantine_root.iterdir()) == []


def test_protected_reads_use_safe_headers_and_minimal_original_audit(
    media_scenario: MediaScenario,
) -> None:
    registered = _register(media_scenario)

    original = media_scenario.service.read_original(
        registered.id,
        actor_user_id=media_scenario.admin.id,
        occurred_at=NOW,
        request_id="media-original-read",
    )
    preview = media_scenario.service.read_preview(
        registered.id,
        actor_user_id=media_scenario.admin.id,
        occurred_at=NOW,
    )

    assert original.mime_type == "image/png"
    assert original.headers == {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="evaluation-image.png"',
    }
    assert preview.mime_type == "image/webp"
    assert preview.headers == {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": 'inline; filename="evaluation-image.webp"',
    }
    assert original.path.is_file()
    assert preview.path.is_file()
    audit = media_scenario.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "evaluation.media.original.read",
            AuditEvent.resource_id == registered.id,
        )
    )
    assert audit is not None
    assert audit.actor_user_id == media_scenario.admin.id
    assert audit.outcome == "allowed"
    assert audit.purpose is None
    assert audit.event_metadata == {"media_id": str(registered.id)}
    audited = str(audit.event_metadata)
    blob = media_scenario.db.scalar(select(EvaluationMediaBlob))
    assert blob is not None
    assert blob.sha256 not in audited
    assert blob.storage_key not in audited
    assert str(media_scenario.store.root) not in audited


def test_protected_read_stays_bound_when_shard_path_is_replaced(
    media_scenario: MediaScenario,
    tmp_path: Path,
) -> None:
    registered = _register(media_scenario)
    blob = media_scenario.db.scalar(select(EvaluationMediaBlob))
    assert blob is not None
    stored_path = media_scenario.store.root / blob.storage_key
    expected = stored_path.read_bytes()

    protected = media_scenario.service.read_original(
        registered.id,
        actor_user_id=media_scenario.admin.id,
        occurred_at=NOW,
        request_id="media-original-race-read",
    )

    outside = tmp_path / "outside-read"
    outside.mkdir()
    displaced = tmp_path / "displaced-read-shard"
    stored_path.parent.rename(displaced)
    stored_path.parent.symlink_to(outside, target_is_directory=True)
    outside_payload = b"outside-private-media-root"
    (outside / stored_path.name).write_bytes(outside_payload)

    protected_payload = protected.path.read_bytes()
    assert protected_payload == expected
    assert protected_payload != outside_payload
    protected.close()


def test_protected_media_file_explicitly_closes_pinned_descriptor(
    media_scenario: MediaScenario,
) -> None:
    registered = _register(media_scenario)
    protected = media_scenario.service.read_preview(
        registered.id,
        actor_user_id=media_scenario.admin.id,
        occurred_at=NOW,
    )
    descriptor = int(protected.path.name)

    assert os.fstat(descriptor).st_size > 0

    protected.close()
    protected.close()

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_original_audit_failure_closes_pinned_descriptor(
    media_scenario: MediaScenario,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = _register(media_scenario)
    real_open = media_scenario.store.open_protected
    retained_paths: list[Path] = []

    def capture_open(storage_key: str) -> Path:
        path = real_open(storage_key)
        retained_paths.append(path)
        return path

    def reject_audit(*args: object, **kwargs: object) -> None:
        raise RuntimeError("forced original read audit failure")

    monkeypatch.setattr(media_scenario.store, "open_protected", capture_open)
    monkeypatch.setattr(media_scenario.service, "_audit_original", reject_audit)

    with pytest.raises(RuntimeError, match="forced original read audit failure"):
        media_scenario.service.read_original(
            registered.id,
            actor_user_id=media_scenario.admin.id,
            occurred_at=NOW,
            request_id="media-original-audit-failure",
        )

    assert len(retained_paths) == 1
    descriptor = int(retained_paths[0].name)
    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_unauthorized_and_random_media_ids_have_the_same_not_found_error(
    media_scenario: MediaScenario,
) -> None:
    registered = _register(media_scenario)

    errors: list[MediaAssetNotFound] = []
    for media_id in (registered.id, uuid4()):
        with pytest.raises(MediaAssetNotFound) as error:
            media_scenario.service.read_original(
                media_id,
                actor_user_id=media_scenario.unauthorized.id,
                occurred_at=NOW,
                request_id=f"media-denied-{uuid4().hex}",
            )
        errors.append(error.value)

    assert [(error.code, str(error)) for error in errors] == [
        ("media_asset_not_found", "media asset not found"),
        ("media_asset_not_found", "media asset not found"),
    ]
    audits = list(
        media_scenario.db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == "evaluation.media.original.read",
                AuditEvent.actor_user_id == media_scenario.unauthorized.id,
            )
            .order_by(AuditEvent.created_at, AuditEvent.id)
        )
    )
    assert len(audits) == 2
    assert {audit.outcome for audit in audits} == {"denied"}
    assert {tuple(audit.event_metadata) for audit in audits} == {("media_id",)}


def test_storage_status_exposes_only_conservative_readiness_and_limits(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    with Session() as db:
        status = EvaluationMediaService(db, store=store).storage_status()

    assert status.ready is True
    assert status.media_writable is True
    assert status.quarantine_writable is True
    assert status.max_bytes == 20 * 1024 * 1024
    assert status.max_pixels == 50_000_000
    assert status.preview_max_edge == 1600
    assert set(vars(status)) == {
        "ready",
        "media_writable",
        "quarantine_writable",
        "max_bytes",
        "max_pixels",
        "preview_max_edge",
    }
    store.close()


def test_reader_rejects_blob_not_marked_available_and_validated(
    media_scenario: MediaScenario,
) -> None:
    registered = _register(media_scenario)
    blob = media_scenario.db.scalar(select(EvaluationMediaBlob))
    assert blob is not None
    blob.storage_status = "missing"
    media_scenario.db.flush()

    with pytest.raises(MediaAssetNotFound):
        media_scenario.service.read_preview(
            registered.id,
            actor_user_id=media_scenario.admin.id,
            occurred_at=NOW,
        )


def test_evaluation_routes_do_not_expose_upload_or_video_methods() -> None:
    from app.main import app

    exposed = {
        (method, route.path)
        for route in app.routes
        for method in getattr(route, "methods", set())
        if route.path.startswith(("/api/v1/evaluations", "/api/v1/integrations/evaluations"))
    }

    assert not {
        (method, path)
        for method, path in exposed
        if method in {"POST", "PUT", "PATCH"}
        and ("media" in path or "upload" in path or "video" in path)
    }
    assert not any("video" in path for _, path in exposed)


def test_store_is_not_ready_when_roots_are_on_different_filesystems(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(PrivateMediaStore, "_same_filesystem", lambda _self: False)

    store = _store(tmp_path)

    assert store.ready is False
    assert store.same_filesystem is False
    with pytest.raises(RuntimeError, match="not ready"):
        store.stage(
            io.BytesIO(_synthetic_image("PNG")),
            declared_filename="blocked.png",
        )
    assert list(store.quarantine_root.iterdir()) == []


def test_concurrent_identical_registrations_converge_on_one_blob(
    postgres_engine: Engine,
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    marker = uuid4().hex
    with Session(postgres_engine) as setup, setup.begin():
        person = Person(
            full_name="媒体并发管理员",
            identity_ciphertext=f"media-race-cipher:{marker}",
            identity_digest=(marker * 2)[:64],
            phone_ciphertext=None,
            phone_digest=None,
            email=f"media-race-{marker[:12]}@example.test",
            sex="female",
            date_of_birth=date(1990, 1, 2),
        )
        setup.add(person)
        setup.flush()
        account = UserAccount(
            person_id=person.id,
            login_phone_digest=(marker[::-1] * 2)[:64],
            email_normalized=f"media-race-{marker[:8]}@example.test",
            password_hash="media-race-password-hash",
            status="active",
            force_password_change=False,
        )
        setup.add(account)
        setup.flush()
        setup.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
        setup.flush()
        person_id = person.id
        account_id = account.id

    store = _store(tmp_path)

    def cleanup() -> None:
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            blobs = list(
                cleanup_db.scalars(
                    select(EvaluationMediaBlob)
                    .join(
                        EvaluationMediaAsset,
                        EvaluationMediaAsset.blob_id == EvaluationMediaBlob.id,
                    )
                    .where(EvaluationMediaAsset.uploaded_by_user_id == account_id)
                    .distinct()
                )
            )
            cleanup_db.execute(
                delete(EvaluationMediaAsset).where(
                    EvaluationMediaAsset.uploaded_by_user_id == account_id
                )
            )
            if blobs:
                cleanup_db.execute(
                    delete(EvaluationMediaBlob).where(
                        EvaluationMediaBlob.id.in_([blob.id for blob in blobs])
                    )
                )
            cleanup_db.execute(delete(UserRole).where(UserRole.user_id == account_id))
            cleanup_db.execute(delete(UserAccount).where(UserAccount.id == account_id))
            cleanup_db.execute(delete(Person).where(Person.id == person_id))
        for blob in blobs:
            (store.root / blob.storage_key).unlink(missing_ok=True)
            (store.root / blob.preview_storage_key).unlink(missing_ok=True)

    request.addfinalizer(cleanup)
    payload = _synthetic_image("PNG", color=(6, 17, 91))
    barrier = threading.Barrier(2)
    worker_ids: set[int] = set()
    synchronized: set[int] = set()
    synchronization_lock = threading.Lock()

    def synchronize_initial_lookup(
        _connection,
        _cursor,
        statement: str,
        _parameters,
        _context,
        _executemany: bool,
    ) -> None:
        thread_id = threading.get_ident()
        if (
            thread_id not in worker_ids
            or "FROM evaluation_media_blobs" not in statement
            or "evaluation_media_blobs.sha256" not in statement
        ):
            return
        with synchronization_lock:
            if thread_id in synchronized:
                return
            synchronized.add(thread_id)
        barrier.wait(timeout=10)

    event.listen(postgres_engine, "after_cursor_execute", synchronize_initial_lookup)
    results: list[object] = []
    errors: list[Exception] = []
    result_lock = threading.Lock()

    def register() -> None:
        with synchronization_lock:
            worker_ids.add(threading.get_ident())
        try:
            staged = store.stage(io.BytesIO(payload), declared_filename="race.png")
            with Session(postgres_engine) as worker:
                result = EvaluationMediaService(worker, store=store).register_validated_image(
                    staged,
                    uploaded_by_user_id=account_id,
                    project_id=INITIAL_PROJECTS[0].id,
                )
            with result_lock:
                results.append(result)
        except Exception as exc:  # noqa: BLE001 - asserted below
            with result_lock:
                errors.append(exc)

    workers = [threading.Thread(target=register) for _ in range(2)]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
    finally:
        event.remove(postgres_engine, "after_cursor_execute", synchronize_initial_lookup)

    assert all(not worker.is_alive() for worker in workers)
    assert errors == []
    assert len(results) == 2
    with Session(postgres_engine) as verify:
        assets = list(
            verify.scalars(
                select(EvaluationMediaAsset).where(
                    EvaluationMediaAsset.uploaded_by_user_id == account_id
                )
            )
        )
        assert len(assets) == 2
        assert len({asset.blob_id for asset in assets}) == 1
        assert (
            verify.scalar(
                select(func.count(EvaluationMediaBlob.id)).where(
                    EvaluationMediaBlob.id == assets[0].blob_id
                )
            )
            == 1
        )
    assert len([path for path in store.root.rglob("*") if path.is_file()]) == 2
    assert list(store.quarantine_root.iterdir()) == []
