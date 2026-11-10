"""Public release boundaries use production availability, never esports_enabled."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from app.audit.models import AuditEvent
from app.esports_assessments.models import (
    AssessmentAttempt,
    AssessmentPracticeRun,
    AssessmentSession,
)
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from tests.api import test_esports_routes as route_fixtures
from tests.esports_helpers import attempt_request, session_request
from tests.services.test_esports_practice import practice_request

# Only the independent committed rate-limit connection is replaced; authentication,
# permissions, release configuration, database writes and API serialization are real.
no_rate_limit = route_fixtures.no_rate_limit
PREFIX = "/api/v1/esports-assessments"
CLOSED = (("mobile-fps", "precision"), ("mobile-moba", "dodge"))


def login(client, users, key="self_student"):
    return {"X-CSRF-Token": route_fixtures._login(client, users, key)}


def counts(db):
    return tuple(
        db.scalar(select(func.count()).select_from(model))
        for model in (AssessmentSession, AssessmentAttempt, AssessmentPracticeRun, AuditEvent)
    )


def test_default_student_context_and_catalog_offer_only_released_v5(db_client, platform_users):
    login(db_client, platform_users)
    context = db_client.get(f"{PREFIX}/context")
    assert context.status_code == 200
    assert context.json()["can_take"] and context.json()["can_read_self"]
    assert context.json()["available_suites"] == ["pc-fps", "pc-moba"]
    catalog = db_client.get(f"{PREFIX}/suites")
    assert catalog.status_code == 200
    assert [item["suite_id"] for item in catalog.json()["items"]] == ["pc-fps", "pc-moba"]
    assert catalog.json()["items"][0]["catalog_version"] == "pc-fps-v5"
    assert catalog.json()["items"][0]["test_ids"] == ["gridshot", "sixshot", "spheretrack90"]
    assert catalog.json()["items"][1]["catalog_version"] == "pc-moba-v5"
    assert catalog.json()["items"][1]["test_ids"] == ["last-hit"]


def test_default_student_can_create_and_start_own_v5_formal(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    created = db_client.post(
        f"{PREFIX}/sessions", json=session_request().model_dump(mode="json"), headers=headers
    )
    assert created.status_code == 201
    assert created.json()["suite_config"]["catalog_version"] == "pc-fps-v5"
    assert created.json()["members"] == ["gridshot", "sixshot", "spheretrack90"]
    stored = db.get(AssessmentSession, UUID(created.json()["id"]))
    assert stored.person_id == platform_users["people"]["self_student"].id
    assert stored.created_by_user_id == platform_users["accounts"]["self_student"].id
    started = db_client.post(
        f"{PREFIX}/sessions/{created.json()['id']}/attempts",
        json=attempt_request("pc-fps", "gridshot").model_dump(mode="json"),
        headers=headers,
    )
    assert started.status_code == 201
    assert started.json()["config"]["catalog_version"] == "pc-fps-v5"


def test_default_student_can_start_own_v5_practice(db_client, db, platform_users):
    headers = login(db_client, platform_users)
    started = db_client.post(
        f"{PREFIX}/practice-runs", json=practice_request().model_dump(mode="json"), headers=headers
    )
    assert started.status_code == 201
    assert started.json()["config"]["catalog_version"] == "pc-fps-v5"
    assert started.json()["config"]["test_id"] == "gridshot"
    stored = db.get(AssessmentPracticeRun, UUID(started.json()["id"]))
    assert stored.person_id == platform_users["people"]["self_student"].id
    assert stored.created_by_user_id == platform_users["accounts"]["self_student"].id


@pytest.mark.parametrize("suite_id,test_id", CLOSED)
def test_student_cannot_create_unopened_formal_or_practice(
    db_client, db, platform_users, suite_id, test_id
):
    headers = login(db_client, platform_users)
    before = counts(db)
    for path, payload in (
        ("sessions", session_request(suite_id)),
        ("practice-runs", practice_request(suite_id, test_id)),
    ):
        response = db_client.post(
            f"{PREFIX}/{path}", json=payload.model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 409
        assert response.json()["code"] == "esports_suite_unavailable"
        assert counts(db) == before


@pytest.mark.parametrize("role", ["admin", "coach", "guardian"])
def test_public_opening_does_not_grant_default_participation(db_client, db, platform_users, role):
    if role == "admin":
        account = platform_users["accounts"]["admin"]
        db.execute(delete(UserRole).where(UserRole.user_id == account.id))
        db.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
        db.commit()
    headers = login(db_client, platform_users, role)
    context = db_client.get(f"{PREFIX}/context")
    assert context.status_code == 200 and not context.json()["can_take"]
    # Public catalog visibility is not participation authorization.
    assert context.json()["available_suites"] == ["pc-fps", "pc-moba"]
    assert [item["suite_id"] for item in db_client.get(f"{PREFIX}/suites").json()["items"]] == [
        "pc-fps",
        "pc-moba",
    ]
    before = counts(db)
    for path, payload in (
        ("sessions", session_request()),
        ("practice-runs", practice_request()),
        ("sessions", session_request("pc-moba").model_copy(update={"attack_mode": "melee"})),
        (
            "practice-runs",
            practice_request("pc-moba", "last-hit").model_copy(update={"attack_mode": "ranged"}),
        ),
    ):
        response = db_client.post(
            f"{PREFIX}/{path}", json=payload.model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 403
        assert response.json()["code"] == "esports_forbidden"
        assert counts(db) == before


@pytest.mark.parametrize("permission", ["esports_assessments.read", "esports_assessments.take"])
def test_explicit_self_deny_still_blocks_released_suite(db_client, db, platform_users, permission):
    headers = login(db_client, platform_users)
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["self_student"].id,
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind="self",
        )
    )
    db.commit()
    context = db_client.get(f"{PREFIX}/context").json()
    assert not context["can_take"] and context["available_suites"] == ["pc-fps", "pc-moba"]
    assert [item["suite_id"] for item in db_client.get(f"{PREFIX}/suites").json()["items"]] == [
        "pc-fps",
        "pc-moba",
    ]
    before = counts(db)
    for path, payload in (
        ("sessions", session_request()),
        ("practice-runs", practice_request()),
        ("sessions", session_request("pc-moba").model_copy(update={"attack_mode": "melee"})),
        (
            "practice-runs",
            practice_request("pc-moba", "last-hit").model_copy(update={"attack_mode": "ranged"}),
        ),
    ):
        response = db_client.post(
            f"{PREFIX}/{path}", json=payload.model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 403 and response.json()["code"] == "esports_forbidden"
        assert counts(db) == before


@pytest.mark.parametrize("suite_id,test_id", CLOSED)
def test_superadmin_retains_unopened_suite_preview(db_client, platform_users, suite_id, test_id):
    headers = login(db_client, platform_users, "admin")
    context = db_client.get(f"{PREFIX}/context").json()
    assert context["available_suites"] == ["pc-fps", "mobile-fps", "pc-moba", "mobile-moba"]
    assert [item["suite_id"] for item in db_client.get(f"{PREFIX}/suites").json()["items"]] == [
        "pc-fps",
        "mobile-fps",
        "pc-moba",
        "mobile-moba",
    ]
    for path, payload in (
        ("sessions", session_request(suite_id)),
        ("practice-runs", practice_request(suite_id, test_id, key=uuid4())),
    ):
        response = db_client.post(
            f"{PREFIX}/{path}", json=payload.model_dump(mode="json"), headers=headers
        )
        assert response.status_code == 201
