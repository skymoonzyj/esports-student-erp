from __future__ import annotations

from app.registration.models import GuardianRegistrationInvitation

REDACTED_GUARDIAN_INVITATION_DIGEST = "redacted:guardian-invitation:v1"
REDACTED_GUARDIAN_INVITATION_CIPHERTEXT = "redacted:guardian-invitation:v1"
REDACTED_GUARDIAN_INVITATION_MASK = "联系方式已清除"


def clear_guardian_invitation_identifier(
    invitation: GuardianRegistrationInvitation,
) -> None:
    invitation.handoff_token_digest = None
    invitation.invited_identifier_digest = REDACTED_GUARDIAN_INVITATION_DIGEST
    invitation.invited_identifier_ciphertext = (
        REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )


def guardian_invitation_identifier_is_cleared(
    invitation: GuardianRegistrationInvitation,
) -> bool:
    return (
        invitation.invited_identifier_digest
        == REDACTED_GUARDIAN_INVITATION_DIGEST
        and invitation.invited_identifier_ciphertext
        == REDACTED_GUARDIAN_INVITATION_CIPHERTEXT
    )
