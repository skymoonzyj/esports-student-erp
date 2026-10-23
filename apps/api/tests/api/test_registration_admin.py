from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from fastapi import Request, Response
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.identity.schemas import PersonBaseInput
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.registration import admin_router
from app.registration.consents import registration_terms_digest
from app.registration.finalization_service import (
    RegistrationFinalizationService,
    RegistrationFinalizationUnavailable,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    RegistrationApplication,
    RegistrationConsentAcceptance,
    RegistrationSession,
    StaffIdentityApplication,
)
from app.registration.review_service import (
    RegistrationReviewNotFound,
    RegistrationReviewService,
)
from app.registration.schemas import (
    RegistrationAdminListItem,
    RegistrationReviewRejectInput,
    StaffApplicationAdminListItem,
)
from app.registration.staff_service import StaffIdentityApplicationService
from app.registration.verification_service import registration_credential_lock_keys
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

NOW = datetime.now(UTC)


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


@dataclass(frozen=True)
class ConflictScenario:
    application_id: UUID
    candidate_person_id: UUID
    identity_number: str
    phone: str


def _mapped_snapshot(row: object) -> dict[str, object]:
    return {
        column.key: getattr(row, column.key)
        for column in row.__mapper__.columns  # type: ignore[attr-defined]
    }


def _conflict_scenario(
    db: Session,
    platform_users,
    *,
    candidate_key: str = "adult_without_account",
    full_name: str = "待开通账号人员",
    birth: str = "19900102",
    sequence: int = 110,
    phone: str = "13800000741",
    candidate_person_id: UUID | None = None,
) -> ConflictScenario:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    identity_number = make_cn_id(birth, sequence)
    identifier = normalize_registration_identifier("phone", phone)
    resolved_candidate_person_id = (
        platform_users["people"][candidate_key].id
        if candidate_person_id is None
        else candidate_person_id
    )
    application = RegistrationApplication(
        primary_purpose="student",
        state="conflict",
        verified_identifier_kind="phone",
        verified_identifier_digest=registration_identifier_digest(lookup, identifier),
        verified_identifier_ciphertext=cipher.encrypt(
            identifier.value,
            field_name="registration_identifier",
        ),
        password_hash=PasswordService().hash("Admin741ReviewAa"),
        full_name=full_name,
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="registration_identity",
        ),
        identity_digest=lookup.digest(identity_number),
        sex="female",
        date_of_birth=date(
            int(birth[0:4]),
            int(birth[4:6]),
            int(birth[6:8]),
        ),
        conflict_code="manual_review_required",
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    terms_digest = registration_terms_digest("2026-08")
    assert terms_digest is not None
    db.add_all(
        [
            RegistrationConsentAcceptance(
                registration_application_id=application.id,
                account_id=None,
                consent_type="registration_terms",
                policy_version="2026-08",
                content_digest=terms_digest,
                accepted_at=NOW,
                request_id="00000000-0000-4000-8000-000000000741",
                source="self_registration",
            ),
            RegistrationSession(
                application_id=application.id,
                token_digest=hashlib.sha256(
                    f"registration-admin-session:{application.id}".encode()
                ).hexdigest(),
                csrf_digest=hashlib.sha256(
                    f"registration-admin-csrf:{application.id}".encode()
                ).hexdigest(),
                expires_at=NOW + timedelta(hours=8),
            ),
        ]
    )
    db.commit()
    return ConflictScenario(
        application_id=application.id,
        candidate_person_id=resolved_candidate_person_id,
        identity_number=identity_number,
        phone=phone,
    )


def _login_customer_service_with_review(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> str:
    account = platform_users["accounts"]["customer_service"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS["registration.review"].id,
            effect="allow",
            scope_kind="global",
            scope_id=None,
            created_by_user_id=platform_users["accounts"]["admin"].id,
            grant_reason="处理脱敏注册冲突",
        )
    )
    db.commit()
    phone, password, _role = platform_users["credentials"]["customer_service"]
    response = db_client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _deny_person_permission(
    db: Session,
    platform_users,
    *,
    permission: str,
    person_id: UUID,
) -> None:
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=admin.id,
            permission_id=PERMISSIONS[permission].id,
            effect="deny",
            scope_kind="person",
            scope_id=person_id,
            created_by_user_id=admin.id,
            grant_reason="限制指定人员注册审核范围",
        )
    )
    db.commit()


