from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.core.time as business_time
import app.identity.router as identity_router
from app.audit.models import AuditEvent
from app.audit.service import AuditMetadataRejected
from app.auth.models import AuthSession, UserAccount
from app.identity.models import GuardianRelationship
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.security.encryption import EnvelopeCipher


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    checksum = sum(int(digit) * weight for digit, weight in zip(body, weights))
    return body + checks[checksum % 11]


def sign_in(client: TestClient, platform_users, key: str) -> str:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post("/api/v1/auth/login", json={"identifier": phone, "password": password})
    assert response.status_code == 200
    return response.json()["csrf_token"]


def assert_minor_account_was_disabled(
    db: Session,
    account_id: UUID,
) -> None:
    account = db.get(UserAccount, account_id)
    assert account is not None
    assert account.status == "disabled"
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == account_id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 0


def test_ending_last_relationship_disables_minor(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in(db_client, platform_users, "admin")
    student = platform_users["people"]["minor_student"]

    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{minor_login.relationship.id}/end",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "end-last-guardian"},
        json={"reason": "监护安排结束"},
    )

    assert response.status_code == 200
    assert_minor_account_was_disabled(db, minor_login.account_id)
    actions = list(
        db.scalars(
            select(AuditEvent.action).where(
                AuditEvent.resource_id.in_(
                    [minor_login.relationship.id, minor_login.account_id]
                )
            )
        )
    )
    assert "guardian.relationship.end" in actions
    assert actions.count("account.auto_disable.guardian_missing") == 1


def test_clearing_all_guardian_contacts_disables_minor(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in(db_client, platform_users, "admin")
    guardian = platform_users["people"]["guardian"]

    response = db_client.patch(
        f"/api/v1/people/{guardian.id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "clear-guardian-contacts"},
        json={"phone": None, "email": None},
    )

    assert response.status_code == 200
    assert response.json()["email"] is None
    assert "guardian@example.com" not in response.text
    assert_minor_account_was_disabled(db, minor_login.account_id)
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == minor_login.account_id,
                AuditEvent.action == "account.auto_disable.guardian_missing",
            )
        )
    )
    assert len(events) == 1
    assert "guardian@example.com" not in str(events[0].event_metadata)


def test_correcting_guardian_birth_date_to_minor_disables_minor(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in(db_client, platform_users, "admin")
    guardian = platform_users["people"]["guardian"]
    corrected_identity = make_cn_id("20150102", 103)

    response = db_client.patch(
        f"/api/v1/people/{guardian.id}",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "correct-guardian-birth"},
        json={
            "identity_number": corrected_identity,
            "date_of_birth": "2015-01-02",
            "sex": "male",
        },
    )

    assert response.status_code == 200
    assert corrected_identity not in response.text
    assert_minor_account_was_disabled(db, minor_login.account_id)
    events = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == minor_login.account_id,
                AuditEvent.action == "account.auto_disable.guardian_missing",
            )
        )
    )
    assert len(events) == 1
    assert corrected_identity not in str(events[0].event_metadata)


def test_invalidating_one_of_two_valid_guardians_keeps_minor_active(
    db_client: TestClient,
    platform_users,
    minor_login,
    db: Session,
) -> None:
    second_guardian = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=second_guardian.id, role_id=ROLES["guardian"].id))
    db.commit()
    platform_users["identity_service"].add_guardian(
        platform_users["people"]["minor_student"].id,
        second_guardian.person_id,
        relationship="father",
        is_primary=False,
        valid_from=date(2026, 1, 1),
        valid_until=None,
    )
    assert minor_login.login(db_client).status_code == 200
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        "/api/v1/people/"
        f"{platform_users['people']['minor_student'].id}/guardians/"
        f"{minor_login.relationship.id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "监护安排结束"},
    )

    assert response.status_code == 200
    account = db.get(UserAccount, minor_login.account_id)
    assert account is not None and account.status == "active"
    assert db.scalar(
        select(func.count(AuthSession.id)).where(
            AuthSession.user_id == minor_login.account_id,
            AuthSession.revoked_at.is_(None),
        )
    ) == 1
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == minor_login.account_id,
            AuditEvent.action == "account.auto_disable.guardian_missing",
        )
    ) == 0


