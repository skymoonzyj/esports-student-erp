"""Atomic immutable results and scoped reports. No device-control authority."""

import base64
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.audit.service import AuditService
from app.auth.dependencies import require_current_standard_account
from app.auth.service import AuthContext
from app.core.transactions import atomic_write_transaction
from app.reactionx.access import ReactionxAccess
from app.reactionx.metrics import (
    InvalidEvents,
    MetricResult,
    _validate_events,
    validate_result_envelope,
)
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxConnector,
    ReactionxEvent,
    ReactionxResultReceipt,
    ReactionxRun,
)
from app.reactionx.pairing import ERP_ORIGIN
from app.reactionx.recovery import RecoveryService, denied, require_not_uncertain_closed
from app.reactionx.rule_versions import RULES
from app.reactionx.schemas import (
    MAX_RESULT_BYTES,
    SAFE_INTEGER,
    Mode,
    ResultEnvelope,
    ResultReceipt,
    RunManifest,
    canonical_digest,
    canonical_json,
)
from app.talent_tests.access import TalentAccess
from app.talent_tests.errors import TalentNotFound


def require_report(access: ReactionxAccess, person_id: UUID) -> None:
    try:
        access.require_report(person_id)
    except TalentNotFound:
        raise denied(404, "reactionx_result_not_found") from None


def strict_json(body: bytes) -> Any:
    """Reject evidence loss BEFORE duplicate keys or unsafe numbers become a dict."""
    if len(body) > MAX_RESULT_BYTES:
        raise denied(413, "reactionx_body_too_large")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def integer(token: str) -> int:
        value = int(token)
        if abs(value) > SAFE_INTEGER:
            raise ValueError("unsafe integer")
        return value

    def invalid(token: str) -> None:
        raise ValueError("non-integer number")

    value = json.loads(
        body.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_int=integer,
        parse_float=invalid,
        parse_constant=invalid,
    )
    canonical_json(value)  # Also rejects unpaired surrogates, unsafe string values.
    return value


def receipt_view(row: ReactionxResultReceipt) -> ResultReceipt:
    return ResultReceipt(
        run_id=str(row.run_id),
        result_id=str(row.result_id),
        digest=row.digest,
        saved_at=row.saved_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    )


class ResultSummary(BaseModel):
    receipt: ResultReceipt
    subject_person_id: UUID
    actor_account_id: UUID
    mode: Mode
    end_kind: str
    metrics: MetricResult
    ranking_eligible: bool
    ranking_reason: str | None


class ResultReport(BaseModel):
    receipt: ResultReceipt
    actor_account_id: UUID
    subject_person_id: UUID
    manifest: RunManifest
    envelope: ResultEnvelope
    metrics: MetricResult
    ranking_eligible: bool
    ranking_reason: str | None


class ResultPage(BaseModel):
    items: list[ResultSummary]
    next_cursor: str | None


