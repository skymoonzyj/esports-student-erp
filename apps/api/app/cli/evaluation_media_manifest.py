from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import stat
import subprocess
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.database import SessionLocal
from app.evaluations.media_models import EvaluationMediaBlob
from app.evaluations.media_store import PrivateMediaStore

_CHUNK_BYTES = 1024 * 1024
_SCAN_MAX_ENTRIES = 1_000_000
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_OPEN_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_RESULT_KEYS = (
    "registered-and-valid",
    "database-row-missing-file",
    "hash-mismatch",
    "unregistered-orphan",
)
_MISSING_ACCESS_ERRNOS = {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}


class MediaManifestAccessError(RuntimeError):
    pass


@dataclass(frozen=True)
class MediaManifestReport:
    registered_and_valid: tuple[str, ...]
    database_row_missing_file: tuple[str, ...]
    hash_mismatch: tuple[str, ...]
    unregistered_orphan: tuple[str, ...]

    @classmethod
    def empty(cls) -> MediaManifestReport:
        return cls((), (), (), ())

    def as_dict(self) -> dict[str, dict[str, int | list[str]]]:
        values = (
            self.registered_and_valid,
            self.database_row_missing_file,
            self.hash_mismatch,
            self.unregistered_orphan,
        )
        return {
            key: {"count": len(ids), "ids": list(ids)}
            for key, ids in zip(_RESULT_KEYS, values, strict=True)
        }


def sha256_stream(stream: BinaryIO, *, chunk_bytes: int = _CHUNK_BYTES) -> str:
    if chunk_bytes < 1:
        raise ValueError("chunk_bytes must be positive")
    digest = hashlib.sha256()
    while chunk := stream.read(chunk_bytes):
        digest.update(chunk)
    return digest.hexdigest()


def _open_root(root: Path) -> int | None:
    descriptor: int | None = None
    try:
        descriptor = os.open(root, _DIRECTORY_OPEN_FLAGS)
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            descriptor = None
            return None
        result = descriptor
        descriptor = None
        return result
    except OSError as exc:
        if exc.errno in _MISSING_ACCESS_ERRNOS:
            return None
        raise MediaManifestAccessError(
            "private media root cannot be read"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _open_storage_descriptor(
    root_descriptor: int,
    storage_key: str,
) -> int | None:
    current: int | None = None
    opened: int | None = None
    try:
        prefix, first, second, filename = PrivateMediaStore._key_parts(storage_key)
        current = os.dup(root_descriptor)
        for component in (prefix, first, second):
            child = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=current,
            )
            os.close(current)
            current = child
        opened = os.open(filename, _FILE_OPEN_FLAGS, dir_fd=current)
        if not stat.S_ISREG(os.fstat(opened).st_mode):
            os.close(opened)
            opened = None
            return None
        descriptor = opened
        opened = None
        return descriptor
    except ValueError:
        return None
    except OSError as exc:
        if exc.errno in _MISSING_ACCESS_ERRNOS:
            return None
        raise MediaManifestAccessError(
            "registered media file cannot be read"
        ) from exc
    finally:
        if opened is not None:
            os.close(opened)
        if current is not None:
            os.close(current)


def _registered_file_hash(
    root_descriptor: int | None,
    storage_key: str,
) -> str | None:
    if root_descriptor is None:
        return None
    descriptor = _open_storage_descriptor(root_descriptor, storage_key)
    if descriptor is None:
        return None
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            return sha256_stream(stream)
    except OSError as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise MediaManifestAccessError(
            "registered media file cannot be read"
        ) from exc


def _registered_file_exists(
    root_descriptor: int | None,
    storage_key: str,
) -> bool:
    if root_descriptor is None:
        return False
    descriptor = _open_storage_descriptor(root_descriptor, storage_key)
    if descriptor is None:
        return False
    try:
        os.close(descriptor)
    except OSError as exc:
        raise MediaManifestAccessError(
            "registered media file cannot be read"
        ) from exc
    return True


