from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.admissions.models import Payment
from app.auth.models import UserAccount
from app.identity.models import Person, StudentProfile
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import AssistedApproval, AssistedPersonProfile
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.team_management.models import TeamMembership
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_personnel_intake import actor, intake, service
from tests.services.test_team_access import teams


@pytest.fixture
def platform_users(db):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    users = _platform_users.__wrapped__(db)
    users["accounts"]["super_admin"] = users["accounts"]["coach"]
    users["people"]["super_admin"] = users["people"]["coach"]
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["admin"].id))
    db.flush()
    return users


def approvals(db):
    from app.personnel_intake.approval_service import PersonnelApprovalService

    return PersonnelApprovalService(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


@pytest.mark.parametrize("kind", ["student", "player", "both"])
@pytest.mark.parametrize("email", [None, "   "])
def test_approved_intake_without_email_is_housing_ready_not_activation_ready(
    db, platform_users, kind, email
):
    from app.housing.resident_readiness import HousingResidentReadinessPolicy
    from app.personnel_intake.models import PersonnelCredentialReservation

    target, _ = teams(db, actor(platform_users, "admin").account.id)
    data = intake(
        platform_users,
        kind=kind,
        email=email,
        team_id=target.id if kind != "student" else None,
        coach_person_ids=[] if kind == "player" else [platform_users["people"]["coach"].id],
    )
    pending = service(db).submit(actor(platform_users, "admin"), data)
    assert pending.scoped_payload.email is None
    result = approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 1)
    person = db.get(Person, result.person_id)
    assert person.email_ciphertext is None and person.email_digest is None
    email_reservations = list(
        db.scalars(
            select(PersonnelCredentialReservation).where(
                PersonnelCredentialReservation.request_id == pending.id,
                PersonnelCredentialReservation.kind == "email",
            )
        )
    )
    assert email_reservations == []
    assert db.get(AssistedPersonProfile, person.id).activation_requirement == "reviewed_claim"
    readiness = HousingResidentReadinessPolicy(
        db, cipher=approvals(db).cipher, lookup=approvals(db).lookup
    ).evaluate_many([person.id], reference_date=date(2026, 10, 20))[person.id]
    assert readiness.housing_ready and not readiness.account_ready
    assert db.scalar(select(UserAccount).where(UserAccount.person_id == person.id)) is None


@pytest.mark.parametrize("email", [None, "intake@example.com"])
def test_null_and_legacy_snapshot_authentication(db, platform_users, email):
    from app.personnel_intake.guardian_records import (
        ApprovalEvidenceError,
        authenticated_approval_facts,
    )

    result = approvals(db).direct_create(
        actor(platform_users, "admin"), intake(platform_users, email=email), uuid4()
    )
    approval = db.get(AssistedApproval, result.approval_id)
    own, guardians = authenticated_approval_facts(
        approval, cipher=approvals(db).cipher, lookup=approvals(db).lookup
    )
    assert own["email"] == email and guardians == []
    approval.snapshot_digest = "0" * 64
    with pytest.raises(ApprovalEvidenceError):
        authenticated_approval_facts(
            approval, cipher=approvals(db).cipher, lookup=approvals(db).lookup
        )


def test_clearing_reserved_email_requires_reviewed_claim_and_cannot_be_refilled(db, platform_users):
    from app.personnel_intake.schemas import AssistedProfileCorrection

    who = actor(platform_users, "admin")
    data = intake(platform_users)
    result = approvals(db).direct_create(who, data, uuid4())
    values = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields
    }
    values["email"] = None
    correction = AssistedProfileCorrection(
        **values, expected_version=1, reason="删除无效邮箱", idempotency_key=uuid4()
    )
    approvals(db).correct_profile(who, result.person_id, correction)
    profile = db.get(AssistedPersonProfile, result.person_id)
    assert profile.activation_requirement == "reviewed_claim"
    assert db.get(Person, result.person_id).email_ciphertext is None
    assert db.get(Person, result.person_id).email is None
    with pytest.raises(PersonnelIntakeError):
        approvals(db).correct_profile(
            who,
            result.person_id,
            correction.model_copy(
                update={
                    "email": data.email,
                    "expected_version": 2,
                    "idempotency_key": uuid4(),
                }
            ),
        )


