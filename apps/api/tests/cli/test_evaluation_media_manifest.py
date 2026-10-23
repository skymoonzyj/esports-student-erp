from __future__ import annotations

import hashlib
import io
import json
import stat
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import SecretStr
from sqlalchemy.orm import Session

import app.cli.evaluation_media_manifest as manifest_module
from app.cli.evaluation_media_manifest import (
    MediaManifestAccessError,
    MediaManifestReport,
    reconcile_media_manifest,
    sha256_stream,
    write_manifest_json,
)
from app.core.config import Settings
from app.evaluations.media_models import EvaluationMediaBlob

TEST_KEY = SecretStr("AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8=")


def _settings(
    tmp_path: Path,
    *,
    app_env: str = "test",
) -> Settings:
    return Settings(
        app_env=app_env,
        database_url="postgresql+psycopg://erp:erp@db:5432/erp_e2e",
        session_cookie_secure=app_env == "production",
        field_encryption_master_key=TEST_KEY,
        field_lookup_key=TEST_KEY,
        session_token_pepper=TEST_KEY,
        evaluation_tool_service_token="evaluation-local-service-token-2026-change-me",
        evaluation_media_root=tmp_path / "private-media",
        evaluation_media_quarantine_root=tmp_path / "private-quarantine",
    )


def _keys(blob_id: UUID) -> tuple[str, str]:
    shard = blob_id.hex
    return (
        f"objects/{shard[:2]}/{shard[2:4]}/{blob_id}.png",
        f"previews/{shard[:2]}/{shard[2:4]}/{blob_id}.webp",
    )


def _write(root: Path, key: str, content: bytes) -> None:
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _blob(blob_id: UUID, *, expected_content: bytes) -> EvaluationMediaBlob:
    storage_key, preview_storage_key = _keys(blob_id)
    return EvaluationMediaBlob(
        id=blob_id,
        storage_key=storage_key,
        preview_storage_key=preview_storage_key,
        sha256=hashlib.sha256(expected_content).hexdigest(),
        mime_type="image/png",
        canonical_extension="png",
        size_bytes=len(expected_content),
        width_pixels=1,
        height_pixels=1,
        storage_status="available",
        validation_status="validated",
        retention_status="retained",
    )


