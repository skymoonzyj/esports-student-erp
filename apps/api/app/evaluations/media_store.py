from __future__ import annotations

import hashlib
import os
import re
import stat
import warnings
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO, Literal
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

from app.evaluations.errors import MediaValidationFailed

_CHUNK_BYTES = 1024 * 1024
_FORMAT_DETAILS: dict[
    str,
    tuple[
        Literal["image/jpeg", "image/png", "image/webp"],
        Literal["jpg", "png", "webp"],
    ],
] = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
_DECLARED_EXTENSIONS = {
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".png": "png",
    ".webp": "webp",
}
_KEY_PATTERN = re.compile(
    r"(?P<prefix>objects|previews)/(?P<first>[0-9a-f]{2})/"
    r"(?P<second>[0-9a-f]{2})/(?P<uuid>[0-9a-f]{8}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.(?P<extension>jpg|png|webp)\Z"
)
_QUARANTINE_OWNERSHIP_PATTERN = re.compile(r"\.owned-[0-9a-f]{32}\Z")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PROC_DESCRIPTOR_ROOT = Path("/proc/self/fd")
_DEV_DESCRIPTOR_ROOT = Path("/dev/fd")
_DESCRIPTOR_ROOT = (
    _PROC_DESCRIPTOR_ROOT
    if _PROC_DESCRIPTOR_ROOT.is_dir()
    else _DEV_DESCRIPTOR_ROOT
    if _DEV_DESCRIPTOR_ROOT.is_dir()
    else None
)
_SECURE_DESCRIPTOR_IO = (
    hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
    and _DESCRIPTOR_ROOT is not None
    and all(
        function in os.supports_dir_fd
        for function in (os.open, os.mkdir, os.stat, os.unlink, os.link, os.rename)
    )
    and os.stat in os.supports_follow_symlinks
    and os.link in os.supports_follow_symlinks
)
_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
)
_OWNERSHIP_SCAN_MAX_ENTRIES = 1_000_000
_STORE_IDENTITY_NAME = ".erp-store-identity"


class _PinnedMediaPath(Path):
    __slots__ = ("_descriptor",)

    def __init__(self, path: Path, descriptor: int | None = None) -> None:
        super().__init__(path)
        self._descriptor = descriptor

    def close(self) -> None:
        descriptor = self._descriptor
        if descriptor is None:
            return
        self._descriptor = None
        os.close(descriptor)

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass


@dataclass(frozen=True)
class ValidatedImage:
    quarantine_path: Path
    sha256: str
    mime_type: Literal["image/jpeg", "image/png", "image/webp"]
    extension: Literal["jpg", "png", "webp"]
    byte_size: int
    width: int
    height: int
    device: int = field(repr=False)
    inode: int = field(repr=False)
    ownership_name: str = field(repr=False)


@dataclass
class _PublishedFile:
    storage_key: str
    parent_descriptor: int | None
    anchor_descriptor: int | None
    filename: str
    anchor_name: str
    device: int
    inode: int


@dataclass
class MediaSettlementBudget:
    visited_entries: int = 0

    def ensure_available(self) -> None:
        if self.visited_entries >= _OWNERSHIP_SCAN_MAX_ENTRIES:
            raise RuntimeError("private media ownership scan limit exceeded")

    def consume(self, count: int) -> None:
        self.visited_entries += count
        if self.visited_entries > _OWNERSHIP_SCAN_MAX_ENTRIES:
            raise RuntimeError("private media ownership scan limit exceeded")


@dataclass(frozen=True)
class StoredImageFiles:
    storage_key: str
    preview_storage_key: str
    created_original: bool
    created_preview: bool
    original_ownership: _PublishedFile | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    preview_ownership: _PublishedFile | None = field(
        default=None,
        repr=False,
        compare=False,
    )


