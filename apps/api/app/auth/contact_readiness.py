from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, SQLColumnExpression, select
from sqlalchemy.orm import Session

from app.auth.models import AccountLoginIdentifier, UserAccount


class VerifiedPhoneRequired(ValueError):
    code = "verified_phone_required"


class VerifiedEmailRequired(ValueError):
    code = "verified_email_required"


class VerifiedContactRequired(VerifiedPhoneRequired):
    code = "verified_contact_required"


def verified_identifier_statement(
    person_id: UUID | SQLColumnExpression[UUID],
    *,
    kind: str,
) -> Select[tuple[AccountLoginIdentifier]]:
    return (
        select(AccountLoginIdentifier)
        .join(
            UserAccount,
            UserAccount.id == AccountLoginIdentifier.account_id,
        )
        .where(
            UserAccount.person_id == person_id,
            UserAccount.status == "active",
            AccountLoginIdentifier.kind == kind,
        )
    )


def verified_phone_identifier_statement(
    person_id: UUID | SQLColumnExpression[UUID],
) -> Select[tuple[AccountLoginIdentifier]]:
    return verified_identifier_statement(person_id, kind="phone")


def verified_email_identifier_statement(
    person_id: UUID | SQLColumnExpression[UUID],
) -> Select[tuple[AccountLoginIdentifier]]:
    return verified_identifier_statement(person_id, kind="email")


class VerifiedContactPolicy:
    def __init__(self, db: Session) -> None:
        self.db = db

    def require_verified_contact_for_person(self, person_id: UUID) -> AccountLoginIdentifier:
        identifier = self.db.scalar(
            select(AccountLoginIdentifier)
            .join(UserAccount, UserAccount.id == AccountLoginIdentifier.account_id)
            .where(
                UserAccount.person_id == person_id,
                UserAccount.status == "active",
                AccountLoginIdentifier.kind.in_(("email", "phone")),
                AccountLoginIdentifier.verified_at.is_not(None),
            )
            .order_by(AccountLoginIdentifier.kind, AccountLoginIdentifier.id)
            .limit(1)
            .with_for_update(of=AccountLoginIdentifier)
            .execution_options(populate_existing=True)
        )
        if identifier is None:
            raise VerifiedContactRequired("verified email or phone is required")
        return identifier

    def require_phone_for_person(self, person_id: UUID) -> AccountLoginIdentifier:
        identifier = self.db.scalar(
            verified_phone_identifier_statement(person_id)
            .with_for_update(of=AccountLoginIdentifier)
            .execution_options(populate_existing=True)
        )
        if identifier is None:
            raise VerifiedPhoneRequired("verified phone is required")
        return identifier

    def require_email_for_person(self, person_id: UUID) -> AccountLoginIdentifier:
        identifier = self.db.scalar(
            verified_email_identifier_statement(person_id)
            .with_for_update(of=AccountLoginIdentifier)
            .execution_options(populate_existing=True)
        )
        if identifier is None:
            raise VerifiedEmailRequired("verified email is required")
        return identifier