def _allow_person_permission(
    db: Session,
    platform_users,
    *,
    account_key: str,
    permission: str,
    person_id: UUID,
) -> None:
    account = platform_users["accounts"][account_key]
    admin = platform_users["accounts"]["admin"]
    db.add(
        UserPermissionOverride(
            user_id=account.id,
            permission_id=PERMISSIONS[permission].id,
            effect="allow",
            scope_kind="person",
            scope_id=person_id,
            created_by_user_id=admin.id,
            grant_reason="允许查看指定人员注册敏感资料",
        )
    )
    db.commit()


def test_registration_admin_requires_formal_session(db_client: TestClient) -> None:
    response = db_client.get("/api/v1/registration-admin/applications")

    assert response.status_code == 401


def test_registration_admin_list_requires_review_capability(
    db_client: TestClient,
    login_as_customer_service,
) -> None:
    del login_as_customer_service

    response = db_client.get("/api/v1/registration-admin/applications")

    assert response.status_code == 403
    assert response.json()["code"] == "registration_review_denied"


def test_registration_channel_settings_requires_review_capability(
    db_client: TestClient,
    login_as_customer_service,
) -> None:
    del login_as_customer_service

    response = db_client.get("/api/v1/registration-admin/channel-settings")

    assert response.status_code == 403
    assert response.json()["code"] == "registration_review_denied"


def test_conflict_list_filters_person_denial_and_keeps_allowed_control(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    denied = _conflict_scenario(db, platform_users)
    control_person = platform_users["identity_service"].create_base_person(
        PersonBaseInput(
            full_name="允许审核的冲突人员",
            identity_number=make_cn_id("19920102", 120),
            sex="female",
            date_of_birth=date(1992, 1, 2),
            phone=None,
            email=None,
        )
    )
    db.commit()
    allowed = _conflict_scenario(
        db,
        platform_users,
        full_name="允许审核的冲突人员",
        birth="19920102",
        sequence=120,
        phone="13800000742",
        candidate_person_id=control_person.id,
    )
    _deny_person_permission(
        db,
        platform_users,
        permission="registration.review",
        person_id=denied.candidate_person_id,
    )

    response = db_client.get("/api/v1/registration-admin/applications")

    assert response.status_code == 200
    assert {item["application_id"] for item in response.json()} == {
        str(allowed.application_id)
    }
    assert str(denied.application_id) not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


def test_conflict_reviewer_without_sensitive_read_gets_masked_detail(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    _login_customer_service_with_review(db_client, db, platform_users)

    listed = db_client.get("/api/v1/registration-admin/applications")
    detail = db_client.get(
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )

    assert listed.status_code == detail.status_code == 200
    assert len(listed.json()) == 1
    assert detail.json()["masked_identifier"] == "138****0741"
    assert detail.json()["candidate_count"] == 1
    assert "identity_number" not in detail.text
    assert "verified_identifier" not in detail.text
    assert "candidate_person_id" not in detail.text
    assert scenario.identity_number not in detail.text
    assert scenario.phone not in detail.text


def test_masked_reviewer_never_decrypts_identity_or_candidate_fields(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    _login_customer_service_with_review(db_client, db, platform_users)
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
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )

    assert response.status_code == 200
    assert "registration_identifier" in decrypted_fields
    assert "registration_identity" not in decrypted_fields
    assert "identity_number" not in decrypted_fields
    assert "phone" not in decrypted_fields
    assert "email" not in decrypted_fields


def test_sensitive_reviewer_gets_validated_application_and_candidate_detail(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    del login_as_admin
    scenario = _conflict_scenario(db, platform_users)

    response = db_client.get(
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )

    assert response.status_code == 200
    assert response.json()["identity_number"] == scenario.identity_number
    assert response.json()["verified_identifier"] == scenario.phone
    assert response.json()["candidates"] == [
        {
            "person_id": str(scenario.candidate_person_id),
            "full_name": "待开通账号人员",
            "identity_number": scenario.identity_number,
            "sex": "female",
            "date_of_birth": "1990-01-02",
            "deleted": False,
        }
    ]


def test_person_scoped_sensitive_allow_reveals_only_authorized_candidate_detail(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    _login_customer_service_with_review(db_client, db, platform_users)
    _allow_person_permission(
        db,
        platform_users,
        account_key="customer_service",
        permission="registration.sensitive.read",
        person_id=scenario.candidate_person_id,
    )

    response = db_client.get(
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )

    assert response.status_code == 200
    assert response.json()["identity_number"] == scenario.identity_number
    assert response.json()["verified_identifier"] == scenario.phone
    assert response.json()["candidates"][0]["person_id"] == str(
        scenario.candidate_person_id
    )


def test_admin_reject_clears_temporary_secrets_without_formal_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    before_people = db.scalar(select(func.count(Person.id)))
    before_accounts = db.scalar(select(func.count(UserAccount.id)))

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/reject",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"reason": "实名资料需先线下更正"},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "rejected"
    db.expire_all()
    application = db.get(RegistrationApplication, scenario.application_id)
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == scenario.application_id
        )
    )
    assert application is not None
    assert application.password_hash is None
    assert application.identity_ciphertext is None
    assert application.verified_identifier_ciphertext is None
    assert application.rejected_at is not None
    assert session is not None and session.revoked_at is not None
    assert db.scalar(select(func.count(Person.id))) == before_people
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "registration.conflict.reject",
            AuditEvent.resource_id == scenario.application_id,
        )
    )
    assert audit is not None and audit.outcome == "allowed"


