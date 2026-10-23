"""Accountless correction is a new approval, never a transfer of email proof."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.auth.models import UserAccount
from app.housing.models import HousingStay
from app.identity.models import Person
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimBinding
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import ApprovalEvidenceError
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.personnel_intake.schemas import PendingProfileCorrection
from app.registration.verification_service import VerificationInvalidOrExpired
from tests.services.test_assisted_activation import activation
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_intake import actor
from tests.services.test_profile_claim_binding import verified_activation
from tests.services.test_profile_claim_review import decision, reviews
from tests.services.test_profile_claims import claim_db as _claim_db
from tests.services.test_profile_claims import (
    claims,
    incomplete_pending_target,
    pending_claim_input,
    verify_new,
)


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def correction(db, person_id, **changes):
    service = approvals(db)
    profile = db.get(AssistedPersonProfile, person_id)
    approval = db.get(AssistedApproval, profile.current_approval_id)
    from app.personnel_intake.models import PersonnelIntakeRequest

    payload = service._payload(db.get(PersonnelIntakeRequest, approval.request_id))
    return PendingProfileCorrection.model_validate(
        {
            **payload.model_dump(
                exclude={
                    "material_version",
                    "kind",
                    "coach_person_ids",
                    "team_id",
                    "team_allocation_status",
                }
            ),
            "expected_version": profile.version,
            "idempotency_key": uuid4(),
            "reason": "已通过可信渠道重新核对本人及原录入错误",
            "identity_confirmed": True,
            "email": "claim@example.com",
            **changes,
        }
    )


def test_typo_correction_requires_fresh_review_and_fresh_activation_preserving_person_stay(
    claim_db,
):
    target, users = incomplete_pending_target(claim_db, email="typo@example.com", sex="female")
    person = claim_db.get(Person, target.person_id)
    person.nickname, person.profile_notes = "原昵称", "原备注"
    from tests.services.test_housing_reconciliation import _room, _stay

    stay = _stay(
        claim_db, person_id=person.id, room=_room(claim_db), actor_id=users["accounts"]["admin"].id
    )
    claim_db.commit()
    stay_bytes = tuple(getattr(stay, c.key) for c in HousingStay.__table__.columns)
    old = claim_db.get(AssistedApproval, target.approval_id)
    original_bytes = tuple(getattr(old, c.key) for c in AssistedApproval.__table__.columns)
    now = [datetime.now(UTC)]
    public, sender = claims(claim_db, clock=lambda: now[0])
    old_token, _ = verify_new(public, sender, pending_claim_input(email="typo@example.com"))
    old_claim = claim_db.scalar(select(ProfileClaim))
    who = actor(users, "admin")
    request = correction(claim_db, person.id)
    changed = approvals(claim_db).correct_pending_profile(who, person.id, request)
    assert approvals(claim_db).correct_pending_profile(who, person.id, request) == changed
    assert changed.person_id == target.person_id and changed.approval_id != target.approval_id
    claim_db.refresh(old_claim)
    assert old_claim.status == "invalidated" and public.status(old_token).status == "closed"
    assert tuple(getattr(old, c.key) for c in AssistedApproval.__table__.columns) == original_bytes
    assert claim_db.get(AssistedPersonProfile, person.id).activation_requirement == "reviewed_claim"
    blocked, blocked_sender = activation(claim_db)
    blocked.start("claim@example.com", "127.0.91.1")
    assert not blocked_sender.messages
    now[0] += timedelta(seconds=61)
    verify_new(public, sender, pending_claim_input())
    fresh = claim_db.scalar(select(ProfileClaim).where(ProfileClaim.id != old_claim.id))
    reviews(claim_db, clock=lambda: now[0]).approve(who, fresh.id, decision())
    assert claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id)) is None
    active, token, code, _ = verified_activation(
        claim_db, person_id=person.id, now=now[0] + timedelta(seconds=61)
    )
    assert active.finish(token, code, SecretStr("SecurePass1")).completed
    claim_db.refresh(person)
    claim_db.refresh(stay)
    assert (person.nickname, person.profile_notes) == ("原昵称", "原备注")
    assert tuple(getattr(stay, c.key) for c in HousingStay.__table__.columns) == stay_bytes
    assert (
        claim_db.scalar(
            select(UserAccount).where(UserAccount.person_id == person.id)
        ).email_normalized
        == "claim@example.com"
    )
    with pytest.raises(PersonnelIntakeError):
        approvals(claim_db).correct_pending_profile(
            who,
            person.id,
            correction(claim_db, person.id, email="not-an-account-change@example.com"),
        )


@pytest.mark.parametrize("reserved", [None, "claim@example.com"])
def test_email_correction_revokes_old_binding_and_otp_without_rewriting_origin_history(
    claim_db, reserved
):
    target, users = incomplete_pending_target(claim_db, email=reserved)
    public, sender = claims(claim_db)
    old_token, _ = verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    who = actor(users, "admin")
    reviews(claim_db).approve(who, claim.id, decision())
    binding = claim_db.scalar(select(ProfileClaimBinding))
    proof_bytes = (
        claim.payload_ciphertext,
        claim.payload_digest,
        binding.email_binding_origin,
        binding.approval_id,
    )
    service, token, code, _ = verified_activation(claim_db, person_id=target.person_id)
    changed = approvals(claim_db).correct_pending_profile(
        who, target.person_id, correction(claim_db, target.person_id, email="fixed@example.com")
    )
    claim_db.refresh(binding)
    claim_db.refresh(claim)
    assert binding.revoked_at is not None and claim.status == "revoked"
    assert (
        claim.payload_ciphertext,
        claim.payload_digest,
        binding.email_binding_origin,
        binding.approval_id,
    ) == proof_bytes
    assert public.status(old_token).status == "closed"
    with pytest.raises(VerificationInvalidOrExpired):
        service.finish(token, code, SecretStr("SecurePass1"))
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )
    assert changed.person_id == target.person_id


@pytest.mark.parametrize(
    "failure",
    [
        "identity_unconfirmed",
        "version",
        "permission",
        "nonadmin",
        "collision",
        "reservation",
        "history",
        "rollback",
    ],
)
def test_pending_email_correction_rejection_is_atomic(claim_db, failure, monkeypatch):
    target, users = incomplete_pending_target(claim_db, email="typo@example.com")
    service, who = approvals(claim_db), actor(users, "admin")
    changes = {"identity_confirmed": False} if failure == "identity_unconfirmed" else {}
    if failure == "version":
        changes["expected_version"] = 999
    if failure == "permission":
        from app.rbac.models import UserPermissionOverride
        from app.rbac.seed import PERMISSIONS

        claim_db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["people.update"].id,
                effect="deny",
                scope_kind="global",
            )
        )
        claim_db.commit()
    if failure == "collision":
        changes["email"] = "admin@example.com"
    if failure == "nonadmin":
        from app.rbac.models import UserPermissionOverride
        from app.rbac.seed import PERMISSIONS

        who = actor(users, "guardian")
        claim_db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS["people.update"].id,
                effect="allow",
                scope_kind="global",
            )
        )
        claim_db.commit()
    if failure == "reservation":
        from app.personnel_intake.schemas import PendingIntakeInput

        approvals(claim_db).direct_create_pending(
            who,
            PendingIntakeInput(
                kind="staff",
                full_name="隔离护照本人",
                identity_number="OTHER-TARGET-859",
                document_type="passport",
                document_issuing_country="US",
            ),
            uuid4(),
        )
        public, sender = claims(claim_db)
        verify_new(
            public,
            sender,
            pending_claim_input(email="occupied@example.com", identity_number="OTHER-TARGET-859"),
        )
        assert claim_db.scalar(select(ProfileClaim)).private_match_reason is None
        changes["email"] = "occupied@example.com"
    if failure == "history":
        # Storage itself rejects tampering; model an authenticated-reader
        # corruption without disabling that production immutability trigger.
        original = claim_db.get(AssistedApproval, target.approval_id).snapshot_ciphertext
        real_decrypt = service.cipher.decrypt
        monkeypatch.setattr(
            service.cipher,
            "decrypt",
            lambda value, **kwargs: "{}" if value == original else real_decrypt(value, **kwargs),
        )
    request = correction(claim_db, target.person_id, **changes)
    person = claim_db.get(Person, target.person_id)
    before = tuple(getattr(person, c.key) for c in Person.__table__.columns)
    if failure == "rollback":
        monkeypatch.setattr(
            service,
            "_new_approval",
            lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rollback probe")),
        )
    error = (
        RuntimeError
        if failure == "rollback"
        else ApprovalEvidenceError
        if failure == "history"
        else PersonnelIntakeError
    )
    with pytest.raises(error):
        service.correct_pending_profile(who, person.id, request)
    claim_db.refresh(person)
    assert tuple(getattr(person, c.key) for c in Person.__table__.columns) == before
    assert claim_db.get(AssistedPersonProfile, person.id).version == 1


def test_failed_late_correction_rolls_back_binding_claim_and_activation_proof(
    claim_db, monkeypatch
):
    from app.personnel_intake.activation_models import AssistedActivationSession

    target, users = incomplete_pending_target(claim_db, email="claim@example.com")
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    who = actor(users, "admin")
    reviews(claim_db).approve(who, claim.id, decision())
    active, token, code, session = verified_activation(claim_db, person_id=target.person_id)
    binding = claim_db.scalar(select(ProfileClaimBinding))
    person = claim_db.get(Person, target.person_id)
    models = [
        (person, Person),
        (claim, ProfileClaim),
        (binding, ProfileClaimBinding),
        (session, AssistedActivationSession),
    ]
    before = [tuple(getattr(row, c.key) for c in model.__table__.columns) for row, model in models]
    service = approvals(claim_db)

    def fail_audit(*args, **kwargs):
        raise RuntimeError("late transaction rollback probe")

    monkeypatch.setattr(service, "_audit", fail_audit)
    with pytest.raises(RuntimeError, match="late transaction rollback probe"):
        service.correct_pending_profile(
            who, person.id, correction(claim_db, person.id, email="fixed@example.com")
        )
    for (row, model), snapshot in zip(models, before, strict=True):
        claim_db.refresh(row)
        assert tuple(getattr(row, c.key) for c in model.__table__.columns) == snapshot
    assert active.finish(token, code, SecretStr("SecurePass1")).completed


def test_original_approval_lineage_is_authenticated_before_email_recovery(claim_db, monkeypatch):
    target, users = incomplete_pending_target(claim_db, email="typo@example.com")
    service, who = approvals(claim_db), actor(users, "admin")
    first = correction(claim_db, target.person_id, email="typo@example.com", phone="+1 original")
    service.correct_pending_profile(who, target.person_id, first)
    old = claim_db.get(AssistedApproval, target.approval_id)
    real_decrypt = service.cipher.decrypt
    monkeypatch.setattr(
        service.cipher,
        "decrypt",
        lambda value, **kwargs: (
            "{}" if value == old.snapshot_ciphertext else real_decrypt(value, **kwargs)
        ),
    )
    with pytest.raises(ApprovalEvidenceError):
        service.correct_pending_profile(
            who, target.person_id, correction(claim_db, target.person_id)
        )
    assert claim_db.get(AssistedPersonProfile, target.person_id).version == 2


def test_document_correction_with_same_email_does_not_transfer_old_binding_proof(claim_db):
    target, users = incomplete_pending_target(claim_db, email="claim@example.com")
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    who = actor(users, "admin")
    reviews(claim_db).approve(who, claim.id, decision())
    approvals(claim_db).correct_pending_profile(
        who,
        target.person_id,
        correction(claim_db, target.person_id, identity_number="REAL-CORRECTED-859"),
    )
    binding = claim_db.scalar(select(ProfileClaimBinding))
    assert binding.revoked_at is not None
    claim_db.refresh(claim)
    assert claim.status == "revoked"
