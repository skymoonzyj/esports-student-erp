"""Fail closed before any disposable fixture writes; verify persisted outcomes."""

import importlib
import json
from contextlib import contextmanager
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.auth.models import UserAccount
from app.identity.models import Person
from app.reactionx.models import ReactionxConnector, ReactionxResultReceipt, ReactionxRun
from tests.unit.test_reactionx_integration_policy import settings


def module():
    return importlib.import_module("app.cli.seed_reactionx_e2e")


@pytest.mark.parametrize(
    "environment,url",
    [
        ("production", "postgresql://localhost/erp_e2e"),
        ("test", "postgresql://localhost/production"),
        ("test", "postgresql://localhost/erp_e2e?dbname=production"),
        ("test", "postgresql://other-host/erp_e2e"),
    ],
)
def test_guard_refuses_before_database_access(monkeypatch, environment, url):
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://localhost/erp_e2e")
    value = settings().model_copy(update={"app_env": environment, "database_url": url})
    with pytest.raises(module().ReactionxE2ERefused, match="isolated"):
        module().require_environment(value)


def test_missing_test_url_is_not_implicit_permission(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    with pytest.raises(module().ReactionxE2ERefused):
        module().require_environment(settings())


@contextmanager
def probe_server(*, enabled=False, wrong_denial=None):
    """Only replace the external server; real HTTP/cookies/probe parsing run.

    Reject malformed auth, pairing, heartbeat and prepare inputs so an unrelated
    403/409/422 cannot masquerade as proof that formal execution is closed.
    """
    fixtures = json.loads(
        (Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    manifest = next(case["manifest"] for case in fixtures["cases"] if case["name"] == "standard")
    identity = "e8400000-0000-4000-8000-000000000001"
    run_id = "e8400000-0000-4000-8000-000000000002"
    operation = "e8400000-0000-4000-8000-000000000003"
    state = {
        "formal_prepare": 0,
        "revoked": 0,
        "logout": 0,
        "cancelled": False,
        "digest": None,
        "tab": None,
        "browser": False,
        "device": False,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, body=None, *, cookie=False):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            if cookie:
                self.send_header("Set-Cookie", "probe-session=synthetic; Path=/")
            self.end_headers()
            if body is not None:
                self.wfile.write(json.dumps(body).encode())

        def browser(self):
            return (
                self.headers.get("Cookie") == "probe-session=synthetic"
                and self.headers.get("X-CSRF-Token") == "synthetic-csrf"
            )

        def machine(self):
            header = self.headers.get("Authorization", "")
            prefix = f"ReactionX {identity}."
            return (
                header.startswith(prefix)
                and "Cookie" not in self.headers
                and sha256(header[len(prefix) :].encode("ascii")).hexdigest() == state["digest"]
            )

        def claim_view(self):
            return {
                "pairing_id": identity,
                "connector_id": identity,
                "verification_phrase": "abcdef-123456",
                "expires_at": "2099-01-01T00:00:00Z",
                "status": "active" if state["browser"] and state["device"] else "pending",
                "browser_confirmed": state["browser"],
                "device_confirmed": state["device"],
                "actor_account_id": "e1100000-0000-4000-8000-000000000001",
                "actor_name": "synthetic",
                "origin": "https://skymoonzyj.l.cd:18080",
            }

        def do_POST(self):
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            if self.path == "/api/v1/auth/login":
                self.reply(200, {"csrf_token": "synthetic-csrf", "user": {}}, cookie=True)
            elif self.path == "/api/v1/auth/logout" and self.browser():
                state["logout"] += 1
                self.reply(204)
            elif self.path == "/api/v1/reactionx/pairings" and self.browser():
                state["tab"] = body["browser_tab_id"]
                UUID(state["tab"])
                self.reply(
                    200,
                    {
                        "pairing_id": identity,
                        "challenge": "A" * 43,
                        "pair_uri": "reactionx-erp://pair?challenge=" + "A" * 43,
                        "expires_at": "2099-01-01T00:00:00Z",
                        "verification_phrase": "abcdef-123456",
                    },
                )
            elif self.path == "/api/v1/reactionx-client/pairings/claim":
                if (
                    body["challenge"] != "A" * 43
                    or body["client_info"]["origin"] != "https://skymoonzyj.l.cd:18080"
                ):
                    self.reply(422, {"code": "invalid_claim"})
                    return
                state["digest"] = body["credential_digest"]
                self.reply(200, self.claim_view())
            elif self.path.endswith(f"/pairings/{identity}/confirm"):
                if body["verification_phrase"] != "abcdef-123456":
                    self.reply(422, {"code": "invalid_phrase"})
                elif self.path.startswith("/api/v1/reactionx-client/") and self.machine():
                    state["device"] = True
                    self.reply(200, self.claim_view())
                elif self.browser():
                    state["browser"] = True
                    self.reply(200, self.claim_view())
                else:
                    self.reply(403, {"code": "invalid_authority"})
            elif self.path == "/api/v1/reactionx-client/heartbeat" and self.machine():
                good = (
                    state["browser"]
                    and state["device"]
                    and body["protocol_version"] == 1
                    and body["connector_version"] == "0.3.0"
                    and body["devices"]
                    == [
                        {**device, "label": f"probe-{device['display_number']}", "connected": True}
                        for device in manifest["devices"]
                    ]
                )
                self.reply(
                    200 if good else 422,
                    {
                        "connector": {
                            "connector_id": identity,
                            "online": good,
                            "compatible": good,
                            "locally_confirmed": good,
                        },
                        "run": None,
                        "safety_only": False,
                    },
                )
            elif self.path == "/api/v1/reactionx/runs/prepare" and self.browser():
                expected = {key: manifest[key] for key in ("mode", "config", "devices", "layout")}
                valid = (
                    all(body[key] == value for key, value in expected.items())
                    and body["person_id"] == "e8300000-0000-4000-8000-000000000001"
                    and body["connector_id"] == identity
                    and body["tab_id"] == state["tab"]
                )
                UUID(body["operation_id"])
                if not valid:
                    self.reply(422, {"code": "invalid_prepare"})
                    return
                state["formal_prepare"] += 1
                if wrong_denial is not None:
                    self.reply(wrong_denial[0], {"code": wrong_denial[1]})
                elif enabled:
                    self.reply(
                        202,
                        {
                            "run_id": run_id,
                            "connector_id": identity,
                            "status": "prepared",
                            "manifest": {**manifest, "run_id": run_id},
                            "lease_remaining_ms": 15000,
                            "lease_expires_at": "2099-01-01T00:00:00Z",
                            "stop_confirmed": False,
                            "result_saved": False,
                        },
                    )
                else:
                    self.reply(409, {"code": "reactionx_hardware_unverified"})
            elif self.path == f"/api/v1/reactionx/runs/{run_id}/stop" and self.browser():
                self.reply(
                    202,
                    {
                        "operation_id": operation,
                        "run_id": run_id,
                        "kind": "stop",
                        "sequence": 2,
                        "status": "queued",
                        "run_status": "stopping",
                    },
                )
            elif (
                self.path == f"/api/v1/reactionx-client/commands/{operation}/ack" and self.machine()
            ):
                valid = body == {
                    "status": "completed",
                    "run_state": "stopped_confirmed",
                    "off_confirmed_device_keys": [
                        device["device_key"] for device in manifest["devices"]
                    ],
                }
                state["cancelled"] = valid
                self.reply(
                    200 if valid else 422,
                    {
                        "operation_id": operation,
                        "run_id": run_id,
                        "kind": "stop",
                        "sequence": 2,
                        "status": "completed",
                        "run_status": "cancelled",
                    },
                )
            else:
                self.reply(403, {"code": "unrelated_authorization_failure"})

        def do_DELETE(self):
            if self.path == f"/api/v1/reactionx/connectors/{identity}" and self.browser():
                state["revoked"] += 1
                self.reply(204)
            else:
                self.reply(403, {"code": "unrelated_authorization_failure"})

        def do_GET(self):
            if self.path == "/api/v1/reactionx/modes":
                self.reply(
                    200,
                    [
                        {
                            "mode": mode,
                            "available": mode == "manual",
                            "verification_status": "unverified",
                            "reason": "hardware_unverified",
                        }
                        for mode in (
                            "manual",
                            "standard",
                            "sequence",
                            "simultaneous",
                            "base",
                            "auditory",
                            "true_false",
                            "counter",
                            "whack",
                        )
                    ],
                )
            elif self.path == "/api/v1/reactionx-client/commands" and self.machine():
                self.reply(
                    200,
                    {
                        "commands": [{"operation_id": operation, "kind": "stop", "run_id": run_id}],
                        "cursor": 2,
                        "run": None,
                        "lease_remaining_ms": 0,
                        "poll_after_ms": 500,
                    },
                )
            else:
                self.reply(403, {"code": "unrelated_authorization_failure"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_closed_probe_never_inherits_ambient_socks_proxy(monkeypatch):
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1")
    with probe_server() as (origin, state):
        module().probe_closed(origin)
        assert state["formal_prepare"] == 1
        assert state["revoked"] == 1 and state["logout"] == 1


def test_closed_probe_rejects_enabled_execution_even_when_mode_metadata_is_closed():
    with probe_server(enabled=True) as (origin, state):
        with pytest.raises(module().ReactionxE2EEvidenceError, match="^default_formal_open$"):
            module().probe_closed(origin)
        assert state["formal_prepare"] == 1 and state["cancelled"]
        assert state["revoked"] == 1 and state["logout"] == 1


@pytest.mark.parametrize(
    "wrong_denial",
    [
        (403, "reactionx_forbidden"),
        (409, "reactionx_devices_unavailable"),
        (422, "reactionx_invalid_manifest"),
    ],
)
def test_unrelated_denial_is_not_closed_policy_evidence(wrong_denial):
    with probe_server(wrong_denial=wrong_denial) as (origin, state):
        with pytest.raises(module().ReactionxE2EEvidenceError, match="^default_formal_open$"):
            module().probe_closed(origin)
        assert state["formal_prepare"] == 1
        assert state["revoked"] == 1 and state["logout"] == 1


@pytest.mark.parametrize("enabled", [False, True])
def test_actual_api_closed_probe_is_mutation_sensitive(
    db_client, db, test_settings, monkeypatch, enabled
):
    import os

    import httpx

    value = test_settings.model_copy(update={"database_url": os.environ["TEST_DATABASE_URL"]})
    module().seed(db, value)
    db_client.app.state.settings.reactionx_synthetic_formal_enabled = enabled
    client_class = httpx.Client

    def request(request):
        response = db_client._transport.handle_request(request)
        # Starlette uses httpx2 while the standalone probe uses httpx. Rewrap
        # transport bytes/headers only; do not fake an application response.
        try:
            return httpx.Response(
                response.status_code,
                headers=response.headers.raw,
                content=response.read(),
                request=request,
            )
        finally:
            response.close()

    def client(**kwargs):
        # Keep real app routes/session/CSRF/RBAC/services/DB. Only replace the
        # external socket with TestClient's ASGI transport and separate cookie
        # jars, so browser cookies are not machine credential proof.
        return client_class(**kwargs, transport=httpx.MockTransport(request))

    monkeypatch.setattr(httpx, "Client", client)
    if enabled:
        with pytest.raises(module().ReactionxE2EEvidenceError, match="^default_formal_open$"):
            module().probe_closed("http://127.0.0.1:8014")
    else:
        module().probe_closed("http://127.0.0.1:8014")
    assert db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 0
    runs = list(db.scalars(select(ReactionxRun)))
    assert len(runs) == int(enabled)
    if enabled:
        assert runs[0].status == "cancelled" and runs[0].stop_confirmed
    connectors = list(db.scalars(select(ReactionxConnector)))
    assert len(connectors) == 1 and connectors[0].status == "revoked"


def test_seed_creates_only_fixture_people_not_outcomes(db, test_settings, monkeypatch):
    import os

    value = test_settings.model_copy(update={"database_url": os.environ["TEST_DATABASE_URL"]})
    module().seed(db, value)
    from app.rbac.service import RbacService

    assert module().ARCHIVE_OPERATOR_ID != UUID("e1100000-0000-4000-8000-000000000001")
    assert RbacService(db).account_has_role(module().ARCHIVE_OPERATOR_ID, "super_admin")
    for person_id in module().SUBJECT_IDS:
        assert db.get(Person, person_id) is not None
        assert db.scalar(select(UserAccount.id).where(UserAccount.person_id == person_id)) is None
    for model in (ReactionxConnector, ReactionxRun, ReactionxResultReceipt):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    with pytest.raises(module().ReactionxE2EEvidenceError, match="missing_results"):
        module().verify(db, value)


def test_verifier_rejects_incomplete_acceptance_even_if_some_results_exist(
    db, test_settings, monkeypatch
):
    import os

    value = test_settings.model_copy(update={"database_url": os.environ["TEST_DATABASE_URL"]})
    module().seed(db, value)
    # Empty acceptance is never a green verifier; no fake receipts are inserted.
    with pytest.raises(module().ReactionxE2EEvidenceError):
        module().verify(db, value)


def archive_evidence():
    """Real ORM evidence objects, no session, connection or seeded outcome."""
    from copy import deepcopy
    from datetime import UTC, datetime

    from app.audit.models import AuditEvent
    from app.reactionx.models import ReactionxCommand
    from app.reactionx.schemas import canonical_digest

    raw = json.loads((Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text())
    manifest = deepcopy(next(case["manifest"] for case in raw["cases"] if case["name"] == "manual"))
    ids = [UUID(f"e8500000-0000-4000-8000-{i:012d}") for i in range(1, 9)]
    run_id, connector_id, session_id, tab_id, start_id, stop_id, request_id, archive_id = ids
    actor = UUID("e1100000-0000-4000-8000-000000000009")
    subject = UUID("e8300000-0000-4000-8000-000000000001")
    manifest.update(
        run_id=str(run_id),
        actor_account_id=str(actor),
        subject_person_id=str(subject),
        connector_id=str(connector_id),
    )
    manifest["manifest_digest"] = canonical_digest(manifest, exclude={"manifest_digest"})
    declaration = {
        "version": 1,
        "kind": "start_delivery_unknown",
        "run_id": str(run_id),
        "actor_account_id": str(actor),
        "subject_person_id": str(subject),
        "connector_id": str(connector_id),
        "manifest_digest": manifest["manifest_digest"],
        "stop_operation_id": str(stop_id),
        "local_evidence_digest": "a" * 64,
    }
    digest = canonical_digest(declaration)
    request = {
        "id": str(request_id),
        "issuer": str(actor),
        "issuer_session": str(session_id),
        "declaration_digest": digest,
        "reason": "synthetic unknown delivery evidence checked",
        "requested_at": "2026-11-04T00:00:00.000Z",
        "expires_at": "2026-11-04T00:05:00.000Z",
        "confirmed_at": "2026-11-04T00:00:01.000Z",
    }
    receipt = {
        "version": 1,
        "archive_id": str(archive_id),
        "run_id": str(run_id),
        "connector_id": str(connector_id),
        "manifest_digest": manifest["manifest_digest"],
        "declaration_digest": digest,
        "stop_operation_id": str(stop_id),
        "status": "execution_unknown",
        "closed_at": request["confirmed_at"],
    }
    run = ReactionxRun(
        id=run_id,
        actor_account_id=actor,
        subject_person_id=subject,
        connector_id=connector_id,
        browser_session_id=session_id,
        browser_tab_id=tab_id,
        manifest=manifest,
        manifest_digest=manifest["manifest_digest"],
        status="uncertain_closed",
        stop_confirmed=True,
        uncertainty_record={
            "declaration": declaration,
            "declaration_digest": digest,
            "request": request,
            "receipt": receipt,
        },
    )
    commands = []
    for sequence, (kind, operation, payload) in enumerate(
        [
            ("prepare", UUID("e8500000-0000-4000-8000-000000000009"), {"manifest": manifest}),
            ("start", start_id, {"manifest_digest": manifest["manifest_digest"]}),
            ("stop", stop_id, {"reason": "user_stopped"}),
        ],
        1,
    ):
        commands.append(
            ReactionxCommand(
                operation_id=operation,
                run_id=run_id,
                connector_id=connector_id,
                actor_account_id=actor,
                subject_person_id=subject,
                browser_tab_id=tab_id,
                kind=kind,
                sequence=sequence,
                payload=payload,
                status="completed" if kind == "stop" else "cancelled",
                delivery_envelope={
                    "protocol_version": 1,
                    "operation_id": str(operation),
                    "run_id": str(run_id),
                    "actor_account_id": str(actor),
                    "subject_person_id": str(subject),
                    "connector_id": str(connector_id),
                    "sequence": sequence,
                    "expires_at": "2026-11-04T00:00:05.000Z",
                    "lease_remaining_ms": 15000,
                    "kind": kind,
                    "payload": payload,
                },
                ack_payload={
                    "status": "completed",
                    "run_state": "stopped_confirmed",
                    "off_confirmed_device_keys": ["lamp-a", "lamp-b"],
                }
                if kind == "stop"
                else None,
                acknowledged_at=datetime(2026, 11, 4, tzinfo=UTC) if kind == "stop" else None,
            )
        )
    audits = [
        AuditEvent(
            actor_user_id=actor,
            action="reactionx.uncertainty." + action,
            resource_type="reactionx_run",
            resource_id=run_id,
            purpose=request["reason"],
            event_metadata={"operation_id": str(request_id), "session_id": str(session_id)},
            outcome="allowed",
        )
        for action in ("requested", "archived")
    ]
    return run, commands, audits


def test_persisted_archive_snapshot_is_independent_non_result_evidence():
    run, commands, audits = archive_evidence()
    module().verify_archive_evidence(run, commands, audits, event_count=0, result_count=0)


@pytest.mark.parametrize(
    "fault",
    ["start_ack", "off", "subject", "receipt", "digest", "session", "audit", "result", "events"],
)
def test_archive_snapshot_rejects_unsupported_or_scored_closure(fault):
    run, commands, audits = archive_evidence()
    if fault == "start_ack":
        commands[1].ack_payload = {
            "status": "accepted",
            "run_state": "running",
            "off_confirmed_device_keys": [],
        }
    elif fault == "off":
        commands[2].ack_payload["off_confirmed_device_keys"] = ["lamp-a"]
    elif fault == "subject":
        run.subject_person_id = UUID("e8300000-0000-4000-8000-000000000002")
    elif fault == "receipt":
        run.uncertainty_record["receipt"]["run_id"] = str(
            UUID("e8500000-0000-4000-8000-000000000099")
        )
    elif fault == "digest":
        run.uncertainty_record["declaration_digest"] = "b" * 64
    elif fault == "session":
        run.uncertainty_record["request"]["issuer_session"] = str(
            UUID("e8500000-0000-4000-8000-000000000099")
        )
    elif fault == "audit":
        audits.append(audits[0])
    with pytest.raises(module().ReactionxE2EEvidenceError):
        module().verify_archive_evidence(
            run,
            commands,
            audits,
            event_count=int(fault == "events"),
            result_count=int(fault == "result"),
        )


def test_actual_api_archive_snapshot_keeps_second_operator_and_zero_results(
    db_client, db, test_settings
):
    import os
    from uuid import uuid4

    from app.audit.models import AuditEvent
    from app.reactionx.models import ReactionxCommand, ReactionxEvent
    from app.reactionx.schemas import canonical_digest
    from tests.api.conftest import LoginResult
    from tests.api.test_reactionx_pairing import WEB, browser_headers
    from tests.api.test_reactionx_runs import ack, command, heartbeat, paired, poll, ready, spec
    from tests.api.test_reactionx_uncertainty import confirm_archive, declare, request_archive

    value = test_settings.model_copy(update={"database_url": os.environ["TEST_DATABASE_URL"]})
    module().seed(db, value)
    credential = module().E2E_CREDENTIALS["life_teacher"]
    response = db_client.post(
        "/api/v1/auth/login",
        json={"identifier": credential.identifier, "password": credential.password},
    )
    assert response.status_code == 200
    login = LoginResult(
        account_id=module().ARCHIVE_OPERATOR_ID, csrf_token=response.json()["csrf_token"]
    )
    connector, tab = paired(db_client, login)
    assert heartbeat(db_client, connector).status_code == 200
    run = ready(
        db_client,
        connector,
        lambda: db_client.post(
            WEB + "/runs/prepare",
            headers=browser_headers(login),
            json={
                "operation_id": str(uuid4()),
                "person_id": str(module().SUBJECT_IDS[0]),
                "connector_id": connector["connector_id"],
                "tab_id": tab,
                **spec(),
            },
        ),
    )
    assert command(db_client, login, run, tab, "start").status_code == 202
    start = poll(db_client, connector)["commands"][0]
    assert start["kind"] == "start"  # Deliberately no ACK.
    assert command(db_client, login, run, tab, "stop").status_code == 202
    stop = poll(db_client, connector)["commands"][0]
    assert stop["kind"] == "stop"
    assert (
        ack(
            db_client,
            connector,
            stop,
            "stopped_confirmed",
            off=[device["device_key"] for device in run["manifest"]["devices"]],
        ).status_code
        == 200
    )
    declaration = {
        "version": 1,
        "kind": "start_delivery_unknown",
        "run_id": run["run_id"],
        "actor_account_id": str(login.account_id),
        "subject_person_id": str(module().SUBJECT_IDS[0]),
        "connector_id": connector["connector_id"],
        "manifest_digest": run["manifest"]["manifest_digest"],
        "stop_operation_id": stop["operation_id"],
        "local_evidence_digest": "a" * 64,
    }
    unknown = connector, run, start, stop, declaration
    assert declare(db_client, unknown).json()["declaration_digest"] == canonical_digest(declaration)
    requested = request_archive(db_client, login, unknown)
    assert requested.status_code == 200
    confirmed = confirm_archive(db_client, login, unknown, requested.json()["request_id"])
    assert confirmed.status_code == 200
    assert (
        confirm_archive(db_client, login, unknown, requested.json()["request_id"]).json()
        == confirmed.json()
    )
    identity = UUID(run["run_id"])
    commands = list(db.scalars(select(ReactionxCommand).where(ReactionxCommand.run_id == identity)))
    audits = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.resource_id == identity,
                AuditEvent.action.in_(
                    ("reactionx.uncertainty.requested", "reactionx.uncertainty.archived")
                ),
            )
        )
    )
    event_count = db.scalar(select(func.count()).select_from(ReactionxEvent))
    result_count = db.scalar(select(func.count()).select_from(ReactionxResultReceipt))
    assert event_count == result_count == 0
    module().verify_archive_evidence(
        db.get(ReactionxRun, identity, populate_existing=True),
        commands,
        audits,
        event_count=event_count,
        result_count=result_count,
    )
    # One archive alone cannot masquerade as the independent five-result lane.
    with pytest.raises(module().ReactionxE2EEvidenceError, match="missing_results"):
        module().verify(db, value)
