import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.core.errors import ProblemError
from app.identity.models import Person
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from app.reactionx.commands import ClientService
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxConnector,
    ReactionxResultReceipt,
    ReactionxRun,
)
from app.reactionx.pairing import ERP_ORIGIN
from app.reactionx.queries import AckRequest, DeviceCommandRequest, HeartbeatRequest, PrepareRequest
from app.reactionx.runs import RunService
from app.reactionx.schemas import ManualActivatePayload


def configuration(mode="manual"):
    fixtures = json.loads(
        (Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    manifest = next(c["manifest"] for c in fixtures["cases"] if c["name"] == mode)
    return {key: manifest[key] for key in ("mode", "config", "devices", "layout")}


def seed(db):
    person = Person(
        full_name="synthetic-rx-owner",
        identity_ciphertext="synthetic",
        identity_digest=uuid4().hex,
        sex="male",
        date_of_birth=date(2000, 1, 1),
    )
    db.add(person)
    db.flush()
    account = UserAccount(
        person_id=person.id,
        password_hash="synthetic-unused",
        status="active",
        force_password_change=False,
    )
    db.add(account)
    db.flush()
    session = AuthSession(
        user_id=account.id,
        token_digest=uuid4().hex * 2,
        csrf_digest=uuid4().hex * 2,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add_all([session, UserRole(user_id=account.id, role_id=ROLES["super_admin"].id)])
    db.flush()
    actor = AuthContext(account=account, person=person, session=session)
    connector = ReactionxConnector(
        actor_account_id=account.id,
        browser_session_id=session.id,
        credential_digest=uuid4().hex * 2,
        origin=ERP_ORIGIN,
        client_name="synthetic-device",
        status="active",
        control_tab_id=uuid4(),
    )
    db.add(connector)
    db.commit()
    return actor, connector


@pytest.fixture
def rig(db):
    actor, connector = seed(db)
    now = [datetime.now(UTC)]
    runs = RunService(db, clock=lambda: now[0])
    client = ClientService(db, clock=lambda: now[0])
    beat = HeartbeatRequest.model_validate(
        {
            "protocol_version": 1,
            "connector_version": "0.3.0",
            "instance_id": str(uuid4()),
            "devices": [
                {**device, "label": "synthetic", "connected": True}
                for device in configuration()["devices"]
            ],
        }
    )
    client.heartbeat(connector, beat)
    return actor, connector, runs, client, now, beat


def prepare(rig, *, mode="manual"):
    actor, connector, runs, _, _, _ = rig
    config = PrepareRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "person_id": str(actor.person.id),
            "connector_id": str(connector.id),
            "tab_id": str(connector.control_tab_id),
            **configuration(mode),
        }
    )
    return runs.prepare(actor, actor.person.id, connector.id, config, UUID(config.operation_id))


def acknowledge(client, connector, operation, state, *, status="completed", keys=None):
    return client.ack(
        connector,
        UUID(str(operation)),
        AckRequest(status=status, run_state=state, off_confirmed_device_keys=keys or []),
    )


def ready(rig):
    run = prepare(rig)
    _, connector, _, client, _, _ = rig
    delivered = client.poll(connector).commands[0]
    acknowledge(client, connector, delivered.operation_id, "ready")
    return run, delivered


def test_second_tab_cannot_renew_but_refresh_read_does_not_extend_lease(rig):
    actor, connector, runs, _, now, _ = rig
    run, _ = ready(rig)
    expires = run.lease_expires_at
    now[0] += timedelta(seconds=1)
    assert runs.get(actor, run.run_id).lease_expires_at == expires
    with pytest.raises(ProblemError) as denied:
        runs.renew_lease(actor, run.run_id, uuid4())
    assert denied.value.status == 403
    lease = runs.renew_lease(actor, run.run_id, connector.control_tab_id)
    assert lease.lease_remaining_ms == 15000 and lease.lease_expires_at == expires + timedelta(
        seconds=1
    )


@pytest.mark.parametrize("delta", [timedelta(seconds=15), timedelta(seconds=-1)])
def test_lease_expiry_and_clock_rollback_do_not_revive_ownership(db, rig, delta):
    actor, connector, runs, client, now, beat = rig
    run, _ = ready(rig)
    original = now[0]
    now[0] += delta
    with pytest.raises(ProblemError):
        runs.renew_lease(actor, run.run_id, connector.control_tab_id)
    row = db.get(ReactionxRun, run.run_id)
    assert row.status == "stopping" and not row.stop_confirmed
    first = client.poll(connector)
    assert first.lease_remaining_ms == 0 and first.commands[0].kind == "stop"
    now[0] = original + timedelta(seconds=16)
    client.heartbeat(connector, beat)
    with pytest.raises(ProblemError):
        runs.renew_lease(actor, run.run_id, connector.control_tab_id)
    assert db.get(ReactionxRun, run.run_id).status == "stopping"
    with pytest.raises(ProblemError):
        prepare(rig)


def test_rollback_after_poll_fails_closed_and_stop_envelope_is_immutable(rig):
    actor, connector, runs, client, now, _ = rig
    run, _ = ready(rig)
    now[0] += timedelta(seconds=4)
    client.poll(connector)
    now[0] -= timedelta(milliseconds=1)
    first = client.poll(connector)
    assert first.commands[0].kind == "stop" and first.lease_remaining_ms == 0
    now[0] += timedelta(seconds=1)
    second = client.poll(connector)
    assert second.commands[0].model_dump_json() == first.commands[0].model_dump_json()
    with pytest.raises(ProblemError):
        runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)


