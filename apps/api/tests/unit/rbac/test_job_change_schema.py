from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.rbac import schemas


def test_job_change_requires_explicit_retention_and_unique_snapshot():
    request = getattr(schemas, "JobRoleChangeRequest", None)
    assert request is not None, "Atomic job changes need a validated request contract"
    role_id = uuid4()
    base = {"from_role_id": None, "to_role_id": role_id, "expected_role_ids": []}
    with pytest.raises(ValidationError):
        request(**base)
    assert request(**base, keep_concurrent_roles=False).keep_concurrent_roles is False
    with pytest.raises(ValidationError):
        request(**{**base, "expected_role_ids": [role_id, role_id]}, keep_concurrent_roles=True)
    with pytest.raises(ValidationError):
        request(**base, keep_concurrent_roles="false")
    with pytest.raises(ValidationError):
        request(to_role_id=role_id, expected_role_ids=[], keep_concurrent_roles=False)


def test_job_change_rejects_unauthenticated_request_at_api_boundary(client):
    response = client.post(f"/api/v1/accounts/{uuid4()}/job-role-change", json={
        "from_role_id": None, "to_role_id": str(uuid4()),
        "expected_role_ids": [], "keep_concurrent_roles": False,
    })
    assert response.status_code == 401
