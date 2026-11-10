import base64
import re
import secrets
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.models import AuthRateLimit, AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction, write_transaction
from app.identity.models import Person
from app.reactionx.access import ReactionxAccess
from app.reactionx.models import ReactionxConnector, ReactionxPairing

ERP_ORIGIN = "https://skymoonzyj.l.cd:18080"
TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


def rejected(status: int, code: str = "pairing_rejected") -> ProblemError:
    return ProblemError(status=status, code=code, detail="ReactionX 配对或授权校验失败")


class PairingView(BaseModel):
    pairing_id: UUID
    challenge: str
    pair_uri: str
    expires_at: datetime
    verification_phrase: str


class ClaimView(BaseModel):
    pairing_id: UUID
    connector_id: UUID
    verification_phrase: str
    expires_at: datetime
    status: str
    browser_confirmed: bool
    device_confirmed: bool
    actor_account_id: UUID
    actor_name: str
    origin: str


class PairingService:
    def __init__(self, db: Session, *, origin: str = ERP_ORIGIN) -> None:
        self.db = db
        self.origin = origin

    def _rate(self, namespace: str, identity: str, maximum: int) -> None:
        """Commit attempt counters before validation, including rejected claims."""
        now = datetime.now(UTC)
        key = sha256(f"reactionx:{namespace}:{identity}".encode()).hexdigest()
        with write_transaction(self.db):
            self.db.execute(
                insert(AuthRateLimit)
                .values(key_digest=key, attempts=0, window_started_at=now, updated_at=now)
                .on_conflict_do_nothing()
            )
            row = self.db.scalar(
                select(AuthRateLimit)
                .where(AuthRateLimit.key_digest == key)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            assert row is not None
            if row.window_started_at + timedelta(minutes=1) <= now:
                row.attempts, row.window_started_at = 0, now
            row.attempts += 1
            row.updated_at = now
            denied = row.attempts > maximum
            retry = max(
                1, int((row.window_started_at + timedelta(minutes=1) - now).total_seconds()) + 1
            )
        if denied:
            raise ProblemError(
                status=429,
                code="reactionx_rate_limited",
                detail="请求过于频繁，请稍后重试",
                retry_after_seconds=retry,
            )

    def _audit(self, action: str, actor: UUID, resource: UUID, request_id: str) -> None:
        AuditService(self.db).append(
            actor_user_id=actor,
            action=f"reactionx.{action}",
            resource_type="reactionx_pairing"
            if action.startswith("pairing.")
            else "reactionx_connector",
            resource_id=resource,
            purpose=None,
            outcome="allowed",
            request_id=request_id,
        )

    def _actor(self, account_id: UUID, session_id: UUID, *, lock: bool = False) -> AuthContext:
        account = self.db.scalar(
            select(UserAccount)
            .where(UserAccount.id == account_id)
            .execution_options(populate_existing=True)
        )
        if account is None:
            raise rejected(401)
        if lock:
            for model, identity in (
                (Person, account.person_id),
                (UserAccount, account_id),
                (AuthSession, session_id),
            ):
                self.db.scalar(
                    select(model)
                    .where(model.id == identity)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
        session = self.db.get(AuthSession, session_id)
        person = self.db.get(Person, account.person_id)
        if session is None or person is None:
            raise rejected(401)
        actor = AuthContext(account=account, person=person, session=session)
        ReactionxAccess(self.db, actor).require_manage()
        return actor

    def create(
        self, actor: AuthContext, request_id: str, *, browser_tab_id: UUID | None = None
    ) -> PairingView:
        ReactionxAccess(self.db, actor).require_manage()
        self._rate("create", str(actor.account.id), 5)
        with atomic_write_transaction(self.db):
            self._actor(actor.account.id, actor.session.id, lock=True)
            challenge = secrets.token_urlsafe(32)
            row = ReactionxPairing(
                origin=self.origin,
                actor_account_id=actor.account.id,
                browser_session_id=actor.session.id,
                challenge_digest=sha256(challenge.encode("ascii")).hexdigest(),
                verification_phrase=f"{secrets.token_hex(3)}-{secrets.token_hex(3)}",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
                browser_tab_id=browser_tab_id,
            )
            self.db.add(row)
            self.db.flush()
            self._audit("pairing.create", actor.account.id, row.id, request_id)
            result = PairingView(
                pairing_id=row.id,
                challenge=challenge,
                pair_uri=f"reactionx-erp://pair?challenge={challenge}",
                expires_at=row.expires_at,
                verification_phrase=row.verification_phrase,
            )
        return result

    def _valid(self, pairing: ReactionxPairing) -> None:
        if pairing.origin != self.origin:
            raise rejected(403)
        if pairing.cancelled_at is not None or pairing.expires_at <= datetime.now(UTC):
            raise rejected(410)

    def _view(self, row: ReactionxPairing, connector: ReactionxConnector) -> ClaimView:
        actor_name = self.db.scalar(
            select(Person.full_name)
            .join(UserAccount, UserAccount.person_id == Person.id)
            .where(UserAccount.id == row.actor_account_id)
        )
        if actor_name is None:
            raise rejected(401)
        return ClaimView(
            pairing_id=row.id,
            connector_id=connector.id,
            verification_phrase=row.verification_phrase,
            expires_at=row.expires_at,
            status=connector.status,
            browser_confirmed=row.browser_confirmed_at is not None,
            device_confirmed=row.device_confirmed_at is not None,
            actor_account_id=row.actor_account_id,
            actor_name=actor_name,
            origin=row.origin,
        )

    def claim(
        self,
        challenge: str,
        credential_digest: str,
        client_info: dict[str, Any],
        *,
        ip: str = "unknown",
        request_id: str = "reactionx-claim",
    ) -> ClaimView:
        self.rate_claim(ip)
        return self._claim_after_rate(
            challenge, credential_digest, client_info, request_id=request_id
        )

    def rate_claim(self, ip: str) -> None:
        self._rate("claim", ip, 20)

    def _claim_after_rate(
        self,
        challenge: str,
        credential_digest: str,
        client_info: dict[str, Any],
        *,
        request_id: str,
    ) -> ClaimView:
        if not TOKEN_PATTERN.fullmatch(challenge) or not DIGEST_PATTERN.fullmatch(
            credential_digest
        ):
            raise rejected(422)
        if client_info.get("origin") != self.origin:
            raise rejected(403)
        name = client_info.get("name")
        if not isinstance(name, str) or not 1 <= len(name) <= 80:
            raise rejected(422)
        challenge_digest = sha256(challenge.encode("ascii")).hexdigest()
        row = self.db.scalar(
            select(ReactionxPairing).where(ReactionxPairing.challenge_digest == challenge_digest)
        )
        if row is None:
            raise rejected(404)
        with atomic_write_transaction(self.db):
            self._actor(row.actor_account_id, row.browser_session_id, lock=True)
            row = self.db.scalar(
                select(ReactionxPairing)
                .where(ReactionxPairing.challenge_digest == challenge_digest)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            assert row is not None
            self._valid(row)
            if row.connector_id is not None:
                raise rejected(409)
            if (
                self.db.scalar(
                    select(ReactionxConnector.id).where(
                        ReactionxConnector.credential_digest == credential_digest
                    )
                )
                is not None
            ):
                raise rejected(409, "reactionx_credential_conflict")
            connector = ReactionxConnector(
                actor_account_id=row.actor_account_id,
                browser_session_id=row.browser_session_id,
                credential_digest=credential_digest,
                origin=self.origin,
                client_name=name,
                status="pending",
            )
            self.db.add(connector)
            try:
                self.db.flush()
            except IntegrityError as error:
                # A different account can win the digest after our preflight.
                # Only this exact uniqueness race is a public conflict; every
                # other database error propagates, and the atomic unit rolls back.
                if (
                    getattr(error.orig, "sqlstate", None) == "23505"
                    and getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                    == "reactionx_connectors_credential_digest_key"
                ):
                    raise rejected(409, "reactionx_credential_conflict") from None
                raise
            row.connector_id = connector.id
            self._audit("pairing.claim", row.actor_account_id, row.id, request_id)
            result = self._view(row, connector)
        return result

    def authenticate(
        self, authorization: str | None, *, allow_pending: bool = False, safety_only: bool = False
    ) -> ReactionxConnector:
        if not authorization or not authorization.startswith("ReactionX "):
            raise rejected(401)
        identity, separator, secret = authorization[10:].partition(".")
        try:
            connector_id = UUID(identity)
        except ValueError:
            raise rejected(401) from None
        if not separator or not TOKEN_PATTERN.fullmatch(secret):
            raise rejected(401)
        decoded = base64.b64decode(secret + "=", altchars=b"-_", validate=True)
        if (
            len(decoded) != 32
            or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != secret
        ):
            raise rejected(401)
        connector = self.db.scalar(
            select(ReactionxConnector)
            .where(ReactionxConnector.id == connector_id)
            .execution_options(populate_existing=True)
        )
        if (
            connector is None
            or not secrets.compare_digest(
                connector.credential_digest, sha256(secret.encode("ascii")).hexdigest()
            )
            or connector.origin != self.origin
            or connector.status == "revoked"
        ):
            raise rejected(401)
        if not safety_only:
            self._actor(connector.actor_account_id, connector.browser_session_id)
        if connector.status != "active":
            if not allow_pending:
                raise rejected(403)
            row = self.db.scalar(
                select(ReactionxPairing).where(ReactionxPairing.connector_id == connector.id)
            )
            if row is None:
                raise rejected(401)
            self._valid(row)
        return connector

    def status(
        self,
        pairing_id: UUID,
        *,
        actor: AuthContext | None = None,
        connector: ReactionxConnector | None = None,
    ) -> ClaimView:
        row = self.db.get(ReactionxPairing, pairing_id)
        if row is None or row.connector_id is None:
            raise rejected(404)
        if actor is not None:
            ReactionxAccess(self.db, actor).require_manage()
            if (actor.account.id, actor.session.id) != (
                row.actor_account_id,
                row.browser_session_id,
            ):
                raise rejected(403)
            connector = self.db.get(ReactionxConnector, row.connector_id)
        if connector is None or connector.id != row.connector_id:
            raise rejected(404)
        if connector.status != "active":
            self._valid(row)
        return self._view(row, connector)

    def _confirm(
        self,
        pairing_id: UUID,
        phrase: str,
        *,
        actor: AuthContext | None,
        connector: ReactionxConnector | None,
        request_id: str,
    ) -> ClaimView:
        initial = self.db.get(ReactionxPairing, pairing_id)
        if initial is None:
            raise rejected(404)
        with atomic_write_transaction(self.db):
            self._actor(initial.actor_account_id, initial.browser_session_id, lock=True)
            row = self.db.scalar(
                select(ReactionxPairing)
                .where(ReactionxPairing.id == pairing_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            assert row is not None
            self._valid(row)
            if row.connector_id is None:
                raise rejected(409)
            current = self.db.scalar(
                select(ReactionxConnector)
                .where(ReactionxConnector.id == row.connector_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if current is None or current.status == "revoked":
                raise rejected(401)
            if not secrets.compare_digest(phrase, row.verification_phrase):
                raise rejected(403)
            now = datetime.now(UTC)
            if actor is not None:
                ReactionxAccess(self.db, actor).require_manage()
                if (actor.account.id, actor.session.id) != (
                    row.actor_account_id,
                    row.browser_session_id,
                ):
                    raise rejected(403)
                row.browser_confirmed_at = row.browser_confirmed_at or now
                action = "pairing.confirm_browser"
            else:
                if connector is None or connector.id != current.id:
                    raise rejected(403)
                row.device_confirmed_at = row.device_confirmed_at or now
                action = "pairing.confirm_device"
            self._audit(action, row.actor_account_id, row.id, request_id)
            if (
                row.browser_confirmed_at is not None
                and row.device_confirmed_at is not None
                and current.status != "active"
            ):
                current.status, current.activated_at = "active", now
                current.control_tab_id = row.browser_tab_id
                self._audit("pairing.activate", row.actor_account_id, row.id, request_id)
            self.db.flush()
            result = self._view(row, current)
        return result

    def confirm_browser(
        self, pairing_id: UUID, actor: AuthContext, verification_phrase: str, *, request_id: str
    ) -> ClaimView:
        return self._confirm(
            UUID(str(pairing_id)),
            verification_phrase,
            actor=actor,
            connector=None,
            request_id=request_id,
        )

    def confirm_device(
        self,
        pairing_id: UUID,
        connector: ReactionxConnector,
        verification_phrase: str,
        *,
        request_id: str,
    ) -> ClaimView:
        return self._confirm(
            pairing_id, verification_phrase, actor=None, connector=connector, request_id=request_id
        )

    def revoke(
        self, connector_id: UUID, actor: AuthContext, *, request_id: str = "reactionx-revoke"
    ) -> None:
        with atomic_write_transaction(self.db):
            actor = self._actor(actor.account.id, actor.session.id, lock=True)
            row = self.db.scalar(
                select(ReactionxConnector)
                .where(ReactionxConnector.id == connector_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise rejected(404)
            # Identity locks stay first; time/permission can expire while the
            # connector row is contended, so refresh without reversing locks.
            ReactionxAccess(self.db, actor).require_manage()
            row.status, row.revoked_at = "revoked", datetime.now(UTC)
            self._audit("connector.revoke", actor.account.id, row.id, request_id)

    def cancel(self, pairing_id: UUID, actor: AuthContext, *, request_id: str) -> None:
        with atomic_write_transaction(self.db):
            self._actor(actor.account.id, actor.session.id, lock=True)
            row = self.db.scalar(
                select(ReactionxPairing)
                .where(ReactionxPairing.id == pairing_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise rejected(404)
            if (row.actor_account_id, row.browser_session_id) != (
                actor.account.id,
                actor.session.id,
            ):
                raise rejected(403)
            row.cancelled_at = datetime.now(UTC)
            if row.connector_id is not None:
                connector = self.db.get(ReactionxConnector, row.connector_id)
                if connector is not None:
                    connector.status, connector.revoked_at = "revoked", datetime.now(UTC)
            self._audit("pairing.cancel", actor.account.id, row.id, request_id)
