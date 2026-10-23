from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person, StudentProfile
from app.personnel_intake.models import AssistedApproval
from app.security.encryption import EnvelopeCipher, LookupDigest
from tests.api.conftest import make_cn_id
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor, intake
from tests.services.test_team_access import teams


@pytest.fixture(autouse=True)
def fixed_approval_clock(monkeypatch):
    """Keep approved guardian effective dates aligned with these reference dates."""
    from app.personnel_intake import approval_service

    fixed = datetime(2026, 10, 18, 8, tzinfo=UTC)

    class FixedApprovalDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed.replace(tzinfo=None) if tz is None else fixed.astimezone(tz)

    monkeypatch.setattr(approval_service, "datetime", FixedApprovalDateTime)


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def approved_minor(
    db, users, *, kind="student", sequence=321, guardian_count=1, approval_service=None
):
    target = teams(db, actor(users, "admin").account.id)[0] if kind != "student" else None
    data = intake(
        users,
        kind=kind,
        team_id=target.id if target else None,
        coach_person_ids=[] if kind == "player" else [users["people"]["coach"].id],
        identity_number=make_cn_id("20150102", sequence),
        date_of_birth=date(2015, 1, 2),
        email=f"assisted-{sequence}@example.com",
        guardians=[
            {
                "full_name": f"监护人员{i}",
                "identity_number": make_cn_id("19800102", 701 + i * 2),
                "phone": f"1390000088{i}",
                "sex": "male",
                "date_of_birth": date(1980, 1, 2),
                "relationship": "父亲" if i == 0 else "亲属",
                "is_primary": i == 0,
            }
            for i in range(guardian_count)
        ],
    )
    result = (approval_service or approvals(db)).direct_create(actor(users, "admin"), data, uuid4())
    approval = db.get(AssistedApproval, result.approval_id)
    return (
        result,
        data,
        list(
            db.scalars(
                select(GuardianRelationship)
                .where(GuardianRelationship.id.in_(approval.guardian_relationship_ids))
                .order_by(GuardianRelationship.id)
            )
        ),
    )


def policy(db):
    from app.personnel_intake.eligibility import AssistedEligibilityPolicy

    return AssistedEligibilityPolicy(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )


@pytest.mark.parametrize("kind", ["student", "player", "both"])
def test_approved_minor_needs_facts_not_guardian_account(db, platform_users, kind):
    result, _, edges = approved_minor(db, platform_users, kind=kind)
    output = policy(db).evaluate_many(
        [result.person_id, uuid4()], reference_date=date(2026, 10, 18)
    )
    value = output[result.person_id]
    assert (value.approved, value.eligible, value.kind, value.version, value.missing) == (
        True,
        True,
        kind,
        1,
        (),
    )
    assert value.approval_id == result.approval_id
    assert edges[0].requires_verification
    assert (
        db.scalar(select(UserAccount).where(UserAccount.person_id == edges[0].guardian_person_id))
        is None
    )
    assert (db.get(StudentProfile, result.person_id) is None) == (kind == "player")
    assert sum(v.eligible for v in output.values()) == 1


def test_each_approved_guardian_is_independent_and_adulthood_removes_requirement(
    db, platform_users
):
    result, _, edges = approved_minor(db, platform_users, guardian_count=2)
    db.get(Person, edges[0].guardian_person_id).full_name = "已变更档案"
    db.flush()
    assert (
        policy(db)
        .evaluate_many([result.person_id], reference_date=date(2033, 1, 1))[result.person_id]
        .eligible
    )
    db.get(Person, edges[1].guardian_person_id).full_name = "也已变更"
    db.flush()
    assert (
        not policy(db)
        .evaluate_many([result.person_id], reference_date=date(2033, 1, 1))[result.person_id]
        .eligible
    )
    assert (
        policy(db)
        .evaluate_many([result.person_id], reference_date=date(2033, 1, 2))[result.person_id]
        .eligible
    )


@pytest.mark.parametrize("change", ["person", "guardian", "deleted", "edge", "expired", "future"])
def test_current_facts_and_effective_dates_are_required(db, platform_users, change):
    result, data, edges = approved_minor(db, platform_users, kind="player")
    if change == "person":
        db.get(Person, result.person_id).full_name = "更改姓名"
    elif change == "guardian":
        db.get(Person, edges[0].guardian_person_id).phone_ciphertext = EnvelopeCipher(
            bytes(range(32))
        ).encrypt("19990000002", field_name="phone")
    elif change == "deleted":
        db.get(Person, edges[0].guardian_person_id).deleted_at = datetime.now(UTC)
    elif change == "edge":
        edges[0].relationship = "亲属"
    else:
        # Date transitions without changing authenticated facts.
        today = date(2026, 10, 17) if change == "future" else date(2030, 1, 1)
        if change == "expired":
            edges[0].valid_until = date(2026, 10, 18)
            db.flush()
            correction(db, platform_users, result.person_id, data)
            assert (
                policy(db)
                .evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[
                    result.person_id
                ]
                .eligible
            )
        assert (
            not policy(db)
            .evaluate_many([result.person_id], reference_date=today)[result.person_id]
            .eligible
        )
        return
    db.flush()
    assert (
        not policy(db)
        .evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[result.person_id]
        .eligible
    )


