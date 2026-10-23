import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.orm import sessionmaker

from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.models import AuthRateLimit, AuthSession
from app.talent_tests.catalog import CURRENT_CATALOG_VERSION, LEGACY_TEST_IDS, TEST_IDS
from app.talent_tests.models import TalentTestReport, TalentTestResult, TalentTestSession
from app.talent_tests.rate_limit import TalentWriteLimiter
from tests.services.talent_helpers import seed_legacy_session, submission
from tests.services.test_talent_access import grant, talent_people  # noqa: F401
from tests.unit.talent_test_helpers import valid_payload

PREFIX = "/api/v1/talent-tests"


def test_known_endpoints_require_authentication(client):
    for path in (
        "/context",
        "/sessions",
        f"/sessions/{uuid4()}",
        "/subjects",
        "/reports",
        f"/reports/{uuid4()}",
    ):
        response = client.get(PREFIX + path)
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store, private"
    assert start(client).status_code == 401


@pytest.fixture
def talent_http(db_client, db, postgres_engine, talent_people):  # noqa: F811
    from app.talent_tests.router import get_talent_write_limiter

    _, actors, _ = talent_people
    factory = sessionmaker(bind=postgres_engine)
    db_client.app.dependency_overrides[get_talent_write_limiter] = lambda: TalentWriteLimiter(
        factory, bytes(range(32))
    )

    def sign_in(role="student"):
        token, csrf = uuid4().hex, uuid4().hex
        digest = lambda value: hmac.new(
            bytes(range(32)), value.encode(), hashlib.sha256
        ).hexdigest()
        db.add(
            AuthSession(
                user_id=actors[role].account.id,
                token_digest=digest(token),
                csrf_digest=digest(csrf),
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        db.commit()
        db_client.cookies.set("erp_session", token)
        db_client.headers["X-CSRF-Token"] = csrf
        return actors[role]

    sign_in()
    yield db_client, sign_in, actors, factory
    keys = [
        hmac.new(
            bytes(range(32)), f"talent-write\0{actor.account.id}".encode(), hashlib.sha256
        ).hexdigest()
        for actor in actors.values()
    ]
    with factory.begin() as cleanup:
        cleanup.execute(delete(AuthRateLimit).where(AuthRateLimit.key_digest.in_(keys)))


def start(client, key=None, catalog=CURRENT_CATALOG_VERSION):
    return client.post(
        PREFIX + "/sessions", json={"request_id": str(key or uuid4()), "catalog_version": catalog}
    )


def counts(db):
    return [
        db.scalar(select(func.count()).select_from(table))
        for table in (TalentTestSession, TalentTestResult, TalentTestReport, AuditEvent)
    ]


def attempts(factory, actor):
    digest = hmac.new(
        bytes(range(32)), f"talent-write\0{actor.account.id}".encode(), hashlib.sha256
    ).hexdigest()
    with factory() as db:
        row = db.get(AuthRateLimit, digest)
        return row.attempts if row else 0


def test_nine_endpoints_replay_completion_and_summary_privacy(talent_http):
    client, _, _, _ = talent_http
    context = client.get(PREFIX + "/context").json()
    assert context["can_take"] and context["can_read_self"]
    key = uuid4()
    first = start(client, key)
    assert first.status_code == 201
    session_id = first.json()["id"]
    assert start(client, key).status_code == 200
    assert start(client).status_code == 409
    for test_id in context["catalog"]["test_ids"]:
        payload = valid_payload(test_id)
        payload["submission_id"] = str(uuid4())
        result = client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload)
        assert result.status_code == 201, result.text
        assert (
            client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload).status_code == 200
        )
    session = client.get(f"{PREFIX}/sessions/{session_id}").json()
    assert session["status"] == "completed"
    report_id = session["report_id"]
    assert client.get(f"{PREFIX}/reports/{report_id}").json()["session_id"] == session_id
    for path in ("/sessions", "/reports", "/subjects"):
        response = client.get(PREFIX + path)
        assert response.status_code == 200
        assert response.json()["total"] == 1
        if path == "/sessions":
            assert response.json()["items"][0]["required_count"] == 8
        assert "raw" not in response.text
    second = start(client).json()["id"]
    abandon_key = str(uuid4())
    abandoned = client.post(f"{PREFIX}/sessions/{second}/abandon", json={"request_id": abandon_key})
    assert abandoned.status_code == 200 and abandoned.json()["status"] == "abandoned"


