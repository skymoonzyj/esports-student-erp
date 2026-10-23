from __future__ import annotations

import importlib
import importlib.util
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.errors import install_exception_handlers


def contracts():
    name = "app.housing.history_void_schemas"
    assert importlib.util.find_spec(name) is not None, "history void input contract is missing"
    return importlib.import_module(name)


def payload(**overrides):
    return {
        "expected_version": 1,
        "reason": "历史记录重复",
        "confirmed": True,
        "idempotency_key": str(uuid4()),
    } | overrides


@pytest.mark.parametrize("raw_reason", [" 测试 ", "测" * 500])
def test_void_input_contract(raw_reason):
    request = contracts().StayHistoryVoidRequest(**payload(reason=raw_reason))
    assert request.reason.get_secret_value() == raw_reason.strip()
    assert raw_reason not in repr(request)
    assert raw_reason not in request.model_dump_json()


@pytest.mark.parametrize(
    "overrides",
    [
        {"reason": "测"},
        {"reason": "测" * 501},
        {"reason": "   "},
        {"reason": "重复手机号13800138000"},
        {"reason": "完整证件11010519491231002X"},
        {"reason": "证件130503670401001"},
        {"confirmed": False},
        {"confirmed": 1},
        {"confirmed": "true"},
        {"confirmed": "True"},
        {"confirmed": None},
        {"unexpected": "extra"},
        {"expected_version": 0},
        {"idempotency_key": "invalid"},
    ],
)
def test_void_input_rejects_invalid_fields(overrides):
    with pytest.raises(ValidationError):
        contracts().StayHistoryVoidRequest(**payload(**overrides))


def test_void_input_requires_explicit_confirmation():
    body = payload()
    del body["confirmed"]
    with pytest.raises(ValidationError):
        contracts().StayHistoryVoidRequest(**body)


def test_void_validation_does_not_echo_failed_reason_in_422_or_logs(caplog):
    model = contracts().StayHistoryVoidRequest
    app = FastAPI()
    install_exception_handlers(app)

    def endpoint(body: model):
        return {"accepted": True}

    # Resolve a dynamically obtained model without relying on local forward refs.
    endpoint.__annotations__["body"] = model
    app.post("/contract-probe")(endpoint)
    for raw_reason in ("私密原因13800138000", "隐" * 501):
        with TestClient(app) as client:
            response = client.post("/contract-probe", json=payload(reason=raw_reason))
        assert response.status_code == 422
        assert raw_reason not in response.text
        assert raw_reason not in caplog.text
        with pytest.raises(ValidationError) as exc:
            model(**payload(reason=raw_reason))
        assert raw_reason not in str(exc.value)


def test_void_result_requires_aware_timestamp():
    model = contracts().StayHistoryVoidResult
    data = {key: uuid4() for key in ("operation_id", "stay_id", "room_id", "person_id")}
    result = model(**data, version=2, voided_at=datetime(2026, 10, 21, tzinfo=UTC))
    assert result.model_dump()["stay_id"] == data["stay_id"]
    with pytest.raises(ValidationError):
        model(**data, version=2, voided_at="2026-10-21T00:00:00")
