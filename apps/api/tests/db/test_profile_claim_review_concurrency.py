from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.identity.models import GuardianRelationship, Person
from app.identity.service import IdentityService
from app.personnel_intake.claims.models import ProfileClaim, ProfileClaimBinding
from app.personnel_intake.claims.schemas import ClaimReasonInput
from app.personnel_intake.schemas import AssistedProfileCorrection
from tests.api.conftest import make_cn_id
from tests.services.test_personnel_approval import approvals
from tests.services.test_profile_claim_review import decision, pending, pending_minor, reviews
from tests.services.test_profile_claims import claim_db as _claim_db


@pytest.fixture
def claim_db(postgres_engine):
    yield from _claim_db.__wrapped__(postgres_engine)


def test_two_emails_cannot_both_approve_on_independent_connections(claim_db):
    target, users, _, _ = pending(claim_db, competing=True)
    ids = list(claim_db.scalars(select(ProfileClaim.id)))
    actor_id = users["accounts"]["admin"].id
    engine = claim_db.get_bind()
    claim_db.rollback()
    ready = Barrier(2)

    def approve(identifier):
        with Session(engine) as db:
            account = db.get(UserAccount, actor_id)
            who = AuthContext(
                account=account, person=db.get(Person, account.person_id), session=None
            )
            ready.wait(timeout=10)
            try:
                return reviews(db).approve(who, identifier, decision()).status
            except Exception as error:
                if getattr(error, "status", None) == 409:
                    return "conflict"
                raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(approve, ids))
    assert sorted(outcomes) == ["approved", "conflict"]
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(ProfileClaimBinding)
            .where(ProfileClaimBinding.revoked_at.is_(None))
        )
        == 1
    )
    assert (
        claim_db.scalar(
            select(func.count()).select_from(Person).where(Person.id == target.person_id)
        )
        == 1
    )
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
        is None
    )


@pytest.mark.parametrize("competitor", ["correction", "account", "revoke"])
def test_review_races_share_real_credential_and_person_locks(claim_db, competitor):
    from uuid import uuid4

    from app.auth.schemas import AccountCreate
    from app.auth.service import AccountService
    from app.identity.service import IdentityService
    from app.personnel_intake.claims.schemas import ClaimReasonInput
    from app.personnel_intake.schemas import AssistedProfileCorrection
    from app.security.passwords import PasswordService
    from tests.services.test_personnel_approval import approvals
    from tests.services.test_personnel_intake import intake

    target, users, claim, _ = pending(claim_db)
    actor_id = users["accounts"]["admin"].id
    claim_id = claim.id
    if competitor == "revoke":
        reviews(claim_db).approve(
            AuthContext(
                account=users["accounts"]["admin"],
                person=claim_db.get(Person, users["people"]["admin"].id),
                session=None,
            ),
            claim_id,
            decision(),
        )
    original = intake(users, email=None)
    payload = {
        key: getattr(original, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(original).model_fields and key != "email"
    }
    correction = AssistedProfileCorrection(
        **dict(payload, full_name="更正后的隔离姓名"),
        expected_version=2 if competitor == "revoke" else 1,
        reason="隔离更正",
        idempotency_key=uuid4(),
    )
    engine = claim_db.get_bind()
    claim_db.rollback()
    ready = Barrier(2)

    def perform(kind):
        with Session(engine) as db:
            account = db.get(UserAccount, actor_id)
            who = AuthContext(
                account=account, person=db.get(Person, account.person_id), session=None
            )
            ready.wait(timeout=10)
            try:
                if kind == "review":
                    if competitor == "revoke":
                        reviews(db).revoke(
                            who,
                            claim_id,
                            ClaimReasonInput(
                                expected_version=2, idempotency_key=uuid4(), reason="撤销"
                            ),
                        )
                    else:
                        reviews(db).approve(who, claim_id, decision())
                elif competitor in {"correction", "revoke"}:
                    approvals(db).correct_profile(who, target.person_id, correction)
                else:
                    service = reviews(db)
                    identity = IdentityService(db, cipher=service.cipher, lookup=service.lookup)
                    AccountService(
                        db, identity_service=identity, passwords=PasswordService()
                    ).create_account(
                        AccountCreate(person_id=target.person_id, email="opening@example.com")
                    )
                return "success"
            except Exception as error:
                if getattr(error, "status", None) in {400, 403, 409, 422} or type(
                    error
                ).__name__ in {
                    "PersonNotReady",
                    "AccountContactMismatch",
                    "CredentialReservationConflict",
                }:
                    return "conflict"
                raise

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(perform, ["review", "other"]))
    assert "success" in outcomes
    person = claim_db.get(Person, target.person_id, populate_existing=True)
    binding = claim_db.scalar(
        select(ProfileClaimBinding).where(ProfileClaimBinding.revoked_at.is_(None))
    )
    account = claim_db.scalar(select(UserAccount).where(UserAccount.person_id == target.person_id))
    assert (
        claim_db.scalar(
            select(func.count()).select_from(Person).where(Person.id == target.person_id)
        )
        == 1
    )
    assert (
        claim_db.scalar(
            select(func.count())
            .select_from(ProfileClaimBinding)
            .where(ProfileClaimBinding.revoked_at.is_(None))
        )
        <= 1
    )
    if binding:
        assert (
            reviews(claim_db).cipher.decrypt(person.email_ciphertext, field_name="email")
            == "claim@example.com"
        )
        assert account is None
    elif account:
        assert (
            reviews(claim_db).cipher.decrypt(person.email_ciphertext, field_name="email")
            == "opening@example.com"
        )
    elif competitor != "revoke":
        assert person.email_ciphertext is None


