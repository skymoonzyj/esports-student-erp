from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text

from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person
from app.personnel_intake.claims.models import (
    ProfileClaim,
    ProfileClaimBinding,
    ProfileClaimDecision,
)
from app.personnel_intake.claims.schemas import ClaimDecisionInput, ClaimReasonInput
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor
from tests.services.test_profile_claims import approved_target, claim_input, claims, verify_new
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.mark.parametrize("reserved", [None, "claim@example.com"])
def test_reserved_pending_email_can_complete_but_not_change_email(claim_db, reserved):
    from app.personnel_intake.readiness import AssistedReadinessPolicy
    from tests.services.test_profile_claims import incomplete_pending_target, pending_claim_input

    target, users = incomplete_pending_target(claim_db, email=reserved)
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    service, who = reviews(claim_db), actor(users, "admin")
    detail = service.detail(who, claim.id)
    assert detail.can_approve
    assert detail.applicant_completion.phone == "+1 202 555 0100"
    assert detail.profile_completion.phone is None
    result = service.approve(who, claim.id, decision())
    assert result.status == "approved"
    person = claim_db.get(Person, target.person_id)
    assert person.sex == "female" and person.date_of_birth == date(2000, 1, 2)
    assert public.cipher.decrypt(person.phone_ciphertext, field_name="phone") == "+1 202 555 0100"
    assert public.cipher.decrypt(person.email_ciphertext, field_name="email") == "claim@example.com"
    binding = claim_db.scalar(
        select(ProfileClaimBinding).where(ProfileClaimBinding.claim_id == claim.id)
    )
    assert binding.email_binding_origin == ("preserved" if reserved else "introduced")
    state = AssistedReadinessPolicy(
        claim_db, cipher=public.cipher, lookup=public.lookup
    ).evaluate_many([person.id], reference_date=date(2026, 10, 21))[person.id]
    assert state.activation_profile_ready
    service.revoke(
        who,
        claim.id,
        ClaimReasonInput(
            expected_version=result.version, idempotency_key=uuid4(), reason="撤销本次邮箱证明"
        ),
    )
    claim_db.refresh(person)
    assert (person.email_ciphertext is not None) == (reserved is not None)
    assert person.sex == "female" and person.intake_mode == "pending"


def test_different_reserved_pending_email_cannot_be_approved(claim_db):
    from tests.services.test_profile_claims import incomplete_pending_target, pending_claim_input

    _, users = incomplete_pending_target(claim_db, email="reserved@example.com")
    public, sender = claims(claim_db)
    _, response = verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    assert response.status == "waiting_review"
    assert not reviews(claim_db).detail(actor(users, "admin"), claim.id).can_approve
    with pytest.raises(Exception) as caught:
        reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    assert caught.value.status == 409


@pytest.mark.parametrize(
    "material,reason",
    [
        ({}, "completion_missing"),
        (
            {"phone": "different", "sex": "female", "date_of_birth": "2000-01-02"},
            "completion_conflicts",
        ),
        ({"phone": "+1 202", "sex": "female", "date_of_birth": "2015-01-02"}, "completion_missing"),
    ],
)
def test_pending_completion_missing_or_conflicting_facts_remain_private_and_unapprovable(
    claim_db, material, reason
):
    from tests.services.test_profile_claims import incomplete_pending_target, pending_claim_input

    target, users = incomplete_pending_target(
        claim_db, phone="+1 existing" if reason == "completion_conflicts" else None
    )
    public, sender = claims(claim_db)
    _, response = verify_new(public, sender, pending_claim_input(completion=material))
    row = claim_db.scalar(select(ProfileClaim))
    detail = reviews(claim_db).detail(actor(users, "admin"), row.id)
    assert response.model_dump() == {"status": "waiting_review", "reason": None}
    assert row.private_match_reason is None and row.target_person_id == target.person_id
    assert not detail.can_approve and detail.private_match_reason == reason
    with pytest.raises(Exception) as caught:
        reviews(claim_db).approve(actor(users, "admin"), row.id, decision())
    assert caught.value.status == 409
    assert claim_db.get(Person, target.person_id).sex is None


def pending_minor_completion(db):
    from tests.api.conftest import make_cn_id
    from tests.services.test_profile_claims import incomplete_pending_target, pending_claim_input

    target, users = incomplete_pending_target(db, kind="student")
    material = {
        "phone": "+1 202 555 0100",
        "sex": "female",
        "date_of_birth": "2015-01-02",
        "guardians": [
            {
                "full_name": "本人提交成年监护事实",
                "identity_number": make_cn_id("19800102", 335),
                "phone": "13900000882",
                "sex": "male",
                "date_of_birth": "1980-01-02",
                "relationship": "父亲",
                "is_primary": True,
            }
        ],
    }
    public, sender = claims(db)
    verify_new(public, sender, pending_claim_input(completion=material))
    return target, users, db.scalar(select(ProfileClaim))


