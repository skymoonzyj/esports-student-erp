from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.auth.schemas import AccountCreate
from app.auth.service import AccountService
from app.identity.models import Person, StaffProfile
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.registration.models import StaffIdentityApplication
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


@dataclass(frozen=True)
class ApiStaffApplicant:
    application_id: UUID
    person_id: UUID
    account_id: UUID
    phone: str
    password: str
    identity_number: str
    email: str


def _staff_applicant(
    db: Session,
    *,
    sequence: int,
    staff_type: str = "coach",
    application_reason: str | None = None,
) -> ApiStaffApplicant:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    identity = IdentityService(db, cipher=cipher, lookup=lookup)
    phone = f"1380000{sequence:04d}"
    email = f"api-staff-{sequence}@example.com"
    password = f"Staff{sequence}Aa"
    identity_number = make_cn_id("19900102", sequence)
    person = identity.create_base_person(
        PersonBaseInput(
            full_name=f"API 工作人员申请人{sequence}",
            identity_number=identity_number,
            sex="male",
            date_of_birth=date(1990, 1, 2),
            phone=phone,
            email=email,
        )
    )
    created = AccountService(
        db,
        identity_service=identity,
        passwords=PasswordService(),
        reference_date_provider=lambda: date(2026, 8, 15),
    ).create_account(
        AccountCreate(
            person_id=person.id,
            login_phone=phone,
            email=email,
        )
    )
    account = db.get(UserAccount, created.account.id)
    assert account is not None
    account.password_hash = PasswordService().hash(password)
    account.force_password_change = False
    account.self_service_primary_purpose = "staff_applicant"
    db.add(
        UserRole(
            user_id=account.id,
            role_id=ROLES["registered_user"].id,
        )
    )
    application = StaffIdentityApplication(
        person_id=person.id,
        requested_staff_type=staff_type,
        application_reason=(
            application_reason
            if application_reason is not None
            else f"申请担任 {staff_type} 工作"
        ),
        status="pending",
    )
    db.add(application)
    db.commit()
    return ApiStaffApplicant(
        application_id=application.id,
        person_id=person.id,
        account_id=account.id,
        phone=phone,
        password=password,
        identity_number=identity_number,
        email=email,
    )


def _login(client: TestClient, identifier: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _login_platform(client: TestClient, platform_users, key: str) -> str:
    identifier, password, _role = platform_users["credentials"][key]
    return _login(client, identifier, password)


def _grant(
    db: Session,
    platform_users,
    *,
    account_key: str,
    permission: str,
    person_id: UUID | None = None,
    effect: str = "allow",
) -> None:
    account = platform_users["accounts"][account_key]
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS[permission].id,
            effect=effect,
            scope_kind="person" if person_id is not None else "global",
            scope_id=person_id,
            created_by_user_id=admin.id,
            grant_reason="工作人员身份审核测试授权",
        )
    )
    db.commit()


def _business_counts(db: Session) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count(StaffProfile.person_id))) or 0,
        db.scalar(select(func.count(UserRole.user_id))) or 0,
        db.scalar(select(func.count(AuditEvent.id))) or 0,
        db.scalar(select(func.count(StaffIdentityApplication.id))) or 0,
    )


def test_staff_applicant_can_read_only_own_status(
    db_client: TestClient,
    db: Session,
) -> None:
    applicant = _staff_applicant(db, sequence=921)
    _login(db_client, applicant.phone, applicant.password)

    response = db_client.get("/api/v1/registration/staff-application")

    assert response.status_code == 200
    assert set(response.json()) == {
        "application_id",
        "requested_staff_type",
        "application_reason",
        "status",
        "reviewed_at",
        "created_at",
    }
    assert response.json()["application_id"] == str(applicant.application_id)
    assert "review_reason" not in response.text
    assert "reviewed_by_user_id" not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


@pytest.mark.parametrize(
    "actor_key",
    ["customer_service", "coach", "guardian", "self_student"],
)
def test_unprivileged_actor_cannot_review_staff_application(
    db_client: TestClient,
    db: Session,
    platform_users,
    actor_key: str,
) -> None:
    applicant = _staff_applicant(db, sequence=923)
    csrf = _login_platform(db_client, platform_users, actor_key)
    before = _business_counts(db)

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "审核通过"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "staff_application_not_found"
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"
    assert _business_counts(db) == before


def test_review_list_requires_capability_but_object_denials_are_private(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=925)
    _login_platform(db_client, platform_users, "customer_service")

    listed = db_client.get("/api/v1/registration-admin/staff-applications")
    detail = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )

    assert listed.status_code == 403
    assert listed.json()["code"] == "staff_application_review_denied"
    assert detail.status_code == 404
    assert detail.json()["code"] == "staff_application_not_found"


