import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.talent_tests.router import PrivateTalentResponseMiddleware
from tests.api.test_talent_routes import PREFIX, counts, start, talent_http  # noqa: F401
from tests.services.talent_helpers import seed_legacy_session
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


def frozen_round(client, db, actor, catalog_version):
    if catalog_version == "talent-training-v2":
        return seed_legacy_session(
            db, actor, uuid4(), datetime.now(UTC), catalog_version=catalog_version
        ).model_dump(mode="json")
    response = start(client, catalog=catalog_version)
    assert response.status_code == 201
    return response.json()


@pytest.mark.parametrize("catalog_version", ["talent-training-v2", "talent-training-v3"])
def test_versioned_dynamic_answers_are_private_404_equivalent_and_read_only(
    talent_http,  # noqa: F811
    db,
    catalog_version,
):
    client, sign_in, actors, _ = talent_http
    session = frozen_round(client, db, actors["student"], catalog_version)
    session_id = session["id"]
    payload = valid_payload("dynamic-vision")
    payload["engine_version"] = session["rules"]["tests"]["dynamic-vision"]["engine_version"]
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


@pytest.mark.parametrize(
    "catalog_version,wrong_engine,correct_engine",
    [
        ("talent-training-v2", "dynamic-vision-v2", "dynamic-vision-v1"),
        ("talent-training-v3", "dynamic-vision-v1", "dynamic-vision-v2"),
    ],
)
def test_wrong_dynamic_engine_is_private_422_without_writes_or_reserving_submission_key(
    talent_http,  # noqa: F811
    db,
    catalog_version,
    wrong_engine,
    correct_engine,
):
    client, _, actors, _ = talent_http
    session = frozen_round(client, db, actors["student"], catalog_version)
    endpoint = f"{PREFIX}/sessions/{session['id']}/results"
    payload = valid_payload("dynamic-vision")
    payload["engine_version"] = wrong_engine
    payload["submission_id"] = str(uuid4())
    before = counts(db)

    rejected = client.post(endpoint, json=payload)
    assert rejected.status_code == 422
    assert rejected.headers["cache-control"] == "no-store, private"
    assert rejected.json() == {
        "status": 422,
        "code": "talent_invalid_data",
        "detail": "测试数据不符合当前规则，请重新完成本项",
    }
    assert wrong_engine not in rejected.text
    assert counts(db) == before
    assert client.get(f"{PREFIX}/sessions/{session['id']}").json()["results"] == []

    payload["engine_version"] = correct_engine
    accepted = client.post(endpoint, json=payload)
    assert accepted.status_code == 201
    assert accepted.json()["result"]["engine_version"] == correct_engine
    assert accepted.json()["result"]["submission_id"] == payload["submission_id"]
    assert counts(db) == [before[0], before[1] + 1, before[2], before[3]]
