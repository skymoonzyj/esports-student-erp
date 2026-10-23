import asyncio
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.talent_tests.router import PrivateTalentResponseMiddleware
from tests.api.test_talent_routes import PREFIX, counts, start, talent_http  # noqa: F401
from tests.services.test_talent_access import talent_people  # noqa: F401
from tests.unit.talent_test_helpers import valid_payload


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 413, 422, 429, 500])
def test_every_module_response_is_private_and_errors_are_sanitized(status):
    app = FastAPI()
    app.add_middleware(PrivateTalentResponseMiddleware)

    @app.get("/api/v1/talent-tests/example")
    def example():
        if status == 500:
            raise RuntimeError("private payload must not leak")
        if status != 200:
            raise HTTPException(status, "private payload must not leak")
        return {"ok": True}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/talent-tests/example")
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store, private"
    if status in (400, 404, 422, 500):
        assert "private payload" not in response.text
        assert any("\u4e00" <= character <= "\u9fff" for character in response.json()["detail"])


def test_actual_stream_limit_rejects_before_downstream_and_stops_receiving():
    consumed = 0
    called = False
    messages = []

    async def downstream(scope, receive, send):
        nonlocal called
        called = True

    async def receive():
        nonlocal consumed
        consumed += 1
        return {"type": "http.request", "body": b"x" * 4096, "more_body": True}

    async def send(message):
        messages.append(message)

    asyncio.run(
        PrivateTalentResponseMiddleware(downstream)(
            {
                "type": "http",
                "path": "/api/v1/talent-tests/sessions",
                "method": "POST",
                "headers": [(b"content-length", b"1")],
            },
            receive,
            send,
        )
    )
    assert not called
    assert consumed == 17
    assert messages[0]["status"] == 413
    assert (b"cache-control", b"no-store, private") in messages[0]["headers"]


def test_body_at_limit_is_replayed_intact_without_limiting_neighbor_prefix():
    captured = []

    async def downstream(scope, receive, send):
        captured.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def run(path, body):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            pass

        await PrivateTalentResponseMiddleware(downstream)(
            {"type": "http", "path": path, "method": "POST", "headers": []}, receive, send
        )

    asyncio.run(run("/api/v1/talent-tests/sessions", b"x" * 65536))
    asyncio.run(run("/api/v1/talent-tests-other", b"x" * 65537))
    assert len(captured[0]["body"]) == 65536
    assert len(captured[1]["body"]) == 65537


def test_validation_never_echoes_input_or_untrusted_field_names():
    app = FastAPI()
    app.add_middleware(PrivateTalentResponseMiddleware)

    class Input(BaseModel):
        number: int

    @app.post("/api/v1/talent-tests/example")
    def example(payload: Input):
        return payload

    with TestClient(app) as client:
        response = client.post("/api/v1/talent-tests/example", json={"number": "SECRET_RAW"})
    assert response.status_code == 422
    assert "SECRET_RAW" not in response.text
    assert response.json()["detail"] == "测试数据不符合当前规则，请重新完成本项"


def test_real_app_unknown_module_path_is_private(client):
    response = client.get("/api/v1/talent-tests/unknown")
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store, private"
    assert response.json()["detail"] == "未找到可访问的测评记录"


def test_v2_dynamic_answers_are_private_404_equivalent_and_read_only(
    talent_http,  # noqa: F811
    db,
):
    client, sign_in, _, _ = talent_http
    session_id = start(client).json()["id"]
    payload = valid_payload("dynamic-vision")
    payload["submission_id"] = str(uuid4())
    payload["raw"]["outcomes"] = [
        "correct",
        "incorrect",
        "skipped",
        "correct",
        "incorrect",
    ]
    payload["raw"]["measured_display_ms"] = [91, 92, 93, 94, 95]
    payload["duration_ms"] = 465
    saved = client.post(f"{PREFIX}/sessions/{session_id}/results", json=payload)
    assert saved.status_code == 201
    assert saved.json()["result"]["test_id"] == "dynamic-vision"

    sign_in("admin")
    before = counts(db)
    denied = client.get(f"{PREFIX}/sessions/{session_id}")
    absent = client.get(f"{PREFIX}/sessions/{uuid4()}")

    assert denied.status_code == absent.status_code == 404
    assert denied.content == absent.content
    assert dict(denied.headers) == dict(absent.headers)
    assert denied.headers["cache-control"] == "no-store, private"
    for private_value in ("dynamic-vision", "correct", "91", "95"):
        assert private_value not in denied.text
    assert counts(db) == before