def test_fresh_heartbeat_after_observed_offline_gap_cannot_resume_run(rig):
    actor, connector, runs, client, now, beat = rig
    run, _ = ready(rig)
    start = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    client.poll(connector)
    acknowledge(client, connector, start.operation_id, "running", status="accepted")
    now[0] += timedelta(seconds=11)
    response = client.heartbeat(connector, beat)
    assert response.run.status == "stopping"
    assert client.poll(connector).commands[0].kind == "stop"
    with pytest.raises(ProblemError):
        runs.renew_lease(actor, run.run_id, connector.control_tab_id)


def test_old_prepare_off_ack_cannot_release_running_run(db, rig):
    actor, connector, runs, client, _, _ = rig
    run, preparation = ready(rig)
    operation = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    client.poll(connector)
    acknowledge(client, connector, operation.operation_id, "running", status="accepted")
    with pytest.raises(ProblemError) as denied:
        acknowledge(
            client,
            connector,
            preparation.operation_id,
            "stopped_confirmed",
            keys=[d.device_key for d in run.manifest.devices],
        )
    assert denied.value.status == 409
    assert db.get(ReactionxRun, run.run_id).status == "running"


@pytest.mark.parametrize(
    "phase,expected",
    [
        ("no_start", "cancelled"),
        ("queued", "cancelled"),
        ("delivered", "awaiting_sync"),
        ("accepted", "awaiting_sync"),
    ],
)
def test_verified_unstarted_cancellation_does_not_invent_pending_result(db, rig, phase, expected):
    actor, connector, runs, client, _, _ = rig
    run, preparation = ready(rig)
    if phase != "no_start":
        start = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
        if phase in ("delivered", "accepted"):
            assert client.poll(connector).commands[0].operation_id == str(start.operation_id)
        if phase == "accepted":
            acknowledge(client, connector, start.operation_id, "running", status="accepted")
    stop = runs.command(actor, run.run_id, "stop", uuid4(), tab_id=connector.control_tab_id)
    assert runs.get(actor, run.run_id).status == "stopping"
    with pytest.raises(ProblemError):
        prepare(rig)
    assert client.poll(connector).commands[0].operation_id == str(stop.operation_id)
    keys = [device.device_key for device in run.manifest.devices]
    if phase != "no_start":
        with pytest.raises(ProblemError) as stale:
            acknowledge(client, connector, preparation.operation_id, "stopped_confirmed", keys=keys)
        assert stale.value.code == "reactionx_stale_terminal_evidence"
    response = acknowledge(client, connector, stop.operation_id, "stopped_confirmed", keys=keys)
    assert response.run_status == expected
    view = runs.get(actor, run.run_id)
    assert view.status == expected and view.stop_confirmed and not view.result_saved
    assert view.lease_remaining_ms == 0
    acknowledge(client, connector, stop.operation_id, "stopped_confirmed", keys=keys)
    assert db.get(ReactionxRun, run.run_id).status == expected
    assert prepare(rig).status == "prepared"


