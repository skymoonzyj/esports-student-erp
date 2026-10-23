from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.audit.service import AuditMetadataRejected, AuditService
from app.rbac.schemas import PermissionOverrideCreate
from app.team_management.schemas import (
    TeamCandidateQuery,
    TeamContext,
    TeamMemberView,
    TeamPage,
    TransferTeamInput,
)


def test_transfer_rejects_effective_date_and_untrimmed_reason() -> None:
    target = uuid4()
    key = uuid4()
    with pytest.raises(ValidationError):
        TransferTeamInput.model_validate(
            {
                "target_team_id": target,
                "reason": "move",
                "idempotency_key": key,
                "effective_date": "2026-09-29",
            }
        )
    for reason in ("   ", "a" * 201):
        with pytest.raises(ValidationError):
            TransferTeamInput(target_team_id=target, reason=reason, idempotency_key=key)
    assert (
        TransferTeamInput(target_team_id=target, reason="  转队  ", idempotency_key=key).reason
        == "转队"
    )


def test_candidate_query_caps_page_size_at_twenty() -> None:
    assert TeamCandidateQuery().page_size == 20
    with pytest.raises(ValidationError):
        TeamCandidateQuery(page_size=21)


def test_assigned_team_override_requires_concrete_team_id() -> None:
    with pytest.raises(ValidationError):
        PermissionOverrideCreate(
            permission_id=uuid4(),
            effect="allow",
            scope_kind="assigned_team",
        )
    value = PermissionOverrideCreate(
        permission_id=uuid4(),
        effect="allow",
        scope_kind="assigned_team",
        scope_id=uuid4(),
    )
    assert value.scope_kind == "assigned_team"


def test_audit_allows_only_named_team_scope_value() -> None:
    metadata = {
        "permission_key": "team_memberships.read",
        "effect": "allow",
        "scope_kind": "assigned_team",
        "scope_id": str(uuid4()),
    }
    AuditService.validate_metadata(metadata)
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata({**metadata, "scope_kind": "another_team"})


def test_context_and_member_contract_are_privacy_bounded() -> None:
    assert TeamContext.model_fields.keys() == {
        "can_open_management",
        "can_read_self",
        "can_manage_catalog",
        "can_assign_managers",
        "can_transfer",
    }
    member = TeamMemberView(
        id=uuid4(),
        person_id=uuid4(),
        full_name="队员",
        nickname=None,
        age=20,
        is_minor=False,
        role="team_member",
        project_id=uuid4(),
        project_name="王者荣耀",
        team_id=uuid4(),
        team_name="一队",
        status="current",
        joined_on=date(2026, 9, 29),
        left_on=None,
        end_kind=None,
        account_status="active",
        transfer_reason=None,
    )
    assert "date_of_birth" not in member.model_dump()
    assert "phone" not in member.model_dump()
    assert TeamPage[TeamMemberView](items=[member], page=1, page_size=20, total=1).total == 1
