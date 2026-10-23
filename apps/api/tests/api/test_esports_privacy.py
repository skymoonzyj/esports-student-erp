from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.types import Message, Receive, Scope, Send

from app.audit.models import AuditEvent
from app.esports_assessments.formal_service import FormalAssessmentService
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentReport,
    AssessmentResult,
    AssessmentSession,
)
from app.esports_assessments.practice_service import PracticeAssessmentService
from app.esports_assessments.schemas import PracticeCreate
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.esports_helpers import (
    attempt_request,
    environment,
    reload_actor,
    result_request,
    session_request,
    settings,
    student_actor,
)

pytestmark = pytest.mark.usefixtures("esports_enabled")
PREFIX = "/api/v1/esports-assessments"
NOW = datetime(2026, 9, 22, 6, tzinfo=UTC)


def _assessment_state(db: Session) -> tuple[int, ...]:
    return tuple(
        db.scalar(select(func.count()).select_from(table)) or 0
        for table in (
            AssessmentSession,
            AssessmentAttempt,
            AssessmentResult,
            AssessmentReport,
            AssessmentPracticeRun,
            AuditEvent,
        )
    )


def _login(client: TestClient, platform_users, key: str = "self_student") -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


@pytest.mark.parametrize("status", [400, 404, 422])
def test_private_middleware_replaces_payload_bearing_errors(status: int) -> None:
    from app.esports_assessments.http_support import (
        PrivateEsportsAssessmentResponseMiddleware,
    )

    app = FastAPI()

    @app.post("/api/v1/esports-assessments/probe")
    async def probe() -> JSONResponse:
        return JSONResponse(
            {"detail": "raw-sentinel", "errors": [{"input": "secret-answer"}]},
            status_code=status,
        )

    app.add_middleware(PrivateEsportsAssessmentResponseMiddleware)
    with TestClient(app) as client:
        response = client.post("/api/v1/esports-assessments/probe", content=b"{}")
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert "raw-sentinel" not in response.text
    assert "secret-answer" not in response.text


def test_private_middleware_sanitizes_unhandled_exception() -> None:
    from app.esports_assessments.http_support import (
        PrivateEsportsAssessmentResponseMiddleware,
    )

    app = FastAPI()

    @app.get("/api/v1/esports-assessments/probe")
    async def probe() -> None:
        raise RuntimeError("database-secret-sentinel")

    app.add_middleware(PrivateEsportsAssessmentResponseMiddleware)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/esports-assessments/probe")
    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store, private"
    assert "database-secret-sentinel" not in response.text


def test_stream_limit_counts_actual_bytes_and_retains_at_most_limit_plus_one() -> None:
    from app.esports_assessments.http_support import (
        PrivateEsportsAssessmentResponseMiddleware,
    )

    called = False
    messages: list[Message] = []
    retained: list[int] = []

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal called
        called = True
        request = await receive()
        retained.append(len(request.get("body", b"")))
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    chunks = [b"x" * 4096 for _ in range(17)]

    async def receive() -> Message:
        chunk = chunks.pop(0)
        return {"type": "http.request", "body": chunk, "more_body": bool(chunks)}

    async def send(message: Message) -> None:
        messages.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/v1/esports-assessments/probe",
        "raw_path": b"/api/v1/esports-assessments/probe",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-length", b"1")],
        "client": ("test", 1),
        "server": ("test", 80),
    }
    asyncio.run(PrivateEsportsAssessmentResponseMiddleware(downstream)(scope, receive, send))
    assert called is False
    assert retained == []
    assert (
        next(message["status"] for message in messages if message["type"] == "http.response.start")
        == 413
    )
    assert sum(len(message.get("body", b"")) for message in messages) < 65537