def test_no_start_before_prepare_ack_and_no_ack_before_delivery(rig):
    actor, connector, runs, client, _, _ = rig
    run = prepare(rig)
    with pytest.raises(ProblemError):
        runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    operation = runs.db.scalar(
        select(ReactionxCommand).where(ReactionxCommand.run_id == run.run_id)
    )
    with pytest.raises(ProblemError) as denied:
        acknowledge(client, connector, operation.operation_id, "ready")
    assert denied.value.code == "reactionx_command_not_delivered"


def test_start_acceptance_ttl_does_not_limit_running_run_with_current_lease(rig):
    actor, connector, runs, client, now, beat = rig
    run, _ = ready(rig)
    start = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    client.poll(connector)
    acknowledge(client, connector, start.operation_id, "running", status="accepted")
    for _ in range(8):
        now[0] += timedelta(seconds=5)
        client.heartbeat(connector, beat)
        runs.renew_lease(actor, run.run_id, connector.control_tab_id)
        response = client.poll(connector)
        assert response.commands == []
        assert response.run.status == "running" and response.lease_remaining_ms == 15000
    acknowledge(
        client,
        connector,
        start.operation_id,
        "stopped_confirmed",
        keys=[d.device_key for d in run.manifest.devices],
    )
    assert runs.get(actor, run.run_id).status == "awaiting_sync"


def test_late_first_running_ack_cannot_authorize_an_expired_start(db, rig):
    actor, connector, runs, client, now, beat = rig
    run, _ = ready(rig)
    start = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    client.poll(connector)
    now[0] += timedelta(seconds=10)
    client.heartbeat(connector, beat)
    with pytest.raises(ProblemError):
        acknowledge(client, connector, start.operation_id, "running", status="accepted")
    assert db.get(ReactionxRun, run.run_id).status == "stopping"
    assert client.poll(connector).commands[0].kind == "stop"


@pytest.mark.parametrize(
    "status", ["prepared", "ready", "starting", "running", "stopping", "unconfirmed"]
)
def test_database_keeps_unique_ownership_even_for_failed_off(db, rig, status):
    from sqlalchemy.exc import IntegrityError

    actor, connector, _, _, now, _ = rig
    run = prepare(rig)
    original = db.get(ReactionxRun, run.run_id)
    original.status = status
    db.commit()
    with pytest.raises(IntegrityError), db.begin_nested():
        db.add(
            ReactionxRun(
                actor_account_id=actor.account.id,
                subject_person_id=actor.person.id,
                connector_id=connector.id,
                browser_session_id=actor.session.id,
                browser_tab_id=uuid4(),
                manifest=original.manifest,
                manifest_digest=original.manifest_digest,
                lease_expires_at=now[0],
                status="prepared",
            )
        )
        db.flush()


