"""Unknown delivery is a separate, scoped closure, never a saved result."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.auth.models import AuthSession
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxEvent,
    ReactionxResultReceipt,
    ReactionxRun,
)
from app.reactionx.schemas import canonical_digest
from tests.api.conftest import _login_as
from tests.api.test_reactionx_pairing import MACHINE, WEB, browser_headers, machine_headers
from tests.api.test_reactionx_results import package, upload
from tests.api.test_reactionx_runs import ack, command, poll, ready
from tests.api.test_reactionx_runs import rig as rig  # noqa: PLC0414

REASON = "Synthetic original device OFF and unknown delivery checked"


@pytest.fixture
def unknown(db_client, db, login_as_admin, rig):
    value, tab, prep = rig
    run = ready(db_client, value, prep)
    assert command(db_client, login_as_admin, run, tab, "start").status_code == 202
    start = poll(db_client, value)["commands"][0]
    assert start["kind"] == "start"
    assert command(db_client, login_as_admin, run, tab, "stop").status_code == 202
    stop = poll(db_client, value)["commands"][0]
    assert stop["kind"] == "stop"
    keys = [device["device_key"] for device in run["manifest"]["devices"]]
    assert ack(db_client, value, stop, "stopped_confirmed", off=keys).status_code == 200
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    assert row.status == "awaiting_sync" and row.stop_confirmed
    declaration = {
        "version": 1,
        "kind": "start_delivery_unknown",
        "run_id": run["run_id"],
        "actor_account_id": str(row.actor_account_id),
        "subject_person_id": str(row.subject_person_id),
        "connector_id": value["connector_id"],
        "manifest_digest": row.manifest_digest,
        "stop_operation_id": stop["operation_id"],
        "local_evidence_digest": "a" * 64,
    }
    return value, run, start, stop, declaration


def declare(client, unknown, body=None):
    value, run, _, _, declaration = unknown
    return client.post(
        MACHINE + f"/runs/{run['run_id']}/uncertainty",
        headers=machine_headers(value),
        json=body or declaration,
    )


def request_archive(client, login, unknown, **changes):
    _, run, _, _, declaration = unknown
    body = {
        "declaration_digest": canonical_digest(declaration),
        "reason": REASON,
        "confirmation": True,
        **changes,
    }
    return client.post(
        WEB + f"/runs/{run['run_id']}/uncertainty/request",
        headers=browser_headers(login),
        json=body,
    )


def confirm_archive(client, login, unknown, request_id, **changes):
    return client.post(
        WEB + f"/runs/{unknown[1]['run_id']}/uncertainty/confirm",
        headers=browser_headers(login),
        json={"request_id": request_id, "confirmation": True, **changes},
    )


def test_real_http_archive_is_non_result_idempotent_and_keeps_original_evidence(
    db_client, db, login_as_admin, unknown
):
    value, run, start, stop, declaration = unknown
    response = declare(db_client, unknown)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "state": "pending_review",
        "declaration_digest": canonical_digest(declaration),
        "receipt": None,
    }
    assert declare(db_client, unknown).json() == response.json()
    page = db_client.get(
        WEB + "/uncertainties?offset=0&limit=1", headers=browser_headers(login_as_admin)
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 1
    assert set(page.json()["items"][0]) == {
        "run_id",
        "subject_name",
        "mode",
        "created_at",
        "declaration_digest",
        "state",
    }
    pending = request_archive(db_client, login_as_admin, unknown)
    assert pending.status_code == 200, pending.text
    assert db.get(ReactionxRun, UUID(run["run_id"])).status == "awaiting_sync"
    result = confirm_archive(db_client, login_as_admin, unknown, pending.json()["request_id"])
    assert result.status_code == 200, result.text
    receipt = result.json()
    assert set(receipt) == {
        "version",
        "archive_id",
        "run_id",
        "connector_id",
        "manifest_digest",
        "declaration_digest",
        "stop_operation_id",
        "status",
        "closed_at",
    }
    assert receipt["status"] == "execution_unknown"
    assert (
        receipt["run_id"] == run["run_id"] and receipt["stop_operation_id"] == stop["operation_id"]
    )
    assert (
        confirm_archive(db_client, login_as_admin, unknown, pending.json()["request_id"]).json()
        == receipt
    )
    assert declare(db_client, unknown).json()["receipt"] == receipt
    fetched = db_client.get(
        MACHINE
        + f"/runs/{run['run_id']}/uncertainty?declaration_digest={canonical_digest(declaration)}",
        headers=machine_headers(value),
    )
    assert fetched.json() == {
        "state": "archived",
        "declaration_digest": canonical_digest(declaration),
        "receipt": receipt,
    }
    assert (
        db.get(ReactionxRun, UUID(run["run_id"]), populate_existing=True).status
        == "uncertain_closed"
    )
    assert db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 0
    assert db.scalar(select(func.count()).select_from(ReactionxEvent)) == 0
    assert (
        db.scalar(
            select(func.count())
            .select_from(ReactionxCommand)
            .where(ReactionxCommand.run_id == UUID(run["run_id"]))
        )
        == 3
    )
    assert (
        db.scalar(
            select(ReactionxCommand).where(
                ReactionxCommand.operation_id == UUID(start["operation_id"])
            )
        ).ack_payload
        is None
    )
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.action.in_(
                    ["reactionx.uncertainty.requested", "reactionx.uncertainty.archived"]
                )
            )
        )
        == 2
    )
    assert (
        db_client.get(WEB + "/uncertainties", headers=browser_headers(login_as_admin)).json()[
            "total"
        ]
        == 0
    )
    assert upload(db_client, value, run, package(run)).status_code == 409
    for suffix, body in [
        ("upload-authorization", {"digest": "b" * 64, "confirm_original_package": True}),
        ("recovery", {"digest": "b" * 64, "reason": REASON, "confirmation": True}),
    ]:
        denied = db_client.post(
            WEB + f"/runs/{run['run_id']}/{suffix}",
            headers=browser_headers(login_as_admin),
            json=body,
        )
        assert denied.status_code == 409
        assert denied.json()["code"] == "reactionx_uncertain_closed"


@pytest.mark.parametrize(
    "field",
    [
        "actor_account_id",
        "subject_person_id",
        "connector_id",
        "manifest_digest",
        "stop_operation_id",
    ],
)
def test_original_identity_and_stop_cannot_be_rebound(db_client, unknown, field):
    body = deepcopy(unknown[4])
    body[field] = "b" * 64 if field == "manifest_digest" else str(uuid4())
    assert declare(db_client, unknown, body).status_code == 409


@pytest.mark.parametrize(
    "evidence",
    ["no_off", "no_start_delivery", "accepted", "running", "terminal", "events", "stop_incomplete"],
)
def test_missing_or_contradictory_server_facts_reject_declaration(db_client, db, unknown, evidence):
    _, run, start, stop, _ = unknown
    row = db.get(ReactionxRun, UUID(run["run_id"]))
    start_row = db.scalar(
        select(ReactionxCommand).where(ReactionxCommand.operation_id == UUID(start["operation_id"]))
    )
    stop_row = db.scalar(
        select(ReactionxCommand).where(ReactionxCommand.operation_id == UUID(stop["operation_id"]))
    )
    if evidence == "no_off":
        row.stop_confirmed = False
    elif evidence == "no_start_delivery":
        start_row.delivery_envelope = None
    elif evidence in {"accepted", "running", "terminal"}:
        start_row.ack_payload = {
            "status": "accepted",
            "run_state": {"accepted": None, "running": "running", "terminal": "stopped_confirmed"}[
                evidence
            ],
        }
    elif evidence == "events":
        db.add(ReactionxEvent(run_id=row.id, seq=1, event={"kind": "activated"}))
    else:
        stop_row.ack_payload = {**stop_row.ack_payload, "off_confirmed_device_keys": []}
    db.commit()
    assert declare(db_client, unknown).status_code == 409


def test_receipt_blocks_declaration_and_new_result_invalidates_pending_confirmation(
    db_client, login_as_admin, unknown
):
    assert declare(db_client, unknown).status_code == 200
    pending = request_archive(db_client, login_as_admin, unknown)
    assert pending.status_code == 200
    assert upload(db_client, unknown[0], unknown[1], package(unknown[1])).status_code == 200
    assert (
        confirm_archive(
            db_client, login_as_admin, unknown, pending.json()["request_id"]
        ).status_code
        == 409
    )
    assert declare(db_client, unknown).status_code == 409


def test_declaration_conflict_and_missing_status(db_client, unknown):
    value, run, _, _, declaration = unknown
    url = MACHINE + f"/runs/{run['run_id']}/uncertainty"
    assert (
        db_client.get(
            url,
            params={"declaration_digest": canonical_digest(declaration)},
            headers=machine_headers(value),
        ).status_code
        == 404
    )
    assert declare(db_client, unknown).status_code == 200
    assert (
        declare(db_client, unknown, {**declaration, "local_evidence_digest": "b" * 64}).status_code
        == 409
    )
    assert (
        db_client.get(
            url, params={"declaration_digest": "b" * 64}, headers=machine_headers(value)
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "mutation",
    ["extra", "bool_version", "upper_uuid", "upper_digest", "float_version", "duplicate"],
)
def test_wire_rejects_noncanonical_evidence(db_client, unknown, mutation):
    import json

    body = deepcopy(unknown[4])
    if mutation == "extra":
        body["saved"] = True
    elif mutation == "bool_version":
        body["version"] = True
    elif mutation == "float_version":
        body["version"] = 1.0
    elif mutation == "upper_uuid":
        body["run_id"] = body["run_id"].upper()
    elif mutation == "upper_digest":
        body["local_evidence_digest"] = "A" * 64
    raw = json.dumps(body)
    if mutation == "duplicate":
        raw = raw[:-1] + ',"version":1}'
    response = db_client.post(
        MACHINE + f"/runs/{unknown[1]['run_id']}/uncertainty",
        headers=machine_headers(unknown[0]),
        content=raw,
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "changes",
    [
        {"confirmation": False},
        {"confirmation": 1},
        {"confirmation": "true"},
        {"reason": "short"},
        {"reason": "x" * 501},
        {"reason": "联系电话 13800138000，确认设备关灯"},
    ],
)
def test_first_confirmation_and_audit_reason_are_mandatory(
    db_client, login_as_admin, unknown, changes
):
    assert declare(db_client, unknown).status_code == 200
    assert request_archive(db_client, login_as_admin, unknown, **changes).status_code == 422


def test_other_login_cannot_consume_request_and_connector_survives_original_logout(
    db_client, db, platform_users, login_as_admin, unknown
):
    assert declare(db_client, unknown).status_code == 200
    pending = request_archive(db_client, login_as_admin, unknown).json()
    row = db.get(ReactionxRun, UUID(unknown[1]["run_id"]))
    db.get(AuthSession, row.browser_session_id).revoked_at = datetime.now(UTC)
    db.commit()
    assert declare(db_client, unknown).status_code == 200
    login = _login_as(db_client, platform_users, "admin")
    assert confirm_archive(db_client, login, unknown, pending["request_id"]).status_code == 403
    new_request = request_archive(db_client, login, unknown)
    assert new_request.status_code == 200
    assert (
        confirm_archive(db_client, login, unknown, new_request.json()["request_id"]).status_code
        == 200
    )


@pytest.mark.parametrize("denial", ["role", "manage", "conduct", "subject"])
def test_live_browser_permissions_and_explicit_denials(
    db_client, db, login_as_admin, unknown, denial
):
    from sqlalchemy import delete

    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import PERMISSIONS, ROLES

    assert declare(db_client, unknown).status_code == 200
    pending = request_archive(db_client, login_as_admin, unknown).json()
    actor = login_as_admin.account_id
    if denial == "role":
        db.execute(delete(UserRole).where(UserRole.user_id == actor))
        db.add(UserRole(user_id=actor, role_id=ROLES["admin"].id))
    else:
        db.add(
            UserPermissionOverride(
                user_id=actor,
                permission_id=PERMISSIONS[
                    f"reactionx.{'conduct' if denial == 'subject' else denial}"
                ].id,
                effect="deny",
                scope_kind="person" if denial == "subject" else "global",
                scope_id=UUID(unknown[4]["subject_person_id"]) if denial == "subject" else None,
            )
        )
    db.commit()
    page = db_client.get(WEB + "/uncertainties")
    if denial == "subject":
        assert page.status_code == 200 and page.json()["items"] == [] and page.json()["total"] == 0
    else:
        assert page.status_code == 403
    assert request_archive(db_client, login_as_admin, unknown).status_code == 403
    assert (
        confirm_archive(db_client, login_as_admin, unknown, pending["request_id"]).status_code
        == 403
    )


def test_machine_auth_origin_revoke_and_browser_csrf_stay_separate(
    db_client, db, login_as_admin, unknown
):
    from app.reactionx.models import ReactionxConnector

    value, run, _, _, declaration = unknown
    url = MACHINE + f"/runs/{run['run_id']}/uncertainty"
    assert db_client.post(url, json=declaration).status_code == 401
    assert (
        db_client.post(
            url,
            headers={**machine_headers(value), "Origin": "https://attacker.invalid"},
            json=declaration,
        ).status_code
        == 403
    )
    assert (
        db_client.post(
            url, headers=machine_headers(value, "B" * 42 + "A"), json=declaration
        ).status_code
        == 401
    )
    assert declare(db_client, unknown).status_code == 200
    assert db_client.get(WEB + "/uncertainties", headers=machine_headers(value)).status_code == 401
    assert (
        db_client.post(
            WEB + f"/runs/{run['run_id']}/uncertainty/request",
            json={
                "declaration_digest": canonical_digest(declaration),
                "reason": REASON,
                "confirmation": True,
            },
        ).status_code
        == 403
    )
    db.get(ReactionxConnector, UUID(value["connector_id"])).status = "revoked"
    db.commit()
    assert declare(db_client, unknown).status_code == 401
    assert (
        db_client.get(
            url,
            params={"declaration_digest": canonical_digest(declaration)},
            headers=machine_headers(value),
        ).status_code
        == 401
    )


@pytest.mark.parametrize("confirmation", [False, 1, "true"])
def test_second_confirmation_must_be_literal_true(db_client, login_as_admin, unknown, confirmation):
    assert declare(db_client, unknown).status_code == 200
    pending = request_archive(db_client, login_as_admin, unknown).json()
    assert (
        confirm_archive(
            db_client, login_as_admin, unknown, pending["request_id"], confirmation=confirmation
        ).status_code
        == 422
    )


def current_actor(db, run_id):
    from app.auth.models import UserAccount
    from app.auth.service import AuthContext
    from app.identity.models import Person

    row = db.get(ReactionxRun, run_id)
    account = db.get(UserAccount, row.actor_account_id)
    return AuthContext(
        account=account,
        person=db.get(Person, account.person_id),
        session=db.get(AuthSession, row.browser_session_id),
    )


def test_fixed_five_minute_expiry_and_receipt_replay_after_expiry(db_client, db, unknown):
    from app.core.errors import ProblemError
    from app.reactionx.uncertainty import UncertaintyService

    assert declare(db_client, unknown).status_code == 200
    run_id = UUID(unknown[1]["run_id"])
    actor = current_actor(db, run_id)
    now = [datetime.now(UTC)]
    service = UncertaintyService(db, clock=lambda: now[0])
    pending = service.request_archive(actor, run_id, canonical_digest(unknown[4]), REASON, True)
    assert datetime.fromisoformat(pending.expires_at) - datetime.fromisoformat(
        db.get(ReactionxRun, run_id).uncertainty_record["request"]["requested_at"]
    ) == timedelta(minutes=5)
    now[0] = datetime.fromisoformat(pending.expires_at)
    with pytest.raises(ProblemError) as error:
        service.confirm_archive(actor, run_id, UUID(pending.request_id), True)
    assert error.value.code == "reactionx_uncertainty_request_expired"
    pending = service.request_archive(actor, run_id, canonical_digest(unknown[4]), REASON, True)
    receipt = service.confirm_archive(actor, run_id, UUID(pending.request_id), True)
    now[0] += timedelta(minutes=6)
    assert service.confirm_archive(actor, run_id, UUID(pending.request_id), True) == receipt


@pytest.mark.parametrize("phase", ["request", "confirm"])
@pytest.mark.parametrize("change", ["expiry", "permission", "evidence"])
def test_final_boundary_rechecks_same_session_permissions_and_facts(
    db_client, db, unknown, monkeypatch, phase, change
):
    from app.core.errors import ProblemError
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    from app.reactionx.uncertainty import UncertaintyService

    assert declare(db_client, unknown).status_code == 200
    run_id = UUID(unknown[1]["run_id"])
    actor = current_actor(db, run_id)
    now = [datetime.now(UTC)]
    actor.session.expires_at = now[0] + timedelta(seconds=30)
    db.commit()
    expires_at = actor.session.expires_at

    class AuthClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)

    monkeypatch.setattr("app.auth.dependencies.datetime", AuthClock)
    service = UncertaintyService(db, clock=lambda: now[0])
    pending = (
        service.request_archive(actor, run_id, canonical_digest(unknown[4]), REASON, True)
        if phase == "confirm"
        else None
    )
    before = deepcopy(db.get(ReactionxRun, run_id).uncertainty_record)
    real_lock = service.recovery._run

    def change_after_lock(identity):
        row = real_lock(identity)
        if change == "expiry":
            now[0] = expires_at + timedelta(microseconds=1)
        elif change == "permission":
            db.add(
                UserPermissionOverride(
                    user_id=actor.account.id,
                    permission_id=PERMISSIONS["reactionx.manage"].id,
                    effect="deny",
                    scope_kind="global",
                )
            )
            db.flush()
        else:
            row.stop_confirmed = False
            db.flush()
        return row

    service.recovery._run = change_after_lock
    with pytest.raises(ProblemError):
        if phase == "request":
            service.request_archive(actor, run_id, canonical_digest(unknown[4]), REASON, True)
        else:
            service.confirm_archive(actor, run_id, UUID(pending.request_id), True)
    row = db.get(ReactionxRun, run_id, populate_existing=True)
    assert row.status == "awaiting_sync" and row.uncertainty_record == before


@pytest.fixture
def committed_unknown(postgres_engine):
    """Real service lifecycle committed for independent-connection lock tests."""
    from sqlalchemy import delete, text
    from sqlalchemy.orm import Session

    from app.auth.models import UserAccount
    from app.identity.models import Person
    from app.rbac.models import UserRole
    from app.reactionx.commands import ClientService
    from app.reactionx.models import ReactionxConnector
    from app.reactionx.queries import AckRequest, HeartbeatRequest
    from app.reactionx.runs import RunService
    from app.reactionx.uncertainty import UncertaintyDeclaration, UncertaintyService
    from tests.services.test_reactionx_commands import configuration, seed
    from tests.services.test_reactionx_commands import ready as service_ready

    with Session(postgres_engine) as session:
        actor, connector = seed(session)
        account_id, person_id, session_id, connector_id = (
            actor.account.id,
            actor.person.id,
            actor.session.id,
            connector.id,
        )
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
        runs.command(actor, run.run_id, "stop", uuid4(), tab_id=connector.control_tab_id)
        stop = client.poll(connector).commands[0]
        assert stop.kind == "stop"
        client.ack(
            connector,
            UUID(stop.operation_id),
            AckRequest.model_validate(
                {
                    "status": "completed",
                    "run_state": "stopped_confirmed",
                    "off_confirmed_device_keys": [d.device_key for d in run.manifest.devices],
                }
            ),
        )
        declaration = UncertaintyDeclaration(
            version=1,
            kind="start_delivery_unknown",
            run_id=str(run.run_id),
            actor_account_id=str(account_id),
            subject_person_id=str(person_id),
            connector_id=str(connector_id),
            manifest_digest=run.manifest.manifest_digest,
            stop_operation_id=stop.operation_id,
            local_evidence_digest="a" * 64,
        )
        service = UncertaintyService(session)
        service.declare(connector, run.run_id, declaration)
        pending = service.request_archive(
            actor, run.run_id, canonical_digest(declaration), REASON, True
        )
        data = {
            "run_id": run.run_id,
            "connector_id": connector_id,
            "request_id": UUID(pending.request_id),
            "body": package(run.model_dump(mode="json")),
        }
    try:
        yield data
    finally:
        with Session(postgres_engine) as session, session.begin():
            assert session.scalar(text("SELECT current_database()")) == "erp_e2e"
            session.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == account_id))
            session.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            for model in (ReactionxEvent, ReactionxResultReceipt):
                table = model.__tablename__
                session.execute(text(f"ALTER TABLE {table} DISABLE TRIGGER {table}_immutable"))
                session.execute(delete(model).where(model.run_id == data["run_id"]))
                session.execute(text(f"ALTER TABLE {table} ENABLE TRIGGER {table}_immutable"))
            session.execute(
                delete(ReactionxCommand).where(ReactionxCommand.connector_id == connector_id)
            )
            session.execute(delete(ReactionxRun).where(ReactionxRun.id == data["run_id"]))
            session.execute(delete(ReactionxConnector).where(ReactionxConnector.id == connector_id))
            session.execute(delete(UserRole).where(UserRole.user_id == account_id))
            session.execute(delete(AuthSession).where(AuthSession.id == session_id))
            session.execute(delete(UserAccount).where(UserAccount.id == account_id))
            session.execute(delete(Person).where(Person.id == person_id))


@pytest.mark.parametrize("winner", ["archive", "result"])
def test_independent_connections_result_and_archive_cannot_both_commit(
    postgres_engine, committed_unknown, winner
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.core.errors import ProblemError
    from app.reactionx.models import ReactionxConnector
    from app.reactionx.results import ResultService
    from app.reactionx.schemas import ResultEnvelope
    from app.reactionx.uncertainty import UncertaintyService

    data = committed_unknown
    locked, other_started = Event(), Event()
    backends = {}

    def execute(kind):
        with Session(postgres_engine) as session:
            backend = session.scalar(text("SELECT pg_backend_pid()"))
            backends[kind] = backend
            service = UncertaintyService(session) if kind == "archive" else ResultService(session)
            actor = current_actor(session, data["run_id"])
            connector = session.get(ReactionxConnector, data["connector_id"])
            if kind == winner:
                real_lock = service.recovery._run

                def hold_lock(identity):
                    row = real_lock(identity)
                    locked.set()
                    assert other_started.wait(timeout=10)
                    loser = "result" if winner == "archive" else "archive"
                    deadline = monotonic() + 5
                    while not session.scalar(
                        text("SELECT :holder = ANY(pg_blocking_pids(:pid))"),
                        {"holder": backend, "pid": backends[loser]},
                    ):
                        assert monotonic() < deadline, "loser never waited on the winner's lock"
                        sleep(0.01)
                    return row

                service.recovery._run = hold_lock
            else:
                assert locked.wait(timeout=10)
                other_started.set()
            try:
                if kind == "archive":
                    service.confirm_archive(actor, data["run_id"], data["request_id"], True)
                else:
                    service.submit(connector, ResultEnvelope.model_validate(data["body"]))
                return backend, kind, 200
            except ProblemError as error:
                return backend, kind, error.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(execute, kind) for kind in ("archive", "result")]
        outcomes = [future.result(timeout=30) for future in futures]
    assert len({row[0] for row in outcomes}) == 2
    assert {kind: status for _, kind, status in outcomes} == {
        winner: 200,
        ("result" if winner == "archive" else "archive"): 409,
    }
    with Session(postgres_engine) as session:
        row = session.get(ReactionxRun, data["run_id"])
        receipt = session.get(ReactionxResultReceipt, data["run_id"])
        if winner == "archive":
            assert row.status == "uncertain_closed" and receipt is None
            assert row.uncertainty_record["receipt"]["status"] == "execution_unknown"
        else:
            assert row.status == "completed" and receipt is not None
            assert row.uncertainty_record["receipt"] is None


def test_actual_resource_lock_wait_does_not_outlive_same_issuer_session(
    postgres_engine, committed_unknown, monkeypatch
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep

    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.core.errors import ProblemError
    from app.reactionx.models import ReactionxConnector
    from app.reactionx.uncertainty import UncertaintyService

    data = committed_unknown
    now = [datetime.now(UTC)]
    expires = now[0] + timedelta(seconds=30)
    with Session(postgres_engine) as session:
        actor = current_actor(session, data["run_id"])
        actor.session.expires_at = expires
        session.commit()

    class AuthClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now[0].astimezone(tz) if tz else now[0].replace(tzinfo=None)

    monkeypatch.setattr("app.auth.dependencies.datetime", AuthClock)
    entered = Event()
    worker = []

    def confirm():
        with Session(postgres_engine) as session:
            worker.append(session.scalar(text("SELECT pg_backend_pid()")))
            service = UncertaintyService(session, clock=lambda: now[0])
            actor = current_actor(session, data["run_id"])
            real_lock = service.recovery._run

            def waiting(identity):
                entered.set()
                return real_lock(identity)

            service.recovery._run = waiting
            try:
                service.confirm_archive(actor, data["run_id"], data["request_id"], True)
            except ProblemError as error:
                return error.status
            return 200

    with Session(postgres_engine) as blocker:
        blocker.execute(
            select(ReactionxConnector)
            .where(ReactionxConnector.id == data["connector_id"])
            .with_for_update()
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(confirm)
            try:
                assert entered.wait(timeout=10)
                deadline = monotonic() + 5
                while True:
                    with postgres_engine.connect() as probe:
                        blocked = probe.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                            {"pid": worker[0]},
                        )
                    if blocked:
                        break
                    assert monotonic() < deadline, (
                        "worker never reached actual PostgreSQL lock wait"
                    )
                    sleep(0.01)
                now[0] = expires + timedelta(microseconds=1)
            finally:
                blocker.rollback()
            assert future.result(timeout=20) == 401
    with Session(postgres_engine) as session:
        row = session.get(ReactionxRun, data["run_id"])
        assert row.status == "awaiting_sync" and row.uncertainty_record["receipt"] is None


def test_list_does_not_return_names_after_subject_permission_changes(
    db_client, db, unknown, monkeypatch
):
    from app.core.errors import ProblemError
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS
    from app.reactionx.pairing import PairingService
    from app.reactionx.uncertainty import UncertaintyService

    assert declare(db_client, unknown).status_code == 200
    actor = current_actor(db, UUID(unknown[1]["run_id"]))
    real_actor = PairingService._actor
    calls = []

    def revoke_at_final_boundary(service, *args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            db.add(
                UserPermissionOverride(
                    user_id=actor.account.id,
                    permission_id=PERMISSIONS["reactionx.conduct"].id,
                    effect="deny",
                    scope_kind="person",
                    scope_id=UUID(unknown[4]["subject_person_id"]),
                )
            )
            db.flush()
        return real_actor(service, *args, **kwargs)

    monkeypatch.setattr(PairingService, "_actor", revoke_at_final_boundary)
    with pytest.raises(ProblemError):
        UncertaintyService(db).list_pending(actor)


def test_undeclared_awaiting_sync_run_is_not_a_pending_archive(db_client, login_as_admin, unknown):
    response = db_client.get(WEB + "/uncertainties", headers=browser_headers(login_as_admin))
    assert response.status_code == 200, response.text
    assert response.json() == {"items": [], "total": 0, "offset": 0, "limit": 20}
