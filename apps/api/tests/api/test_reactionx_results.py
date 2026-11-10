"""Immutable original-connector results and explicit browser recovery."""

import importlib.util
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text

from app.audit.models import AuditEvent
from app.auth.models import AuthSession
from app.reactionx.models import ReactionxEvent, ReactionxResultReceipt, ReactionxRun
from app.reactionx.schemas import canonical_digest
from tests.api.conftest import _login_as
from tests.api.test_reactionx_pairing import MACHINE, WEB, browser_headers, machine_headers
from tests.api.test_reactionx_runs import (
    ack,
    command,
    poll,
    ready,
)
from tests.api.test_reactionx_runs import (
    rig as rig,  # noqa: PLC0414
)


def package(run, mode="manual"):
    fixtures = json.loads(
        (Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    result = deepcopy(next(c["result"] for c in fixtures["cases"] if c["name"] == mode))
    result.update(run_id=run["run_id"], manifest_digest=run["manifest"]["manifest_digest"])
    return result


@pytest.fixture
def executing(db_client, login_as_admin, rig):
    value, tab, prep = rig
    run = ready(db_client, value, prep)
    assert command(db_client, login_as_admin, run, tab, "start").status_code == 202
    start = poll(db_client, value)["commands"][0]
    assert ack(db_client, value, start, "running", status="accepted").status_code == 200
    return value, tab, prep, run, start, package(run)


def upload(client, value, run, body):
    return client.post(
        MACHINE + f"/runs/{run['run_id']}/result", headers=machine_headers(value), json=body
    )


def test_lost_receipt_retry_is_one_result_audit_and_never_proves_off(db_client, db, executing):
    value, _, prep, run, start, body = executing
    first = upload(db_client, value, run, body)
    assert first.status_code == 200, first.text
    again = upload(db_client, value, run, body)
    assert again.json() == first.json()
    assert first.json()["digest"] == canonical_digest(body)
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    assert row.status == "stopping" and not row.stop_confirmed
    assert prep().status_code == 409
    assert db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 1
    assert db.scalar(select(func.count()).select_from(ReactionxEvent)) == len(body["events"])
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "reactionx.result.saved")
        )
        == 1
    )
    response = db_client.get(
        MACHINE + f"/runs/{run['run_id']}/receipt?digest={canonical_digest(body)}",
        headers=machine_headers(value),
    )
    assert response.json() == first.json()
    keys = [d["device_key"] for d in run["manifest"]["devices"]]
    assert (
        ack(db_client, value, start, "stopped_confirmed", off=keys).json()["run_status"]
        == "completed"
    )


@pytest.mark.parametrize("mutation", ["manifest", "run", "seq", "device", "missing", "actor"])
def test_changed_or_missing_evidence_is_rejected(db_client, executing, mutation):
    value, _, _, run, _, body = executing
    if mutation == "manifest":
        body["manifest_digest"] = "0" * 64
    elif mutation == "run":
        body["run_id"] = str(uuid4())
    elif mutation == "seq":
        body["events"][0]["seq"] = 2
    elif mutation == "device":
        body["events"][0]["device_key"] = "unknown-device"
    elif mutation == "missing":
        body["events"] = body["events"][1:]
    else:
        body["actor_account_id"] = str(uuid4())
    assert upload(db_client, value, run, body).status_code in (409, 422)


def test_digest_conflict_never_rewrites_saved_evidence(db_client, db, executing):
    value, _, _, run, _, body = executing
    assert upload(db_client, value, run, body).status_code == 200
    changed = deepcopy(body)
    changed["end"]["active_elapsed_ms"] += 1
    assert upload(db_client, value, run, changed).status_code == 409
    assert db.get(ReactionxResultReceipt, UUID(run["run_id"])).envelope == body


@pytest.mark.parametrize(
    "raw",
    [
        b'{"run_id":"a","run_id":"b"}',
        b'{"events":[{"seq":1,"seq":2}]}',
        b'{"x":NaN}',
        b'{"x":9007199254740992}',
        b'{"x":1.5}',
        b"\xff",
    ],
)
def test_raw_json_evidence_is_not_silently_normalized(db_client, executing, raw):
    value, _, _, run, _, _ = executing
    response = db_client.post(
        MACHINE + f"/runs/{run['run_id']}/result",
        headers={**machine_headers(value), "Content-Type": "application/json"},
        content=raw,
    )
    assert response.status_code == 422