class PrivateMediaStore:
    def __init__(
        self,
        *,
        root: Path,
        quarantine_root: Path,
        max_bytes: int,
        max_pixels: int,
        preview_max_edge: int,
        expected_identity: UUID | None = None,
        read_only: bool = False,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.quarantine_root = quarantine_root.expanduser().resolve()
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self.preview_max_edge = preview_max_edge
        self.expected_identity = expected_identity
        self._read_only = read_only
        self.media_writable, self._root_descriptor = self._prepare_root(
            self.root, read_only=read_only,
        )
        if not self._root_descriptor_is_current(
            self.root,
            self._root_descriptor,
        ):
            self._close_descriptor(self._root_descriptor)
            self._root_descriptor = None
            self.media_writable = False
        (
            self.quarantine_writable,
            self._quarantine_descriptor,
        ) = self._prepare_root(self.quarantine_root, read_only=read_only)
        if not self._root_descriptor_is_current(
            self.quarantine_root,
            self._quarantine_descriptor,
        ):
            self._close_descriptor(self._quarantine_descriptor)
            self._quarantine_descriptor = None
            self.quarantine_writable = False
        self.same_filesystem = self._same_filesystem()
        self.ready = (
            self.media_writable
            and self.quarantine_writable
            and self.same_filesystem
            and self.root != self.quarantine_root
            and self._root_descriptor is not None
            and self._quarantine_descriptor is not None
        )

    @staticmethod
    def _root_descriptor_is_current(root: Path, descriptor: int | None) -> bool:
        if descriptor is None:
            return False
        current_descriptor: int | None = None
        try:
            current_descriptor = os.open(root, _DIRECTORY_OPEN_FLAGS)
            retained = os.fstat(descriptor)
            current = os.fstat(current_descriptor)
            return retained.st_dev == current.st_dev and retained.st_ino == current.st_ino
        except OSError:
            return False
        finally:
            PrivateMediaStore._close_descriptor(current_descriptor)

    def close(self) -> None:
        for attribute in ("_root_descriptor", "_quarantine_descriptor"):
            descriptor = getattr(self, attribute, None)
            if descriptor is None:
                continue
            setattr(self, attribute, None)
            try:
                os.close(descriptor)
            except OSError:
                pass

    def __del__(self) -> None:
        self.close()

    @staticmethod
    def _prepare_root(root: Path, *, read_only: bool = False) -> tuple[bool, int | None]:
        root_descriptor: int | None = None
        probe_descriptor: int | None = None
        probe_name: str | None = None
        prepared = False
        try:
            if not _SECURE_DESCRIPTOR_IO:
                return False, None
            if not read_only:
                root.mkdir(mode=0o700, parents=True, exist_ok=True)
            root_descriptor = os.open(root, _DIRECTORY_OPEN_FLAGS)
            if not read_only:
                os.fchmod(root_descriptor, 0o700)
            root_details = os.fstat(root_descriptor)
            expected_owner = os.geteuid()
            if (
                not stat.S_ISDIR(root_details.st_mode)
                or root_details.st_uid != expected_owner
                or stat.S_IMODE(root_details.st_mode) != 0o700
            ):
                raise PermissionError("private media root ownership is invalid")
            if read_only:
                prepared = True
                return True, root_descriptor
            for _ in range(100):
                candidate = f".write-probe-{uuid4().hex}"
                try:
                    probe_descriptor = os.open(
                        candidate,
                        os.O_WRONLY
                        | os.O_CREAT
                        | os.O_EXCL
                        | getattr(os, "O_CLOEXEC", 0)
                        | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=root_descriptor,
                    )
                except FileExistsError:
                    continue
                probe_name = candidate
                break
            if probe_descriptor is None or probe_name is None:
                raise OSError("could not allocate private media write probe")
            os.fchmod(probe_descriptor, 0o600)
            probe_details = os.fstat(probe_descriptor)
            if (
                not stat.S_ISREG(probe_details.st_mode)
                or probe_details.st_uid != expected_owner
                or stat.S_IMODE(probe_details.st_mode) != 0o600
            ):
                raise PermissionError("private media write probe is invalid")
            os.close(probe_descriptor)
            probe_descriptor = None
            os.unlink(probe_name, dir_fd=root_descriptor)
            probe_name = None
            prepared = True
        except OSError:
            return False, None
        finally:
            PrivateMediaStore._close_descriptor(probe_descriptor)
            if probe_name is not None and root_descriptor is not None:
                try:
                    os.unlink(probe_name, dir_fd=root_descriptor)
                except OSError:
                    pass
            if not prepared:
                PrivateMediaStore._close_descriptor(root_descriptor)
        result = root_descriptor
        root_descriptor = None
        return True, result

    def _same_filesystem(self) -> bool:
        if self._root_descriptor is None or self._quarantine_descriptor is None:
            return False
        try:
            return (
                os.fstat(self._root_descriptor).st_dev
                == os.fstat(self._quarantine_descriptor).st_dev
            )
        except OSError:
            return False

    def _require_ready(self) -> None:
        if not self.ready or self._read_only:
            raise RuntimeError("private media store is not ready")

    def _identity_roots(self) -> tuple[tuple[int, str], tuple[int, str]]:
        if (
            self.expected_identity is None
            or not self.ready
            or self._root_descriptor is None
            or self._quarantine_descriptor is None
            or not self._root_descriptor_is_current(self.root, self._root_descriptor)
            or not self._root_descriptor_is_current(
                self.quarantine_root, self._quarantine_descriptor,
            )
        ):
            raise RuntimeError("private media store identity is unavailable")
        return ((self._root_descriptor, "media"), (self._quarantine_descriptor, "quarantine"))

    def _identity_payload(self, role: str) -> bytes:
        return f"{self.expected_identity}\n{role}\n".encode("ascii")

    def _check_identity_marker(self, descriptor: int, role: str) -> None:
        marker = os.open(_STORE_IDENTITY_NAME, _FILE_OPEN_FLAGS, dir_fd=descriptor)
        try:
            details = os.fstat(marker)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid != os.geteuid()
                or stat.S_IMODE(details.st_mode) != 0o600
                or os.read(marker, 128) != self._identity_payload(role)
            ):
                raise RuntimeError("private media store identity does not match")
        finally:
            os.close(marker)

    def verify_store_identity(self) -> None:
        """Verify both live roots before treating an absent file as deleted."""
        try:
            for descriptor, role in self._identity_roots():
                self._check_identity_marker(descriptor, role)
        except OSError as exc:
            raise RuntimeError("private media store identity is unavailable") from exc

    def initialize_store_identity(self) -> None:
        """Explicit deployment operation; never called by requests or cleanup.

        The operator must first verify these are the authoritative mounted
        volumes. Precheck both markers before creating either; existing IDs
        cannot be replaced. A partially completed initialization can be retried.
        """
        self._require_ready()
        roots = self._identity_roots()
        missing: list[tuple[int, str]] = []
        for descriptor, role in roots:
            try:
                self._check_identity_marker(descriptor, role)
            except FileNotFoundError:
                missing.append((descriptor, role))
        for descriptor, role in missing:
            try:
                marker = os.open(
                    _STORE_IDENTITY_NAME,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=descriptor,
                )
            except FileExistsError:
                self._check_identity_marker(descriptor, role)
                continue
            try:
                payload = self._identity_payload(role)
                if os.write(marker, payload) != len(payload):
                    raise OSError("incomplete private media store identity write")
                os.fsync(marker)
            finally:
                os.close(marker)
            os.fsync(descriptor)
        self.verify_store_identity()

    @staticmethod
    def _descriptor_path(descriptor: int) -> Path:
        if _DESCRIPTOR_ROOT is None:
            raise RuntimeError("secure descriptor paths are unavailable")
        return _DESCRIPTOR_ROOT / str(descriptor)

    @staticmethod
    def _close_descriptor(descriptor: int | None) -> None:
        if descriptor is None:
            return
        try:
            os.close(descriptor)
        except OSError:
            pass

    def _quarantine_name(self, path: Path) -> str:
        try:
            relative = path.relative_to(self.quarantine_root)
        except ValueError as exc:
            raise MediaValidationFailed("decode_failed") from exc
        if len(relative.parts) != 1 or relative.name in {"", ".", ".."}:
            raise MediaValidationFailed("decode_failed")
        return relative.name

    @staticmethod
    def _remove_inode_entries(
        parent_descriptor: int,
        *,
        device: int,
        inode: int,
    ) -> None:
        scan_descriptor: int | None = None
        try:
            scan_descriptor = os.open(
                ".",
                _DIRECTORY_OPEN_FLAGS,
                dir_fd=parent_descriptor,
            )
            names = os.listdir(scan_descriptor)
            for name in names:
                try:
                    details = os.stat(
                        name,
                        dir_fd=scan_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    continue
                if details.st_dev != device or details.st_ino != inode:
                    continue
                try:
                    os.unlink(name, dir_fd=scan_descriptor)
                except FileNotFoundError:
                    pass
        finally:
            PrivateMediaStore._close_descriptor(scan_descriptor)

    def _remove_quarantine_inode(self, *, device: int, inode: int) -> None:
        descriptor = self._quarantine_descriptor
        if descriptor is None:
            raise RuntimeError("private media store is not ready")
        self._remove_inode_entries(
            descriptor,
            device=device,
            inode=inode,
        )

    def _create_quarantine_anchor(
        self,
        path: Path,
        *,
        device: int,
        inode: int,
    ) -> str:
        quarantine_descriptor = self._quarantine_descriptor
        if quarantine_descriptor is None:
            raise RuntimeError("private media store is not ready")
        source_name = self._quarantine_name(path)
        for _ in range(100):
            anchor_name = f".owned-{uuid4().hex}"
            try:
                os.link(
                    source_name,
                    anchor_name,
                    src_dir_fd=quarantine_descriptor,
                    dst_dir_fd=quarantine_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                continue
            details = os.stat(
                anchor_name,
                dir_fd=quarantine_descriptor,
                follow_symlinks=False,
            )
            if details.st_dev == device and details.st_ino == inode:
                return anchor_name
            os.unlink(anchor_name, dir_fd=quarantine_descriptor)
            raise MediaValidationFailed("decode_failed")
        raise RuntimeError("could not allocate private media ownership anchor")

    def _temporary_quarantine_file(
        self,
        *,
        prefix: str,
        suffix: str,
    ) -> tuple[int, Path]:
        quarantine_descriptor = self._quarantine_descriptor
        if quarantine_descriptor is None:
            raise RuntimeError("private media store is not ready")
        for _ in range(100):
            name = f"{prefix}{uuid4().hex}{suffix}"
            try:
                descriptor = os.open(
                    name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=quarantine_descriptor,
                )
            except FileExistsError:
                continue
            path = self.quarantine_root / name
            details = os.fstat(descriptor)
            try:
                os.fchmod(descriptor, 0o600)
            except Exception:
                self._close_descriptor(descriptor)
                self._remove_quarantine_inode(
                    device=details.st_dev,
                    inode=details.st_ino,
                )
                raise
            return descriptor, path
        raise RuntimeError("could not allocate private media temporary file")

    def _quarantine_file(self) -> tuple[int, Path]:
        return self._temporary_quarantine_file(
            prefix="staged-",
            suffix=".image",
        )

    def _open_quarantine_path(self, path: Path) -> int:
        quarantine_descriptor = self._quarantine_descriptor
        if quarantine_descriptor is None:
            raise MediaValidationFailed("decode_failed")
        name = self._quarantine_name(path)
        opened: int | None = None
        try:
            opened = os.open(name, _FILE_OPEN_FLAGS, dir_fd=quarantine_descriptor)
            details = os.fstat(opened)
            if not stat.S_ISREG(details.st_mode):
                raise MediaValidationFailed("decode_failed")
            result = opened
            opened = None
            return result
        finally:
            self._close_descriptor(opened)

    @staticmethod
    def _descriptor_digest(descriptor: int) -> tuple[str, int]:
        digest = hashlib.sha256()
        byte_size = 0
        os.lseek(descriptor, 0, os.SEEK_SET)
        while True:
            chunk = os.read(descriptor, _CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            byte_size += len(chunk)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return digest.hexdigest(), byte_size

    @staticmethod
    def _validate_png_structure(path: Path) -> None:
        def read_exact(source: BinaryIO, size: int) -> bytes:
            value = source.read(size)
            if len(value) != size:
                raise MediaValidationFailed("decode_failed")
            return value

        try:
            with path.open("rb") as source:
                source.seek(0)
                if read_exact(source, len(_PNG_SIGNATURE)) != _PNG_SIGNATURE:
                    raise MediaValidationFailed("decode_failed")
                chunk_index = 0
                saw_idat = False
                while True:
                    chunk_size = int.from_bytes(
                        read_exact(source, 4),
                        byteorder="big",
                    )
                    chunk_type = read_exact(source, 4)
                    if chunk_index == 0 and chunk_type != b"IHDR":
                        raise MediaValidationFailed("decode_failed")
                    if chunk_index > 0 and chunk_type == b"IHDR":
                        raise MediaValidationFailed("decode_failed")

                    checksum = zlib.crc32(chunk_type)
                    remaining = chunk_size
                    while remaining:
                        data = source.read(min(_CHUNK_BYTES, remaining))
                        if not data:
                            raise MediaValidationFailed("decode_failed")
                        checksum = zlib.crc32(data, checksum)
                        remaining -= len(data)
                    stored_checksum = int.from_bytes(
                        read_exact(source, 4),
                        byteorder="big",
                    )
                    if checksum != stored_checksum:
                        raise MediaValidationFailed("decode_failed")

                    if chunk_type == b"IDAT":
                        saw_idat = True
                    if chunk_type == b"IEND":
                        if chunk_size != 0 or not saw_idat or source.read(1):
                            raise MediaValidationFailed("decode_failed")
                        return
                    chunk_index += 1
        except OSError as exc:
            raise MediaValidationFailed("decode_failed") from exc

    def stage(
        self,
        stream: BinaryIO,
        *,
        declared_filename: str,
        declared_mime_type: str | None = None,
    ) -> ValidatedImage:
        self._require_ready()
        declared_extension = _DECLARED_EXTENSIONS.get(Path(declared_filename).suffix.casefold())
        if declared_extension is None:
            raise MediaValidationFailed("type_mismatch")

        descriptor, quarantine_path = self._quarantine_file()
        staged_details = os.fstat(descriptor)
        digest = hashlib.sha256()
        byte_size = 0
        try:
            try:
                staged_file = os.fdopen(descriptor, "wb")
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            with staged_file as staged:
                while True:
                    remaining_with_sentinel = self.max_bytes - byte_size + 1
                    try:
                        chunk = stream.read(min(_CHUNK_BYTES, remaining_with_sentinel))
                    except Exception as exc:
                        raise MediaValidationFailed("stream_failed") from exc
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise MediaValidationFailed("stream_failed")
                    byte_size += len(chunk)
                    if byte_size > self.max_bytes:
                        raise MediaValidationFailed("byte_limit")
                    digest.update(chunk)
                    staged.write(chunk)
            if byte_size == 0:
                raise MediaValidationFailed("decode_failed")

            validation_descriptor = self._open_quarantine_path(quarantine_path)
            try:
                validation_details = os.fstat(validation_descriptor)
                if (
                    validation_details.st_dev != staged_details.st_dev
                    or validation_details.st_ino != staged_details.st_ino
                ):
                    raise MediaValidationFailed("decode_failed")
                stored_digest, stored_size = self._descriptor_digest(validation_descriptor)
                if stored_digest != digest.hexdigest() or stored_size != byte_size:
                    raise MediaValidationFailed("decode_failed")
                descriptor_path = self._descriptor_path(validation_descriptor)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    os.lseek(validation_descriptor, 0, os.SEEK_SET)
                    with Image.open(descriptor_path) as opened:
                        image_format = opened.format
                        width, height = opened.size
                        if width <= 0 or height <= 0:
                            raise MediaValidationFailed("decode_failed")
                        if width * height > self.max_pixels:
                            raise MediaValidationFailed("pixel_limit")
                        details = _FORMAT_DETAILS.get(image_format or "")
                        if details is None or details[1] != declared_extension:
                            raise MediaValidationFailed("type_mismatch")
                        if (
                            declared_mime_type is not None
                            and declared_mime_type != details[0]
                        ):
                            raise MediaValidationFailed("declared_mime_mismatch")
                        opened.verify()
                    if image_format == "PNG":
                        os.lseek(validation_descriptor, 0, os.SEEK_SET)
                        self._validate_png_structure(descriptor_path)
                    os.lseek(validation_descriptor, 0, os.SEEK_SET)
                    with Image.open(descriptor_path) as reopened:
                        oriented = ImageOps.exif_transpose(reopened)
                        try:
                            oriented.load()
                        finally:
                            if oriented is not reopened:
                                oriented.close()
            except MediaValidationFailed:
                raise
            except Image.DecompressionBombWarning as exc:
                raise MediaValidationFailed("pixel_limit") from exc
            except (
                Image.DecompressionBombError,
                UnidentifiedImageError,
                OSError,
                SyntaxError,
                ValueError,
            ) as exc:
                raise MediaValidationFailed("decode_failed") from exc
            finally:
                self._close_descriptor(validation_descriptor)

            ownership_name = self._create_quarantine_anchor(
                quarantine_path,
                device=staged_details.st_dev,
                inode=staged_details.st_ino,
            )
            mime_type, extension = _FORMAT_DETAILS[image_format or ""]
            return ValidatedImage(
                quarantine_path=quarantine_path,
                sha256=digest.hexdigest(),
                mime_type=mime_type,
                extension=extension,
                byte_size=byte_size,
                width=width,
                height=height,
                device=staged_details.st_dev,
                inode=staged_details.st_ino,
                ownership_name=ownership_name,
            )
        except Exception:
            self._remove_quarantine_inode(
                device=staged_details.st_dev,
                inode=staged_details.st_ino,
            )
            raise

    def quarantine_storage_key(self, image: ValidatedImage) -> str:
        key = image.ownership_name
        if _QUARANTINE_OWNERSHIP_PATTERN.fullmatch(key) is None:
            raise MediaValidationFailed("decode_failed")
        return key

    def resume_staged(
        self,
        quarantine_storage_key: str,
        *,
        sha256: str,
        mime_type: Literal["image/jpeg", "image/png", "image/webp"],
        extension: Literal["jpg", "png", "webp"],
        byte_size: int,
        width: int,
        height: int,
    ) -> ValidatedImage:
        if (
            _QUARANTINE_OWNERSHIP_PATTERN.fullmatch(quarantine_storage_key) is None
            or (mime_type, extension) not in _FORMAT_DETAILS.values()
            or byte_size < 1
            or byte_size > self.max_bytes
            or width < 1
            or height < 1
            or width * height > self.max_pixels
        ):
            raise MediaValidationFailed("decode_failed")
        quarantine_path = self.quarantine_root / quarantine_storage_key
        descriptor: int | None = None
        try:
            descriptor = self._open_quarantine_path(quarantine_path)
            details = os.fstat(descriptor)
            stored_digest, stored_size = self._descriptor_digest(descriptor)
            if stored_digest != sha256 or stored_size != byte_size:
                raise MediaValidationFailed("decode_failed")
            return ValidatedImage(
                quarantine_path=quarantine_path,
                sha256=sha256,
                mime_type=mime_type,
                extension=extension,
                byte_size=byte_size,
                width=width,
                height=height,
                device=details.st_dev,
                inode=details.st_ino,
                ownership_name=quarantine_storage_key,
            )
        except OSError as exc:
            raise MediaValidationFailed("decode_failed") from exc
        finally:
            self._close_descriptor(descriptor)

    def discard_staged_key(self, quarantine_storage_key: str) -> None:
        self.verify_store_identity()
        self._require_ready()
        if _QUARANTINE_OWNERSHIP_PATTERN.fullmatch(quarantine_storage_key) is None:
            raise MediaValidationFailed("decode_failed")
        descriptor: int | None = None
        try:
            descriptor = self._open_quarantine_path(
                self.quarantine_root / quarantine_storage_key
            )
            details = os.fstat(descriptor)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise MediaValidationFailed("decode_failed") from exc
        finally:
            self._close_descriptor(descriptor)
        self._remove_quarantine_inode(device=details.st_dev, inode=details.st_ino)

    @staticmethod
    def _keys(
        blob_id: UUID,
        extension: Literal["jpg", "png", "webp"],
    ) -> tuple[str, str]:
        shard = blob_id.hex
        identity = str(blob_id)
        return (
            f"objects/{shard[:2]}/{shard[2:4]}/{identity}.{extension}",
            f"previews/{shard[:2]}/{shard[2:4]}/{identity}.webp",
        )

    @staticmethod
    def _key_parts(storage_key: str) -> tuple[str, str, str, str]:
        match = _KEY_PATTERN.fullmatch(storage_key)
        if match is None:
            raise ValueError("invalid private media storage key")
        identity = UUID(match["uuid"])
        if match["first"] != identity.hex[:2] or match["second"] != identity.hex[2:4]:
            raise ValueError("invalid private media storage key")
        if match["prefix"] == "previews" and match["extension"] != "webp":
            raise ValueError("invalid private media storage key")
        return (
            match["prefix"],
            match["first"],
            match["second"],
            f"{match['uuid']}.{match['extension']}",
        )

    def _parent_descriptor(
        self,
        storage_key: str,
        *,
        create: bool,
    ) -> tuple[int, str]:
        root_descriptor = self._root_descriptor
        if root_descriptor is None:
            raise RuntimeError("private media store is not ready")
        prefix, first, second, filename = self._key_parts(storage_key)
        descriptor = os.dup(root_descriptor)
        try:
            for component in (prefix, first, second):
                if create:
                    try:
                        os.mkdir(component, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(component, _DIRECTORY_OPEN_FLAGS, dir_fd=descriptor)
                try:
                    os.fchmod(child, 0o700)
                except Exception:
                    self._close_descriptor(child)
                    raise
                self._close_descriptor(descriptor)
                descriptor = child
            return descriptor, filename
        except Exception:
            self._close_descriptor(descriptor)
            raise

    def _private_parent(self, path: Path) -> None:
        try:
            storage_key = path.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError("invalid private media storage key") from exc
        descriptor, _ = self._parent_descriptor(storage_key, create=True)
        self._close_descriptor(descriptor)

    def _preview_temp(
        self,
        image: ValidatedImage,
        *,
        source_descriptor: int | None = None,
    ) -> tuple[int, Path]:
        descriptor, preview_path = self._temporary_quarantine_file(
            prefix="preview-",
            suffix=".webp",
        )
        preview_details = os.fstat(descriptor)
        opened_source = source_descriptor
        owns_source = False
        try:
            if opened_source is None:
                opened_source = self._open_quarantine_path(image.quarantine_path)
                owns_source = True
            os.lseek(opened_source, 0, os.SEEK_SET)
            with (
                os.fdopen(os.dup(opened_source), "rb") as source,
                Image.open(source) as opened,
            ):
                oriented = ImageOps.exif_transpose(opened)
                try:
                    has_alpha = oriented.mode in {"RGBA", "LA"} or (
                        oriented.mode == "P" and "transparency" in oriented.info
                    )
                    clean = oriented.convert("RGBA" if has_alpha else "RGB")
                    try:
                        clean.info.clear()
                        clean.thumbnail(
                            (self.preview_max_edge, self.preview_max_edge),
                            Image.Resampling.LANCZOS,
                        )
                        with os.fdopen(os.dup(descriptor), "wb") as output:
                            clean.save(
                                output,
                                format="WEBP",
                                quality=88,
                                method=6,
                                exif=b"",
                                icc_profile=b"",
                                xmp=b"",
                            )
                            output.flush()
                    finally:
                        clean.close()
                finally:
                    if oriented is not opened:
                        oriented.close()
            os.fchmod(descriptor, 0o600)
            os.lseek(descriptor, 0, os.SEEK_SET)
            return descriptor, preview_path
        except Exception:
            self._close_descriptor(descriptor)
            self._remove_quarantine_inode(
                device=preview_details.st_dev,
                inode=preview_details.st_ino,
            )
            raise
        finally:
            if owns_source:
                self._close_descriptor(opened_source)

    @staticmethod
    def _copy_descriptor(source: int, destination: int) -> None:
        os.lseek(source, 0, os.SEEK_SET)
        while True:
            chunk = os.read(source, _CHUNK_BYTES)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(destination, view)
                view = view[written:]

    def _publish_descriptor(self, source: int, storage_key: str) -> _PublishedFile:
        parent_descriptor, filename = self._parent_descriptor(
            storage_key,
            create=False,
        )
        root_descriptor = self._root_descriptor
        if root_descriptor is None:
            self._close_descriptor(parent_descriptor)
            raise RuntimeError("private media store is not ready")
        try:
            anchor_descriptor = os.dup(root_descriptor)
        except Exception:
            self._close_descriptor(parent_descriptor)
            raise
        anchor_name = f".pending-{uuid4().hex}"
        temporary_descriptor: int | None = None
        details: os.stat_result | None = None
        try:
            if not self._parent_is_current(storage_key, parent_descriptor):
                raise RuntimeError("private media ownership was lost")
            temporary_descriptor = os.open(
                anchor_name,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=anchor_descriptor,
            )
            details = os.fstat(temporary_descriptor)
            self._copy_descriptor(source, temporary_descriptor)
            os.fchmod(temporary_descriptor, 0o600)
            self._close_descriptor(temporary_descriptor)
            temporary_descriptor = None
            try:
                os.link(
                    anchor_name,
                    filename,
                    src_dir_fd=anchor_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError as exc:
                raise RuntimeError("private media object already exists") from exc
            ownership = _PublishedFile(
                storage_key=storage_key,
                parent_descriptor=parent_descriptor,
                anchor_descriptor=anchor_descriptor,
                filename=filename,
                anchor_name=anchor_name,
                device=details.st_dev,
                inode=details.st_ino,
            )
            if not self._parent_is_current(
                storage_key,
                parent_descriptor,
            ) or not self._entry_owned(
                ownership,
                filename,
                parent_descriptor=parent_descriptor,
            ):
                raise RuntimeError("private media ownership was lost")
            parent_descriptor = -1
            anchor_descriptor = -1
            return ownership
        except Exception:
            if details is not None:
                self._unlink_entry_if_owned(
                    parent_descriptor,
                    name=filename,
                    device=details.st_dev,
                    inode=details.st_ino,
                )
                self._unlink_entry_if_owned(
                    anchor_descriptor,
                    name=anchor_name,
                    device=details.st_dev,
                    inode=details.st_ino,
                )
            raise
        finally:
            self._close_descriptor(temporary_descriptor)
            if parent_descriptor >= 0:
                self._close_descriptor(parent_descriptor)
            if anchor_descriptor >= 0:
                self._close_descriptor(anchor_descriptor)

    @staticmethod
    def _unlink_entry_if_owned(
        parent_descriptor: int,
        *,
        name: str,
        device: int,
        inode: int,
    ) -> bool:
        settling_name = f".settling-{uuid4().hex}"
        try:
            os.rename(
                name,
                settling_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
        except FileNotFoundError:
            return False
        details = os.stat(
            settling_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if details.st_dev == device and details.st_ino == inode:
            os.unlink(settling_name, dir_fd=parent_descriptor)
            return True
        try:
            os.link(
                settling_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError:
            return False
        os.unlink(settling_name, dir_fd=parent_descriptor)
        return False

    @staticmethod
    def _entry_owned(
        ownership: _PublishedFile,
        name: str,
        *,
        parent_descriptor: int,
    ) -> bool:
        try:
            details = os.stat(
                name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return False
        return details.st_dev == ownership.device and details.st_ino == ownership.inode

    def _parent_is_current(
        self,
        storage_key: str,
        parent_descriptor: int,
    ) -> bool:
        try:
            current_parent, _ = self._parent_descriptor(
                storage_key,
                create=False,
            )
        except (OSError, RuntimeError, ValueError):
            return False
        try:
            retained_details = os.fstat(parent_descriptor)
            current_details = os.fstat(current_parent)
            return (
                retained_details.st_dev == current_details.st_dev
                and retained_details.st_ino == current_details.st_ino
            )
        finally:
            self._close_descriptor(current_parent)

    @staticmethod
    def _open_descendant_directory(
        root_descriptor: int,
        components: tuple[str, ...],
    ) -> int:
        descriptor = os.dup(root_descriptor)
        try:
            for component in components:
                child = os.open(
                    component,
                    _DIRECTORY_OPEN_FLAGS,
                    dir_fd=descriptor,
                )
                os.close(descriptor)
                descriptor = child
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _descendant_is_current(
        self,
        root_descriptor: int,
        components: tuple[str, ...],
        retained_descriptor: int,
    ) -> bool:
        try:
            current_descriptor = self._open_descendant_directory(
                root_descriptor,
                components,
            )
        except (OSError, RuntimeError):
            return False
        try:
            retained_details = os.fstat(retained_descriptor)
            current_details = os.fstat(current_descriptor)
            return (
                retained_details.st_dev == current_details.st_dev
                and retained_details.st_ino == current_details.st_ino
            )
        finally:
            self._close_descriptor(current_descriptor)

    def _remove_owned_links(
        self,
        ownership: _PublishedFile,
        budget: MediaSettlementBudget | None = None,
    ) -> None:
        active_budget = budget or MediaSettlementBudget()
        root_descriptor = ownership.anchor_descriptor
        if root_descriptor is None:
            return
        parent_descriptor = ownership.parent_descriptor
        if parent_descriptor is not None:
            self._unlink_entry_if_owned(
                parent_descriptor,
                name=ownership.filename,
                device=ownership.device,
                inode=ownership.inode,
            )
        retry_scan = True
        while retry_scan:
            retry_scan = False
            pending_directories: list[tuple[str, ...]] = [()]
            while pending_directories:
                components = pending_directories.pop()
                try:
                    descriptor = self._open_descendant_directory(
                        root_descriptor,
                        components,
                    )
                except (FileNotFoundError, NotADirectoryError):
                    continue
                try:
                    if not self._descendant_is_current(
                        root_descriptor,
                        components,
                        descriptor,
                    ):
                        continue
                    active_budget.ensure_available()
                    names = os.listdir(descriptor)
                    active_budget.consume(len(names))
                    for name in names:
                        try:
                            details = os.stat(
                                name,
                                dir_fd=descriptor,
                                follow_symlinks=False,
                            )
                        except FileNotFoundError:
                            continue
                        if (
                            not components
                            and name == ownership.anchor_name
                            and details.st_dev == ownership.device
                            and details.st_ino == ownership.inode
                        ):
                            continue
                        if details.st_dev == ownership.device and details.st_ino == ownership.inode:
                            if not self._descendant_is_current(
                                root_descriptor,
                                components,
                                descriptor,
                            ):
                                continue
                            if not self._unlink_entry_if_owned(
                                descriptor,
                                name=name,
                                device=ownership.device,
                                inode=ownership.inode,
                            ):
                                retry_scan = True
                            continue
                        if not stat.S_ISDIR(details.st_mode):
                            continue
                        if details.st_dev != ownership.device:
                            continue
                        pending_directories.append((*components, name))
                finally:
                    self._close_descriptor(descriptor)
        try:
            anchor_details = os.stat(
                ownership.anchor_name,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if (
            anchor_details.st_dev == ownership.device
            and anchor_details.st_ino == ownership.inode
            and anchor_details.st_nlink > 1
        ):
            raise RuntimeError("private media ownership links remain")
        self._unlink_entry_if_owned(
            root_descriptor,
            name=ownership.anchor_name,
            device=ownership.device,
            inode=ownership.inode,
        )

    def _close_ownership_descriptors(self, ownership: _PublishedFile) -> None:
        self._close_descriptor(ownership.parent_descriptor)
        self._close_descriptor(ownership.anchor_descriptor)
        ownership.parent_descriptor = None
        ownership.anchor_descriptor = None

    def _remove_owned_file(
        self,
        ownership: _PublishedFile,
        budget: MediaSettlementBudget | None = None,
    ) -> None:
        self._remove_owned_links(ownership, budget)
        self._close_ownership_descriptors(ownership)

    @staticmethod
    def _settle_ownerships(
        files: StoredImageFiles,
        settle: Callable[[_PublishedFile, MediaSettlementBudget | None], None],
        budget: MediaSettlementBudget,
    ) -> None:
        failed: list[_PublishedFile] = []
        for ownership in (files.original_ownership, files.preview_ownership):
            if ownership is None:
                continue
            try:
                settle(ownership, budget)
            except Exception:  # noqa: BLE001 - settle every ownership first
                failed.append(ownership)
        first_error: Exception | None = None
        for ownership in failed:
            try:
                settle(ownership, budget)
            except Exception as exc:  # noqa: BLE001 - retain retryable ownership
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def _validate_owned_file(self, ownership: _PublishedFile) -> None:
        parent_descriptor = ownership.parent_descriptor
        anchor_descriptor = ownership.anchor_descriptor
        if parent_descriptor is None or anchor_descriptor is None:
            raise RuntimeError("private media ownership was lost")
        if not self._parent_is_current(
            ownership.storage_key,
            parent_descriptor,
        ):
            raise RuntimeError("private media ownership was lost")
        if (
            not self._entry_owned(
                ownership,
                ownership.filename,
                parent_descriptor=parent_descriptor,
            )
            or not self._entry_owned(
                ownership,
                ownership.anchor_name,
                parent_descriptor=anchor_descriptor,
            )
            or not self._parent_is_current(
                ownership.storage_key,
                parent_descriptor,
            )
        ):
            raise RuntimeError("private media ownership was lost")

    def finalize(self, files: StoredImageFiles) -> None:
        ownerships = [
            ownership
            for ownership in (files.original_ownership, files.preview_ownership)
            if ownership is not None
        ]
        for ownership in ownerships:
            self._validate_owned_file(ownership)

    def _release_owned_file(
        self,
        ownership: _PublishedFile,
        budget: MediaSettlementBudget | None = None,
    ) -> None:
        active_budget = budget or MediaSettlementBudget()
        anchor_descriptor = ownership.anchor_descriptor
        if anchor_descriptor is not None:
            retry_scan = True
            while retry_scan:
                retry_scan = False
                active_budget.ensure_available()
                names = os.listdir(anchor_descriptor)
                active_budget.consume(len(names))
                for name in names:
                    if name != ownership.anchor_name and not name.startswith(".settling-"):
                        continue
                    try:
                        details = os.stat(
                            name,
                            dir_fd=anchor_descriptor,
                            follow_symlinks=False,
                        )
                    except FileNotFoundError:
                        continue
                    if details.st_dev != ownership.device or details.st_ino != ownership.inode:
                        continue
                    if not self._unlink_entry_if_owned(
                        anchor_descriptor,
                        name=name,
                        device=ownership.device,
                        inode=ownership.inode,
                    ):
                        retry_scan = True
        self._close_ownership_descriptors(ownership)

    def release(
        self,
        files: StoredImageFiles,
        *,
        budget: MediaSettlementBudget | None = None,
    ) -> None:
        self._settle_ownerships(
            files,
            self._release_owned_file,
            budget or MediaSettlementBudget(),
        )

    def _validated_source_descriptor(self, image: ValidatedImage) -> int:
        descriptor = self._open_quarantine_path(image.quarantine_path)
        try:
            details = os.fstat(descriptor)
            if details.st_dev != image.device or details.st_ino != image.inode:
                raise MediaValidationFailed("decode_failed")
            digest, byte_size = self._descriptor_digest(descriptor)
            if digest != image.sha256 or byte_size != image.byte_size:
                raise MediaValidationFailed("decode_failed")
            return descriptor
        except Exception:
            self._close_descriptor(descriptor)
            raise

    def persist(self, image: ValidatedImage, *, blob_id: UUID) -> StoredImageFiles:
        self._require_ready()
        storage_key = ""
        preview_storage_key = ""
        source_descriptor: int | None = None
        preview_descriptor: int | None = None
        preview_details: os.stat_result | None = None
        original_ownership: _PublishedFile | None = None
        preview_ownership: _PublishedFile | None = None
        created_original = False
        created_preview = False
        staged_discarded = False
        try:
            source_descriptor = self._validated_source_descriptor(image)
            storage_key, preview_storage_key = self._keys(blob_id, image.extension)
            original_path = self.root / storage_key
            preview_path = self.root / preview_storage_key
            self._private_parent(original_path)
            self._private_parent(preview_path)

            preview_descriptor, _ = self._preview_temp(
                image,
                source_descriptor=source_descriptor,
            )
            preview_details = os.fstat(preview_descriptor)
            original_ownership = self._publish_descriptor(
                source_descriptor,
                storage_key,
            )
            created_original = True
            preview_ownership = self._publish_descriptor(
                preview_descriptor,
                preview_storage_key,
            )
            created_preview = True
            self.discard_staged(image)
            staged_discarded = True
            self._close_descriptor(preview_descriptor)
            preview_descriptor = None
            if preview_details is not None:
                self._remove_quarantine_inode(
                    device=preview_details.st_dev,
                    inode=preview_details.st_ino,
                )
                preview_details = None
        except Exception:
            cleanup_error: Exception | None = None
            if original_ownership is not None or preview_ownership is not None:
                try:
                    self._settle_ownerships(
                        StoredImageFiles(
                            storage_key=storage_key,
                            preview_storage_key=preview_storage_key,
                            created_original=created_original,
                            created_preview=created_preview,
                            original_ownership=original_ownership,
                            preview_ownership=preview_ownership,
                        ),
                        self._remove_owned_file,
                        MediaSettlementBudget(),
                    )
                except Exception as exc:  # noqa: BLE001 - finish all cleanup
                    cleanup_error = exc
            if not staged_discarded:
                try:
                    self.discard_staged(image)
                except Exception as exc:  # noqa: BLE001 - finish all cleanup
                    if cleanup_error is None:
                        cleanup_error = exc
            if preview_details is not None:
                self._close_descriptor(preview_descriptor)
                preview_descriptor = None
                try:
                    self._remove_quarantine_inode(
                        device=preview_details.st_dev,
                        inode=preview_details.st_ino,
                    )
                except Exception as exc:  # noqa: BLE001 - finish all cleanup
                    if cleanup_error is None:
                        cleanup_error = exc
            if cleanup_error is not None:
                raise cleanup_error
            raise
        finally:
            self._close_descriptor(source_descriptor)
            self._close_descriptor(preview_descriptor)
        return StoredImageFiles(
            storage_key=storage_key,
            preview_storage_key=preview_storage_key,
            created_original=created_original,
            created_preview=created_preview,
            original_ownership=original_ownership,
            preview_ownership=preview_ownership,
        )

    def resolve(self, storage_key: str) -> Path:
        return self.open_protected(storage_key)

    def open_protected(self, storage_key: str) -> Path:
        descriptor: int | None = None
        parent_descriptor: int | None = None
        try:
            parent_descriptor, filename = self._parent_descriptor(
                storage_key,
                create=False,
            )
            descriptor = os.open(filename, _FILE_OPEN_FLAGS, dir_fd=parent_descriptor)
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or not self._parent_is_current(
                storage_key,
                parent_descriptor,
            ):
                raise ValueError("invalid private media storage key")
            pinned = _PinnedMediaPath(self._descriptor_path(descriptor), descriptor)
            descriptor = None
            return pinned
        except (OSError, RuntimeError) as exc:
            raise ValueError("invalid private media storage key") from exc
        finally:
            self._close_descriptor(descriptor)
            self._close_descriptor(parent_descriptor)

    def remove_if_created(
        self,
        files: StoredImageFiles,
        *,
        budget: MediaSettlementBudget | None = None,
    ) -> None:
        self._settle_ownerships(
            files,
            self._remove_owned_file,
            budget or MediaSettlementBudget(),
        )

    def discard_staged(self, image: ValidatedImage) -> None:
        self._remove_quarantine_inode(device=image.device, inode=image.inode)


def main() -> int:
    import argparse

    from app.core.config import get_settings

    parser = argparse.ArgumentParser(description="Explicit private media volume identity gate")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--initialize-store-identity", action="store_true")
    action.add_argument("--verify-store-identity", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    media_root, quarantine_root = settings.evaluation_media_paths()
    store = PrivateMediaStore(
        root=media_root,
        quarantine_root=quarantine_root,
        max_bytes=settings.evaluation_media_max_bytes,
        max_pixels=settings.evaluation_media_max_pixels,
        preview_max_edge=settings.evaluation_media_preview_max_edge,
        expected_identity=settings.evaluation_media_store_id,
        read_only=args.verify_store_identity,
    )
    try:
        if args.initialize_store_identity:
            store.initialize_store_identity()
        else:
            store.verify_store_identity()
    except (OSError, RuntimeError):
        parser.exit(1, "private media store identity verification failed\n")
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