def test_manifest_classifies_registered_missing_mismatch_and_orphan_without_mutation(
    db: Session,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    media_root, _ = settings.evaluation_media_paths()
    valid_id = UUID("51000000-0000-4000-8000-000000000001")
    missing_id = UUID("51000000-0000-4000-8000-000000000002")
    mismatch_id = UUID("51000000-0000-4000-8000-000000000003")
    orphan_id = UUID("51000000-0000-4000-8000-000000000004")
    valid = _blob(valid_id, expected_content=b"registered-valid")
    missing = _blob(missing_id, expected_content=b"registered-missing")
    mismatch = _blob(mismatch_id, expected_content=b"registered-expected")
    db.add_all([valid, missing, mismatch])
    db.commit()
    _write(media_root, valid.storage_key, b"registered-valid")
    _write(media_root, valid.preview_storage_key, b"valid-preview")
    _write(media_root, mismatch.storage_key, b"registered-tampered")
    _write(media_root, mismatch.preview_storage_key, b"mismatch-preview")
    orphan_key, _ = _keys(orphan_id)
    _write(media_root, orphan_key, b"unregistered-orphan-content")

    report = reconcile_media_manifest(db, settings=settings)

    payload = report.as_dict()
    assert payload["registered-and-valid"] == {
        "count": 1,
        "ids": [str(valid_id)],
    }
    assert payload["database-row-missing-file"] == {
        "count": 1,
        "ids": [str(missing_id)],
    }
    assert payload["hash-mismatch"] == {
        "count": 1,
        "ids": [str(mismatch_id)],
    }
    orphan_result = payload["unregistered-orphan"]
    assert orphan_result["count"] == 1
    assert len(orphan_result["ids"]) == 1
    assert orphan_result["ids"][0] != orphan_key
    assert "/" not in orphan_result["ids"][0]

    db.refresh(missing)
    db.refresh(mismatch)
    assert missing.storage_status == "available"
    assert mismatch.validation_status == "validated"
    assert (media_root / orphan_key).read_bytes() == b"unregistered-orphan-content"

    output_path = settings.evaluation_media_quarantine_root / "reports" / "manifest.json"
    write_manifest_json(report, output_path=output_path, settings=settings)
    assert json.loads(output_path.read_text(encoding="utf-8")) == payload
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    rendered = output_path.read_text(encoding="utf-8")
    assert str(media_root) not in rendered
    assert "objects/" not in rendered
    assert "registered-valid" not in rendered
    assert "unregistered-orphan-content" not in rendered

    reconcile_media_manifest(db, settings=settings, record_status=True)
    db.refresh(missing)
    db.refresh(mismatch)
    assert missing.storage_status == "missing"
    assert mismatch.validation_status == "invalid"
    assert (media_root / mismatch.storage_key).exists()
    assert (media_root / orphan_key).exists()


def test_manifest_hashes_streams_in_bounded_chunks() -> None:
    class TrackingStream(io.BytesIO):
        def __init__(self, content: bytes) -> None:
            super().__init__(content)
            self.read_sizes: list[int] = []

        def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            return super().read(size)

    content = b"0123456789abcdef"
    stream = TrackingStream(content)

    digest = sha256_stream(stream, chunk_bytes=4)

    assert digest == hashlib.sha256(content).hexdigest()
    assert stream.read_sizes == [4, 4, 4, 4, 4]


def test_manifest_default_run_does_not_create_configured_directories(
    db: Session,
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    media_root, quarantine_root = settings.evaluation_media_paths()
    assert not media_root.exists()
    assert not quarantine_root.exists()

    report = reconcile_media_manifest(db, settings=settings)

    assert report == MediaManifestReport.empty()
    assert not media_root.exists()
    assert not quarantine_root.exists()


def test_manifest_root_permission_error_aborts_instead_of_reporting_missing(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    media_root, _ = settings.evaluation_media_paths()
    media_root.mkdir(mode=0o700, parents=True)
    original_open = manifest_module.os.open

    def deny_root(path: object, flags: int, *args: object, **kwargs: object) -> int:
        if manifest_module.os.fspath(path) == manifest_module.os.fspath(media_root):
            raise PermissionError("synthetic root denial")
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(manifest_module.os, "open", deny_root)

    with pytest.raises(MediaManifestAccessError, match="cannot be read"):
        reconcile_media_manifest(db, settings=settings, record_status=True)


def test_manifest_file_read_error_aborts_without_recording_status(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    media_root, _ = settings.evaluation_media_paths()
    blob_id = UUID("51000000-0000-4000-8000-000000000011")
    blob = _blob(blob_id, expected_content=b"registered-content")
    db.add(blob)
    db.commit()
    _write(media_root, blob.storage_key, b"registered-content")
    _write(media_root, blob.preview_storage_key, b"preview-content")

    def fail_hash(*args: object, **kwargs: object) -> str:
        raise OSError("synthetic read failure")

    monkeypatch.setattr(manifest_module, "sha256_stream", fail_hash)

    with pytest.raises(MediaManifestAccessError, match="cannot be read"):
        reconcile_media_manifest(db, settings=settings, record_status=True)

    db.refresh(blob)
    assert blob.storage_status == "available"
    assert blob.validation_status == "validated"


def test_manifest_scan_error_aborts_without_recording_planned_missing_status(
    db: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    media_root, _ = settings.evaluation_media_paths()
    (media_root / "objects").mkdir(mode=0o700, parents=True)
    blob = _blob(
        UUID("51000000-0000-4000-8000-000000000012"),
        expected_content=b"missing-content",
    )
    db.add(blob)
    db.commit()

    def fail_scan(*args: object, **kwargs: object) -> object:
        raise PermissionError("synthetic scan denial")

    monkeypatch.setattr(manifest_module.os, "scandir", fail_scan)

    with pytest.raises(MediaManifestAccessError, match="cannot be scanned"):
        reconcile_media_manifest(db, settings=settings, record_status=True)

    db.refresh(blob)
    assert blob.storage_status == "available"
    assert blob.validation_status == "validated"

def test_manifest_rejects_status_writes_in_production(
    db: Session,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="test or development"):
        reconcile_media_manifest(
            db,
            settings=_settings(tmp_path, app_env="production"),
            record_status=True,
        )


def test_manifest_json_must_stay_under_configured_quarantine_root(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    report = MediaManifestReport.empty()

    with pytest.raises(ValueError, match="configured ignored output directory"):
        write_manifest_json(
            report,
            output_path=tmp_path / "outside" / "manifest.json",
            settings=settings,
        )


def test_manifest_json_rejects_output_root_owned_by_another_user(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _, output_root = settings.evaluation_media_paths()
    output_root.mkdir(mode=0o700, parents=True)
    monkeypatch.setattr(manifest_module.os, "geteuid", lambda: manifest_module.os.getuid() + 1)

    with pytest.raises(ValueError, match="owned by the current user"):
        write_manifest_json(
            MediaManifestReport.empty(),
            output_path=output_root / "manifest.json",
            settings=settings,
        )


def test_manifest_json_aborts_if_checked_parent_is_replaced_by_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _, output_root = settings.evaluation_media_paths()
    reports = output_root / "reports"
    reports.mkdir(mode=0o700, parents=True)
    displaced = output_root / "reports-displaced"
    outside = tmp_path / "outside-target"
    outside.mkdir(mode=0o700)
    original_open = manifest_module.os.open
    swapped = False

    def swap_before_temp_open(
        path: object,
        flags: int,
        *args: object,
        **kwargs: object,
    ) -> int:
        nonlocal swapped
        name = manifest_module.os.fspath(path)
        if (
            not swapped
            and flags & manifest_module.os.O_CREAT
            and manifest_module.os.path.basename(name).startswith(".manifest.json.")
        ):
            reports.rename(displaced)
            reports.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(manifest_module.os, "open", swap_before_temp_open)

    with pytest.raises(ValueError, match="changed during manifest write"):
        write_manifest_json(
            MediaManifestReport.empty(),
            output_path=reports / "manifest.json",
            settings=settings,
        )

    assert not (outside / "manifest.json").exists()
    assert not list(displaced.iterdir())
