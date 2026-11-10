"""Real HTTP + PostgreSQL command/lease safety, using synthetic devices only."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.auth.models import AuthSession, UserAccount
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from app.reactionx.models import ReactionxRun
from tests.api.test_reactionx_pairing import (
    MACHINE,
    WEB,
    browser_headers,
    claim,
    machine_headers,
)


def paired(client, login, *, bound=True):
    tab = str(uuid4())
    response = client.post(
        WEB + "/pairings",
        headers=browser_headers(login),
        json={"browser_tab_id": tab} if bound else {},
    )
    assert response.status_code == 200, response.text
    pairing = response.json()
    value = claim(client, pairing).json()
    body = {"verification_phrase": value["verification_phrase"]}
    assert (
        client.post(
            WEB + f"/pairings/{pairing['pairing_id']}/confirm",
            headers=browser_headers(login),
            json=body,
        ).status_code
        == 200
    )
    assert (
        client.post(
            MACHINE + f"/pairings/{pairing['pairing_id']}/confirm",
            headers=machine_headers(value),
            json=body,
        ).json()["status"]
        == "active"
    )
    return value, tab


def spec(mode="manual"):
    fixtures = json.loads(
        (Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    manifest = next(c["manifest"] for c in fixtures["cases"] if c["name"] == mode)
    return {k: manifest[k] for k in ("mode", "config", "devices", "layout")}


def heartbeat(client, value, *, instance=None, version="0.3.0"):
    return client.post(
        MACHINE + "/heartbeat",
        headers=machine_headers(value),
        json={
            "protocol_version": 1,
            "connector_version": version,
            "instance_id": instance or str(uuid4()),
            "devices": [
                {**d, "label": f"灯 {d['display_number']}", "connected": True}
                for d in spec()["devices"]
            ],
        },
    )


def prepare(client, login, value, tab, *, operation=None, mode="manual"):
    return client.post(
        WEB + "/runs/prepare",
        headers=browser_headers(login),
        json={
            "operation_id": operation or str(uuid4()),
            "person_id": str(login.account_id),
            "connector_id": value["connector_id"],
            "tab_id": tab,
            **spec(mode),
        },
    )


@pytest.fixture
def rig(db_client, db, login_as_admin, platform_users):
    value, tab = paired(db_client, login_as_admin)
    response = heartbeat(db_client, value)
    assert response.status_code == 200, response.text
    person = platform_users["accounts"]["admin"].person_id

    def prep(*, mode="manual", operation=None, selected_tab=None):
        return db_client.post(
            WEB + "/runs/prepare",
            headers=browser_headers(login_as_admin),
            json={
                "operation_id": operation or str(uuid4()),
                "person_id": str(person),
                "connector_id": value["connector_id"],
                "tab_id": selected_tab or tab,
                **spec(mode),
            },
        )

    return value, tab, prep


def poll(client, value, cursor=0):
    response = client.get(MACHINE + f"/commands?cursor={cursor}", headers=machine_headers(value))
    assert response.status_code == 200, response.text
    return response.json()


def ack(client, value, command, state, *, status="completed", off=None):
    return client.post(
        MACHINE + f"/commands/{command['operation_id']}/ack",
        headers=machine_headers(value),
        json={
            "status": status,
            "run_state": state,
            "off_confirmed_device_keys": off or [],
        },
    )


def ready(client, value, prep):
    response = prep()
    assert response.status_code == 202, response.text
    run = response.json()
    command = poll(client, value)["commands"][0]
    assert command["kind"] == "prepare"
    response = ack(client, value, command, "ready")
    assert response.status_code == 200, response.text
    return run


def command(client, login, run, tab, kind, operation=None):
    return client.post(
        WEB + f"/runs/{run['run_id']}/{kind}",
        headers=browser_headers(login),
        json={
            "operation_id": operation or str(uuid4()),
            "tab_id": tab,
        },
    )


def test_pairing_without_tab_is_not_local_control_proof(db_client, login_as_admin, platform_users):
    value, tab = paired(db_client, login_as_admin, bound=False)
    assert heartbeat(db_client, value).status_code == 200
    response = db_client.post(
        WEB + "/runs/prepare",
        headers=browser_headers(login_as_admin),
        json={
            "operation_id": str(uuid4()),
            "person_id": str(platform_users["accounts"]["admin"].person_id),
            "connector_id": value["connector_id"],
            "tab_id": tab,
            **spec(),
        },
    )
    assert response.status_code == 403


def test_run_idempotency_tab_ownership_and_stop_priority(db_client, login_as_admin, rig):
    value, tab, prep = rig
    operation = str(uuid4())
    first = prep(operation=operation)
    assert first.status_code == 202, first.text
    assert prep(operation=operation).json()["run_id"] == first.json()["run_id"]
    assert prep(operation=operation, mode="standard").status_code == 409
    run = first.json()
    assert command(db_client, login_as_admin, run, str(uuid4()), "start").status_code == 403
    queued = poll(db_client, value)["commands"][0]
    assert ack(db_client, value, queued, "ready").status_code == 200
    start_id = str(uuid4())
    assert command(db_client, login_as_admin, run, tab, "start", start_id).status_code == 202
    assert command(db_client, login_as_admin, run, tab, "start", start_id).status_code == 202
    assert command(db_client, login_as_admin, run, tab, "stop", start_id).status_code == 409
    stop = command(db_client, login_as_admin, run, tab, "stop")
    assert stop.status_code == 202
    delivered = poll(db_client, value, 2_000_000)["commands"]
    assert [c["kind"] for c in delivered] == ["stop"]
    assert (
        command(db_client, login_as_admin, run, tab, "start", start_id).json()["status"]
        == "cancelled"
    )
    for _ in range(2):
        assert ack(db_client, value, delivered[0], None, status="accepted").status_code == 200
        assert db_client.get(WEB + f"/runs/{run['run_id']}").json()["status"] == "stopping"
    assert prep().status_code == 409
    assert (
        ack(db_client, value, delivered[0], "stopped_confirmed", off=["wrong"]).status_code == 422
    )
    keys = [d["device_key"] for d in run["manifest"]["devices"]]
    confirmed = ack(db_client, value, delivered[0], "stopped_confirmed", off=keys)
    assert confirmed.status_code == 200
    assert confirmed.json()["run_status"] == "cancelled"
    status = db_client.get(WEB + f"/runs/{run['run_id']}").json()
    assert status["status"] == "cancelled" and status["stop_confirmed"] is True


@pytest.mark.parametrize("revoke", ["session", "login_expired", "account", "conduct", "expired"])
def test_revocation_and_expiry_only_deliver_safety_stop(db_client, db, login_as_admin, rig, revoke):
    value, tab, prep = rig
    run = ready(db_client, value, prep)
    assert command(db_client, login_as_admin, run, tab, "start").status_code == 202
    if revoke in ("session", "login_expired"):
        session = db.scalar(
            select(AuthSession).where(AuthSession.user_id == login_as_admin.account_id)
        )
        if revoke == "session":
            session.revoked_at = datetime.now(UTC)
        else:
            session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    elif revoke == "account":
        db.get(UserAccount, login_as_admin.account_id).status = "disabled"
    elif revoke == "conduct":
        db.add(
            UserPermissionOverride(
                user_id=login_as_admin.account_id,
                permission_id=PERMISSIONS["reactionx.conduct"].id,
                effect="deny",
                scope_kind="global",
            )
        )
    else:
        db.get(ReactionxRun, UUID(run["run_id"])).lease_expires_at = datetime.now(UTC) - timedelta(
            seconds=1
        )
    db.commit()
    delivered = poll(db_client, value)["commands"]
    assert [c["kind"] for c in delivered] == ["stop"]
    assert delivered[0]["lease_remaining_ms"] == 0
    assert db.get(ReactionxRun, UUID(run["run_id"])).status == "stopping"
    assert (
        ack(db_client, value, delivered[0], "stopped_unconfirmed", status="failed").status_code
        == 200
    )
    assert db.get(ReactionxRun, UUID(run["run_id"])).status == "unconfirmed"
    assert (
        ack(
            db_client,
            value,
            delivered[0],
            "stopped_confirmed",
            off=[d["device_key"] for d in run["manifest"]["devices"]],
        ).status_code
        == 200
    )
    stored = db.get(ReactionxRun, UUID(run["run_id"]))
    assert stored.status == "cancelled" and stored.stop_confirmed
    assert db_client.post(
        WEB + f"/runs/{run['run_id']}/lease",
        headers=browser_headers(login_as_admin),
        json={"tab_id": tab},
    ).status_code in (401, 403, 409)


def test_formal_modes_closed_and_status_truthful(db_client, rig):
    _, _, prep = rig
    for mode in (
        "standard",
        "sequence",
        "simultaneous",
        "base",
        "auditory",
        "true_false",
        "counter",
        "whack",
    ):
        assert prep(mode=mode).status_code == 409
    modes = db_client.get(WEB + "/modes").json()
    assert len(modes) == 9
    assert all(
        not m["available"] and m["reason"] == "hardware_unverified"
        for m in modes
        if m["mode"] != "manual"
    )


def test_restart_never_replays_start_or_auto_resumes(db_client, login_as_admin, rig):
    value, tab, prep = rig
    run = ready(db_client, value, prep)
    assert command(db_client, login_as_admin, run, tab, "start").status_code == 202
    start = poll(db_client, value)["commands"][0]
    assert start["kind"] == "start"
    assert not poll(db_client, value)["commands"]
    assert heartbeat(db_client, value).status_code == 200  # new instance
    assert [c["kind"] for c in poll(db_client, value)["commands"]] == ["stop"]
    assert ack(db_client, value, start, "running", status="accepted").status_code == 409
    assert command(db_client, login_as_admin, run, tab, "start").status_code == 409


def test_http_csrf_and_strict_device_payload(db_client, login_as_admin, rig):
    value, tab, _ = rig
    route = WEB + f"/connectors/{value['connector_id']}/commands"
    payload = {
        "operation_id": str(uuid4()),
        "tab_id": tab,
        "kind": "scan",
        "payload": {"duration_ms": 1000},
    }
    assert db_client.post(route, json=payload).status_code == 403
    assert (
        db_client.post(
            route, json={**payload, "bytes": "01"}, headers=browser_headers(login_as_admin)
        ).status_code
        == 422
    )
    response = db_client.post(route, json=payload, headers=browser_headers(login_as_admin))
    assert response.status_code == 202, response.text
    assert response.json()["run_status"] is None
    cmd = poll(db_client, value)["commands"][0]
    assert cmd["run_id"] is None and cmd["kind"] == "scan"
    assert "no-store" in response.headers["cache-control"]
