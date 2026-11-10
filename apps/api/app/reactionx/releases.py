"""Restricted download service: caller MUST supply a fresh live authority check.

The callback must re-evaluate effective superadmin AND reactionx.manage on every
call, not a cached role/permission or a capability derived from release_id.
Never hand a FileResponse a source path; stream VerifiedFile.chunks() and close
it even on client disconnect. The private temporary descriptor owns its bytes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Self

from app.reactionx.release_bundle import CHUNK_SIZE, BundleError, verified_bundle

DEFAULT_RELEASE_ROOT = Path("/app/runtime/reactionx-release")


@dataclass(frozen=True)
class ReleaseView:
    available: bool = False
    release_id: str | None = None
    version: str | None = None
    source_commit: str | None = None
    protocol_version: int | None = None
    filename: str | None = None
    sha256: str | None = None
    size: int | None = None
    signature_status: str | None = None
    distribution_status: str = "unavailable"


@dataclass
class VerifiedFile:
    view: ReleaseView
    file: BinaryIO
    _close: Callable[[], None]

    def chunks(self) -> Iterator[bytes]:
        try:
            while chunk := self.file.read(CHUNK_SIZE):
                yield chunk
        finally:
            self.close()

    def close(self) -> None:
        self._close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class ReleaseService:
    def __init__(self, root: Path = DEFAULT_RELEASE_ROOT, *, authorize: Callable[[], None]) -> None:
        self.root = root
        self.authorize = authorize

    def current(self) -> ReleaseView:
        self.authorize()
        with verified_bundle(self.root, allow_unavailable=True) as bundle:
            if bundle is None:
                return ReleaseView()
            return ReleaseView(
                available=True,
                release_id=bundle.bundle_digest,
                version=bundle.manifest.version,
                source_commit=bundle.manifest.source_commit,
                protocol_version=bundle.manifest.protocol_version,
                filename=bundle.manifest.filename,
                sha256=bundle.manifest.sha256,
                size=bundle.manifest.size,
                signature_status=bundle.manifest.signature_status,
                distribution_status="internal_test",
            )

    def open_verified(self, release_id: str) -> VerifiedFile:
        self.authorize()
        context = verified_bundle(self.root)
        bundle = context.__enter__()
        try:
            assert bundle is not None
            if release_id != bundle.bundle_digest:
                raise BundleError("Unknown release identity")
            view = ReleaseView(
                available=True,
                release_id=bundle.bundle_digest,
                version=bundle.manifest.version,
                source_commit=bundle.manifest.source_commit,
                protocol_version=bundle.manifest.protocol_version,
                filename=bundle.manifest.filename,
                sha256=bundle.manifest.sha256,
                size=bundle.manifest.size,
                signature_status=bundle.manifest.signature_status,
                distribution_status="internal_test",
            )

            def close() -> None:
                context.__exit__(None, None, None)

            return VerifiedFile(view, bundle.file, close)
        except BaseException:
            context.__exit__(None, None, None)
            raise