def test_actual_stream_oversize_is_rejected(db_client, executing):
    value, _, _, run, _, _ = executing
    response = db_client.post(
        MACHINE + f"/runs/{run['run_id']}/result",
        headers=machine_headers(value),
        content=b" " * (8 * 1024 * 1024 + 1),
    )
    assert response.status_code == 413


def test_logout_pauses_and_original_explicit_new_session_grant_only_uploads_exact_package(
    db_client, db, executing, login_as_admin, platform_users
):
    value, _, _, run, _, body = executing
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    old_session = db.get(AuthSession, row.browser_session_id)
    old_session.revoked_at = datetime.now(UTC)
    db.commit()
    assert upload(db_client, value, run, body).status_code in (401, 403)
    # A distinct actual login session; no impersonation of the former session.
    login = _login_as(db_client, platform_users, "admin")
    grant = db_client.post(
        WEB + f"/runs/{run['run_id']}/upload-authorization",
        headers=browser_headers(login),
        json={"digest": canonical_digest(body), "confirm_original_package": True},
    )
    assert grant.status_code == 200, grant.text
    changed = deepcopy(body)
    changed["end"]["active_elapsed_ms"] += 1
    assert upload(db_client, value, run, changed).status_code in (401, 403)
    assert upload(db_client, value, run, body).status_code == 200
    assert upload(db_client, value, run, body).status_code == 200
    assert db_client.get(MACHINE + "/status", headers=machine_headers(value)).status_code in (
        401,
        403,
    )
    assert db.get(ReactionxRun, UUID(run["run_id"])).browser_session_id == old_session.id


def test_results_service_contract_exists():
    assert importlib.util.find_spec("app.reactionx.results") is not None


@pytest.mark.parametrize("revocation", ["account", "expired_window", "session"])
def test_superadmin_recovery_needs_two_confirmations_and_original_bytes(
    db_client, db, executing, revocation
):
    assert importlib.util.find_spec("app.reactionx.recovery") is not None
    from app.auth.models import UserAccount
    from app.core.errors import ProblemError
    from app.reactionx.recovery import RecoveryService
    from tests.services.test_reactionx_commands import seed

    value, _, _, run, _, body = executing
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    if revocation == "account":
        db.get(UserAccount, row.actor_account_id).status = "disabled"
    elif revocation == "session":
        db.get(AuthSession, row.browser_session_id).revoked_at = datetime.now(UTC)
    else:
        row.created_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    issuer, _ = seed(db)
    recovery = RecoveryService(db)
    with pytest.raises(ProblemError):
        recovery.authorize(issuer, row.id, canonical_digest(body), "", True)
    pending = recovery.authorize(
        issuer,
        row.id,
        canonical_digest(body),
        "Synthetic original package explicitly verified",
        True,
    )
    assert upload(db_client, value, run, body).status_code in (401, 403)
    grant = recovery.confirm(issuer, row.id, pending.request_id, True)
    assert grant.status == "authorized"
    changed = deepcopy(body)
    changed["end"]["active_elapsed_ms"] += 1
    assert upload(db_client, value, run, changed).status_code in (401, 403)
    saved = upload(db_client, value, run, body)
    assert saved.status_code == 200, saved.text
    assert upload(db_client, value, run, body).json() == saved.json()
    assert db.get(ReactionxRun, row.id).actor_account_id != issuer.account.id
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action.in_(
                    ["reactionx.recovery.requested", "reactionx.recovery.confirmed"]
                )
            )
        )
        == 2
    )


def test_recovery_issuer_revocation_and_connector_revocation_are_not_bypassed(
    db_client, db, executing
):
    assert importlib.util.find_spec("app.reactionx.recovery") is not None
    from app.reactionx.models import ReactionxConnector
    from app.reactionx.recovery import RecoveryService
    from tests.services.test_reactionx_commands import seed

    value, _, _, run, _, body = executing
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    db.get(AuthSession, row.browser_session_id).revoked_at = datetime.now(UTC)
    issuer, _ = seed(db)
    service = RecoveryService(db)
    pending = service.authorize(
        issuer,
        row.id,
        canonical_digest(body),
        "Synthetic offline package explicitly verified",
        True,
    )
    service.confirm(issuer, row.id, pending.request_id, True)
    issuer.session.revoked_at = datetime.now(UTC)
    db.commit()
    assert upload(db_client, value, run, body).status_code in (401, 403)
    issuer.session.revoked_at = None
    db.get(ReactionxConnector, row.connector_id).status = "revoked"
    db.commit()
    assert upload(db_client, value, run, body).status_code == 401


