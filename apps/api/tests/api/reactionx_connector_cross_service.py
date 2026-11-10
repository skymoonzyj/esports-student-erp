"""Explicit cross-checkout gate; deliberately excluded from ordinary discovery.

Run only with the coordinator-owned disposable DB slot and RX_CONNECTOR_SOURCE.
No mock machine server, repaired DTO, synthetic receipt or handcrafted result.
"""

import hashlib
import json
import time
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.audit.models import AuditEvent
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxEvent,
    ReactionxResultReceipt,
    ReactionxRun,
)
from tests.api.reactionx_connector_harness import FORMAL_MODES, WEB, Harness


@pytest.fixture
def rig(db_client, db, login_as_admin, platform_users, tmp_path):
    value = Harness(db_client, db, login_as_admin, platform_users, tmp_path)
    try:
        yield value
    finally:
        value.close()


def assert_saved_once(rig, run_id):
    """Catch missing durable ACK, wrong ownership, duplicate ingestion/audit."""
    rig.wait(lambda: rig.store.get_run(run_id).receipt is not None, "durable receipt")
    local = rig.store.get_run(run_id)
    rig.stop_workers()
    with rig.gateway.lock:
        receipt = rig.db.scalar(select(ReactionxResultReceipt))
        assert receipt is not None
        assert rig.db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 1
        assert rig.db.scalar(select(func.count()).select_from(ReactionxEvent)) == len(
            json.loads(local.outbox.payload)["events"]
        )
        assert (
            rig.db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "reactionx.result.saved")
            )
            == 1
        )
        row = rig.db.get(ReactionxRun, UUID(run_id))
        assert str(row.actor_account_id) == rig.credential.actor_id
        assert str(row.subject_person_id) == rig.person_id
        assert str(row.connector_id) == rig.credential.connector_id
    assert local.receipt["digest"] == hashlib.sha256(local.outbox.payload).hexdigest()
    assert local.receipt["run_id"] == run_id
    assert rig.gateway.machine_cookie_count == 0
    assert rig.gateway.tls_checked
    return local


@pytest.mark.parametrize("lost_result_response", [False, True])
@pytest.mark.parametrize("subject", ["admin", "adult_without_account"])
def test_actual_manual_lifecycle_exact_receipt_and_report(rig, lost_result_response, subject):
    # Missing real Engine terminal, byte retry or C3 ACK must fail this test.
    rig.person_id = str(rig.users["people"][subject].id)
    rig.boot()
    run = rig.prepare_ready()
    rig.start(run)
    rig.manual_hit(run)
    if lost_result_response:
        rig.gateway.drop_result_response = True
    rig.command(run, "stop")
    local = assert_saved_once(rig, run["run_id"])
    assert json.loads(local.outbox.payload)["end"]["kind"] == "user_stopped"
    assert any(e["kind"] == "hit" for e in json.loads(local.outbox.payload)["events"])
    if lost_result_response:
        assert rig.gateway.dropped_result is not None
        assert local.receipt == rig.gateway.dropped_result
    sent = rig.gateway.result_bodies
    assert sent and all(raw == local.outbox.payload for raw in sent)
    report = rig.request("GET", WEB + f"/results/{local.receipt['result_id']}")
    assert report.status_code == 200
    assert report.json()["ranking_reason"] == "practice_only"
    assert report.json()["ranking_eligible"] is False
    assert report.json()["envelope"] == json.loads(local.outbox.payload)
    ranking = rig.request("GET", WEB + f"/results/{local.receipt['result_id']}/ranking")
    assert ranking.status_code == 200 and ranking.json()["reason"] == "practice_only"
    rig.logout()
    assert rig.request("GET", WEB + f"/results/{local.receipt['result_id']}").status_code == 401
    rig.login("guardian")
    assert rig.request("GET", WEB + f"/results/{local.receipt['result_id']}").status_code == 404


def test_logout_preserves_original_package_explicit_grant_uploads_only(rig):
    # Relogin must not revive the original control lease or silently upload.
    rig.boot()
    run = rig.prepare_ready()
    rig.start(run)
    rig.manual_hit(run)
    rig.logout()
    rig.wait(lambda: rig.store.get_run(run["run_id"]).outbox is not None, "logout terminal")
    local = rig.store.get_run(run["run_id"])
    original = local.outbox.payload
    assert local.receipt is None
    rig.service.pause()
    rig.login()
    digest = hashlib.sha256(original).hexdigest()
    response = rig.request(
        "POST",
        WEB + f"/runs/{run['run_id']}/upload-authorization",
        json={"digest": digest, "confirm_original_package": True},
    )
    assert response.status_code == 200, response.text
    rig.service.select_saved_upload(run["run_id"])
    rig.wait(lambda: rig.service.status()["upload"]["state"] == "selected", "original preview")
    rig.service.resume_saved_upload(run["run_id"], digest, confirm_original_package=True)
    saved = assert_saved_once(rig, run["run_id"])
    assert saved.outbox.payload == original
    assert rig.service.status()["state"] != "running"
    assert not rig.runtime.engine.running