def test_admin_reject_persists_full_500_character_reason(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    reason = "审" * 500

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/reject",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"reason": reason},
    )

    assert response.status_code == 200
    db.expire_all()
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None
    assert application.review_decision == "rejected"
    assert application.review_reason == reason
    assert application.reviewed_by_user_id == login_as_admin.account_id
    assert application.reviewed_at is not None
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "registration.conflict.reject",
            AuditEvent.resource_id == scenario.application_id,
        )
    )
    assert audit is not None
    assert audit.purpose == "registration_conflict_review"
    assert audit.event_metadata == {"review_decision": "rejected"}


def test_candidate_scoped_review_deny_reject_is_private_and_zero_write(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    _deny_person_permission(
        db,
        platform_users,
        permission="registration.review",
        person_id=scenario.candidate_person_id,
    )
    application = db.get(RegistrationApplication, scenario.application_id)
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == scenario.application_id
        )
    )
    assert application is not None
    assert session is not None
    before_application = _mapped_snapshot(application)
    before_session = _mapped_snapshot(session)
    before_audits = [
        _mapped_snapshot(audit)
        for audit in db.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ]
    headers = {"X-CSRF-Token": login_as_admin.csrf_token}
    payload = {"reason": "人员范围明确禁止该冲突审核"}

    missing = db_client.post(
        f"/api/v1/registration-admin/applications/{uuid4()}/reject",
        headers=headers,
        json=payload,
    )
    denied = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/reject",
        headers=headers,
        json=payload,
    )

    assert denied.status_code == missing.status_code == 404
    assert {
        key: value for key, value in denied.json().items() if key != "request_id"
    } == {
        key: value for key, value in missing.json().items() if key != "request_id"
    }
    assert denied.headers["Cache-Control"] == missing.headers["Cache-Control"] == (
        "no-store, private"
    )
    db.expire_all()
    stored_application = db.get(RegistrationApplication, scenario.application_id)
    stored_session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == scenario.application_id
        )
    )
    assert stored_application is not None
    assert stored_session is not None
    assert _mapped_snapshot(stored_application) == before_application
    assert _mapped_snapshot(stored_session) == before_session
    assert [
        _mapped_snapshot(audit)
        for audit in db.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ] == before_audits


def test_reject_rechecks_candidate_scope_after_candidate_drift(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    denied_person_id = platform_users["people"]["other_student"].id
    _deny_person_permission(
        db,
        platform_users,
        permission="registration.review",
        person_id=denied_person_id,
    )
    application = db.get(RegistrationApplication, scenario.application_id)
    original_candidate = db.get(Person, scenario.candidate_person_id)
    denied_person = db.get(Person, denied_person_id)
    assert application is not None
    assert application.identity_digest is not None
    assert original_candidate is not None
    assert denied_person is not None
    original_candidate_digest = original_candidate.identity_digest
    denied_person_digest = denied_person.identity_digest
    before_application = _mapped_snapshot(application)
    before_audits = [
        _mapped_snapshot(audit)
        for audit in db.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ]
    original_identity_candidates = (
        RegistrationReviewService._identity_candidate_ids
    )
    drifted = False

    def drift_after_candidate_snapshot(
        service: RegistrationReviewService,
        target: RegistrationApplication,
    ) -> list[UUID]:
        nonlocal drifted
        candidate_ids = original_identity_candidates(service, target)
        if not drifted:
            drifted = True
            original_candidate.identity_digest = hashlib.sha256(
                f"registration-review-drift:{uuid4()}".encode()
            ).hexdigest()
            db.flush()
            denied_person.identity_digest = target.identity_digest
            db.flush()
        return candidate_ids

    monkeypatch.setattr(
        RegistrationReviewService,
        "_identity_candidate_ids",
        drift_after_candidate_snapshot,
    )

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/reject",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={"reason": "候选人员发生漂移时不得继续驳回"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    db.expire_all()
    stored_application = db.get(
        RegistrationApplication,
        scenario.application_id,
    )
    stored_original_candidate = db.get(Person, scenario.candidate_person_id)
    stored_denied_person = db.get(Person, denied_person_id)
    assert stored_application is not None
    assert stored_original_candidate is not None
    assert stored_denied_person is not None
    assert _mapped_snapshot(stored_application) == before_application
    assert stored_original_candidate.identity_digest == original_candidate_digest
    assert stored_denied_person.identity_digest == denied_person_digest
    assert [
        _mapped_snapshot(audit)
        for audit in db.scalars(select(AuditEvent).order_by(AuditEvent.id))
    ] == before_audits


def test_admin_resolve_rechecks_candidate_and_finalizes_once(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "实名字段复核一致，允许认领",
        },
    )

    assert response.status_code == 200
    assert set(response.json()) == {"state", "next_action", "request_id"}
    assert response.json()["state"] == "finalized"
    assert response.json()["next_action"] == "login"
    assert db.scalar(
        select(func.count(UserAccount.id)).where(
            UserAccount.person_id == scenario.candidate_person_id
        )
    ) == 1
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "finalized"
    assert application.review_decision == "approved"
    assert application.review_reason == "实名字段复核一致，允许认领"
    assert application.reviewed_by_user_id == login_as_admin.account_id
    assert application.reviewed_candidate_person_id is None
    assert application.reviewed_at is not None
    review_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "registration.conflict.resolve",
            AuditEvent.resource_id == scenario.application_id,
        )
    )
    assert review_audit is not None
    assert review_audit.purpose == "registration_conflict_review"
    assert review_audit.event_metadata == {"review_decision": "approved"}
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action.in_(
                {"registration.conflict.resolve", "registration.finalize"}
            ),
        )
    ) == 2


