from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.evaluations.dependencies import (
    get_evaluation_processing_service,
    get_worker_media_ticket_service,
)
from app.evaluations.errors import EvaluationLeaseInvalid, EvaluationResultInvalid
from app.evaluations.integration_v2_router import EvaluationV2Capabilities
from app.evaluations.media_service import ProtectedMediaFile
from app.evaluations.processing_schemas import (
    EvaluationWorkerClaimEnvelope,
    EvaluationWorkerClaimInput,
    EvaluationWorkerFailureResponse,
    EvaluationWorkerLeaseRenewalResponse,
    EvaluationWorkerResultResponse,
    SystemEvaluationFailureInput,
    SystemEvaluationResultInput,
)
from app.evaluations.processing_service import (
    EvaluationClaim,
    EvaluationClaimMedia,
    EvaluationFailureSubmission,
    EvaluationLeaseRenewal,
    EvaluationResultSubmission,
)

V1_SERVICE_TOKEN = "evaluation-local-service-token-2026-change-me"
WEB_SERVICE_TOKEN = "evaluation-local-web-service-token-2026-change-me"
WORKER_SERVICE_TOKEN = "evaluation-local-worker-service-token-2026-change-me"
V1_HEADERS = {"Authorization": f"Bearer {V1_SERVICE_TOKEN}"}
WEB_HEADERS = {"Authorization": f"Bearer {WEB_SERVICE_TOKEN}"}
WORKER_HEADERS = {"Authorization": f"Bearer {WORKER_SERVICE_TOKEN}"}
NOW = datetime(2026, 8, 8, 10, tzinfo=UTC)
LEASE_HEADERS = {
    **WORKER_HEADERS,
    "X-Evaluation-Worker-Identity": "score-worker-local",
    "X-Evaluation-Task-Version": "2",
    "X-Evaluation-Lease-Token": "lease-token-" + "a" * 32,
}
MEDIA_TICKET = "a" * 32 + "." + "b" * 43


def _assert_private_headers(response) -> None:
    assert response.headers["cache-control"] == "no-store, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_v2_worker_openapi_is_an_exact_processing_gate(client: TestClient) -> None:
    paths = client.app.openapi()["paths"]
    methods = {
        path: {
            method
            for method in operations
            if method in {"get", "post", "put", "patch", "delete"}
        }
        for path, operations in paths.items()
        if path.startswith("/api/v1/integrations/evaluations/v2/worker/")
    }

    assert methods == {
        "/api/v1/integrations/evaluations/v2/worker/claims": {"post"},
        "/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/renew": {
            "post"
        },
        "/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/media/{media_id}": {
            "get"
        },
        "/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/results": {
            "post"
        },
        "/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/{attempt}/failures": {
            "post"
        },
    }
    assert paths["/api/v1/integrations/evaluations/v2/capabilities"]


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        f"Bearer {V1_SERVICE_TOKEN}",
        f"Bearer {WEB_SERVICE_TOKEN}",
        f"Bearer {WORKER_SERVICE_TOKEN}, Bearer {WORKER_SERVICE_TOKEN}",
        f"bearer {WORKER_SERVICE_TOKEN}",
    ],
)
def test_v2_worker_accepts_only_its_unique_exact_bearer(
    client: TestClient,
    authorization: str | None,
) -> None:
    headers = {"Authorization": authorization} if authorization is not None else {}

    response = client.post(
        "/api/v1/integrations/evaluations/v2/worker/claims",
        headers=headers,
        json={
            "contract_version": "v2",
            "worker_identity": "score-worker-local",
            "supported_adapters": ["lol.v1"],
        },
    )

    assert response.status_code == 401
    _assert_private_headers(response)
    assert response.json()["code"] == "integration_auth_invalid"
    assert WORKER_SERVICE_TOKEN not in response.text