@pytest.mark.parametrize("terminal", ["abandoned", "completed"])
def test_http_legacy_key_replays_but_fresh_v1_is_rejected_without_consuming_key(
    talent_http,
    db,
    terminal,
):
    from app.talent_tests.service import TalentTestService

    client, _, actors, _ = talent_http
    actor = actors["student"]
    old_key = uuid4()
    legacy = seed_legacy_session(db, actor, old_key, datetime.now(UTC))
    service = TalentTestService(db)
    if terminal == "abandoned":
        service.abandon(
            actor=actor,
            session_id=legacy.id,
            request_id=uuid4(),
            now=datetime.now(UTC),
        )
    else:
        for test_id in LEGACY_TEST_IDS:
            service.submit(
                actor=actor,
                session_id=legacy.id,
                value=submission(test_id),
                now=datetime.now(UTC),
            )

    current = start(client)
    assert current.status_code == 201
    replay = start(client, old_key, catalog="talent-training-v1")
    assert replay.status_code == 200
    assert replay.json()["id"] == str(legacy.id)
    assert replay.json()["status"] == terminal
    required = {
        item["id"]: item["required_count"]
        for item in client.get(PREFIX + "/sessions").json()["items"]
    }
    assert required[str(legacy.id)] == 7
    assert required[current.json()["id"]] == 8

    rejected_key = uuid4()
    before = counts(db)
    rejected = start(client, rejected_key, catalog="talent-training-v1")
    assert rejected.status_code == 409
    assert rejected.headers["cache-control"] == "no-store, private"
    assert rejected.json() == {
        "code": "talent_catalog_updated",
        "detail": "测试目录已更新，请刷新页面后开始新一轮",
    }
    assert counts(db) == before

    abandoned = client.post(
        f"{PREFIX}/sessions/{current.json()['id']}/abandon",
        json={"request_id": str(uuid4())},
    )
    assert abandoned.status_code == 200
    accepted = start(client, rejected_key)
    assert accepted.status_code == 201


def test_protocol_failures_are_private_and_write_nothing(talent_http, db):
    client, _, _, _ = talent_http
    session_id = start(client).json()["id"]
    before = counts(db)
    payload = valid_payload("reactiontime")
    payload["test_id"] = "typing"
    response = client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload)
    assert response.status_code == 422
    payload = valid_payload("reactiontime")
    payload["raw"]["samples_ms"][0] = -1
    assert client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload).status_code == 422
    assert counts(db) == before
    for path in (
        "/sessions/not-a-uuid",
        "/reports?person_id=private-input",
        "/reports?page=0",
        "/subjects?page_size=101",
        "/subjects?name=" + "x" * 81,
    ):
        response = client.get(PREFIX + path)
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store, private"
        assert "private-input" not in response.text


def test_visual_impossible_misses_422_does_not_consume_submission_key(talent_http, db):
    client, _, _, _ = talent_http
    session_id = start(client).json()["id"]
    before = counts(db)
    payload = valid_payload("memory")
    payload["raw"]["accepted_misses"] = 10
    endpoint = f"{PREFIX}/sessions/{session_id}/results"
    rejected = client.post(endpoint, json=payload)
    assert rejected.status_code == 422
    assert rejected.headers["cache-control"] == "no-store, private"
    assert counts(db) == before
    payload["raw"]["accepted_misses"] = 9
    saved = client.post(endpoint, json=payload)
    assert saved.status_code == 201
    assert saved.json()["result"]["submission_id"] == payload["submission_id"]
    assert len(saved.json()["session"]["results"]) == 1
    assert counts(db) == [before[0], before[1] + 1, before[2], before[3]]


def test_report_detail_and_person_filter_private_404_equivalence_without_writes(talent_http, db):
    client, sign_in, actors, _ = talent_http
    session_id = start(client).json()["id"]
    for test_id in TEST_IDS:
        payload = valid_payload(test_id)
        payload["submission_id"] = str(uuid4())
        saved = client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload)
        assert saved.status_code == 201
    report_id = saved.json()["session"]["report_id"]
    person_id = str(actors["student"].person.id)
    sign_in("admin")
    before = counts(db)
    for existing, missing in (
        (f"/reports/{report_id}", f"/reports/{uuid4()}"),
        (f"/reports?person_id={person_id}", f"/reports?person_id={uuid4()}"),
    ):
        denied = client.get(PREFIX + existing)
        absent = client.get(PREFIX + missing)
        assert denied.status_code == absent.status_code == 404
        assert denied.content == absent.content
        assert dict(denied.headers) == dict(absent.headers)
        assert denied.headers["cache-control"] == "no-store, private"
    assert counts(db) == before


