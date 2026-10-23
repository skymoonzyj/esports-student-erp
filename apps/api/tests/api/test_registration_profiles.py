from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.identity.models import (
    GuardianProfile,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.rbac.models import Role, UserRole
from app.registration.delivery import CapturingVerificationSender
from app.registration.models import (
    RegistrationApplication,
    RegistrationConsentAcceptance,
    StaffIdentityApplication,
)
from app.security.passwords import PasswordService


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _open_registration_session(
    client: TestClient,
    sender: CapturingVerificationSender,
    *,
    purpose: str,
    phone: str,
) -> tuple[UUID, str]:
    started = client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": purpose,
            "identifier_kind": "phone",
            "identifier": phone,
        },
    )
    assert started.status_code == 201
    application_id = UUID(started.json()["application_id"])
    challenged = client.post(
        f"/api/v1/registration/applications/{application_id}/challenge"
    )
    assert challenged.status_code == 201
    challenge_id = UUID(challenged.json()["challenge_id"])
    verified = client.post(
        f"/api/v1/registration/applications/{application_id}/verify",
        json={
            "challenge_id": str(challenge_id),
            "code": sender.code_for(challenge_id),
        },
    )
    assert verified.status_code == 200
    return application_id, verified.json()["csrf_token"]


def complete_profile_payload(
    purpose: str,
    *,
    sequence: int = 711,
) -> dict[str, object]:
    details: dict[str, str] = {}
    if purpose == "staff_applicant":
        details = {
            "requested_staff_type": "coach",
            "staff_application_reason": "申请担任青训教练",
        }
    elif purpose == "visitor":
        details = {"visit_reason": "探望营期学员"}
    return {
        "password": "Reg711StrongAa",
        "full_name": f"{purpose}注册人",
        "identity_number": make_cn_id("19900102", sequence),
        "sex": "male",
        "date_of_birth": "1990-01-02",
        "consent_version": "2026-08",
        "purpose_details": details,
    }


@pytest.mark.parametrize(
    ("purpose", "sequence"),
    [
        ("student", 711),
        ("guardian", 713),
        ("staff_applicant", 715),
        ("visitor", 717),
    ],
)
def test_profile_requires_exact_purpose_fields_and_persists_consent(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    purpose: str,
    sequence: int,
) -> None:
    application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose=purpose,
        phone=f"1380000{sequence:04d}",
    )
    payload = complete_profile_payload(purpose, sequence=sequence)

    response = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 200
    assert response.json()["primary_purpose"] == purpose
    assert response.json()["state"] == "ready_to_finalize"
    assert payload["identity_number"] not in response.text
    assert payload["password"] not in response.text
    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    assert application.password_hash is not None
    assert application.password_hash != payload["password"]
    assert PasswordService().verify(
        str(payload["password"]),
        application.password_hash,
    )
    assert application.identity_ciphertext is not None
    assert application.identity_digest is not None
    assert application.full_name == payload["full_name"]
    consent = db.scalar(
        select(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.registration_application_id
            == application_id
        )
    )
    assert consent is not None
    assert consent.policy_version == "2026-08"
    assert consent.consent_type == "registration_terms"
    assert len(consent.content_digest) == 64


def test_profile_accepts_post_for_mobile_webview_compatibility(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    _application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose="staff_applicant",
        phone="13800000727",
    )

    response = db_client.post(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json={
            "password": "MobileAa0830",
            "full_name": "测试教练",
            "identity_number": make_cn_id("20000830", 563),
            "sex": "male",
            "date_of_birth": "2000-08-30",
            "consent_version": "2026-08",
            "purpose_details": {
                "requested_staff_type": "coach",
                "staff_application_reason": "王者教练",
                "visit_reason": None,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["primary_purpose"] == "staff_applicant"
    assert response.json()["state"] == "ready_to_finalize"


def test_minor_student_profile_waits_for_guardian_completion(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    _application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose="student",
        phone="13800000719",
    )
    payload = complete_profile_payload("student", sequence=719)
    payload["identity_number"] = make_cn_id("20120102", 719)
    payload["date_of_birth"] = "2012-01-02"

    completed = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )
    finalized = db_client.post(
        "/api/v1/registration/me/finalize",
        headers={"X-Registration-CSRF-Token": csrf},
    )

    assert completed.status_code == 200
    assert completed.json()["state"] == "identity_completed"
    assert finalized.status_code == 200
    assert finalized.json() == {
        "state": "pending_guardian",
        "next_action": "complete_guardian",
        "request_id": finalized.json()["request_id"],
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update({"unexpected": "forbidden"}),
        lambda payload: payload["purpose_details"].update(
            {"visit_reason": "不属于学员字段"}
        ),
        lambda payload: payload.update({"password": "weak-password"}),
        lambda payload: payload.update({"sex": "female"}),
        lambda payload: payload.update({"date_of_birth": "1991-01-02"}),
        lambda payload: payload.update({"full_name": "㍿" * 21}),
    ],
    ids=[
        "extra-field",
        "wrong-purpose-field",
        "weak-password",
        "identity-sex-mismatch",
        "identity-birth-mismatch",
        "normalized-full-name-too-long",
    ],
)
def test_invalid_profile_is_rejected_without_partial_persistence(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    mutate,
) -> None:
    application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose="student",
        phone="13800000721",
    )
    payload = complete_profile_payload("student", sequence=721)
    mutate(payload)

    response = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 422
    assert str(payload["password"]) not in response.text
    assert str(payload["identity_number"]) not in response.text
    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    assert application.state == "contact_verified"
    assert application.password_hash is None
    assert application.identity_ciphertext is None
    assert db.scalar(
        select(func.count(RegistrationConsentAcceptance.id)).where(
            RegistrationConsentAcceptance.registration_application_id
            == application_id
        )
    ) == 0