def test_duplicate_worker_authorization_headers_are_rejected(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/integrations/evaluations/v2/worker/claims",
        headers=[
            (b"authorization", f"Bearer {WORKER_SERVICE_TOKEN}".encode("ascii")),
            (b"authorization", f"Bearer {WORKER_SERVICE_TOKEN}".encode("ascii")),
        ],
        json={
            "contract_version": "v2",
            "worker_identity": "score-worker-local",
            "supported_adapters": ["lol.v1"],
        },
    )

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_web_and_worker_service_identities_remain_isolated(
    client: TestClient,
) -> None:
    worker_with_web = client.post(
        "/api/v1/integrations/evaluations/v2/worker/claims",
        headers=WEB_HEADERS,
        json={
            "contract_version": "v2",
            "worker_identity": "score-worker-local",
            "supported_adapters": ["lol.v1"],
        },
    )
    web_with_worker = client.post(
        "/api/v1/integrations/evaluations/v2/web/session-context",
        headers={
            **WORKER_HEADERS,
            "X-Evaluation-Delegated-Session": str(uuid4()),
        },
    )
    v1_with_worker = client.get(
        "/api/v1/integrations/evaluations/v1/capabilities",
        headers=WORKER_HEADERS,
    )

    assert worker_with_web.status_code == 401
    assert web_with_worker.status_code == 401
    assert v1_with_worker.status_code == 401


class FakeProcessingService:
    def __init__(
        self,
        claim: EvaluationClaim | None = None,
        renewal: EvaluationLeaseRenewal | None = None,
        result: EvaluationResultSubmission | None = None,
        failure: EvaluationFailureSubmission | None = None,
    ) -> None:
        self.claim = claim
        self.renewal = renewal
        self.result = result
        self.failure = failure
        self.claim_calls: list[dict[str, object]] = []
        self.renew_calls: list[dict[str, object]] = []
        self.result_calls: list[dict[str, object]] = []
        self.failure_calls: list[dict[str, object]] = []
        self.result_lease_validation_calls: list[dict[str, object]] = []
        self.failure_lease_validation_calls: list[dict[str, object]] = []

    def claim_next(self, **kwargs: object) -> EvaluationClaim | None:
        self.claim_calls.append(kwargs)
        return self.claim

    def renew(self, **kwargs: object) -> EvaluationLeaseRenewal:
        self.renew_calls.append(kwargs)
        assert self.renewal is not None
        return self.renewal

    def validate_result_lease(self, **kwargs: object) -> None:
        self.result_lease_validation_calls.append(kwargs)

    def validate_failure_lease(self, **kwargs: object) -> None:
        self.failure_lease_validation_calls.append(kwargs)

    def submit_result(self, **kwargs: object) -> EvaluationResultSubmission:
        self.result_calls.append(kwargs)
        assert self.result is not None
        return self.result

    def submit_failure(self, **kwargs: object) -> EvaluationFailureSubmission:
        self.failure_calls.append(kwargs)
        assert self.failure is not None
        return self.failure


def _claim() -> EvaluationClaim:
    return EvaluationClaim(
        task_id=UUID("10000000-0000-4000-8000-000000000001"),
        attempt=1,
        task_version=2,
        lease_token="lease-token-" + "a" * 32,
        lease_expires_at=NOW + timedelta(minutes=5),
        adapter_id="lol",
        adapter_version="1.0.0",
        scoring_rule_version="1",
        result_schema_version="1",
        required_fields=("kills", "deaths", "assists"),
        confidence_thresholds={
            "kills": 0.8,
            "deaths": 0.8,
            "assists": 0.8,
        },
        media=(
            EvaluationClaimMedia(
                media_id=UUID("20000000-0000-4000-8000-000000000001"),
                position=1,
                slot_key="profile",
                mime_type="image/png",
                size_bytes=1024,
                ticket="media-ticket-" + "b" * 32,
            ),
        ),
    )