def test_auth_csrf_permissions_precede_budget_and_reads_do_not_consume(talent_http, db):
    client, sign_in, actors, factory = talent_http
    client.cookies.clear()
    assert start(client).status_code == 401
    sign_in()
    csrf = client.headers.pop("X-CSRF-Token")
    assert start(client).status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert attempts(factory, actors["student"]) == 0
    for _ in range(31):
        assert client.get(PREFIX + "/context").status_code == 200
    assert attempts(factory, actors["student"]) == 0
    grant(db, actors["student"], effect="deny", scope="self")
    before = counts(db)
    assert start(client).status_code == 403
    assert counts(db) == before
    assert attempts(factory, actors["student"]) == 0


def test_429_has_retry_after_business_rejections_count_and_other_accounts_do_not_share(
    talent_http, db
):
    client, sign_in, actors, factory = talent_http
    for _ in range(30):
        response = start(client, catalog="unknown-version")
        assert response.status_code == 422
    response = start(client)
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert response.headers["cache-control"] == "no-store, private"
    assert attempts(factory, actors["student"]) == 30
    grant(db, actors["other"], "talent_tests.take", scope="self")
    grant(db, actors["other"], scope="self")
    sign_in("other")
    assert start(client).status_code == 201
    assert attempts(factory, actors["other"]) == 1


def test_invisible_existing_and_random_objects_have_identical_private_errors(talent_http, db):
    client, sign_in, _, _ = talent_http
    session_id = start(client).json()["id"]
    sign_in("admin")
    before = counts(db)
    actual = client.get(f"{PREFIX}/sessions/{session_id}")
    missing = client.get(f"{PREFIX}/sessions/{uuid4()}")
    assert actual.status_code == missing.status_code == 404
    assert actual.content == missing.content
    assert dict(actual.headers) == dict(missing.headers)
    assert counts(db) == before


@pytest.mark.parametrize("operation", ["start", "abandon", "complete"])
def test_audit_failure_rolls_back_business_but_preserves_budget(
    talent_http,
    db,
    monkeypatch,
    operation,
):
    client, _, actors, factory = talent_http
    session_id = None
    if operation != "start":
        session_id = start(client).json()["id"]
    if operation == "complete":
        for test_id in TEST_IDS[:-1]:
            payload = valid_payload(test_id)
            payload["submission_id"] = str(uuid4())
            assert (
                client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload).status_code
                == 201
            )
    before = counts(db)
    budget_before = attempts(factory, actors["student"])

    def fail(*args, **kwargs):
        raise RuntimeError("private raw must not leak")

    monkeypatch.setattr(AuditService, "append", fail)
    if operation == "start":
        response = start(client)
    elif operation == "abandon":
        response = client.post(
            f"{PREFIX}/sessions/{session_id}/abandon", json={"request_id": str(uuid4())}
        )
    else:
        response = client.post(
            f"{PREFIX}/sessions/{session_id}/results",
            json=valid_payload(TEST_IDS[-1]),
        )
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store, private"
    assert "private raw" not in response.text
    assert counts(db) == before
    assert attempts(factory, actors["student"]) == budget_before + 1


@pytest.mark.parametrize("change", ["disabled", "password", "force_password_change"])
def test_existing_session_lifecycle_rules_are_enforced(talent_http, db, change):
    client, _, actors, factory = talent_http
    actor = actors["student"]
    if change == "disabled":
        actor.account.status = "disabled"
    elif change == "force_password_change":
        actor.account.force_password_change = True
    else:
        # Existing password-update flow revokes sessions; module must honor revocation.
        for session in db.scalars(
            select(AuthSession).where(AuthSession.user_id == actor.account.id)
        ):
            session.revoked_at = datetime.now(UTC)
    db.commit()
    assert start(client).status_code == (403 if change == "force_password_change" else 401)
    assert attempts(factory, actor) == 0


def test_revoked_permission_blocks_successful_write_replay(talent_http, db):
    client, _, actors, factory = talent_http
    key = uuid4()
    legacy = seed_legacy_session(db, actors["student"], key, datetime.now(UTC))
    assert legacy.rules.catalog_version == "talent-training-v1"
    grant(db, actors["student"], effect="deny", scope="self")
    assert start(client, key, catalog="talent-training-v1").status_code == 403
    assert attempts(factory, actors["student"]) == 0
