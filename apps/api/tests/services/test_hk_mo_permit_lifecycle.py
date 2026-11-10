"""Real PostgreSQL lifecycle coverage with synthetic permit identities only."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select

from app.auth.models import UserAccount
from app.identity.models import GuardianRelationship, Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.guardian_records import authenticated_approval_facts
from app.personnel_intake.models import AssistedApproval
from app.personnel_intake.schemas import AssistedProfileCorrection, PendingIntakeInput
from tests.services.test_personnel_approval import approvals
from tests.services.test_personnel_approval import platform_users as _platform_users
from tests.services.test_personnel_intake import actor, intake, service
from tests.unit.test_hk_mo_permit_identity import guardian


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


@pytest.fixture
def claim_db(postgres_engine):
    from tests.services.test_profile_claims import claim_db as committed

    yield from committed.__wrapped__(postgres_engine)


def test_permit_managed_approval_correction_and_shared_guardian(db, platform_users):
    s, who = approvals(db), actor(platform_users, "admin")
    fields = {
        "document_type": "hk_mo_permit",
        "identity_number": "h8765432101",
        "sex": "female",
        "date_of_birth": date(2015, 1, 1),
        "guardians": [guardian()],
    }
    submitted = service(db).submit(who, intake(platform_users, **fields))
    first = s.approve(actor(platform_users, "super_admin"), submitted.id, 1)
    second = s.direct_create(
        who,
        intake(
            platform_users,
            **(
                fields
                | {
                    "identity_number": "M87654321",
                    "full_name": "Synthetic second minor",
                    "phone": "13900000003",
                    "email": "second-synthetic@example.com",
                    "guardians": [guardian(identity_number="M12345678")],
                }
            ),
        ),
        uuid4(),
    )
    edges = list(
        db.scalars(
            select(GuardianRelationship).where(
                GuardianRelationship.student_person_id.in_([first.person_id, second.person_id])
            )
        )
    )
    assert len(edges) == 2 and len({e.guardian_person_id for e in edges}) == 1
    person = db.get(Person, first.person_id)
    assert person.identity_digest == s.lookup.digest_domain("hk-mo-permit-identity", "H87654321")
    assert person.document_issuing_country is person.passport_number_digest is None
    old = db.get(AssistedApproval, first.approval_id)
    old_cipher = old.snapshot_ciphertext
    own, guardians = authenticated_approval_facts(old, cipher=s.cipher, lookup=s.lookup)
    assert own["document_type"] == guardians[0]["guardian"]["document_type"] == "hk_mo_permit"
    updated = s.correct_profile(
        who,
        person.id,
        AssistedProfileCorrection(
            full_name="Synthetic corrected",
            phone="13900000001",
            email="intake@example.com",
            expected_version=1,
            reason="核对资料",
            idempotency_key=uuid4(),
            **(fields | {"identity_number": "H8765432102"}),
        ),
    )
    assert updated.person_id == first.person_id
    assert old.snapshot_ciphertext == old_cipher
    assert person.identity_digest == s.lookup.digest_domain("hk-mo-permit-identity", "H87654321")


def test_permit_pending_duplicate_and_correction(db, platform_users):
    from app.personnel_intake.schemas import PendingProfileCorrection

    s, who = approvals(db), actor(platform_users, "admin")
    fields = {
        "kind": "staff",
        "full_name": "Synthetic pending",
        "document_type": "hk_mo_permit",
        "identity_number": "H87654321",
    }
    first = s.direct_create_pending(who, PendingIntakeInput(**fields), uuid4())
    person = db.get(Person, first.person_id)
    assert person.sex is person.date_of_birth is None
    with pytest.raises(PersonnelIntakeError):
        s.direct_create_pending(
            who, PendingIntakeInput(**(fields | {"identity_number": "h8765432102"})), uuid4()
        )
    changed = s.correct_pending_profile(
        who,
        first.person_id,
        PendingProfileCorrection(
            full_name="Synthetic pending corrected",
            document_type="hk_mo_permit",
            identity_number="m8765432101",
            expected_version=1,
            identity_confirmed=True,
            reason="核对证件",
            idempotency_key=uuid4(),
        ),
    )
    assert changed.person_id == first.person_id
    assert person.identity_digest == s.lookup.digest_domain("hk-mo-permit-identity", "M87654321")
    assert person.sex is person.date_of_birth is None


@pytest.mark.parametrize("missing", [False, True])
def test_permit_claim_review_then_real_email_otp_or_missing_facts_refusal(claim_db, missing):
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
        full_name="Synthetic permit applicant",
        identity_number="H87654321",
        document_type="hk_mo_permit",
        document_issuing_country=None,
        email="claim@example.com",
        guardians=[guardian(identity_number="M12345678")],
    )
    initial, initial_sender = activation(claim_db)
    initial.start("claim@example.com", "127.0.9.84")
    assert not initial_sender.messages
    public, sender = claims(claim_db)
    completion = (
        {}
        if missing
        else {
            "phone": "13900000984",
            "sex": "female",
            "date_of_birth": "2015-01-02",
            "guardians": [guardian(identity_number="m1234567802")],
        }
    )
    _, response = verify_new(
        public,
        sender,
        pending_claim_input(
            full_name="Synthetic permit applicant",
            identity_number="h8765432102",
            document_type="hk_mo_permit",
            issuing_country=None,
            completion=completion,
        ),
    )
    claim = claim_db.scalar(select(ProfileClaim))
    assert response.model_dump() == {"status": "waiting_review", "reason": None}
    assert claim.target_person_id == target.person_id
    proof = validate_claim_proof(claim_db, claim, cipher=public.cipher, lookup=public.lookup)
    assert proof.document_type == "hk_mo_permit" and proof.identity_number == "H87654321"
    if missing:
        from app.personnel_intake.claims.errors import ProfileClaimError

        with pytest.raises(ProfileClaimError):
            reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
        assert (
            claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == target.person_id))
            is None
        )
        return
    reviews(claim_db).approve(actor(users, "admin"), claim.id, decision())
    person = claim_db.get(Person, target.person_id)
    assert person.date_of_birth == date(2015, 1, 2) and person.sex == "female"
    edge = claim_db.scalar(
        select(GuardianRelationship).where(GuardianRelationship.student_person_id == person.id)
    )
    approved_guardian = claim_db.get(Person, edge.guardian_person_id)
    assert approved_guardian.sex == "male"
    assert approved_guardian.date_of_birth == date(1980, 1, 1)
    assert approved_guardian.identity_digest == public.lookup.digest_domain(
        "hk-mo-permit-identity", "M12345678"
    )
    activate, token, code, _ = verified_activation(claim_db, person_id=person.id)
    assert activate.finish(token, code, SecretStr("SecurePass1")).completed
    assert (
        claim_db.scalar(select(UserAccount.id).where(UserAccount.person_id == person.id))
        is not None
    )
    assert person.identity_digest == public.lookup.digest_domain(
        "hk-mo-permit-identity", "H87654321"
    )
    assert person.document_issuing_country is person.passport_number_digest is None


@pytest.mark.parametrize("alias", ["h87654321", "H 8765 4321 02", "Ｈ８７６５４３２１０２"])
def test_permit_sensitive_history_reason_variants_are_rejected_without_echo(db, alias):
    from app.housing.errors import HousingServiceError
    from tests.services.test_housing_history_void import (
        CIPHER,
        LOOKUP,
        command,
        counts,
        service,
        setup_history,
    )

    who, _, stay = setup_history(db)
    resident = db.get(Person, stay.person_id)
    resident.document_type = "hk_mo_permit"
    resident.identity_digest = LOOKUP.digest_domain("hk-mo-permit-identity", "H87654321")
    resident.identity_ciphertext = CIPHER.encrypt("H87654321", field_name="identity_number")
    db.commit()
    with pytest.raises(HousingServiceError) as caught:
        service(db).void(who, stay.id, command(reason=f"重复导入 {alias} 已核对"))
    assert caught.value.code == "housing_void_reason_invalid"
    assert alias not in str(caught.value)
    assert counts(db, stay.id) == (0, 0)