def test_persisted_receipt_and_events_cannot_be_mutated(db_client, db, executing):
    from sqlalchemy.exc import DBAPIError

    value, _, _, run, _, body = executing
    assert upload(db_client, value, run, body).status_code == 200
    for table in ("reactionx_events", "reactionx_result_receipts"):
        with pytest.raises(DBAPIError), db.begin_nested():
            db.execute(text(f"DELETE FROM {table} WHERE run_id=:run"), {"run": run["run_id"]})


def test_report_list_and_ranking_are_separate_from_machine_authority(db_client, db, executing):
    value, _, _, run, _, body = executing
    saved = upload(db_client, value, run, body)
    assert saved.status_code == 200, saved.text
    result_id = saved.json()["result_id"]
    report = db_client.get(WEB + f"/results/{result_id}")
    assert report.status_code == 200, report.text
    assert report.json()["ranking_eligible"] is False
    assert report.json()["ranking_reason"] == "practice_only"
    assert report.json()["envelope"] == body
    listing = db_client.get(WEB + "/results?limit=1").json()
    assert len(listing["items"]) == 1 and "total" not in listing
    ranking = db_client.get(WEB + f"/results/{result_id}/ranking")
    assert ranking.status_code == 200 and ranking.json()["reason"] == "practice_only"
    assert db_client.get(WEB + "/results", headers=machine_headers(value)).status_code == 401