def test_admin_resolve_minor_conflict_waits_for_guardian_without_formal_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    birth = "20120102"
    sequence = 118
    candidate = platform_users["identity_service"].create_base_person(
        PersonBaseInput(
            full_name="待监护冲突学员",
            identity_number=make_cn_id(birth, sequence),
            sex="female",
            date_of_birth=date(2012, 1, 2),
            phone=None,
            email=None,
        )
    )
    db.commit()
    scenario = _conflict_scenario(
        db,
        platform_users,
        full_name="待监护冲突学员",
        birth=birth,
        sequence=sequence,
        phone="13800000743",
        candidate_person_id=candidate.id,
    )
    invitation = GuardianRegistrationInvitation(
        application_id=scenario.application_id,
        student_person_id=None,
        invited_identifier_kind="phone",
        invited_identifier_digest="7" * 64,
        invited_identifier_ciphertext="pending-guardian-invitation",
        handoff_token_digest="8" * 64,
        relationship="mother",
        is_primary=True,
        status="pending",
        expires_at=NOW + timedelta(days=7),
    )
    db.add(invitation)
    db.commit()
    before_guardian_consents = db.scalar(
        select(func.count(RegistrationConsentAcceptance.id)).where(
            RegistrationConsentAcceptance.consent_type == "guardian_relationship"
        )
    )
    before_roles = db.scalar(select(func.count(UserRole.role_id)))
    request_id = "registration-review-minor-guardian-gate"

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={
            "X-CSRF-Token": login_as_admin.csrf_token,
            "X-Request-ID": request_id,
        },
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "实名字段复核一致，继续等待监护确认",
        },
    )

    assert response.status_code == 200
    account_count = db.scalar(
        select(func.count(UserAccount.id)).where(
            UserAccount.person_id == scenario.candidate_person_id
        )
    )
    assert (
        response.json()["state"],
        response.json()["next_action"],
        account_count,
    ) == ("pending_guardian", "complete_guardian", 0)
    assert response.json() == {
        "state": "pending_guardian",
        "next_action": "complete_guardian",
        "request_id": request_id,
    }
    assert response.headers["x-request-id"] == request_id

    db.expire_all()
    application = db.get(RegistrationApplication, scenario.application_id)
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == scenario.application_id
        )
    )
    stored_invitation = db.get(GuardianRegistrationInvitation, invitation.id)
    assert application is not None
    assert application.state == "pending_guardian"
    assert application.conflict_code is None
    assert application.matched_person_id is None
    assert application.finalized_account_id is None
    assert application.finalized_at is None
    assert application.review_decision == "approved"
    assert application.reviewed_by_user_id == login_as_admin.account_id
    assert (
        application.reviewed_candidate_person_id
        == scenario.candidate_person_id
    )
    assert application.review_reason == "实名字段复核一致，继续等待监护确认"
    assert application.reviewed_at is not None
    assert session is not None and session.revoked_at is None
    assert db.get(StudentProfile, scenario.candidate_person_id) is None
    assert db.scalar(
        select(func.count(UserRole.role_id))
        .join(UserAccount, UserAccount.id == UserRole.user_id)
        .where(UserAccount.person_id == scenario.candidate_person_id)
    ) == 0
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.student_person_id == scenario.candidate_person_id
        )
    ) == 0
    assert db.scalar(
        select(func.count(RegistrationConsentAcceptance.id)).where(
            RegistrationConsentAcceptance.consent_type == "guardian_relationship"
        )
    ) == before_guardian_consents
    assert db.scalar(select(func.count(UserRole.role_id))) == before_roles
    assert stored_invitation is not None
    assert stored_invitation.status == "pending"
    assert stored_invitation.accepted_by_person_id is None
    assert stored_invitation.accepted_at is None
    assert stored_invitation.handoff_token_digest == "8" * 64
    review_audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == scenario.application_id,
                AuditEvent.action == "registration.conflict.resolve",
            )
        )
    )
    assert len(review_audits) == 1
    assert review_audits[0].purpose == "registration_conflict_review"
    assert review_audits[0].event_metadata == {"review_decision": "approved"}
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action == "registration.finalize",
        )
    ) == 0


