from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from app.auth.identifiers import NormalizedLoginIdentifier, login_identifier_digest
from app.auth.models import AccountLoginIdentifier, UserAccount
from app.identity.models import (
    GuardianProfile,
    Person,
    StaffProfile,
    StudentProfile,
    VisitorProfile,
)
from app.identity.schemas import PersonBaseInput
from app.identity.service import IdentityService
from app.registration.identifiers import (
    normalize_registration_identifier,
    registration_identifier_digest,
)
from app.registration.matching_service import (
    RegistrationApplicationInvalid,
    RegistrationMatchingService,
)
from app.registration.models import RegistrationApplication
from app.security.encryption import EnvelopeCipher, LookupDigest

NOW = datetime(2026, 8, 15, 8, tzinfo=UTC)


def make_cn_id(birth: str, sequence: int) -> str:
    body = f"110105{birth}{sequence:03d}"
    weights = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
    checks = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")
    return body + checks[sum(int(a) * b for a, b in zip(body, weights)) % 11]


def _crypto() -> tuple[EnvelopeCipher, LookupDigest]:
    return EnvelopeCipher(bytes(range(32))), LookupDigest(bytes(reversed(range(32))))


def _identity_service(db: Session) -> IdentityService:
    cipher, lookup = _crypto()
    return IdentityService(db, cipher=cipher, lookup=lookup)


def _person(
    db: Session,
    *,
    full_name: str,
    identity_number: str,
    phone: str | None = None,
    email: str | None = None,
) -> Person:
    service = _identity_service(db)
    summary = service.create_base_person(
        PersonBaseInput(
            full_name=full_name,
            identity_number=identity_number,
            sex="male",
            date_of_birth=date.fromisoformat(
                f"{identity_number[6:10]}-{identity_number[10:12]}-{identity_number[12:14]}"
            ),
            phone=phone,
            email=email,
        )
    )
    person = db.get(Person, summary.id)
    assert person is not None
    return person


def _application(
    db: Session,
    *,
    full_name: str,
    identity_number: str,
    identifier_kind: str,
    identifier: str,
    purpose: str = "student",
    requested_staff_type: str | None = None,
    staff_application_reason: str | None = None,
    visit_reason: str | None = None,
) -> RegistrationApplication:
    cipher, lookup = _crypto()
    normalized_identifier = normalize_registration_identifier(  # type: ignore[arg-type]
        identifier_kind,
        identifier,
    )
    application = RegistrationApplication(
        primary_purpose=purpose,
        state="ready_to_finalize",
        verified_identifier_kind=normalized_identifier.kind,
        verified_identifier_digest=registration_identifier_digest(
            lookup,
            normalized_identifier,
        ),
        verified_identifier_ciphertext=cipher.encrypt(
            normalized_identifier.value,
            field_name="registration_identifier",
        ),
        full_name=full_name,
        identity_ciphertext=cipher.encrypt(
            identity_number,
            field_name="registration_identity",
        ),
        identity_digest=lookup.digest(identity_number),
        sex="male",
        date_of_birth=date.fromisoformat(
            f"{identity_number[6:10]}-{identity_number[10:12]}-{identity_number[12:14]}"
        ),
        requested_staff_type=requested_staff_type,
        staff_application_reason=staff_application_reason,
        visit_reason=visit_reason,
        expires_at=NOW + timedelta(days=30),
    )
    db.add(application)
    db.flush()
    return application


def _matching_service(db: Session) -> RegistrationMatchingService:
    cipher, lookup = _crypto()
    return RegistrationMatchingService(db, cipher=cipher, lookup=lookup)


def _add_account(db: Session, person: Person, phone: str) -> UserAccount:
    _cipher, lookup = _crypto()
    account = UserAccount(
        person_id=person.id,
        password_hash="test-password-hash",
        status="active",
        force_password_change=False,
        failed_login_count=0,
    )
    db.add(account)
    db.flush()
    db.add(
        AccountLoginIdentifier(
            account_id=account.id,
            kind="phone",
            identifier_digest=login_identifier_digest(
                lookup,
                NormalizedLoginIdentifier("phone", phone),
            ),
            verified_at=NOW,
            verification_source="test",
            is_primary=True,
        )
    )
    db.flush()
    return account