def test_masked_reviewer_and_sensitive_reviewer_get_distinct_allowlists(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=927)
    _grant(
        db,
        platform_users,
        account_key="customer_service",
        permission="staff_identity_applications.review",
    )
    _login_platform(db_client, platform_users, "customer_service")

    listed = db_client.get("/api/v1/registration-admin/staff-applications")
    masked = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )

    assert listed.status_code == masked.status_code == 200
    assert set(listed.json()[0]) == {
        "application_id",
        "requested_staff_type",
        "status",
        "created_at",
    }
    assert set(masked.json()) == {
        "application_id",
        "requested_staff_type",
        "status",
        "created_at",
        "application_reason",
    }
    assert applicant.identity_number not in masked.text
    assert applicant.phone not in masked.text
    assert applicant.email not in masked.text
    assert str(applicant.person_id) not in masked.text

    admin_csrf = _login_platform(db_client, platform_users, "admin")
    sensitive = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )

    assert sensitive.status_code == 200
    assert sensitive.json()["person_id"] == str(applicant.person_id)
    assert sensitive.json()["identity_number"] == applicant.identity_number
    assert sensitive.json()["sex"] == "male"
    assert sensitive.json()["date_of_birth"] == "1990-01-02"
    assert sensitive.json()["phone"] == applicant.phone
    assert sensitive.json()["email"] == applicant.email
    assert admin_csrf


def test_masked_reviewer_never_decrypts_identity_or_contacts(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applicant = _staff_applicant(db, sequence=935)
    _grant(
        db,
        platform_users,
        account_key="customer_service",
        permission="staff_identity_applications.review",
    )
    _login_platform(db_client, platform_users, "customer_service")
    decrypted_fields: list[str] = []
    original = EnvelopeCipher.decrypt

    def record_decrypt(
        cipher: EnvelopeCipher,
        token: str,
        *,
        field_name: str,
    ) -> str:
        decrypted_fields.append(field_name)
        return original(cipher, token, field_name=field_name)

    monkeypatch.setattr(EnvelopeCipher, "decrypt", record_decrypt)

    response = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )

    assert response.status_code == 200
    assert decrypted_fields == []


def test_person_scoped_reviewer_can_handle_only_delegated_application(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    delegated = _staff_applicant(db, sequence=937)
    other = _staff_applicant(db, sequence=939)
    _grant(
        db,
        platform_users,
        account_key="customer_service",
        permission="staff_identity_applications.review",
        person_id=delegated.person_id,
    )
    csrf = _login_platform(db_client, platform_users, "customer_service")

    listed = db_client.get("/api/v1/registration-admin/staff-applications")
    allowed_detail = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{delegated.application_id}"
    )
    denied_detail = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{other.application_id}"
    )
    approved = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{delegated.application_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "委派范围内审核通过"},
    )

    assert listed.status_code == 403
    assert allowed_detail.status_code == 200
    assert denied_detail.status_code == 404
    assert approved.status_code == 200