def test_same_account_new_session_cannot_rebind_old_run(db, rig):
    actor, connector, runs, _, _, _ = rig
    run, _ = ready(rig)
    old_manifest = run.manifest.model_dump_json()
    session = AuthSession(
        user_id=actor.account.id,
        token_digest=uuid4().hex * 2,
        csrf_digest=uuid4().hex * 2,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db.add(session)
    db.commit()
    other = AuthContext(account=actor.account, person=actor.person, session=session)
    with pytest.raises(ProblemError) as denied:
        runs.renew_lease(other, run.run_id, connector.control_tab_id)
    assert denied.value.status == 403
    assert runs.get(actor, run.run_id).manifest.model_dump_json() == old_manifest


def test_manual_activation_requires_running_manual_manifest_and_registered_key(rig):
    actor, connector, runs, client, _, _ = rig
    run, _ = ready(rig)
    payload = ManualActivatePayload(device_key=run.manifest.devices[0].device_key, color=1)
    with pytest.raises(ProblemError):
        runs.command(
            actor,
            run.run_id,
            "manual_activate",
            uuid4(),
            tab_id=connector.control_tab_id,
            payload=payload,
        )
    start = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    client.poll(connector)
    acknowledge(client, connector, start.operation_id, "running", status="accepted")
    with pytest.raises(ProblemError):
        runs.command(
            actor,
            run.run_id,
            "manual_activate",
            uuid4(),
            tab_id=connector.control_tab_id,
            payload=ManualActivatePayload(device_key="unknown", color=1),
        )
    activation = runs.command(
        actor,
        run.run_id,
        "manual_activate",
        uuid4(),
        tab_id=connector.control_tab_id,
        payload=payload,
    )
    assert client.poll(connector).commands[0].operation_id == str(activation.operation_id)
    assert acknowledge(client, connector, activation.operation_id, None).status == "completed"


@pytest.mark.parametrize("unsupported", ["version", "stale", "protocol"])
def test_stale_and_incompatible_handshake_never_appears_ready_or_starts(rig, unsupported):
    actor, connector, runs, client, now, beat = rig
    if unsupported == "stale":
        now[0] += timedelta(seconds=10, microseconds=1)
    else:
        change = (
            {"connector_version": "0.4.0"} if unsupported == "version" else {"protocol_version": 2}
        )
        client.heartbeat(connector, beat.model_copy(update=change))
    assert runs.connectors(actor)[0].online is False
    with pytest.raises(ProblemError):
        prepare(rig)


def test_expired_start_is_cancelled_and_expired_stop_reissues_without_freeing_run(db, rig):
    actor, connector, runs, client, now, beat = rig
    run, _ = ready(rig)
    start = runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    now[0] += timedelta(seconds=10)
    client.heartbeat(connector, beat)
    polled = client.poll(connector)
    assert [c.kind for c in polled.commands] == ["stop"]
    assert (
        db.scalar(
            select(ReactionxCommand).where(ReactionxCommand.operation_id == start.operation_id)
        ).status
        == "expired"
    )
    old_stop = polled.commands[0]
    now[0] += timedelta(seconds=30)
    new = client.poll(connector).commands[0]
    assert new.kind == "stop" and new.operation_id != old_stop.operation_id
    assert db.get(ReactionxRun, run.run_id).status == "stopping"


def test_foreign_connector_cannot_ack_or_observe_run(db, rig):
    _, connector, _, client, _, _ = rig
    run, preparation = ready(rig)
    _, other = seed(db)
    assert client.poll(other).run is None
    with pytest.raises(ProblemError) as denied:
        acknowledge(client, other, preparation.operation_id, "ready")
    assert denied.value.status == 404
    assert client.poll(connector).run.run_id == run.run_id


def identify(rig):
    actor, connector, runs, _, _, _ = rig
    command = DeviceCommandRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "tab_id": str(connector.control_tab_id),
            "kind": "identify",
            "payload": {
                "device_key": configuration()["devices"][0]["device_key"],
                "color": 1,
                "duration_ms": 1000,
            },
        }
    )
    return runs.device_command(actor, connector.id, command)


@pytest.mark.parametrize("kind", ["scan", "connect", "disconnect", "identify"])
def test_same_instance_reconnect_cancels_never_delivered_idle_work(db, rig, kind):
    actor, connector, runs, client, now, beat = rig
    key = configuration()["devices"][0]["device_key"]
    payload = (
        {"duration_ms": 1000}
        if kind == "scan"
        else {"device_key": key, "color": 1, "duration_ms": 1000}
        if kind == "identify"
        else {"device_keys": [key]}
    )
    request = DeviceCommandRequest.model_validate(
        {
            "operation_id": str(uuid4()),
            "tab_id": str(connector.control_tab_id),
            "kind": kind,
            "payload": payload,
        }
    )
    queued = runs.device_command(actor, connector.id, request)
    now[0] += timedelta(seconds=11)  # no intervening poll; command TTL is still valid
    assert client.heartbeat(connector, beat).connector.online is True
    assert client.poll(connector).commands == []
    row = db.scalar(
        select(ReactionxCommand).where(ReactionxCommand.operation_id == queued.operation_id)
    )
    assert row.status == "cancelled" and row.delivery_envelope is None
    assert runs.device_command(actor, connector.id, request).status == "cancelled"
    assert client.poll(connector).commands == []


