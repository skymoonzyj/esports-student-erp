from __future__ import annotations

from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.revenue_share import reconciliation_locking as locking
from app.revenue_share.errors import RevenueShareNotFound


class RecordingSession:
    def __init__(self, payment_exists: bool = True):
        self.statements = []
        self.payment_exists = payment_exists

    def scalars(self, statement):
        self.statements.append(statement)
        return []

    def scalar(self, statement):
        self.statements.append(statement)
        return uuid4() if self.payment_exists else None

    @property
    def tables(self):
        return [statement.get_final_froms()[0].name for statement in self.statements]


def test_prelocks_sorted_business_context_before_payment(monkeypatch) -> None:
    first, second = UUID(int=1), UUID(int=2)
    snapshot = locking._ContextSnapshot(uuid4(), second, ((first, second), (second, first)))
    monkeypatch.setattr(locking, "_context_snapshot", lambda *_: snapshot)
    recorder = RecordingSession()
    locking.lock_reconciliation_context(cast(Session, recorder), uuid4())
    assert recorder.tables == ["cohorts", "enrollments", "admission_cases", "payments"]
    assert list(recorder.statements[0].compile().params.values()) == [[first, second]]
    assert list(recorder.statements[1].compile().params.values()) == [[first, second]]
    assert "ORDER BY cohorts.id" in str(recorder.statements[0])
    assert "ORDER BY enrollments.id" in str(recorder.statements[1])
    assert all("FOR UPDATE" in str(statement) for statement in recorder.statements)


@pytest.mark.parametrize("change", ["current_pointer", "new_enrollment", "cohort"])
def test_changed_context_aborts_before_locking_lower_level_rows(monkeypatch, change) -> None:
    case_id, enrollment_id, cohort_id = uuid4(), uuid4(), uuid4()
    before = locking._ContextSnapshot(case_id, enrollment_id, ((enrollment_id, cohort_id),))
    after = {
        "current_pointer": locking._ContextSnapshot(case_id, None, before.enrollments),
        "new_enrollment": locking._ContextSnapshot(case_id, enrollment_id,
                                                  before.enrollments + ((uuid4(), cohort_id),)),
        "cohort": locking._ContextSnapshot(case_id, enrollment_id, ((enrollment_id, uuid4()),)),
    }[change]
    snapshots = iter((before, after))
    monkeypatch.setattr(locking, "_context_snapshot", lambda *_: next(snapshots))
    recorder = RecordingSession()
    with pytest.raises(locking.ReconciliationContextChanged):
        locking.lock_reconciliation_context(cast(Session, recorder), uuid4())
    assert recorder.tables == ["cohorts"]


def test_new_attachment_after_payment_lock_requires_retry_without_reverse_locks(monkeypatch) -> None:
    empty = locking._ContextSnapshot(None, None, ())
    linked = locking._ContextSnapshot(uuid4(), uuid4(), ((uuid4(), uuid4()),))
    snapshots = iter((empty, linked))
    monkeypatch.setattr(locking, "_context_snapshot", lambda *_: next(snapshots))
    recorder = RecordingSession()
    with pytest.raises(locking.ReconciliationContextChanged):
        locking.lock_reconciliation_context(cast(Session, recorder), uuid4())
    assert recorder.tables == ["payments"]


def test_missing_payment_uses_existing_not_found_error(monkeypatch) -> None:
    monkeypatch.setattr(locking, "_context_snapshot", lambda *_: locking._ContextSnapshot(None, None, ()))
    recorder = RecordingSession(payment_exists=False)
    with pytest.raises(RevenueShareNotFound):
        locking.lock_reconciliation_context(cast(Session, recorder), uuid4())
