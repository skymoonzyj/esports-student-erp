from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.esports_assessments.errors import AssessmentRateLimited
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
)
from app.esports_assessments.schemas import PracticeCreate
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.esports_helpers import (
    attempt_request,
    environment,
    result_request,
    session_request,
    settings,
)

pytestmark = pytest.mark.usefixtures("esports_enabled")
PREFIX = "/api/v1/esports-assessments"


class _NoopLimiter:
    def record(self, account_id: UUID, operation_kind: str, now: datetime) -> None:
        del account_id, operation_kind, now


@pytest.fixture(autouse=True)
def no_rate_limit(db_client: TestClient):
    from app.esports_assessments.dependencies import get_assessment_write_limiter

    db_client.app.dependency_overrides[get_assessment_write_limiter] = lambda: _NoopLimiter()
    yield
    db_client.app.dependency_overrides.pop(get_assessment_write_limiter, None)


def _login(client: TestClient, platform_users, key: str = "self_student") -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _json(model) -> dict[str, object]:
    return model.model_dump(mode="json")


@pytest.mark.parametrize("suite_id", ["pc-fps", "mobile-fps", "pc-moba", "mobile-moba"])
def test_old_formal_http_session_returns_frozen_v1_config_after_latest_catalog_is_v3(
    db_client, platform_users, monkeypatch, suite_id
):
    from app.esports_assessments import catalog

    frozen = catalog.get_suite(suite_id, catalog_version=f"{suite_id}-v1")
    expected = frozen.model_dump(mode="json")
    headers = {"X-CSRF-Token": _login(db_client, platform_users)}
    with monkeypatch.context() as patch:
        patch.setattr(catalog, "get_suite", lambda _suite_id: frozen)
        created = db_client.post(
            f"{PREFIX}/sessions", json=_json(session_request(suite_id)), headers=headers
        )
    assert created.status_code == 201
    assert created.json()["suite_config"] == expected
    session_id = created.json()["id"]
    assert catalog.get_suite(suite_id).catalog_version == (
        "pc-fps-v5"
        if suite_id == "pc-fps"
        else "pc-moba-v5"
        if suite_id == "pc-moba"
        else f"{suite_id}-v3"
    )
    read = db_client.get(f"{PREFIX}/sessions/{session_id}")
    assert read.status_code == 200 and read.json()["suite_config"] == expected
    abandoned = db_client.post(
        f"{PREFIX}/sessions/{session_id}/abandon",
        json={"operation_key": str(uuid4())},
        headers=headers,
    )
    assert abandoned.status_code == 200
    assert abandoned.json()["suite_config"] == expected


@pytest.mark.parametrize(
    "suite_id,test_id",
    [
        ("pc-fps", "gridshot"),
        ("mobile-fps", "precision"),
        ("pc-moba", "last-hit"),
        ("mobile-moba", "dodge"),
    ],
)
def test_closed_suite_routes_are_actor_bound_for_actual_superadmin(
    db_client, platform_users, monkeypatch, suite_id, test_id
):
    from app.esports_assessments import catalog

    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())
    csrf = _login(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}
    context = db_client.get(f"{PREFIX}/context").json()
    suites = db_client.get(f"{PREFIX}/suites")
    assert suites.status_code == 200
    assert context["can_take"] and context["can_read_self"]
    assert context["available_suites"] == ["pc-fps", "mobile-fps", "pc-moba", "mobile-moba"]
    assert [item["suite_id"] for item in suites.json()["items"]] == context["available_suites"]
    created = db_client.post(
        f"{PREFIX}/sessions",
        json=_json(
            session_request(suite_id).model_copy(update={"attack_mode": "melee"})
            if suite_id == "pc-moba"
            else session_request(suite_id)
        ),
        headers=headers,
    )
    assert created.status_code == 201
    assert created.json()["suite_config"]["catalog_version"] == (
        "pc-fps-v5"
        if suite_id == "pc-fps"
        else "pc-moba-v5"
        if suite_id == "pc-moba"
        else f"{suite_id}-v3"
    )
    started = db_client.post(
        f"{PREFIX}/sessions/{created.json()['id']}/attempts",
        json=_json(attempt_request(suite_id, test_id)),
        headers=headers,
    )
    assert started.status_code == 201
    interrupted = db_client.post(
        f"{PREFIX}/attempts/{started.json()['id']}/interrupt",
        json={"operation_key": str(uuid4())},
        headers=headers,
    )
    assert interrupted.status_code == 200
    practice = PracticeCreate(
        suite_id=suite_id,
        attack_mode="melee" if suite_id == "pc-moba" else None,
        test_id=test_id,
        difficulty=3,
        settings=settings(suite_id),
        environment=environment(suite_id),
        operation_key=uuid4(),
    )
    assert (
        db_client.post(f"{PREFIX}/practice-runs", json=_json(practice), headers=headers).status_code
        == 201
    )
    _login(db_client, platform_users)
    assert db_client.get(f"{PREFIX}/context").json()["available_suites"] == []
    assert db_client.get(f"{PREFIX}/suites").json()["items"] == []