def test_admin_lists_adds_and_ends_guardian_relationships(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    own_student = platform_users["people"]["own_student"]
    other_student = platform_users["people"]["other_student"]
    guardian = platform_users["people"]["guardian"]

    listed = db_client.get(f"/api/v1/people/{own_student.id}/guardians")
    created = db_client.post(
        f"/api/v1/people/{other_student.id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian.id),
            "relationship": "father",
            "is_primary": False,
            "valid_from": "2026-01-01",
            "valid_until": None,
        },
    )
    relationship_id = created.json()["relationship_id"]
    ended = db_client.post(
        f"/api/v1/people/{other_student.id}/guardians/{relationship_id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "  监护安排变更  "},
    )
    history = db_client.get(
        f"/api/v1/people/{other_student.id}/guardians?include_inactive=true"
    )
    removed_route = db_client.delete(
        f"/api/v1/people/{other_student.id}/guardians/{guardian.id}",
        headers={"X-CSRF-Token": csrf},
    )

    assert listed.status_code == 200
    assert listed.json()[0]["id"] == str(guardian.id)
    assert listed.json()[0]["phone"] == "138****0003"
    assert listed.json()[0]["identity_number"].startswith("110105********")
    assert created.status_code == 201
    assert created.json()["status"] == "active"
    assert ended.status_code == 200
    assert ended.json()["relationship_id"] == relationship_id
    assert ended.json()["status"] == "ended"
    assert ended.json()["end_reason"] == "监护安排变更"
    assert db_client.get(f"/api/v1/people/{other_student.id}/guardians").json() == []
    assert [item["relationship_id"] for item in history.json()] == [relationship_id]
    assert history.json()[0]["status"] == "ended"
    assert removed_route.status_code == 405


def test_guardian_delete_tombstone_is_absent_from_openapi(
    db_client: TestClient,
) -> None:
    paths = db_client.get("/openapi.json").json()["paths"]

    assert "/api/v1/people/{student_id}/guardians/{guardian_id}" not in paths
    assert sorted(
        paths["/api/v1/people/{student_id}/guardians/{relationship_id}/end"]
    ) == ["post"]


def test_admin_can_manage_future_guardian_relationships(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    student = platform_users["people"]["other_student"]
    guardian = platform_users["people"]["guardian"]

    created = db_client.post(
        f"/api/v1/people/{student.id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian.id),
            "relationship": "father",
            "is_primary": False,
            "valid_from": "2027-01-01",
            "valid_until": None,
        },
    )
    active = db_client.get(f"/api/v1/people/{student.id}/guardians")
    managed = db_client.get(
        f"/api/v1/people/{student.id}/guardians?include_inactive=true"
    )

    assert created.status_code == 201
    assert created.json()["status"] == "scheduled"
    assert active.json() == []
    assert [item["id"] for item in managed.json()] == [str(guardian.id)]
    assert managed.json()[0]["relationship_id"] == created.json()["relationship_id"]
    assert managed.json()[0]["status"] == "scheduled"


