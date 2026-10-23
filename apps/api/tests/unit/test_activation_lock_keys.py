from sqlalchemy.orm import Session

from app.registration.delivery import CapturingVerificationSender
from app.registration.verification_service import RegistrationVerificationService
from app.security.encryption import LookupDigest


def test_send_lock_key_projection_preserves_email_and_canonical_source_domains():
    service = RegistrationVerificationService(
        Session(),
        lookup=LookupDigest(bytes(range(32))),
        verification_pepper=bytes(reversed(range(32))),
        sender=CapturingVerificationSender(messages=[]),
        app_env="test",
    )
    first = service.issue_limit_lock_keys(
        identifier_kind="email",
        identifier=" USER@example.com ",
        request_source="2001:0db8:0:0:0:0:0:1",
    )
    second = service.issue_limit_lock_keys(
        identifier_kind="email", identifier="user@example.com", request_source="2001:db8::1"
    )
    assert first == second
    assert len(first) == 2 and first == sorted(set(first))
    different_email = service.issue_limit_lock_keys(
        identifier_kind="email", identifier="other@example.com", request_source="2001:db8::1"
    )
    different_source = service.issue_limit_lock_keys(
        identifier_kind="email", identifier="user@example.com", request_source="2001:db8::2"
    )
    assert len(set(first) & set(different_email)) == 1
    assert len(set(first) & set(different_source)) == 1
    assert set(first) & set(different_email) != set(first) & set(different_source)
