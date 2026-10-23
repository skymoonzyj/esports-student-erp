from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.registration.router as registration_router
from app.audit.models import AuditEvent
from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person
from app.rbac.models import UserRole
from app.registration.delivery import CapturingVerificationSender
from app.registration.guardian_service import (
    GuardianInvitationFlowContext,
    GuardianRegistrationService,
)
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.invitation_flow import (
    guardian_invitation_handoff_token_digest,
    issue_guardian_invitation_handoff_token,
)
from app.registration.invitation_retention import (
    REDACTED_GUARDIAN_INVITATION_CIPHERTEXT,
    REDACTED_GUARDIAN_INVITATION_DIGEST,
    REDACTED_GUARDIAN_INVITATION_MASK,
)
from app.registration.models import (
    GuardianRegistrationInvitation,
    RegistrationApplication,
    RegistrationConsentAcceptance,
)
from app.security.encryption import EnvelopeCipher, LookupDigest


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _open_minor_registration(
    client: TestClient,
    sender: CapturingVerificationSender,
    *,
    phone: str,
    sequence: int,
) -> tuple[UUID, str]:
    started = client.post(
        "/api/v1/registration/applications",
        json={
            "primary_purpose": "student",
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
    csrf = verified.json()["csrf_token"]
    completed = client.patch(
        "/api/v1/registration/me",
        headers={"X-Registration-CSRF-Token": csrf},
        json={
            "password": "Guard701FlowAa",
            "full_name": "API 待监护学员",
            "identity_number": make_cn_id("20120102", sequence),
            "sex": "male",
            "date_of_birth": "2012-01-02",
            "consent_version": "2026-08",
            "purpose_details": {},
        },
    )
    assert completed.status_code == 200
    assert completed.json()["state"] == "identity_completed"
    return application_id, csrf


def _login(client: TestClient, platform_users, key: str):
    identifier, password, _role = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()["csrf_token"]


def _handoff(client: TestClient, token: str) -> None:
    response = client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": token},
    )
    assert response.status_code == 204


def _invitation_response_fields() -> set[str]:
    return {
        "id",
        "masked_identifier",
        "relationship",
        "is_primary",
        "status",
        "expires_at",
    }


def _store_formal_invitation(
    db: Session,
    platform_users,
    *,
    identifier: str,
    target_key: str = "self_student",
    status: str = "pending",
    expired: bool = False,
    accepted_by_key: str | None = None,
) -> tuple[GuardianRegistrationInvitation, str]:
    cipher = EnvelopeCipher(bytes(range(32)))
    lookup = LookupDigest(bytes(range(32)))
    normalized = normalize_registration_identifier("phone", identifier)
    handoff_token = issue_guardian_invitation_handoff_token()
    handoff_token_digest = guardian_invitation_handoff_token_digest(
        lookup,
        handoff_token,
    )
    assert handoff_token_digest is not None
    now = datetime.now(UTC)
    invitation = GuardianRegistrationInvitation(
        application_id=None,
        student_person_id=platform_users["people"][target_key].id,
        invited_identifier_kind=normalized.kind,
        invited_identifier_digest=registration_identifier_digest(
            lookup,
            normalized,
        ),
        invited_identifier_ciphertext=cipher.encrypt(
            normalized.value,
            field_name="guardian_invitation_identifier",
        ),
        handoff_token_digest=(
            handoff_token_digest if status == "pending" else None
        ),
        relationship="parent",
        is_primary=False,
        status=status,
        expires_at=now - timedelta(seconds=1) if expired else now + timedelta(days=7),
        accepted_by_person_id=(
            platform_users["people"][accepted_by_key].id
            if accepted_by_key is not None
            else None
        ),
        accepted_at=now if accepted_by_key is not None else None,
        revoked_at=now if status == "revoked" else None,
    )
    db.add(invitation)
    db.commit()
    return invitation, handoff_token


def _business_write_counts(db: Session) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count(GuardianRelationship.id))) or 0,
        db.scalar(select(func.count(UserAccount.id))) or 0,
        db.scalar(select(func.count(RegistrationConsentAcceptance.id))) or 0,
        db.scalar(select(func.count()).select_from(UserRole)) or 0,
    )