@pytest.mark.parametrize("recover,stale_absence", [(False, False), (True, False), (True, True)])
def test_independent_connections_concurrent_submit_lost_ack_is_one_commit(
    postgres_engine, recover, stale_absence
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Event

    from sqlalchemy import delete, event
    from sqlalchemy.orm import Session

    from app.auth.models import UserAccount
    from app.identity.models import Person
    from app.rbac.models import UserRole
    from app.reactionx.commands import ClientService
    from app.reactionx.models import ReactionxCommand, ReactionxConnector
    from app.reactionx.queries import HeartbeatRequest
    from app.reactionx.recovery import RecoveryService
    from app.reactionx.results import ResultService
    from app.reactionx.runs import RunService
    from app.reactionx.schemas import ResultEnvelope
    from tests.services.test_reactionx_commands import configuration, seed
    from tests.services.test_reactionx_commands import ready as service_ready

    barrier = Barrier(2)
    absent_seen, winner_done = Event(), Event()
    with Session(postgres_engine) as session:
        actor, connector = seed(session)
        accounts, people, sessions, connectors = (
            [actor.account.id],
            [actor.person.id],
            [actor.session.id],
            [connector.id],
        )
        owner_id, connector_id = actor.account.id, connector.id
        now = datetime.now(UTC)
        client = ClientService(session, clock=lambda: now)
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
        runs = RunService(session, clock=lambda: now)
        run, _ = service_ready((actor, connector, runs, client, [now], None))
        runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
        assert client.poll(connector).commands[0].kind == "start"
        body = ResultEnvelope.model_validate(package(run.model_dump(mode="json")))
        run_id = run.run_id
        if recover:
            actor.session.revoked_at = now
            session.commit()
            issuer, extra = seed(session)
            accounts.append(issuer.account.id)
            people.append(issuer.person.id)
            sessions.append(issuer.session.id)
            connectors.append(extra.id)
            recovery = RecoveryService(session)
            pending = recovery.authorize(
                issuer,
                run_id,
                canonical_digest(body),
                "Synthetic concurrent original package verified",
                True,
            )
            recovery.confirm(issuer, run_id, pending.request_id, True)

    def submit(index):
        with Session(postgres_engine) as session:
            connector = session.get(ReactionxConnector, connector_id)
            backend = session.scalar(text("SELECT pg_backend_pid()"))
            service = ResultService(session)
            if recover:
                from app.core.errors import ProblemError

                real_issuer = service.recovery._issuer

                def issuer_without_rejected_owner_lock(*args, **kwargs):
                    try:
                        return real_issuer(*args, **kwargs)
                    except ProblemError:
                        if args[0] == owner_id and args[1] == sessions[0]:
                            # A rejected old login must not keep its identity lock
                            # while acquiring a different recovery issuer's lock.
                            # Otherwise reciprocal A/B recovery grants deadlock.
                            with Session(postgres_engine) as probe, probe.begin():
                                probe.execute(
                                    select(UserAccount.id)
                                    .where(UserAccount.id == owner_id)
                                    .with_for_update(read=True, key_share=True, nowait=True)
                                )
                        raise

                service.recovery._issuer = issuer_without_rejected_owner_lock
            if stale_absence and index == 1:

                def hold_after_absence(connection, cursor, statement, parameters, context, many):
                    if "FROM reactionx_runs" in statement and not absent_seen.is_set():
                        absent_seen.set()
                        assert winner_done.wait(timeout=20)

                event.listen(session.connection(), "before_cursor_execute", hold_after_absence)
            if stale_absence and index == 0:
                assert absent_seen.wait(timeout=20)
            if not stale_absence:
                barrier.wait(timeout=10)
            try:
                result = service.submit(connector, body)
                return backend, result.model_dump()
            finally:
                if index == 0:
                    winner_done.set()

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, index) for index in range(2)]
            outcomes = [future.result(timeout=30) for future in futures]
        assert len({backend for backend, _ in outcomes}) == 2
        assert outcomes[0][1] == outcomes[1][1]
        with Session(postgres_engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(ReactionxResultReceipt)
                    .where(ReactionxResultReceipt.run_id == run_id)
                )
                == 1
            )
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.actor_user_id == owner_id,
                        AuditEvent.action == "reactionx.result.saved",
                    )
                )
                == 1
            )
            assert session.scalar(
                select(func.count())
                .select_from(ReactionxEvent)
                .where(ReactionxEvent.run_id == run_id)
            ) == len(body.events)
            if recover:
                assert (
                    session.get(ReactionxRun, run_id).upload_authorization["consumed_at"]
                    is not None
                )
    finally:
        with Session(postgres_engine) as session, session.begin():
            database = session.scalar(text("SELECT current_database()"))
            assert database == "erp_e2e" or database.endswith("_test")
            session.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id.in_(accounts)))
            session.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            for model in (ReactionxEvent, ReactionxResultReceipt):
                table = model.__tablename__
                session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_immutable"))
                session.execute(delete(model).where(model.run_id == run_id))
                session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {table}_immutable"))
            session.execute(
                delete(ReactionxCommand).where(ReactionxCommand.connector_id.in_(connectors))
            )
            session.execute(delete(ReactionxRun).where(ReactionxRun.connector_id.in_(connectors)))
            session.execute(delete(ReactionxConnector).where(ReactionxConnector.id.in_(connectors)))
            session.execute(delete(UserRole).where(UserRole.user_id.in_(accounts)))
            session.execute(delete(AuthSession).where(AuthSession.id.in_(sessions)))
            session.execute(delete(UserAccount).where(UserAccount.id.in_(accounts)))
            session.execute(delete(Person).where(Person.id.in_(people)))


@pytest.mark.parametrize(
    "mode,verified,expected",
    [
        ("standard", False, "mode_unverified"),
        ("standard", True, None),
        ("manual", True, "practice_only"),
    ],
)
def test_only_explicit_service_policy_can_rank_formal_and_manual_never_ranks(
    db, mode, verified, expected
):
    from app.reactionx.commands import ClientService
    from app.reactionx.queries import HeartbeatRequest
    from app.reactionx.results import ResultService
    from app.reactionx.runs import RunService
    from app.reactionx.schemas import ResultEnvelope
    from tests.services.test_reactionx_commands import acknowledge, configuration, prepare, seed

    actor, connector = seed(db)
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
                    for d in configuration(mode)["devices"]
                ],
            }
        ),
    )
    runs = RunService(db, clock=lambda: now, validation_policy=lambda _: True)
    run = prepare((actor, connector, runs, client, [now], None), mode=mode)
    op = client.poll(connector).commands[0]
    acknowledge(client, connector, op.operation_id, "ready")
    runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    assert client.poll(connector).commands[0].kind == "start"
    body = ResultEnvelope.model_validate(package(run.model_dump(mode="json"), mode))
    receipt = ResultService(db, validation_policy=(lambda _: True) if verified else None).submit(
        connector, body
    )
    stored = db.get(ReactionxResultReceipt, run.run_id)
    assert stored.ranking_reason == expected and stored.eligible == (expected is None)
    assert receipt.digest == canonical_digest(body)