def test_pending_minor_completion_registers_real_guardian_without_family_grants(
    claim_db, monkeypatch
):
    from app.core.time import business_date_at
    from app.personnel_intake import approval_service
    from app.personnel_intake.readiness import AssistedReadinessPolicy
    from app.rbac.models import UserRole

    frozen_now = datetime(2026, 10, 22, 4, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen_now.astimezone(tz) if tz is not None else frozen_now.replace(tzinfo=None)

    monkeypatch.setattr(approval_service, "datetime", FrozenDateTime)
    reference_date = business_date_at(frozen_now)
    assert reference_date == date(2026, 10, 22)

    target, users, claim = pending_minor_completion(claim_db)
    models = (UserAccount, UserRole)
    before = tuple(claim_db.scalar(select(func.count()).select_from(m)) for m in models)
    reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    edge = claim_db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id == target.person_id
        )
    )
    assert edge is not None and edge.requires_verification
    assert edge.valid_from == reference_date
    assert edge.relationship == "父亲"
    assert tuple(claim_db.scalar(select(func.count()).select_from(m)) for m in models) == before
    service, _ = claims(claim_db)
    policy = AssistedReadinessPolicy(claim_db, cipher=service.cipher, lookup=service.lookup)
    prior_state = policy.evaluate_many(
        [target.person_id], reference_date=reference_date - timedelta(days=1)
    )[target.person_id]
    assert prior_state.housing_ready
    assert not prior_state.activation_profile_ready
    assert "guardian" in prior_state.profile_missing
    state = policy.evaluate_many([target.person_id], reference_date=reference_date)[
        target.person_id
    ]
    assert state.housing_ready
    assert "guardian" not in state.profile_missing
    assert state.activation_profile_ready


def claim_business_state(db):
    from app.core.models import Base

    return {
        table.name: [
            dict(row)
            for row in db.execute(select(table).order_by(*table.primary_key.columns)).mappings()
        ]
        for table in Base.metadata.tables.values()
    }


def test_pending_completion_and_revocation_preserve_existing_housing_and_notes(claim_db):
    from app.housing.models import HousingStay
    from tests.services.test_housing_reconciliation import _room, _stay
    from tests.services.test_profile_claims import incomplete_pending_target, pending_claim_input

    target, users = incomplete_pending_target(claim_db, sex="female")
    person = claim_db.get(Person, target.person_id)
    person.nickname, person.profile_notes = "原有昵称", "原有备注"
    stay = _stay(
        claim_db,
        person_id=target.person_id,
        room=_room(claim_db),
        actor_id=users["accounts"]["admin"].id,
    )
    claim_db.commit()
    before = tuple(getattr(stay, c.key) for c in HousingStay.__table__.columns)
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    claim = claim_db.scalar(select(ProfileClaim))
    service, who = reviews(claim_db), actor(users, "admin")
    result = service.approve(who, claim.id, decision())
    service.revoke(
        who,
        claim.id,
        ClaimReasonInput(
            expected_version=result.version, idempotency_key=uuid4(), reason="本次申请撤销"
        ),
    )
    claim_db.refresh(stay)
    claim_db.refresh(person)
    assert tuple(getattr(stay, c.key) for c in HousingStay.__table__.columns) == before
    assert (person.nickname, person.profile_notes) == ("原有昵称", "原有备注")


def test_pending_different_material_cannot_reuse_review_operation_key(claim_db):
    from tests.services.test_profile_claims import incomplete_pending_target, pending_claim_input

    _, users = incomplete_pending_target(claim_db)
    public, sender = claims(claim_db)
    verify_new(public, sender, pending_claim_input())
    first = claim_db.scalar(select(ProfileClaim))
    verify_new(
        public,
        sender,
        pending_claim_input(
            email="different@example.com",
            completion={"phone": "+1 203", "sex": "female", "date_of_birth": "2000-01-02"},
        ),
    )
    second = claim_db.scalar(select(ProfileClaim).where(ProfileClaim.id != first.id))
    key = uuid4()
    service, who = reviews(claim_db), actor(users, "admin")
    service.reject(
        who,
        first.id,
        ClaimReasonInput(expected_version=1, idempotency_key=key, reason="请重新核实"),
    )
    before = claim_business_state(claim_db)
    with pytest.raises(Exception) as caught:
        service.approve(
            who,
            second.id,
            ClaimDecisionInput(expected_version=1, idempotency_key=key, identity_confirmed=True),
        )
    assert caught.value.status == 409 and claim_business_state(claim_db) == before