def test_formal_context_catalog_recovery_detail_and_expired_interrupt(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users)
    headers = {"X-CSRF-Token": csrf}

    context = db_client.get(f"{PREFIX}/context")
    suites = db_client.get(f"{PREFIX}/suites")
    assert context.status_code == suites.status_code == 200
    assert context.json()["available_suites"] == [
        "pc-fps",
        "mobile-fps",
        "pc-moba",
        "mobile-moba",
    ]
    assert [item["suite_id"] for item in suites.json()["items"]] == context.json()[
        "available_suites"
    ]

    created = db_client.post(
        f"{PREFIX}/sessions",
        json=_json(session_request()),
        headers=headers,
    )
    assert created.status_code == 201
    assert len(created.json()["members"]) == 3
    session_id = created.json()["id"]

    started = db_client.post(
        f"{PREFIX}/sessions/{session_id}/attempts",
        json=_json(attempt_request("pc-fps", "gridshot")),
        headers=headers,
    )
    assert started.status_code == 201
    attempt_id = started.json()["id"]

    recovered = db_client.get(f"{PREFIX}/recovery")
    refreshed = db_client.get(f"{PREFIX}/context")
    detail = db_client.get(f"{PREFIX}/attempts/{attempt_id}")
    session = db_client.get(f"{PREFIX}/sessions/{session_id}")
    assert recovered.status_code == refreshed.status_code == detail.status_code == 200
    assert recovered.json()["items"][0]["attempt_id"] == attempt_id
    assert refreshed.json()["active_run"]["attempt_id"] == attempt_id
    assert detail.json()["attempt"]["id"] == attempt_id
    assert detail.json()["saved"] is None
    assert session.json() == created.json()

    row = db.get(AssessmentAttempt, UUID(attempt_id))
    assert row is not None
    row.created_at = datetime.now(UTC) - timedelta(minutes=10)
    row.run_until = datetime.now(UTC) - timedelta(minutes=2)
    row.submit_until = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()
    expired = db_client.post(
        f"{PREFIX}/attempts/{attempt_id}/interrupt",
        json={"operation_key": str(uuid4())},
        headers=headers,
    )
    assert expired.status_code == 410
    assert expired.json()["code"] == "esports_expired"
    assert db.get(AssessmentAttempt, row.id).state == "expired"


