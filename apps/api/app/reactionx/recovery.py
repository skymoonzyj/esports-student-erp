"""Narrow, original-credential uploads; never renew runtime/browser authority."""

import base64
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.reactionx.access import ReactionxAccess
from app.reactionx.models import ReactionxConnector, ReactionxRun
from app.reactionx.pairing import DIGEST_PATTERN, ERP_ORIGIN, TOKEN_PATTERN, PairingService


def denied(status: int = 403, code: str = "reactionx_upload_forbidden") -> ProblemError:
    return ProblemError(status=status, code=code, detail="ReactionX 原始结果上传授权无效")


def require_not_uncertain_closed(run: ReactionxRun) -> None:
    if run.status == "uncertain_closed":
        raise ProblemError(
            status=409,
            code="reactionx_uncertain_closed",
            detail="执行状态未知已归档，矛盾证据需人工核对",
        )


class UploadPermit(BaseModel):
    authorization_id: UUID
    run_id: UUID
    digest: str
    kind: Literal["original_reauthorization", "superadmin_recovery"]
    status: Literal["authorized"] = "authorized"
    expires_at: datetime


RecoveryPermit = UploadPermit


class RecoveryRequest(BaseModel):
    request_id: UUID
    run_id: UUID
    digest: str
    status: Literal["confirmation_required"] = "confirmation_required"
    expires_at: datetime