@pytest.mark.parametrize(
    "stage", ["facts", "guardian", "revision", "approval", "binding", "decision", "audit"]
)
def test_pending_completion_each_write_failure_rolls_back_all_business_state(
    claim_db, monkeypatch, stage
):
    from sqlalchemy import event, inspect
    from sqlalchemy.orm import Session

    from app.audit.service import AuditService
    from app.personnel_intake.approval_service import PersonnelApprovalService

    target, users, claim = pending_minor_completion(claim_db)
    who = actor(users, "admin")
    before = claim_business_state(claim_db)

    def broken(*args, **kwargs):
        raise RuntimeError("injected completion write failure")

    def fail_flush(db, *args):
        if stage == "facts" and any(
            isinstance(row, Person)
            and row.id == target.person_id
            and inspect(row).attrs.sex.history.has_changes()
            for row in db.dirty
        ):
            broken()
        if stage == "guardian" and any(isinstance(row, GuardianRelationship) for row in db.new):
            broken()
        if stage == "binding" and any(isinstance(row, ProfileClaimBinding) for row in db.new):
            broken()
        if stage == "decision" and any(isinstance(row, ProfileClaimDecision) for row in db.new):
            broken()

    if stage in {"revision", "approval"}:
        monkeypatch.setattr(
            PersonnelApprovalService,
            "_save_revision" if stage == "revision" else "_new_approval",
            broken,
        )
    elif stage == "audit":
        monkeypatch.setattr(AuditService, "append", broken)
    else:
        event.listen(Session, "before_flush", fail_flush)
    try:
        with pytest.raises(RuntimeError, match="injected completion"):
            reviews(claim_db).approve(who, claim.id, decision())
    finally:
        if stage not in {"revision", "approval", "audit"}:
            event.remove(Session, "before_flush", fail_flush)
    assert claim_business_state(claim_db) == before


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def reviews(db, *, clock=None):
    from app.personnel_intake.claims.review_service import ProfileClaimReviewService

    public, _ = claims(db)
    return ProfileClaimReviewService(db, cipher=public.cipher, lookup=public.lookup, clock=clock)


def pending(db, *, competing=False):
    target, users = approved_target(db)
    now = [datetime.now(UTC)]
    public, sender = claims(db, clock=lambda: now[0])
    verify_new(public, sender)
    claim = db.scalar(select(ProfileClaim))
    if competing:
        verify_new(public, sender, claim_input(email="competing@example.com"))
    return target, users, claim, now


def pending_minor(db):
    from tests.api.conftest import make_cn_id
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake

    users = _platform_users.__wrapped__(db)
    identity_number = make_cn_id("20150102", 321)
    data = intake(
        users,
        email=None,
        identity_number=identity_number,
        date_of_birth=date(2015, 1, 2),
        guardians=[
            {
                "full_name": "认领监护人",
                "identity_number": make_cn_id("19800102", 331),
                "phone": "13900000880",
                "sex": "male",
                "date_of_birth": date(1980, 1, 2),
                "relationship": "父亲",
                "is_primary": True,
            }
        ],
    )
    target = approvals(db).direct_create(actor(users, "admin"), data, uuid4())
    db.commit()
    public, sender = claims(db)
    verify_new(public, sender, claim_input(identity_number=identity_number))
    return target, users, db.scalar(select(ProfileClaim)), data


def decision(version=1, **changes):
    return ClaimDecisionInput(
        expected_version=version, idempotency_key=uuid4(), identity_confirmed=True, **changes
    )


def pending_self_claim(db, *, unmatched=False):
    _, users, _, _ = pending(db)
    who = actor(users, "admin")
    person = db.get(Person, who.person.id)
    public, sender = claims(db)
    verify_new(
        public,
        sender,
        claim_input(
            full_name="不匹配的申请姓名" if unmatched else person.full_name,
            identity_number=public.cipher.decrypt(
                person.identity_ciphertext, field_name="identity_number"
            ),
            email="self-action@example.com",
        ),
    )
    own = db.scalar(
        select(ProfileClaim).where(
            ProfileClaim.email_digest
            == public.lookup.digest_domain(
                "registration-verification-email", "self-action@example.com"
            )
        )
    )
    assert (own.target_person_id is None) == unmatched
    return users, own


@pytest.mark.parametrize("unmatched", [False, True])
def test_pending_self_claim_has_no_list_or_detail_decision_actions(claim_db, unmatched):
    users, own = pending_self_claim(claim_db, unmatched=unmatched)
    service, who = reviews(claim_db), actor(users, "admin")
    detail = service.detail(who, own.id)
    listed = next(item for item in service.list_claims(who).items if item.id == own.id)
    assert detail.can_approve is False
    assert detail.allowed_actions == []
    assert listed.allowed_actions == []


