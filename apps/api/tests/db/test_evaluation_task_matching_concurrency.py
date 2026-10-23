from __future__ import annotations

import threading

from sqlalchemy import event, func, literal, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.evaluations.errors import (
    EvaluationIdempotencyConflict,
    EvaluationTaskStateConflict,
)
from app.evaluations.media_models import EvaluationMediaAsset, EvaluationMediaBlob
from app.evaluations.task_models import EvaluationIdempotencyKey, SystemEvaluationEvent
from tests.services.test_evaluation_task_matching import (
    _input,
    _pending_task,
    _service,
)
from tests.services.test_evaluation_uploads import TaskCreationScenario

pytest_plugins = ("tests.services.test_evaluation_uploads",)


def test_shared_blob_matches_serialize_and_return_a_domain_conflict(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as setup_db, setup_db.begin():
        first_task, first_asset = _pending_task(setup_db, scenario)
        second_task, second_asset = _pending_task(setup_db, scenario)
        unused_blob = setup_db.get(EvaluationMediaBlob, second_asset.blob_id)
        assert unused_blob is not None
        second_asset.blob_id = first_asset.blob_id
        setup_db.flush()
        setup_db.delete(unused_blob)
        first_task_id = first_task.id
        second_task_id = second_task.id
        first_asset_id = first_asset.id
        second_asset_id = second_asset.id

    first_checked_for_duplicates = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    sessions_usable: dict[str, bool] = {}
    result_guard = threading.Lock()

    def pause_first_after_duplicate_check(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            connection.info.get("task_match_label") != "first"
            or "SELECT evaluation_media_assets.id" not in statement
            or "evaluation_media_assets.blob_id IN" not in statement
            or "evaluation_media_assets.student_person_id =" not in statement
        ):
            return
        first_checked_for_duplicates.set()
        if not release_first.wait(timeout=10):
            raise TimeoutError("first match was not released")

    def match(label: str, task_id, *, mark_second: bool = False) -> None:
        try:
            with Session(scenario.engine) as db:
                db.connection().info["task_match_label"] = label
                try:
                    result = _service(db).confirm_task_match(
                        task_id,
                        _input(scenario),
                        actor_user_id=scenario.admin_user_id,
                        idempotency_key=f"shared-blob-{label}",
                        occurred_at=scenario.now,
                        request_id=f"shared-blob-{label}",
                    )
                except BaseException as exc:  # noqa: BLE001
                    with result_guard:
                        errors[label] = exc
                else:
                    with result_guard:
                        results[label] = result
                session_usable = db.scalar(select(literal(1))) == 1
                with result_guard:
                    sessions_usable[label] = session_usable
        finally:
            if mark_second:
                second_finished.set()

    event.listen(
        scenario.engine,
        "after_cursor_execute",
        pause_first_after_duplicate_check,
    )
    first = threading.Thread(target=match, args=("first", first_task_id))
    second = threading.Thread(
        target=match,
        args=("second", second_task_id),
        kwargs={"mark_second": True},
    )
    second_started = False
    try:
        first.start()
        assert first_checked_for_duplicates.wait(timeout=10)
        second.start()
        second_started = True
        second_completed_while_first_locked = second_finished.wait(timeout=2)
    finally:
        release_first.set()
        first.join(timeout=10)
        if second_started:
            second.join(timeout=10)
        event.remove(
            scenario.engine,
            "after_cursor_execute",
            pause_first_after_duplicate_check,
        )

    assert not second_completed_while_first_locked
    assert not first.is_alive()
    assert not second.is_alive()
    assert set(results) == {"first"}
    assert isinstance(errors.get("second"), EvaluationTaskStateConflict)
    assert sessions_usable == {"first": True, "second": True}

    with Session(scenario.engine) as observer:
        first_db_task = observer.get(type(first_task), first_task_id)
        second_db_task = observer.get(type(second_task), second_task_id)
        first_db_asset = observer.get(EvaluationMediaAsset, first_asset_id)
        second_db_asset = observer.get(EvaluationMediaAsset, second_asset_id)
        assert first_db_task is not None and first_db_task.status == "manual_input"
        assert second_db_task is not None and second_db_task.status == "pending_match"
        assert first_db_asset is not None
        assert first_db_asset.student_person_id == scenario.actor_person_id
        assert second_db_asset is not None and second_db_asset.student_person_id is None
        assert observer.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.event_type == "task_matched",
                SystemEvaluationEvent.task_id.in_((first_task_id, second_task_id)),
            )
        ) == 1
        assert observer.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.scope == "evaluation.task.match",
                EvaluationIdempotencyKey.actor_key
                == str(scenario.admin_user_id),
            )
        ) == 1