def test_manual_stop_cannot_be_relabelled_normal_and_crash_elapsed_is_only_lower_bound(
    db_client, db, login_as_admin, executing
):
    value, tab, _, run, _, body = executing
    assert command(db_client, login_as_admin, run, tab, "stop").status_code == 202
    body["end"]["kind"] = "task_completed"
    assert upload(db_client, value, run, body).status_code == 422
    body["events"] = []
    body["end"] = {"kind": "process_interrupted", "active_elapsed_ms": 0}
    saved = upload(db_client, value, run, body)
    assert saved.status_code == 200, saved.text
    report = db_client.get(WEB + f"/results/{saved.json()['result_id']}").json()
    assert report["envelope"]["end"] == body["end"] and not report["ranking_eligible"]


@pytest.mark.parametrize(
    "evidence",
    [
        "complete",
        "quality_warning",
        "reverse_off",
        "missing_ack",
        "partial_off",
        "failed_ack",
        "delivered_stop",
        "user_stop",
        "uncancelled_stop",
        "missing_run_off",
        "invalid_diagnostic",
    ],
)
def test_late_safety_stop_requires_original_natural_lifecycle_and_exact_start_off(db, evidence):
    from app.core.errors import ProblemError
    from app.reactionx.commands import ClientService
    from app.reactionx.models import ReactionxCommand
    from app.reactionx.queries import HeartbeatRequest
    from app.reactionx.results import ResultService
    from app.reactionx.runs import RunService
    from app.reactionx.schemas import ResultEnvelope
    from tests.services.test_reactionx_commands import acknowledge, configuration, prepare, seed

    actor, connector = seed(db)
    now = [datetime.now(UTC)]
    client = ClientService(db, clock=lambda: now[0])
    beat = HeartbeatRequest.model_validate(
        {
            "protocol_version": 1,
            "connector_version": "0.3.0",
            "instance_id": str(uuid4()),
            "devices": [
                {**d, "label": "synthetic", "connected": True}
                for d in configuration("whack")["devices"]
            ],
        }
    )
    client.heartbeat(connector, beat)
    runs = RunService(db, clock=lambda: now[0], validation_policy=lambda _: True)
    run = prepare((actor, connector, runs, client, now, beat), mode="whack")
    ready_op = client.poll(connector).commands[0]
    acknowledge(client, connector, ready_op.operation_id, "ready")
    runs.command(actor, run.run_id, "start", uuid4(), tab_id=connector.control_tab_id)
    start_op = client.poll(connector).commands[0]
    acknowledge(client, connector, start_op.operation_id, "running", status="accepted")
    if evidence == "user_stop":
        runs.command(actor, run.run_id, "stop", uuid4(), tab_id=connector.control_tab_id)
    else:
        now[0] += timedelta(seconds=16)
        client.heartbeat(connector, beat)
    if evidence == "delivered_stop":
        assert client.poll(connector).commands[0].kind == "stop"
    keys = [d.device_key for d in run.manifest.devices]
    if evidence == "reverse_off":
        keys.reverse()
    if evidence != "missing_ack":
        acknowledge(client, connector, start_op.operation_id, "stopped_confirmed", keys=keys)
    start_row = db.scalar(
        select(ReactionxCommand).where(ReactionxCommand.operation_id == UUID(start_op.operation_id))
    )
    stop_row = db.scalar(
        select(ReactionxCommand).where(
            ReactionxCommand.run_id == run.run_id, ReactionxCommand.kind == "stop"
        )
    )
    if evidence == "partial_off":
        start_row.ack_payload = {**start_row.ack_payload, "off_confirmed_device_keys": keys[:1]}
    if evidence == "failed_ack":
        start_row.ack_payload = {**start_row.ack_payload, "status": "failed"}
    if evidence == "uncancelled_stop":
        stop_row.status = "queued"
    if evidence == "missing_run_off":
        db.get(ReactionxRun, run.run_id).stop_confirmed = False
    db.commit()
    raw = package(run.model_dump(mode="json"), "whack")
    if evidence == "quality_warning":
        diagnostic = {
            **raw["events"][0],
            "kind": "invalid",
            "quality_code": "duplicate_notification",
        }
        raw["events"].insert(1, diagnostic)
        for seq, event in enumerate(raw["events"], 1):
            event["seq"] = seq
    if evidence == "invalid_diagnostic":
        raw["events"][0]["kind"] = "invalid"
        raw["events"][0]["quality_code"] = "activation_write_failed"
    envelope = ResultEnvelope.model_validate(raw)
    digest = canonical_digest(envelope)
    service = ResultService(db)
    if evidence in ("complete", "quality_warning", "reverse_off"):
        receipt = service.submit(connector, envelope)
        assert service.submit(connector, envelope) == receipt
        stored = db.get(ReactionxResultReceipt, run.run_id)
        assert stored.digest == digest and stored.envelope == raw
        assert not stored.eligible
        assert stored.ranking_reason == (
            "quality_warning" if evidence == "quality_warning" else "mode_unverified"
        )
        assert db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 1
    else:
        with pytest.raises(ProblemError) as rejected:
            service.submit(connector, envelope)
        assert rejected.value.status == 422
        assert db.get(ReactionxResultReceipt, run.run_id) is None