@pytest.mark.parametrize("email", [None, "reviewed@example.com"])
def test_reviewed_claim_corrections_preserve_email_but_allow_other_fields(
    db, platform_users, email
):
    from app.personnel_intake.schemas import AssistedProfileCorrection

    who = actor(platform_users, "admin")
    data = intake(platform_users, email=email)
    result = approvals(db).direct_create(who, data, uuid4())
    db.get(AssistedPersonProfile, result.person_id).activation_requirement = "reviewed_claim"
    db.flush()
    values = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields
    }
    correction = AssistedProfileCorrection(
        **dict(values, full_name="核对后姓名"),
        expected_version=1,
        reason="更正姓名",
        idempotency_key=uuid4(),
    )
    approvals(db).correct_profile(who, result.person_id, correction)
    assert db.get(Person, result.person_id).full_name == "核对后姓名"
    with pytest.raises(PersonnelIntakeError):
        approvals(db).correct_profile(
            who,
            result.person_id,
            correction.model_copy(
                update={
                    "email": None if email else intake(platform_users).email,
                    "expected_version": 2,
                    "idempotency_key": uuid4(),
                }
            ),
        )


@pytest.mark.parametrize("reviewed", [False, True])
@pytest.mark.parametrize("email", [None, "kept@example.com"])
def test_omitted_correction_email_preserves_current_value(db, platform_users, reviewed, email):
    from app.personnel_intake.guardian_records import person_facts
    from app.personnel_intake.schemas import AssistedProfileCorrection

    who = actor(platform_users, "admin")
    data = intake(platform_users, email=email)
    result = approvals(db).direct_create(who, data, uuid4())
    if reviewed:
        db.get(AssistedPersonProfile, result.person_id).activation_requirement = "reviewed_claim"
        db.flush()
    correction = AssistedProfileCorrection(
        **{
            key: getattr(data, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(data).model_fields and key != "email" and key != "full_name"
        },
        full_name="保留邮箱更正姓名",
        expected_version=1,
        reason="更正姓名",
        idempotency_key=uuid4(),
    )
    assert "email" not in correction.model_fields_set
    approvals(db).correct_profile(who, result.person_id, correction)
    assert person_facts(db.get(Person, result.person_id), approvals(db).cipher)["email"] == email
    assert db.get(Person, result.person_id).full_name == "保留邮箱更正姓名"


@pytest.mark.parametrize("next_email", [None, "later@example.com"])
@pytest.mark.parametrize("omit_email", [True, False])
def test_correction_command_replay_after_later_email_change_preserves_latest_profile(
    db, platform_users, next_email, omit_email
):
    from app.personnel_intake.guardian_records import person_facts
    from app.personnel_intake.schemas import AssistedProfileCorrection

    who = actor(platform_users, "admin")
    data = intake(platform_users)
    original = approvals(db).direct_create(who, data, uuid4())
    fields = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields
    }
    if omit_email:
        fields.pop("email")
    first = AssistedProfileCorrection(
        **dict(fields, full_name="第一次更正"),
        expected_version=1,
        reason="首次核对",
        idempotency_key=uuid4(),
    )
    first_result = approvals(db).correct_profile(who, original.person_id, first)
    later = AssistedProfileCorrection(
        **dict(fields, full_name="后来更正", email=next_email),
        expected_version=2,
        reason="后续核对",
        idempotency_key=uuid4(),
    )
    later_result = approvals(db).correct_profile(who, original.person_id, later)
    assert approvals(db).correct_profile(who, original.person_id, first) == first_result
    profile = db.get(AssistedPersonProfile, original.person_id)
    assert profile.version == 3 and profile.current_approval_id == later_result.approval_id
    assert (
        person_facts(db.get(Person, original.person_id), approvals(db).cipher)["email"]
        == next_email
    )
    assert db.get(Person, original.person_id).full_name == "后来更正"
    assert db.scalar(select(func.count()).select_from(AssistedApproval)) == 3
    with pytest.raises(PersonnelIntakeError):
        approvals(db).correct_profile(
            who,
            original.person_id,
            first.model_copy(
                update={
                    "full_name": "不同客户端命令",
                }
            ),
        )
    if omit_email:
        with pytest.raises(PersonnelIntakeError):
            approvals(db).correct_profile(
                who,
                original.person_id,
                first.model_copy(
                    update={
                        "email": data.email,
                    }
                ),
            )