def test_minor_invitation_acceptance_finalizes_student_without_early_access(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    platform_users,
) -> None:
    application_id, registration_csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008701",
        sequence=701,
    )
    invited = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": registration_csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "19990000005",
            "relationship": "mother",
            "is_primary": True,
        },
    )

    assert invited.status_code == 201
    assert set(invited.json()) == _invitation_response_fields()
    invitation_id = UUID(invited.json()["id"])
    assert invited.json()["masked_identifier"] == "138****0003"
    assert "19990000005" not in invited.text
    assert db_client.get("/api/v1/auth/me").status_code == 401

    _handoff(db_client, captured_sender.code_for(invitation_id))
    guardian_csrf = _login(db_client, platform_users, "guardian")
    accepted = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": guardian_csrf},
        json={"consent_version": "2026-08"},
    )

    assert accepted.status_code == 200
    assert set(accepted.json()) == {
        "status",
        "student_finalized",
        "request_id",
    }
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["student_finalized"] is True
    db.expire_all()
    stored_invitation = db.get(GuardianRegistrationInvitation, invitation_id)
    assert stored_invitation is not None
    assert (
        stored_invitation.invited_identifier_digest
        == REDACTED_GUARDIAN_INVITATION_DIGEST
    )
    assert (
        stored_invitation.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )
    application = db.get(RegistrationApplication, application_id)
    assert application is not None and application.state == "finalized"
    assert application.matched_person_id is not None
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.student_person_id == application.matched_person_id,
            GuardianRelationship.guardian_person_id
            == platform_users["people"]["guardian"].id,
        )
    ) == 1
    assert db.scalar(
        select(func.count(RegistrationConsentAcceptance.id)).where(
            RegistrationConsentAcceptance.account_id
            == platform_users["accounts"]["guardian"].id,
            RegistrationConsentAcceptance.consent_type == "guardian_relationship",
        )
    ) == 1


def test_pending_minor_reconfirms_current_terms_before_guardian_finalization(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    platform_users,
) -> None:
    application_id, registration_csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008717",
        sequence=717,
    )
    consent = db.scalar(
        select(RegistrationConsentAcceptance).where(
            RegistrationConsentAcceptance.registration_application_id
            == application_id,
            RegistrationConsentAcceptance.consent_type == "registration_terms",
        )
    )
    assert consent is not None
    consent.policy_version = "2026-07"
    consent.content_digest = hashlib.sha256(b"legacy registration terms").hexdigest()
    db.commit()

    status = db_client.get("/api/v1/registration/me/consents/registration-terms")
    assert status.status_code == 200
    assert status.json() == {
        "current_version": "2026-08",
        "accepted_version": "2026-07",
        "reconfirmation_required": True,
        "request_id": status.json()["request_id"],
    }

    invited = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": registration_csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "19990000005",
            "relationship": "mother",
            "is_primary": True,
        },
    )
    assert invited.status_code == 201
    invitation_id = UUID(invited.json()["id"])
    _handoff(db_client, captured_sender.code_for(invitation_id))
    guardian_csrf = _login(db_client, platform_users, "guardian")

    stale_acceptance = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": guardian_csrf},
        json={"consent_version": "2026-08"},
    )
    assert stale_acceptance.status_code == 409

    reconfirmed = db_client.post(
        "/api/v1/registration/me/consents/registration-terms",
        headers={"X-Registration-CSRF-Token": registration_csrf},
        json={"consent_version": "2026-08"},
    )
    assert reconfirmed.status_code == 200
    assert reconfirmed.json()["reconfirmation_required"] is False
    assert reconfirmed.json()["accepted_version"] == "2026-08"

    accepted = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": guardian_csrf},
        json={"consent_version": "2026-08"},
    )
    assert accepted.status_code == 200
    assert accepted.json()["student_finalized"] is True


def test_registration_cookie_restores_csrf_after_page_reload(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
) -> None:
    _application_id, original_csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008711",
        sequence=711,
    )

    restored = db_client.get("/api/v1/registration/csrf")
    state = db_client.get("/api/v1/registration/me")

    assert restored.status_code == 200
    assert state.status_code == 200
    assert state.json()["primary_purpose"] == "student"
    rotated_csrf = restored.json()["csrf_token"]
    assert rotated_csrf != original_csrf
    accepted = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": rotated_csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "19990000005",
            "relationship": "mother",
            "is_primary": True,
        },
    )
    rejected_old = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": original_csrf},
        json={
            "identifier_kind": "email",
            "identifier": "guardian-old-csrf@example.com",
            "relationship": "father",
            "is_primary": False,
        },
    )

    assert accepted.status_code == 201
    assert rejected_old.status_code == 403
    assert rejected_old.json()["code"] == "registration_csrf_invalid"