def test_invisible_report_list_and_rank_return_404_not_an_exception(
    db_client, db, executing, login_as_admin
):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    value, _, _, run, _, body = executing
    saved = upload(db_client, value, run, body).json()
    person_id = UUID(run["manifest"]["subject_person_id"])
    db.add(
        UserPermissionOverride(
            user_id=login_as_admin.account_id,
            permission_id=PERMISSIONS["talent_tests.read"].id,
            scope_kind="person",
            scope_id=person_id,
            effect="deny",
        )
    )
    db.commit()
    for path in (
        f"/results/{saved['result_id']}",
        f"/results/{saved['result_id']}/ranking",
        f"/results?person_id={person_id}",
    ):
        response = db_client.get(WEB + path)
        assert response.status_code == 404, response.text
    page = db_client.get(WEB + "/results").json()
    assert page == {"items": [], "next_cursor": None}


def test_recovery_expiry_wrong_actor_and_repeated_confirmation_do_not_extend_authority(
    db_client, db, executing
):
    from app.core.errors import ProblemError
    from app.reactionx.models import ReactionxConnector
    from app.reactionx.recovery import RecoveryService
    from app.reactionx.results import ResultService
    from app.reactionx.schemas import ResultEnvelope
    from tests.services.test_reactionx_commands import seed

    _, _, _, run, _, body = executing
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    db.get(AuthSession, row.browser_session_id).revoked_at = datetime.now(UTC)
    issuer, _ = seed(db)
    other, _ = seed(db)
    now = [datetime.now(UTC)]
    recovery = RecoveryService(db, clock=lambda: now[0])
    with pytest.raises(ProblemError):
        recovery.authorize_upload(other, row.id, canonical_digest(body), True)
    pending = recovery.authorize(
        issuer,
        row.id,
        canonical_digest(body),
        "Synthetic original package confirmed for expiry",
        True,
    )
    with pytest.raises(ProblemError):
        recovery.confirm(other, row.id, pending.request_id, True)
    first = recovery.confirm(issuer, row.id, pending.request_id, True)
    now[0] += timedelta(minutes=4)
    assert recovery.confirm(issuer, row.id, pending.request_id, True).expires_at == first.expires_at
    now[0] += timedelta(minutes=1)
    with pytest.raises(ProblemError):
        recovery.confirm(issuer, row.id, pending.request_id, True)
    with pytest.raises(ProblemError):
        ResultService(db, clock=lambda: now[0]).submit(
            db.get(ReactionxConnector, row.connector_id), ResultEnvelope.model_validate(body)
        )


def test_recovery_browser_csrf_and_two_distinct_requests(db_client, db, login_as_admin, executing):
    _, _, _, run, _, body = executing
    route = WEB + f"/runs/{run['run_id']}/recovery"
    request = {
        "digest": canonical_digest(body),
        "reason": "Synthetic package and original owner verified",
        "confirmation": True,
    }
    assert db_client.post(route, json=request).status_code == 403
    first = db_client.post(route, headers=browser_headers(login_as_admin), json=request)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "confirmation_required"
    second = db_client.post(
        route + f"/{first.json()['request_id']}/confirm",
        headers=browser_headers(login_as_admin),
        json={"confirmation": True},
    )
    assert second.status_code == 200 and second.json()["status"] == "authorized"