class RecoveryService:
    def __init__(
        self, db: Session, *, origin: str = ERP_ORIGIN, clock: Callable[[], datetime] | None = None
    ) -> None:
        self.db, self.origin = db, origin
        self.clock = clock or (lambda: datetime.now(UTC))

    def authenticate_result(self, authorization: str | None) -> ReactionxConnector:
        """Credential proof ONLY. Every consumer MUST authorize the exact package.

        Kept separate from generic PairingService authentication and B3 safety auth.
        Disabled original actors are usable only under a separately validated grant.
        """
        if not authorization or not authorization.startswith("ReactionX "):
            raise denied(401)
        identity, separator, secret = authorization[10:].partition(".")
        try:
            connector_id = UUID(identity)
        except ValueError:
            raise denied(401) from None
        if not separator or not TOKEN_PATTERN.fullmatch(secret):
            raise denied(401)
        decoded = base64.b64decode(secret + "=", altchars=b"-_", validate=True)
        if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).rstrip(b"=").decode() != secret:
            raise denied(401)
        connector = self.db.scalar(
            select(ReactionxConnector)
            .where(ReactionxConnector.id == connector_id)
            .execution_options(populate_existing=True)
        )
        if (
            connector is None
            or connector.status != "active"
            or connector.origin != self.origin
            or not secrets.compare_digest(
                connector.credential_digest, sha256(secret.encode("ascii")).hexdigest()
            )
        ):
            raise denied(401)
        return connector

    def _issuer(
        self, account_id: UUID, session_id: UUID, subject: UUID, *, lock: bool = True
    ) -> AuthContext:
        actor = PairingService(self.db, origin=self.origin)._actor(
            account_id, session_id, lock=lock
        )
        ReactionxAccess(self.db, actor).require_conduct(subject)
        return actor

    def _run(self, run_id: UUID) -> ReactionxRun:
        original = self.db.get(ReactionxRun, run_id)
        if original is None:
            raise denied(404)
        connector = self.db.scalar(
            select(ReactionxConnector)
            .where(ReactionxConnector.id == original.connector_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if connector is None or connector.status != "active" or connector.origin != self.origin:
            raise denied(401)
        run = self.db.scalar(
            select(ReactionxRun)
            .where(ReactionxRun.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        assert run is not None
        return run

    def _audit(self, actor: UUID, action: str, run: ReactionxRun, grant: dict[str, Any]) -> None:
        AuditService(self.db).append(
            actor_user_id=actor,
            action=f"reactionx.{action}",
            resource_type="reactionx_run",
            resource_id=run.id,
            purpose=grant.get("reason"),
            outcome="allowed",
            request_id="reactionx-upload",
            metadata={"operation_id": grant["id"], "session_id": grant["issuer_session"]},
        )

    def _new(
        self, run: ReactionxRun, actor: AuthContext, digest: str, kind: str, reason: str | None
    ) -> dict[str, Any]:
        if not DIGEST_PATTERN.fullmatch(digest):
            raise denied(422)
        now = self.clock()
        return {
            "id": str(uuid4()),
            "run": str(run.id),
            "actor": str(run.actor_account_id),
            "subject": str(run.subject_person_id),
            "connector": str(run.connector_id),
            "digest": digest,
            "issuer": str(actor.account.id),
            "issuer_session": str(actor.session.id),
            "kind": kind,
            "reason": reason,
            "first_confirmed_at": now.isoformat(),
            "second_confirmed_at": None,
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(minutes=5)).isoformat(),
            "consumed_at": None,
        }

    def _store(self, run: ReactionxRun, grant: dict[str, Any]) -> None:
        run.upload_authorization = grant
        run.upload_permit_digest = grant["digest"]
        run.upload_permit_expires_at = datetime.fromisoformat(grant["expires_at"])

    def _view(self, run: ReactionxRun, grant: dict[str, Any]) -> UploadPermit:
        return UploadPermit(
            authorization_id=grant["id"],
            run_id=run.id,
            digest=grant["digest"],
            kind=grant["kind"],
            expires_at=grant["expires_at"],
        )

    def authorize_upload(
        self, actor: AuthContext, run_id: UUID, digest: str, confirmation: bool = True
    ) -> UploadPermit:
        with atomic_write_transaction(self.db):
            original = self.db.get(ReactionxRun, run_id)
            if original is None or original.actor_account_id != actor.account.id:
                raise denied(404)
            self._issuer(actor.account.id, actor.session.id, original.subject_person_id)
            run = self._run(run_id)
            require_not_uncertain_closed(run)
            # Identity locks do not stop time. Recheck the SAME issuer after
            # waiting for connector/run locks, without taking new identity locks.
            self._issuer(actor.account.id, actor.session.id, run.subject_person_id, lock=False)
            if confirmation is not True or not self.in_window(run):
                raise denied()
            grant = self._new(run, actor, digest, "original_reauthorization", None)
            grant["second_confirmed_at"] = grant["first_confirmed_at"]
            self._store(run, grant)
            self._audit(actor.account.id, "upload.authorized", run, grant)
            return self._view(run, grant)

    def authorize(
        self, actor: AuthContext, run_id: UUID, digest: str, reason: str, confirmation: bool
    ) -> RecoveryRequest:
        reason = reason.strip()
        if confirmation is not True or not 10 <= len(reason) <= 500:
            raise denied(422)
        # Audit validation is performed before any grant changes.
        try:
            AuditService.validate_purpose(reason)
        except AuditMetadataRejected:
            raise denied(422, "reactionx_recovery_reason_invalid") from None
        with atomic_write_transaction(self.db):
            original = self.db.get(ReactionxRun, run_id)
            if original is None:
                raise denied(404)
            self._issuer(actor.account.id, actor.session.id, original.subject_person_id)
            run = self._run(run_id)
            require_not_uncertain_closed(run)
            self._issuer(actor.account.id, actor.session.id, run.subject_person_id, lock=False)
            grant = self._new(run, actor, digest, "superadmin_recovery", reason)
            self._store(run, grant)
            self._audit(actor.account.id, "recovery.requested", run, grant)
            return RecoveryRequest(
                request_id=grant["id"], run_id=run.id, digest=digest, expires_at=grant["expires_at"]
            )

    def confirm(
        self, actor: AuthContext, run_id: UUID, request_id: UUID, confirmation: bool
    ) -> RecoveryPermit:
        with atomic_write_transaction(self.db):
            original = self.db.get(ReactionxRun, run_id)
            if original is None:
                raise denied(404)
            self._issuer(actor.account.id, actor.session.id, original.subject_person_id)
            run = self._run(run_id)
            require_not_uncertain_closed(run)
            self._issuer(actor.account.id, actor.session.id, run.subject_person_id, lock=False)
            grant = dict(run.upload_authorization or {})
            if (
                confirmation is not True
                or grant.get("id") != str(request_id)
                or grant.get("issuer") != str(actor.account.id)
                or grant.get("issuer_session") != str(actor.session.id)
                or grant.get("kind") != "superadmin_recovery"
                or grant.get("consumed_at") is not None
                or not self._fresh(grant)
            ):
                raise denied()
            if grant.get("second_confirmed_at") is None:
                grant["second_confirmed_at"] = self.clock().isoformat()
                # Same fixed expiry as first confirmation; repeat cannot extend it.
                self._store(run, grant)
                self._audit(actor.account.id, "recovery.confirmed", run, grant)
            return self._view(run, grant)

    def in_window(self, run: ReactionxRun) -> bool:
        return run.created_at <= self.clock() <= run.created_at + timedelta(days=30)

    def _fresh(self, grant: dict[str, Any]) -> bool:
        try:
            return (
                datetime.fromisoformat(grant["issued_at"])
                <= self.clock()
                < datetime.fromisoformat(grant["expires_at"])
            )
        except (KeyError, TypeError, ValueError):
            return False

    def authorize_submission(
        self, run: ReactionxRun, digest: str, *, replay: bool, lock: bool = True
    ) -> bool:
        """Return True when this call uses the one-use grant (caller consumes it)."""
        require_not_uncertain_closed(run)
        if self.in_window(run):
            try:
                # Probe rejected original authority without retaining its locks.
                # A recovery grant belongs to another live issuer; holding both
                # identities could deadlock reciprocal A/B recovery submissions.
                self._issuer(
                    run.actor_account_id, run.browser_session_id, run.subject_person_id, lock=False
                )
            except ProblemError:
                pass
            else:
                # Successful normal authority still gets the original B3 lock
                # and a fresh check. If revoked during that gap, fail this call
                # rather than retaining its lock and switching to another issuer.
                self._issuer(
                    run.actor_account_id, run.browser_session_id, run.subject_person_id, lock=lock
                )
                return False
        grant = run.upload_authorization or {}
        expected = {
            "run": str(run.id),
            "actor": str(run.actor_account_id),
            "subject": str(run.subject_person_id),
            "connector": str(run.connector_id),
            "digest": digest,
        }
        if (
            any(grant.get(key) != value for key, value in expected.items())
            or grant.get("second_confirmed_at") is None
            or not self._fresh(grant)
            or (grant.get("consumed_at") is not None and not replay)
        ):
            raise denied()
        self._issuer(
            UUID(grant["issuer"]), UUID(grant["issuer_session"]), run.subject_person_id, lock=lock
        )
        if grant["kind"] == "original_reauthorization":
            if grant["issuer"] != str(run.actor_account_id) or not self.in_window(run):
                raise denied()
        elif grant["kind"] != "superadmin_recovery":
            raise denied()
        return True