@pytest.mark.parametrize(
    ("purpose", "field", "sensitive_value", "sequence"),
    [
        ("staff_applicant", "staff_application_reason", "联系手机号 13800009991", 741),
        ("staff_applicant", "staff_application_reason", "联系邮箱 coach@example.com", 743),
        ("visitor", "visit_reason", "身份证 110105199001021234", 745),
    ],
)
def test_profile_business_text_rejects_plaintext_pii_without_persistence(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    purpose: str,
    field: str,
    sensitive_value: str,
    sequence: int,
) -> None:
    application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose=purpose,
        phone=f"1380000{sequence:04d}",
    )
    payload = complete_profile_payload(purpose, sequence=sequence)
    details = payload["purpose_details"]
    assert isinstance(details, dict)
    details[field] = sensitive_value

    response = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 422
    assert sensitive_value not in response.text
    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    assert application.state == "contact_verified"
    assert application.staff_application_reason is None
    assert application.visit_reason is None
    assert application.password_hash is None


def test_profile_completion_is_single_use(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    _application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose="guardian",
        phone="13800000723",
    )
    payload = complete_profile_payload("guardian", sequence=723)
    first = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )
    second = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == "registration_action_unavailable"


def test_profile_identity_conflict_returns_safe_profile_conflict(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    _first_application_id, first_csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose="staff_applicant",
        phone="13800000729",
    )
    first_payload = complete_profile_payload("staff_applicant", sequence=729)
    first = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": first_csrf},
        json=first_payload,
    )
    assert first.status_code == 200

    _second_application_id, second_csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose="staff_applicant",
        phone="13800000731",
    )
    second = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": second_csrf},
        json=first_payload,
    )

    assert second.status_code == 409
    assert second.json()["code"] == "registration_profile_conflict"
    assert "uq_registration" not in second.text


@pytest.mark.parametrize(
    ("purpose", "sequence", "details"),
    [
        ("staff_applicant", 731, {}),
        ("staff_applicant", 733, {"visit_reason": "错误访客字段"}),
        ("visitor", 735, {}),
        (
            "visitor",
            737,
            {
                "requested_staff_type": "coach",
                "staff_application_reason": "错误工作人员字段",
            },
        ),
        ("guardian", 739, {"visit_reason": "错误监护人字段"}),
    ],
)
def test_each_purpose_rejects_missing_or_foreign_details(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    purpose: str,
    sequence: int,
    details: dict[str, str],
) -> None:
    application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose=purpose,
        phone=f"1390000{sequence:04d}",
    )
    payload = complete_profile_payload(purpose, sequence=sequence)
    payload["purpose_details"] = details

    response = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=payload,
    )

    assert response.status_code == 422
    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    assert application is not None and application.state == "contact_verified"
    assert application.password_hash is None


@pytest.mark.parametrize(
    ("purpose", "sequence", "expected_roles"),
    [
        ("student", 751, {"registered_user", "student"}),
        ("guardian", 753, {"registered_user", "guardian"}),
        ("staff_applicant", 755, {"registered_user"}),
        ("visitor", 757, {"registered_user"}),
    ],
)
def test_completed_adult_purpose_finalizes_through_public_api(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    purpose: str,
    sequence: int,
    expected_roles: set[str],
) -> None:
    application_id, csrf = _open_registration_session(
        db_client,
        captured_sender,
        purpose=purpose,
        phone=f"1380000{sequence:04d}",
    )
    completed = db_client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json=complete_profile_payload(purpose, sequence=sequence),
    )
    finalized = db_client.post(
        "/api/v1/registration/me/finalize",
        headers={"X-Registration-CSRF-Token": csrf},
    )

    assert completed.status_code == finalized.status_code == 200
    assert finalized.json()["state"] == "finalized"
    assert finalized.json()["next_action"] == "login"
    db.expire_all()
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    assert application.finalized_account_id is not None
    assert application.matched_person_id is not None
    account = db.get(UserAccount, application.finalized_account_id)
    assert account is not None
    assert account.self_service_primary_purpose == purpose
    role_keys = set(
        db.scalars(
            select(Role.key)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == account.id)
        )
    )
    assert role_keys == expected_roles
    if purpose == "student":
        assert db.get(StudentProfile, application.matched_person_id) is not None
    elif purpose == "guardian":
        assert db.get(GuardianProfile, application.matched_person_id) is not None
    elif purpose == "staff_applicant":
        assert db.get(StaffProfile, application.matched_person_id) is None
        staff_application = db.scalar(
            select(StaffIdentityApplication).where(
                StaffIdentityApplication.person_id
                == application.matched_person_id
            )
        )
        assert staff_application is not None
        assert staff_application.status == "pending"
    else:
        visitor = db.get(VisitorProfile, application.matched_person_id)
        assert visitor is not None and visitor.visit_reason == "探望营期学员"