def test_practice_submit_routes_never_create_formal_result_or_report(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.esports_assessments import router

    clock = [datetime(2026, 9, 23, 1, tzinfo=UTC)]
    monkeypatch.setattr(router, "_now", lambda: clock[0])
    csrf = _login(db_client, platform_users)
    headers = {"X-CSRF-Token": csrf}
    request = PracticeCreate(
        suite_id="pc-fps",
        test_id="gridshot",
        difficulty=4,
        settings=settings("pc-fps"),
        environment=environment("pc-fps"),
        operation_key=uuid4(),
    )
    started = db_client.post(f"{PREFIX}/practice-runs", json=_json(request), headers=headers)
    assert started.status_code == 201
    run = started.json()
    submit = result_request(
        "pc-fps",
        "gridshot",
        seed=run["seed"],
        catalog_version=run["config"]["catalog_version"],
    )
    payload = _json(submit)
    audits_before_early_retry = db.scalar(select(func.count()).select_from(AuditEvent))
    early = db_client.post(
        f"{PREFIX}/practice-runs/{run['id']}/result",
        json=payload,
        headers=headers,
    )
    assert early.status_code == 409
    assert early.json()["code"] == "esports_result_too_early"
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == audits_before_early_retry
    assert db.get(AssessmentPracticeRun, UUID(run["id"])).state == "running"
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 0

    clock[0] += timedelta(seconds=63)
    submitted = db_client.post(
        f"{PREFIX}/practice-runs/{run['id']}/result",
        json=payload,
        headers=headers,
    )
    assert submitted.status_code == 201
    assert submitted.json()["mode"] == "practice"
    assert submitted.json()["scored"]["score"] is None
    assert submitted.json()["session"] is None

    detail = db_client.get(f"{PREFIX}/practice-runs/{run['id']}")
    history = db_client.get(f"{PREFIX}/history", params={"mode": "practice"})
    assert detail.status_code == history.status_code == 200
    assert detail.json()["saved"] == submitted.json()
    assert history.json()["items"][0]["attempt_id"] == run["id"]
    assert db.scalar(select(func.count()).select_from(AssessmentPracticeRun)) == 1
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 0
    assert db.scalar(select(func.count()).select_from(AssessmentReport)) == 0


def test_write_routes_require_csrf_and_new_esports_permissions(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users)
    payload = _json(session_request())
    missing_csrf = db_client.post(f"{PREFIX}/sessions", json=payload)
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["code"] == "csrf_invalid"
    assert missing_csrf.headers["cache-control"] == "no-store, private"

    csrf = _login(db_client, platform_users, "customer_service")
    old_role = db_client.post(
        f"{PREFIX}/sessions",
        json=_json(session_request()),
        headers={"X-CSRF-Token": csrf},
    )
    assert old_role.status_code == 403
    assert old_role.json()["code"] == "esports_forbidden"


def test_practice_expired_interrupt_is_http_410(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users)
    headers = {"X-CSRF-Token": csrf}
    request = PracticeCreate(
        suite_id="pc-moba",
        attack_mode="melee",
        test_id="last-hit",
        difficulty=3,
        settings=settings("pc-moba"),
        environment=environment("pc-moba"),
        operation_key=uuid4(),
    )
    started = db_client.post(f"{PREFIX}/practice-runs", json=_json(request), headers=headers)
    assert started.status_code == 201
    row = db.get(AssessmentPracticeRun, UUID(started.json()["id"]))
    assert row is not None
    row.created_at = datetime.now(UTC) - timedelta(minutes=10)
    row.run_until = datetime.now(UTC) - timedelta(minutes=2)
    row.submit_until = datetime.now(UTC) - timedelta(minutes=1)
    db.flush()

    response = db_client.post(
        f"{PREFIX}/practice-runs/{row.id}/interrupt",
        json={"operation_key": str(uuid4())},
        headers=headers,
    )
    assert response.status_code == 410
    assert response.json()["code"] == "esports_expired"
    assert row.state == "expired"


def test_revoked_permission_precedes_budget_and_business_mutation(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    from app.esports_assessments.dependencies import get_assessment_write_limiter

    csrf = _login(db_client, platform_users)
    headers = {"X-CSRF-Token": csrf}
    created = db_client.post(f"{PREFIX}/sessions", json=_json(session_request()), headers=headers)
    started = db_client.post(
        f"{PREFIX}/sessions/{created.json()['id']}/attempts",
        json=_json(attempt_request("pc-fps", "gridshot")),
        headers=headers,
    )
    account = platform_users["accounts"]["self_student"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["esports_assessments.read"].id,
            effect="deny",
            scope_kind="self",
        )
    )
    db.commit()

    class SpyLimiter:
        calls = 0

        def record(self, account_id: UUID, operation_kind: str, now: datetime) -> None:
            del account_id, operation_kind, now
            self.calls += 1

    limiter = SpyLimiter()
    db_client.app.dependency_overrides[get_assessment_write_limiter] = lambda: limiter
    before = (
        db.scalar(select(func.count()).select_from(AssessmentResult)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    )
    response = db_client.post(
        f"{PREFIX}/attempts/{started.json()['id']}/result",
        json=_json(
            result_request(
                "pc-fps",
                "gridshot",
                seed=started.json()["seed"],
                catalog_version=started.json()["config"]["catalog_version"],
            )
        ),
        headers=headers,
    )
    assert response.status_code == 403
    assert response.json()["code"] == "esports_forbidden"
    assert limiter.calls == 0
    assert (
        db.scalar(select(func.count()).select_from(AssessmentResult)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    ) == before
    assert db.get(AssessmentAttempt, UUID(started.json()["id"])).state == "running"


def test_rate_limit_response_is_private_and_carries_retry_after(
    db_client: TestClient,
    platform_users,
) -> None:
    from app.esports_assessments.dependencies import get_assessment_write_limiter

    class RejectingLimiter:
        def record(self, account_id: UUID, operation_kind: str, now: datetime) -> None:
            del account_id, operation_kind, now
            raise AssessmentRateLimited(37)

    db_client.app.dependency_overrides[get_assessment_write_limiter] = RejectingLimiter
    csrf = _login(db_client, platform_users)
    response = db_client.post(
        f"{PREFIX}/sessions",
        json=_json(session_request()),
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 429
    assert response.json()["code"] == "esports_rate_limited"
    assert response.headers["retry-after"] == "37"
    assert response.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "0"},
        {"date_from": "2026-09-22T00:00:00"},
        {"date_from": "2026-09-23", "date_to": "2026-09-22"},
    ],
)
def test_report_dates_reject_non_iso_and_reversed_ranges_before_query_or_write(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
    params: dict[str, str],
) -> None:
    from app.esports_assessments.queries import AssessmentQueries

    _login(db_client, platform_users)
    before = (
        db.scalar(select(func.count()).select_from(AssessmentReport)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    )

    def unexpected_query(*args, **kwargs):
        del args, kwargs
        raise AssertionError("invalid report dates reached the report query")

    monkeypatch.setattr(AssessmentQueries, "reports", unexpected_query)
    response = db_client.get(f"{PREFIX}/reports", params=params)

    assert response.status_code == 422
    assert response.json() == {
        "type": "about:blank",
        "title": "Request Error",
        "status": 422,
        "detail": "评测数据不符合当前规则，请重新完成本项",
        "code": "esports_invalid_data",
    }
    assert response.headers["cache-control"] == "no-store, private"
    assert (
        db.scalar(select(func.count()).select_from(AssessmentReport)),
        db.scalar(select(func.count()).select_from(AuditEvent)),
    ) == before


def test_report_dates_accept_exact_iso_wire_format(
    db_client: TestClient,
    platform_users,
) -> None:
    _login(db_client, platform_users)
    response = db_client.get(
        f"{PREFIX}/reports",
        params={"date_from": "2026-09-22", "date_to": "2026-09-22"},
    )
    assert response.status_code == 200
    assert response.json() == {"items": [], "next_cursor": None, "visible_total": 0}