def test_eligibility_batch_reads_do_not_grow_per_person(db, platform_users):
    ids = [approved_minor(db, platform_users, sequence=321 + i * 2)[0].person_id for i in range(6)]
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", record)
    try:
        result = policy(db).evaluate_many(ids, reference_date=date(2026, 10, 18))
    finally:
        event.remove(db.bind, "before_cursor_execute", record)
    assert all(result[value].eligible for value in ids)
    assert len(statements) <= 8


def correction(db, users, person_id, data, **changes):
    from app.personnel_intake.models import AssistedPersonProfile
    from app.personnel_intake.schemas import AssistedProfileCorrection

    values = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields
    }
    values.update(changes)
    return approvals(db).correct_profile(
        actor(users, "admin"),
        person_id,
        AssistedProfileCorrection(
            **values,
            expected_version=db.get(AssistedPersonProfile, person_id).version,
            reason="隔离测试更正",
            idempotency_key=uuid4(),
        ),
    )


def test_mutable_request_kind_cannot_change_approved_kind(db, platform_users):
    from app.personnel_intake.models import PersonnelIntakeRequest

    result, _, _ = approved_minor(db, platform_users)
    approval = db.get(AssistedApproval, result.approval_id)
    db.get(PersonnelIntakeRequest, approval.request_id).kind = "player"
    db.flush()
    assert (
        not policy(db)
        .evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[result.person_id]
        .eligible
    )


def test_unapproved_new_guardian_cannot_substitute_for_approved_edge(db, platform_users):
    from app.personnel_intake.guardian_records import AssistedGuardianWriter
    from app.personnel_intake.schemas import AssistedGuardianInput

    result, _, edges = approved_minor(db, platform_users)
    db.get(Person, edges[0].guardian_person_id).full_name = "审批事实已失效"
    db.flush()
    AssistedGuardianWriter(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    ).apply_in_transaction(
        result.person_id,
        [
            AssistedGuardianInput(
                full_name="新增未审监护人",
                identity_number=make_cn_id("19800102", 709),
                phone="19990000002",
                sex="male",
                date_of_birth=date(1980, 1, 2),
                relationship="亲属",
            )
        ],
        date(2026, 10, 18),
    )
    assert (
        not policy(db)
        .evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[result.person_id]
        .eligible
    )


def test_evaluation_reloads_current_facts_in_long_lived_session(db, platform_users):
    from sqlalchemy import update

    result, _, edges = approved_minor(db, platform_users)
    guardian = db.get(Person, edges[0].guardian_person_id)
    db.execute(
        update(Person)
        .where(Person.id == guardian.id)
        .values(full_name="外部变更")
        .execution_options(synchronize_session=False)
    )
    assert (
        not policy(db)
        .evaluate_many([result.person_id], reference_date=date(2026, 10, 18))[result.person_id]
        .eligible
    )


@pytest.mark.parametrize(
    "corruption", ["envelope", "digest", "json", "shape", "edge_ids", "coercion"]
)
def test_malformed_approval_evidence_raises_controlled_integrity_error(
    db, platform_users, corruption
):

    from app.personnel_intake.guardian_records import (
        GUARDIAN_SNAPSHOT_AAD,
        GUARDIAN_SNAPSHOT_DOMAIN,
        ApprovalEvidenceError,
        authenticated_approval_facts,
    )

    result, _, _ = approved_minor(db, platform_users)
    row = db.get(AssistedApproval, result.approval_id)
    # Detached evidence object: do not bypass the database immutable-history trigger.
    evidence = AssistedApproval(
        **{column.key: getattr(row, column.key) for column in AssistedApproval.__table__.columns}
    )
    cipher, lookup = EnvelopeCipher(bytes(range(32))), LookupDigest(bytes(range(32)))
    if corruption == "envelope":
        evidence.guardian_snapshot_ciphertext = "corrupt"
    elif corruption == "digest":
        evidence.guardian_snapshot_digest = "0" * 64
    elif corruption == "edge_ids":
        evidence.guardian_relationship_ids = [uuid4()]
    elif corruption == "coercion":
        import json

        from app.personnel_intake.guardian_records import canonical_json

        facts = json.loads(
            cipher.decrypt(evidence.guardian_snapshot_ciphertext, field_name=GUARDIAN_SNAPSHOT_AAD)
        )
        facts[0]["is_primary"] = "false"
        raw = canonical_json(facts)
        evidence.guardian_snapshot_ciphertext = cipher.encrypt(
            raw, field_name=GUARDIAN_SNAPSHOT_AAD
        )
        evidence.guardian_snapshot_digest = lookup.digest_domain(GUARDIAN_SNAPSHOT_DOMAIN, raw)
    else:
        raw = "not json" if corruption == "json" else '[{"guardian":null}]'
        evidence.guardian_snapshot_ciphertext = cipher.encrypt(
            raw, field_name=GUARDIAN_SNAPSHOT_AAD
        )
        evidence.guardian_snapshot_digest = lookup.digest_domain(GUARDIAN_SNAPSHOT_DOMAIN, raw)
    with pytest.raises(ApprovalEvidenceError) as raised:
        authenticated_approval_facts(evidence, cipher=cipher, lookup=lookup)
    assert raised.value.status_code == 503