def test_guardian_cannot_manage_relationships(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "guardian")
    student = platform_users["people"]["other_student"]
    guardian = platform_users["people"]["guardian"]

    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian.id),
            "relationship": "father",
            "is_primary": False,
            "valid_from": "2026-01-01",
            "valid_until": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_guardian_scope_uses_shanghai_business_date(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch,
) -> None:
    guardian = platform_users["people"]["guardian"]
    student = platform_users["people"]["own_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == guardian.id,
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    relationship.valid_from = date(2026, 7, 26)
    db.commit()

    real_datetime = datetime

    class FrozenDateTime:
        @classmethod
        def now(cls, timezone=None):  # type: ignore[no-untyped-def]
            fixed = real_datetime(2026, 7, 25, 16, 30, tzinfo=UTC)
            return fixed if timezone is None else fixed.astimezone(timezone)

    monkeypatch.setattr(business_time, "datetime", FrozenDateTime)
    sign_in(db_client, platform_users, "guardian")

    response = db_client.get(f"/api/v1/people/{student.id}")

    assert response.status_code == 200


def test_person_detail_exposes_object_scoped_account_management_capability(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    account = platform_users["accounts"]["guardian"]
    student = platform_users["people"]["own_student"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="allow",
            scope_kind="family",
            scope_id=None,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "guardian")

    response = db_client.get(f"/api/v1/people/{student.id}")

    assert response.status_code == 200
    assert response.json()["account_management_allowed"] is True


def test_guardian_cannot_list_other_guardian_profiles(
    db_client: TestClient,
    platform_users,
    monkeypatch,
) -> None:
    sign_in(db_client, platform_users, "guardian")
    student = platform_users["people"]["own_student"]
    decrypted_fields: list[str] = []
    original_decrypt = EnvelopeCipher.decrypt

    def record_decryption(cipher, payload, *, field_name):
        decrypted_fields.append(field_name)
        return original_decrypt(cipher, payload, field_name=field_name)

    monkeypatch.setattr(
        EnvelopeCipher,
        "decrypt",
        record_decryption,
    )

    response = db_client.get(f"/api/v1/people/{student.id}/guardians")

    assert response.status_code == 403
    assert "guardian@example.com" not in response.text
    assert decrypted_fields == []


def test_guardian_lists_relationship_after_receiving_person_scoped_access(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    account = platform_users["accounts"]["guardian"]
    guardian = platform_users["people"]["guardian"]
    student = platform_users["people"]["own_student"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["people.read"].id,
            effect="allow",
            scope_kind="person",
            scope_id=guardian.id,
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "guardian")

    response = db_client.get(f"/api/v1/people/{student.id}/guardians")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(guardian.id)]
    assert response.json()[0]["email"] == "guardian@example.com"


def test_student_scoped_update_ends_relationship_without_decrypting_unreadable_guardian(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch,
) -> None:
    customer = platform_users["accounts"]["customer_service"]
    student = platform_users["people"]["own_student"]
    guardian = platform_users["people"]["guardian"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == guardian.id,
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    db.add_all(
        [
            UserPermissionOverride(
                user_id=customer.id,
                permission_id=PERMISSIONS["people.update"].id,
                effect="allow",
                scope_kind="person",
                scope_id=student.id,
            ),
            UserPermissionOverride(
                user_id=customer.id,
                permission_id=PERMISSIONS["people.read"].id,
                effect="deny",
                scope_kind="person",
                scope_id=guardian.id,
            ),
        ]
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")
    decrypted_person_ids: list[UUID] = []
    original_decrypt = identity_router.IdentityService._decrypt_email

    def record_decryption(service, person):
        decrypted_person_ids.append(person.id)
        return original_decrypt(service, person)

    monkeypatch.setattr(
        identity_router.IdentityService,
        "_decrypt_email",
        record_decryption,
    )

    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{relationship.id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "监护安排变更"},
    )

    assert response.status_code == 200
    assert response.json()["email"] is None
    assert decrypted_person_ids == []


def test_person_scoped_update_cannot_bind_unscoped_guardian(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    customer = platform_users["accounts"]["customer_service"]
    student = platform_users["people"]["other_student"]
    guardian = platform_users["people"]["guardian"]
    db.add(
        UserPermissionOverride(
            user_id=customer.id,
            permission_id=PERMISSIONS["people.update"].id,
            effect="allow",
            scope_kind="person",
            scope_id=student.id,
        )
    )
    db.commit()
    csrf = sign_in(db_client, platform_users, "customer_service")

    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian.id),
            "relationship": "father",
            "is_primary": False,
            "valid_from": "2026-01-01",
            "valid_until": None,
        },
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"


def test_guardian_cannot_end_relationship(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    guardian = platform_users["people"]["guardian"]
    student = platform_users["people"]["own_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.guardian_person_id == guardian.id,
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    csrf = sign_in(db_client, platform_users, "guardian")

    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{relationship.id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "监护安排变更"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"
    db.refresh(relationship)
    assert relationship.ended_at is None


def test_guardian_end_rejects_short_reason(
    db_client: TestClient,
    platform_users,
    db: Session,
) -> None:
    student = platform_users["people"]["own_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    csrf = sign_in(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{relationship.id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": " 一 "},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_guardian_create_rolls_back_when_audit_append_fails(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch,
) -> None:
    student = platform_users["people"]["other_student"]
    guardian = platform_users["people"]["guardian"]
    csrf = sign_in(db_client, platform_users, "admin")

    def reject_audit(*args: object, **kwargs: object) -> None:
        raise AuditMetadataRejected("audit rejected")

    monkeypatch.setattr(identity_router.AuditService, "append", reject_audit)
    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians",
        headers={"X-CSRF-Token": csrf},
        json={
            "guardian_id": str(guardian.id),
            "relationship": "father",
            "is_primary": False,
            "valid_from": "2026-01-01",
            "valid_until": None,
        },
    )

    assert response.status_code == 500
    assert db.scalar(
        select(GuardianRelationship.id).where(
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.guardian_person_id == guardian.id,
        )
    ) is None


def test_guardian_end_rolls_back_when_audit_append_fails(
    db_client: TestClient,
    platform_users,
    db: Session,
    monkeypatch,
) -> None:
    student = platform_users["people"]["own_student"]
    relationship = db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id == student.id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert relationship is not None
    csrf = sign_in(db_client, platform_users, "admin")

    def reject_audit(*args: object, **kwargs: object) -> None:
        raise AuditMetadataRejected("audit rejected")

    monkeypatch.setattr(identity_router.AuditService, "append", reject_audit)
    response = db_client.post(
        f"/api/v1/people/{student.id}/guardians/{relationship.id}/end",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "监护安排变更"},
    )

    assert response.status_code == 500
    db.expire_all()
    persisted = db.get(GuardianRelationship, relationship.id)
    assert persisted is not None
    assert persisted.ended_at is None
    assert persisted.end_reason is None