def test_admin_approval_never_grants_admin_role(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(
        db,
        sequence=929,
        staff_type="administration",
    )
    csrf = _login_platform(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={},
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "application_id",
        "status",
        "requested_staff_type",
        "assigned_role",
        "request_id",
    }
    assert response.json()["assigned_role"] == "administration"
    assert db.scalar(
        select(func.count(UserRole.user_id)).where(
            UserRole.user_id == applicant.account_id,
            UserRole.role_id.in_((ROLES["admin"].id, ROLES["super_admin"].id)),
        )
    ) == 0
    profile = db.get(StaffProfile, applicant.person_id)
    assert profile is not None and profile.staff_type == "administration"
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.review_reason is None


def test_admin_rejection_preserves_registered_user_account(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=947, staff_type="coach")
    csrf = _login_platform(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/reject",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "资料暂不符合审核要求"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert response.json()["assigned_role"] is None
    account = db.get(UserAccount, applicant.account_id)
    assert account is not None and account.status == "active"
    assert db.get(StaffProfile, applicant.person_id) is None
    assert db.scalar(
        select(func.count(UserRole.user_id)).where(
            UserRole.user_id == applicant.account_id,
            UserRole.role_id != ROLES["registered_user"].id,
        )
    ) == 0


def test_admin_rejection_still_requires_a_review_reason(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=949, staff_type="coach")
    csrf = _login_platform(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/reject",
        headers={"X-CSRF-Token": csrf},
        json={},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"


def test_person_scoped_deny_is_private_and_zero_write(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=931)
    _grant(
        db,
        platform_users,
        account_key="admin",
        permission="staff_identity_applications.review",
        person_id=applicant.person_id,
        effect="deny",
    )
    csrf = _login_platform(db_client, platform_users, "admin")
    before = _business_counts(db)

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/reject",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "暂不符合工作人员要求"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "staff_application_not_found"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"
    assert _business_counts(db) == before


def test_review_inputs_are_strict_and_errors_are_private_no_store(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=933)
    csrf = _login_platform(db_client, platform_users, "admin")

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "审核通过", "role": "admin"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"


def test_unprivileged_invalid_review_body_is_still_private_404(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=943)
    csrf = _login_platform(db_client, platform_users, "customer_service")

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "x", "role": "admin"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "staff_application_not_found"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"


def test_unprivileged_malformed_json_is_still_private_404(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    applicant = _staff_applicant(db, sequence=951)
    csrf = _login_platform(db_client, platform_users, "customer_service")
    before = _business_counts(db)

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/approve",
        headers={
            "X-CSRF-Token": csrf,
            "Content-Type": "application/json",
        },
        content="{",
    )

    assert response.status_code == 404
    assert response.json()["code"] == "staff_application_not_found"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"
    assert _business_counts(db) == before


@pytest.mark.parametrize("scenario", ["disabled", "wrong_purpose"])
def test_ineligible_staff_applications_are_absent_from_every_admin_selector(
    db_client: TestClient,
    db: Session,
    platform_users,
    scenario: str,
) -> None:
    applicant = _staff_applicant(db, sequence=953)
    account = db.get(UserAccount, applicant.account_id)
    assert account is not None
    if scenario == "disabled":
        account.status = "disabled"
    else:
        account.self_service_primary_purpose = "guardian"
    db.commit()
    csrf = _login_platform(db_client, platform_users, "admin")
    before = _business_counts(db)

    listed = db_client.get("/api/v1/registration-admin/staff-applications")
    detail = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )
    approved = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "资料审核通过"},
    )

    assert listed.status_code == 200
    assert all(
        item["application_id"] != str(applicant.application_id)
        for item in listed.json()
    )
    assert detail.status_code == 404
    assert detail.json()["code"] == "staff_application_not_found"
    assert approved.status_code == 404
    assert approved.json()["code"] == "staff_application_not_found"
    db.expire_all()
    stored = db.get(StaffIdentityApplication, applicant.application_id)
    assert stored is not None and stored.status == "pending"
    assert _business_counts(db) == before


@pytest.mark.parametrize(
    "reason",
    [
        "请联系 13800001234 或 private@example.com 安排面试",
        "本人张三，住址北京市朝阳区示例路1号，微信 zhangsan_88",
    ],
)
def test_masked_detail_hides_every_free_text_application_reason(
    db_client: TestClient,
    db: Session,
    platform_users,
    reason: str,
) -> None:
    applicant = _staff_applicant(
        db,
        sequence=955,
        application_reason=reason,
    )
    _grant(
        db,
        platform_users,
        account_key="customer_service",
        permission="staff_identity_applications.review",
    )
    _login_platform(db_client, platform_users, "customer_service")

    masked = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )

    assert masked.status_code == 200
    assert masked.json()["application_reason"] == "申请说明需敏感资料权限查看"
    assert masked.json()["application_reason"] != reason

    _login_platform(db_client, platform_users, "admin")
    sensitive = db_client.get(
        f"/api/v1/registration-admin/staff-applications/{applicant.application_id}"
    )
    assert sensitive.status_code == 200
    assert sensitive.json()["application_reason"] == reason


@pytest.mark.parametrize("scenario", ["missing", "deleted", "disabled"])
def test_unavailable_staff_application_targets_are_private_and_zero_write(
    db_client: TestClient,
    db: Session,
    platform_users,
    scenario: str,
) -> None:
    applicant = _staff_applicant(db, sequence=945)
    target_id = applicant.application_id
    if scenario == "missing":
        target_id = uuid4()
    elif scenario == "deleted":
        person = db.get(Person, applicant.person_id)
        assert person is not None
        person.deleted_at = datetime.now(UTC)
        db.commit()
    else:
        account = db.get(UserAccount, applicant.account_id)
        assert account is not None
        account.status = "disabled"
        db.commit()
    csrf = _login_platform(db_client, platform_users, "admin")
    before = _business_counts(db)

    response = db_client.post(
        f"/api/v1/registration-admin/staff-applications/{target_id}/approve",
        headers={"X-CSRF-Token": csrf},
        json={"reason": "资料审核通过"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "staff_application_not_found"
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    assert _business_counts(db) == before