def test_recovery_reason_rejects_sensitive_content_without_server_error(
    db_client, login_as_admin, executing
):
    _, _, _, run, _, body = executing
    response = db_client.post(
        WEB + f"/runs/{run['run_id']}/recovery",
        headers=browser_headers(login_as_admin),
        json={
            "digest": canonical_digest(body),
            "reason": "联系电话 13800138000，确认恢复原始结果",
            "confirmation": True,
        },
    )
    assert response.status_code == 422


def test_recovery_issuer_is_rechecked_after_resource_lock(db_client, db, executing):
    from app.core.errors import ProblemError
    from app.reactionx.models import ReactionxConnector
    from app.reactionx.recovery import RecoveryService
    from app.reactionx.results import ResultService
    from app.reactionx.schemas import ResultEnvelope
    from tests.services.test_reactionx_commands import seed

    _, _, _, run, _, body = executing
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    db.get(AuthSession, row.browser_session_id).revoked_at = datetime.now(UTC)
    issuer, _ = seed(db)
    recovery = RecoveryService(db)
    pending = recovery.authorize(
        issuer,
        row.id,
        canonical_digest(body),
        "Synthetic package verified at protected boundary",
        True,
    )
    recovery.confirm(issuer, row.id, pending.request_id, True)
    service = ResultService(db)
    lock_run = service.recovery._run

    def expire_after_lock(run_id):
        locked = lock_run(run_id)
        # Emulates expiry while waiting for the resource lock, without sleeping
        # or replacing the actual authorization/persistence implementations.
        issuer.session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        db.flush()
        return locked

    service.recovery._run = expire_after_lock
    with pytest.raises(ProblemError):
        service.submit(
            db.get(ReactionxConnector, row.connector_id), ResultEnvelope.model_validate(body)
        )


@pytest.mark.parametrize("operation", ["authorize_upload", "authorize", "confirm"])
def test_grant_mutations_recheck_issuer_expiry_after_run_lock(
    db, executing, monkeypatch, operation
):
    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.core.errors import ProblemError
    from app.identity.models import Person
    from app.reactionx.recovery import RecoveryService
    from tests.services.test_reactionx_commands import seed

    _, _, _, run, _, body = executing
    run_id = UUID(run["run_id"])
    row = db.get(ReactionxRun, run_id)
    if operation == "authorize_upload":
        account = db.get(UserAccount, row.actor_account_id)
        issuer = AuthContext(
            account=account,
            person=db.get(Person, account.person_id),
            session=db.get(AuthSession, row.browser_session_id),
        )
    else:
        issuer, _ = seed(db)
    now = [datetime.now(UTC)]
    expires_at = now[0] + timedelta(seconds=30)
    issuer.session.expires_at = expires_at
    db.commit()

    class AuthClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)

    # The actual session row keeps its fixed expiry. Only time advances while
    # waiting for the real connector/run lock; authority is never mocked.
    monkeypatch.setattr("app.auth.dependencies.datetime", AuthClock)
    service = RecoveryService(db, clock=lambda: now[0])
    digest = canonical_digest(body)
    reason = "Synthetic original package verified before issuer expiry"
    pending = (
        service.authorize(issuer, run_id, digest, reason, True) if operation == "confirm" else None
    )
    row = db.get(ReactionxRun, run_id, populate_existing=True)
    before = (
        deepcopy(row.upload_authorization),
        row.upload_permit_digest,
        row.upload_permit_expires_at,
    )
    audit_count = db.scalar(
        select(func.count()).select_from(AuditEvent).where(AuditEvent.resource_id == run_id)
    )
    lock_run = service._run

    def time_passes_while_waiting(identity):
        locked = lock_run(identity)
        now[0] = expires_at + timedelta(microseconds=1)
        return locked

    service._run = time_passes_while_waiting
    with pytest.raises(ProblemError) as rejected:
        if operation == "authorize_upload":
            service.authorize_upload(issuer, run_id, digest, True)
        elif operation == "authorize":
            service.authorize(issuer, run_id, digest, reason, True)
        else:
            service.confirm(issuer, run_id, pending.request_id, True)
    assert rejected.value.status == 401
    row = db.get(ReactionxRun, run_id, populate_existing=True)
    assert (
        row.upload_authorization,
        row.upload_permit_digest,
        row.upload_permit_expires_at,
    ) == before
    assert (
        db.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.resource_id == run_id)
        )
        == audit_count
    )
    assert db.get(AuthSession, issuer.session.id, populate_existing=True).expires_at == expires_at
