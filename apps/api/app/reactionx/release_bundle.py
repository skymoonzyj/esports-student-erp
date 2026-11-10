"""Portable, bounded release validation. Never executes the supplied installer.

Unsigned means no embedded Authenticode certificate table, NOT trusted/safe.
Signed distribution is deliberately unavailable until a trusted verifier exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import struct
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

FILENAME = "ReactionXConnectorSetup.exe"
MANIFEST = "manifest.json"
MAX_MANIFEST_SIZE = 16 * 1024
MAX_PACKAGE_SIZE = 512 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024
FIELDS = {
    "schema_version",
    "protocol_version",
    "version",
    "source_commit",
    "filename",
    "sha256",
    "size",
    "signature_status",
}


class BundleError(ValueError):
    """Untrusted or unavailable release input. Messages never include private paths."""


@dataclass(frozen=True)
class BundleManifest:
    schema_version: int
    protocol_version: int
    version: str
    source_commit: str
    filename: str
    sha256: str
    size: int
    signature_status: str


@dataclass
class VerifiedBundle:
    manifest: BundleManifest
    raw_manifest: bytes
    file: BinaryIO
    bundle_digest: str


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise BundleError("Duplicate manifest key")
        result[key] = value
    return result


def parse_manifest(raw: bytes) -> BundleManifest:
    if not raw or len(raw) > MAX_MANIFEST_SIZE:
        raise BundleError("Manifest size outside limits")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise BundleError("Invalid manifest JSON") from exc
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise BundleError("Manifest fields do not match schema")
    for key in ("schema_version", "protocol_version"):
        if type(value[key]) is not int or value[key] != 1:
            raise BundleError("Unsupported release protocol or schema")
    patterns = {
        "version": r"(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})\.(?:0|[1-9][0-9]{0,4})",
        "source_commit": r"(?:[0-9a-f]{40}|[0-9a-f]{64})",
        "sha256": r"[0-9a-f]{64}",
    }
    for key, pattern in patterns.items():
        if not isinstance(value[key], str) or re.fullmatch(pattern, value[key]) is None:
            raise BundleError("Invalid release metadata")
    if value["filename"] != FILENAME or value["signature_status"] not in ("unsigned", "signed"):
        raise BundleError("Unsupported filename or signature declaration")
    if type(value["size"]) is not int or not 0 < value["size"] <= MAX_PACKAGE_SIZE:
        raise BundleError("Package size outside limits")
    return BundleManifest(**value)


@contextmanager
def open_directory(root: Path) -> Iterator[int]:
    """Walk every component descriptor-relatively; reject links, including parents."""
    absolute = Path(os.path.abspath(root))
    descriptor = os.open(absolute.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in absolute.parts[1:]:
            next_fd = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = next_fd
        yield descriptor
    finally:
        os.close(descriptor)


def _open_regular(directory: int, filename: str, limit: int) -> BinaryIO:
    descriptor = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= limit:
        os.close(descriptor)
        raise BundleError("Release file is not a bounded regular file")
    return os.fdopen(descriptor, "rb")


def verify_unsigned_pe(file: BinaryIO, declared: str) -> None:
    """Read Windows PE headers only, not a homegrown cryptographic verifier.

    No signature claim can pass this gate. A certificate-table-bearing PE cannot
    be mislabeled unsigned. Catalog signing is not checked on this non-Windows
    host; unsigned is specifically the embedded-signature state.
    """
    if declared != "unsigned":
        raise BundleError("Trusted Authenticode verification unavailable; signed claim rejected")
    file.seek(0)
    dos = file.read(64)
    if len(dos) != 64 or dos[:2] != b"MZ":
        raise BundleError("Not a Windows PE executable")
    offset = struct.unpack_from("<I", dos, 60)[0]
    if not 64 <= offset <= 1024 * 1024:
        raise BundleError("Invalid PE header offset")
    file.seek(offset)
    header = file.read(24)
    if len(header) != 24 or header[:4] != b"PE\0\0":
        raise BundleError("Invalid PE signature")
    optional_size = struct.unpack_from("<H", header, 20)[0]
    if not 144 <= optional_size <= 4096:
        raise BundleError("Invalid PE optional header")
    optional = file.read(optional_size)
    if len(optional) != optional_size:
        raise BundleError("Truncated PE header")
    magic = struct.unpack_from("<H", optional, 0)[0]
    directories = {0x10B: 96, 0x20B: 112}.get(magic)
    if directories is None or len(optional) < directories + 40:
        raise BundleError("Unsupported PE optional header")
    count = struct.unpack_from("<I", optional, directories - 4)[0]
    if count < 5 or count > 16 or len(optional) < directories + count * 8:
        raise BundleError("Invalid PE data directory inventory")
    certificate_offset, certificate_size = struct.unpack_from("<II", optional, directories + 32)
    if certificate_offset != 0 or certificate_size != 0:
        raise BundleError("Embedded certificate conflicts with unsigned declaration")
    file.seek(0)


@contextmanager
def verified_bundle(
    root: Path, *, allow_unavailable: bool = False
) -> Iterator[VerifiedBundle | None]:
    """Hash private frozen bytes and serve those same bytes, never a mutable path."""
    try:
        with open_directory(root) as directory:
            names = set(os.listdir(directory))
            if not names and allow_unavailable:
                yield None
                return
            if names != {MANIFEST, FILENAME}:
                raise BundleError(
                    "Release bundle must contain exactly manifest and fixed installer"
                )
            with _open_regular(directory, MANIFEST, MAX_MANIFEST_SIZE) as stream:
                raw = stream.read(MAX_MANIFEST_SIZE + 1)
            manifest = parse_manifest(raw)
            with (
                _open_regular(directory, FILENAME, MAX_PACKAGE_SIZE) as source,
                tempfile.TemporaryFile(mode="w+b") as frozen,
            ):
                digest = hashlib.sha256()
                total = 0
                while chunk := source.read(CHUNK_SIZE):
                    total += len(chunk)
                    if total > manifest.size:
                        raise BundleError("Package size mismatch")
                    digest.update(chunk)
                    frozen.write(chunk)
                if total != manifest.size or digest.hexdigest() != manifest.sha256:
                    raise BundleError("Package digest or size mismatch")
                frozen.flush()
                verify_unsigned_pe(frozen, manifest.signature_status)
                identity = hashlib.sha256(raw + bytes.fromhex(manifest.sha256)).hexdigest()
                yield VerifiedBundle(manifest, raw, frozen, identity)
    except FileNotFoundError as exc:
        # Missing package/manifest in an existing nonempty root is never absent.
        if allow_unavailable and not root.exists():
            yield None
            return
        raise BundleError("Release bundle unavailable or changed") from exc
    except OSError as exc:
        raise BundleError("Release bundle cannot be read safely") from exc


def freeze_bundle(source: Path, destination: Path) -> str:
    """Copy only validated, pinned bytes into a newly created private directory."""
    if destination.exists() or destination.is_symlink():
        raise BundleError("Frozen bundle destination must not exist")
    with verified_bundle(source) as bundle:
        assert bundle is not None
        destination.mkdir(mode=0o755, parents=False)
        (destination / MANIFEST).write_bytes(bundle.raw_manifest)
        with (destination / FILENAME).open("xb") as output:
            while chunk := bundle.file.read(CHUNK_SIZE):
                output.write(chunk)
        for name in (MANIFEST, FILENAME):
            (destination / name).chmod(0o444)
        with verified_bundle(destination) as copied:
            assert copied is not None
            if copied.bundle_digest != bundle.bundle_digest:
                raise BundleError("Frozen bundle verification mismatch")
        return bundle.bundle_digest


def bundle_digest(root: Path, expected: str | None = None) -> str:
    with verified_bundle(root) as bundle:
        assert bundle is not None
        if expected is not None and bundle.bundle_digest != expected:
            raise BundleError("Transferred bundle digest mismatch")
        return bundle.bundle_digest