def test_approve_after_otp_expiry_before_review_deadline(claim_db):
    target, users, claim, now = pending(claim_db, competing=True)
    old = claim_db.get(AssistedApproval, target.approval_id)
    original = (old.snapshot_ciphertext, old.snapshot_digest)
    evidence = (
        claim.session_id,
        claim.challenge_id,
        claim.payload_ciphertext,
        claim.verified_at,
        claim.expires_at,
    )
    now[0] += timedelta(days=2)
    s = reviews(claim_db, clock=lambda: now[0])
    data = decision()
    result = s.approve(actor(users, "admin"), claim.id, data)
    assert result.status == "approved" and result.person_id == target.person_id
    successor = claim_db.get(
        AssistedApproval, claim_db.get(AssistedPersonProfile, target.person_id).current_approval_id
    )
    assert successor.supersedes_approval_id == old.id
    assert (old.snapshot_ciphertext, old.snapshot_digest) == original
    assert (
        claim.session_id,
        claim.challenge_id,
        claim.payload_ciphertext,
        claim.verified_at,
        claim.expires_at,
    ) == evidence
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(ProfileClaimBinding)
            .where(ProfileClaimBinding.revoked_at.is_(None))
        )
        == 1
    )
    assert (
        claim_db.scalar(select(ProfileClaim.status).where(ProfileClaim.id != claim.id))
        == "invalidated"
    )
    from app.audit.models import AuditEvent

    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "profile_claim.invalidated")
        )
        == 1
    )
    assert s.approve(actor(users, "admin"), claim.id, data) == result
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaimDecision)) == 1
    with pytest.raises(Exception) as caught:
        s.approve(actor(users, "admin"), claim.id, data.model_copy(update={"note": "不同输入"}))
    assert caught.value.status == 409


def test_expiry_boundary_is_read_only_and_cannot_approve(claim_db):
    _, users, claim, now = pending(claim_db)
    now[0] = claim.expires_at
    s = reviews(claim_db, clock=lambda: now[0])
    assert s.list_claims(actor(users, "admin"), status="expired", page=1, page_size=20).total == 1
    assert s.detail(actor(users, "admin"), claim.id).status == "expired"
    claim_db.refresh(claim)
    assert claim.status == "pending"
    with pytest.raises(Exception) as caught:
        s.approve(actor(users, "admin"), claim.id, decision())
    assert caught.value.status == 409
    assert claim_db.get(Person, claim.target_person_id).email_ciphertext is None


def test_review_requires_admin_account_capability_and_target_scope(claim_db):
    from sqlalchemy import delete

    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES

    _, users, claim, _ = pending(claim_db)
    claim_db.execute(
        delete(UserRole).where(
            UserRole.user_id == users["accounts"]["coach"].id, UserRole.role_id == ROLES["admin"].id
        )
    )
    claim_db.commit()
    s = reviews(claim_db)
    for key in ("coach", "guardian", "self_student"):
        assert not s.context(actor(users, key)).can_enter
        with pytest.raises(Exception) as caught:
            s.detail(actor(users, key), claim.id)
        assert caught.value.status == 403
    who = actor(users, "admin")
    claim_db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["accounts.manage"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    claim_db.commit()
    assert not s.context(who).can_enter
    with pytest.raises(Exception) as caught:
        s.approve(who, claim.id, decision())
    assert caught.value.status == 403


def test_explicit_confirmation_and_self_review_denied(claim_db):
    _, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    with pytest.raises(Exception) as caught:
        s.approve(
            actor(users, "admin"),
            claim.id,
            decision().model_copy(update={"identity_confirmed": False}),
        )
    assert caught.value.status == 422
    who = actor(users, "admin")
    # Compare normalized identity too: an administrator cannot approve a claim
    # made in their own identity, even when no candidate is publicly disclosed.
    # Self target is tested through the real public matcher, not tampering proof.
    public, sender = claims(claim_db)
    person = claim_db.get(Person, who.person.id)
    value = claim_input(
        full_name=person.full_name,
        identity_number=public.cipher.decrypt(
            person.identity_ciphertext, field_name="identity_number"
        ),
        email="self@example.com",
    )
    verify_new(public, sender, value)
    own = claim_db.scalar(
        select(ProfileClaim).where(
            ProfileClaim.email_digest
            == public.lookup.digest_domain("registration-verification-email", "self@example.com")
        )
    )
    with pytest.raises(Exception) as caught:
        s.approve(who, own.id, decision())
    assert caught.value.status == 403


def test_revoke_creates_empty_successor_and_replay_is_exact(claim_db):
    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    approved = s.approve(actor(users, "admin"), claim.id, decision())
    binding = claim_db.scalar(select(ProfileClaimBinding))
    approved_id = binding.approval_id
    data = ClaimReasonInput(
        expected_version=approved.version, idempotency_key=uuid4(), reason="本人要求撤销"
    )
    result = s.revoke(actor(users, "admin"), claim.id, data)
    assert result.status == "revoked"
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    current = claim_db.get(
        AssistedApproval, claim_db.get(AssistedPersonProfile, target.person_id).current_approval_id
    )
    assert (
        current.supersedes_approval_id == approved_id and current.operation_kind == "claim_revoke"
    )
    assert binding.revoked_at is not None
    assert s.revoke(actor(users, "admin"), claim.id, data) == result
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaimDecision)) == 2


