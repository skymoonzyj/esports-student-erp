from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.esports_assessments.errors import (
    AssessmentConflict,
    AssessmentExpired,
    AssessmentForbidden,
    AssessmentUnavailable,
)
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.lifecycle import effective_run_state, operation_digest
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.esports_assessments.schemas import SessionCreate
from tests.esports_helpers import (
    attempt_request,
    environment,
    result_request,
    session_request,
    settings,
    student_actor,
)

pytestmark = pytest.mark.usefixtures("esports_enabled")
NOW = datetime(2026, 9, 22, 8, tzinfo=UTC)


@pytest.fixture
def actor(db):
    return student_actor(db)


def test_run_lease_and_submission_window_are_different():
    run_until = NOW + timedelta(seconds=90)
    submit_until = NOW + timedelta(minutes=30)
    assert effective_run_state("running", run_until, submit_until, run_until) == (
        "awaiting_submission"
    )
    assert effective_run_state("running", run_until, submit_until, submit_until) == "expired"
    assert effective_run_state("submitted", run_until, submit_until, submit_until) == "submitted"


def test_operation_digest_is_canonical_and_target_bound():
    key = UUID("11111111-1111-4111-8111-111111111111")
    request = SessionCreate(suite_id="pc-fps", operation_key=key)
    first = operation_digest("formal", "create_session", None, request)
    assert first == operation_digest(
        "formal", "create_session", None, request.model_dump(mode="json")
    )
    assert first != operation_digest("formal", "create_session", uuid4(), request)
    assert first != operation_digest("practice", "create_session", None, request)


def test_session_create_replay_is_authorized_and_payload_bound(db, actor):
    service = FormalAssessmentService(db)
    key = uuid4()
    first = service.create(actor, session_request("pc-fps", key), NOW)
    replay = service.create(actor, session_request("pc-fps", key), NOW + timedelta(seconds=1))
    assert replay == first
    with pytest.raises(AssessmentConflict) as mismatch:
        service.create(actor, session_request("pc-moba", key), NOW)
    assert mismatch.value.code == "esports_replay_mismatch"

    actor.account.status = "disabled"
    db.commit()
    with pytest.raises(AssessmentForbidden):
        service.create(actor, session_request("pc-fps", key), NOW)


def test_withdrawn_suite_rejects_fresh_work_but_not_replay(db, actor, monkeypatch):
    from app.esports_assessments import catalog

    service = FormalAssessmentService(db)
    request = session_request("pc-fps")
    session = service.create(actor, request, NOW)
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())
    assert service.create(actor, request, NOW).id == session.id
    with pytest.raises(AssessmentUnavailable) as create_error:
        service.create(actor, session_request("pc-moba"), NOW)
    assert create_error.value.code == "esports_suite_unavailable"
    with pytest.raises(AssessmentUnavailable):
        service.start_attempt(
            actor,
            session.id,
            attempt_request("pc-fps", "precision"),
            NOW,
        )


def test_withdrawn_suite_still_accepts_valid_existing_attempt_submission(db, actor, monkeypatch):
    from app.esports_assessments import catalog

    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    attempt = service.start_attempt(actor, session.id, attempt_request("pc-fps", "precision"), NOW)
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())
    saved = service.submit(
        actor,
        attempt.id,
        result_request(
            "pc-fps",
            "precision",
            seed=attempt.seed,
            catalog_version=attempt.config.catalog_version,
        ),
        NOW + timedelta(seconds=63),
    )
    assert saved.result_id


def test_attempt_replay_keeps_seed_and_deadlines_but_refreshes_server_now(db, actor, monkeypatch):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    generated: list[int] = []

    def next_seed(bits: int) -> int:
        assert bits == 32
        value = 123 + len(generated)
        generated.append(value)
        return value

    monkeypatch.setattr("app.esports_assessments.formal_service.secrets.randbits", next_seed)
    key = uuid4()
    first = service.start_attempt(
        actor, session.id, attempt_request("pc-fps", "precision", key), NOW
    )
    replay_now = NOW + timedelta(seconds=20)
    replay = service.start_attempt(
        actor, session.id, attempt_request("pc-fps", "precision", key), replay_now
    )
    assert generated == [123]
    assert (replay.id, replay.seed, replay.issued_at, replay.run_until, replay.submit_until) == (
        first.id,
        first.seed,
        first.issued_at,
        first.run_until,
        first.submit_until,
    )
    assert replay.server_now == replay_now