def test_guardian_invitation_handoff_context_is_private_and_read_only(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    invited_phone = "13800008721"
    invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier=invited_phone,
    )
    before = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))

    handed_off = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    )
    context = db_client.get(
        "/api/v1/registration/guardian-invitations/context"
    )

    assert handed_off.status_code == 204
    set_cookie = handed_off.headers["Set-Cookie"]
    assert "erp_guardian_invitation_flow=" in set_cookie
    assert "HttpOnly" in set_cookie
    assert "SameSite=lax" in set_cookie
    assert "Path=/api/v1/registration/guardian-invitations" in set_cookie
    assert context.status_code == 200
    assert set(context.json()) == {
        "masked_identifier",
        "relationship",
        "is_primary",
        "expires_at",
        "consent_version",
        "request_id",
    }
    assert context.json()["masked_identifier"] == "138****8721"
    assert context.json()["relationship"] == "parent"
    assert context.json()["consent_version"] == "2026-08"
    assert invited_phone not in context.text
    assert str(invitation.id) not in context.text
    assert str(platform_users["people"]["self_student"].id) not in context.text
    assert context.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    after = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))
    assert after == before


def test_invalid_guardian_invitation_handoff_preserves_existing_flow_without_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    _invitation, existing_token = _store_formal_invitation(
        db,
        platform_users,
        identifier="13800008729",
    )
    established = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": existing_token},
    )
    assert established.status_code == 204
    before = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))

    handed_off = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": "not-an-invitation-token"},
    )
    context = db_client.get(
        "/api/v1/registration/guardian-invitations/context"
    )

    assert handed_off.status_code == 404
    assert handed_off.json()["code"] == "registration_action_unavailable"
    assert "Set-Cookie" not in handed_off.headers
    assert handed_off.headers["Cache-Control"] == "no-store, private"
    assert context.status_code == 200
    assert context.json()["masked_identifier"] == "138****8729"
    db.expire_all()
    after = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))
    assert after == before


@pytest.mark.parametrize("cookie_mode", ["duplicate", "tampered"])
def test_guardian_invitation_context_rejects_ambiguous_or_tampered_cookie(
    db_client: TestClient,
    db: Session,
    platform_users,
    cookie_mode: str,
) -> None:
    _invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier="13800008722",
    )
    handed_off = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    )
    assert handed_off.status_code == 204
    cookie_name = "erp_guardian_invitation_flow"
    signed_value = handed_off.headers["Set-Cookie"].split(
        f"{cookie_name}=", 1
    )[1].split(";", 1)[0]
    if cookie_mode == "duplicate":
        cookie_header = (
            f"{cookie_name}={signed_value}; {cookie_name}={signed_value}"
        )
    else:
        replacement = "A" if signed_value[-1] != "A" else "B"
        cookie_header = f"{cookie_name}={signed_value[:-1]}{replacement}"
    db_client.cookies.clear()
    before = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))

    response = db_client.get(
        "/api/v1/registration/guardian-invitations/context",
        headers={"Cookie": cookie_header},
    )

    assert response.status_code == 404
    assert response.json()["code"] == "registration_action_unavailable"
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    after = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))
    assert after == before


@pytest.mark.parametrize("scenario", ["missing", "expired", "revoked", "accepted"])
def test_guardian_invitation_context_has_equivalent_private_errors_without_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    scenario: str,
) -> None:
    handoff_token = issue_guardian_invitation_handoff_token()
    if scenario != "missing":
        _, handoff_token = _store_formal_invitation(
            db,
            platform_users,
            identifier="13800008723",
            status=(
                "revoked"
                if scenario == "revoked"
                else "accepted"
                if scenario == "accepted"
                else "pending"
            ),
            expired=scenario == "expired",
            accepted_by_key="guardian" if scenario == "accepted" else None,
        )
    before = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))

    handed_off = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    )
    response = db_client.get(
        "/api/v1/registration/guardian-invitations/context"
    )

    assert handed_off.status_code == 404
    assert handed_off.json()["code"] == "registration_action_unavailable"
    assert "Set-Cookie" not in handed_off.headers
    assert response.status_code == 404
    assert {
        key: value for key, value in response.json().items() if key != "request_id"
    } == {
        "type": "about:blank",
        "title": "Not Found",
        "status": 404,
        "detail": "注册操作不可用，请重新开始或稍后重试",
        "code": "registration_action_unavailable",
    }
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    after = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))
    assert after == before


