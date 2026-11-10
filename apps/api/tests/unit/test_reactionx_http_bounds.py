"""Exercise actual streamed bytes, including lying or absent content length."""

import asyncio

import pytest

from app.core.errors import ProblemError
from app.reactionx import http_support


def exercise(path, chunks, *, declared=None, method="POST", throttle=None):
    consumed, calls, sent = [], [], []
    messages = iter(
        [
            {"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
            for i, chunk in enumerate(chunks)
        ]
    )

    async def receive():
        consumed.append(True)
        return next(messages, {"type": "http.disconnect"})

    async def app(scope, receive, send):
        calls.append(await receive())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "headers": [] if declared is None else [(b"content-length", str(declared).encode())],
        "state": {},
    }
    if throttle is not None:
        # Only the external durable DB throttle is replaced; receive ordering is real.
        scope["state"]["throttle_observer"] = (throttle, consumed)
    asyncio.run(http_support.PrivateReactionxResponseMiddleware(app)(scope, receive, send))
    return sent, calls, consumed


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/reactionx/runs/prepare",
        "/api/v1/reactionx-client/heartbeat",
        "/api/v1/reactionx-client/runs/not-a-uuid/result",
        "/api/v1/reactionx-client/runs/00000000-0000-4000-8000-000000000001/result/extra",
    ],
)
@pytest.mark.parametrize("declared", [None, 1, 8 * 1024 * 1024])
def test_generic_requests_are_rejected_after_256k_actual_bytes(path, declared):
    sent, calls, consumed = exercise(path, [b"a" * 262144, b"b", b"never"], declared=declared)
    assert sent[0]["status"] == 413
    assert not calls
    assert len(consumed) == 2
    assert (b"cache-control", b"no-store, private") in sent[0]["headers"]


def test_only_exact_machine_result_route_gets_8m_and_replays_unchanged():
    path = "/api/v1/reactionx-client/runs/00000000-0000-4000-8000-000000000001/result"
    sent, calls, _ = exercise(path, [b"a" * 8388608], declared=1)
    assert sent[0]["status"] == 204
    assert len(calls[0]["body"]) == 8388608
    sent, calls, consumed = exercise(path, [b"a" * 8388608, b"b", b"never"])
    assert sent[0]["status"] == 413 and not calls and len(consumed) == 2
    # GET/trailing slash are not the fixed upload operation.
    assert exercise(path, [b"a" * 262145], method="GET")[0][0]["status"] == 413
    assert exercise(path + "/", [b"a" * 262145])[0][0]["status"] == 413


def test_generic_exact_boundary_and_unrelated_uploads_are_preserved():
    assert exercise("/api/v1/reactionx/pairings/x/confirm", [b"a" * 262144])[0][0]["status"] == 204
    assert exercise("/api/v1/other/upload", [b"a" * 8388609])[0][0]["status"] == 204


def test_claim_throttle_is_applied_before_first_body_and_429_never_reads(monkeypatch):
    order = []

    def rate(request):
        observer, consumed = request.scope["state"]["throttle_observer"]
        assert not consumed
        observer.append("rate")
        raise ProblemError(
            status=429, code="reactionx_rate_limited", detail="limited", retry_after_seconds=10
        )

    monkeypatch.setattr(http_support, "_rate_claim_before_body", rate)
    sent, calls, consumed = exercise(
        "/api/v1/reactionx-client/pairings/claim", [b"secret"], throttle=order
    )
    assert order == ["rate"] and not calls and not consumed
    assert sent[0]["status"] == 429