def test_formal_pc_fps_requires_active_pointer_lock(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    bad_environment = environment("pc-fps").model_copy(update={"pointer_lock_active": False})
    request = attempt_request("pc-fps", "precision").model_copy(
        update={"environment": bad_environment}
    )
    with pytest.raises(ValueError, match="Pointer Lock"):
        service.start_attempt(actor, session.id, request, NOW)


def test_awaiting_submission_releases_occupancy_but_not_same_test(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    first = service.start_attempt(actor, session.id, attempt_request("pc-fps", "precision"), NOW)
    at_lease = first.run_until
    with pytest.raises(AssessmentConflict) as pending:
        service.start_attempt(
            actor,
            session.id,
            attempt_request("pc-fps", "precision"),
            at_lease,
        )
    assert pending.value.code == "esports_test_attempt_pending"
    second = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "tracking"),
        at_lease,
    )
    assert second.test_id == "tracking"
    saved = service.submit(
        actor,
        first.id,
        result_request(
            "pc-fps",
            "precision",
            seed=first.seed,
            catalog_version=first.config.catalog_version,
        ),
        first.submit_until - timedelta(microseconds=1),
    )
    assert saved.result_id


def test_fresh_formal_start_reconciles_cross_mode_occupancy(db, actor):
    from app.esports_assessments.catalog import get_task

    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    config = get_task("pc-moba", "last-hit")
    practice = AssessmentPracticeRun(
        person_id=actor.person.id,
        created_by_user_id=actor.account.id,
        suite_id="pc-moba",
        test_id="last-hit",
        difficulty=3,
        seed=123,
        config_snapshot=config.model_dump(mode="json"),
        settings_snapshot=settings("pc-moba").model_dump(mode="json"),
        environment_snapshot=environment("pc-moba").model_dump(mode="json"),
        state="running",
        creation_key=uuid4(),
        creation_request_digest="a" * 64,
        run_until=NOW + timedelta(seconds=90),
        submit_until=NOW + timedelta(minutes=30),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(practice)
    db.commit()
    with pytest.raises(AssessmentConflict) as occupied:
        service.start_attempt(actor, session.id, attempt_request("pc-fps", "precision"), NOW)
    assert occupied.value.code == "esports_active_run"
    started = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "precision"),
        practice.run_until,
    )
    assert started.state == "running"
    db.expire_all()
    assert db.get(AssessmentPracticeRun, practice.id).state == "awaiting_submission"


def test_submit_deadline_is_exclusive_and_invalid_payload_does_not_claim_key(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    attempt = service.start_attempt(actor, session.id, attempt_request("pc-fps", "precision"), NOW)
    key = uuid4()
    invalid = result_request(
        "pc-fps",
        "precision",
        seed=attempt.seed,
        key=key,
        catalog_version=attempt.config.catalog_version,
    )
    invalid = invalid.model_copy(update={"raw": invalid.raw.model_copy(update={"shots": 0})})
    with pytest.raises(ValueError):
        service.submit(actor, attempt.id, invalid, NOW + timedelta(seconds=63))
    valid = result_request(
        "pc-fps",
        "precision",
        seed=attempt.seed,
        key=key,
        catalog_version=attempt.config.catalog_version,
    )
    saved = service.submit(actor, attempt.id, valid, NOW + timedelta(seconds=63))
    assert saved.result_id

    other = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "tracking"),
        NOW + timedelta(seconds=90),
    )
    with pytest.raises(AssessmentExpired) as expired:
        service.submit(
            actor,
            other.id,
            result_request(
                "pc-fps",
                "tracking",
                seed=other.seed,
                catalog_version=other.config.catalog_version,
            ),
            other.submit_until,
        )
    assert expired.value.code == "esports_expired"
    db.expire_all()
    assert db.get(AssessmentAttempt, other.id).state == "expired"
    retried = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "tracking"),
        other.submit_until,
    )
    assert retried.id != other.id