@pytest.mark.parametrize("scenario", ["disabled", "deleted", "non_student"])
def test_guardian_invitation_context_rejects_ineligible_targets_without_creating_application(
    db_client: TestClient,
    db: Session,
    platform_users,
    scenario: str,
) -> None:
    target_key = "guardian" if scenario == "non_student" else "self_student"
    _invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier="13800008724",
        target_key=target_key,
    )
    if scenario == "disabled":
        account = platform_users["accounts"]["self_student"]
        account.status = "disabled"
    elif scenario == "deleted":
        person = db.get(Person, platform_users["people"]["self_student"].id)
        assert person is not None
        person.deleted_at = datetime.now(UTC)
    db.commit()
    before_applications = db.scalar(select(func.count(RegistrationApplication.id))) or 0
    before = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))

    handoff = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    )
    context = db_client.get("/api/v1/registration/guardian-invitations/context")
    started = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={},
    )

    assert handoff.status_code == context.status_code == 404
    assert handoff.json()["code"] == "registration_action_unavailable"
    assert "Set-Cookie" not in handoff.headers
    assert started.status_code == 409
    assert context.json()["code"] == started.json()["code"] == (
        "registration_action_unavailable"
    )
    db.expire_all()
    assert (db.scalar(select(func.count(RegistrationApplication.id))) or 0) == (
        before_applications
    )
    after = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))
    assert after == before


def test_guardian_invitation_acceptance_rejects_a_disabled_student_without_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    guardian_identifier = platform_users["credentials"]["guardian"][0]
    invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier=guardian_identifier,
    )
    assert db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    ).status_code == 204
    csrf = _login(db_client, platform_users, "guardian")
    student_account = platform_users["accounts"]["self_student"]
    student_account.status = "disabled"
    db.commit()
    before = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))

    response = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": csrf},
        json={"consent_version": "2026-08"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "registration_action_unavailable"
    db.expire_all()
    stored = db.get(GuardianRegistrationInvitation, invitation.id)
    assert stored is not None
    assert stored.status == "pending"
    after = (*_business_write_counts(db), db.scalar(select(func.count(AuditEvent.id))))
    assert after == before


def test_guardian_invitation_start_forces_guardian_purpose_and_invited_contact(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    platform_users,
) -> None:
    invited_phone = "13800008725"
    _invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier=invited_phone,
    )
    assert db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    ).status_code == 204

    rejected_override = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={"primary_purpose": "student", "identifier": "13800009999"},
    )
    started = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={},
    )

    assert rejected_override.status_code == 422
    assert started.status_code == 201
    assert started.json()["masked_identifier"] == "138****8725"
    application_id = UUID(started.json()["application_id"])
    application = db.get(RegistrationApplication, application_id)
    assert application is not None
    assert application.primary_purpose == "guardian"
    assert application.verified_identifier_kind == "phone"
    assert application.verified_identifier_digest == registration_identifier_digest(
        LookupDigest(bytes(range(32))),
        normalize_registration_identifier("phone", invited_phone),
    )

    challenged = db_client.post(
        f"/api/v1/registration/applications/{application_id}/challenge"
    )
    assert challenged.status_code == 201
    message = captured_sender.message_for(UUID(challenged.json()["challenge_id"]))
    assert message.destination == invited_phone
    assert message.purpose == "registration"