@pytest.mark.parametrize("stage", ["revision", "approval", "binding", "audit"])
def test_any_write_failure_rolls_back_email_approval_and_binding(claim_db, monkeypatch, stage):
    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    from app.audit.service import AuditService

    def broken(*args, **kwargs):
        raise RuntimeError("isolated audit failure")

    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.personnel_intake.approval_service import PersonnelApprovalService

    if stage == "audit":
        monkeypatch.setattr(AuditService, "append", broken)
    elif stage in {"revision", "approval"}:
        monkeypatch.setattr(
            PersonnelApprovalService,
            "_save_revision" if stage == "revision" else "_new_approval",
            broken,
        )
    else:

        def fail_binding(db, *args):
            if any(isinstance(value, ProfileClaimBinding) for value in db.new):
                broken()

        event.listen(Session, "before_flush", fail_binding)
    with pytest.raises(RuntimeError):
        try:
            s.approve(actor(users, "admin"), claim.id, decision())
        finally:
            if stage == "binding":
                event.remove(Session, "before_flush", fail_binding)
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    assert (
        claim_db.get(AssistedPersonProfile, target.person_id).current_approval_id
        == target.approval_id
    )
    assert claim_db.get(ProfileClaim, claim.id).status == "pending"
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaimBinding)) == 0


@pytest.mark.parametrize("mutation", ["purpose", "consumption", "private_reason"])
def test_confirmation_never_overrides_invalid_original_proof_or_private_match_reason(
    claim_db, mutation
):
    from app.registration.models import RegistrationVerificationChallenge

    target, users, claim, _ = pending(claim_db)
    if mutation == "private_reason":
        public, sender = claims(claim_db)
        verify_new(public, sender, claim_input(full_name="不相同的名字", email="wrong@example.com"))
        claim = claim_db.scalar(select(ProfileClaim).where(ProfileClaim.id != claim.id))
        assert claim.private_match_reason is not None
    else:
        challenge = claim_db.get(RegistrationVerificationChallenge, claim.challenge_id)
        if mutation == "purpose":
            challenge.purpose = "registration"
        else:
            challenge.consumed_at += timedelta(microseconds=1)
        claim_db.commit()
    with pytest.raises(Exception) as caught:
        reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    assert caught.value.status == 409
    assert claim_db.get(Person, target.person_id).email_ciphertext is None


def test_reject_releases_email_and_exposes_only_own_public_reason(claim_db):
    from app.personnel_intake.models import PersonnelCredentialReservation

    _, users, claim, _ = pending(claim_db)
    reason = ClaimReasonInput(
        expected_version=1, idempotency_key=uuid4(), reason="请本人联系管理员核对"
    )
    s = reviews(claim_db)
    assert s.reject(actor(users, "admin"), claim.id, reason).status == "rejected"
    assert s.reject(actor(users, "admin"), claim.id, reason).status == "rejected"
    claim_db.refresh(claim)
    assert claim.public_reason == reason.reason
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(PersonnelCredentialReservation)
            .where(
                PersonnelCredentialReservation.profile_claim_id == claim.id,
                PersonnelCredentialReservation.ended_at.is_(None),
            )
        )
        == 0
    )


def test_revoke_does_not_remove_an_existing_account_or_another_round_email(claim_db):
    from app.security.passwords import PasswordService

    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    claim_db.add(
        UserAccount(
            person_id=target.person_id,
            status="disabled",
            password_hash=PasswordService().hash("SyntheticPass1"),
        )
    )
    claim_db.commit()
    with pytest.raises(Exception) as caught:
        s.revoke(
            actor(users, "admin"),
            claim.id,
            ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="撤销"),
        )
    assert caught.value.status == 409
    assert claim_db.get(Person, target.person_id).email_ciphertext is not None
    assert claim_db.scalar(select(ProfileClaimBinding)).revoked_at is None