def _add_explicit_profiles(
    db: Session,
    person: Person,
    profile_kinds: frozenset[str],
    *,
    visit_reason: str,
) -> None:
    if "student" in profile_kinds:
        db.add(
            StudentProfile(
                person_id=person.id,
                student_number=f"REG-{person.id.hex[:20]}",
                status="active",
            )
        )
    if "guardian" in profile_kinds:
        db.add(GuardianProfile(person_id=person.id))
    if "staff" in profile_kinds:
        db.add(StaffProfile(person_id=person.id, staff_type="coach"))
    if "visitor" in profile_kinds:
        db.add(
            VisitorProfile(
                person_id=person.id,
                linked_student_id=None,
                visit_reason=visit_reason,
            )
        )
    db.flush()


PROFILE_KINDS = ("student", "guardian", "staff", "visitor")
EXPLICIT_PROFILE_SETS = [
    frozenset(kind for index, kind in enumerate(PROFILE_KINDS) if mask & (1 << index))
    for mask in range(1 << len(PROFILE_KINDS))
]


def test_no_identity_or_contact_match_is_new_person(db: Session) -> None:
    identity_number = make_cn_id("19900102", 501)
    application = _application(
        db,
        full_name="全新学员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005001",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "new_person"
    assert result.person_id is None
    assert result.conflict_code is None


@pytest.mark.parametrize(
    "application_kwargs",
    [
        {
            "purpose": "staff_applicant",
            "requested_staff_type": "coach",
            "staff_application_reason": "联系 coach@example.com",
        },
        {
            "purpose": "visitor",
            "visit_reason": "联系手机号 13800005991",
        },
    ],
)
def test_persisted_business_text_with_plaintext_pii_is_rejected_before_matching(
    db: Session,
    application_kwargs: dict[str, str],
) -> None:
    application = _application(
        db,
        full_name="敏感文本申请人",
        identity_number=make_cn_id("19900102", 591),
        identifier_kind="phone",
        identifier="13800005991",
        **application_kwargs,
    )

    with pytest.raises(RegistrationApplicationInvalid):
        _matching_service(db).application_snapshot(application)


def test_exact_unowned_person_is_claim_existing(db: Session) -> None:
    identity_number = make_cn_id("19900102", 503)
    person = _person(
        db,
        full_name="既有学员",
        identity_number=identity_number,
        phone="13800005003",
    )
    application = _application(
        db,
        full_name="既有学员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005003",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "claim_existing"
    assert result.person_id == person.id
    assert result.conflict_code is None


@pytest.mark.parametrize(
    "existing_profiles",
    EXPLICIT_PROFILE_SETS,
    ids=lambda profiles: "+".join(sorted(profiles)) or "base-person",
)
@pytest.mark.parametrize(
    "purpose",
    ["student", "guardian", "staff_applicant", "visitor"],
)
def test_existing_unowned_person_requires_exclusive_compatible_purpose_profile(
    db: Session,
    purpose: str,
    existing_profiles: frozenset[str],
) -> None:
    identity_number = make_cn_id("19900102", 523)
    person = _person(
        db,
        full_name="既有用途人员",
        identity_number=identity_number,
        phone="13800005023",
    )
    visit_reason = "探望营期学员"
    _add_explicit_profiles(
        db,
        person,
        existing_profiles,
        visit_reason=visit_reason,
    )
    application = _application(
        db,
        full_name="既有用途人员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005023",
        purpose=purpose,
        requested_staff_type="coach" if purpose == "staff_applicant" else None,
        staff_application_reason=("申请成为教练" if purpose == "staff_applicant" else None),
        visit_reason=visit_reason if purpose == "visitor" else None,
    )

    result = _matching_service(db).classify(application.id)

    matching_profile = {
        "student": "student",
        "guardian": "guardian",
        "staff_applicant": None,
        "visitor": "visitor",
    }[purpose]
    is_compatible = not existing_profiles or (
        matching_profile is not None and existing_profiles == frozenset({matching_profile})
    )
    if is_compatible:
        assert result.kind == "claim_existing"
        assert result.person_id == person.id
        assert result.conflict_code is None
    else:
        assert result.kind == "conflict"
        assert result.person_id is None
        assert result.conflict_code == "purpose_conflict"


def test_existing_visitor_requires_matching_visit_reason(db: Session) -> None:
    identity_number = make_cn_id("19900102", 525)
    person = _person(
        db,
        full_name="既有访客",
        identity_number=identity_number,
        phone="13800005025",
    )
    _add_explicit_profiles(
        db,
        person,
        frozenset({"visitor"}),
        visit_reason="参加开放日",
    )
    application = _application(
        db,
        full_name="既有访客",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005025",
        purpose="visitor",
        visit_reason="探望营期学员",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "conflict"
    assert result.person_id is None
    assert result.conflict_code == "purpose_conflict"


def test_exact_person_with_formal_account_is_existing_account(db: Session) -> None:
    identity_number = make_cn_id("19900102", 505)
    person = _person(
        db,
        full_name="已有账号学员",
        identity_number=identity_number,
        phone="13800005005",
    )
    _add_account(db, person, "13800005005")
    application = _application(
        db,
        full_name="已有账号学员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005005",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "existing_account"
    assert result.person_id == person.id
    assert result.conflict_code is None


def test_existing_account_does_not_disclose_incompatible_staff_profile(
    db: Session,
) -> None:
    identity_number = make_cn_id("19900102", 517)
    person = _person(
        db,
        full_name="已有工作人员账号",
        identity_number=identity_number,
        phone="13800005019",
    )
    db.add(StaffProfile(person_id=person.id, staff_type="coach"))
    _add_account(db, person, "13800005019")
    db.flush()
    application = _application(
        db,
        full_name="已有工作人员账号",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005019",
        purpose="staff_applicant",
        requested_staff_type="coach",
        staff_application_reason="重新申请教练身份",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "existing_account"
    assert result.person_id == person.id
    assert result.conflict_code is None


def test_identity_and_contact_on_different_people_is_conflict(db: Session) -> None:
    identity_number = make_cn_id("19900102", 507)
    identity_person = _person(
        db,
        full_name="证件命中人员",
        identity_number=identity_number,
        phone="13800005007",
    )
    contact_person = _person(
        db,
        full_name="联系方式命中人员",
        identity_number=make_cn_id("19900102", 509),
        phone="13800005009",
    )
    application = _application(
        db,
        full_name="证件命中人员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005009",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "conflict"
    assert result.person_id is None
    assert result.conflict_code == "split_identifier_match"
    assert identity_person.id != contact_person.id


def test_deleted_identity_match_is_never_claimed(db: Session) -> None:
    identity_number = make_cn_id("19900102", 511)
    person = _person(
        db,
        full_name="已删除人员",
        identity_number=identity_number,
        phone="13800005011",
    )
    person.deleted_at = NOW
    db.flush()
    application = _application(
        db,
        full_name="已删除人员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005011",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "conflict"
    assert result.person_id is None
    assert result.conflict_code == "deleted_person_match"


def test_identity_match_with_different_name_is_conflict(db: Session) -> None:
    identity_number = make_cn_id("19900102", 513)
    _person(
        db,
        full_name="原档案姓名",
        identity_number=identity_number,
        phone="13800005013",
    )
    application = _application(
        db,
        full_name="不同姓名",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005013",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "conflict"
    assert result.person_id is None
    assert result.conflict_code == "identity_profile_mismatch"


def test_incomplete_existing_contact_pair_fails_closed(db: Session) -> None:
    identity_number = make_cn_id("19900102", 515)
    person = _person(
        db,
        full_name="联系方式损坏人员",
        identity_number=identity_number,
        phone="13800005015",
    )
    person.phone_digest = None
    db.flush()
    application = _application(
        db,
        full_name="联系方式损坏人员",
        identity_number=identity_number,
        identifier_kind="phone",
        identifier="13800005017",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "conflict"
    assert result.person_id is None
    assert result.conflict_code == "invalid_person_contact"


def test_exact_unowned_person_with_empty_email_is_claim_existing(db: Session) -> None:
    identity_number = make_cn_id("19900102", 519)
    person = _person(
        db,
        full_name="邮箱认领人员",
        identity_number=identity_number,
        email=None,
    )
    application = _application(
        db,
        full_name="邮箱认领人员",
        identity_number=identity_number,
        identifier_kind="email",
        identifier="claim519@example.com",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "claim_existing"
    assert result.person_id == person.id
    assert result.conflict_code is None


def test_corrupted_existing_email_digest_fails_closed(db: Session) -> None:
    identity_number = make_cn_id("19900102", 521)
    person = _person(
        db,
        full_name="邮箱损坏人员",
        identity_number=identity_number,
        email="broken521@example.com",
    )
    person.email_digest = "f" * 64
    db.flush()
    application = _application(
        db,
        full_name="邮箱损坏人员",
        identity_number=identity_number,
        identifier_kind="email",
        identifier="broken521@example.com",
    )

    result = _matching_service(db).classify(application.id)

    assert result.kind == "conflict"
    assert result.person_id is None
    assert result.conflict_code == "invalid_person_contact"