@pytest.mark.parametrize("kind", ["student", "player", "both"])
def test_approval_without_accounts_creates_one_person_and_no_payments(db, platform_users, kind):
    from app.admissions.models import Enrollment
    from app.revenue_share.models import StudentReferralBinding

    target, _ = teams(db, actor(platform_users, "admin").account.id)
    data = intake(
        platform_users,
        kind=kind,
        team_id=target.id if kind != "student" else None,
        coach_person_ids=[] if kind == "player" else [platform_users["people"]["coach"].id],
    )
    pending = service(db).submit(actor(platform_users, "admin"), data)
    before = db.scalar(select(func.count()).select_from(Person))
    payment_count = db.scalar(select(func.count()).select_from(Payment))
    ancillary_before = [
        db.scalar(select(func.count()).select_from(model))
        for model in (Enrollment, StudentReferralBinding)
    ]
    result = approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 1)
    assert db.scalar(select(func.count()).select_from(Person)) == before + 1
    assert db.scalar(select(UserAccount).where(UserAccount.person_id == result.person_id)) is None
    student = db.get(StudentProfile, result.person_id)
    assert (student is None) == (kind == "player")
    if student:
        assert student.status == "draft" and student.student_number.startswith("REG-")
    assert db.scalar(select(func.count()).select_from(Payment)) == payment_count
    assert [
        db.scalar(select(func.count()).select_from(model))
        for model in (Enrollment, StudentReferralBinding)
    ] == ancillary_before
    assert db.get(AssistedPersonProfile, result.person_id).current_approval_id == result.approval_id
    assert approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 1) == result
    assert db.scalar(select(func.count()).select_from(AssistedApproval)) == 1
    memberships = list(
        db.scalars(select(TeamMembership).where(TeamMembership.person_id == result.person_id))
    )
    assert len(memberships) == (kind != "student")
    with pytest.raises(PersonnelIntakeError):
        approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 2)


def test_self_approval_is_denied_but_explicit_direct_create_is_idempotent(db, platform_users):
    from app.audit.models import AuditEvent

    # This actor has both the coach and actual admin roles.
    who = actor(platform_users, "super_admin")
    pending = service(db).submit(who, intake(platform_users))
    with pytest.raises(PersonnelIntakeError):
        approvals(db).approve(who, pending.id, 1)
    service(db).withdraw(who, pending.id, 1)
    key = uuid4()
    result = approvals(db).direct_create(who, intake(platform_users), key)
    assert result.request_id is None
    assert approvals(db).direct_create(who, intake(platform_users), key) == result
    actions = list(
        db.scalars(select(AuditEvent.action).where(AuditEvent.actor_user_id == who.account.id))
    )
    assert "personnel_intake.direct_create" in actions and "personnel_intake.approve" not in actions
    with pytest.raises(PersonnelIntakeError):
        approvals(db).direct_create(who, intake(platform_users, full_name="changed"), key)


@pytest.mark.parametrize("change", ["reviewer", "submitter", "team"])
def test_recheck_actor_submitter_and_team_at_approval(db, platform_users, change):
    from app.rbac.models import UserRole

    target, _ = teams(db, actor(platform_users, "admin").account.id)
    pending = service(db).submit(
        actor(platform_users, "admin"),
        intake(platform_users, kind="player", coach_person_ids=[], team_id=target.id),
    )
    if change == "team":
        target.is_active = False
    else:
        key = "super_admin" if change == "reviewer" else "admin"
        db.execute(
            UserRole.__table__.delete().where(
                UserRole.user_id == platform_users["accounts"][key].id
            )
        )
    db.flush()
    with pytest.raises(PersonnelIntakeError):
        approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 1)
    assert db.scalar(select(func.count()).select_from(AssistedApproval)) == 0


def test_student_number_uses_existing_sequence_and_china_year(db):
    from app.identity.student_numbers import generate_student_number

    first = generate_student_number(db, datetime(2026, 12, 31, 16, tzinfo=UTC))
    second = generate_student_number(db, datetime(2026, 12, 31, 16, tzinfo=UTC))
    assert first.startswith("REG-2027")
    assert int(second[8:]) == int(first[8:]) + 1


