"""Regression coverage for the evaluation-center camp-survey capability hint."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.evaluations.context_service import EvaluationContextService
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.passwords import PasswordService
from tests.api.test_camp_surveys import (
    OPEN_AT,
    CampApiScenario,
    _append_snapshot_v2,
)

pytest_plugins = ("tests.api.test_camp_surveys",)

def _capability(scenario: CampApiScenario, account_id: UUID, occurred_at: datetime) -> bool:
    return EvaluationContextService(scenario.db).surveys_available(
        account_id=account_id,
        occurred_at=occurred_at,
    )


def test_guardian_relationship_is_available_until_ended_at_occurrence(
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    relations = list(
        scenario.db.scalars(
            select(GuardianRelationship).where(
                GuardianRelationship.guardian_person_id == scenario.people["guardian"].id
            )
        )
    )
    assert any(row.id == scenario.guardian_one_relationship_id for row in relations)
    for relation in relations:
        relation.ended_at = None
    scenario.db.flush()
    assert _capability(scenario, scenario.accounts["guardian"].id, OPEN_AT) is True

    for relation in relations:
        relation.ended_at = OPEN_AT + timedelta(seconds=1)
    scenario.db.flush()

    assert _capability(scenario, scenario.accounts["guardian"].id, OPEN_AT) is True

    for relation in relations:
        relation.ended_at = OPEN_AT
    scenario.db.flush()
    assert _capability(scenario, scenario.accounts["guardian"].id, OPEN_AT) is False


def test_guardian_relationship_requires_live_guardian_role(
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    scenario.db.execute(
        delete(UserRole).where(
            UserRole.user_id == scenario.accounts["guardian"].id,
            UserRole.role_id == ROLES["guardian"].id,
        )
    )
    scenario.db.flush()

    assert _capability(scenario, scenario.accounts["guardian"].id, OPEN_AT) is False


def test_center_permission_is_evaluated_at_occurred_at(
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    occurred_at = datetime.now(UTC) - timedelta(days=1)
    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["self_student"].id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
            valid_from=occurred_at - timedelta(minutes=1),
            valid_until=occurred_at + timedelta(minutes=1),
            created_by_user_id=scenario.accounts["admin"].id,
            grant_reason="回归测试时间窗口拒绝",
        )
    )
    scenario.db.flush()

    # The override is expired at the wall-clock time of the test run but was
    # active at the historical event timestamp supplied to the capability.
    service = EvaluationContextService(scenario.db)
    assert (
        service.center_allowed(
            scenario.accounts["self_student"].id,
            occurred_at=occurred_at,
        )
        is False
    )
    assert service.center_allowed(scenario.accounts["self_student"].id) is True
    assert _capability(scenario, scenario.accounts["self_student"].id, occurred_at) is False


def test_capability_requires_active_student_profile_and_account(
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    student = scenario.db.get(
        StudentProfile,
        scenario.people["self_student"].id,
    )
    assert student is not None
    student.status = "inactive"
    scenario.db.flush()
    assert _capability(scenario, scenario.accounts["self_student"].id, OPEN_AT) is False

    student.status = "active"
    account = scenario.db.get(UserAccount, scenario.accounts["self_student"].id)
    assert account is not None
    account.status = "disabled"
    scenario.db.flush()
    assert _capability(scenario, account.id, OPEN_AT) is False


def test_staff_capability_covers_main_rating_and_submitted_supplement(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    assert _capability(scenario, scenario.accounts["coach"].id, OPEN_AT) is False

    student_csrf = scenario.login(db_client, "self_student")
    submitted = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers={
            "X-CSRF-Token": student_csrf,
            "X-Request-ID": "surveys-available-main-rating",
        },
        json=scenario.submission_payload(key="surveys-available-main-rating"),
    )
    assert submitted.status_code == 201, submitted.text
    assert _capability(scenario, scenario.accounts["coach"].id, OPEN_AT) is True

    # Add a corrected roster row for a staff member who has no main rating.
    added = _append_snapshot_v2(scenario)
    extra_person = scenario.db.get(Person, scenario.extra_staff_person_id)
    assert extra_person is not None
    created = scenario.users["account_service"].create_account(
        AccountCreate(
            person_id=extra_person.id,
            login_phone=None,
            email=None,
        )
    )
    extra_account = scenario.db.get(UserAccount, created.account.id)
    assert extra_account is not None
    extra_account.password_hash = PasswordService().hash("Supp2026PassAa")
    extra_account.force_password_change = False
    scenario.db.add(UserRole(user_id=extra_account.id, role_id=ROLES["staff"].id))
    scenario.db.commit()

    supplement = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{submitted.json()['submission_id']}/supplement",
        headers={
            "X-CSRF-Token": student_csrf,
            "X-Request-ID": "surveys-available-supplement",
        },
        json=scenario.supplement_payload(
            key="surveys-available-supplement",
            staff_id=added.id,
        ),
    )
    assert supplement.status_code == 201, supplement.text

    assert _capability(scenario, extra_account.id, OPEN_AT) is True