def test_fatal_quality_is_a_safe_conflict_and_does_not_claim_submission_key(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    attempt = service.start_attempt(actor, session.id, attempt_request("pc-fps", "precision"), NOW)
    key = uuid4()
    rejected = result_request(
        "pc-fps",
        "precision",
        seed=attempt.seed,
        key=key,
        quality_updates={"hidden": True},
        catalog_version=attempt.config.catalog_version,
    )
    with pytest.raises(AssessmentConflict) as error:
        service.submit(actor, attempt.id, rejected, NOW + timedelta(seconds=63))
    assert error.value.code == "esports_quality_rejected"
    accepted = result_request(
        "pc-fps",
        "precision",
        seed=attempt.seed,
        key=key,
        catalog_version=attempt.config.catalog_version,
    )
    assert service.submit(actor, attempt.id, accepted, NOW + timedelta(seconds=63)).result_id


def test_schedule_failure_does_not_claim_submission_key(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    attempt = service.start_attempt(
        actor, session.id, attempt_request("pc-fps", "recognition"), NOW
    )
    key = uuid4()
    wrong = result_request(
        "pc-fps",
        "recognition",
        seed=(attempt.seed + 1) & 0xFFFFFFFF,
        key=key,
        catalog_version=attempt.config.catalog_version,
    )
    with pytest.raises(ValueError, match="schedule"):
        service.submit(actor, attempt.id, wrong, NOW + timedelta(seconds=63))
    correct = result_request(
        "pc-fps",
        "recognition",
        seed=attempt.seed,
        key=key,
        catalog_version=attempt.config.catalog_version,
    )
    assert service.submit(actor, attempt.id, correct, NOW + timedelta(seconds=63)).result_id


def test_interrupt_is_idempotent_and_operation_key_is_attempt_bound(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    first = service.start_attempt(actor, session.id, attempt_request("pc-fps", "precision"), NOW)
    key = uuid4()
    interrupted = service.interrupt(actor, first.id, key, NOW + timedelta(seconds=10))
    assert interrupted.state == "interrupted"
    assert service.interrupt(actor, first.id, key, NOW + timedelta(seconds=20)).id == first.id
    second = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "precision"),
        NOW + timedelta(seconds=20),
    )
    with pytest.raises(AssessmentConflict) as mismatch:
        service.interrupt(actor, second.id, key, NOW + timedelta(seconds=30))
    assert mismatch.value.code == "esports_replay_mismatch"


def test_abandon_interrupts_pending_attempts_and_saved_replay_survives(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    saved_attempt = service.start_attempt(
        actor, session.id, attempt_request("pc-fps", "precision"), NOW
    )
    submit = result_request(
        "pc-fps",
        "precision",
        seed=saved_attempt.seed,
        catalog_version=saved_attempt.config.catalog_version,
    )
    saved = service.submit(actor, saved_attempt.id, submit, NOW + timedelta(seconds=63))
    pending = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "tracking"),
        NOW + timedelta(seconds=90),
    )
    abandon_key = uuid4()
    abandoned = service.abandon(actor, session.id, abandon_key, NOW + timedelta(seconds=100))
    assert abandoned.status == "abandoned"
    assert service.abandon(actor, session.id, abandon_key, NOW).status == "abandoned"
    db.expire_all()
    assert db.get(AssessmentAttempt, pending.id).state == "interrupted"
    assert service.submit(actor, saved_attempt.id, submit, NOW).result_id == saved.result_id
    with pytest.raises(AssessmentConflict) as late:
        service.submit(
            actor,
            pending.id,
            result_request(
                "pc-fps",
                "tracking",
                seed=pending.seed,
                catalog_version=pending.config.catalog_version,
            ),
            NOW + timedelta(seconds=101),
        )
    assert late.value.code == "esports_session_terminal"


def test_four_out_of_order_results_create_one_report_with_exact_comparisons(db, actor):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    order = ("recognition", "precision", "switching", "tracking")
    final = None
    final_request_key = None
    for index, test_id in enumerate(order):
        started_at = NOW + timedelta(seconds=90 * index)
        attempt = service.start_attempt(
            actor, session.id, attempt_request("pc-fps", test_id), started_at
        )
        quality_updates = {"long_frame_gap_total_ms": 3001} if test_id == "precision" else None
        request = result_request(
            "pc-fps",
            test_id,
            seed=attempt.seed,
            quality_updates=quality_updates,
            catalog_version=attempt.config.catalog_version,
        )
        final_request_key = request.operation_key
        final = service.submit(
            actor,
            attempt.id,
            request,
            started_at + timedelta(seconds=63),
        )
        assert (final.session.report_id is not None) == (index == 3)
    assert final is not None and final_request_key is not None
    report_row = db.scalar(
        select(AssessmentReport).where(AssessmentReport.session_id == session.id)
    )
    assert report_row is not None
    report = report_row.report_snapshot
    assert [item["test_id"] for item in report["results"]] == [
        "precision",
        "tracking",
        "switching",
        "recognition",
    ]
    assert [item["test_id"] for item in report["comparisons"]] == [
        "precision",
        "tracking",
        "switching",
        "recognition",
    ]
    reasons = {item["test_id"]: item["non_comparable_reasons"] for item in report["comparisons"]}
    assert reasons["precision"] == ["current_quality_warning", "no_comparable_history"]
    assert reasons["tracking"] == ["no_comparable_history"]
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssessmentReport)
            .where(AssessmentReport.session_id == session.id)
        )
        == 1
    )
    actions = list(
        db.scalars(
            select(AuditEvent.action)
            .where(AuditEvent.resource_id == session.id)
            .order_by(AuditEvent.created_at)
        )
    )
    assert actions == ["esports_assessments.started", "esports_assessments.completed"]
    completed_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.resource_id == session.id,
            AuditEvent.action == "esports_assessments.completed",
        )
    )
    assert completed_audit is not None
    assert completed_audit.event_metadata == {
        "session_id": str(session.id),
        "person_id": str(actor.person.id),
        "operation_key": str(final_request_key),
        "suite_id": "pc-fps",
        "catalog_version": "pc-fps-v3",
        "scoring_version": "pc-fps-v1",
        "result_count": 4,
        "report_id": str(final.session.report_id),
    }
    with pytest.raises(AssessmentConflict) as terminal:
        service.abandon(actor, session.id, uuid4(), NOW + timedelta(minutes=10))
    assert terminal.value.code == "esports_session_terminal"


