from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.audit.service import AuditService
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.transactions import write_transaction
from app.evaluations.config import EvaluationBridgeConfig
from app.evaluations.errors import (
    EvaluationAccessDenied,
    EvaluationBridgeRequestConflict,
    EvaluationBridgeRequestExpired,
    EvaluationBridgeRequestNotFound,
    EvaluationDelegatedSessionInvalid,
    EvaluationTicketInvalid,
)
from app.evaluations.models import EvaluationBridgeRequest, EvaluationBridgeTicket
from app.evaluations.schemas import (
    EvaluationLoginRequestCreate,
    EvaluationLoginRequestCreated,
    EvaluationTicketIssued,
    EvaluationToolSessionContext,
)
from app.identity.models import Person
from app.rbac.service import RbacService
from app.security.encryption import EnvelopeCipher

_STATE_FIELD = "evaluation_bridge.state"
_TICKET_FIELD = "evaluation_bridge.ticket"


class EvaluationBridgeService:
    def __init__(
        self,
        db: Session,
        *,
        cipher: EnvelopeCipher,
        token_pepper: bytes,
        config: EvaluationBridgeConfig,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if len(token_pepper) != 32:
            raise ValueError("evaluation token pepper must be 32 bytes")
        self.db = db
        self.cipher = cipher
        self.token_pepper = token_pepper
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))

    def _now(self) -> datetime:
        value = self.clock()
        if value.utcoffset() is None:
            raise ValueError("evaluation bridge clock must be timezone-aware")
        return value

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)

    def _token_digest(self, value: str) -> str:
        material = b"evaluation-ticket-v1\x00" + value.encode()
        return hmac.new(self.token_pepper, material, hashlib.sha256).hexdigest()

    def _has_access(self, account_id: UUID) -> bool:
        return (
            RbacService(self.db)
            .decision_context(account_id)
            .decide("evaluation_center.access", {"global"})
            .allowed
        )

    def _session_context_entities(
        self,
        session_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[AuthSession, UserAccount, Person] | None:
        query = (
            select(AuthSession, UserAccount, Person)
            .join(UserAccount, UserAccount.id == AuthSession.user_id)
            .join(Person, Person.id == UserAccount.person_id)
            .where(AuthSession.id == session_id)
            .execution_options(populate_existing=True)
        )
        if lock:
            query = query.with_for_update(of=AuthSession)
        row = self.db.execute(query).one_or_none()
        if row is None:
            return None
        session, account, person = row
        return session, account, person

    def _validate_live_session_context(
        self,
        entities: tuple[AuthSession, UserAccount, Person] | None,
        *,
        now: datetime,
    ) -> tuple[AuthSession, UserAccount, Person]:
        if entities is None:
            raise EvaluationDelegatedSessionInvalid("delegated session is invalid")
        session, account, person = entities
        if (
            session.revoked_at is not None
            or session.expires_at <= now
            or account.status != "active"
            or account.force_password_change
            or person.deleted_at is not None
        ):
            raise EvaluationDelegatedSessionInvalid("delegated session is invalid")
        if not self._has_access(account.id):
            raise EvaluationAccessDenied("evaluation access is denied")
        return session, account, person

    def _live_session_context(
        self,
        session_id: UUID,
        *,
        lock: bool = False,
    ) -> tuple[AuthSession, UserAccount, Person]:
        entities = self._session_context_entities(session_id, lock=lock)
        return self._validate_live_session_context(entities, now=self._now())

    @staticmethod
    def _context(
        session: AuthSession,
        account: UserAccount,
        person: Person,
    ) -> EvaluationToolSessionContext:
        return EvaluationToolSessionContext(
            account_id=account.id,
            person_id=person.id,
            display_name=person.full_name,
            session_id=session.id,
            expires_at=session.expires_at,
        )

    def create_login_request(
        self,
        input: EvaluationLoginRequestCreate,
    ) -> EvaluationLoginRequestCreated:
        now = self._now()
        with write_transaction(self.db):
            stale_ids = (
                select(EvaluationBridgeRequest.id)
                .where(
                    EvaluationBridgeRequest.expires_at < now - timedelta(hours=24)
                )
                .order_by(
                    EvaluationBridgeRequest.expires_at,
                    EvaluationBridgeRequest.id,
                )
                .limit(500)
            )
            self.db.execute(
                delete(EvaluationBridgeRequest).where(
                    EvaluationBridgeRequest.id.in_(stale_ids)
                )
            )
            row = EvaluationBridgeRequest(
                client_id=input.client_id,
                mode=input.mode,
                state_ciphertext=self.cipher.encrypt(
                    input.state,
                    field_name=_STATE_FIELD,
                ),
                expires_at=now
                + timedelta(seconds=self.config.request_ttl_seconds),
                created_at=now,
                updated_at=now,
            )
            self.db.add(row)
            self.db.flush()
            result = EvaluationLoginRequestCreated(
                request_id=row.id,
                authorize_url=self.config.authorize_url(row.id),
                expires_at=row.expires_at,
            )
        return result

    def issue_ticket(
        self,
        *,
        request_id: UUID,
        context: AuthContext,
        request_id_for_audit: str,
    ) -> EvaluationTicketIssued:
        with write_transaction(self.db):
            request_row = self.db.scalar(
                select(EvaluationBridgeRequest)
                .where(EvaluationBridgeRequest.id == request_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if request_row is None:
                raise EvaluationBridgeRequestNotFound("bridge request was not found")
            existing = self.db.scalar(
                select(EvaluationBridgeTicket)
                .where(EvaluationBridgeTicket.login_request_id == request_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            live_session, live_account, _ = self._live_session_context(
                context.session.id,
                lock=True,
            )
            now = self._now()
            if request_row.expires_at <= now:
                raise EvaluationBridgeRequestExpired("bridge request expired")
            if (
                live_account.id != context.account.id
                or live_session.expires_at <= now
                or live_session.revoked_at is not None
            ):
                raise EvaluationDelegatedSessionInvalid("delegated session is invalid")
            if existing is not None and (
                existing.account_id != live_account.id
                or existing.auth_session_id != live_session.id
            ):
                raise EvaluationBridgeRequestConflict(
                    "bridge request already authorized"
                )
            if existing is not None and existing.consumed_at is not None:
                raise EvaluationBridgeRequestConflict("bridge request already consumed")
            if (
                existing is not None
                and existing.expires_at > now
                and existing.token_ciphertext is not None
            ):
                ticket = self.cipher.decrypt(
                    existing.token_ciphertext,
                    field_name=_TICKET_FIELD,
                )
                ticket_row = existing
            else:
                ticket = self._new_token()
                ticket_expires_at = min(
                    request_row.expires_at,
                    now + timedelta(seconds=self.config.ticket_ttl_seconds),
                )
                if existing is None:
                    ticket_row = EvaluationBridgeTicket(
                        login_request_id=request_id,
                        auth_session_id=live_session.id,
                        account_id=live_account.id,
                        token_digest=self._token_digest(ticket),
                        token_ciphertext=self.cipher.encrypt(
                            ticket,
                            field_name=_TICKET_FIELD,
                        ),
                        expires_at=ticket_expires_at,
                        created_at=now,
                        updated_at=now,
                    )
                    self.db.add(ticket_row)
                else:
                    ticket_row = existing
                    ticket_row.token_digest = self._token_digest(ticket)
                    ticket_row.token_ciphertext = self.cipher.encrypt(
                        ticket,
                        field_name=_TICKET_FIELD,
                    )
                    ticket_row.expires_at = ticket_expires_at
                    ticket_row.updated_at = now
                    flag_modified(ticket_row, "updated_at")
                self.db.flush()
            request_row.authorized_at = now
            request_row.updated_at = now
            flag_modified(request_row, "updated_at")
            AuditService(self.db).append(
                actor_user_id=live_account.id,
                action="evaluation.bridge.ticket_issued",
                resource_type="evaluation_bridge_request",
                resource_id=request_row.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id_for_audit,
                metadata={
                    "mode": request_row.mode,
                    "client_id": request_row.client_id,
                },
            )
            result = EvaluationTicketIssued(
                callback_url=self.config.callback_url(request_row.mode),
                ticket=ticket,
                state=self.cipher.decrypt(
                    request_row.state_ciphertext,
                    field_name=_STATE_FIELD,
                ),
                expires_at=ticket_row.expires_at,
            )
        return result

    def exchange_ticket(
        self,
        ticket: str,
        *,
        request_id_for_audit: str,
    ) -> EvaluationToolSessionContext:
        digest = self._token_digest(ticket)
        with write_transaction(self.db):
            ticket_id = self.db.scalar(
                select(EvaluationBridgeTicket.id).where(
                    EvaluationBridgeTicket.token_digest == digest
                )
            )
            if ticket_id is None:
                raise EvaluationTicketInvalid("bridge ticket is invalid")
            ticket_row = self.db.scalar(
                select(EvaluationBridgeTicket)
                .where(EvaluationBridgeTicket.id == ticket_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if ticket_row is None or not hmac.compare_digest(
                ticket_row.token_digest,
                digest,
            ):
                raise EvaluationTicketInvalid("bridge ticket is invalid")
            entities = self._session_context_entities(
                ticket_row.auth_session_id,
                lock=True,
            )
            now = self._now()
            if ticket_row.expires_at <= now or ticket_row.consumed_at is not None:
                raise EvaluationTicketInvalid("bridge ticket is invalid")
            session, account, person = self._validate_live_session_context(
                entities,
                now=now,
            )
            if (
                account.id != ticket_row.account_id
                or session.expires_at <= now
                or session.revoked_at is not None
            ):
                raise EvaluationTicketInvalid("bridge ticket is invalid")
            ticket_row.consumed_at = now
            ticket_row.token_ciphertext = None
            ticket_row.updated_at = now
            flag_modified(ticket_row, "updated_at")
            AuditService(self.db).append(
                actor_user_id=account.id,
                action="evaluation.bridge.ticket_exchanged",
                resource_type="evaluation_bridge_ticket",
                resource_id=ticket_row.id,
                purpose=None,
                outcome="allowed",
                request_id=request_id_for_audit,
                metadata={"login_request_id": str(ticket_row.login_request_id)},
            )
            result = self._context(session, account, person)
        return result

    def session_context(self, session_id: UUID) -> EvaluationToolSessionContext:
        session, account, person = self._live_session_context(session_id)
        return self._context(session, account, person)
