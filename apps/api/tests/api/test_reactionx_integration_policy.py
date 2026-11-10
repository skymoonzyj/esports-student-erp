"""Formal HTTP acceptance requires two explicit server-side gates."""

from uuid import UUID

import pytest

from app.reactionx.models import ReactionxResultReceipt
from tests.api.test_reactionx_pairing import WEB
from tests.api.test_reactionx_results import package, upload
from tests.api.test_reactionx_runs import ack, command, poll
from tests.api.test_reactionx_runs import rig as rig  # noqa: PLC0414


@pytest.mark.parametrize(
    "mode",
    ["standard", "sequence", "simultaneous", "base", "auditory", "true_false", "counter", "whack"],
)
def test_default_http_formal_modes_remain_closed_even_in_test(db_client, rig, mode):
    _, _, prepare = rig
    response = prepare(mode=mode)
    assert response.status_code == 409
    assert response.json()["code"] == "reactionx_hardware_unverified"
    assert all(
        not item["available"]
        for item in db_client.get(WEB + "/modes").json()
        if item["mode"] != "manual"
    )


@pytest.mark.parametrize("mode", ["standard", "manual"])
def test_explicit_opt_in_wires_actual_prepare_start_machine_ack_and_result(
    db_client, db, login_as_admin, rig, mode
):
    db_client.app.state.settings.reactionx_synthetic_formal_enabled = True
    connector, tab, prepare = rig
    prepared = prepare(mode=mode)
    assert prepared.status_code == 202
    run = prepared.json()
    preparation = poll(db_client, connector)["commands"][0]
    assert ack(db_client, connector, preparation, "ready").status_code == 200
    assert command(db_client, login_as_admin, run, tab, "start").status_code == 202
    start = poll(db_client, connector)["commands"][0]
    assert ack(db_client, connector, start, "running", status="accepted").status_code == 200
    body = package(run, mode)
    saved = upload(db_client, connector, run, body)
    assert saved.status_code == 200
    assert upload(db_client, connector, run, body).json() == saved.json()
    keys = [device["device_key"] for device in run["manifest"]["devices"]]
    assert ack(db_client, connector, start, "stopped_confirmed", off=keys).status_code == 200
    persisted = db.get(ReactionxResultReceipt, UUID(run["run_id"]))
    assert persisted.eligible is (mode == "standard")
    report = db_client.get(WEB + f"/results/{saved.json()['result_id']}").json()
    assert report["ranking_eligible"] is (mode == "standard")
    rank = db_client.get(WEB + f"/results/{saved.json()['result_id']}/ranking").json()
    assert len(rank["top_three"]) == (1 if mode == "standard" else 0)