def test_clean_same_configuration_uses_previous_completed_report(db, actor):
    service = FormalAssessmentService(db)

    def complete(started_at):
        session = service.create(actor, session_request(), started_at)
        saved = None
        for index, test_id in enumerate(("precision", "tracking", "switching", "recognition")):
            attempt_at = started_at + timedelta(seconds=90 * index)
            attempt = service.start_attempt(
                actor, session.id, attempt_request("pc-fps", test_id), attempt_at
            )
            saved = service.submit(
                actor,
                attempt.id,
                result_request(
                    "pc-fps",
                    test_id,
                    seed=attempt.seed,
                    catalog_version=attempt.config.catalog_version,
                ),
                attempt_at + timedelta(seconds=63),
            )
        assert saved is not None and saved.session.report_id is not None
        return saved.session.report_id

    previous_report_id = complete(NOW)
    current_report_id = complete(NOW + timedelta(minutes=10))
    row = db.get(AssessmentReport, current_report_id)
    assert row is not None
    assert [item["previous_report_id"] for item in row.report_snapshot["comparisons"]] == [
        str(previous_report_id)
    ] * 4
    assert [item["score_delta"] for item in row.report_snapshot["comparisons"]] == [0] * 4
    assert all(not item["non_comparable_reasons"] for item in row.report_snapshot["comparisons"])


def complete_comparison_report(
    db, actor, at, *, sensitivity=1, width=1280, flagged=False, perfect=True
):
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), at)
    saved = None
    for index, test_id in enumerate(("precision", "tracking", "switching", "recognition")):
        started = at + timedelta(seconds=90 * index)
        request = attempt_request("pc-fps", test_id)
        request = request.model_copy(
            update={
                "settings": request.settings.model_copy(update={"sensitivity": sensitivity}),
                "environment": request.environment.model_copy(update={"width_css_px": width}),
            }
        )
        attempt = service.start_attempt(actor, session.id, request, started)
        saved = service.submit(
            actor,
            attempt.id,
            result_request(
                "pc-fps",
                test_id,
                seed=attempt.seed,
                perfect=perfect,
                quality_updates={"long_frame_gap_total_ms": 3001} if flagged else None,
                catalog_version=attempt.config.catalog_version,
            ),
            started + timedelta(seconds=63),
        )
    assert saved is not None and saved.session.report_id is not None
    report = db.get(AssessmentReport, saved.session.report_id)
    assert report is not None
    return report


@pytest.mark.parametrize(
    ("previous_options", "current_options", "reasons"),
    [
        ({"sensitivity": 1.05}, {}, ["settings_changed"]),
        ({"width": 1000}, {}, ["environment_changed"]),
        ({"flagged": True}, {}, ["previous_quality_warning"]),
        ({}, {"flagged": True}, ["current_quality_warning"]),
        (
            {"sensitivity": 1.05, "width": 1000, "flagged": True},
            {"flagged": True},
            [
                "current_quality_warning",
                "settings_changed",
                "environment_changed",
                "previous_quality_warning",
            ],
        ),
    ],
)
def test_formal_report_freezes_actual_incompatibility_reasons(
    db, actor, previous_options, current_options, reasons
):
    previous = complete_comparison_report(db, actor, NOW, **previous_options)
    previous_snapshot = previous.report_snapshot
    current = complete_comparison_report(db, actor, NOW + timedelta(minutes=10), **current_options)
    assert current.report_snapshot["comparisons"] == [
        {
            "test_id": test_id,
            "previous_report_id": str(previous.id),
            "score_delta": None,
            "non_comparable_reasons": reasons,
        }
        for test_id in ("precision", "tracking", "switching", "recognition")
    ]
    db.refresh(previous)
    assert previous.report_snapshot == previous_snapshot