def test_exact_limit_replays_body_and_neighbor_prefix_is_not_intercepted() -> None:
    from app.esports_assessments.http_support import (
        PrivateEsportsAssessmentResponseMiddleware,
    )

    async def run(path: str, size: int) -> tuple[int, list[Message]]:
        seen = 0
        sent: list[Message] = []

        async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
            nonlocal seen
            message = await receive()
            seen = len(message.get("body", b""))
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

        delivered = False

        async def receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": b"x" * size, "more_body": False}

        async def send(message: Message) -> None:
            sent.append(message)

        scope: Scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
        }
        await PrivateEsportsAssessmentResponseMiddleware(downstream)(scope, receive, send)
        return seen, sent

    exact, exact_messages = asyncio.run(run(f"{PREFIX}/probe", 65536))
    neighbor, neighbor_messages = asyncio.run(run(f"{PREFIX}-admin/probe", 65537))
    assert exact == 65536
    assert neighbor == 65537
    start = next(m for m in exact_messages if m["type"] == "http.response.start")
    assert (b"cache-control", b"no-store, private") in start["headers"]
    neighbor_start = next(m for m in neighbor_messages if m["type"] == "http.response.start")
    assert (b"cache-control", b"no-store, private") not in neighbor_start["headers"]


def test_unknown_prefix_path_is_fixed_private_404(db_client: TestClient) -> None:
    response = db_client.get(f"{PREFIX}/does-not-exist/raw-secret")
    assert response.status_code == 404
    assert response.json()["code"] == "esports_not_found"
    assert response.headers["cache-control"] == "no-store, private"
    assert "does-not-exist" not in response.text


def test_unknown_and_foreign_attempts_are_identical_and_gets_do_not_write(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    other = student_actor(db)
    service = FormalAssessmentService(db)
    session = service.create(other, session_request(), NOW)
    attempt = service.start_attempt(
        other,
        session.id,
        attempt_request("pc-fps", "precision"),
        NOW,
    )
    _login(db_client, platform_users)
    tables = (
        AssessmentSession,
        AssessmentAttempt,
        AssessmentResult,
        AssessmentReport,
        AssessmentPracticeRun,
        AuditEvent,
    )
    before = tuple(db.scalar(select(func.count()).select_from(table)) for table in tables)

    foreign = db_client.get(f"{PREFIX}/attempts/{attempt.id}")
    missing = db_client.get(f"{PREFIX}/attempts/{uuid4()}")
    recovery = db_client.get(f"{PREFIX}/recovery")

    assert foreign.status_code == missing.status_code == 404
    assert foreign.json() == missing.json()
    assert (
        foreign.headers["cache-control"]
        == missing.headers["cache-control"]
        == ("no-store, private")
    )
    assert recovery.status_code == 200
    assert recovery.json() == {"items": [], "next_cursor": None, "visible_total": 0}
    after = tuple(db.scalar(select(func.count()).select_from(table)) for table in tables)
    assert after == before


def test_unknown_and_foreign_completed_session_and_report_are_private_without_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    other = student_actor(db)
    service = FormalAssessmentService(db)
    session = service.create(other, session_request(), NOW)
    saved = None
    for index, test_id in enumerate(("precision", "tracking", "switching", "recognition")):
        started_at = NOW + timedelta(seconds=index * 90)
        attempt = service.start_attempt(
            other,
            session.id,
            attempt_request("pc-fps", test_id),
            started_at,
        )
        saved = service.submit(
            other,
            attempt.id,
            result_request(
                "pc-fps",
                test_id,
                seed=attempt.seed,
                catalog_version=attempt.config.catalog_version,
            ),
            started_at + timedelta(seconds=63),
        )
    assert saved is not None and saved.session.report_id is not None
    report_id = saved.session.report_id

    _login(db_client, platform_users)
    before = _assessment_state(db)
    responses = (
        db_client.get(f"{PREFIX}/sessions/{session.id}"),
        db_client.get(f"{PREFIX}/sessions/{uuid4()}"),
        db_client.get(f"{PREFIX}/reports/{report_id}"),
        db_client.get(f"{PREFIX}/reports/{uuid4()}"),
    )
    assert all(response.status_code == 404 for response in responses)
    assert len({response.text for response in responses}) == 1
    assert all(response.json()["code"] == "esports_not_found" for response in responses)
    assert _assessment_state(db) == before

    _login(db_client, platform_users, "admin")
    before_admin_reads = _assessment_state(db)
    assert db_client.get(f"{PREFIX}/sessions/{session.id}").status_code == 200
    assert db_client.get(f"{PREFIX}/reports/{report_id}").status_code == 200
    assert _assessment_state(db) == before_admin_reads


def test_related_guardian_cannot_read_partial_runs_but_global_superadmin_can(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    account_id = platform_users["accounts"]["minor_student"].id
    actor = reload_actor(db, account_id)
    formal = FormalAssessmentService(db)
    session = formal.create(actor, session_request(), NOW)
    attempt = formal.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "precision"),
        NOW,
    )
    formal.interrupt(actor, attempt.id, uuid4(), NOW + timedelta(seconds=1))
    practice = PracticeAssessmentService(db).start(
        actor,
        PracticeCreate(
            suite_id="pc-moba",
            test_id="last-hit",
            difficulty=3,
            settings=settings("pc-moba"),
            environment=environment("pc-moba"),
            operation_key=uuid4(),
        ),
        NOW + timedelta(seconds=2),
    )

    _login(db_client, platform_users, "guardian")
    before_guardian_reads = _assessment_state(db)
    guardian_responses = (
        db_client.get(f"{PREFIX}/sessions/{session.id}"),
        db_client.get(f"{PREFIX}/attempts/{attempt.id}"),
        db_client.get(f"{PREFIX}/practice-runs/{practice.id}"),
    )
    assert all(response.status_code == 404 for response in guardian_responses)
    assert len({response.text for response in guardian_responses}) == 1
    assert _assessment_state(db) == before_guardian_reads

    _login(db_client, platform_users, "admin")
    before_admin_reads = _assessment_state(db)
    assert db_client.get(f"{PREFIX}/sessions/{session.id}").status_code == 200
    assert db_client.get(f"{PREFIX}/attempts/{attempt.id}").status_code == 200
    assert db_client.get(f"{PREFIX}/practice-runs/{practice.id}").status_code == 200
    assert _assessment_state(db) == before_admin_reads