def test_admin_correction_preserves_previous_approval_and_invalidates_proofs(db, platform_users):
    from app.personnel_intake.activation_models import AssistedActivationSession
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from app.registration.models import RegistrationVerificationChallenge

    who = actor(platform_users, "admin")
    original = approvals(db).direct_create(who, intake(platform_users), uuid4())
    old = db.get(AssistedApproval, original.approval_id)
    before = (old.snapshot_ciphertext, old.snapshot_digest)
    now = datetime.now(UTC)
    challenge = RegistrationVerificationChallenge(
        purpose="assisted_activation",
        identifier_kind="email",
        identifier_digest="a" * 64,
        code_digest="b" * 64,
        request_source_digest="c" * 64,
        expires_at=now + timedelta(minutes=10),
        last_sent_at=now,
    )
    db.add(challenge)
    db.flush()
    proof = AssistedActivationSession(
        token_digest="d" * 64,
        challenge_id=challenge.id,
        person_id=original.person_id,
        approval_id=old.id,
        revision_version=1,
        email_digest="a" * 64,
        state="verified",
        created_at=now - timedelta(minutes=1),
        verified_at=now,
        expires_at=now + timedelta(minutes=10),
    )
    account = UserAccount(
        person_id=original.person_id,
        password_hash="synthetic",
        status="active",
        force_password_change=False,
        account_access_origin="assisted_activation",
        assisted_approval_id=old.id,
    )
    db.add_all([proof, account])
    db.flush()
    data = intake(platform_users, full_name="更正姓名")
    correction = AssistedProfileCorrection(
        **{
            key: getattr(data, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(data).model_fields
        },
        expected_version=1,
        reason="核验更正",
        idempotency_key=uuid4(),
    )
    result = approvals(db).correct_profile(who, original.person_id, correction)
    assert result.approval_id != original.approval_id
    assert db.get(Person, original.person_id).full_name == "更正姓名"
    assert (old.snapshot_ciphertext, old.snapshot_digest) == before
    assert db.get(AssistedApproval, result.approval_id).supersedes_approval_id == old.id
    assert account.assisted_approval_id == old.id
    assert proof.expires_at <= datetime.now(UTC) and challenge.consumed_at is not None
    assert approvals(db).correct_profile(who, original.person_id, correction) == result
    with pytest.raises(PersonnelIntakeError):
        approvals(db).correct_profile(
            who, original.person_id, correction.model_copy(update={"idempotency_key": uuid4()})
        )


def test_minor_player_real_guardian_snapshot_and_conflicting_reuse(db, platform_users):
    from app.identity.models import GuardianRelationship
    from app.personnel_intake.guardian_records import capture_current_snapshots
    from tests.api.conftest import make_cn_id

    who = actor(platform_users, "admin")
    target, _ = teams(db, who.account.id)
    guardian = {
        "full_name": "独立监护人",
        "identity_number": make_cn_id("19800102", 331),
        "phone": "13900000880",
        "sex": "male",
        "date_of_birth": date(1980, 1, 2),
        "relationship": "父亲",
    }
    data = intake(
        platform_users,
        kind="player",
        team_id=target.id,
        coach_person_ids=[],
        identity_number=make_cn_id("20150102", 321),
        date_of_birth=date(2015, 1, 2),
        guardians=[guardian],
    )
    result = approvals(db).direct_create(who, data, uuid4())
    old = db.get(AssistedApproval, result.approval_id)
    assert len(old.guardian_relationship_ids) == 1
    relationship = db.get(GuardianRelationship, old.guardian_relationship_ids[0])
    assert relationship.student_person_id == result.person_id
    assert relationship.requires_verification is True
    assert db.get(StudentProfile, result.person_id) is None
    own, guardians = capture_current_snapshots(
        db,
        person_id=result.person_id,
        relationship_ids=old.guardian_relationship_ids,
        cipher=approvals(db).cipher,
    )
    assert str(relationship.id) in guardians and '"email":null' in guardians
    relationship.requires_verification = False
    db.flush()
    assert capture_current_snapshots(
        db,
        person_id=result.person_id,
        relationship_ids=old.guardian_relationship_ids,
        cipher=approvals(db).cipher,
    ) == (own, guardians)
    with pytest.raises(PersonnelIntakeError):
        capture_current_snapshots(
            db,
            person_id=who.person.id,
            relationship_ids=old.guardian_relationship_ids,
            cipher=approvals(db).cipher,
        )
    conflicting = dict(guardian, phone="13900000881")
    with pytest.raises(PersonnelIntakeError):
        approvals(db).direct_create(
            who,
            intake(
                platform_users,
                email="second@example.com",
                identity_number=make_cn_id("20000102", 325),
                guardians=[conflicting],
            ),
            uuid4(),
        )


@pytest.mark.parametrize("phase", ["guardian", "membership", "audit"])
def test_approval_failure_rolls_back_all_formal_rows(db, platform_users, monkeypatch, phase):
    from app.audit.service import AuditService
    from app.personnel_intake.guardian_records import AssistedGuardianWriter
    from app.team_management.membership_service import TeamMembershipService

    target, _ = teams(db, actor(platform_users, "admin").account.id)
    pending = service(db).submit(
        actor(platform_users, "admin"), intake(platform_users, kind="both", team_id=target.id)
    )
    before = db.scalar(select(func.count()).select_from(Person))

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    obj, method = {
        "guardian": (AssistedGuardianWriter, "apply_in_transaction"),
        "membership": (TeamMembershipService, "join_assisted_in_transaction"),
        "audit": (AuditService, "append"),
    }[phase]
    monkeypatch.setattr(obj, method, broken)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        approvals(db).approve(actor(platform_users, "super_admin"), pending.id, 1)
    assert db.scalar(select(func.count()).select_from(Person)) == before
    assert db.scalar(select(func.count()).select_from(AssistedApproval)) == 0


def test_reviewer_actions_and_reasoned_return(db, platform_users):
    pending = service(db).submit(actor(platform_users, "admin"), intake(platform_users))
    reviewer = actor(platform_users, "super_admin")
    assert service(db).detail(reviewer, pending.id).allowed_actions == ["approve", "return"]
    assert service(db).list_requests(reviewer).items[0].allowed_actions == ["approve", "return"]
    with pytest.raises(PersonnelIntakeError):
        approvals(db).return_request(reviewer, pending.id, 1, "   ")
    returned = approvals(db).return_request(reviewer, pending.id, 1, "  核对资料  ")
    assert (returned.status, returned.returned_reason, returned.allowed_actions) == (
        "returned",
        "核对资料",
        [],
    )


def test_coach_replacement_version_and_correction_staleness(db, platform_users):
    from app.personnel_intake.responsibilities import CoachResponsibilityService
    from app.personnel_intake.schemas import AssistedProfileCorrection

    who = actor(platform_users, "admin")
    result = approvals(db).direct_create(who, intake(platform_users), uuid4())
    replacement = CoachResponsibilityService(db)
    replacement.replace(who, result.person_id, [], 1, "负责人调整")
    assert db.get(AssistedPersonProfile, result.person_id).version == 2
    with pytest.raises(PersonnelIntakeError):
        replacement.replace(who, result.person_id, [], 1, "重复旧版本")
    data = intake(platform_users)
    correction = AssistedProfileCorrection(
        **{
            key: getattr(data, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(data).model_fields
        },
        expected_version=1,
        reason="更正",
        idempotency_key=uuid4(),
    )
    with pytest.raises(PersonnelIntakeError):
        approvals(db).correct_profile(who, result.person_id, correction)


@pytest.mark.parametrize("trusted", [False, True])
@pytest.mark.parametrize("mode", ["omit", "replace_label"])
def test_correction_changes_only_owned_unverified_guardian_edges(db, platform_users, trusted, mode):
    from app.identity.models import GuardianRelationship
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.api.conftest import make_cn_id

    guardian = {
        "full_name": "批准监护人",
        "identity_number": make_cn_id("19800102", 333),
        "phone": "13900000883",
        "sex": "male",
        "date_of_birth": date(1980, 1, 2),
        "relationship": "父亲",
    }
    data = intake(platform_users, guardians=[guardian])
    who = actor(platform_users, "admin")
    result = approvals(db).direct_create(who, data, uuid4())
    old = db.get(AssistedApproval, result.approval_id)
    relationship = db.get(GuardianRelationship, old.guardian_relationship_ids[0])
    if trusted:
        relationship.requires_verification = False
        db.flush()
    changed = intake(
        platform_users,
        guardians=[] if mode == "omit" else [dict(guardian, relationship="法定监护人")],
    )
    correction = AssistedProfileCorrection(
        **{
            key: getattr(changed, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(changed).model_fields
        },
        expected_version=1,
        reason="核对监护资料",
        idempotency_key=uuid4(),
    )
    if trusted and mode == "replace_label":
        with pytest.raises(PersonnelIntakeError):
            approvals(db).correct_profile(who, result.person_id, correction)
        assert relationship.ended_at is None
    else:
        new = approvals(db).correct_profile(who, result.person_id, correction)
        assert (relationship.ended_at is None) == trusted
        snapshot = db.get(AssistedApproval, new.approval_id)
        assert old.guardian_relationship_ids == [relationship.id]
        assert relationship.id not in snapshot.guardian_relationship_ids
        if mode == "replace_label":
            successor = db.get(GuardianRelationship, snapshot.guardian_relationship_ids[0])
            assert (
                successor.requires_verification is True and successor.relationship == "法定监护人"
            )


def test_approval_snapshot_database_checks_real_guardian_owner(db, platform_users):
    from sqlalchemy.exc import IntegrityError

    from app.identity.models import GuardianRelationship
    from app.personnel_intake.models import PersonnelIntakeRequest, PersonnelIntakeRevision

    who = actor(platform_users, "admin")
    guardian = platform_users["people"]["guardian"]
    relation = GuardianRelationship(
        guardian_person_id=guardian.id,
        student_person_id=who.person.id,
        relationship="监护人",
        valid_from=date(2026, 1, 1),
    )
    db.add(relation)
    db.flush()

    def insert_snapshot(person_id):
        row = PersonnelIntakeRequest(submitted_by_user_id=who.account.id, kind="student", version=1)
        db.add(row)
        db.flush()
        db.add(
            PersonnelIntakeRevision(
                request_id=row.id,
                version=1,
                payload_ciphertext="synthetic",
                payload_digest="a" * 64,
                identity_digest="b" * 64,
                email_digest="c" * 64,
                created_by_user_id=who.account.id,
            )
        )
        db.flush()
        value = AssistedApproval(
            request_id=row.id,
            revision_version=1,
            person_id=person_id,
            approved_by_user_id=who.account.id,
            snapshot_ciphertext="synthetic",
            snapshot_digest="d" * 64,
            guardian_relationship_version=1,
            guardian_relationship_ids=[relation.id],
            guardian_snapshot_ciphertext="synthetic",
            guardian_snapshot_digest="e" * 64,
        )
        db.add(value)
        db.flush()
        return value.id

    assert insert_snapshot(who.person.id) is not None
    with pytest.raises(IntegrityError), db.begin_nested():
        insert_snapshot(platform_users["people"]["coach"].id)


@pytest.mark.parametrize("day,allowed", [(date(2026, 10, 17), False), (date(2026, 10, 18), True)])
def test_approval_eighteenth_birthday_boundary(db, platform_users, monkeypatch, day, allowed):
    from tests.api.conftest import make_cn_id

    monkeypatch.setattr("app.personnel_intake.codec.business_today", lambda: day)
    data = intake(
        platform_users,
        identity_number=make_cn_id("20081018", 321),
        date_of_birth=date(2008, 10, 18),
    )
    if allowed:
        assert approvals(db).direct_create(actor(platform_users, "admin"), data, uuid4()).person_id
    else:
        with pytest.raises(PersonnelIntakeError):
            approvals(db).direct_create(actor(platform_users, "admin"), data, uuid4())


def test_direct_create_person_deny_is_checked_before_stored_contacts(
    db, platform_users, monkeypatch
):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    who = actor(platform_users, "admin")
    result = approvals(db).direct_create(who, intake(platform_users), uuid4())
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="deny",
            scope_kind="person",
            scope_id=result.person_id,
        )
    )
    db.flush()
    value = approvals(db)

    def forbidden_decrypt(*args, **kwargs):
        pytest.fail("person-scoped denied approval decrypted stored contacts")

    monkeypatch.setattr(value.cipher, "decrypt", forbidden_decrypt)
    with pytest.raises(PersonnelIntakeError):
        value.direct_create(who, intake(platform_users), uuid4())


def test_explicit_direct_create_uses_review_not_submission_capability(db, platform_users):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    who = actor(platform_users, "admin")
    db.add(
        UserPermissionOverride(
            user_id=who.account.id,
            permission_id=PERMISSIONS["personnel_intake.submit"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.flush()
    context = service(db).access.context(who)
    assert context.can_direct_create and not context.can_submit
    assert context.eligible_coaches
    assert approvals(db).direct_create(who, intake(platform_users), uuid4()).person_id


def test_review_only_scope_filters_queue_detail_and_actions(db, platform_users):
    from app.rbac.models import RolePermission, UserPermissionOverride
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.api.conftest import make_cn_id

    submitter = actor(platform_users, "admin")
    reviewer = actor(platform_users, "super_admin")
    first, second = teams(db, submitter.account.id)
    first_request = service(db).submit(
        submitter, intake(platform_users, kind="player", coach_person_ids=[], team_id=first.id)
    )
    second_request = service(db).submit(
        submitter,
        intake(
            platform_users,
            kind="player",
            coach_person_ids=[],
            team_id=second.id,
            identity_number=make_cn_id("20000102", 329),
            email="other-queue@example.com",
        ),
    )
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=reviewer.account.id,
                permission_id=PERMISSIONS["personnel_intake.submit"].id,
                effect="deny",
                scope_kind="global",
            ),
            UserPermissionOverride(
                user_id=reviewer.account.id,
                permission_id=PERMISSIONS["personnel_intake.review"].id,
                effect="allow",
                scope_kind="assigned_team",
                scope_id=first.id,
            ),
        ]
    )
    db.flush()
    s = service(db)
    assert s.access.context(reviewer).can_review is True
    assert [item.id for item in s.list_requests(reviewer).items] == [first_request.id]
    assert s.list_requests(reviewer).total == 1
    assert s.detail(reviewer, first_request.id).allowed_actions == ["approve", "return"]
    with pytest.raises(PersonnelIntakeError) as hidden:
        s.detail(reviewer, second_request.id)
    assert hidden.value.status == 404
    with pytest.raises(PersonnelIntakeError):
        approvals(db).approve(reviewer, second_request.id, 1)


def test_team_scoped_review_handles_both_but_cannot_borrow_team_or_ignore_denies(
    db, platform_users
):
    from app.rbac.models import RolePermission, UserPermissionOverride
    from app.rbac.seed import PERMISSIONS, ROLES
    from tests.api.conftest import make_cn_id

    reviewer = actor(platform_users, "super_admin")
    first, second = teams(db, reviewer.account.id)
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    db.add_all(
        [
            UserPermissionOverride(
                user_id=reviewer.account.id,
                permission_id=PERMISSIONS["personnel_intake.submit"].id,
                effect="deny",
                scope_kind="global",
            ),
            UserPermissionOverride(
                user_id=reviewer.account.id,
                permission_id=PERMISSIONS["personnel_intake.review"].id,
                effect="allow",
                scope_kind="assigned_team",
                scope_id=first.id,
            ),
        ]
    )
    db.flush()
    s = approvals(db)
    assert reviewer.person.id in [
        choice.id for choice in s.access.context(reviewer).eligible_coaches
    ]
    db.commit()
    data = intake(platform_users, kind="both", team_id=first.id)
    with pytest.raises(PersonnelIntakeError):
        s.direct_create(reviewer, data.model_copy(update={"team_id": second.id}), uuid4())
    with pytest.raises(PersonnelIntakeError):
        s.direct_create(
            reviewer, data.model_copy(update={"kind": "student", "team_id": None}), uuid4()
        )
    assert s.direct_create(reviewer, data, uuid4()).person_id
    db.add(
        UserPermissionOverride(
            user_id=reviewer.account.id,
            permission_id=PERMISSIONS["personnel_intake.review"].id,
            effect="deny",
            scope_kind="person",
            scope_id=reviewer.person.id,
        )
    )
    db.flush()
    denied = intake(
        platform_users,
        kind="both",
        team_id=first.id,
        identity_number=make_cn_id("20000102", 333),
        email="coach-denied@example.com",
    )
    with pytest.raises(PersonnelIntakeError):
        s.direct_create(reviewer, denied, uuid4())


def test_existing_disabled_account_player_is_private_conflict_and_atomic(db, platform_users):
    from app.personnel_intake.codec import normalize_payload
    from app.personnel_intake.guardian_records import insert_person

    who = actor(platform_users, "admin")
    target, _ = teams(db, who.account.id)
    data = intake(platform_users, kind="player", coach_person_ids=[], team_id=target.id)
    s = approvals(db)
    person = insert_person(db, normalize_payload(data), cipher=s.cipher, lookup=s.lookup)
    account = UserAccount(
        person_id=person.id,
        password_hash="synthetic",
        status="disabled",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    with pytest.raises(PersonnelIntakeError) as rejected:
        s.direct_create(who, data, uuid4())
    assert rejected.value.status == 409
    assert db.get(AssistedPersonProfile, person.id) is None
    assert account.status == "disabled" and account.account_access_origin == "legacy"


@pytest.mark.parametrize(
    "change,verified,allowed",
    [
        ("email", True, False),
        ("email", False, False),
        ("phone", False, True),
        ("phone", True, False),
        ("identity", True, True),
    ],
)
def test_correction_protects_verified_contacts_but_allows_identity_and_unverified_phone(
    db, platform_users, change, verified, allowed
):
    from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
    from app.auth.models import AccountLoginIdentifier
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from tests.api.conftest import make_cn_id

    who = actor(platform_users, "admin")
    s = approvals(db)
    data = intake(platform_users)
    result = s.direct_create(who, data, uuid4())
    account = UserAccount(
        person_id=result.person_id,
        password_hash="synthetic",
        status="active",
        force_password_change=False,
        account_access_origin="assisted_activation",
        assisted_approval_id=result.approval_id,
    )
    db.add(account)
    db.flush()
    identifier = None
    if verified:
        kind = "phone" if change == "phone" else "email"
        identifier = AccountLoginIdentifier(
            account_id=account.id,
            kind=kind,
            identifier_digest=login_identifier_digest(
                s.lookup, NormalizedLoginIdentifier(kind, getattr(data, kind).get_secret_value())
            ),
            verified_at=datetime.now(UTC),
            verification_source="self_service",
            is_primary=False,
        )
        db.add(identifier)
        db.flush()
    before = (
        (identifier.id, identifier.identifier_digest, identifier.verified_at)
        if identifier
        else None
    )
    db.commit()
    updates = {
        "email": {"email": "changed@example.com"},
        "phone": {"phone": "13900000009"},
        "identity": {
            "identity_number": make_cn_id("19990102", 321),
            "date_of_birth": date(1999, 1, 2),
        },
    }[change]
    changed = intake(platform_users, **updates)
    correction = AssistedProfileCorrection(
        **{
            key: getattr(changed, key)
            for key in AssistedProfileCorrection.model_fields
            if key in type(changed).model_fields
        },
        expected_version=1,
        reason="核验更正",
        idempotency_key=uuid4(),
    )
    if allowed:
        successor = s.correct_profile(who, result.person_id, correction)
        assert successor.approval_id != result.approval_id
    else:
        with pytest.raises(PersonnelIntakeError):
            s.correct_profile(who, result.person_id, correction)
    db.refresh(account)
    assert account.assisted_approval_id == result.approval_id and account.status == "active"
    if identifier:
        assert (identifier.id, identifier.identifier_digest, identifier.verified_at) == before


def test_snapshot_comparison_reads_current_database_facts_not_cached_entities(db, platform_users):
    from app.personnel_intake.guardian_records import approval_snapshots_match

    s = approvals(db)
    result = s.direct_create(actor(platform_users, "admin"), intake(platform_users), uuid4())
    approval = db.get(AssistedApproval, result.approval_id)
    assert approval_snapshots_match(db, approval=approval, cipher=s.cipher, lookup=s.lookup)
    db.execute(
        Person.__table__.update()
        .where(Person.id == result.person_id)
        .values(full_name="实际已更改")
    )
    assert not approval_snapshots_match(db, approval=approval, cipher=s.cipher, lookup=s.lookup)


def test_ordinary_approval_replay_rechecks_reviewer_not_historical_submitter(db, platform_users):
    from app.rbac.models import UserRole

    submitter = actor(platform_users, "admin")
    reviewer = actor(platform_users, "super_admin")
    pending = service(db).submit(submitter, intake(platform_users))
    approved = approvals(db).approve(reviewer, pending.id, 1)
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == submitter.account.id))
    db.commit()
    assert approvals(db).approve(reviewer, pending.id, 1) == approved
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == reviewer.account.id))
    db.commit()
    with pytest.raises(PersonnelIntakeError):
        approvals(db).approve(reviewer, pending.id, 1)