def test_formal_report_prefers_older_compatible_over_newer_incompatible(db, actor):
    older = complete_comparison_report(db, actor, NOW, perfect=False)
    complete_comparison_report(db, actor, NOW + timedelta(minutes=10), sensitivity=1.05)
    current = complete_comparison_report(db, actor, NOW + timedelta(minutes=20))
    for comparison in current.report_snapshot["comparisons"]:
        assert comparison["previous_report_id"] == str(older.id)
        assert comparison["score_delta"] == 100
        assert comparison["non_comparable_reasons"] == []


def test_formal_report_uses_latest_compatible_not_highest_score(db, actor):
    complete_comparison_report(db, actor, NOW)
    latest = complete_comparison_report(db, actor, NOW + timedelta(minutes=10), perfect=False)
    current = complete_comparison_report(db, actor, NOW + timedelta(minutes=20))
    assert all(
        item["previous_report_id"] == str(latest.id) and item["score_delta"] == 100
        for item in current.report_snapshot["comparisons"]
    )


def test_frozen_comparison_detects_registered_config_difference_without_database_fabrication():
    from app.esports_assessments.catalog import get_task
    from app.esports_assessments.formal_service import _frozen_comparison
    from tests.unit.test_esports_reporting import report_results

    # Formal v1 configs cannot differ today. Exercise the future-facing pure
    # comparison branch with valid registered values, not an impossible DB report.
    current = report_results()[0]
    previous = current.model_copy(
        update={"config": get_task("pc-fps", "precision", 2, catalog_version="pc-fps-v3")}
    )
    report_id = uuid4()
    result = _frozen_comparison(current, (report_id, previous), allow_delta=True)
    assert result.previous_report_id == report_id
    assert result.score_delta is None
    assert result.non_comparable_reasons == ("config_changed",)


def test_frozen_comparison_never_derives_a_delta_for_a_fallback_candidate():
    from app.esports_assessments.formal_service import _frozen_comparison
    from tests.unit.test_esports_reporting import report_results

    current = report_results()[0]
    candidate_id = uuid4()
    comparison = _frozen_comparison(current, (candidate_id, current), allow_delta=False)
    assert comparison.previous_report_id == candidate_id
    assert comparison.score_delta is None
    assert comparison.non_comparable_reasons == ()
    assert _frozen_comparison(current, (candidate_id, current), allow_delta=True).score_delta == 0


def test_final_audit_failure_rolls_back_result_report_and_operation_key(db, actor, monkeypatch):
    from app.audit.service import AuditMetadataRejected, AuditService

    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    attempts = []
    for index, test_id in enumerate(("precision", "tracking", "switching", "recognition")):
        started_at = NOW + timedelta(seconds=90 * index)
        attempt = service.start_attempt(
            actor, session.id, attempt_request("pc-fps", test_id), started_at
        )
        attempts.append(attempt)
        if index < 3:
            service.submit(
                actor,
                attempt.id,
                result_request(
                    "pc-fps",
                    test_id,
                    seed=attempt.seed,
                    catalog_version=attempt.config.catalog_version,
                ),
                started_at + timedelta(seconds=63),
            )

    original = AuditService.append

    def fail_completion(self, **kwargs):
        if kwargs["action"] == "esports_assessments.completed":
            raise AuditMetadataRejected("forced completion failure")
        return original(self, **kwargs)

    monkeypatch.setattr(AuditService, "append", fail_completion)
    final_request = result_request(
        "pc-fps",
        "recognition",
        seed=attempts[-1].seed,
        catalog_version=attempts[-1].config.catalog_version,
    )
    with pytest.raises(AuditMetadataRejected):
        service.submit(
            actor,
            attempts[-1].id,
            final_request,
            NOW + timedelta(seconds=333),
        )
    db.expire_all()
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssessmentResult)
            .where(AssessmentResult.session_id == session.id)
        )
        == 3
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssessmentReport)
            .where(AssessmentReport.session_id == session.id)
        )
        == 0
    )
    assert db.get(AssessmentSession, session.id).status == "in_progress"

    monkeypatch.setattr(AuditService, "append", original)
    retried = service.submit(
        actor,
        attempts[-1].id,
        final_request,
        NOW + timedelta(seconds=333),
    )
    assert retried.session.status == "completed"