def test_never_delivered_start_cancel_is_not_a_result(rig):
    # STOP priority must cancel without inventing local start/result evidence.
    rig.boot()
    run = rig.prepare_ready()
    with rig.gateway.lock:
        rig.command(run, "start")
        rig.command(run, "stop")
    rig.wait(lambda: rig.store.get_run(run["run_id"]).status == "cancelled", "genuine cancellation")
    local = rig.store.get_run(run["run_id"])
    assert local.outbox is None and local.receipt is None
    assert not rig.physical.enable_writes
    rig.stop_workers()
    status = rig.run_view(run)
    assert status["status"] == "cancelled" and status["stop_confirmed"] is True
    with rig.gateway.lock:
        assert rig.db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 0


def test_failed_off_never_releases_ownership_then_fresh_off_recovers(rig):
    # A terminal package must not make failed physical OFF safe or reusable.
    rig.boot()
    run = rig.prepare_ready()
    rig.start(run)
    rig.manual_hit(run)
    rig.physical.fail_off = True
    rig.command(run, "stop")
    rig.wait(lambda: rig.service.status()["state"] == "stopped-unconfirmed", "failed OFF")
    assert rig.service.status()["off_confirmed"] is False
    assert rig.prepare().status_code == 409
    assert rig.store.get_run(run["run_id"]).receipt is None
    rig.physical.fail_off = False
    future = rig.service.stop_local()
    if future is not None:
        future.result(3)
    rig.wait(lambda: rig.service.status()["off_confirmed"], "fresh OFF")
    assert_saved_once(rig, run["run_id"])
    assert rig.run_view(run)["stop_confirmed"] is True


@pytest.mark.parametrize("mode", FORMAL_MODES)
def test_all_formal_modes_closed_by_default_actual_service(rig, mode):
    # The ordinary HTTP construction path must not treat tests as hardware proof.
    rig.boot()
    response = rig.prepare(mode)
    assert response.status_code == 409, response.text
    assert response.json()["code"] == "reactionx_hardware_unverified"
    assert not rig.physical.enable_writes
    assert rig.service.status()["run_id"] is None


def test_actual_engine_natural_all_lit_under_double_guarded_synthetic_policy(rig):
    # Handcrafted natural envelopes would miss Engine/B1 schedule disagreement.
    settings = rig.browser.app.state.settings
    assert settings.app_env == "test"
    settings.reactionx_synthetic_formal_enabled = True
    try:
        rig.boot()
        run = rig.prepare_ready("whack")
        rig.start(run)
        local = assert_saved_once(rig, run["run_id"])
        envelope = json.loads(local.outbox.payload)
        assert envelope["end"]["kind"] == "all_lit"
        assert [event["kind"] for event in envelope["events"]] == ["activated", "activated"]
        report = rig.request("GET", WEB + f"/results/{local.receipt['result_id']}")
        assert report.status_code == 200
        assert report.json()["ranking_eligible"] is True
        ranking = rig.request("GET", WEB + f"/results/{local.receipt['result_id']}/ranking")
        assert ranking.status_code == 200
        assert ranking.json()["status"] == "ranked"
    finally:
        settings.reactionx_synthetic_formal_enabled = False


def test_natural_completion_network_loss_before_terminal_ack_preserves_original_digest(rig):
    from app.reactionx.queries import HEARTBEAT_FRESH_SECONDS

    settings = rig.browser.app.state.settings
    assert settings.app_env == "test"
    settings.reactionx_synthetic_formal_enabled = True
    try:
        rig.boot()
        run = rig.prepare_ready("whack")
        rig.gateway.lose_terminal_ack = True
        rig.start(run)
        assert rig.gateway.terminal_ack_lost.wait(5), "natural terminal ACK was never intercepted"
        original = rig.store.get_run(run["run_id"]).outbox.payload
        digest = hashlib.sha256(original).hexdigest()
        assert json.loads(original)["end"]["kind"] == "all_lit"
        assert rig.service.status()["off_confirmed"]
        with rig.gateway.lock:
            row = rig.db.get(ReactionxRun, UUID(run["run_id"]))
            assert not row.stop_confirmed
            assert rig.db.get(ReactionxResultReceipt, row.id) is None
        # Real elapsed connectivity gap, not a replaced heartbeat/ACK service.
        time.sleep(HEARTBEAT_FRESH_SECONDS + 0.05)
        rig.gateway.offline = False
        rig.service._wake.set()
        rig.wait(
            lambda: bool(rig.gateway.result_outcomes), "original package reached actual FastAPI"
        )
        assert rig.gateway.result_outcomes[0] == (200, None)
        local = assert_saved_once(rig, run["run_id"])
        assert local.outbox.payload == original and local.receipt["digest"] == digest
        assert all(raw == original for raw in rig.gateway.result_bodies)
        with rig.gateway.lock:
            stops = list(
                rig.db.scalars(
                    select(ReactionxCommand).where(
                        ReactionxCommand.run_id == UUID(run["run_id"]),
                        ReactionxCommand.kind == "stop",
                    )
                )
            )
            assert stops and all(
                row.delivery_envelope is None and row.status == "cancelled" for row in stops
            )
            assert all(row.payload["reason"] in ("disconnected", "lease_expired") for row in stops)
            assert rig.db.get(ReactionxRun, UUID(run["run_id"])).stop_confirmed
    finally:
        rig.gateway.offline = False
        settings.reactionx_synthetic_formal_enabled = False