def _iter_regular_storage_keys(root_descriptor: int | None) -> Iterator[str]:
    if root_descriptor is None:
        return
    visited = 0
    for prefix in ("objects", "previews"):
        try:
            prefix_descriptor = os.open(
                prefix,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=root_descriptor,
            )
        except OSError as exc:
            if exc.errno in _MISSING_ACCESS_ERRNOS:
                continue
            raise MediaManifestAccessError(
                "private media directory cannot be scanned"
            ) from exc
        pending: list[tuple[str, int]] = [(prefix, prefix_descriptor)]
        try:
            while pending:
                relative_directory, directory_descriptor = pending.pop()
                try:
                    try:
                        with os.scandir(directory_descriptor) as entries:
                            for entry in entries:
                                visited += 1
                                if visited > _SCAN_MAX_ENTRIES:
                                    raise RuntimeError(
                                        "private media manifest scan limit exceeded"
                                    )
                                try:
                                    if entry.is_dir(follow_symlinks=False):
                                        child = os.open(
                                            entry.name,
                                            _DIRECTORY_OPEN_FLAGS,
                                            dir_fd=directory_descriptor,
                                        )
                                        pending.append(
                                            (
                                                f"{relative_directory}/{entry.name}",
                                                child,
                                            )
                                        )
                                    elif entry.is_file(follow_symlinks=False):
                                        yield f"{relative_directory}/{entry.name}"
                                except OSError as exc:
                                    if exc.errno in _MISSING_ACCESS_ERRNOS:
                                        continue
                                    raise MediaManifestAccessError(
                                        "private media directory cannot be scanned"
                                    ) from exc
                    except MediaManifestAccessError:
                        raise
                    except OSError as exc:
                        raise MediaManifestAccessError(
                            "private media directory cannot be scanned"
                        ) from exc
                finally:
                    os.close(directory_descriptor)
        finally:
            for _, descriptor in pending:
                os.close(descriptor)


def _orphan_id(storage_key: str) -> str:
    return hashlib.sha256(
        f"evaluation-media-orphan\0{storage_key}".encode()
    ).hexdigest()


def reconcile_media_manifest(
    db: Session,
    *,
    settings: Settings,
    record_status: bool = False,
) -> MediaManifestReport:
    if record_status and settings.app_env not in {"test", "development"}:
        raise ValueError("record-status is limited to test or development")
    media_root, _ = settings.evaluation_media_paths()
    root_descriptor = _open_root(media_root)

    valid_ids: list[str] = []
    missing_ids: list[str] = []
    mismatch_ids: list[str] = []
    missing_blobs: list[EvaluationMediaBlob] = []
    mismatch_blobs: list[EvaluationMediaBlob] = []
    registered_keys: set[str] = set()
    try:
        blobs = db.scalars(
            select(EvaluationMediaBlob).order_by(EvaluationMediaBlob.id)
        ).yield_per(500)
        for blob in blobs:
            registered_keys.update((blob.storage_key, blob.preview_storage_key))
            original_hash = _registered_file_hash(
                root_descriptor,
                blob.storage_key,
            )
            preview_exists = _registered_file_exists(
                root_descriptor,
                blob.preview_storage_key,
            )
            if original_hash is None or not preview_exists:
                missing_ids.append(str(blob.id))
                missing_blobs.append(blob)
                continue
            if original_hash != blob.sha256:
                mismatch_ids.append(str(blob.id))
                mismatch_blobs.append(blob)
                continue
            valid_ids.append(str(blob.id))

        orphan_ids = sorted(
            _orphan_id(storage_key)
            for storage_key in _iter_regular_storage_keys(root_descriptor)
            if storage_key not in registered_keys
        )
        if record_status:
            for blob in missing_blobs:
                blob.storage_status = "missing"
            for blob in mismatch_blobs:
                blob.validation_status = "invalid"
            db.flush()
    finally:
        if root_descriptor is not None:
            os.close(root_descriptor)

    return MediaManifestReport(
        registered_and_valid=tuple(valid_ids),
        database_row_missing_file=tuple(missing_ids),
        hash_mismatch=tuple(mismatch_ids),
        unregistered_orphan=tuple(orphan_ids),
    )


def _is_git_ignored(path: Path) -> bool:
    if not path.is_relative_to(_REPOSITORY_ROOT):
        return True
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--", str(path)],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
    )
    return result.returncode == 0


@dataclass
class _PreparedManifestOutput:
    root_path: Path
    root_descriptor: int
    parent_descriptor: int
    parent_parts: tuple[str, ...]
    filename: str

    def close(self) -> None:
        os.close(self.parent_descriptor)
        os.close(self.root_descriptor)


def _require_owned_output_directory(descriptor: int) -> None:
    try:
        details = os.fstat(descriptor)
    except OSError as exc:
        raise ValueError("manifest output directory cannot be inspected") from exc
    if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
        raise ValueError("manifest output directory must be owned by the current user")
    try:
        os.fchmod(descriptor, 0o700)
    except OSError as exc:
        raise ValueError("manifest output directory cannot be secured") from exc