def test_revoke_preserves_housing_and_consumes_old_activation_challenge_last(claim_db):
    from sqlalchemy import event

    from app.housing.models import HousingStay
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.registration.models import RegistrationVerificationChallenge
    from tests.services.test_assisted_activation import activation
    from tests.services.test_housing_reconciliation import _room, _stay

    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    stay = _stay(
        claim_db,
        person_id=target.person_id,
        room=_room(claim_db),
        actor_id=users["accounts"]["admin"].id,
    )
    claim_db.commit()
    before = tuple(getattr(stay, column.key) for column in HousingStay.__table__.columns)
    now = [datetime.now(UTC) + timedelta(seconds=61)]
    activate, _sender = activation(claim_db, clock=lambda: now[0])
    activate.start("claim@example.com", "127.0.31.21")
    session = claim_db.scalar(select(AssistedActivationSession))
    assert session is not None
    challenge_id = session.challenge_id
    observed = []

    def locks(conn, cursor, statement, parameters, context, executemany):
        if "FOR UPDATE" in statement:
            observed.append(statement)

    engine = claim_db.get_bind()
    event.listen(engine, "before_cursor_execute", locks)
    try:
        now[0] += timedelta(seconds=1)
        s.clock = lambda: now[0]
        s.revoke(
            actor(users, "admin"),
            claim.id,
            ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="撤销"),
        )
    finally:
        event.remove(engine, "before_cursor_execute", locks)
    claim_db.refresh(stay)
    claim_db.refresh(session)
    assert tuple(getattr(stay, column.key) for column in HousingStay.__table__.columns) == before
    assert session.expires_at <= now[0]
    assert claim_db.get(RegistrationVerificationChallenge, challenge_id).consumed_at is not None
    assert "registration_verification_challenges" in observed[-1]


def test_scope_filter_precedes_total_and_unknown_requires_global_review(claim_db):
    from sqlalchemy import delete

    from app.rbac.models import RolePermission, UserPermissionOverride
    from app.rbac.seed import PERMISSIONS, ROLES

    target, users, claim, _ = pending(claim_db)
    public, sender = claims(claim_db)
    verify_new(public, sender, claim_input(full_name="未匹配人员", email="unknown@example.com"))
    unknown = claim_db.scalar(select(ProfileClaim).where(ProfileClaim.id != claim.id))
    who = actor(users, "admin")
    denial = UserPermissionOverride(
        user_id=who.account.id,
        permission_id=PERMISSIONS["personnel_intake.review"].id,
        effect="deny",
        scope_kind="person",
        scope_id=target.person_id,
    )
    claim_db.add(denial)
    claim_db.commit()
    s = reviews(claim_db)
    page = s.list_claims(who, page=1, page_size=1)
    assert page.total == page.pending_count == 1
    assert page.items[0].id == unknown.id
    with pytest.raises(Exception) as caught:
        s.detail(who, claim.id)
    assert caught.value.status == 404
    claim_db.delete(denial)
    claim_db.execute(
        delete(RolePermission).where(
            RolePermission.role_id.in_([ROLES["admin"].id, ROLES["super_admin"].id]),
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    claim_db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="allow",
            scope_kind="person",
            scope_id=target.person_id,
        )
    )
    claim_db.commit()
    assert s.context(who).can_enter
    page = s.list_claims(who, page=1, page_size=20)
    assert page.total == page.pending_count == 1 and page.items[0].id == claim.id
    with pytest.raises(Exception) as caught:
        s.detail(who, unknown.id)
    assert caught.value.status == 404


@pytest.mark.parametrize("missing", ["accounts.manage", "personnel_intake.review"])
def test_one_capability_alone_cannot_read_or_write(claim_db, missing):
    from sqlalchemy import delete

    from app.rbac.models import RolePermission
    from app.rbac.seed import PERMISSIONS, ROLES

    _, users, claim, _ = pending(claim_db)
    claim_db.execute(
        delete(RolePermission).where(
            RolePermission.role_id.in_([ROLES["admin"].id, ROLES["super_admin"].id]),
            RolePermission.permission_id == PERMISSIONS[missing].id,
        )
    )
    claim_db.commit()
    who = actor(users, "admin")
    s = reviews(claim_db)
    assert not s.context(who).can_enter
    for operation in (
        lambda: s.list_claims(who, page=1, page_size=20),
        lambda: s.approve(who, claim.id, decision()),
    ):
        with pytest.raises(Exception) as caught:
            operation()
        assert caught.value.status == 403


def test_replaying_approved_decision_after_revocation_never_resurrects_binding(claim_db):
    from app.audit.models import AuditEvent

    target, users, claim, _ = pending(claim_db)
    who = actor(users, "admin")
    s = reviews(claim_db)
    data = decision()
    s.approve(who, claim.id, data)
    s.revoke(
        who, claim.id, ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="撤销")
    )
    before = tuple(
        claim_db.scalar(select(func.count()).select_from(model))
        for model in (AuditEvent, AssistedApproval, ProfileClaimDecision)
    )
    replay = s.approve(who, claim.id, data)
    assert replay.status == "revoked" and replay.version == 3
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    assert claim_db.scalar(select(ProfileClaimBinding)).revoked_at is not None
    assert before == tuple(
        claim_db.scalar(select(func.count()).select_from(model))
        for model in (AuditEvent, AssistedApproval, ProfileClaimDecision)
    )


