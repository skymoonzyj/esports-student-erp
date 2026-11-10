"""Original unknown-delivery evidence and explicitly confirmed non-result closure."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import AuditMetadataRejected, AuditService
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.identity.models import Person
from app.reactionx.access import ReactionxAccess
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxConnector,
    ReactionxEvent,
    ReactionxResultReceipt,
    ReactionxRun,
)
from app.reactionx.pairing import DIGEST_PATTERN, ERP_ORIGIN, PairingService
from app.reactionx.queries import AckRequest
from app.reactionx.recovery import RecoveryService
from app.reactionx.schemas import (
    CommandEnvelope,
    Digest,
    Mode,
    RunManifest,
    StrictModel,
    UTCStamp,
    UUIDText,
    canonical_digest,
)


def rejected(status: int = 409, code: str = "reactionx_uncertainty_conflict") -> ProblemError:
    return ProblemError(status=status, code=code, detail="执行状态未知归档条件不符，需人工核对")


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class UncertaintyDeclaration(StrictModel):
    version: Literal[1]
    kind: Literal["start_delivery_unknown"]
    run_id: UUIDText
    actor_account_id: UUIDText
    subject_person_id: UUIDText
    connector_id: UUIDText
    manifest_digest: Digest
    stop_operation_id: UUIDText
    local_evidence_digest: Digest


class UncertaintyReceipt(StrictModel):
    version: Literal[1]
    archive_id: UUIDText
    run_id: UUIDText
    connector_id: UUIDText
    manifest_digest: Digest
    declaration_digest: Digest
    stop_operation_id: UUIDText
    status: Literal["execution_unknown"]
    closed_at: UTCStamp


class UncertaintyStatus(StrictModel):
    state: Literal["pending_review", "archived"]
    declaration_digest: Digest
    receipt: UncertaintyReceipt | None


class ArchiveRequest(StrictModel):
    request_id: UUIDText
    run_id: UUIDText
    declaration_digest: Digest
    expires_at: UTCStamp


class UncertaintyItem(BaseModel):
    run_id: UUID
    subject_name: str
    mode: Mode
    created_at: datetime
    declaration_digest: str
    state: Literal["pending_review"] = "pending_review"


class UncertaintyPage(BaseModel):
    items: list[UncertaintyItem]
    total: int
    offset: int
    limit: int


class UncertaintyService:
    def __init__(
        self,
        db: Session,
        *,
        origin: str = ERP_ORIGIN,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db, self.origin = db, origin
        self.clock = clock or (lambda: datetime.now(UTC))
        self.recovery = RecoveryService(db, origin=origin, clock=self.clock)

    def _machine_run(self, connector: ReactionxConnector, run_id: UUID) -> ReactionxRun:
        # Capture authentication binding BEFORE refreshing a potentially shared
        # ORM identity; revocation/credential replacement during lock wait fails.
        binding = (connector.id, connector.actor_account_id, connector.credential_digest)
        original = self.db.get(ReactionxRun, run_id, populate_existing=True)
        if original is None or (original.connector_id, original.actor_account_id) != binding[:2]:
            raise rejected(404)
        run = self.recovery._run(run_id)
        current = self.db.get(ReactionxConnector, run.connector_id)
        assert current is not None
        if (current.id, current.actor_account_id, current.credential_digest) != binding:
            raise rejected(401)
        if (run.connector_id, run.actor_account_id) != binding[:2]:
            raise rejected(404)
        return run

    def _browser_run(self, actor: AuthContext, run_id: UUID) -> ReactionxRun:
        original = self.db.get(ReactionxRun, run_id, populate_existing=True)
        if original is None:
            raise rejected(404)
        self.recovery._issuer(actor.account.id, actor.session.id, original.subject_person_id)
        run = self.recovery._run(run_id)
        self.recovery._issuer(actor.account.id, actor.session.id, run.subject_person_id, lock=False)
        return run

    def _candidate(self, run: ReactionxRun, declaration: UncertaintyDeclaration) -> None:
        expected = {
            "run_id": str(run.id),
            "actor_account_id": str(run.actor_account_id),
            "subject_person_id": str(run.subject_person_id),
            "connector_id": str(run.connector_id),
            "manifest_digest": run.manifest_digest,
        }
        if (
            any(getattr(declaration, key) != value for key, value in expected.items())
            or run.status not in ("awaiting_sync", "uncertain_closed")
            or not run.stop_confirmed
            or self.db.get(ReactionxResultReceipt, run.id, populate_existing=True) is not None
            or self.db.scalar(
                select(ReactionxEvent.seq).where(ReactionxEvent.run_id == run.id).limit(1)
            )
            is not None
        ):
            raise rejected()
        try:
            manifest = RunManifest.model_validate(run.manifest)
            if (
                any(getattr(manifest, key) != value for key, value in expected.items())
                or canonical_digest(manifest, exclude={"manifest_digest"}) != run.manifest_digest
            ):
                raise ValueError("manifest binding")
            commands = list(
                self.db.scalars(
                    select(ReactionxCommand)
                    .where(ReactionxCommand.run_id == run.id)
                    .execution_options(populate_existing=True)
                )
            )
            starts = [row for row in commands if row.kind == "start"]
            stops = [
                row
                for row in commands
                if str(row.operation_id) == declaration.stop_operation_id and row.kind == "stop"
            ]
            # Any START ACK contradicts the claimed missing local acceptance.
            # Historical running ACKs can have been superseded by terminal ACKs;
            # accepting those rows would incorrectly infer no execution.
            if len(starts) != 1 or starts[0].ack_payload is not None or len(stops) != 1:
                raise ValueError("start/stop evidence")
            start, stop = starts[0], stops[0]
            for row in (start, stop):
                envelope = CommandEnvelope.model_validate(row.delivery_envelope)
                if (
                    (row.connector_id, row.actor_account_id, row.subject_person_id)
                    != (run.connector_id, run.actor_account_id, run.subject_person_id)
                    or any(
                        getattr(envelope, key) != value
                        for key, value in expected.items()
                        if key != "manifest_digest"
                    )
                    or envelope.kind != row.kind
                    or envelope.operation_id != str(row.operation_id)
                    or envelope.sequence != row.sequence
                    or envelope.payload.model_dump(mode="json") != row.payload
                ):
                    raise ValueError("command binding")
            off = AckRequest.model_validate(stop.ack_payload)
            if (
                stop.status != "completed"
                or stop.acknowledged_at is None
                or stop.sequence <= start.sequence
                or off.status != "completed"
                or off.run_state != "stopped_confirmed"
                or set(off.off_confirmed_device_keys)
                != {device.device_key for device in manifest.devices}
            ):
                raise ValueError("physical OFF evidence")
        except (ValueError, TypeError, KeyError):
            raise rejected() from None

    def _record(self, run: ReactionxRun, digest: str) -> dict[str, Any]:
        record = dict(run.uncertainty_record or {})
        if not record:
            raise rejected(404)
        if not DIGEST_PATTERN.fullmatch(digest) or record["declaration_digest"] != digest:
            raise rejected()
        self._candidate(run, UncertaintyDeclaration.model_validate(record["declaration"]))
        return record

    def _view(self, record: dict[str, Any]) -> UncertaintyStatus:
        return UncertaintyStatus.model_validate(
            {
                "state": "archived" if record.get("receipt") else "pending_review",
                "declaration_digest": record["declaration_digest"],
                "receipt": record.get("receipt"),
            }
        )

    def _audit(
        self, actor: AuthContext, action: str, run: ReactionxRun, request: dict[str, Any]
    ) -> None:
        AuditService(self.db).append(
            actor_user_id=actor.account.id,
            action=f"reactionx.uncertainty.{action}",
            resource_type="reactionx_run",
            resource_id=run.id,
            purpose=request["reason"],
            outcome="allowed",
            request_id="reactionx-uncertainty",
            metadata={"operation_id": request["id"], "session_id": request["issuer_session"]},
        )

    def declare(
        self, connector: ReactionxConnector, run_id: UUID, declaration: UncertaintyDeclaration
    ) -> UncertaintyStatus:
        with atomic_write_transaction(self.db):
            run = self._machine_run(connector, run_id)
            self._candidate(run, declaration)
            digest = canonical_digest(declaration)
            if run.uncertainty_record is not None:
                return self._view(self._record(run, digest))
            record = {
                "declaration": declaration.model_dump(mode="json"),
                "declaration_digest": digest,
                "declared_at": stamp(self.clock()),
                "request": None,
                "receipt": None,
            }
            run.uncertainty_record = record
            return self._view(record)

    def status(
        self, connector: ReactionxConnector, run_id: UUID, declaration_digest: str
    ) -> UncertaintyStatus:
        with atomic_write_transaction(self.db):
            return self._view(
                self._record(self._machine_run(connector, run_id), declaration_digest)
            )

    def list_pending(self, actor: AuthContext, offset: int = 0, limit: int = 20) -> UncertaintyPage:
        if offset < 0 or not 1 <= limit <= 100:
            raise rejected(422)
        fresh = PairingService(self.db, origin=self.origin)._actor(
            actor.account.id, actor.session.id
        )
        access = ReactionxAccess(self.db, fresh)
        permitted = access.conduct_person_ids()
        conditions = (
            ReactionxRun.status == "awaiting_sync",
            ReactionxRun.uncertainty_record.is_not(None),
            ReactionxRun.subject_person_id.in_(permitted),
        )
        total = (
            self.db.scalar(select(func.count()).select_from(ReactionxRun).where(*conditions)) or 0
        )
        rows = list(
            self.db.execute(
                select(ReactionxRun, Person.full_name)
                .join(Person, Person.id == ReactionxRun.subject_person_id)
                .where(*conditions)
                .order_by(ReactionxRun.created_at, ReactionxRun.id)
                .offset(offset)
                .limit(limit)
            )
        )
        items = [
            UncertaintyItem(
                run_id=run.id,
                subject_name=name,
                mode=run.manifest["mode"],
                created_at=run.created_at,
                declaration_digest=run.uncertainty_record["declaration_digest"],
            )
            for run, name in rows
        ]
        fresh = PairingService(self.db, origin=self.origin)._actor(
            actor.account.id, actor.session.id
        )
        access = ReactionxAccess(self.db, fresh)
        access.require_conduct()
        for run, _ in rows:
            access.require_conduct(run.subject_person_id)
        return UncertaintyPage(items=items, total=total, offset=offset, limit=limit)

    def request_archive(
        self,
        actor: AuthContext,
        run_id: UUID,
        declaration_digest: str,
        reason: str,
        confirmation: bool,
    ) -> ArchiveRequest:
        reason = reason.strip()
        if confirmation is not True or not 10 <= len(reason) <= 500:
            raise rejected(422)
        try:
            AuditService.validate_purpose(reason)
        except AuditMetadataRejected:
            raise rejected(422, "reactionx_uncertainty_reason_invalid") from None
        with atomic_write_transaction(self.db):
            run = self._browser_run(actor, run_id)
            record = self._record(run, declaration_digest)
            if record.get("receipt"):
                raise rejected()
            now = self.clock()
            request: dict[str, Any] = {
                "id": str(uuid4()),
                "issuer": str(actor.account.id),
                "issuer_session": str(actor.session.id),
                "declaration_digest": declaration_digest,
                "reason": reason,
                "requested_at": stamp(now),
                "expires_at": stamp(now + timedelta(minutes=5)),
                "confirmed_at": None,
            }
            record["request"] = request
            self.recovery._issuer(
                actor.account.id, actor.session.id, run.subject_person_id, lock=False
            )
            run.uncertainty_record = record
            self._audit(actor, "requested", run, request)
            return ArchiveRequest(
                request_id=request["id"],
                run_id=str(run.id),
                declaration_digest=declaration_digest,
                expires_at=request["expires_at"],
            )

    def confirm_archive(
        self, actor: AuthContext, run_id: UUID, request_id: UUID, confirmation: bool
    ) -> UncertaintyReceipt:
        if confirmation is not True:
            raise rejected(422)
        with atomic_write_transaction(self.db):
            run = self._browser_run(actor, run_id)
            saved = run.uncertainty_record or {}
            record = self._record(run, saved.get("declaration_digest", ""))
            request = dict(record.get("request") or {})
            if (
                request.get("id") != str(request_id)
                or request.get("issuer") != str(actor.account.id)
                or request.get("issuer_session") != str(actor.session.id)
                or request.get("declaration_digest") != record["declaration_digest"]
            ):
                raise rejected(403)
            if record.get("receipt"):
                return UncertaintyReceipt.model_validate(record["receipt"])
            now = self.clock()
            if (
                not datetime.fromisoformat(request["requested_at"])
                <= now
                < datetime.fromisoformat(request["expires_at"])
            ):
                raise rejected(403, "reactionx_uncertainty_request_expired")
            receipt = UncertaintyReceipt(
                version=1,
                archive_id=str(uuid4()),
                run_id=str(run.id),
                connector_id=str(run.connector_id),
                manifest_digest=run.manifest_digest,
                declaration_digest=record["declaration_digest"],
                stop_operation_id=record["declaration"]["stop_operation_id"],
                status="execution_unknown",
                closed_at=stamp(now),
            )
            request["confirmed_at"] = stamp(now)
            record.update(request=request, receipt=receipt.model_dump(mode="json"))
            self.recovery._issuer(
                actor.account.id, actor.session.id, run.subject_person_id, lock=False
            )
            run.status, run.uncertainty_record = "uncertain_closed", record
            self._audit(actor, "archived", run, request)
            return receipt