def test_repeated_guardian_invitation_start_returns_pending_without_disclosure(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    invited_phone = "13800008726"
    _invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier=invited_phone,
    )
    assert db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    ).status_code == 204
    first = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={},
    )
    assert first.status_code == 201
    application_id = first.json()["application_id"]

    repeated = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={},
    )

    assert repeated.status_code == 429
    assert repeated.json()["code"] == "registration_application_pending"
    assert repeated.json()["detail"] == "已有注册申请正在处理中，请继续原流程或稍后重试"
    assert 1 <= int(repeated.headers["Retry-After"]) <= 600
    assert "application_id" not in repeated.json()
    assert application_id not in repeated.text
    assert invited_phone not in repeated.text


def test_guardian_invitation_start_runs_global_cleanup_before_owned_write(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier="13800008727",
    )
    assert db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    ).status_code == 204
    calls: list[tuple[str, str | None]] = []

    class CleanupService:
        def __init__(self, session: Session) -> None:
            self.db = session

        def prune(self, *, limit: int) -> int:
            assert limit == 25
            transaction = self.db.get_transaction()
            calls.append(
                (
                    "global",
                    transaction.origin.name if transaction is not None else None,
                )
            )
            self.db.commit()
            return 0

        def prune_identifier(self, *, identifier_digest: str) -> int:
            assert len(identifier_digest) == 64
            transaction = self.db.get_transaction()
            calls.append(
                (
                    "identifier",
                    transaction.origin.name if transaction is not None else None,
                )
            )
            return 0

    monkeypatch.setattr(
        registration_router,
        "RegistrationCleanupService",
        CleanupService,
    )

    response = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={},
    )

    assert response.status_code == 201
    assert calls == [("global", "AUTOBEGIN"), ("identifier", "BEGIN")]


def test_guardian_invitation_start_locks_identifier_before_target_rows(
    db_client: TestClient,
    db: Session,
    platform_users,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier="13800008728",
    )
    assert db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    ).status_code == 204
    events: list[str] = []
    real_lock = registration_router.lock_registration_credential_keys
    real_context = GuardianRegistrationService.invitation_flow_context

    def record_identifier_lock(session: Session, keys: list[int]) -> None:
        events.append("identifier")
        real_lock(session, keys)

    def record_context(
        self: GuardianRegistrationService,
        invitation_id: UUID,
        *,
        lock: bool = False,
    ) -> GuardianInvitationFlowContext:
        if lock:
            events.append("target")
        return real_context(self, invitation_id, lock=lock)

    monkeypatch.setattr(
        registration_router,
        "lock_registration_credential_keys",
        record_identifier_lock,
    )
    monkeypatch.setattr(
        GuardianRegistrationService,
        "invitation_flow_context",
        record_context,
    )

    response = db_client.post(
        "/api/v1/registration/guardian-invitations/start-registration",
        json={},
    )

    assert response.status_code == 201
    assert events[:2] == ["identifier", "target"]


def test_logged_in_guardian_accepts_handed_off_invitation_without_token_in_url(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    guardian_identifier = platform_users["credentials"]["guardian"][0]
    invitation, handoff_token = _store_formal_invitation(
        db,
        platform_users,
        identifier=guardian_identifier,
    )
    assert db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    ).status_code == 204
    csrf = _login(db_client, platform_users, "guardian")

    accepted = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": csrf},
        json={"consent_version": "2026-08"},
    )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
    assert accepted.json()["student_finalized"] is False
    assert str(invitation.id) not in accepted.request.url.path
    assert 'erp_guardian_invitation_flow=""' not in accepted.headers.get(
        "Set-Cookie",
        "",
    )
    assert db_client.get(
        "/api/v1/registration/guardian-invitations/context"
    ).status_code == 404
    receipt = db_client.get(
        "/api/v1/registration/guardian-invitations/acceptance"
    )
    repeated = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": csrf},
        json={"consent_version": "2026-08"},
    )

    assert receipt.status_code == 200
    assert receipt.json() == {
        "status": "accepted",
        "student_finalized": False,
        "request_id": receipt.json()["request_id"],
    }
    assert repeated.status_code == 200
    assert repeated.json()["status"] == "accepted"
    assert db.scalar(
        select(func.count(GuardianRelationship.id)).where(
            GuardianRelationship.student_person_id
            == platform_users["people"]["self_student"].id,
            GuardianRelationship.guardian_person_id
            == platform_users["people"]["guardian"].id,
            GuardianRelationship.ended_at.is_(None),
        )
    ) == 1

    _login(db_client, platform_users, "self_student")
    denied_receipt = db_client.get(
        "/api/v1/registration/guardian-invitations/acceptance"
    )
    assert denied_receipt.status_code == 404
    assert denied_receipt.json()["code"] == "registration_action_unavailable"