def test_authority_is_rechecked_after_precheck_before_writes(claim_db, monkeypatch):
    from sqlalchemy.orm import Session

    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    original = s._lock_lifecycle
    actor_id = users["accounts"]["admin"].id

    def lose_authority(who, row):
        with Session(claim_db.get_bind()) as other:
            other.add(
                UserPermissionOverride(
                    user_id=actor_id,
                    permission_id=PERMISSIONS["accounts.manage"].id,
                    effect="deny",
                    scope_kind="global",
                )
            )
            other.commit()
        return original(who, row)

    monkeypatch.setattr(s, "_lock_lifecycle", lose_authority)
    with pytest.raises(Exception) as caught:
        s.approve(actor(users, "admin"), claim.id, decision())
    assert caught.value.status == 403
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    assert claim_db.get(ProfileClaim, claim.id).status == "pending"


def test_changed_person_email_is_never_cleared_by_old_revoke(claim_db):
    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    person = claim_db.get(Person, target.person_id)
    person.email_ciphertext = s.cipher.encrypt("later-round@example.com", field_name="email")
    person.email_digest = s.lookup.digest_domain("person-email", "later-round@example.com")
    claim_db.commit()
    with pytest.raises(Exception) as caught:
        s.revoke(
            actor(users, "admin"),
            claim.id,
            ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="旧轮撤销"),
        )
    assert caught.value.status == 409
    assert (
        s.cipher.decrypt(
            claim_db.get(Person, target.person_id).email_ciphertext, field_name="email"
        )
        == "later-round@example.com"
    )


def test_same_email_trusted_successor_keeps_activation_binding_valid(claim_db):
    from app.personnel_intake.claims.binding import require_activation_binding_in_transaction
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake

    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    data = intake(users)
    values = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields and key != "email"
    }
    correction = AssistedProfileCorrection(
        **dict(values, full_name="可信后继姓名"),
        expected_version=2,
        reason="正常更正",
        idempotency_key=uuid4(),
    )
    approvals(claim_db).correct_profile(actor(users, "admin"), target.person_id, correction)
    binding = require_activation_binding_in_transaction(
        claim_db,
        person_id=target.person_id,
        email_digest=claim.email_digest,
        cipher=s.cipher,
        lookup=s.lookup,
    )
    assert binding.claim_id == claim.id
    assert s.detail(actor(users, "admin"), claim.id).allowed_actions == ["revoke"]


def test_revoke_after_trusted_identity_correction_locks_current_target_graph(claim_db):
    from sqlalchemy.exc import OperationalError
    from sqlalchemy.orm import Session

    from app.core.transactions import atomic_write_transaction
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.api.conftest import make_cn_id
    from tests.services.test_personnel_approval import approvals

    target, users, claim, data = pending_minor(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    values = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields and key != "email"
    }
    approvals(claim_db).correct_profile(
        actor(users, "admin"),
        target.person_id,
        AssistedProfileCorrection(
            **dict(values, identity_number=make_cn_id("20150102", 323)),
            expected_version=2,
            reason="核对后更正证件",
            idempotency_key=uuid4(),
        ),
    )
    claim_db.commit()
    assert claim_db.get(Person, target.person_id).identity_digest != claim.identity_digest
    edge_id = claim_db.scalar(
        select(GuardianRelationship.id).where(
            GuardianRelationship.student_person_id == target.person_id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert edge_id is not None
    guardian_id = claim_db.scalar(
        select(GuardianRelationship.guardian_person_id).where(GuardianRelationship.id == edge_id)
    )
    with atomic_write_transaction(claim_db):
        s._lock_lifecycle(actor(users, "admin"), claim)
        # A separate connection must be unable to mutate either the corrected
        # target or its current guardian edge while revoke owns the prefix.
        with Session(claim_db.get_bind()) as other:
            with pytest.raises(OperationalError) as caught:
                other.scalar(
                    select(Person).where(Person.id == target.person_id).with_for_update(nowait=True)
                )
            assert caught.value.orig.sqlstate == "55P03"
        with Session(claim_db.get_bind()) as other:
            from app.identity.readiness import GuardianReadinessPolicy

            other.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError) as caught:
                GuardianReadinessPolicy(other).lock_guardian_relationships(guardian_id)
            assert caught.value.orig.sqlstate == "55P03"


def test_revoke_owned_binding_after_guardian_expiry(claim_db):
    from app.housing.models import HousingStay
    from app.personnel_intake.claims.binding import require_activation_binding_in_transaction
    from app.personnel_intake.claims.errors import ProfileClaimError
    from tests.services.test_housing_reconciliation import _room, _stay

    target, users, claim, _ = pending_minor(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    stay = _stay(
        claim_db,
        person_id=target.person_id,
        room=_room(claim_db),
        actor_id=users["accounts"]["admin"].id,
    )
    before = tuple(getattr(stay, column.key) for column in HousingStay.__table__.columns)
    edge = claim_db.scalar(
        select(GuardianRelationship).where(
            GuardianRelationship.student_person_id == target.person_id
        )
    )
    edge.ended_at = datetime.now(UTC)
    claim_db.commit()
    with pytest.raises(ProfileClaimError):
        require_activation_binding_in_transaction(
            claim_db,
            person_id=target.person_id,
            email_digest=claim.email_digest,
            cipher=s.cipher,
            lookup=s.lookup,
        )
    assert s.detail(actor(users, "admin"), claim.id).allowed_actions == ["revoke"]
    result = s.revoke(
        actor(users, "admin"),
        claim.id,
        ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="监护关系结束后撤销"),
    )
    assert result.status == "revoked" and result.person_id == target.person_id
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    assert claim_db.scalar(select(ProfileClaimBinding)).revoked_at is not None
    claim_db.refresh(stay)
    assert tuple(getattr(stay, column.key) for column in HousingStay.__table__.columns) == before


def test_materialized_person_email_still_blocks_other_people_and_registration_policy(claim_db):
    from app.personnel_intake.errors import CredentialReservationConflict, PersonnelIntakeError
    from app.personnel_intake.locks import CredentialReservationPolicy
    from app.personnel_intake.models import PersonnelCredentialReservation
    from tests.api.conftest import make_cn_id
    from tests.services.test_personnel_intake import intake, service

    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    s.approve(actor(users, "admin"), claim.id, decision())
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(PersonnelCredentialReservation)
            .where(
                PersonnelCredentialReservation.ended_at.is_(None),
                PersonnelCredentialReservation.kind == "email",
            )
        )
        == 0
    )
    with pytest.raises(CredentialReservationConflict):
        CredentialReservationPolicy(claim_db, lookup=s.lookup).require_email_available(
            email="claim@example.com"
        )
    with pytest.raises(PersonnelIntakeError):
        service(claim_db).submit(
            actor(users, "admin"),
            intake(
                users,
                identity_number=make_cn_id("20000102", 322),
                email="claim@example.com",
                phone="13900000002",
            ),
        )
    assert claim_db.get(Person, target.person_id).email_ciphertext is not None