def test_live_read_revocation_removes_recovery_history_detail_and_replay(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    account = platform_users["accounts"]["self_student"]
    actor = reload_actor(db, account.id)
    service = FormalAssessmentService(db)
    session = service.create(actor, session_request(), NOW)
    saved_attempt = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "precision"),
        NOW,
    )
    saved_request = result_request(
        "pc-fps",
        "precision",
        seed=saved_attempt.seed,
        catalog_version=saved_attempt.config.catalog_version,
    )
    saved = service.submit(
        actor,
        saved_attempt.id,
        saved_request,
        NOW + timedelta(seconds=63),
    )
    pending = service.start_attempt(
        actor,
        session.id,
        attempt_request("pc-fps", "tracking"),
        NOW + timedelta(seconds=90),
    )

    csrf = _login(db_client, platform_users)
    assert db_client.get(f"{PREFIX}/recovery").status_code == 200
    assert db_client.get(f"{PREFIX}/history", params={"mode": "formal"}).status_code == 200
    assert db_client.get(f"{PREFIX}/attempts/{saved_attempt.id}").status_code == 200
    assert db_client.get(f"{PREFIX}/attempts/{pending.id}").status_code == 200

    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["esports_assessments.read"].id,
            effect="deny",
            scope_kind="self",
            grant_reason="实时撤销验证",
        )
    )
    db.commit()
    before_denied_requests = _assessment_state(db)

    context = db_client.get(f"{PREFIX}/context")
    denied_reads = (
        db_client.get(f"{PREFIX}/sessions/{session.id}"),
        db_client.get(f"{PREFIX}/recovery"),
        db_client.get(f"{PREFIX}/history", params={"mode": "formal"}),
        db_client.get(f"{PREFIX}/attempts/{saved_attempt.id}"),
        db_client.get(f"{PREFIX}/attempts/{pending.id}"),
    )
    replay = db_client.post(
        f"{PREFIX}/attempts/{saved_attempt.id}/result",
        json=saved_request.model_dump(mode="json"),
        headers={"X-CSRF-Token": csrf},
    )

    assert context.status_code == 200
    assert context.json()["can_take"] is False
    assert context.json()["can_read_self"] is False
    assert context.json()["own_in_progress_sessions"] == []
    assert context.json()["active_run"] is None
    assert all(response.status_code == 404 for response in denied_reads)
    assert len({response.text for response in denied_reads}) == 1
    assert replay.status_code == 403
    assert replay.json()["code"] == "esports_forbidden"
    assert _assessment_state(db) == before_denied_requests
    assert (
        db.scalar(
            select(func.count())
            .select_from(AssessmentResult)
            .where(AssessmentResult.id == saved.result_id)
        )
        == 1
    )