def test_admin_resolve_locks_claim_keys_before_application(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None
    identifier_digest = application.verified_identifier_digest
    identity_digest = application.identity_digest
    assert identifier_digest is not None
    assert identity_digest is not None
    expected_keys = set(
        registration_credential_lock_keys(
            {identifier_digest},
            identity_digests={identity_digest},
        )
    )
    statements: list[tuple[str, tuple[object, ...]]] = []

    def capture_statement(
        _connection,
        _cursor,
        statement: str,
        parameters,
        _context,
        _executemany,
    ) -> None:
        if isinstance(parameters, dict):
            values = tuple(parameters.values())
        elif isinstance(parameters, (list, tuple)):
            values = tuple(parameters)
        else:
            values = (parameters,)
        statements.append((" ".join(statement.casefold().split()), values))

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", capture_statement)
    try:
        response = db_client.post(
            f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
            headers={"X-CSRF-Token": login_as_admin.csrf_token},
            json={
                "candidate_person_id": str(scenario.candidate_person_id),
                "reason": "实名字段复核一致，允许认领",
            },
        )
    finally:
        event.remove(bind, "before_cursor_execute", capture_statement)

    assert response.status_code == 200
    application_lock_position = next(
        index
        for index, (statement, _parameters) in enumerate(statements)
        if "from registration_applications" in statement
        and "for update" in statement
    )
    key_positions = {
        key: next(
            index
            for index, (statement, parameters) in enumerate(statements)
            if "pg_advisory_xact_lock" in statement and key in parameters
        )
        for key in expected_keys
    }
    assert all(
        position < application_lock_position
        for position in key_positions.values()
    )


def test_candidate_scoped_sensitive_deny_hides_detail(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    _deny_person_permission(
        db,
        platform_users,
        permission="registration.sensitive.read",
        person_id=scenario.candidate_person_id,
    )

    response = db_client.get(
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"


def test_detail_projects_only_candidate_ids_authorized_before_decryption(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    monkeypatch.setattr(
        RegistrationReviewService,
        "candidate_person_ids",
        lambda _service, _application_id: [],
    )

    response = db_client.get(
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )

    assert response.status_code == 200
    assert response.json()["candidate_count"] == 0
    assert response.json()["candidates"] == []
    assert str(scenario.candidate_person_id) not in response.text


def test_candidate_scoped_sensitive_deny_blocks_resolve_without_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    _deny_person_permission(
        db,
        platform_users,
        permission="registration.sensitive.read",
        person_id=scenario.candidate_person_id,
    )
    before_accounts = db.scalar(select(func.count(UserAccount.id)))

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "候选实名一致但人员范围禁止审核",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    db.expire_all()
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "conflict"
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action.in_(
                {"registration.conflict.resolve", "registration.finalize"}
            ),
        )
    ) == 0


def test_resolve_rolls_back_review_when_finalization_fails(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario = _conflict_scenario(db, platform_users)

    def fail_finalize(
        _service: RegistrationFinalizationService,
        _application_id: UUID,
        *,
        request_id: str,
    ) -> None:
        assert request_id
        raise RegistrationFinalizationUnavailable("forced finalization failure")

    monkeypatch.setattr(RegistrationFinalizationService, "finalize", fail_finalize)

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "审核通过后模拟正式开通失败",
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "registration_review_data_unavailable"
    db.expire_all()
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None
    assert application.state == "conflict"
    assert application.conflict_code == "manual_review_required"
    assert application.review_decision is None
    assert application.reviewed_by_user_id is None
    assert application.review_reason is None
    assert application.reviewed_at is None
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action == "registration.conflict.resolve",
        )
    ) == 0


def test_mismatched_candidate_and_denied_object_are_private_404_with_zero_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_customer_service,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    before_accounts = db.scalar(select(func.count(UserAccount.id)))
    denied = db_client.get(
        f"/api/v1/registration-admin/applications/{scenario.application_id}"
    )
    mismatched = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_customer_service.csrf_token},
        json={
            "candidate_person_id": str(
                platform_users["people"]["other_student"].id
            ),
            "reason": "尝试选择不匹配人员",
        },
    )

    assert denied.status_code == mismatched.status_code == 404
    assert {
        key: value for key, value in denied.json().items() if key != "request_id"
    } == {
        key: value for key, value in mismatched.json().items() if key != "request_id"
    }
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "conflict"
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action.in_(
                {"registration.conflict.resolve", "registration.conflict.reject"}
            ),
            AuditEvent.outcome == "allowed",
        )
    ) == 0


