from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.esports_assessments.config_schemas import MEMBERS
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
)
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.schemas import AssessmentReportView, PracticeCreate
from tests.esports_helpers import (
    assessment_counts,
    attempt_request,
    environment,
    result_request,
    session_request,
    settings,
    student_actor,
)

pytestmark = pytest.mark.usefixtures("esports_enabled")
PREFIX = "/api/v1/esports-assessments"
NOW = datetime(2026, 9, 22, 4, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class CompletedFourSuites:
    person_id: UUID
    session_ids: tuple[UUID, ...]
    report_ids: tuple[UUID, ...]
    practice_ids: tuple[UUID, ...]
    formal_counts_before_practice: dict[str, int]


@pytest.fixture
def completed_four_suites(db: Session) -> CompletedFourSuites:
    actor = student_actor(db)
    formal = FormalAssessmentService(db)
    practice = PracticeAssessmentService(db)
    session_ids: list[UUID] = []
    report_ids: list[UUID] = []
    practice_ids: list[UUID] = []

    for suite_index, (suite_id, members) in enumerate(MEMBERS.items()):
        suite_started_at = NOW + timedelta(hours=suite_index * 2)
        session = formal.create(actor, session_request(suite_id), suite_started_at)
        session_ids.append(session.id)
        saved = None
        for member_index, test_id in enumerate(members):
            attempt_started_at = suite_started_at + timedelta(seconds=member_index * 90)
            attempt = formal.start_attempt(
                actor,
                session.id,
                attempt_request(suite_id, test_id),
                attempt_started_at,
            )
            saved = formal.submit(
                actor,
                attempt.id,
                result_request(
                    suite_id,
                    test_id,
                    seed=attempt.seed,
                    catalog_version=attempt.config.catalog_version,
                ),
                attempt_started_at + timedelta(seconds=63),
            )
        assert saved is not None
        assert saved.session.report_id is not None
        assert saved.session.status == "completed"
        assert tuple(item.test_id for item in saved.session.results) == members
        report_ids.append(saved.session.report_id)

    formal_counts = assessment_counts(db, actor.person.id)

    for suite_index, (suite_id, members) in enumerate(MEMBERS.items()):
        practice_started_at = NOW + timedelta(hours=10 + suite_index * 2)
        test_id = members[0]
        run = practice.start(
            actor,
            PracticeCreate(
                suite_id=suite_id,
                test_id=test_id,
                difficulty=3,
                settings=settings(suite_id),
                environment=environment(suite_id),
                operation_key=uuid4(),
            ),
            practice_started_at,
        )
        saved = practice.submit(
            actor,
            run.id,
            result_request(
                suite_id,
                test_id,
                seed=run.seed,
                catalog_version=run.config.catalog_version,
            ),
            practice_started_at + timedelta(seconds=63),
        )
        assert saved.mode == "practice"
        assert saved.result_id == run.id
        assert saved.scored.score is saved.scored.grade is saved.session is None
        practice_ids.append(run.id)

    return CompletedFourSuites(
        person_id=actor.person.id,
        session_ids=tuple(session_ids),
        report_ids=tuple(report_ids),
        practice_ids=tuple(practice_ids),
        formal_counts_before_practice=formal_counts,
    )


def test_formal_and_practice_counts_stay_separate(
    db: Session,
    completed_four_suites: CompletedFourSuites,
) -> None:
    completed = completed_four_suites
    assert completed.formal_counts_before_practice == {
        "formal_results": 16,
        "formal_reports": 4,
        "practice_results": 0,
    }
    assert assessment_counts(db, completed.person_id) == {
        "formal_results": 16,
        "formal_reports": 4,
        "practice_results": 4,
    }
    assert len(set(completed.session_ids)) == len(set(completed.report_ids)) == 4
    assert len(set(completed.practice_ids)) == 4

    reports = list(
        db.scalars(
            select(AssessmentReport)
            .where(AssessmentReport.id.in_(completed.report_ids))
            .order_by(AssessmentReport.id)
        )
    )
    assert len(reports) == 4
    for row in reports:
        report = AssessmentReportView.model_validate(row.report_snapshot)
        assert tuple(item.test_id for item in report.results) == MEMBERS[report.suite_id]
        assert len(report.comparisons) == 4


def _login(client: TestClient, platform_users, key: str = "self_student") -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _json(value) -> dict[str, object]:
    return value.model_dump(mode="json")


def _response_digest(response) -> bytes:
    canonical = json.dumps(
        response.json(),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).digest()


def test_response_digest_failure_does_not_disclose_scored_payload() -> None:
    private_marker = "synthetic-private-metric-marker"
    saved = Response(
        200,
        json={"metrics": {"private_marker": private_marker}, "score": 47},
    )
    replay = Response(
        200,
        json={"metrics": {"private_marker": "changed-private-marker"}, "score": 48},
    )

    with pytest.raises(AssertionError) as captured:
        assert _response_digest(replay) == _response_digest(saved)

    failure_text = str(captured.value)
    assert private_marker not in failure_text
    assert "changed-private-marker" not in failure_text
    assert "metrics" not in failure_text
    assert "score" not in failure_text


@pytest.fixture
def real_assessment_limiter(db_client: TestClient, postgres_engine):
    from app.auth.models import AuthRateLimit
    from app.esports_assessments.dependencies import get_assessment_write_limiter
    from app.esports_assessments.rate_limit import AssessmentWriteLimiter

    limiter = AssessmentWriteLimiter(sessionmaker(bind=postgres_engine), bytes(range(32)))
    with Session(postgres_engine) as cleanup_db:
        existing_keys = frozenset(cleanup_db.scalars(select(AuthRateLimit.key_digest)))
    db_client.app.dependency_overrides[get_assessment_write_limiter] = lambda: limiter
    try:
        yield
    finally:
        db_client.app.dependency_overrides.pop(get_assessment_write_limiter, None)
        with Session(postgres_engine) as cleanup_db, cleanup_db.begin():
            current_keys = frozenset(cleanup_db.scalars(select(AuthRateLimit.key_digest)))
            created_keys = current_keys - existing_keys
            if created_keys:
                cleanup_db.execute(
                    delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(created_keys))
                )


