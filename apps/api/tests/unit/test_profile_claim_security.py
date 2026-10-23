import pytest


@pytest.mark.parametrize(
    "document_type,number,expected",
    [
        ("passport", "P123", "****"),
        ("passport", "P12345678", "****78"),
        ("cn_id", "110105200001023212", "110***********3212"),
    ],
)
def test_typed_claim_identity_masks_short_passports_without_changing_legacy_cn(
    document_type, number, expected
):
    from app.personnel_intake.claims.queries import mask_identity

    assert mask_identity(number, document_type=document_type) == expected


def test_corrupt_email_review_is_privately_unapprovable_instead_of_crashing():
    from types import SimpleNamespace
    from uuid import uuid4

    from app.identity.models import Person
    from app.personnel_intake.claims.models import ProfileClaim
    from app.personnel_intake.claims.proof import ClaimSubmission
    from app.personnel_intake.claims.queries import ClaimQueries
    from app.personnel_intake.models import AssistedPersonProfile
    from app.security.encryption import EnvelopeCipher, LookupDigest

    person = Person(
        id=uuid4(),
        full_name="本人",
        identity_digest="d" * 64,
        intake_mode="pending",
        email_ciphertext="corrupt-envelope",
        deleted_at=None,
    )
    profile = AssistedPersonProfile(
        person_id=person.id, current_approval_id=uuid4(), activation_requirement="reviewed_claim"
    )
    row = ProfileClaim(
        target_person_id=person.id,
        target_approval_id=profile.current_approval_id,
        identity_digest=person.identity_digest,
        private_match_reason=None,
    )
    # This reader's dependency has only two reads before the malformed envelope;
    # retain real ORM records and real crypto, replacing only database transport.
    db = SimpleNamespace(
        get=lambda model, identifier, **kwargs: person if model is Person else profile
    )
    query = ClaimQueries(
        db, cipher=EnvelopeCipher(bytes(range(32))), lookup=LookupDigest(bytes(range(32)))
    )
    assert (
        query._approval_reason(row, ClaimSubmission("本人", "P851", "claim@example.com"))
        == "approval_evidence_invalid"
    )


@pytest.mark.parametrize(
    "field", ["person_id", "kind", "staff_type", "roles", "coach", "team", "assignment"]
)
def test_completion_rejects_authority_fields(field):
    from pydantic import ValidationError

    from app.personnel_intake.claims.schemas import ClaimStartInput

    base = {
        "full_name": "本人输入",
        "identity_number": "110105200001023212",
        "email": "synthetic@example.com",
    }
    assert ClaimStartInput.model_validate({**base, "completion": {}}).completion is not None
    with pytest.raises(ValidationError) as caught:
        ClaimStartInput.model_validate(
            {
                "full_name": "本人输入",
                "identity_number": "110105200001023212",
                "email": "synthetic@example.com",
                "completion": {field: "untrusted"},
            }
        )
    assert caught.value.errors()[0]["loc"] == ("completion", field)


def test_completion_merges_only_missing_facts_without_authority_or_identity_changes():
    from datetime import date

    from app.personnel_intake.claims.completion import merge_completion_payload
    from app.personnel_intake.claims.schemas import ClaimCompletionFields
    from app.personnel_intake.schemas import PendingIntakePayloadView

    old = PendingIntakePayloadView(
        kind="staff",
        full_name="旧姓名",
        identity_number=" P123 ",
        document_type="passport",
        document_issuing_country="US",
        sex=None,
        date_of_birth=None,
        phone=None,
        email="reserved@example.com",
        guardians=[],
        coach_person_ids=[],
        team_id=None,
        team_allocation_status="not_required",
    )
    value = merge_completion_payload(
        old,
        ClaimCompletionFields(phone="+1 202", sex="female", date_of_birth=date(2000, 1, 2)),
        verified_email="reserved@example.com",
        today=date(2026, 10, 21),
    )
    assert (value.phone, value.sex, value.date_of_birth) == ("+1 202", "female", date(2000, 1, 2))
    assert (value.kind, value.full_name, value.identity_number, value.document_issuing_country) == (
        "staff",
        "旧姓名",
        " P123 ",
        "US",
    )
    assert old.phone is None
    from app.personnel_intake.claims.errors import ProfileClaimError

    with pytest.raises(ProfileClaimError):
        merge_completion_payload(
            value,
            ClaimCompletionFields(phone="different"),
            verified_email="reserved@example.com",
            today=date(2026, 10, 21),
        )
    with pytest.raises(ProfileClaimError):
        merge_completion_payload(
            old,
            ClaimCompletionFields(),
            verified_email="changed@example.com",
            today=date(2026, 10, 21),
        )