class ResultService:
    def __init__(
        self,
        db: Session,
        *,
        origin: str = ERP_ORIGIN,
        clock: Callable[[], datetime] | None = None,
        validation_policy: Callable[[Mode], bool] | None = None,
    ) -> None:
        self.db, self.origin = db, origin
        self.clock = clock or (lambda: datetime.now(UTC))
        # Explicit isolated service-test injection only. HTTP never supplies this.
        self.validation_policy = validation_policy or (lambda mode: False)
        self.recovery = RecoveryService(db, origin=origin, clock=self.clock)

    def _owned(
        self, connector: ReactionxConnector, run_id: UUID, digest: str, *, replay: bool
    ) -> tuple[ReactionxRun, bool]:
        original = self.db.scalar(
            select(ReactionxRun)
            .where(ReactionxRun.id == run_id)
            .execution_options(populate_existing=True)
        )
        if (
            original is None
            or original.connector_id != connector.id
            or original.actor_account_id != connector.actor_account_id
        ):
            raise denied(404)
        # Same actor->connector->run lock ordering as B3. Authorization locks the
        # issuer first; if a different grant won the run lock, fail and retry.
        grant_id = (original.upload_authorization or {}).get("id")
        used_grant = self.recovery.authorize_submission(original, digest, replay=replay)
        run = self.recovery._run(run_id)
        require_not_uncertain_closed(run)
        if used_grant and (run.upload_authorization or {}).get("id") != grant_id:
            raise denied(409, "reactionx_authorization_changed")
        # The selected live issuer's identity locks are already held. Re-read
        # session expiry, effective permissions and exact grant AFTER acquiring
        # connector/run locks, without introducing a second identity lock order.
        # A changed authorization mode must retry from the initial lock boundary.
        if self.recovery.authorize_submission(run, digest, replay=replay, lock=False) != used_grant:
            raise denied(409, "reactionx_authorization_changed")
        return run, used_grant

    def submit(self, connector: ReactionxConnector, envelope: ResultEnvelope) -> ResultReceipt:
        digest = canonical_digest(envelope)
        try:
            run_id = UUID(envelope.run_id)
        except ValueError:
            raise denied(422) from None
        with atomic_write_transaction(self.db):
            # A consumed grant may authorize ONLY reading its matching receipt.
            # Do not decide replay from an unlocked absence preflight: a winner
            # can commit between that read and authorization (lost-ACK race).
            run, used_grant = self._owned(connector, run_id, digest, replay=True)
            existing = self.db.get(ReactionxResultReceipt, run_id, populate_existing=True)
            if existing is not None:
                if existing.digest != digest:
                    raise denied(409, "reactionx_result_conflict")
                if run.stop_confirmed and run.status == "awaiting_sync":
                    run.status = "completed"
                return receipt_view(existing)
            if used_grant and (run.upload_authorization or {}).get("consumed_at") is not None:
                raise denied()
            manifest = RunManifest.model_validate(run.manifest)
            if (
                str(manifest.actor_account_id) != str(run.actor_account_id)
                or str(manifest.subject_person_id) != str(run.subject_person_id)
                or manifest.connector_id != str(run.connector_id)
                or manifest.manifest_digest != run.manifest_digest
            ):
                raise denied(409, "reactionx_manifest_conflict")
            try:
                metrics = validate_result_envelope(manifest, envelope)
            except ValueError:
                raise denied(422, "reactionx_invalid_result") from None
            keys = {device.device_key for device in manifest.devices}
            previous = 0
            for seq, event in enumerate(envelope.events, start=1):
                if (
                    event.seq != seq
                    or event.host_elapsed_ms < previous
                    or event.host_elapsed_ms > envelope.end.active_elapsed_ms
                    or (event.device_key is not None and event.device_key not in keys)
                ):
                    raise denied(422, "reactionx_invalid_events")
                previous = event.host_elapsed_ms
            if metrics.reason in ("invalid_manifest", "invalid_events"):
                raise denied(422, "reactionx_invalid_events")
            commands = self.db.scalars(
                select(ReactionxCommand).where(ReactionxCommand.run_id == run.id)
            ).all()
            if (
                not any(c.kind == "start" and c.delivery_envelope is not None for c in commands)
                or run.status == "cancelled"
            ):
                raise denied(409, "reactionx_run_not_executed")
            normal_ends = {"task_completed", "time_completed", "all_lit"}
            stops = [c for c in commands if c.kind == "stop"]
            if envelope.end.kind in normal_ends and stops:
                # Reconnect may enqueue a safety STOP after the connector has
                # already persisted natural completion. Only the original START
                # terminal ACK cancelling NEVER-delivered safety work proves
                # that boundary. User STOP/delivered STOP still forbids relabel.
                terminal = next(
                    (
                        c
                        for c in commands
                        if c.kind == "start"
                        and c.delivery_envelope is not None
                        and c.status == "completed"
                        and c.ack_payload is not None
                        and c.ack_payload.get("status") == "completed"
                        and c.ack_payload.get("run_state") == "stopped_confirmed"
                        and sorted(c.ack_payload.get("off_confirmed_device_keys", []))
                        == sorted(keys)
                    ),
                    None,
                )
                late_safety = (
                    run.stop_confirmed
                    and terminal is not None
                    and terminal.acknowledged_at is not None
                    and terminal.acknowledged_at == run.ended_at
                    and envelope.end.kind in RULES[manifest.mode].allowed_ends
                    and all(
                        c.delivery_envelope is None
                        and c.status == "cancelled"
                        and c.ack_payload is None
                        and c.acknowledged_at is None
                        and c.payload.get("reason") in ("disconnected", "lease_expired")
                        and c.created_at <= terminal.acknowledged_at
                        for c in stops
                    )
                )
                if late_safety:
                    try:
                        _validate_events(
                            manifest, envelope.events, envelope.end, allow_diagnostics=True
                        )
                    except InvalidEvents:
                        late_safety = False
                if not late_safety:
                    raise denied(422, "reactionx_stop_not_normal_completion")
            eligible = (
                metrics.eligible
                and manifest.mode != "manual"
                and self.validation_policy(manifest.mode)
            )
            reason = (
                None
                if eligible
                else "practice_only"
                if manifest.mode == "manual"
                else "quality_warning"
                if metrics.reason == "quality_warning"
                else "incomplete_result"
                if not metrics.eligible
                else "mode_unverified"
            )
            rank_columns: dict[str, int] = {}
            if eligible:
                from app.reactionx.ranking import exact_order_key

                if (
                    tuple(d.key for d in metrics.dimensions) != RULES[manifest.mode].metric_keys
                    or not 1 <= len(metrics.dimensions) <= 2
                ):
                    raise denied(422, "reactionx_unsupported_rank_dimensions")
                for index in (1, 2):
                    dimension = (
                        metrics.dimensions[index - 1] if index <= len(metrics.dimensions) else None
                    )
                    n, d, sign = (
                        (
                            dimension.numerator,
                            dimension.denominator,
                            -1 if dimension.direction == "desc" else 1,
                        )
                        if dimension
                        else (0, 1, 1)
                    )
                    exact_order_key(n, d)
                    rank_columns.update(
                        {f"rank_n{index}": n, f"rank_d{index}": d, f"rank_sign{index}": sign}
                    )
            now = self.clock()
            row = ReactionxResultReceipt(
                run_id=run.id,
                result_id=uuid4(),
                digest=digest,
                envelope=envelope.model_dump(mode="json"),
                metrics=metrics.model_dump(mode="json"),
                eligible=eligible,
                ranking_reason=reason,
                comparison_key=metrics.comparison_key,
                saved_at=now,
                **rank_columns,
            )
            self.db.add_all(
                [
                    ReactionxEvent(run_id=run.id, seq=e.seq, event=e.model_dump(mode="json"))
                    for e in envelope.events
                ]
            )
            self.db.add(row)
            if used_grant:
                grant = dict(run.upload_authorization or {})
                grant["consumed_at"] = now.isoformat()
                run.upload_authorization = grant
            if run.stop_confirmed:
                if run.status == "awaiting_sync":
                    run.status = "completed"
            else:
                if run.status != "unconfirmed":
                    run.status = "stopping"
                run.lease_expires_at = min(run.lease_expires_at, now)
            AuditService(self.db).append(
                actor_user_id=run.actor_account_id,
                action="reactionx.result.saved",
                resource_type="reactionx_result",
                resource_id=row.result_id,
                purpose=None,
                outcome="allowed",
                request_id="reactionx-result",
            )
            self.db.flush()
            return receipt_view(row)

    def receipt(self, connector: ReactionxConnector, run_id: UUID, digest: str) -> ResultReceipt:
        with atomic_write_transaction(self.db):
            self._owned(connector, run_id, digest, replay=True)
            row = self.db.get(ReactionxResultReceipt, run_id, populate_existing=True)
            if row is None:
                raise denied(404, "reactionx_receipt_not_found")
            if not hmac.compare_digest(row.digest, digest):
                raise denied(409, "reactionx_result_conflict")
            return receipt_view(row)

    def report(self, actor: AuthContext, result_id: UUID) -> ResultReport:
        target = self.db.execute(
            select(ReactionxResultReceipt, ReactionxRun)
            .join(ReactionxRun, ReactionxRun.id == ReactionxResultReceipt.run_id)
            .where(ReactionxResultReceipt.result_id == result_id)
        ).first()
        if target is None:
            raise denied(404)
        row, run = target
        require_report(ReactionxAccess(self.db, actor), run.subject_person_id)
        return ResultReport(
            receipt=receipt_view(row),
            actor_account_id=run.actor_account_id,
            subject_person_id=run.subject_person_id,
            manifest=RunManifest.model_validate(run.manifest),
            envelope=ResultEnvelope.model_validate(row.envelope),
            metrics=MetricResult.model_validate(row.metrics),
            ranking_eligible=row.eligible,
            ranking_reason=row.ranking_reason,
        )

    def list_visible(
        self,
        actor: AuthContext,
        person_id: UUID | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ResultPage:
        require_current_standard_account(self.db, actor)
        if not 1 <= limit <= 100:
            raise denied(422)
        access = ReactionxAccess(self.db, actor)
        if person_id is not None:
            require_report(access, person_id)
        now = self.clock()
        scope = {
            "actor": str(actor.account.id),
            "session": str(actor.session.id),
            "person": str(person_id) if person_id else None,
            "limit": limit,
        }
        # Domain-separated HMAC from the unexposed per-session token digest;
        # no new deployment secret, no transferable cross-session cursor.
        key = sha256(("reactionx-result-cursor-v1:" + actor.session.token_digest).encode()).digest()
        statement = (
            select(
                ReactionxResultReceipt.run_id,
                ReactionxResultReceipt.result_id,
                ReactionxResultReceipt.digest,
                ReactionxResultReceipt.saved_at,
                ReactionxResultReceipt.metrics,
                ReactionxResultReceipt.eligible,
                ReactionxResultReceipt.ranking_reason,
                ReactionxRun.subject_person_id,
                ReactionxRun.actor_account_id,
                ReactionxRun.manifest["mode"].astext.label("mode"),
                ReactionxResultReceipt.envelope["end"]["kind"].astext.label("end_kind"),
            )
            .join(ReactionxRun, ReactionxRun.id == ReactionxResultReceipt.run_id)
            .where(
                ReactionxRun.subject_person_id.in_(
                    TalentAccess(self.db, actor, now).visible_person_ids()
                )
            )
        )
        if person_id is not None:
            statement = statement.where(ReactionxRun.subject_person_id == person_id)
        if cursor:
            try:
                payload, signature = cursor.split(".")
                if len(cursor) > 2048 or not hmac.compare_digest(
                    hmac.new(key, payload.encode(), sha256).hexdigest(), signature
                ):
                    raise ValueError("signature")
                decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                if (
                    decoded["scope"] != scope
                    or not decoded["issued"] <= now.timestamp() < decoded["expires"]
                ):
                    raise ValueError("scope or expiry")
                saved_at, result_id = (
                    datetime.fromisoformat(decoded["saved_at"]),
                    UUID(decoded["result_id"]),
                )
            except (ValueError, TypeError, KeyError, UnicodeError):
                raise denied(422, "reactionx_cursor_invalid") from None
            statement = statement.where(
                or_(
                    ReactionxResultReceipt.saved_at < saved_at,
                    and_(
                        ReactionxResultReceipt.saved_at == saved_at,
                        ReactionxResultReceipt.result_id < result_id,
                    ),
                )
            )
        rows = self.db.execute(
            statement.order_by(
                ReactionxResultReceipt.saved_at.desc(), ReactionxResultReceipt.result_id.desc()
            ).limit(limit + 1)
        ).all()
        items = [
            ResultSummary(
                receipt=ResultReceipt(
                    run_id=str(row.run_id),
                    result_id=str(row.result_id),
                    digest=row.digest,
                    saved_at=row.saved_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                ),
                subject_person_id=row.subject_person_id,
                actor_account_id=row.actor_account_id,
                mode=row.mode,
                end_kind=row.end_kind,
                metrics=MetricResult.model_validate(row.metrics),
                ranking_eligible=row.eligible,
                ranking_reason=row.ranking_reason,
            )
            for row in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            payload = (
                base64.urlsafe_b64encode(
                    json.dumps(
                        {
                            "scope": scope,
                            "issued": now.timestamp(),
                            "expires": (now + timedelta(minutes=15)).timestamp(),
                            "saved_at": last.saved_at.isoformat(),
                            "result_id": str(last.result_id),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                )
                .rstrip(b"=")
                .decode()
            )
            next_cursor = payload + "." + hmac.new(key, payload.encode(), sha256).hexdigest()
        require_current_standard_account(self.db, actor)
        return ResultPage(items=items, next_cursor=next_cursor)