def test_http_expired_unsubmitted_and_submitted_replay_are_distinct(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
    real_assessment_limiter,
) -> None:
    from app.esports_assessments import catalog, router

    clock = [NOW]
    monkeypatch.setattr(router, "_now", lambda: clock[0])
    csrf = _login(db_client, platform_users)
    headers = {"X-CSRF-Token": csrf}

    created = db_client.post(
        f"{PREFIX}/sessions",
        json=_json(session_request("pc-fps")),
        headers=headers,
    )
    assert created.status_code == 201
    session_id = created.json()["id"]
    first = db_client.post(
        f"{PREFIX}/sessions/{session_id}/attempts",
        json=_json(attempt_request("pc-fps", "precision")),
        headers=headers,
    )
    assert first.status_code == 201
    first_attempt = db.get(AssessmentAttempt, UUID(first.json()["id"]))
    assert first_attempt is not None
    first_request = result_request(
        "pc-fps",
        "precision",
        seed=first.json()["seed"],
        catalog_version=first.json()["config"]["catalog_version"],
    )
    clock[0] = first_attempt.submit_until

    expired = db_client.post(
        f"{PREFIX}/attempts/{first_attempt.id}/result",
        json=_json(first_request),
        headers=headers,
    )
    assert expired.status_code == 410
    assert expired.json()["code"] == "esports_expired"
    db.refresh(first_attempt)
    assert first_attempt.state == "expired"
    assert (
        db.scalar(
            select(AssessmentResult.id).where(AssessmentResult.attempt_id == first_attempt.id)
        )
        is None
    )

    clock[0] += timedelta(seconds=1)
    second = db_client.post(
        f"{PREFIX}/sessions/{session_id}/attempts",
        json=_json(attempt_request("pc-fps", "tracking")),
        headers=headers,
    )
    assert second.status_code == 201
    second_attempt = db.get(AssessmentAttempt, UUID(second.json()["id"]))
    assert second_attempt is not None
    second_request = result_request(
        "pc-fps",
        "tracking",
        seed=second.json()["seed"],
        catalog_version=second.json()["config"]["catalog_version"],
    )
    clock[0] += timedelta(seconds=63)
    saved = db_client.post(
        f"{PREFIX}/attempts/{second_attempt.id}/result",
        json=_json(second_request),
        headers=headers,
    )
    assert saved.status_code == 201

    clock[0] = second_attempt.submit_until + timedelta(seconds=1)
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())
    replay = db_client.post(
        f"{PREFIX}/attempts/{second_attempt.id}/result",
        json=_json(second_request),
        headers=headers,
    )
    assert replay.status_code == 201
    assert _response_digest(replay) == _response_digest(saved)

    different_key = second_request.model_copy(update={"operation_key": uuid4()})
    rejected = db_client.post(
        f"{PREFIX}/attempts/{second_attempt.id}/result",
        json=_json(different_key),
        headers=headers,
    )
    assert rejected.status_code == 409
    assert rejected.json()["code"] == "esports_attempt_terminal"
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 1
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssessmentPracticeRun)
            .where(AssessmentPracticeRun.state == "submitted")
        )
        == 0
    )