def test_delivered_start_before_local_accept_retains_unresolved_delivery(rig):
    # Original negative: declaration alone never closes unknown execution.
    run = rig.unknown_run()
    declaration = rig.declared_uncertainty(run)
    assert declaration["run_id"] == run["run_id"]
    local = rig.store.get_run(run["run_id"])
    assert local.status == "recovered_unstarted"
    assert local.outbox is None and local.receipt is None
    assert rig.store.usage().unacked_runs == 1
    assert rig.store.uncertainty_summaries() == [
        {"run_id": run["run_id"], "state": "pending_review"}
    ]
    rig.stop_workers()
    server = rig.run_view(run)
    assert server["status"] == "awaiting_sync"
    assert server["result_saved"] is False
    assert not rig.physical.enable_writes
    with rig.gateway.lock:
        assert rig.db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 0


@pytest.mark.parametrize("lost_response_restart", [False, True])
def test_unknown_delivery_real_double_confirmation_and_durable_non_result_archive(
    rig, lost_response_restart
):
    # Catch early quota release, fabricated receipts and lost-response replay.
    run = rig.unknown_run()
    declaration = rig.declared_uncertainty(run)
    run_id = run["run_id"]
    digest = hashlib.sha256(rig.gateway.declaration_bodies[0]).hexdigest()
    assert json.loads(rig.gateway.declaration_bodies[0]) == declaration
    assert declaration["actor_account_id"] == rig.credential.actor_id
    assert declaration["connector_id"] == rig.credential.connector_id
    assert declaration["manifest_digest"] == run["manifest"]["manifest_digest"]
    original = rig.store.get_run(run_id)
    assert original.events == [] and original.intents == []
    assert original.outbox is None and original.receipt is None
    request = rig.archive_request(run, digest)
    assert rig.store.usage().unacked_runs == 1
    assert rig.run_view(run)["status"] == "awaiting_sync"
    if lost_response_restart:
        rig.gateway.hold_archive_response = True
    confirmed = rig.request(
        "POST",
        WEB + f"/runs/{run_id}/uncertainty/confirm",
        json={"request_id": request["request_id"], "confirmation": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    receipt = confirmed.json()
    assert receipt["status"] == "execution_unknown"
    assert receipt["declaration_digest"] == digest
    assert receipt["stop_operation_id"] == declaration["stop_operation_id"]
    if lost_response_restart:
        assert rig.gateway.archive_delivered.wait(4), (
            "real archived response never reached boundary"
        )
        assert rig.gateway.dropped_archive_receipt == receipt
        assert rig.store.get_run(run_id).status == "recovered_unstarted"
        assert rig.store.usage().unacked_runs == 1  # server receipt is NOT local durable proof
        rig.crash_and_reopen()
        assert rig.store.usage().unacked_runs == 1
        rig.boot()
    rig.wait(
        lambda: rig.store.get_run(run_id).status == "uncertain_closed",
        "real archive receipt protected commit",
    )
    assert rig.store.usage().unacked_runs == 0
    assert rig.store.owned_unacked_count() == 0
    assert rig.store.uncertainty_summaries() == [{"run_id": run_id, "state": "execution_unknown"}]
    assert not rig.physical.enable_writes and not rig.runtime.engine.running
    assert rig.gateway.result_bodies == []
    rig.stop_workers()
    # Reopen actual protected SQLite after a second, fully acknowledged exit.
    rig.assemble()
    saved = rig.store.get_run(run_id)
    assert saved.status == "uncertain_closed" and saved.manifest == original.manifest
    assert saved.events == original.events and saved.intents == original.intents
    assert saved.outbox is None and saved.receipt is None
    assert rig.store.usage().unacked_runs == 0
    assert rig.store.pending_uncertainties() == []
    status = rig.api.uncertainty_status(run_id, digest)
    assert status == {"state": "archived", "declaration_digest": digest, "receipt": receipt}
    # Real same-proof replay exercises the pinned local receipt comparison.
    rig.store.ack_uncertainty(run_id, status["receipt"])
    assert rig.store.usage().unacked_runs == 0
    repeated = rig.request(
        "POST",
        WEB + f"/runs/{run_id}/uncertainty/confirm",
        json={"request_id": request["request_id"], "confirmation": True},
    )
    assert repeated.status_code == 200 and repeated.json() == receipt
    rig.stop_workers()
    assert rig.run_view(run)["status"] == "uncertain_closed"
    assert rig.run_view(run)["result_saved"] is False
    assert rig.request("GET", WEB + f"/results/{run_id}/ranking").status_code == 404
    listing = rig.request("GET", WEB + f"/results?person_id={rig.person_id}")
    assert listing.status_code == 200 and listing.json()["items"] == []
    with rig.gateway.lock:
        row = rig.db.get(ReactionxRun, UUID(run_id), populate_existing=True)
        assert row.uncertainty_record["receipt"] == receipt
        assert row.uncertainty_record["declaration"] == declaration
        commands = list(
            rig.db.scalars(select(ReactionxCommand).where(ReactionxCommand.run_id == UUID(run_id)))
        )
        assert sorted(command.kind for command in commands) == ["prepare", "start", "stop"]
        start = next(command for command in commands if command.kind == "start")
        assert start.delivery_envelope is not None and start.ack_payload is None
        assert rig.db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 0
        assert rig.db.scalar(select(func.count()).select_from(ReactionxEvent)) == 0
        for action in ("requested", "archived"):
            assert (
                rig.db.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.action == f"reactionx.uncertainty.{action}")
                )
                == 1
            )
    assert rig.gateway.machine_cookie_count == 0