def test_absent_idempotency_key_is_serialized_across_different_tasks(
    task_creation_scenario: TaskCreationScenario,
) -> None:
    scenario = task_creation_scenario
    with Session(scenario.engine) as setup_db, setup_db.begin():
        first_task, _first_asset = _pending_task(setup_db, scenario)
        second_task, _second_asset = _pending_task(setup_db, scenario)
        first_task_id = first_task.id
        second_task_id = second_task.id

    first_checked_key = threading.Event()
    release_first = threading.Event()
    second_finished = threading.Event()
    results: dict[str, object] = {}
    errors: dict[str, BaseException] = {}
    sessions_usable: dict[str, bool] = {}
    result_guard = threading.Lock()

    def pause_first_after_absent_key_check(
        connection: Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if (
            connection.info.get("task_match_label") != "first"
            or "FROM evaluation_idempotency_keys" not in statement
            or "FOR UPDATE" not in statement
        ):
            return
        first_checked_key.set()
        if not release_first.wait(timeout=10):
            raise TimeoutError("first idempotency request was not released")

    def match(label: str, task_id, *, mark_second: bool = False) -> None:
        try:
            with Session(scenario.engine) as db:
                db.connection().info["task_match_label"] = label
                try:
                    result = _service(db).confirm_task_match(
                        task_id,
                        _input(scenario),
                        actor_user_id=scenario.admin_user_id,
                        idempotency_key="shared-idempotency-key",
                        occurred_at=scenario.now,
                        request_id=f"shared-key-{label}",
                    )
                except BaseException as exc:  # noqa: BLE001
                    with result_guard:
                        errors[label] = exc
                else:
                    with result_guard:
                        results[label] = result
                session_usable = db.scalar(select(literal(1))) == 1
                with result_guard:
                    sessions_usable[label] = session_usable
        finally:
            if mark_second:
                second_finished.set()

    event.listen(
        scenario.engine,
        "after_cursor_execute",
        pause_first_after_absent_key_check,
    )
    first = threading.Thread(target=match, args=("first", first_task_id))
    second = threading.Thread(
        target=match,
        args=("second", second_task_id),
        kwargs={"mark_second": True},
    )
    second_started = False
    try:
        first.start()
        assert first_checked_key.wait(timeout=10)
        second.start()
        second_started = True
        second_completed_while_first_locked = second_finished.wait(timeout=2)
    finally:
        release_first.set()
        first.join(timeout=10)
        if second_started:
            second.join(timeout=10)
        event.remove(
            scenario.engine,
            "after_cursor_execute",
            pause_first_after_absent_key_check,
        )

    assert not second_completed_while_first_locked
    assert not first.is_alive()
    assert not second.is_alive()
    assert set(results) == {"first"}
    assert isinstance(errors.get("second"), EvaluationIdempotencyConflict)
    assert sessions_usable == {"first": True, "second": True}

    with Session(scenario.engine) as observer:
        first_db_task = observer.get(type(first_task), first_task_id)
        second_db_task = observer.get(type(second_task), second_task_id)
        assert first_db_task is not None and first_db_task.status == "manual_input"
        assert second_db_task is not None and second_db_task.status == "pending_match"
        assert observer.scalar(
            select(func.count(SystemEvaluationEvent.id)).where(
                SystemEvaluationEvent.event_type == "task_matched",
                SystemEvaluationEvent.task_id.in_((first_task_id, second_task_id)),
            )
        ) == 1
        assert observer.scalar(
            select(func.count(EvaluationIdempotencyKey.id)).where(
                EvaluationIdempotencyKey.scope == "evaluation.task.match",
                EvaluationIdempotencyKey.actor_key
                == str(scenario.admin_user_id),
            )
        ) == 1