def test_completion_v2_proof_preserves_material_and_authenticates_typed_identity():
    from app.personnel_intake.claims.proof import decode_claim_submission, submission_from_input
    from app.personnel_intake.claims.schemas import ClaimStartInput
    from app.registration.verification_service import VerificationInvalidOrExpired
    from app.security.encryption import EnvelopeCipher, LookupDigest

    source = ClaimStartInput.model_validate(
        {
            "full_name": "护照本人",
            "identity_number": " p123a ",
            "document_type": "passport",
            "issuing_country": "us",
            "email": "Synthetic@EXAMPLE.COM",
            "completion": {
                "phone": "+1 202 555 0100",
                "sex": "female",
                "date_of_birth": "2000-01-02",
            },
        }
    )
    value = submission_from_input(source)
    assert value.version == 2
    assert value.identity_number == " p123a "
    assert value.issuing_country == "US"
    assert value.completion.phone.get_secret_value() == "+1 202 555 0100"
    cipher, lookup = EnvelopeCipher(bytes(range(32))), LookupDigest(bytes(range(32)))
    raw = value.canonical()
    kwargs = {
        "ciphertext": cipher.encrypt(raw, field_name="profile_claim_submission"),
        "payload_digest": lookup.digest_domain("profile-claim-submission-v2", raw),
        "identity_digest": value.identity_keys(lookup).identity_digest,
        "email_digest": lookup.digest_domain("registration-verification-email", value.email),
        "submission_version": 2,
        "cipher": cipher,
        "lookup": lookup,
    }
    decoded = decode_claim_submission(**kwargs)
    assert decoded.canonical() == raw
    assert "+1 202" not in repr(decoded) and "p123a" not in repr(decoded)
    kwargs["identity_digest"] = lookup.digest("P123A")
    with pytest.raises(VerificationInvalidOrExpired):
        decode_claim_submission(**kwargs)


def test_completion_cannot_supply_contradicting_passport_country():
    from pydantic import ValidationError

    from app.personnel_intake.claims.schemas import ClaimStartInput

    with pytest.raises(ValidationError):
        ClaimStartInput.model_validate(
            {
                "full_name": "本人",
                "identity_number": "P123",
                "document_type": "passport",
                "issuing_country": "US",
                "email": "a@example.com",
                "completion": {"issuing_country": "GB"},
            }
        )


@pytest.mark.parametrize("path", ["verify", "status"])
@pytest.mark.parametrize(
    "cookie", ["", "erp_profile_claim=a; erp_profile_claim=b", "erp_profile_activation=other"]
)
def test_profile_claim_rejects_missing_duplicate_or_wrong_purpose_cookie(client, path, cookie):
    response = client.post(
        f"/api/v1/auth/profile-claims/{path}",
        json={"code": "123456"} if path == "verify" else {},
        headers={"cookie": cookie},
    )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store, private"


def test_profile_claim_email_uses_explicit_claim_context(monkeypatch):
    from uuid import uuid4

    from app.registration.delivery import ConfiguredSmtpVerificationSender
    from tests.services.test_mail_templates import _FakeSmtp, _template

    _FakeSmtp.sent = None
    monkeypatch.setattr("app.registration.delivery.smtplib.SMTP_SSL", _FakeSmtp)
    sender = ConfiguredSmtpVerificationSender(
        host="smtp.example.com",
        port=465,
        username="admin@example.com",
        password="authorization-code",
        sender="admin@example.com",
        from_name="竞学 ERP",
        reply_to="",
        tls_mode="implicit_tls",
        template=_template(),
    )
    sender.send_code(
        challenge_id=uuid4(),
        destination="isolated@example.com",
        code="123456",
        purpose="profile_claim",
    )
    message = _FakeSmtp.sent
    assert "本人档案认领邮箱验证" in str(message["Subject"])
    assert "123456" in message.get_body(preferencelist=("plain",)).get_content()


@pytest.mark.parametrize(
    "change",
    [
        {"payload_digest": "0" * 64},
        {"identity_digest": "0" * 64},
        {"email_digest": "0" * 64},
        {"submission_version": 2},
    ],
)
def test_submission_decoder_authenticates_all_input_domains(change):
    from app.personnel_intake.claims.proof import decode_claim_submission
    from app.registration.verification_service import VerificationInvalidOrExpired
    from app.security.encryption import EnvelopeCipher, LookupDigest

    raw = '{"email":"synthetic@example.com","full_name":"本人输入","identity_number":"110105200001023212","version":1}'
    cipher, lookup = EnvelopeCipher(bytes(range(32))), LookupDigest(bytes(range(32)))
    kwargs = {
        "ciphertext": cipher.encrypt(raw, field_name="profile_claim_submission"),
        "payload_digest": lookup.digest_domain("profile-claim-submission-v1", raw),
        "identity_digest": lookup.digest("110105200001023212"),
        "email_digest": lookup.digest_domain(
            "registration-verification-email", "synthetic@example.com"
        ),
        "cipher": cipher,
        "lookup": lookup,
    }
    # The unmodified independently written canonical fixture is valid.
    valid = decode_claim_submission(**kwargs)
    assert valid.full_name == "本人输入" and "synthetic@example.com" not in repr(valid)
    kwargs.update(change)
    with pytest.raises(VerificationInvalidOrExpired):
        decode_claim_submission(**kwargs)