def test_minor_can_replace_own_contact_only_after_fresh_verification(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    application_id, csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008713",
        sequence=713,
    )
    before = db.get(RegistrationApplication, application_id)
    assert before is not None
    previous_digest = before.verified_identifier_digest
    new_email = "minor-updated-8713@example.com"

    challenged = db_client.post(
        "/api/v1/registration/me/contact-challenges",
        headers={"X-Registration-CSRF-Token": csrf},
        json={"identifier_kind": "email", "identifier": new_email},
    )
    assert challenged.status_code == 201
    challenge_id = UUID(challenged.json()["challenge_id"])
    changed = db_client.post(
        "/api/v1/registration/me/contact",
        headers={"X-Registration-CSRF-Token": csrf},
        json={
            "challenge_id": str(challenge_id),
            "code": captured_sender.code_for(challenge_id),
            "identifier_kind": "email",
            "identifier": new_email,
        },
    )

    assert changed.status_code == 200
    assert changed.json()["masked_identifier"] == "m***@example.com"
    assert changed.json()["primary_purpose"] == "student"
    assert new_email not in changed.text
    db.expire_all()
    stored = db.get(RegistrationApplication, application_id)
    assert stored is not None
    assert stored.verified_identifier_kind == "email"
    assert stored.verified_identifier_digest != previous_digest
    assert new_email not in (stored.verified_identifier_ciphertext or "")


def test_adult_student_optional_invitation_does_not_change_account_status(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "self_student")
    response = db_client.post(
        "/api/v1/registration/guardian-invitations",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "adult-family@example.com",
            "relationship": "parent",
            "is_primary": False,
        },
    )
    listed = db_client.get("/api/v1/registration/guardian-invitations")

    assert response.status_code == 201
    assert set(response.json()) == _invitation_response_fields()
    assert listed.status_code == 200
    assert set(listed.json()[0]) == _invitation_response_fields()
    assert listed.json()[0]["id"] == response.json()["id"]
    account = db.get(UserAccount, platform_users["accounts"]["self_student"].id)
    assert account is not None and account.status == "active"


def test_formal_invitation_routes_are_self_scoped_to_adult_students(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "guardian")
    before = db.scalar(select(func.count(GuardianRegistrationInvitation.id))) or 0

    listed = db_client.get("/api/v1/registration/guardian-invitations")
    created = db_client.post(
        "/api/v1/registration/guardian-invitations",
        headers={"X-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "out-of-scope-family@example.com",
            "relationship": "parent",
            "is_primary": False,
        },
    )

    assert listed.status_code == created.status_code == 409
    assert listed.json()["code"] == "registration_action_unavailable"
    assert created.json()["code"] == "registration_action_unavailable"
    assert listed.headers["Cache-Control"] == "no-store, private"
    assert created.headers["Cache-Control"] == "no-store, private"
    assert (
        db.scalar(select(func.count(GuardianRegistrationInvitation.id))) or 0
    ) == before


