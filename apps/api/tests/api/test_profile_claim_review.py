from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import require_standard_account
from app.core.database import get_db
from app.main import create_app
from tests.services.test_personnel_intake import actor
from tests.services.test_profile_claim_review import pending, pending_self_claim, reviews
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def test_admin_routes_have_private_headers_strict_inputs_and_masked_output(claim_db, test_settings):
    _, users, claim, _ = pending(claim_db)
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = lambda: claim_db
    app.dependency_overrides[require_standard_account] = lambda: actor(users, "admin")
    # Dependency import stays inside the test: the initial RED is a missing route,
    # rather than failing collection while other task suites are still running.
    with TestClient(app, base_url="https://testserver") as client:
        base = "/api/v1/personnel-intake/profile-claims"
        response = client.get(base + "/context")
        assert response.status_code == 200
        assert response.json() == {"can_enter": True}
        from app.personnel_intake.claims.admin_router import get_review_service

        app.dependency_overrides[get_review_service] = lambda: reviews(claim_db)
        detail = client.get(f"{base}/{claim.id}")
        assert detail.status_code == 200 and detail.headers["cache-control"] == "no-store, private"
        assert "11010120000102321" not in detail.text
        assert "identity_number" not in detail.json()
        assert client.get(base + "?page_size=101").status_code == 422
        rejected = client.post(
            f"{base}/{claim.id}/reject",
            json={"expected_version": 1, "idempotency_key": str(uuid4()), "reason": " "},
        )
        assert (
            rejected.status_code == 422 and rejected.headers["cache-control"] == "no-store, private"
        )
        assert (
            client.post(
                f"{base}/{claim.id}/approve",
                json={
                    "expected_version": 1,
                    "idempotency_key": str(uuid4()),
                    "identity_confirmed": False,
                },
            ).status_code
            == 422
        )


def test_pending_completion_detail_is_private_and_lists_never_expose_submitted_contacts(
    claim_db, test_settings
):
    from sqlalchemy import select

    from app.personnel_intake.claims.admin_router import get_review_service
    from app.personnel_intake.claims.models import ProfileClaim
    from tests.services.test_profile_claims import (
        claims,
        incomplete_pending_target,
        pending_claim_input,
        verify_new,
    )

    _, users = incomplete_pending_target(claim_db, phone="+1 ORIGINAL PRIVATE")
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    who = [actor(users, "admin")]
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = lambda: claim_db
    app.dependency_overrides[require_standard_account] = lambda: who[0]
    app.dependency_overrides[get_review_service] = lambda: reviews(claim_db)
    with TestClient(app, base_url="https://testserver") as client:
        base = "/api/v1/personnel-intake/profile-claims"
        detail = client.get(f"{base}/{claim.id}")
        assert detail.status_code == 200
        assert detail.json()["applicant_completion"]["phone"] == "+1 202 555 0100"
        assert detail.json()["profile_completion"]["phone"] == "+1 ORIGINAL PRIVATE"
        assert detail.headers["cache-control"] == "no-store, private"
        listed = client.get(base)
        assert "+1 202" not in listed.text and "ORIGINAL PRIVATE" not in listed.text
        assert "applicant_completion" not in listed.text and "profile_completion" not in listed.text
        who[0] = actor(users, "self_student")
        denied = client.get(f"{base}/{claim.id}")
        assert denied.status_code == 403
        assert "ORIGINAL PRIVATE" not in denied.text and "+1 202" not in denied.text


def test_review_http_actions_return_fixed_detail_and_never_activate(claim_db, test_settings):
    from sqlalchemy import select

    from app.auth.models import UserAccount
    from app.personnel_intake.claims.admin_router import get_review_service

    target, users, claim, _ = pending(claim_db)
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = lambda: claim_db
    app.dependency_overrides[require_standard_account] = lambda: actor(users, "admin")
    app.dependency_overrides[get_review_service] = lambda: reviews(claim_db)
    with TestClient(app, base_url="https://testserver") as client:
        base = f"/api/v1/personnel-intake/profile-claims/{claim.id}"
        assert (
            client.post(
                base + "/approve", json={"expected_version": 1, "idempotency_key": str(uuid4())}
            ).status_code
            == 422
        )
        response = client.post(
            base + "/approve",
            json={
                "expected_version": 1,
                "idempotency_key": str(uuid4()),
                "identity_confirmed": True,
                "note": None,
            },
        )
        assert response.status_code == 200 and response.json()["status"] == "approved"
        assert set(response.json()) == {
            "id",
            "version",
            "status",
            "masked_name",
            "masked_email",
            "verified_at",
            "expires_at",
            "allowed_actions",
            "person_id",
            "applicant_masked_name",
            "applicant_masked_identity",
            "applicant_email",
            "profile_masked_name",
            "profile_masked_identity",
            "profile_masked_email",
            "private_match_reason",
            "same_target_pending_count",
            "can_approve",
            "applicant_document_type",
            "applicant_issuing_country",
            "applicant_completion",
            "profile_completion",
        }
        revoked = client.post(
            base + "/revoke",
            json={"expected_version": 2, "idempotency_key": str(uuid4()), "reason": "本人要求撤销"},
        )
        assert revoked.status_code == 200 and revoked.json()["status"] == "revoked"
        assert revoked.headers["cache-control"] == "no-store, private"
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )


@pytest.mark.parametrize("unmatched", [False, True])
def test_self_claim_http_hides_actions_and_reject_is_forbidden_without_effect(
    claim_db, test_settings, unmatched
):
    from sqlalchemy import func, select

    from app.audit.models import AuditEvent
    from app.auth.models import UserAccount
    from app.personnel_intake.claims.admin_router import get_review_service
    from app.personnel_intake.claims.models import ProfileClaimBinding, ProfileClaimDecision
    from app.personnel_intake.models import AssistedApproval

    users, own = pending_self_claim(claim_db, unmatched=unmatched)
    app = create_app(test_settings)
    app.dependency_overrides[get_db] = lambda: claim_db
    app.dependency_overrides[require_standard_account] = lambda: actor(users, "admin")
    app.dependency_overrides[get_review_service] = lambda: reviews(claim_db)
    before = tuple(getattr(own, column.key) for column in own.__table__.columns)
    models = (ProfileClaimBinding, ProfileClaimDecision, AssistedApproval, UserAccount, AuditEvent)
    counts = tuple(claim_db.scalar(select(func.count()).select_from(model)) for model in models)
    with TestClient(app, base_url="https://testserver") as client:
        base = "/api/v1/personnel-intake/profile-claims"
        detail = client.get(f"{base}/{own.id}")
        listed = client.get(base)
        assert detail.status_code == listed.status_code == 200
        rejected = client.post(
            f"{base}/{own.id}/reject",
            json={
                "expected_version": 1,
                "idempotency_key": str(uuid4()),
                "reason": "本人不能审核本人",
            },
        )
        assert rejected.status_code == 403
        assert rejected.headers["cache-control"] == "no-store, private"
    claim_db.refresh(own)
    assert tuple(getattr(own, column.key) for column in own.__table__.columns) == before
    assert (
        tuple(claim_db.scalar(select(func.count()).select_from(model)) for model in models)
        == counts
    )
    assert detail.json()["can_approve"] is False
    assert detail.json()["allowed_actions"] == []
    assert (
        next(item for item in listed.json()["items"] if item["id"] == str(own.id))[
            "allowed_actions"
        ]
        == []
    )
