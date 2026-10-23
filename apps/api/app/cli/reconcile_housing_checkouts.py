from __future__ import annotations

import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from app.housing.reconciliation import HousingCheckoutReconciler


def utc_now() -> datetime:
    return datetime.now(UTC)


def _load_runtime() -> tuple[
    sessionmaker[Session],
    type[HousingCheckoutReconciler],
]:
    from app.core.config import get_settings

    get_settings()

    from app.core.database import SessionLocal
    from app.housing.reconciliation import HousingCheckoutReconciler

    return SessionLocal, HousingCheckoutReconciler


def run_once() -> int:
    session_factory, reconciler_type = _load_runtime()
    with session_factory() as db:
        return reconciler_type(db).run(utc_now())


def main() -> int:
    try:
        changed = run_once()
    except Exception as exc:  # noqa: BLE001 - the CLI must return a safe failure code
        print(
            f"housing checkout reconciliation failed: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1
    print(f"housing_checkout_pending={changed}")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main
    raise SystemExit(main())
