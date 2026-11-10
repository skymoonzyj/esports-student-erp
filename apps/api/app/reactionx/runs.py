"""Serialized browser ownership. Time passing never proves a physical stop."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import AuditService
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
from app.reactionx.pairing import ERP_ORIGIN, PairingService
from app.reactionx.queries import (
    ACTIVE_STATES,
    HEARTBEAT_FRESH_SECONDS,
    SUPPORTED_CONNECTOR_VERSION,
    CommandView,
    ConnectorView,
    DeviceCommandRequest,
    InventoryDevice,
    LeaseView,
    PrepareRequest,
    RunView,
)
from app.reactionx.rule_versions import validate_command_for_manifest, validate_manifest
from app.reactionx.schemas import (
    CommandEnvelope,
    ManualActivatePayload,
    Mode,
    RunManifest,
    canonical_digest,
)


def failure(status: int = 409, code: str = "reactionx_state_conflict") -> ProblemError:
    return ProblemError(
        status=status, code=code, detail="ReactionX 控制权、设备状态或运行状态不允许此操作"
    )


def stamp(now: datetime) -> str:
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class RunService:
    def __init__(
        self,
        db: Session,
        *,
        origin: str = ERP_ORIGIN,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        validation_policy: Callable[[Mode], bool] | None = None,
    ) -> None:
        self.db, self.origin, self.clock = db, origin, clock
        # Only explicit server-side injection, never an HTTP/body switch.
        self.validation_policy = validation_policy or (lambda mode: mode == "manual")

    def _actor(self, account_id: UUID, session_id: UUID) -> AuthContext:
        return PairingService(self.db, origin=self.origin)._actor(account_id, session_id, lock=True)

    def _connector(self, identity: UUID) -> ReactionxConnector:
        row = self.db.scalar(
            select(ReactionxConnector)
            .where(ReactionxConnector.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.origin != self.origin or row.status != "active":
            raise failure(404)
        return row

    def _active(self, connector: ReactionxConnector) -> ReactionxRun | None:
        return self.db.scalar(
            select(ReactionxRun)
            .where(
                ReactionxRun.connector_id == connector.id, ReactionxRun.status.in_(ACTIVE_STATES)
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    def _run(self, identity: UUID) -> ReactionxRun:
        row = self.db.scalar(
            select(ReactionxRun)
            .where(ReactionxRun.id == identity)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise failure(404)
        return row

    def _clock(self, connector: ReactionxConnector, now: datetime) -> bool:
        rollback = connector.clock_seen_at is not None and now < connector.clock_seen_at
        if not rollback:
            connector.clock_seen_at = now
        return not rollback

    def _lease_ms(self, run: ReactionxRun, now: datetime) -> int:
        if run.status not in ("prepared", "ready", "starting", "running"):
            return 0
        if run.lease_observed_at is not None and now < run.lease_observed_at:
            return 0
        return max(0, min(15000, int((run.lease_expires_at - now).total_seconds() * 1000)))

    def _view(self, run: ReactionxRun, now: datetime) -> RunView:
        return RunView(
            run_id=run.id,
            connector_id=run.connector_id,
            status=run.status,
            manifest=RunManifest.model_validate(run.manifest),
            lease_expires_at=run.lease_expires_at,
            lease_remaining_ms=self._lease_ms(run, now),
            stop_confirmed=run.stop_confirmed,
            result_saved=self.db.get(ReactionxResultReceipt, run.id) is not None,
        )

    def _command_view(self, command: ReactionxCommand) -> CommandView:
        run = self.db.get(ReactionxRun, command.run_id) if command.run_id else None
        return CommandView(
            operation_id=command.operation_id,
            run_id=command.run_id,
            kind=command.kind,
            sequence=command.sequence,
            status=command.status,
            run_status=run.status if run else None,
        )

    def _online(self, connector: ReactionxConnector, now: datetime) -> bool:
        return (
            connector.heartbeat_at is not None
            and timedelta(0)
            <= now - connector.heartbeat_at
            <= timedelta(seconds=HEARTBEAT_FRESH_SECONDS)
            and connector.protocol_version == 1
            and connector.connector_version == SUPPORTED_CONNECTOR_VERSION
        )

    def _connector_view(self, connector: ReactionxConnector, now: datetime) -> ConnectorView:
        run = self._active(connector)
        unsafe = self._unsafe_identify(connector)
        return ConnectorView(
            connector_id=connector.id,
            name=connector.client_name,
            online=self._online(connector, now),
            compatible=connector.protocol_version == 1
            and connector.connector_version == SUPPORTED_CONNECTOR_VERSION,
            locally_confirmed=connector.control_tab_id is not None,
            protocol_version=connector.protocol_version,
            connector_version=connector.connector_version,
            devices=[InventoryDevice.model_validate(d) for d in connector.devices],
            active_run_id=run.id if run else None,
            attention_required=unsafe is not None
            or (run is not None and run.status == "unconfirmed"),
            unsafe_operation_id=unsafe.operation_id if unsafe else None,
            unsafe_device_keys=[unsafe.payload["device_key"]] if unsafe else [],
        )

    def _unsafe_identify(self, connector: ReactionxConnector) -> ReactionxCommand | None:
        return self.db.scalar(
            select(ReactionxCommand)
            .where(
                ReactionxCommand.connector_id == connector.id,
                ReactionxCommand.run_id.is_(None),
                ReactionxCommand.kind == "identify",
                ReactionxCommand.delivery_envelope.is_not(None),
                ReactionxCommand.status != "completed",
            )
            .order_by(ReactionxCommand.sequence)
            .limit(1)
        )

    def _idle_blocker(self, connector: ReactionxConnector) -> ReactionxCommand | None:
        return self.db.scalar(
            select(ReactionxCommand)
            .where(
                ReactionxCommand.connector_id == connector.id,
                ReactionxCommand.run_id.is_(None),
                or_(
                    ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
                    and_(
                        ReactionxCommand.kind == "identify",
                        ReactionxCommand.delivery_envelope.is_not(None),
                        ReactionxCommand.status != "completed",
                    ),
                ),
            )
            .order_by(ReactionxCommand.sequence)
            .limit(1)
        )

    def _binding(self, actor: AuthContext, connector: ReactionxConnector, tab_id: UUID) -> None:
        if (actor.account.id, actor.session.id, tab_id) != (
            connector.actor_account_id,
            connector.browser_session_id,
            connector.control_tab_id,
        ):
            raise failure(403, "reactionx_local_confirmation_required")

    def _run_binding(
        self, actor: AuthContext, run: ReactionxRun, tab_id: UUID | None = None
    ) -> None:
        if (actor.account.id, actor.session.id) != (run.actor_account_id, run.browser_session_id):
            raise failure(403)
        if tab_id is not None and tab_id != run.browser_tab_id:
            raise failure(403, "reactionx_tab_conflict")

    def _fingerprint(self, kind: str, payload: dict[str, Any]) -> str:
        return canonical_digest({"kind": kind, "request": payload})

    def _replay(
        self,
        operation: UUID,
        connector: ReactionxConnector,
        actor: AuthContext,
        tab: UUID,
        kind: str,
        fingerprint: str,
        run_id: UUID | None = None,
    ) -> ReactionxCommand | None:
        previous = self.db.scalar(
            select(ReactionxCommand)
            .where(ReactionxCommand.operation_id == operation)
            .execution_options(populate_existing=True)
        )
        if previous is None:
            return None
        if (
            previous.connector_id,
            previous.actor_account_id,
            previous.browser_tab_id,
            previous.kind,
            previous.payload_fingerprint,
        ) != (connector.id, actor.account.id, tab, kind, fingerprint) or (
            run_id is not None and previous.run_id != run_id
        ):
            raise failure(409, "reactionx_operation_conflict")
        return previous

    def _audit(self, actor_id: UUID, action: str, identity: UUID) -> None:
        AuditService(self.db).append(
            actor_user_id=actor_id,
            action=f"reactionx.{action}",
            resource_type="reactionx_run" if action.startswith("run.") else "reactionx_connector",
            resource_id=identity,
            purpose=None,
            outcome="allowed",
            request_id="reactionx-control",
        )

    def _enqueue(
        self,
        connector: ReactionxConnector,
        run: ReactionxRun | None,
        kind: str,
        payload: dict[str, Any],
        operation: UUID,
        fingerprint: str,
        tab: UUID | None,
        now: datetime,
    ) -> ReactionxCommand:
        connector.last_sequence += 1
        if run is not None:
            run.last_sequence = connector.last_sequence
        command = ReactionxCommand(
            operation_id=operation,
            run_id=run.id if run else None,
            connector_id=connector.id,
            actor_account_id=connector.actor_account_id,
            subject_person_id=run.subject_person_id if run else None,
            browser_tab_id=tab,
            sequence=connector.last_sequence,
            kind=kind,
            payload=payload,
            payload_fingerprint=fingerprint,
            expires_at=now + timedelta(seconds=10 if kind in ("start", "manual_activate") else 30),
            status="queued",
        )
        self.db.add(command)
        try:
            self.db.flush()
        except IntegrityError as error:
            if (
                getattr(error.orig, "sqlstate", None) == "23505"
                and getattr(getattr(error.orig, "diag", None), "constraint_name", None)
                == "reactionx_commands_operation_id_key"
            ):
                raise failure(409, "reactionx_operation_conflict") from None
            raise
        return command

    def _envelope(
        self, command: ReactionxCommand, run: ReactionxRun | None, now: datetime
    ) -> CommandEnvelope:
        return CommandEnvelope.model_validate(
            {
                "protocol_version": 1,
                "operation_id": str(command.operation_id),
                "run_id": str(command.run_id) if command.run_id else None,
                "actor_account_id": str(command.actor_account_id),
                "subject_person_id": str(command.subject_person_id)
                if command.subject_person_id
                else None,
                "connector_id": str(command.connector_id),
                "sequence": command.sequence,
                "expires_at": stamp(command.expires_at),
                "lease_remaining_ms": self._lease_ms(run, now) if run else 15000,
                "kind": command.kind,
                "payload": command.payload,
            }
        )

    def _stop(
        self,
        connector: ReactionxConnector,
        run: ReactionxRun,
        now: datetime,
        *,
        reason: str = "lease_expired",
    ) -> None:
        if run.status not in ACTIVE_STATES:
            return
        for row in self.db.scalars(
            select(ReactionxCommand).where(
                ReactionxCommand.run_id == run.id,
                ReactionxCommand.kind != "stop",
                ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
            )
        ):
            row.status = "cancelled"
        if run.status != "unconfirmed":
            run.status = "stopping"
        run.lease_expires_at = min(run.lease_expires_at, now)
        stop = self.db.scalar(
            select(ReactionxCommand).where(
                ReactionxCommand.run_id == run.id,
                ReactionxCommand.kind == "stop",
                ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
            )
        )
        if stop is None:
            self._enqueue(
                connector,
                run,
                "stop",
                {"reason": reason},
                uuid4(),
                canonical_digest({"safety": reason, "run": str(run.id)}),
                run.browser_tab_id,
                now,
            )
            self._audit(run.actor_account_id, "run.safety_stop", run.id)

    def _guard(
        self,
        connector: ReactionxConnector,
        run: ReactionxRun,
        now: datetime,
        *,
        authorized: bool = True,
        clock_ok: bool = True,
    ) -> bool:
        safe = (
            authorized
            and clock_ok
            and self._lease_ms(run, now) > 0
            and self._online(connector, now)
        )
        if not safe and run.status in ACTIVE_STATES:
            self._stop(
                connector, run, now, reason="lease_expired" if authorized else "disconnected"
            )
        return safe

    def prepare(
        self,
        actor: AuthContext,
        person_id: UUID,
        connector_id: UUID,
        config: PrepareRequest,
        operation_id: UUID,
    ) -> RunView:
        with atomic_write_transaction(self.db):
            actor = self._actor(actor.account.id, actor.session.id)
            ReactionxAccess(self.db, actor).require_conduct(person_id)
            connector = self._connector(connector_id)
            tab = UUID(config.tab_id)
            self._binding(actor, connector, tab)
            if (str(person_id), str(connector_id), str(operation_id)) != (
                config.person_id,
                config.connector_id,
                config.operation_id,
            ):
                raise failure(422)
            fingerprint = self._fingerprint(
                "prepare", config.model_dump(mode="json", exclude={"operation_id"})
            )
            replay = self._replay(operation_id, connector, actor, tab, "prepare", fingerprint)
            now = self.clock()
            if replay is not None:
                assert replay.run_id is not None
                replay_run = self._run(replay.run_id)
                ReactionxAccess(self.db, actor).require_manage()
                ReactionxAccess(self.db, actor).require_conduct(person_id)
                return self._view(replay_run, now)
            clock_ok = self._clock(connector, now)
            if not clock_ok or not self._online(connector, now):
                raise failure(409, "reactionx_connector_unavailable")
            if self._active(connector) is not None:
                raise failure()
            if self._idle_blocker(connector) is not None:
                raise failure(409, "reactionx_device_stop_unconfirmed")
            if not self.validation_policy(config.mode):
                raise failure(409, "reactionx_hardware_unverified")
            inventory = {d["device_key"]: d for d in connector.devices if d["connected"]}
            if any(
                d.device_key not in inventory
                or inventory[d.device_key]["protocol_version"] != d.protocol_version
                or inventory[d.device_key]["display_number"] != d.display_number
                for d in config.devices
            ):
                raise failure(409, "reactionx_devices_unavailable")
            raw = {
                "protocol_version": 1,
                "run_id": str(uuid4()),
                "actor_account_id": str(actor.account.id),
                "subject_person_id": str(person_id),
                "connector_id": str(connector.id),
                "rule_version": "reactionx-v1",
                **config.model_dump(mode="json", include={"mode", "config", "devices", "layout"}),
            }
            raw["manifest_digest"] = canonical_digest(raw)
            manifest = RunManifest.model_validate(raw)
            try:
                validate_manifest(manifest)
            except ValueError:
                raise failure(422, "reactionx_invalid_manifest") from None
            # The resource locks above may have waited past the same session's
            # expiry. Refresh final authority without reacquiring identity locks.
            ReactionxAccess(self.db, actor).require_manage()
            ReactionxAccess(self.db, actor).require_conduct(person_id)
            run = ReactionxRun(
                id=UUID(manifest.run_id),
                actor_account_id=actor.account.id,
                subject_person_id=person_id,
                connector_id=connector.id,
                browser_session_id=actor.session.id,
                browser_tab_id=tab,
                manifest=raw,
                manifest_digest=manifest.manifest_digest,
                lease_expires_at=now + timedelta(seconds=15),
                lease_observed_at=now,
                status="prepared",
                last_sequence=0,
                stop_confirmed=False,
            )
            self.db.add(run)
            self.db.flush()
            self._enqueue(
                connector, run, "prepare", {"manifest": raw}, operation_id, fingerprint, tab, now
            )
            self._audit(actor.account.id, "run.prepare", run.id)
            return self._view(run, now)

    def command(
        self,
        actor: AuthContext,
        run_id: UUID,
        kind: str,
        operation_id: UUID,
        *,
        tab_id: UUID,
        payload: ManualActivatePayload | None = None,
    ) -> CommandView:
        if kind not in ("start", "stop", "manual_activate"):
            raise failure(422)
        denied = False
        with atomic_write_transaction(self.db):
            actor = self._actor(actor.account.id, actor.session.id)
            initial = self.db.get(ReactionxRun, run_id)
            if initial is None:
                raise failure(404)
            connector = self._connector(initial.connector_id)
            run = self._run(run_id)
            self._run_binding(actor, run, tab_id)
            self._binding(actor, connector, tab_id)
            ReactionxAccess(self.db, actor).require_conduct(run.subject_person_id)
            data: dict[str, Any] = (
                {"manifest_digest": run.manifest_digest}
                if kind == "start"
                else {"reason": "user_stopped"}
                if kind == "stop"
                else payload.model_dump(mode="json")
                if payload
                else {}
            )
            fingerprint = self._fingerprint(
                kind, {"run_id": str(run.id), "tab_id": str(tab_id), "payload": data}
            )
            replay = self._replay(operation_id, connector, actor, tab_id, kind, fingerprint, run.id)
            if replay is not None:
                return self._command_view(replay)
            now = self.clock()
            clock_ok = self._clock(connector, now)
            if kind != "stop" and not self._guard(connector, run, now, clock_ok=clock_ok):
                denied = True
            else:
                if kind == "start" and (
                    run.status != "ready"
                    or not self.validation_policy(cast(Mode, run.manifest["mode"]))
                ):
                    raise failure()
                if kind == "manual_activate" and (
                    run.status != "running" or run.manifest["mode"] != "manual" or payload is None
                ):
                    raise failure()
                if kind == "stop":
                    if run.status not in ACTIVE_STATES:
                        raise failure()
                    # Cancel before enqueue; never leave an executable late start.
                    for previous in self.db.scalars(
                        select(ReactionxCommand).where(
                            ReactionxCommand.run_id == run.id,
                            ReactionxCommand.kind != "stop",
                            ReactionxCommand.status.in_(("queued", "delivered", "accepted")),
                        )
                    ):
                        previous.status = "cancelled"
                    run.status, run.lease_expires_at = "stopping", min(now, run.lease_expires_at)
                row = self._enqueue(
                    connector, run, kind, data, operation_id, fingerprint, tab_id, now
                )
                try:
                    validate_command_for_manifest(
                        self._envelope(row, run, now), RunManifest.model_validate(run.manifest)
                    )
                except ValueError:
                    raise failure(422, "reactionx_invalid_command") from None
                if kind == "start":
                    run.status = "starting"
                self._audit(actor.account.id, f"run.{kind}", run.id)
                view = self._command_view(row)
        if denied:
            raise failure(409, "reactionx_lease_expired")
        return view

    def renew_lease(self, actor: AuthContext, run_id: UUID, tab_id: UUID) -> LeaseView:
        denied = False
        with atomic_write_transaction(self.db):
            actor = self._actor(actor.account.id, actor.session.id)
            initial = self.db.get(ReactionxRun, run_id)
            if initial is None:
                raise failure(404)
            connector = self._connector(initial.connector_id)
            run = self._run(run_id)
            self._binding(actor, connector, tab_id)
            self._run_binding(actor, run, tab_id)
            ReactionxAccess(self.db, actor).require_conduct(run.subject_person_id)
            now = self.clock()
            if not self._guard(connector, run, now, clock_ok=self._clock(connector, now)):
                denied = True
            else:
                run.lease_expires_at, run.lease_observed_at = now + timedelta(seconds=15), now
                result = LeaseView(
                    run_id=run.id, lease_expires_at=run.lease_expires_at, lease_remaining_ms=15000
                )
        if denied:
            raise failure(409, "reactionx_lease_expired")
        return result

    def get(self, actor: AuthContext, run_id: UUID) -> RunView:
        with atomic_write_transaction(self.db):
            actor = self._actor(actor.account.id, actor.session.id)
            initial = self.db.get(ReactionxRun, run_id)
            if initial is None:
                raise failure(404)
            connector = self._connector(initial.connector_id)
            run = self._run(run_id)
            self._run_binding(actor, run)
            ReactionxAccess(self.db, actor).require_conduct(run.subject_person_id)
            now = self.clock()
            self._guard(connector, run, now, clock_ok=self._clock(connector, now))
            return self._view(run, now)

    def connectors(self, actor: AuthContext) -> list[ConnectorView]:
        ReactionxAccess(self.db, actor).require_manage()
        now = self.clock()
        return [
            self._connector_view(row, now)
            for row in self.db.scalars(
                select(ReactionxConnector)
                .where(
                    ReactionxConnector.actor_account_id == actor.account.id,
                    ReactionxConnector.browser_session_id == actor.session.id,
                    ReactionxConnector.status == "active",
                    ReactionxConnector.origin == self.origin,
                )
                .order_by(ReactionxConnector.created_at)
                .limit(50)
            )
        ]

    def device_command(
        self, actor: AuthContext, connector_id: UUID, command: DeviceCommandRequest
    ) -> CommandView:
        with atomic_write_transaction(self.db):
            actor = self._actor(actor.account.id, actor.session.id)
            ReactionxAccess(self.db, actor).require_conduct()
            connector = self._connector(connector_id)
            tab = UUID(command.tab_id)
            self._binding(actor, connector, tab)
            fingerprint = self._fingerprint(
                command.kind, command.model_dump(mode="json", exclude={"operation_id"})
            )
            replay = self._replay(
                UUID(command.operation_id), connector, actor, tab, command.kind, fingerprint
            )
            if replay:
                ReactionxAccess(self.db, actor).require_manage()
                ReactionxAccess(self.db, actor).require_conduct()
                return self._command_view(replay)
            now = self.clock()
            if (
                not self._clock(connector, now)
                or not self._online(connector, now)
                or self._active(connector)
            ):
                raise failure()
            if self._idle_blocker(connector) is not None:
                raise failure(409, "reactionx_device_stop_unconfirmed")
            payload = command.payload.model_dump(mode="json")
            keys = payload.get(
                "device_keys", [payload["device_key"]] if "device_key" in payload else []
            )
            inventory = {d["device_key"]: d for d in connector.devices}
            if any(key not in inventory for key in keys):
                raise failure(422, "reactionx_unknown_device")
            if command.kind == "identify" and any(not inventory[key]["connected"] for key in keys):
                raise failure()
            ReactionxAccess(self.db, actor).require_manage()
            ReactionxAccess(self.db, actor).require_conduct()
            row = self._enqueue(
                connector,
                None,
                command.kind,
                payload,
                UUID(command.operation_id),
                fingerprint,
                tab,
                now,
            )
            self._envelope(row, None, now)
            self._audit(actor.account.id, f"device.{command.kind}", connector.id)
            return self._command_view(row)