def test_worker_claim_response_is_exact_and_filters_internal_snapshot(
    client: TestClient,
) -> None:
    service = FakeProcessingService(_claim())
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/worker/claims",
            headers=WORKER_HEADERS,
            json={
                "contract_version": "v2",
                "worker_identity": "score-worker-local",
                "supported_adapters": ["lol.v1", "valorant.v1"],
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 200
    _assert_private_headers(response)
    assert set(response.json()) == {"api_version", "claim"}
    claim = response.json()["claim"]
    assert set(claim) == {
        "task_id",
        "attempt",
        "task_version",
        "lease_token",
        "lease_expires_at",
        "adapter_id",
        "adapter_version",
        "scoring_rule_version",
        "result_schema_version",
        "required_fields",
        "confidence_thresholds",
        "media",
    }
    assert set(claim["media"][0]) == {
        "media_id",
        "position",
        "slot_key",
        "mime_type",
        "size_bytes",
        "ticket",
    }
    assert "不得返回" not in response.text
    assert service.claim_calls[0]["worker_identity"] == "score-worker-local"
    assert service.claim_calls[0]["supported_adapters"] == {
        "lol.v1",
        "valorant.v1",
    }
    assert service.claim_calls[0]["occurred_at"].tzinfo is not None
    assert service.claim_calls[0]["request_id"]


def test_worker_renew_uses_strict_lease_headers_and_exact_response(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = FakeProcessingService(
        renewal=EvaluationLeaseRenewal(
            task_id=task_id,
            attempt=1,
            task_version=2,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
    )
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/renew",
            headers=LEASE_HEADERS,
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 200
    _assert_private_headers(response)
    assert set(response.json()) == {
        "api_version",
        "task_id",
        "attempt",
        "task_version",
        "lease_expires_at",
    }
    assert service.renew_calls[0]["task_id"] == task_id
    assert service.renew_calls[0]["worker_identity"] == "score-worker-local"
    assert service.renew_calls[0]["lease_token"] == LEASE_HEADERS[
        "X-Evaluation-Lease-Token"
    ]


def _result_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "contract_version": "v2",
        "adapter_id": "lol",
        "adapter_version": "1.0.0",
        "scoring_rule_version": "1",
        "result_schema_version": "1",
        "fields": {"kills": 12, "deaths": 3, "assists": 8},
        "confidences": {"kills": 0.98, "deaths": 0.97, "assists": 0.96},
        "missing_fields": [],
        "warnings": [],
        "score": 86,
        "grade": "A",
        "components": [
            {
                "key": "combat",
                "score": 86,
                "weight": 1.0,
                "summary": "作战数据稳定",
            }
        ],
        "advice": "继续保持团战前的信息确认。",
        "duration_ms": 1200,
        "idempotency_key": "result-task-1-attempt-1",
    }
    body.update(overrides)
    return body


def test_worker_result_and_failure_routes_use_the_frozen_bodies(
    client: TestClient,
) -> None:
    task_id = uuid4()
    result_id = uuid4()
    service = FakeProcessingService(
        result=EvaluationResultSubmission(
            task_id=task_id,
            result_id=result_id,
            result_version=1,
            task_status="needs_review",
            task_version=3,
        ),
        failure=EvaluationFailureSubmission(
            task_id=task_id,
            attempt=1,
            task_status="queued",
            task_version=3,
            retryable=True,
            available_at=NOW + timedelta(seconds=30),
            failure_code=None,
        ),
    )
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        result_response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/results",
            headers=LEASE_HEADERS,
            json=_result_body(),
        )
        failure_response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/failures",
            headers=LEASE_HEADERS,
            json={
                "contract_version": "v2",
                "error_class": "network",
                "safe_error_summary": "worker network request failed",
                "trace_id": "trace-123",
                "idempotency_key": "failure-task-1-attempt-1",
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert result_response.status_code == 200
    assert set(result_response.json()) == {
        "api_version",
        "task_id",
        "result_id",
        "result_version",
        "task_status",
        "task_version",
    }
    assert failure_response.status_code == 200
    assert set(failure_response.json()) == {
        "api_version",
        "task_id",
        "attempt",
        "task_status",
        "task_version",
        "retryable",
        "available_at",
        "failure_code",
    }
    assert service.result_calls[0]["input"].contract_version == "v2"
    assert service.failure_calls[0]["input"].error_class == "network"


class FakeWorkerMediaService:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls: list[dict[str, object]] = []

    def read_original(self, **kwargs: object) -> ProtectedMediaFile:
        self.calls.append(kwargs)
        return ProtectedMediaFile(
            path=self.path,
            mime_type="image/png",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


def test_worker_media_ticket_is_received_only_in_a_header(
    client: TestClient,
    tmp_path: Path,
) -> None:
    task_id = uuid4()
    media_id = uuid4()
    path = tmp_path / "worker-media.png"
    path.write_bytes(b"private-worker-media")
    service = FakeWorkerMediaService(path)
    client.app.dependency_overrides[get_worker_media_ticket_service] = lambda: service
    try:
        response = client.get(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/media/{media_id}",
            headers={
                **WORKER_HEADERS,
                "X-Evaluation-Worker-Identity": "score-worker-local",
                "X-Evaluation-Task-Version": "2",
                "X-Evaluation-Media-Ticket": MEDIA_TICKET,
            },
        )
    finally:
        client.app.dependency_overrides.pop(get_worker_media_ticket_service, None)

    assert response.status_code == 200
    assert response.content == b"private-worker-media"
    _assert_private_headers(response)
    assert service.calls[0]["ticket"] == MEDIA_TICKET


class RaisingRenewService(FakeProcessingService):
    def renew(self, **kwargs: object) -> EvaluationLeaseRenewal:
        self.renew_calls.append(kwargs)
        raise EvaluationLeaseInvalid("secret stale lease detail")


class RaisingInvalidResultService(FakeProcessingService):
    def submit_result(self, **kwargs: object) -> EvaluationResultSubmission:
        self.result_calls.append(kwargs)
        raise EvaluationResultInvalid("private result validation detail")


def test_service_result_contract_error_uses_safe_422_envelope(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = RaisingInvalidResultService()
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/results",
            headers=LEASE_HEADERS,
            json=_result_body(),
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 422
    assert response.json()["code"] == "evaluation_result_invalid"
    assert "private result validation detail" not in response.text


def test_malformed_and_stale_lease_have_the_same_private_404(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = RaisingRenewService()
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        malformed = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/renew",
            headers={
                **WORKER_HEADERS,
                "X-Evaluation-Worker-Identity": "score-worker-local",
                "X-Evaluation-Task-Version": "02",
                "X-Evaluation-Lease-Token": LEASE_HEADERS[
                    "X-Evaluation-Lease-Token"
                ],
            },
        )
        stale = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/renew",
            headers=LEASE_HEADERS,
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert malformed.status_code == stale.status_code == 404
    assert malformed.json() == stale.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "评价资源不存在",
        "code": "evaluation_resource_not_found",
    }
    assert "secret stale lease detail" not in stale.text
    assert len(service.renew_calls) == 1


def test_duplicate_lease_headers_fail_before_service_call(client: TestClient) -> None:
    task_id = uuid4()
    service = FakeProcessingService()
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/renew",
            headers=[
                (b"authorization", f"Bearer {WORKER_SERVICE_TOKEN}".encode("ascii")),
                (b"x-evaluation-worker-identity", b"score-worker-local"),
                (b"x-evaluation-task-version", b"2"),
                (b"x-evaluation-task-version", b"2"),
                (
                    b"x-evaluation-lease-token",
                    LEASE_HEADERS["X-Evaluation-Lease-Token"].encode("ascii"),
                ),
            ],
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 404
    assert service.renew_calls == []


def test_worker_result_dto_forbids_unknown_fields_before_service_call(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = FakeProcessingService()
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/results",
            headers=LEASE_HEADERS,
            json=_result_body(raw_ocr="must-not-be-accepted"),
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 422
    _assert_private_headers(response)
    assert response.json()["code"] == "validation_error"
    assert service.result_calls == []


def test_v2_capabilities_accepts_web_or_worker_but_not_v1(
    client: TestClient,
) -> None:
    worker = client.get(
        "/api/v1/integrations/evaluations/v2/capabilities",
        headers=WORKER_HEADERS,
    )
    web = client.get(
        "/api/v1/integrations/evaluations/v2/capabilities",
        headers=WEB_HEADERS,
    )
    v1 = client.get(
        "/api/v1/integrations/evaluations/v2/capabilities",
        headers=V1_HEADERS,
    )

    assert worker.status_code == web.status_code == 200
    assert worker.json() == web.json() == {
        "api_version": "v2",
        "lease_seconds": 300,
        "max_attempts": 3,
    }
    assert v1.status_code == 401


def test_worker_token_query_and_cookie_cannot_replace_authorization(
    client: TestClient,
) -> None:
    client.cookies.set("evaluation_tool_worker_service_token", WORKER_SERVICE_TOKEN)
    try:
        response = client.post(
            "/api/v1/integrations/evaluations/v2/worker/claims",
            params={"access_token": WORKER_SERVICE_TOKEN},
            json={
                "contract_version": "v2",
                "worker_identity": "score-worker-local",
                "supported_adapters": ["lol.v1"],
            },
        )
    finally:
        client.cookies.delete("evaluation_tool_worker_service_token")

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_worker_bearer_precedes_body_validation(client: TestClient) -> None:
    response = client.post(
        "/api/v1/integrations/evaluations/v2/worker/claims",
        headers={"Authorization": "Bearer wrong-worker-token"},
        json={"unexpected": "field"},
    )

    assert response.status_code == 401
    assert response.json()["code"] == "integration_auth_invalid"


def test_media_ticket_query_cannot_replace_the_private_header(
    client: TestClient,
) -> None:
    response = client.get(
        f"/api/v1/integrations/evaluations/v2/worker/tasks/{uuid4()}/attempts/1/media/{uuid4()}",
        headers=LEASE_HEADERS,
        params={"ticket": MEDIA_TICKET},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "evaluation_resource_not_found"


def test_worker_lease_secret_cannot_be_smuggled_into_audit_request_id(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = FakeProcessingService(
        renewal=EvaluationLeaseRenewal(
            task_id=task_id,
            attempt=1,
            task_version=2,
            lease_expires_at=NOW + timedelta(minutes=5),
        )
    )
    lease_token = LEASE_HEADERS["X-Evaluation-Lease-Token"]
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/renew",
            headers={**LEASE_HEADERS, "X-Request-ID": f"worker-{lease_token}"},
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 200
    assert len(service.renew_calls) == 1
    assert lease_token not in str(service.renew_calls[0]["request_id"])
    assert lease_token not in response.text


def test_worker_service_token_cannot_be_reflected_as_request_id(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service_token = "correct-horse-battery-staple-production"
    monkeypatch.setattr(
        client.app.state.settings,
        "evaluation_tool_worker_service_token",
        SecretStr(service_token),
    )

    response = client.post(
        "/api/v1/integrations/evaluations/v2/worker/claims",
        headers={
            "Authorization": f"Bearer {service_token}",
            "X-Request-ID": service_token,
        },
        json={
            "contract_version": "v2",
            "worker_identity": "score-worker-local",
            "supported_adapters": [],
        },
    )

    assert response.status_code == 422
    assert service_token not in response.text
    assert service_token not in response.headers["X-Request-ID"]


class RejectingResultLeaseService(FakeProcessingService):
    def validate_result_lease(self, **kwargs: object) -> None:
        self.result_lease_validation_calls.append(kwargs)
        raise EvaluationLeaseInvalid("private lease detail")


def test_invalid_worker_lease_precedes_sensitive_result_body_validation(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = RejectingResultLeaseService()
    private_key = "13800138000"
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/results",
            headers=LEASE_HEADERS,
            json=_result_body(fields={private_key: []}),
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 404
    assert response.json() == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "评价资源不存在",
        "code": "evaluation_resource_not_found",
    }
    assert private_key not in response.text
    assert service.result_calls == []


def test_result_validation_never_echoes_dynamic_mapping_keys(
    client: TestClient,
) -> None:
    task_id = uuid4()
    service = FakeProcessingService()
    private_key = "player_game_id_13800138000"
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/results",
            headers=LEASE_HEADERS,
            json=_result_body(fields={private_key: []}),
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 422
    assert private_key not in response.text
    assert service.result_calls == []


def test_result_body_idempotency_key_cannot_be_smuggled_into_audit_request_id(
    client: TestClient,
) -> None:
    task_id = uuid4()
    result_id = uuid4()
    idempotency_key = "result-task-1-attempt-1"
    service = FakeProcessingService(
        result=EvaluationResultSubmission(
            task_id=task_id,
            result_id=result_id,
            result_version=1,
            task_status="needs_review",
            task_version=3,
        )
    )
    client.app.dependency_overrides[get_evaluation_processing_service] = lambda: service
    try:
        response = client.post(
            f"/api/v1/integrations/evaluations/v2/worker/tasks/{task_id}/attempts/1/results",
            headers={**LEASE_HEADERS, "X-Request-ID": idempotency_key},
            json=_result_body(idempotency_key=idempotency_key),
        )
    finally:
        client.app.dependency_overrides.pop(get_evaluation_processing_service, None)

    assert response.status_code == 200
    assert len(service.result_calls) == 1
    assert idempotency_key not in str(service.result_calls[0]["request_id"])


def test_worker_contract_fixture_is_canonical_and_complete() -> None:
    contract_path = (
        Path(__file__).resolve().parents[1]
        / "contracts"
        / "evaluation_integration_v2.json"
    )
    raw = contract_path.read_text(encoding="utf-8")
    contract = json.loads(raw)

    assert raw == json.dumps(
        contract,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    assert contract["api_version"] == "v2"
    assert contract["limits"] == {"lease_seconds": 300, "max_attempts": 3}
    assert contract["media_types"] == ["image/jpeg", "image/png", "image/webp"]
    assert contract["headers"] == {
        "authorization": "Authorization",
        "delegated_session": "X-Evaluation-Delegated-Session",
        "idempotency_key": "Idempotency-Key",
        "lease_token": "X-Evaluation-Lease-Token",
        "media_ticket": "X-Evaluation-Media-Ticket",
        "task_version": "X-Evaluation-Task-Version",
        "worker_identity": "X-Evaluation-Worker-Identity",
    }
    assert set(contract["paths"]) == {
        "capabilities",
        "web_session_context",
        "web_upload_context",
        "web_upload_sessions",
        "web_upload_slot",
        "web_create_task",
        "web_tasks",
        "web_task_detail",
        "worker_claims",
        "worker_renew",
        "worker_media",
        "worker_results",
        "worker_failures",
    }
    assert contract["schema_versions"] == {
        "claim_request": "v2",
        "claim_response": "v2",
        "failure_request": "v2",
        "result_request": "v2",
        "web_request": "v2",
        "web_response": "v2",
    }
    assert contract["schemas"] == {
        "capabilities_response": EvaluationV2Capabilities.model_json_schema(),
        "claim_request": EvaluationWorkerClaimInput.model_json_schema(),
        "claim_response": EvaluationWorkerClaimEnvelope.model_json_schema(),
        "failure_request": SystemEvaluationFailureInput.model_json_schema(),
        "failure_response": EvaluationWorkerFailureResponse.model_json_schema(),
        "renew_response": EvaluationWorkerLeaseRenewalResponse.model_json_schema(),
        "result_request": SystemEvaluationResultInput.model_json_schema(),
        "result_response": EvaluationWorkerResultResponse.model_json_schema(),
    }
    assert contract["error_envelopes"] == {
        "delegated_session_invalid": {
            "body": {
                "code": "delegated_session_invalid",
                "detail": "ERP 登录已失效，请重新连接",
                "status": 401,
                "title": "Unauthorized",
                "type": "about:blank",
            },
            "http_status": 401,
            "variable_fields": ["request_id"],
        },
        "evaluation_access_denied": {
            "body": {
                "code": "evaluation_access_denied",
                "detail": "评价中心权限已失效",
                "status": 403,
                "title": "Forbidden",
                "type": "about:blank",
            },
            "http_status": 403,
            "variable_fields": ["request_id"],
        },
        "evaluation_idempotency_conflict": {
            "body": {
                "code": "evaluation_idempotency_conflict",
                "detail": "幂等请求与原请求不一致",
                "status": 409,
                "title": "Conflict",
                "type": "about:blank",
            },
            "http_status": 409,
            "variable_fields": ["request_id"],
        },
        "evaluation_operation_denied": {
            "body": {
                "code": "evaluation_operation_denied",
                "detail": "没有执行此评价操作的权限",
                "status": 403,
                "title": "Forbidden",
                "type": "about:blank",
            },
            "http_status": 403,
            "variable_fields": ["request_id"],
        },
        "evaluation_request_invalid": {
            "body": {
                "code": "evaluation_request_invalid",
                "detail": "评价请求无效",
                "status": 422,
                "title": "Unprocessable Entity",
                "type": "about:blank",
            },
            "http_status": 422,
            "variable_fields": ["request_id"],
        },
        "evaluation_resource_not_found": {
            "body": {
                "code": "evaluation_resource_not_found",
                "detail": "评价资源不存在",
                "status": 404,
                "title": "Not Found",
                "type": "about:blank",
            },
            "http_status": 404,
            "variable_fields": [],
        },
        "evaluation_result_invalid": {
            "body": {
                "code": "evaluation_result_invalid",
                "detail": "评价结果无效",
                "status": 422,
                "title": "Unprocessable Entity",
                "type": "about:blank",
            },
            "http_status": 422,
            "variable_fields": ["request_id"],
        },
        "integration_auth_invalid": {
            "body": {
                "code": "integration_auth_invalid",
                "detail": "集成服务身份无效",
                "status": 401,
                "title": "Unauthorized",
                "type": "about:blank",
            },
            "http_status": 401,
            "variable_fields": ["request_id"],
        },
        "media_validation_failed": {
            "body": {
                "code": "media_validation_failed",
                "detail": "评价媒体无效",
                "status": 422,
                "title": "Unprocessable Entity",
                "type": "about:blank",
            },
            "http_status": 422,
            "variable_fields": ["request_id"],
        },
        "validation_error": {
            "body": {
                "code": "validation_error",
                "detail": "Request validation failed",
                "status": 422,
                "title": "Unprocessable Entity",
                "type": "about:blank",
            },
            "http_status": 422,
            "variable_fields": ["request_id", "errors"],
        },
    }
    assert contract["error_codes"] == sorted(contract["error_envelopes"])
