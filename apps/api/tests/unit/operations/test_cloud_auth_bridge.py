import importlib
import io
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest


def module():
    assert importlib.util.find_spec("app.operations.cloud_auth_bridge") is not None
    return importlib.import_module("app.operations.cloud_auth_bridge")


@pytest.mark.parametrize(
    "raw",
    [b"x" * 65537, b'{"action":"claim","url":"https://evil"}', b'{"action":"delete_storage"}'],
    ids=["oversized", "extra", "unsupported"],
)
def test_invalid_input_is_rejected_before_database_open(raw):
    bridge = module()
    opened = []
    output = io.StringIO()
    assert (
        bridge.serve(io.BytesIO(raw), output, dispatch=lambda request: opened.append(request)) == 1
    )
    assert opened == []
    assert json.loads(output.getvalue()) == {"ok": False, "error_code": "cloud_auth_bridge_failed"}


def test_response_bound_never_leaks_raw_errors():
    bridge = module()
    for dispatch in (lambda request: {"secret": "x" * 65537}, lambda request: 1 / 0):
        output = io.StringIO()
        assert bridge.serve(io.BytesIO(b'{"action":"claim"}'), output, dispatch=dispatch) == 1
        assert json.loads(output.getvalue()) == {
            "ok": False,
            "error_code": "cloud_auth_bridge_failed",
        }


def test_snapshot_extra_fields_and_expired_fence_are_structurally_rejected():
    bridge = module()
    with pytest.raises(ValueError):
        bridge.REQUEST_ADAPTER.validate_python(
            {
                "action": "begin_snapshot",
                "connector_id": str(uuid4()),
                "operation_id": str(uuid4()),
                "path": "/etc",
            }
        )


def test_result_union_rejects_extra_provider_fields():
    bridge = module()
    with pytest.raises(ValueError):
        bridge.RESULT_ADAPTER.validate_python(
            {"action": "poll", "poll": {"status": "pending"}, "raw_response": "token"}
        )


def test_claim_does_not_accept_heartbeat_only_connector_fields():
    bridge = module()
    with pytest.raises(ValueError):
        bridge.REQUEST_ADAPTER.validate_python({"action": "claim", "connector_id": str(uuid4())})


def test_replacement_and_exact_expiry_reject_old_lease():
    assert importlib.util.find_spec("app.operations.cloud_auth_worker") is not None
    worker = importlib.import_module("app.operations.cloud_auth_worker")
    from app.operations.cloud_auth_models import CloudAuthOperation
    from app.operations.cloud_auth_schemas import AuthFence

    now = datetime.now(UTC)
    fence = AuthFence(
        session_id=uuid4(), session_revision=2, app_revision=1, target_revision=0, lease_id=uuid4()
    )
    operation = CloudAuthOperation(
        protocol_version=1,
        mode="own_app",
        session_id=fence.session_id,
        session_revision=2,
        app_revision=1,
        target_revision=0,
        lease_id=fence.lease_id,
        lease_expires_at=now + timedelta(seconds=90),
        status="running",
    )
    assert worker.lease_matches(operation, fence, now=now)
    assert not worker.lease_matches(operation, fence, now=now + timedelta(seconds=90))
    operation.lease_id = uuid4()
    assert not worker.lease_matches(operation, fence, now=now)


@pytest.mark.parametrize(
    "code", ["cloud_cleanup_pending", "cloud_delivery_unknown", "cloud_directory_delivery_unknown"]
)
def test_unknown_connector_outcomes_keep_safe_supervision_codes(code):
    from app.operations.cloud_auth_errors import safe_error_code

    assert safe_error_code(code) == code


def import_fence():
    return {
        "protocol_version": 2,
        "mode": "openlist_builtin_import",
        "auth_config_id": "openlist-builtin-import-v1:baidu",
        "auth_config_revision": 1,
        "app_revision": None,
        "session_id": str(uuid4()),
        "session_revision": 1,
        "target_revision": 0,
        "lease_id": str(uuid4()),
    }


def test_refresh_checkpoint_is_candidate_independent_and_requires_explicit_source():
    bridge = module()
    value = {
        "action": "refresh_checkpoint",
        "fence": import_fence(),
        "operation_id": str(uuid4()),
        "phase": "sent",
        "identity": {"provider": "baidu", "account_id": "12345"},
    }
    request = bridge.REQUEST_ADAPTER.validate_python(value)
    assert request.phase == "sent" and request.tokens is None
    value["fence"].pop("protocol_version")
    with pytest.raises(ValueError):
        bridge.REQUEST_ADAPTER.validate_python(value)


def test_verify_import_result_requires_explicit_builtin_source():
    bridge = module()
    source = {
        key: value
        for key, value in import_fence().items()
        if key
        in {"protocol_version", "mode", "auth_config_id", "auth_config_revision", "app_revision"}
    }
    result = bridge.RESULT_ADAPTER.validate_python({"action": "verify_import", **source})
    assert result.protocol_version == 2
    with pytest.raises(ValueError):
        bridge.RESULT_ADAPTER.validate_python({"action": "verify_import"})