def test_unknown_delivery_first_confirmation_and_wrong_session_stay_pending(rig):
    # The current superadmin request is not permission for a later login session.
    run = rig.unknown_run()
    rig.declared_uncertainty(run)
    digest = hashlib.sha256(rig.gateway.declaration_bodies[0]).hexdigest()
    request = rig.archive_request(run, digest)
    rig.stop_workers()
    rig.assemble()
    assert rig.store.usage().unacked_runs == 1
    assert rig.api.uncertainty_status(run["run_id"], digest)["state"] == "pending_review"
    rig.logout()
    rig.login("guardian")
    denied = rig.request(
        "POST",
        WEB + f"/runs/{run['run_id']}/uncertainty/confirm",
        json={"request_id": request["request_id"], "confirmation": True},
    )
    assert denied.status_code == 403
    rig.logout()
    rig.login()
    denied = rig.request(
        "POST",
        WEB + f"/runs/{run['run_id']}/uncertainty/confirm",
        json={"request_id": request["request_id"], "confirmation": True},
    )
    assert denied.status_code == 403
    status = rig.api.uncertainty_status(run["run_id"], digest)
    assert status["state"] == "pending_review" and status["receipt"] is None
    local = rig.store.get_run(run["run_id"])
    assert local.status == "recovered_unstarted"
    assert local.outbox is None and local.receipt is None
    assert rig.store.usage().unacked_runs == 1
    assert not rig.physical.enable_writes
    rig.stop_workers()
    # Dedicated uncertainty access does not revive the previous control session.
    assert rig.request("GET", WEB + f"/runs/{run['run_id']}").status_code == 403
    with rig.gateway.lock:
        assert (
            rig.db.get(ReactionxRun, UUID(run["run_id"]), populate_existing=True).status
            == "awaiting_sync"
        )
        assert rig.db.scalar(select(func.count()).select_from(ReactionxResultReceipt)) == 0
        assert (
            rig.db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "reactionx.uncertainty.archived")
            )
            == 0
        )


def test_unknown_delivery_failed_actual_off_cannot_declare_or_archive(rig):
    # A browser confirmation can never substitute for original physical OFF.
    run = rig.unknown_run(fail_off=True)
    rig.wait(lambda: rig.service.status()["state"] == "stopped-unconfirmed", "actual OFF failure")
    assert rig.service.status()["off_confirmed"] is False
    assert rig.run_view(run)["stop_confirmed"] is False
    assert rig.store.pending_uncertainties() == []
    assert rig.gateway.declaration_bodies == []
    listing = rig.request("GET", WEB + "/uncertainties")
    assert listing.status_code == 200 and listing.json()["total"] == 0
    # There is deliberately no declaration/digest to submit for review.
    missing = rig.request(
        "POST",
        WEB + f"/runs/{run['run_id']}/uncertainty/confirm",
        json={"request_id": run["run_id"], "confirmation": True},
    )
    assert missing.status_code == 404
    assert rig.store.get_run(run["run_id"]).outbox is None
    assert rig.store.usage().unacked_runs == 1
    assert not rig.physical.enable_writes
    assert rig.gateway.result_bodies == []