@pytest.mark.parametrize("receipt_first", [True, False])
def test_receipt_and_verified_off_converge_without_freeing_unsafe_run(db, rig, receipt_first):
    actor, connector, runs, client, _, _ = rig
    run, _ = ready(rig)
    runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    assert client.poll(connector).commands[0].kind == "start"
    stop = runs.command(actor, run.run_id, "stop", uuid4(), tab_id=connector.control_tab_id)
    assert client.poll(connector).commands[0].operation_id == str(stop.operation_id)
    keys = [device.device_key for device in run.manifest.devices]

    def persist_receipt():
        db.add(
            ReactionxResultReceipt(
                run_id=run.run_id,
                digest="a" * 64,
                envelope={},
                metrics={},
                eligible=False,
                saved_at=datetime.now(UTC),
            )
        )
        db.commit()

    if receipt_first:
        persist_receipt()
        pending = runs.get(actor, run.run_id)
        assert pending.status == "stopping" and pending.result_saved and not pending.stop_confirmed
        with pytest.raises(ProblemError):
            prepare(rig)
        acknowledge(client, connector, stop.operation_id, "stopped_unconfirmed", status="failed")
        unsafe = runs.get(actor, run.run_id)
        assert unsafe.status == "unconfirmed" and unsafe.result_saved and not unsafe.stop_confirmed
    else:
        first = acknowledge(client, connector, stop.operation_id, "stopped_confirmed", keys=keys)
        assert first.run_status == "awaiting_sync"
        assert not runs.get(actor, run.run_id).result_saved
        persist_receipt()
    confirmed = acknowledge(client, connector, stop.operation_id, "stopped_confirmed", keys=keys)
    assert confirmed.run_status == "completed"
    final = runs.get(actor, run.run_id)
    assert final.status == "completed" and final.stop_confirmed and final.result_saved
    assert prepare(rig).status == "prepared"


@pytest.mark.parametrize(
    "transition", ["queued", "delivered", "failed", "restart", "timeout", "offline"]
)
def test_idle_identify_serializes_and_holds_unsafe_ownership_until_exact_off(rig, transition):
    actor, connector, runs, client, now, beat = rig
    identification = identify(rig)
    if transition != "queued":
        assert client.poll(connector).commands[0].kind == "identify"
    if transition == "failed":
        acknowledge(
            client, connector, identification.operation_id, "stopped_unconfirmed", status="failed"
        )
    elif transition == "restart":
        client.heartbeat(connector, beat.model_copy(update={"instance_id": str(uuid4())}))
    elif transition == "timeout":
        now[0] += timedelta(seconds=31)
        client.heartbeat(connector, beat)
        client.poll(connector)
    elif transition == "offline":
        now[0] += timedelta(seconds=11)
        client.heartbeat(connector, beat)
        assert client.poll(connector).commands == []
    with pytest.raises(ProblemError):
        prepare(rig)
    with pytest.raises(ProblemError):
        identify(rig)
    if transition == "queued":
        client.poll(connector)
    with pytest.raises(ProblemError):
        acknowledge(client, connector, identification.operation_id, None)
    with pytest.raises(ProblemError):
        acknowledge(
            client, connector, identification.operation_id, "stopped_confirmed", keys=["wrong"]
        )
    if transition != "queued":
        assert runs.connectors(actor)[0].attention_required is True
        assert runs.connectors(actor)[0].unsafe_device_keys == [
            configuration()["devices"][0]["device_key"]
        ]
    acknowledge(
        client,
        connector,
        identification.operation_id,
        "stopped_confirmed",
        keys=[configuration()["devices"][0]["device_key"]],
    )
    assert runs.connectors(actor)[0].attention_required is False
    assert runs.connectors(actor)[0].unsafe_device_keys == []
    assert prepare(rig).status == "prepared"


