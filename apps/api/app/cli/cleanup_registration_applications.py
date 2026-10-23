from __future__ import annotations

import argparse
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from app.registration.cleanup_service import RegistrationCleanupService


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("limit must be a positive integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("limit must be a positive integer")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Expire registration applications and clear temporary secrets"
    )
    parser.add_argument("--limit", type=_positive_int, default=500)
    return parser


def _load_runtime() -> tuple[
    Callable[[], Session],
    type[RegistrationCleanupService],
]:
    from app.core.config import get_settings

    get_settings()
    from app.core.database import SessionLocal
    from app.registration.cleanup_service import RegistrationCleanupService

    return SessionLocal, RegistrationCleanupService


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        session_factory, service_type = _load_runtime()
        with session_factory() as db:
            changed = service_type(db).prune(limit=args.limit)
    except Exception as exc:  # noqa: BLE001 - CLI must redact runtime details
        print(
            f"registration cleanup failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    print(changed)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main
    raise SystemExit(main())