def test_pending_existing_person_drift_never_creates_replacement(db, platform_users):
    from app.identity.schemas import PersonUpdate
    from app.identity.service import IdentityService
    from app.personnel_intake.codec import normalize_payload
    from app.personnel_intake.guardian_records import insert_person
    from app.personnel_intake.models import PersonnelIntakeRequest
    from tests.api.conftest import make_cn_id

    s = approvals(db)
    data = intake(platform_users)
    person = insert_person(db, normalize_payload(data), cipher=s.cipher, lookup=s.lookup)
    pending = service(db).submit(actor(platform_users, "admin"), data)
    assert db.get(PersonnelIntakeRequest, pending.id).person_id == person.id
    IdentityService(db, cipher=s.cipher, lookup=s.lookup).update_person(
        person.id,
        PersonUpdate(
            identity_number=make_cn_id("20000102", 335), email="corrected-link@example.com"
        ),
        actor_user_id=actor(platform_users, "admin").account.id,
        request_id=str(uuid4()),
    )
    before = db.scalar(select(func.count()).select_from(Person))
    with pytest.raises(PersonnelIntakeError) as rejected:
        s.approve(actor(platform_users, "super_admin"), pending.id, 1)
    assert rejected.value.status == 409
    assert db.scalar(select(func.count()).select_from(Person)) == before
    assert db.scalar(select(func.count()).select_from(AssistedApproval)) == 0
    row = db.get(PersonnelIntakeRequest, pending.id, populate_existing=True)
    assert row.person_id == person.id and row.state == "pending"
    assert db.get(AssistedPersonProfile, person.id) is None