def test_competitor_set_drift_aborts_instead_of_acquiring_another_email_late(claim_db, monkeypatch):
    from sqlalchemy.orm import Session

    target, users, claim, _ = pending(claim_db)
    s = reviews(claim_db)
    original = s._related
    calls = [0]

    def add_competitor(row):
        result = original(row)
        calls[0] += 1
        if calls[0] == 1:
            with Session(claim_db.get_bind()) as other:
                public, sender = claims(other)
                verify_new(public, sender, claim_input(email="late-competitor@example.com"))
        return result

    monkeypatch.setattr(s, "_related", add_competitor)
    with pytest.raises(Exception) as caught:
        s.approve(actor(users, "admin"), claim.id, decision())
    assert caught.value.status == 409 and caught.value.code == "profile_claim_retry"
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    assert claim_db.scalar(select(func.count()).select_from(ProfileClaim)) == 2


def test_review_deadline_crossing_while_waiting_for_session_lock_cannot_bind(claim_db, monkeypatch):
    target, users, claim, now = pending(claim_db)
    now[0] = claim.expires_at - timedelta(seconds=1)
    s = reviews(claim_db, clock=lambda: now[0])
    original = s._lock_sessions

    def cross_deadline(rows):
        original(rows)
        now[0] = claim.expires_at

    monkeypatch.setattr(s, "_lock_sessions", cross_deadline)
    with pytest.raises(Exception) as caught:
        s.approve(actor(users, "admin"), claim.id, decision())
    assert caught.value.status == 409
    assert claim_db.get(Person, target.person_id).email_ciphertext is None
    assert claim_db.get(ProfileClaim, claim.id).status == "pending"


@pytest.mark.parametrize("role", ["administration", "coach", "team_manager"])
def test_both_capabilities_cannot_substitute_for_actual_admin_role(claim_db, role):
    from sqlalchemy import delete

    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES

    _, users, claim, _ = pending(claim_db)
    who = actor(users, "admin")
    claim_db.execute(delete(UserRole).where(UserRole.user_id == who.account.id))
    claim_db.add(UserRole(user_id=who.account.id, role_id=ROLES[role].id))
    for permission in ("accounts.manage", "personnel_intake.review"):
        claim_db.add(
            UserPermissionOverride(
                user_id=who.account.id,
                permission_id=PERMISSIONS[permission].id,
                effect="allow",
                scope_kind="global",
            )
        )
    claim_db.commit()
    s = reviews(claim_db)
    assert not s.context(who).can_enter
    with pytest.raises(Exception) as caught:
        s.detail(who, claim.id)
    assert caught.value.status == 403