def test_minor_resend_revoke_and_missing_errors_are_private_and_equivalent(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
) -> None:
    _application_id, csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008703",
        sequence=703,
    )
    created = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": csrf},
        json={
            "identifier_kind": "email",
            "identifier": "private-family@example.com",
            "relationship": "parent",
            "is_primary": False,
        },
    )
    assert created.status_code == 201
    assert set(created.json()) == _invitation_response_fields()
    first_id = UUID(created.json()["id"])
    resent = db_client.post(
        f"/api/v1/registration/me/guardian-invitations/{first_id}/resend",
        headers={"X-Registration-CSRF-Token": csrf},
    )
    assert resent.status_code == 201
    assert set(resent.json()) == _invitation_response_fields()
    second_id = UUID(resent.json()["id"])
    assert second_id != first_id
    revoked = db_client.delete(
        f"/api/v1/registration/me/guardian-invitations/{second_id}",
        headers={"X-Registration-CSRF-Token": csrf},
    )
    assert revoked.status_code == 200
    assert set(revoked.json()) == _invitation_response_fields()
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["masked_identifier"] == REDACTED_GUARDIAN_INVITATION_MASK
    assert REDACTED_GUARDIAN_INVITATION_CIPHERTEXT not in revoked.text

    reused = db_client.post(
        f"/api/v1/registration/me/guardian-invitations/{second_id}/resend",
        headers={"X-Registration-CSRF-Token": csrf},
    )
    missing = db_client.post(
        f"/api/v1/registration/me/guardian-invitations/{uuid4()}/resend",
        headers={"X-Registration-CSRF-Token": csrf},
    )
    assert reused.status_code == missing.status_code == 409
    assert {
        key: value for key, value in reused.json().items() if key != "request_id"
    } == {
        key: value for key, value in missing.json().items() if key != "request_id"
    }
    assert reused.json()["code"] == "registration_action_unavailable"
    listed = db_client.get("/api/v1/registration/me/guardian-invitations")
    assert listed.status_code == 200
    assert all(
        set(invitation) == _invitation_response_fields()
        for invitation in listed.json()
    )
    assert [item["status"] for item in listed.json()] == ["revoked", "revoked"]
    assert [item["masked_identifier"] for item in listed.json()] == [
        REDACTED_GUARDIAN_INVITATION_MASK,
        REDACTED_GUARDIAN_INVITATION_MASK,
    ]
    assert "private-family@example.com" not in listed.text
    assert REDACTED_GUARDIAN_INVITATION_CIPHERTEXT not in listed.text
    assert listed.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    rows = list(
        db.scalars(
            select(GuardianRegistrationInvitation)
            .where(GuardianRegistrationInvitation.id.in_([first_id, second_id]))
            .order_by(GuardianRegistrationInvitation.id)
        )
    )
    assert len(rows) == 2
    assert all(
        row.invited_identifier_digest == REDACTED_GUARDIAN_INVITATION_DIGEST
        and row.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
        for row in rows
    )


def test_missing_handoff_acceptance_is_private_for_logged_in_guardian(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "guardian")
    response = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": csrf},
        json={"consent_version": "2026-08"},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "registration_action_unavailable"
    assert response.headers["Cache-Control"] == "no-store, private"