def test_coach_change_persists_trimmed_reason_in_same_audit(db, platform_users):
    from app.audit.models import AuditEvent
    from app.personnel_intake.responsibilities import CoachResponsibilityService

    who = actor(platform_users, "admin")
    result = approvals(db).direct_create(who, intake(platform_users), uuid4())
    CoachResponsibilityService(db).replace(who, result.person_id, [], 1, "  轮值调整  ")
    event = db.scalar(
        select(AuditEvent).where(AuditEvent.action == "personnel_intake.coaches_replace")
    )
    assert event.purpose == "轮值调整"
    assert event.event_metadata == {"new_version": 2}


@pytest.mark.parametrize("reason", ["请联系13900000123", "更" * 201])
def test_coach_change_rejects_sensitive_or_overlong_reason_before_writes(
    db, platform_users, reason
):
    from app.audit.models import AuditEvent
    from app.personnel_intake.responsibilities import CoachResponsibilityService

    who = actor(platform_users, "admin")
    result = approvals(db).direct_create(who, intake(platform_users), uuid4())
    with pytest.raises(PersonnelIntakeError) as rejected:
        CoachResponsibilityService(db).replace(who, result.person_id, [], 1, reason)
    assert rejected.value.status == 422 and "原因" in rejected.value.detail
    assert reason not in rejected.value.detail
    assert db.get(AssistedPersonProfile, result.person_id).version == 1
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "personnel_intake.coaches_replace")
        )
        == 0
    )
