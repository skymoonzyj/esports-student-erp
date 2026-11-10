"""Machine-only command delivery and physical acknowledgement boundaries."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.core.transactions import atomic_write_transaction
from app.reactionx.access import ReactionxAccess
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxConnector,
    ReactionxResultReceipt,
    ReactionxRun,
)
from app.reactionx.queries import (
    SUPPORTED_CONNECTOR_VERSION,
    AckRequest,
    CommandView,
    HeartbeatRequest,
    HeartbeatView,
    PollView,
)
from app.reactionx.runs import RunService, failure
from app.reactionx.schemas import CommandEnvelope


class ClientService(RunService):
    def _authorization(self, connector: ReactionxConnector) -> AuthContext | None:
        try:
            return self._actor(connector.actor_account_id, connector.browser_session_id)
        except ProblemError:
            return None

    def _conduct(self, actor: AuthContext | None, run: ReactionxRun | None) -> bool:
        if actor is None:
            return False
        try:
            ReactionxAccess(self.db, actor).require_conduct(
                run.subject_person_id if run is not None else None
            )
            if run is not None:
                self._run_binding(actor, run)
            return True
        except ProblemError:
            return False

    def heartbeat(
        self, connector: ReactionxConnector, heartbeat: HeartbeatRequest
    ) -> HeartbeatView:
        with atomic_write_transaction(self.db):
            actor = self._authorization(connector)
            connector = self._connector(connector.id)
            run = self._active(connector)
            authorized = self._conduct(actor, run)
            now = self.clock()
            clock_ok = self._clock(connector, now)
            instance = UUID(heartbeat.instance_id)
            changed = connector.instance_id is not None and connector.instance_id != instance
            was_online = self._online(connector, now)
            compatible = (
                heartbeat.protocol_version == 1
                and heartbeat.connector_version == SUPPORTED_CONNECTOR_VERSION
            )
            if authorized and clock_ok:
                connector.instance_id = instance
                connector.protocol_version = heartbeat.protocol_version
                connector.connector_version = heartbeat.connector_version
                connector.heartbeat_at = now if compatible else None
                connector.devices = [d.model_dump(mode="json") for d in heartbeat.devices]
            if run is not None:
                available = {d.device_key: d for d in heartbeat.devices if d.connected}
                missing = any(
                    d["device_key"] not in available
                    or available[d["device_key"]].protocol_version != d["protocol_version"]
                    for d in run.manifest["devices"]
                )
                if changed or missing or not compatible or not was_online:
                    self._stop(connector, run, now, reason="disconnected")
                self._guard(connector, run, now, authorized=authorized, clock_ok=clock_ok)
            # Reconnection invalidates pending unbound work even when the same
            # process survived the offline gap. Delivered identify remains unsafe.
            if changed or not authorized or not compatible or not clock_ok or not was_online:
                for row in self.db.scalars(
                    select(ReactionxCommand).where(
                        ReactionxCommand.connector_id == connector.id,
                        ReactionxCommand.run_id.is_(None),
                        ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
                    )
                ):
                    row.status = (
                        "failed"
                        if row.kind == "identify" and row.delivery_envelope is not None
                        else "cancelled"
                    )
            return HeartbeatView(
                connector=self._connector_view(connector, now),
                run=self._view(run, now) if run else None,
                safety_only=not authorized,
            )

    def _deliver(
        self, command: ReactionxCommand, run: ReactionxRun | None, now: datetime
    ) -> CommandEnvelope:
        if command.delivery_envelope is None:
            command.delivery_envelope = self._envelope(command, run, now).model_dump(mode="json")
        command.status = "delivered" if command.status == "queued" else command.status
        # The envelope bytes/fingerprint are immutable across retry. Authorization
        # remaining time is separate in PollView and can only reduce execution TTL.
        return CommandEnvelope.model_validate(command.delivery_envelope)

    def poll(self, connector: ReactionxConnector, cursor: int = 0) -> PollView:
        # Cursor is a progress hint, NEVER a filter/permission or stop bypass.
        if cursor < 0:
            raise failure(422)
        with atomic_write_transaction(self.db):
            actor = self._authorization(connector)
            connector = self._connector(connector.id)
            run = self._active(connector)
            now = self.clock()
            authorized = self._conduct(actor, run)
            clock_ok = self._clock(connector, now)
            if run:
                self._guard(connector, run, now, authorized=authorized, clock_ok=clock_ok)
            commands: list[CommandEnvelope] = []
            rows = list(
                self.db.scalars(
                    select(ReactionxCommand)
                    .where(
                        ReactionxCommand.connector_id == connector.id,
                        ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
                    )
                    .order_by(ReactionxCommand.sequence)
                )
            )
            for row in rows:
                if row.kind == "stop":
                    if run is not None and row.run_id == run.id:
                        if row.expires_at <= now:
                            row.status = "expired"
                            self._stop(connector, run, now)
                        else:
                            commands = [self._deliver(row, run, now)]
                            break
                    continue
                if not authorized or not clock_ok or not self._online(connector, now):
                    row.status = (
                        "failed"
                        if row.kind == "identify" and row.delivery_envelope is not None
                        else "cancelled"
                    )
                    continue
                if run is not None and row.run_id != run.id:
                    row.status = "cancelled"
                    continue
                if (
                    row.kind == "start"
                    and row.ack_payload is not None
                    and row.ack_payload.get("run_state") == "running"
                ):
                    # Acceptance TTL is not a training-duration limit. Ongoing
                    # execution is bounded by the separately renewable run lease.
                    continue
                if row.expires_at <= now:
                    row.status = (
                        "failed"
                        if row.kind == "identify" and row.delivery_envelope is not None
                        else "expired"
                    )
                    if run is not None and row.run_id == run.id:
                        self._stop(connector, run, now)
                    continue
                if row.status != "queued":
                    continue  # consumed start/prepare/manual commands never replay
                if row.run_id is not None and (
                    run is None or run.status in ("stopping", "unconfirmed")
                ):
                    row.status = "cancelled"
                    continue
                commands = [self._deliver(row, run if row.run_id else None, now)]
                break
            # Stop generated while expiring a command wins in the same response.
            if run is not None and run.status in ("stopping", "unconfirmed"):
                stop = self.db.scalar(
                    select(ReactionxCommand)
                    .where(
                        ReactionxCommand.run_id == run.id,
                        ReactionxCommand.kind == "stop",
                        ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
                        ReactionxCommand.expires_at > now,
                    )
                    .order_by(ReactionxCommand.sequence.desc())
                )
                commands = [self._deliver(stop, run, now)] if stop is not None else []
            return PollView(
                commands=commands,
                cursor=connector.last_sequence,
                poll_after_ms=500 if run else 2000,
                run=self._view(run, now) if run else None,
                lease_remaining_ms=self._lease_ms(run, now)
                if run
                else 15000
                if authorized and clock_ok and self._online(connector, now)
                else 0,
            )

    def ack(
        self, connector: ReactionxConnector, operation_id: UUID, ack: AckRequest
    ) -> CommandView:
        denied = False
        with atomic_write_transaction(self.db):
            actor = self._authorization(connector)
            connector = self._connector(connector.id)
            row = self.db.scalar(
                select(ReactionxCommand)
                .where(
                    ReactionxCommand.operation_id == operation_id,
                    ReactionxCommand.connector_id == connector.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if row is None:
                raise failure(404)
            if row.delivery_envelope is None:
                raise failure(409, "reactionx_command_not_delivered")
            run = self._run(row.run_id) if row.run_id else None
            now = self.clock()
            authorized = self._conduct(actor, run)
            clock_ok = self._clock(connector, now)
            allowed = authorized and clock_ok and self._online(connector, now)
            if run:
                allowed = self._guard(connector, run, now, authorized=authorized, clock_ok=clock_ok)
                # OFF may have arrived before the receipt. A repeated ACK can
                # reconcile saved state, but a receipt alone never releases an
                # unsafe active run or invents physical confirmation.
                if (
                    run.stop_confirmed
                    and run.status == "awaiting_sync"
                    and self.db.get(ReactionxResultReceipt, run.id) is not None
                ):
                    run.status = "completed"
            body = ack.model_dump(mode="json")
            if row.ack_payload == body:
                return self._command_view(row)
            terminal = ack.run_state == "stopped_confirmed"
            if run is not None and ack.run_state in ("ready", "running") and row.expires_at <= now:
                self._stop(connector, run, now)
                denied = True
            # Only terminal OFF or failure diagnostics survive expired authority.
            if (
                not allowed
                and not terminal
                and ack.run_state != "stopped_unconfirmed"
                and ack.status != "failed"
            ):
                if ack.status == "accepted" and ack.run_state is None and row.kind == "stop":
                    pass  # transport receipt is harmless, never changes run state
                else:
                    denied = True
            if not denied:
                if run is None:
                    if row.kind == "identify":
                        if terminal:
                            if ack.off_confirmed_device_keys != [row.payload["device_key"]]:
                                raise failure(422, "reactionx_incomplete_off_evidence")
                        elif ack.status == "completed" or ack.run_state in ("ready", "running"):
                            raise failure(422, "reactionx_terminal_evidence_required")
                    elif ack.run_state is not None:
                        raise failure(422)
                if run is not None:
                    if terminal:
                        if row.kind not in ("prepare", "start", "stop"):
                            raise failure(422)
                        if (
                            row.kind == "prepare"
                            and self.db.scalar(
                                select(ReactionxCommand.id)
                                .where(
                                    ReactionxCommand.run_id == run.id,
                                    ReactionxCommand.kind == "start",
                                )
                                .limit(1)
                            )
                            is not None
                        ):
                            raise failure(409, "reactionx_stale_terminal_evidence")
                        if set(ack.off_confirmed_device_keys) != {
                            d["device_key"] for d in run.manifest["devices"]
                        }:
                            raise failure(422, "reactionx_incomplete_off_evidence")
                        if not run.stop_confirmed:
                            delivered_start = self.db.scalar(
                                select(ReactionxCommand.id)
                                .where(
                                    ReactionxCommand.run_id == run.id,
                                    ReactionxCommand.kind == "start",
                                    ReactionxCommand.delivery_envelope.is_not(None),
                                )
                                .limit(1)
                            )
                            # A delivered start might have executed even if its
                            # ACK/result was lost. Only durable non-delivery plus
                            # real OFF evidence establishes no result is expected.
                            terminal_status = "cancelled"
                            if delivered_start is not None:
                                terminal_status = (
                                    "completed"
                                    if self.db.get(ReactionxResultReceipt, run.id) is not None
                                    else "awaiting_sync"
                                )
                            run.status, run.stop_confirmed, run.ended_at = (
                                terminal_status,
                                True,
                                now,
                            )
                            run.lease_expires_at = min(run.lease_expires_at, now)
                            for pending in self.db.scalars(
                                select(ReactionxCommand).where(
                                    ReactionxCommand.run_id == run.id,
                                    ReactionxCommand.id != row.id,
                                    ReactionxCommand.status.in_(
                                        ("queued", "delivered", "accepted")
                                    ),
                                )
                            ):
                                pending.status = "cancelled"
                            self._audit(run.actor_account_id, "run.stop_confirmed", run.id)
                    elif ack.run_state == "ready":
                        if (
                            row.kind != "prepare"
                            or row.status not in ("delivered", "accepted")
                            or run.status != "prepared"
                        ):
                            raise failure()
                        run.status = "ready"
                    elif ack.run_state == "running":
                        if (
                            row.kind != "start"
                            or row.status not in ("delivered", "accepted")
                            or run.status != "starting"
                        ):
                            raise failure()
                        run.status = "running"
                    elif ack.status == "failed":
                        if not run.stop_confirmed:
                            run.status = "unconfirmed"
                            self._stop(connector, run, now, reason="disconnected")
                    elif ack.status == "completed":
                        # A generic completed ACK is not a physical terminal ACK.
                        if row.kind != "manual_activate":
                            raise failure(422, "reactionx_terminal_evidence_required")
                    elif row.kind != "stop" and row.status in ("cancelled", "expired"):
                        raise failure()
                if row.status == "completed" and not terminal:
                    raise failure(409, "reactionx_ack_conflict")
                row.status, row.ack_payload, row.acknowledged_at = ack.status, body, now
                result = self._command_view(row)
        if denied:
            raise failure(409, "reactionx_authority_expired")
        return result