def test_sensitive_reviewer_mismatched_candidate_is_private_404_with_zero_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    before_accounts = db.scalar(select(func.count(UserAccount.id)))

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "candidate_person_id": str(
                platform_users["people"]["other_student"].id
            ),
            "reason": "实名字段不匹配的错误候选",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "conflict"
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action == "registration.conflict.resolve",
            AuditEvent.outcome == "allowed",
        )
    ) == 0


def test_masked_reviewer_cannot_resolve_without_sensitive_read(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    csrf = _login_customer_service_with_review(db_client, db, platform_users)
    before_accounts = db.scalar(select(func.count(UserAccount.id)))

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": csrf},
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "缺少敏感资料权限不得认领",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "conflict"


def test_disabled_candidate_is_private_404_with_zero_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(
        db,
        platform_users,
        candidate_key="self_student",
        full_name="自助账号学员",
        birth="19950102",
        sequence=112,
        phone="13800000004",
    )
    account = platform_users["accounts"]["self_student"]
    account.status = "disabled"
    db.commit()
    before_accounts = db.scalar(select(func.count(UserAccount.id)))

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "停用账号不可通过冲突认领",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "conflict"


def test_deleted_candidate_is_private_404_with_zero_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    person = db.get(Person, scenario.candidate_person_id)
    assert person is not None
    person.deleted_at = NOW
    db.commit()
    before_accounts = db.scalar(select(func.count(UserAccount.id)))

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/resolve",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json={
            "candidate_person_id": str(scenario.candidate_person_id),
            "reason": "删除人员不可通过冲突认领",
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    application = db.get(RegistrationApplication, scenario.application_id)
    assert application is not None and application.state == "conflict"


@pytest.mark.parametrize("action", ["resolve", "reject"])
@pytest.mark.parametrize(
    "reason",
    [
        "请联系手机号 13800000000 后再处理",
        "请联系 reviewer@example.com 后再处理",
        "请联系 用户@example.com 后再处理",
        "请联系 user@例子.公司 后再处理",
        f"申请人身份证号 {make_cn_id('19900102', 111)} 需复核",
        "禁止记录 Token_Aa12345678901234567890",
        "验证码 verification-code-123456 不得写入",
        "验证码 verification code 123456 不得写入",
        "请求头 x-registration-csrf-token-abc123 不得写入",
    ],
    ids=[
        "phone",
        "ascii-email",
        "unicode-local-email",
        "unicode-domain-email",
        "identity",
        "secret",
        "hyphenated-verification-code",
        "spaced-verification-code",
        "hyphenated-registration-csrf",
    ],
)
def test_sensitive_review_reason_is_rejected_and_rolls_back(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
    action: str,
    reason: str,
) -> None:
    scenario = _conflict_scenario(db, platform_users)
    before_accounts = db.scalar(select(func.count(UserAccount.id)))
    payload: dict[str, str] = {"reason": reason}
    if action == "resolve":
        payload["candidate_person_id"] = str(scenario.candidate_person_id)

    response = db_client.post(
        f"/api/v1/registration-admin/applications/{scenario.application_id}/{action}",
        headers={"X-CSRF-Token": login_as_admin.csrf_token},
        json=payload,
    )

    assert response.status_code == 422
    assert response.json()["code"] == "registration_review_reason_invalid"
    db.expire_all()
    application = db.get(RegistrationApplication, scenario.application_id)
    session = db.scalar(
        select(RegistrationSession).where(
            RegistrationSession.application_id == scenario.application_id
        )
    )
    assert application is not None and application.state == "conflict"
    assert application.password_hash is not None
    assert application.identity_ciphertext is not None
    assert session is not None and session.revoked_at is None
    assert db.scalar(select(func.count(UserAccount.id))) == before_accounts
    assert db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.resource_id == scenario.application_id,
            AuditEvent.action.in_(
                {"registration.conflict.resolve", "registration.conflict.reject"}
            ),
        )
    ) == 0