def test_idle_identify_rechecks_conduct_permission_before_delivery_and_acceptance(db, rig):
    actor, connector, _, client, _, _ = rig
    pending = identify(rig)
    db.add(
        UserPermissionOverride(
            user_id=actor.account.id,
            permission_id=PERMISSIONS["reactionx.conduct"].id,
            effect="deny",
            scope_kind="global",
        )
    )
    db.commit()
    assert client.poll(connector).commands == []
    assert (
        db.scalar(
            select(ReactionxCommand).where(ReactionxCommand.operation_id == pending.operation_id)
        ).status
        == "cancelled"
    )
    with pytest.raises(ProblemError) as denied:
        identify(rig)
    assert denied.value.status == 403


def test_concurrent_independent_connections_start_exactly_once(postgres_engine):
    barrier = Barrier(2)
    with Session(postgres_engine) as db:
        actor, connector = seed(db)
        account_id, person_id, session_id = actor.account.id, actor.person.id, actor.session.id
        connector_id, tab = connector.id, connector.control_tab_id
        now = datetime.now(UTC)
        client = ClientService(db, clock=lambda: now)
        client.heartbeat(
            connector,
            HeartbeatRequest.model_validate(
                {
                    "protocol_version": 1,
                    "connector_version": "0.3.0",
                    "instance_id": str(uuid4()),
                    "devices": [
                        {**d, "label": "synthetic", "connected": True}
                        for d in configuration()["devices"]
                    ],
                }
            ),
        )
        rig = actor, connector, RunService(db, clock=lambda: now), client, [now], None
        run, _ = ready(rig)
        run_id = run.run_id

    def start():
        with Session(postgres_engine) as db:
            actor = AuthContext(
                account=db.get(UserAccount, account_id),
                person=db.get(Person, person_id),
                session=db.get(AuthSession, session_id),
            )
            backend = db.scalar(text("SELECT pg_backend_pid()"))
            barrier.wait(timeout=10)
            try:
                RunService(db).command(actor, run_id, "start", uuid4(), tab_id=tab)
                return backend, 202
            except ProblemError as error:
                return backend, error.status

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start) for _ in range(2)]
            outcomes = [future.result(timeout=20) for future in futures]
        assert len({backend for backend, _ in outcomes}) == 2
        assert sorted(status for _, status in outcomes) == [202, 409]
        with Session(postgres_engine) as db:
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ReactionxCommand)
                    .where(ReactionxCommand.run_id == run_id, ReactionxCommand.kind == "start")
                )
                == 1
            )
            assert db.get(ReactionxRun, run_id).status == "starting"
    finally:
        with Session(postgres_engine) as db, db.begin():
            database = db.scalar(text("SELECT current_database()"))
            assert database == "erp_e2e" or database.endswith("_test")
            db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == account_id))
            db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            db.execute(
                delete(ReactionxCommand).where(ReactionxCommand.connector_id == connector_id)
            )
            db.execute(delete(ReactionxRun).where(ReactionxRun.connector_id == connector_id))
            db.execute(delete(ReactionxConnector).where(ReactionxConnector.id == connector_id))
            db.execute(delete(UserRole).where(UserRole.user_id == account_id))
            db.execute(delete(AuthSession).where(AuthSession.id == session_id))
            db.execute(delete(UserAccount).where(UserAccount.id == account_id))
            db.execute(delete(Person).where(Person.id == person_id))