def test_revoke_races_guardian_change_after_identity_correction_on_independent_connections(
    claim_db,
):
    from app.housing.models import HousingStay
    from tests.services.test_housing_reconciliation import _room, _stay

    target, users, claim, data = pending_minor(claim_db)
    reviews(claim_db).approve(
        AuthContext(
            account=users["accounts"]["admin"],
            person=claim_db.get(Person, users["people"]["admin"].id),
            session=None,
        ),
        claim.id,
        decision(),
    )
    stay = _stay(
        claim_db,
        person_id=target.person_id,
        room=_room(claim_db),
        actor_id=users["accounts"]["admin"].id,
    )
    housing_before = tuple(getattr(stay, column.key) for column in HousingStay.__table__.columns)
    fields = {
        key: getattr(data, key)
        for key in AssistedProfileCorrection.model_fields
        if key in type(data).model_fields and key != "email"
    }
    approvals(claim_db).correct_profile(
        AuthContext(
            account=users["accounts"]["admin"],
            person=claim_db.get(Person, users["people"]["admin"].id),
            session=None,
        ),
        target.person_id,
        AssistedProfileCorrection(
            **dict(fields, identity_number=make_cn_id("20150102", 323)),
            expected_version=2,
            reason="独立连接更正证件",
            idempotency_key=uuid4(),
        ),
    )
    edge_id = claim_db.scalar(
        select(GuardianRelationship.id).where(
            GuardianRelationship.student_person_id == target.person_id,
            GuardianRelationship.ended_at.is_(None),
        )
    )
    assert edge_id is not None
    stay_id, claim_id, actor_id = stay.id, claim.id, users["accounts"]["admin"].id
    engine = claim_db.get_bind()
    claim_db.commit()
    locked, release = Event(), Event()

    def revoke():
        with Session(engine) as db:
            account = db.get(UserAccount, actor_id)
            who = AuthContext(account=account, person=db.get(Person, account.person_id), session=None)
            service = reviews(db)
            original = service._lock_sessions

            def hold_after_business_locks(rows):
                locked.set()
                assert release.wait(timeout=10)
                return original(rows)

            service._lock_sessions = hold_after_business_locks
            return service.revoke(
                who,
                claim_id,
                ClaimReasonInput(expected_version=2, idempotency_key=uuid4(), reason="独立连接撤销"),
            ).status

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(revoke)
        assert locked.wait(timeout=10)
        try:
            with Session(engine) as other:
                service = reviews(other)
                other.execute(text("SET LOCAL lock_timeout = '200ms'"))
                with pytest.raises(OperationalError) as caught:
                    IdentityService(other, cipher=service.cipher, lookup=service.lookup).end_guardian_relationship(
                        target.person_id,
                        edge_id,
                        ended_by_user_id=actor_id,
                        reason="并发结束监护关系",
                        ended_at=datetime.now(UTC),
                        request_id=str(uuid4()),
                    )
                assert caught.value.orig.sqlstate == "55P03"
        finally:
            release.set()
        assert future.result(timeout=10) == "revoked"

    with Session(engine) as other:
        service = reviews(other)
        IdentityService(other, cipher=service.cipher, lookup=service.lookup).end_guardian_relationship(
            target.person_id,
            edge_id,
            ended_by_user_id=actor_id,
            reason="撤销后结束监护关系",
            ended_at=datetime.now(UTC),
            request_id=str(uuid4()),
        )
    assert claim_db.get(ProfileClaim, claim_id, populate_existing=True).status == "revoked"
    assert claim_db.get(Person, target.person_id, populate_existing=True).email_ciphertext is None
    assert claim_db.scalar(select(ProfileClaimBinding)).revoked_at is not None
    current_stay = claim_db.get(HousingStay, stay_id, populate_existing=True)
    assert tuple(getattr(current_stay, column.key) for column in HousingStay.__table__.columns) == housing_before
    assert (
        claim_db.scalar(select(func.count()).select_from(Person).where(Person.id == target.person_id))
        == 1
    )
