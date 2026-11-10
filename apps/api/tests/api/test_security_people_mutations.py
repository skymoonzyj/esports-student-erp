"""Mutation responses must not bypass the returned person's read permission."""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.rbac.service import RbacService
from tests.api.test_people import sign_in


def _override(db: Session, user_id: UUID, person_id: UUID, permission: str, effect: str) -> None:
    db.add(
        UserPermissionOverride(
            user_id=user_id,
            permission_id=PERMISSIONS[permission].id,
            effect=effect,
            scope_kind="person",
            scope_id=person_id,
        )
    )


def _allowed_audits(db: Session, actor_id: UUID) -> set[UUID]:
    return set(
        db.scalars(
            select(AuditEvent.id).where(
                AuditEvent.actor_user_id == actor_id,
                AuditEvent.outcome == "allowed",
            )
        )
    )


@pytest.mark.parametrize("changes", [{}, {"full_name": "不应保存的新姓名"}])
def test_patch_cannot_return_read_denied_person_or_write_changes(
    db_client: TestClient, platform_users, db: Session, changes
) -> None:
    # Catches missing people.read authorization for empty and ordinary-field PATCHes.
    actor_id = platform_users["accounts"]["customer_service"].id
    person_id = platform_users["people"]["self_student"].id
    _override(db, actor_id, person_id, "people.update", "allow")
    _override(db, actor_id, person_id, "people.read", "deny")
    db.commit()
    assert RbacService(db).decide(
        user_id=actor_id,
        permission="people.update",
        resource_tokens={"global", f"person:{person_id}"},
    ).allowed
    csrf = sign_in(db_client, platform_users, "customer_service")
    assert db_client.get(f"/api/v1/people/{person_id}").status_code == 403
    person = db.get(Person, person_id)
    assert person is not None
    before = {column.key: getattr(person, column.key) for column in Person.__table__.columns}
    allowed_before = _allowed_audits(db, actor_id)

    response = db_client.patch(
        f"/api/v1/people/{person_id}",
        headers={"X-CSRF-Token": csrf},
        json=changes,
    )

    db.expire_all()
    stored = db.get(Person, person_id)
    assert stored is not None
    assert {
        column.key: getattr(stored, column.key) for column in Person.__table__.columns
    } == before
    assert _allowed_audits(db, actor_id) == allowed_before
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert "student@example.com" not in response.text


@pytest.mark.parametrize(
    ("changes", "expected_name"),
    [({}, "自助账号学员"), ({"full_name": "经授权新姓名"}, "经授权新姓名")],
)
def test_patch_with_subject_read_and_update_returns_existing_detail_contract(
    db_client: TestClient, platform_users, db: Session, changes, expected_name: str
) -> None:
    # Catches an overbroad gate that blocks permitted scoped updates or changes the DTO.
    actor_id = platform_users["accounts"]["customer_service"].id
    person_id = platform_users["people"]["self_student"].id
    _override(db, actor_id, person_id, "people.update", "allow")
    _override(db, actor_id, person_id, "people.read", "allow")
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")

    response = db_client.patch(
        f"/api/v1/people/{person_id}",
        headers={"X-CSRF-Token": csrf},
        json=changes,
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(person_id)
    assert response.json()["full_name"] == expected_name
    assert response.json()["email"] == "student@example.com"
    assert response.json()["phone"] == "138****0004"
    assert response.json()["student_number"] == "API-S-004"
    stored = db.get(Person, person_id, populate_existing=True)
    assert stored is not None
    assert stored.full_name == expected_name


def test_guardian_add_read_deny_prevents_link_and_allowed_audit(
    db_client: TestClient, platform_users, db: Session
) -> None:
    # Catches checking update alone, or checking guardian read only after the write.
    actor_id = platform_users["accounts"]["customer_service"].id
    student_id = platform_users["people"]["other_student"].id
    guardian_id = platform_users["people"]["guardian"].id
    for person_id in (student_id, guardian_id):
        _override(db, actor_id, person_id, "people.update", "allow")
    _override(db, actor_id, guardian_id, "people.read", "deny")
    db.commit()
    for person_id in (student_id, guardian_id):
        assert RbacService(db).decide(
            user_id=actor_id,
            permission="people.update",
            resource_tokens={"global", f"person:{person_id}"},
        ).allowed
    csrf = sign_in(db_client, platform_users, "customer_service")
    assert db_client.get(f"/api/v1/people/{guardian_id}").status_code == 403
    allowed_before = _allowed_audits(db, actor_id)
    links_before = set(db.scalars(select(GuardianRelationship.id)))

    response = db_client.post(
        f"/api/v1/people/{student_id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian_id),
            "relationship": "mother",
            "is_primary": True,
            "valid_from": "2026-01-01",
        },
    )

    db.expire_all()
    assert set(db.scalars(select(GuardianRelationship.id))) == links_before
    assert _allowed_audits(db, actor_id) == allowed_before
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    assert "guardian@example.com" not in response.text


def test_guardian_add_with_guardian_read_and_both_updates_succeeds(
    db_client: TestClient, platform_users, db: Session
) -> None:
    # Catches authorizing the wrong read subject or rejecting a permitted relationship.
    actor_id = platform_users["accounts"]["customer_service"].id
    student_id = platform_users["people"]["other_student"].id
    guardian_id = platform_users["people"]["guardian"].id
    for person_id in (student_id, guardian_id):
        _override(db, actor_id, person_id, "people.update", "allow")
    _override(db, actor_id, guardian_id, "people.read", "allow")
    _override(db, actor_id, student_id, "people.read", "deny")
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    assert db_client.get(f"/api/v1/people/{student_id}").status_code == 403

    response = db_client.post(
        f"/api/v1/people/{student_id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian_id),
            "relationship": "mother",
            "is_primary": True,
            "valid_from": "2026-01-01",
        },
    )

    assert response.status_code == 201
    assert response.json()["id"] == str(guardian_id)
    assert response.json()["email"] == "guardian@example.com"
    assert response.json()["relationship"] == "mother"
    relationship_id = UUID(response.json()["relationship_id"])
    stored = db.get(GuardianRelationship, relationship_id, populate_existing=True)
    assert stored is not None
    assert stored.student_person_id == student_id
    assert stored.guardian_person_id == guardian_id
    event = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "guardian.relationship.create",
            AuditEvent.resource_id == relationship_id,
            AuditEvent.actor_user_id == actor_id,
            AuditEvent.outcome == "allowed",
        )
    )
    assert event is not None
