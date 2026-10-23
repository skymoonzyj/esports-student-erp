from __future__ import annotations

import threading
from datetime import timedelta

from sqlalchemy import event, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.evaluations.processing_models import SystemEvaluationAttempt
from app.evaluations.processing_service import (
    EvaluationLeaseRenewal,
    EvaluationProcessingService,
)
from tests.services.test_evaluation_processing import (
    NOW,
    TOKEN_PEPPER,
    _create_queued_task,
)
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_uploads",)


def test_two_postgresql_workers_use_skip_locked_for_one_atomic_claim(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    task_id = _create_queued_task(scenario)
    first_claim_locked = threading.Event()
    release_first_claim = threading.Event()
    second_finished = threading.Event()
    listener_guard = threading.Lock()
    paused_connection: list[int] = []
    claims: dict[str, object | None] = {}
    backend_pids: dict[str, int] = {}
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    def hold_first_claim_after_lock(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            "FOR UPDATE SKIP LOCKED" not in statement
            or "analysis_config_snapshot" not in statement
        ):
            return
        should_pause = False
        with listener_guard:
            if not paused_connection:
                paused_connection.append(id(connection))
                should_pause = True
        if should_pause:
            first_claim_locked.set()
            if not release_first_claim.wait(timeout=10):
                raise TimeoutError("first claim was not released")

    def claim(worker_identity: str, *, mark_second: bool = False) -> None:
        try:
            with Session(scenario.engine) as db:
                backend_pid = int(db.scalar(select(func.pg_backend_pid())) or 0)
                result = EvaluationProcessingService(
                    db,
                    token_pepper=TOKEN_PEPPER,
                ).claim_next(
                    worker_identity=worker_identity,
                    supported_adapters={"lol.v1"},
                    occurred_at=NOW,
                )
                with result_guard:
                    backend_pids[worker_identity] = backend_pid
                    claims[worker_identity] = result
        except BaseException as exc:  # noqa: BLE001  # pragma: no cover
            with result_guard:
                errors.append(exc)
        finally:
            if mark_second:
                second_finished.set()

    event.listen(scenario.engine, "after_cursor_execute", hold_first_claim_after_lock)
    first = threading.Thread(target=claim, args=("score-worker-one",))
    second = threading.Thread(
        target=claim,
        args=("score-worker-two",),
        kwargs={"mark_second": True},
    )
    second_started = False
    try:
        first.start()
        assert first_claim_locked.wait(timeout=10)
        second.start()
        second_started = True
        second_completed_while_locked = second_finished.wait(timeout=5)
    finally:
        release_first_claim.set()
        first.join(timeout=10)
        if second_started:
            second.join(timeout=10)
        event.remove(
            scenario.engine,
            "after_cursor_execute",
            hold_first_claim_after_lock,
        )

    assert second_completed_while_locked
    assert not errors
    assert not first.is_alive()
    assert not second.is_alive()
    assert len(set(backend_pids.values())) == 2
    assert claims["score-worker-one"] is not None
    assert claims["score-worker-two"] is None
    assert claims["score-worker-one"].task_id == task_id
    with Session(scenario.engine) as db:
        attempts = list(
            db.scalars(
                select(SystemEvaluationAttempt).where(
                    SystemEvaluationAttempt.task_id == task_id
                )
            )
        )
        assert [(attempt.task_id, attempt.attempt) for attempt in attempts] == [
            (task_id, 1)
        ]


def test_two_postgresql_renewals_cannot_move_lease_expiry_backwards(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    task_id = _create_queued_task(scenario)
    with Session(scenario.engine) as db:
        claim = EvaluationProcessingService(
            db,
            token_pepper=TOKEN_PEPPER,
        ).claim_next(
            worker_identity="score-worker-local",
            supported_adapters={"lol.v1"},
            occurred_at=NOW,
        )
    assert claim is not None

    old_waiting_before_lock = threading.Event()
    release_old_request = threading.Event()
    results: dict[str, EvaluationLeaseRenewal] = {}
    backend_pids: dict[str, int] = {}
    errors: list[BaseException] = []
    result_guard = threading.Lock()

    def hold_old_request_before_task_lock(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            connection.info.get("renew_order") == "old"
            and "FROM system_evaluation_tasks" in statement
            and "FOR UPDATE" in statement
        ):
            old_waiting_before_lock.set()
            if not release_old_request.wait(timeout=10):
                raise TimeoutError("old renewal was not released")

    def renew(label: str, accepted_offset: int) -> None:
        try:
            with Session(scenario.engine) as db:
                db.connection().info["renew_order"] = label
                backend_pid = int(db.scalar(select(func.pg_backend_pid())) or 0)
                result = EvaluationProcessingService(
                    db,
                    token_pepper=TOKEN_PEPPER,
                ).renew(
                    task_id=claim.task_id,
                    attempt=claim.attempt,
                    task_version=claim.task_version,
                    worker_identity="score-worker-local",
                    lease_token=claim.lease_token,
                    occurred_at=NOW + timedelta(minutes=accepted_offset),
                    request_id=f"processing-renew-{label}",
                )
                with result_guard:
                    backend_pids[label] = backend_pid
                    results[label] = result
        except BaseException as exc:  # noqa: BLE001  # pragma: no cover
            with result_guard:
                errors.append(exc)

    event.listen(
        scenario.engine,
        "before_cursor_execute",
        hold_old_request_before_task_lock,
    )
    old = threading.Thread(target=renew, args=("old", 1))
    new = threading.Thread(target=renew, args=("new", 2))
    new_started = False
    try:
        old.start()
        assert old_waiting_before_lock.wait(timeout=10)
        new.start()
        new_started = True
        new.join(timeout=10)
        assert not new.is_alive()
    finally:
        release_old_request.set()
        old.join(timeout=10)
        if new_started:
            new.join(timeout=10)
        event.remove(
            scenario.engine,
            "before_cursor_execute",
            hold_old_request_before_task_lock,
        )

    assert not errors
    assert not old.is_alive()
    assert len(set(backend_pids.values())) == 2
    expected_expiry = NOW + timedelta(minutes=7)
    assert results["new"].lease_expires_at == expected_expiry
    assert results["old"].lease_expires_at == expected_expiry
    with Session(scenario.engine) as db:
        attempt = db.scalar(
            select(SystemEvaluationAttempt).where(
                SystemEvaluationAttempt.task_id == task_id,
                SystemEvaluationAttempt.attempt == 1,
            )
        )
        assert attempt is not None
        assert attempt.renewed_at == NOW + timedelta(minutes=2)
        assert attempt.lease_expires_at == expected_expiry
        assert attempt.status == "active"
