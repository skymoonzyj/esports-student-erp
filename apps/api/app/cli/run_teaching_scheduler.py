from __future__ import annotations

import argparse
import logging
import time
from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.evaluations.upload_cleanup import cleanup_expired_uploads
from app.operations.heartbeat import (
    TEACHING_SCHEDULER_SERVICE_KEY,
    record_service_heartbeat,
)
from app.teaching.scheduler import reconcile_due_cohorts

logger = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


def _interval(value: str) -> int:
    parsed = int(value)
    if not 5 <= parsed <= 3600:
        raise argparse.ArgumentTypeError("interval must be between 5 and 3600 seconds")
    return parsed


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run teaching cohort state reconciliation")
    parser.add_argument("--once", action="store_true", help="run one iteration and exit")
    parser.add_argument("--interval", type=_interval, help="polling interval in seconds")
    return parser


def run_once() -> int:
    with SessionLocal() as db:
        try:
            changed = reconcile_due_cohorts(db, now=utc_now())
            cleanup_expired_uploads(db, settings=get_settings(), now=utc_now())
        except Exception as exc:
            try:
                record_service_heartbeat(
                    db,
                    service_key=TEACHING_SCHEDULER_SERVICE_KEY,
                    succeeded=False,
                    occurred_at=utc_now(),
                    error_code=type(exc).__name__,
                )
            except Exception as heartbeat_exc:  # noqa: BLE001 - monitoring must not mask the cause
                logger.warning(
                    "teaching scheduler failure heartbeat write failed: %s",
                    type(heartbeat_exc).__name__,
                )
            raise
        try:
            record_service_heartbeat(
                db,
                service_key=TEACHING_SCHEDULER_SERVICE_KEY,
                succeeded=True,
                occurred_at=utc_now(),
                result_count=changed,
            )
        except Exception as exc:  # noqa: BLE001 - reconciliation remains successful
            logger.warning(
                "teaching scheduler heartbeat write failed: %s",
                type(exc).__name__,
            )
        return changed


def _run_logged_iteration() -> tuple[bool, int]:
    try:
        changed = run_once()
    except Exception as exc:  # noqa: BLE001 - the long-running process must survive a bad iteration
        logger.error("teaching scheduler iteration failed: %s", type(exc).__name__)
        return False, 0
    logger.info("teaching scheduler processed %d cohort transitions", changed)
    return True, changed


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    interval = args.interval or settings.cohort_scheduler_interval_seconds
    if args.once:
        succeeded, _ = _run_logged_iteration()
        return 0 if succeeded else 1

    while True:
        _run_logged_iteration()
        time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover - exercised through main
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