@pytest.mark.parametrize(
    "operation", ["revoke", "prepare", "prepare_replay", "device", "device_replay"]
)
def test_resource_lock_wait_cannot_outlive_naturally_expiring_session(postgres_engine, operation):
    from threading import Event
    from time import monotonic, sleep

    from app.reactionx.pairing import PairingService

    entered, worker_pid = Event(), []
    with Session(postgres_engine) as db:
        actor, connector = seed(db)
        account_id, person_id, session_id = actor.account.id, actor.person.id, actor.session.id
        connector_id, tab = connector.id, connector.control_tab_id
        ClientService(db).heartbeat(
            connector,
            HeartbeatRequest.model_validate(
                {
                    "protocol_version": 1,
                    "connector_version": "0.3.0",
                    "instance_id": str(uuid4()),
                    "devices": [
                        {**d, "label": "synthetic", "connected": True}
                        for d in configuration()["devices"]
                    ],
                }
            ),
        )
        operation_id = uuid4()
        config = PrepareRequest.model_validate(
            {
                "operation_id": str(operation_id),
                "person_id": str(person_id),
                "connector_id": str(connector_id),
                "tab_id": str(tab),
                **configuration(),
            }
        )
        device = DeviceCommandRequest.model_validate(
            {
                "operation_id": str(operation_id),
                "tab_id": str(tab),
                "kind": "scan",
                "payload": {"duration_ms": 2000},
            }
        )
        if operation == "prepare_replay":
            RunService(db).prepare(actor, person_id, connector_id, config, operation_id)
        if operation == "device_replay":
            RunService(db).device_command(actor, connector_id, device)
        baseline = (
            db.scalar(
                select(func.count())
                .select_from(ReactionxRun)
                .where(ReactionxRun.connector_id == connector_id)
            ),
            db.scalar(
                select(func.count())
                .select_from(ReactionxCommand)
                .where(ReactionxCommand.connector_id == connector_id)
            ),
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == account_id)
            ),
        )
        # No clock mock: expire naturally only AFTER an actual independent
        # PostgreSQL waiter has acquired identity locks and blocked on resource.
        expires = datetime.now(UTC) + timedelta(seconds=1)
        actor.session.expires_at = expires
        db.commit()

    def execute():
        with Session(postgres_engine) as db:
            worker_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            actor = AuthContext(
                account=db.get(UserAccount, account_id),
                person=db.get(Person, person_id),
                session=db.get(AuthSession, session_id),
            )
            entered.set()
            try:
                if operation == "revoke":
                    PairingService(db).revoke(connector_id, actor)
                elif operation.startswith("prepare"):
                    RunService(db).prepare(actor, person_id, connector_id, config, operation_id)
                else:
                    RunService(db).device_command(actor, connector_id, device)
                return 200
            except ProblemError as error:
                return error.status

    try:
        with Session(postgres_engine) as blocker:
            blocker.execute(
                select(ReactionxConnector)
                .where(ReactionxConnector.id == connector_id)
                .with_for_update()
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(execute)
                try:
                    assert entered.wait(5)
                    deadline = monotonic() + 5
                    while True:
                        with postgres_engine.connect() as probe:
                            blocked = probe.scalar(
                                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                                {"pid": worker_pid[0]},
                            )
                        if blocked:
                            break
                        assert monotonic() < deadline, (
                            "request never blocked on actual connector row lock"
                        )
                        sleep(0.01)
                    assert datetime.now(UTC) < expires, (
                        "test did not reach lock before session expiry"
                    )
                    while datetime.now(UTC) <= expires:
                        sleep(0.01)
                finally:
                    blocker.rollback()
                assert future.result(timeout=10) == 401
        with Session(postgres_engine) as db:
            assert db.get(ReactionxConnector, connector_id).status == "active"
            assert db.get(ReactionxConnector, connector_id).revoked_at is None
            assert (
                db.scalar(
                    select(func.count())
                    .select_from(ReactionxRun)
                    .where(ReactionxRun.connector_id == connector_id)
                ),
                db.scalar(
                    select(func.count())
                    .select_from(ReactionxCommand)
                    .where(ReactionxCommand.connector_id == connector_id)
                ),
                db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.actor_user_id == account_id)
                ),
            ) == baseline
    finally:
        with Session(postgres_engine) as db, db.begin():
            assert db.scalar(text("SELECT current_database()")) == "erp_e2e"
            db.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            db.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == account_id))
            db.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            db.execute(
                delete(ReactionxCommand).where(ReactionxCommand.connector_id == connector_id)
            )
            db.execute(delete(ReactionxRun).where(ReactionxRun.connector_id == connector_id))
            db.execute(delete(ReactionxConnector).where(ReactionxConnector.id == connector_id))
            db.execute(delete(UserRole).where(UserRole.user_id == account_id))
            db.execute(delete(AuthSession).where(AuthSession.id == session_id))
            db.execute(delete(UserAccount).where(UserAccount.id == account_id))
            db.execute(delete(Person).where(Person.id == person_id))
