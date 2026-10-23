from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import Connection, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, SessionTransactionOrigin

from app.core.config import Settings
from app.core.transactions import write_transaction
from app.evaluations.media_store import PrivateMediaStore
from app.evaluations.task_models import EvaluationQuarantineCleanup


def enqueue_quarantine_cleanup(
    db: Session, keys: Iterable[str], *, occurred_at: datetime | None = None,
) -> None:
    """Record retired files in the same transaction that removes their slot."""
    keys = sorted(set(keys))
    if not keys:
        return
    if not db.in_transaction():
        raise RuntimeError("quarantine cleanup enqueue requires a transaction")
    now = occurred_at or datetime.now(UTC)
    db.execute(pg_insert(EvaluationQuarantineCleanup).values([
        {"storage_key": key, "next_attempt_at": now} for key in keys
    ]).on_conflict_do_nothing(index_elements=["storage_key"]))


def drain_quarantine_cleanup(
    db: Session, store: PrivateMediaStore, *, occurred_at: datetime, limit: int = 50,
) -> tuple[int, int]:
    """Retain failures across request/process exits; coordinate concurrent drains."""
    bind = db.get_bind()
    if isinstance(bind, Connection) and bind.in_transaction():
        # A Session may commit only its savepoint while an externally owned
        # connection transaction remains reversible. Let the scheduler unlink.
        return 0, 0
    transaction = db.get_transaction()
    if transaction is not None and transaction.origin is not SessionTransactionOrigin.AUTOBEGIN:
        # Never unlink before a caller-owned business transaction is durable.
        return 0, 0
    deleted = failed = 0
    with write_transaction(db):
        rows = list(db.scalars(
            select(EvaluationQuarantineCleanup)
            .where(EvaluationQuarantineCleanup.next_attempt_at <= occurred_at)
            .order_by(EvaluationQuarantineCleanup.next_attempt_at,
                      EvaluationQuarantineCleanup.storage_key)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ))
        for row in rows:
            try:
                store.discard_staged_key(row.storage_key)
            except Exception:  # noqa: BLE001 -- retry without exposing paths or raw IO errors
                row.attempts += 1
                row.next_attempt_at = occurred_at + timedelta(seconds=min(3600, 30 * row.attempts))
                failed += 1
            else:
                db.delete(row)
                deleted += 1
    return deleted, failed


class EvaluationUploadCleanupPending(RuntimeError):
    pass


def cleanup_expired_uploads(db: Session, *, settings: Settings, now: datetime) -> int:
    from app.evaluations.upload_service import EvaluationUploadService

    media_root, quarantine_root = settings.evaluation_media_paths()
    store = PrivateMediaStore(
        root=media_root,
        quarantine_root=quarantine_root,
        max_bytes=settings.evaluation_media_max_bytes,
        max_pixels=settings.evaluation_media_max_pixels,
        preview_max_edge=settings.evaluation_media_preview_max_edge,
        expected_identity=settings.evaluation_media_store_id,
    )
    try:
        count = EvaluationUploadService(db, store=store).expire_stale_sessions(occurred_at=now)
        drain_quarantine_cleanup(db, store, occurred_at=now)
        failed = db.scalar(select(func.count()).select_from(EvaluationQuarantineCleanup).where(
            EvaluationQuarantineCleanup.attempts > 0,
        )) or 0
        if failed:
            raise EvaluationUploadCleanupPending("quarantine cleanup will retry")
        return count
    finally:
        store.close()