def test_missing_application_is_private_404_for_reviewer(
    db_client: TestClient,
    login_as_admin,
) -> None:
    missing_id = uuid4()

    response = db_client.get(
        f"/api/v1/registration-admin/applications/{missing_id}"
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    assert response.headers["Cache-Control"] == "no-store, private"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.parametrize("action", ["resolve", "reject"])
def test_denied_reviewer_is_private_404_before_body_validation(
    db_client: TestClient,
    login_as_customer_service,
    action: str,
) -> None:
    response = db_client.post(
        f"/api/v1/registration-admin/applications/{uuid4()}/{action}",
        headers={"X-CSRF-Token": login_as_customer_service.csrf_token},
        json={},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_application_not_found"
    assert response.headers["Cache-Control"] == "no-store, private"


def test_staff_list_filters_person_denial_and_keeps_allowed_control(
    db_client: TestClient,
    db: Session,
    platform_users,
    login_as_admin,
) -> None:
    denied_person = platform_users["people"]["self_student"]
    allowed_person = platform_users["people"]["guardian"]
    for account_key in ("self_student", "guardian"):
        platform_users["accounts"][account_key].self_service_primary_purpose = (
            "staff_applicant"
        )
    denied = StaffIdentityApplication(
        person_id=denied_person.id,
        requested_staff_type="coach",
        application_reason="申请担任教练工作",
        status="pending",
    )
    allowed = StaffIdentityApplication(
        person_id=allowed_person.id,
        requested_staff_type="life_teacher",
        application_reason="申请担任生活老师工作",
        status="pending",
    )
    db.add_all([denied, allowed])
    db.commit()
    _deny_person_permission(
        db,
        platform_users,
        permission="staff_identity_applications.review",
        person_id=denied_person.id,
    )

    response = db_client.get("/api/v1/registration-admin/staff-applications")

    assert response.status_code == 200
    assert {item["application_id"] for item in response.json()} == {
        str(allowed.id)
    }
    assert str(denied.id) not in response.text
    assert response.headers["Cache-Control"] == "no-store, private"


def test_conflict_list_route_applies_candidate_scope_before_returning_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied_person_id = uuid4()
    denied_item = RegistrationAdminListItem(
        application_id=uuid4(),
        primary_purpose="student",
        state="conflict",
        masked_identifier="138****0741",
        conflict_code="manual_review_required",
        candidate_count=1,
        expires_at=NOW + timedelta(days=1),
        created_at=NOW,
    )
    allowed_item = denied_item.model_copy(update={"application_id": uuid4()})

    class FakeReviewService:
        def list_conflicts(
            self,
            *,
            candidate_allowed: Callable[[list[UUID]], bool] | None = None,
        ) -> list[RegistrationAdminListItem]:
            rows = [
                (denied_item, [denied_person_id]),
                (allowed_item, [uuid4()]),
            ]
            if candidate_allowed is None:
                return [item for item, _candidate_ids in rows]
            return [
                item
                for item, candidate_ids in rows
                if candidate_allowed(candidate_ids)
            ]

    def allowed(
        _db: Session,
        *,
        account_id: UUID,
        permission: str,
        person_id: UUID | None = None,
    ) -> bool:
        del account_id, permission
        return person_id != denied_person_id

    monkeypatch.setattr(admin_router, "_allowed", allowed)
    response = Response()

    result = admin_router.list_registration_conflicts(
        response=response,
        context=cast(
            AuthContext,
            SimpleNamespace(account=SimpleNamespace(id=uuid4())),
        ),
        db=cast(Session, object()),
        service=cast(RegistrationReviewService, FakeReviewService()),
    )

    assert [item.application_id for item in result] == [allowed_item.application_id]
    assert response.headers["Cache-Control"] == "no-store, private"


def test_conflict_list_skips_identity_denial_before_identifier_decryption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied_person_id = uuid4()
    allowed_person_id = uuid4()
    denied_application = RegistrationApplication(id=uuid4(), state="conflict")
    allowed_application = RegistrationApplication(id=uuid4(), state="conflict")
    denied_item = RegistrationAdminListItem(
        application_id=denied_application.id,
        primary_purpose="student",
        state="conflict",
        masked_identifier="138****0741",
        conflict_code="manual_review_required",
        candidate_count=1,
        expires_at=NOW + timedelta(days=1),
        created_at=NOW,
    )
    allowed_item = denied_item.model_copy(
        update={"application_id": allowed_application.id}
    )

    class FakeDb:
        def scalars(self, _statement: object) -> list[RegistrationApplication]:
            return [denied_application, allowed_application]

    service = object.__new__(RegistrationReviewService)
    service.db = cast(Session, FakeDb())
    identity_candidates = {
        denied_application.id: [denied_person_id],
        allowed_application.id: [allowed_person_id],
    }
    decrypted_application_ids: list[UUID] = []

    def record_identifier_decryption(application: RegistrationApplication):
        decrypted_application_ids.append(application.id)
        return normalize_registration_identifier("phone", "13800000741")

    monkeypatch.setattr(
        service,
        "_identity_candidate_ids",
        lambda application: identity_candidates[application.id],
        raising=False,
    )
    monkeypatch.setattr(
        service,
        "_identifier",
        record_identifier_decryption,
    )
    monkeypatch.setattr(
        service,
        "_candidate_ids",
        lambda application, _identifier, **_kwargs: identity_candidates[
            application.id
        ],
    )
    monkeypatch.setattr(
        service,
        "_base_item",
        lambda application, _identifier, **_kwargs: (
            denied_item if application.id == denied_application.id else allowed_item
        ),
    )

    result = service.list_conflicts(
        candidate_allowed=lambda person_ids: denied_person_id not in person_ids
    )

    assert [item.application_id for item in result] == [allowed_application.id]
    assert decrypted_application_ids == [allowed_application.id]


def test_reject_route_delegates_candidate_scope_to_service_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied_person_id = uuid4()

    class FakeReviewService:
        reject_called = False

        def reject(self, *_args: object, **kwargs: object) -> None:
            self.reject_called = True
            candidate_allowed = cast(
                Callable[[list[UUID]], bool],
                kwargs["candidate_allowed"],
            )
            if not candidate_allowed([denied_person_id]):
                raise RegistrationReviewNotFound(
                    "registration application not found"
                )

    def allowed(
        _db: Session,
        *,
        account_id: UUID,
        permission: str,
        person_id: UUID | None = None,
    ) -> bool:
        del account_id, permission
        return person_id != denied_person_id

    service = FakeReviewService()
    monkeypatch.setattr(admin_router, "_allowed", allowed)

    with pytest.raises(ProblemError) as error:
        admin_router.reject_registration_conflict(
            application_id=uuid4(),
            input=RegistrationReviewRejectInput(reason="人员范围明确禁止该冲突审核"),
            request=cast(
                Request,
                SimpleNamespace(state=SimpleNamespace(request_id="reject-scope-test")),
            ),
            response=Response(),
            context=cast(
                AuthContext,
                SimpleNamespace(account=SimpleNamespace(id=uuid4())),
            ),
            db=cast(Session, object()),
            service=cast(RegistrationReviewService, service),
        )

    assert (error.value.status, error.value.code) == (
        404,
        "registration_application_not_found",
    )
    assert service.reject_called is True


def test_staff_list_route_applies_person_scope_before_returning_items(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    denied_person_id = uuid4()
    denied_item = StaffApplicationAdminListItem(
        application_id=uuid4(),
        requested_staff_type="coach",
        status="pending",
        created_at=NOW,
    )
    allowed_item = denied_item.model_copy(update={"application_id": uuid4()})

    class FakeStaffService:
        def list_pending(
            self,
            *,
            person_allowed: Callable[[UUID], bool] | None = None,
        ) -> list[StaffApplicationAdminListItem]:
            rows = [
                (denied_item, denied_person_id),
                (allowed_item, uuid4()),
            ]
            if person_allowed is None:
                return [item for item, _person_id in rows]
            return [
                item for item, person_id in rows if person_allowed(person_id)
            ]

    def allowed(
        _db: Session,
        *,
        account_id: UUID,
        permission: str,
        person_id: UUID | None = None,
    ) -> bool:
        del account_id, permission
        return person_id != denied_person_id

    monkeypatch.setattr(admin_router, "_allowed", allowed)
    response = Response()

    result = admin_router.list_staff_identity_applications(
        response=response,
        context=cast(
            AuthContext,
            SimpleNamespace(account=SimpleNamespace(id=uuid4())),
        ),
        db=cast(Session, object()),
        service=cast(StaffIdentityApplicationService, FakeStaffService()),
    )

    assert [item.application_id for item in result] == [allowed_item.application_id]
    assert response.headers["Cache-Control"] == "no-store, private"
