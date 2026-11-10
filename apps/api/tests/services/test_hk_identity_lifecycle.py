from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import (
    approval_snapshots_match,
    authenticated_approval_facts,
)
from app.personnel_intake.models import AssistedApproval
from app.personnel_intake.schemas import AssistedProfileCorrection, PendingIntakeInput
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor, intake, service


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


@pytest.fixture
def claim_db(postgres_engine):
    from tests.services.test_profile_claims import claim_db as committed

    yield from committed.__wrapped__(postgres_engine)


def test_hkid_claim_completion_and_real_otp_activation_keep_identity(claim_db):
    from pydantic import SecretStr

    from app.auth.models import UserAccount
    from app.personnel_intake.claims.models import ProfileClaim
    from app.personnel_intake.claims.proof import validate_claim_proof
    from tests.services.test_assisted_activation import activation
    from tests.services.test_profile_claim_binding import verified_activation
    from tests.services.test_profile_claim_review import decision, reviews
    from tests.services.test_profile_claims import (
        claims,
        incomplete_pending_target,
        pending_claim_input,
        verify_new,
    )

    target, users = incomplete_pending_target(
        claim_db,
        full_name="香港认领本人",
        identity_number="A1234563",
        document_type="hk_id",
        document_issuing_country=None,
        email="claim@example.com",
    )
    initial, initial_sender = activation(claim_db)
    initial.start("claim@example.com", "127.0.9.84")
    assert not initial_sender.messages
    public, sender = claims(claim_db)
    verify_new(
        public,
        sender,
        pending_claim_input(
            full_name="香港认领本人",
            identity_number="a123456（3）",
            document_type="hk_id",
            issuing_country="HK",
            completion={"phone": "13900000984", "sex": "female", "date_of_birth": "2000-01-02"},
        ),
    )
    claim = claim_db.scalar(select(ProfileClaim))
    assert claim.target_person_id == target.person_id
    proof = validate_claim_proof(claim_db, claim, cipher=public.cipher, lookup=public.lookup)
    assert proof.document_type == "hk_id" and proof.identity_number == "A1234563"
    reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    person = claim_db.get(Person, target.person_id)
    assert person.date_of_birth == date(2000, 1, 2) and person.sex == "female"
    activate, token, code, _ = verified_activation(claim_db, person_id=person.id)
    assert activate.finish(token, code, SecretStr("SecurePass1")).completed
    account = claim_db.scalar(select(UserAccount).where(UserAccount.person_id == person.id))
    assert account is not None
    assert person.identity_digest == public.lookup.digest_domain("hk-identity", "A1234563")
    assert person.document_issuing_country is person.passport_number_digest is None


def test_strict_hkid_submission_approval_correction_and_guardian(db, platform_users):
    s = approvals(db)
    who = actor(platform_users, "admin")
    fields = {
        "document_type": "hk_id",
        "identity_number": "A123456(3)",
        "sex": "female",
        "date_of_birth": date(2015, 1, 1),
        "guardians": [
            {
                "full_name": "香港监护人",
                "document_type": "hk_id",
                "identity_number": "AB123456(9)",
                "sex": "male",
                "date_of_birth": "1980-01-01",
                "phone": "13900000002",
                "relationship": "父亲",
            }
        ],
    }
    submitted = service(db).submit(who, intake(platform_users, **fields))
    result = s.approve(actor(platform_users, "super_admin"), submitted.id, 1)
    person = db.get(Person, result.person_id)
    assert person.document_type == "hk_id"
    assert person.identity_digest == s.lookup.digest_domain("hk-identity", "A1234563")
    assert person.passport_number_digest is person.document_issuing_country is None
    approval = db.get(AssistedApproval, result.approval_id)
    own, guardians = authenticated_approval_facts(approval, cipher=s.cipher, lookup=s.lookup)
    assert own["document_type"] == guardians[0]["guardian"]["document_type"] == "hk_id"
    assert approval_snapshots_match(db, approval=approval, cipher=s.cipher, lookup=s.lookup)
    corrected = s.correct_profile(
        who,
        person.id,
        AssistedProfileCorrection(
            full_name="更正姓名",
            phone="13900000001",
            email="intake@example.com",
            expected_version=1,
            reason="核对资料",
            idempotency_key=uuid4(),
            **fields,
        ),
    )
    assert corrected.person_id == person.id
    assert person.identity_digest == s.lookup.digest_domain("hk-identity", "A1234563")


def test_pending_hkid_duplicate_is_normalized_and_does_not_match_passport(db, platform_users):
    s = approvals(db)
    who = actor(platform_users, "admin")

    def source(number, document_type="hk_id", **more):
        return PendingIntakeInput(
            kind="staff",
            full_name="香港待补",
            document_type=document_type,
            identity_number=number,
            **more,
        )

    first = s.direct_create_pending(who, source("A123456(3)"), uuid4())
    person = db.get(Person, first.person_id)
    assert person.sex is person.date_of_birth is None
    with pytest.raises(PersonnelIntakeError):
        s.direct_create_pending(who, source(" a 1234563 ", document_issuing_country="HK"), uuid4())
    other = s.direct_create_pending(
        who, source("A1234563", "passport", document_issuing_country="HK"), uuid4()
    )
    assert other.person_id != first.person_id
    assert len(list(db.scalars(select(Person).where(Person.full_name == "香港待补")))) == 2