def test_management_uuid_is_not_a_public_guardian_acceptance_credential(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "guardian")

    response = db_client.post(
        f"/api/v1/registration/guardian-invitations/{uuid4()}/accept",
        headers={"X-CSRF-Token": csrf},
        json={"consent_version": "2026-08"},
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("scenario", "actor_key"),
    [
        ("missing", "guardian"),
        ("expired", "guardian"),
        ("revoked", "guardian"),
        ("wrong_identifier", "guardian"),
        ("underage_guardian", "minor_student"),
        ("incomplete_profile", "guardian"),
        ("wrong_scope", "guardian"),
        ("different_account_replay", "guardian"),
        ("wrong_consent_version", "guardian"),
    ],
)
def test_acceptance_privacy_matrix_has_equivalent_errors_and_zero_writes(
    db_client: TestClient,
    db: Session,
    platform_users,
    scenario: str,
    actor_key: str,
) -> None:
    actor_identifier = platform_users["credentials"][actor_key][0]
    invitation_state_before: tuple[object, ...] | None = None
    if scenario == "missing":
        invitation_id = uuid4()
        handoff_token = issue_guardian_invitation_handoff_token()
    else:
        invitation, handoff_token = _store_formal_invitation(
            db,
            platform_users,
            identifier=(
                "13800009997" if scenario == "wrong_identifier" else actor_identifier
            ),
            target_key="guardian" if scenario == "wrong_scope" else "self_student",
            status=(
                "revoked"
                if scenario == "revoked"
                else "accepted"
                if scenario == "different_account_replay"
                else "pending"
            ),
            expired=scenario == "expired",
            accepted_by_key=(
                "self_student" if scenario == "different_account_replay" else None
            ),
        )
        invitation_id = invitation.id
        invitation_state_before = (
            invitation.status,
            invitation.handoff_token_digest,
            invitation.invited_identifier_digest,
            invitation.invited_identifier_ciphertext,
            invitation.accepted_by_person_id,
            invitation.accepted_at,
            invitation.revoked_at,
        )

    handoff = db_client.post(
        "/api/v1/registration/guardian-invitations/handoff",
        json={"token": handoff_token},
    )
    handoff_rejected = scenario in {
        "missing",
        "expired",
        "revoked",
        "wrong_scope",
        "different_account_replay",
    }
    assert handoff.status_code == (404 if handoff_rejected else 204)
    if handoff_rejected:
        assert handoff.json()["code"] == "registration_action_unavailable"
        assert "Set-Cookie" not in handoff.headers
    csrf = _login(db_client, platform_users, actor_key)
    if scenario == "incomplete_profile":
        actor = db.get(Person, platform_users["people"][actor_key].id)
        assert actor is not None
        actor.full_name = ""
        db.commit()
    before = (
        *_business_write_counts(db),
        db.scalar(select(func.count(AuditEvent.id))) or 0,
    )
    account_before = db.get(UserAccount, platform_users["accounts"][actor_key].id)
    assert account_before is not None
    account_state_before = (account_before.status, account_before.force_password_change)

    response = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": csrf},
        json={
            "consent_version": (
                "2026-07" if scenario == "wrong_consent_version" else "2026-08"
            )
        },
    )

    assert response.status_code == 409
    assert {
        key: value
        for key, value in response.json().items()
        if key != "request_id"
    } == {
        "type": "about:blank",
        "title": "Conflict",
        "status": 409,
        "detail": "注册操作不可用，请重新开始或稍后重试",
        "code": "registration_action_unavailable",
    }
    assert response.headers["Cache-Control"] == "no-store, private"
    db.expire_all()
    assert (
        *_business_write_counts(db),
        db.scalar(select(func.count(AuditEvent.id))) or 0,
    ) == before
    if invitation_state_before is not None:
        stored_invitation = db.get(GuardianRegistrationInvitation, invitation_id)
        assert stored_invitation is not None
        assert (
            stored_invitation.status,
            stored_invitation.handoff_token_digest,
            stored_invitation.invited_identifier_digest,
            stored_invitation.invited_identifier_ciphertext,
            stored_invitation.accepted_by_person_id,
            stored_invitation.accepted_at,
            stored_invitation.revoked_at,
        ) == invitation_state_before
    account_after = db.get(UserAccount, platform_users["accounts"][actor_key].id)
    assert account_after is not None
    assert (account_after.status, account_after.force_password_change) == account_state_before


def test_guardian_inputs_reject_out_of_scope_extra_fields(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    platform_users,
) -> None:
    _application_id, registration_csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008705",
        sequence=705,
    )
    create_response = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": registration_csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "19990000005",
            "relationship": "mother",
            "is_primary": True,
            "student_person_id": str(platform_users["people"]["other_student"].id),
        },
    )

    guardian_csrf = _login(db_client, platform_users, "guardian")
    accept_response = db_client.post(
        "/api/v1/registration/guardian-invitations/accept",
        headers={"X-CSRF-Token": guardian_csrf},
        json={
            "consent_version": "2026-08",
            "account_id": str(platform_users["accounts"]["self_student"].id),
        },
    )

    assert create_response.status_code == 422
    assert accept_response.status_code == 422
    assert create_response.headers["Cache-Control"] == "no-store, private"
    assert accept_response.headers["Cache-Control"] == "no-store, private"


@pytest.mark.parametrize(
    "relationship",
    [
        "母亲 13800009992",
        "父亲 family@example.com",
        "监护人 110105199001021234",
    ],
)
def test_guardian_relationship_rejects_plaintext_pii_without_creating_invitation(
    db_client: TestClient,
    captured_sender: CapturingVerificationSender,
    db: Session,
    relationship: str,
) -> None:
    _application_id, registration_csrf = _open_minor_registration(
        db_client,
        captured_sender,
        phone="13800008707",
        sequence=707,
    )
    before = db.scalar(select(func.count(GuardianRegistrationInvitation.id))) or 0

    response = db_client.post(
        "/api/v1/registration/me/guardian-invitations",
        headers={"X-Registration-CSRF-Token": registration_csrf},
        json={
            "identifier_kind": "phone",
            "identifier": "19990000005",
            "relationship": relationship,
            "is_primary": True,
        },
    )

    assert response.status_code == 422
    assert relationship not in response.text
    db.expire_all()
    assert (
        db.scalar(select(func.count(GuardianRegistrationInvitation.id))) or 0
    ) == before