def _prepare_output_path(
    output_path: Path,
    settings: Settings,
) -> _PreparedManifestOutput:
    if not output_path.expanduser().is_absolute():
        raise ValueError("json output must use an absolute path")
    _, configured_root = settings.evaluation_media_paths()
    candidate = Path(os.path.abspath(output_path.expanduser()))
    try:
        relative = candidate.relative_to(configured_root)
    except ValueError as exc:
        raise ValueError(
            "json output must use the configured ignored output directory"
        ) from exc
    if (
        not relative.parts
        or candidate.suffix.lower() != ".json"
        or not _is_git_ignored(candidate)
    ):
        raise ValueError("json output must use the configured ignored output directory")

    root_descriptor: int | None = None
    parent_descriptor: int | None = None
    try:
        configured_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_descriptor = os.open(configured_root, _DIRECTORY_OPEN_FLAGS)
        _require_owned_output_directory(root_descriptor)
        parent_descriptor = os.dup(root_descriptor)
        parent_parts = tuple(relative.parts[:-1])
        for component in parent_parts:
            try:
                os.mkdir(component, 0o700, dir_fd=parent_descriptor)
            except FileExistsError:
                pass
            child = os.open(
                component,
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=parent_descriptor,
            )
            try:
                _require_owned_output_directory(child)
            except Exception:
                os.close(child)
                raise
            os.close(parent_descriptor)
            parent_descriptor = child
        prepared = _PreparedManifestOutput(
            root_path=configured_root,
            root_descriptor=root_descriptor,
            parent_descriptor=parent_descriptor,
            parent_parts=parent_parts,
            filename=relative.parts[-1],
        )
        root_descriptor = None
        parent_descriptor = None
        return prepared
    except ValueError:
        raise
    except OSError as exc:
        raise ValueError(
            "json output must use the configured ignored output directory"
        ) from exc
    finally:
        if parent_descriptor is not None:
            os.close(parent_descriptor)
        if root_descriptor is not None:
            os.close(root_descriptor)


def _output_parent_is_current(output: _PreparedManifestOutput) -> bool:
    current: int | None = None
    try:
        current = os.open(output.root_path, _DIRECTORY_OPEN_FLAGS)
        retained_root = os.fstat(output.root_descriptor)
        current_root = os.fstat(current)
        if (
            retained_root.st_dev != current_root.st_dev
            or retained_root.st_ino != current_root.st_ino
        ):
            return False
        for component in output.parent_parts:
            child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=current)
            os.close(current)
            current = child
        retained_parent = os.fstat(output.parent_descriptor)
        current_parent = os.fstat(current)
        return (
            retained_parent.st_dev == current_parent.st_dev
            and retained_parent.st_ino == current_parent.st_ino
        )
    except OSError:
        return False
    finally:
        if current is not None:
            os.close(current)


def write_manifest_json(
    report: MediaManifestReport,
    *,
    output_path: Path,
    settings: Settings,
) -> None:
    destination = _prepare_output_path(output_path, settings)
    rendered = (
        json.dumps(report.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        + "\n"
    ).encode("utf-8")
    temporary = f".{destination.filename}.{uuid4().hex}.tmp"
    descriptor: int | None = None
    temporary_exists = False
    destination_written = False
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=destination.parent_descriptor,
        )
        temporary_exists = True
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            descriptor = None
            stream.write(rendered)
            stream.flush()
            os.fsync(stream.fileno())
        if not _output_parent_is_current(destination):
            raise ValueError("manifest output directory changed during manifest write")
        os.replace(
            temporary,
            destination.filename,
            src_dir_fd=destination.parent_descriptor,
            dst_dir_fd=destination.parent_descriptor,
        )
        temporary_exists = False
        destination_written = True
        os.fsync(destination.parent_descriptor)
        if not _output_parent_is_current(destination):
            raise ValueError("manifest output directory changed during manifest write")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary_exists:
            try:
                os.unlink(temporary, dir_fd=destination.parent_descriptor)
            except FileNotFoundError:
                pass
        if destination_written and not _output_parent_is_current(destination):
            try:
                os.unlink(destination.filename, dir_fd=destination.parent_descriptor)
                os.fsync(destination.parent_descriptor)
            except FileNotFoundError:
                pass
        destination.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Reconcile private evaluation media without deleting files.",
    )
    parser.add_argument(
        "--record-status",
        action="store_true",
        help="Record missing or invalid status in test/development only.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="Write a 0600 report below the configured quarantine directory.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    settings = get_settings()
    settings.validate_runtime_secrets()
    with SessionLocal() as db:
        report = reconcile_media_manifest(
            db,
            settings=settings,
            record_status=arguments.record_status,
        )
        if arguments.record_status:
            db.commit()
    if arguments.json_output is not None:
        write_manifest_json(
            report,
            output_path=arguments.json_output,
            settings=settings,
        )
    print(json.dumps(report.as_dict(), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
