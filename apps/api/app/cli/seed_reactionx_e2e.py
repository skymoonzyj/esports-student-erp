"""Disposable fixture seed and independent persisted acceptance verification.

No connector, run, event, receipt or result audit is fabricated by this seed.
Both write and read commands require the exact explicitly supplied test DSN.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
from collections.abc import Sequence
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.auth.models import AuthSession, UserAccount
from app.auth.service import AuthContext
from app.cli.e2e_credentials import E2E_CREDENTIALS
from app.cli.e2e_database_safety import E2EDatabaseRefused, require_e2e_database_url
from app.cli.seed_e2e import E2E_ADMIN_ACCOUNT_ID, E2E_LIFE_TEACHER_ACCOUNT_ID, _seed_person
from app.cli.seed_talent_e2e import _replace_role, seed_talent_e2e
from app.core.config import Settings, get_settings
from app.core.transactions import write_transaction
from app.identity.models import Person, StudentProfile
from app.reactionx.access import ReactionxAccess
from app.reactionx.metrics import validate_result_envelope
from app.reactionx.models import (
    ReactionxCommand,
    ReactionxConnector,
    ReactionxEvent,
    ReactionxResultReceipt,
    ReactionxRun,
)
from app.reactionx.queries import AckRequest
from app.reactionx.ranking import ReactionxRankingService
from app.reactionx.rule_versions import validate_manifest
from app.reactionx.schemas import CommandEnvelope, ResultEnvelope, RunManifest, canonical_digest
from app.reactionx.uncertainty import UncertaintyDeclaration, UncertaintyReceipt
from app.security.encryption import EnvelopeCipher, LookupDigest
from app.security.passwords import PasswordService

SUBJECT_IDS = tuple(UUID(f"e8300000-0000-4000-8000-{index:012d}") for index in (1, 2, 3))
ARCHIVE_OPERATOR_ID = E2E_LIFE_TEACHER_ACCOUNT_ID


class ReactionxE2ERefused(RuntimeError):
    pass


class ReactionxE2EEvidenceError(RuntimeError):
    pass


def require_environment(settings: Settings) -> None:
    supplied = os.environ.get("TEST_DATABASE_URL")
    if settings.app_env != "test" or not supplied or supplied != settings.database_url:
        raise ReactionxE2ERefused("requires test environment and exact isolated database")
    try:
        require_e2e_database_url(supplied)
    except E2EDatabaseRefused:
        raise ReactionxE2ERefused("requires test environment and exact isolated database") from None


def _key(settings: Settings, name: str) -> bytes:
    return base64.b64decode(
        getattr(settings, name).get_secret_value(), altchars=b"-_", validate=True
    )


def seed(db: Session, settings: Settings) -> None:
    require_environment(settings)
    cipher = EnvelopeCipher(_key(settings, "field_encryption_master_key"))
    lookup = LookupDigest(_key(settings, "field_lookup_key"))
    seed_talent_e2e(
        db, settings=settings, cipher=cipher, lookup=lookup, passwords=PasswordService()
    )
    with write_transaction(db):
        # One separate synthetic operator avoids exceeding the original admin's
        # five-pairing/minute limit (closed probe + four unchanged old cases).
        # Do not clear/reset the limiter or change any production permissions.
        _replace_role(db, ARCHIVE_OPERATOR_ID, "super_admin")
        for index, identity in enumerate(SUBJECT_IDS, 1):
            db.add(
                _seed_person(
                    person_id=identity,
                    full_name=f"ReactionX 合成人员 {index}",
                    identity_birth="20000102",
                    identity_sequence=170 + index * 2,
                    sex="female",
                    date_of_birth=date(2000, 1, 2),
                    cipher=cipher,
                    lookup=lookup,
                )
            )
        db.flush()
        db.add_all(
            StudentProfile(person_id=identity, student_number=f"RX-E2E-{index}")
            for index, identity in enumerate(SUBJECT_IDS, 1)
        )


def _require(condition: object, code: str) -> None:
    if not condition:
        raise ReactionxE2EEvidenceError(code)


def verify_archive_evidence(
    run: ReactionxRun,
    commands: list[ReactionxCommand],
    audits: list[AuditEvent],
    *,
    event_count: int,
    result_count: int,
) -> None:
    """Read-only postcondition over actual persisted evidence, not a service bypass."""
    code = "archive_evidence_invalid"
    _require(
        run.status == "uncertain_closed"
        and run.stop_confirmed
        and event_count == result_count == 0,
        code,
    )
    _require(
        run.actor_account_id == ARCHIVE_OPERATOR_ID and run.subject_person_id == SUBJECT_IDS[0],
        code,
    )
    try:
        manifest = RunManifest.model_validate(run.manifest)
        validate_manifest(manifest)
        record = run.uncertainty_record or {}
        declaration = UncertaintyDeclaration.model_validate(record["declaration"])
        receipt = UncertaintyReceipt.model_validate(record["receipt"])
        request = record["request"]
        expected = {
            "run_id": str(run.id),
            "actor_account_id": str(run.actor_account_id),
            "subject_person_id": str(run.subject_person_id),
            "connector_id": str(run.connector_id),
            "manifest_digest": run.manifest_digest,
        }
        _require(
            all(
                getattr(manifest, key) == value == getattr(declaration, key)
                for key, value in expected.items()
            ),
            code,
        )
        _require(
            record["declaration_digest"]
            == canonical_digest(declaration)
            == receipt.declaration_digest
            == request["declaration_digest"],
            code,
        )
        _require(
            receipt.run_id == str(run.id)
            and receipt.connector_id == str(run.connector_id)
            and receipt.manifest_digest == run.manifest_digest
            and receipt.stop_operation_id == declaration.stop_operation_id,
            code,
        )
        _require(
            request["issuer"] == str(run.actor_account_id)
            and request["issuer_session"] == str(run.browser_session_id)
            and request["confirmed_at"] == receipt.closed_at
            and datetime.fromisoformat(request["requested_at"])
            <= datetime.fromisoformat(receipt.closed_at)
            < datetime.fromisoformat(request["expires_at"]),
            code,
        )
        by_kind = {
            kind: [command for command in commands if command.kind == kind]
            for kind in ("prepare", "start", "stop")
        }
        _require(len(commands) == 3 and all(len(rows) == 1 for rows in by_kind.values()), code)
        start, stop = by_kind["start"][0], by_kind["stop"][0]
        _require(
            start.ack_payload is None
            and stop.status == "completed"
            and stop.acknowledged_at is not None
            and stop.sequence > start.sequence
            and str(stop.operation_id) == declaration.stop_operation_id,
            code,
        )
        for command in (start, stop):
            envelope = CommandEnvelope.model_validate(command.delivery_envelope)
            _require(
                command.run_id == run.id
                and command.actor_account_id == run.actor_account_id
                and command.subject_person_id == run.subject_person_id
                and command.connector_id == run.connector_id
                and command.browser_tab_id == run.browser_tab_id
                and envelope.operation_id == str(command.operation_id)
                and envelope.kind == command.kind
                and envelope.sequence == command.sequence
                and envelope.payload.model_dump(mode="json") == command.payload
                and all(
                    getattr(envelope, key) == value
                    for key, value in expected.items()
                    if key != "manifest_digest"
                ),
                code,
            )
        off = AckRequest.model_validate(stop.ack_payload)
        _require(
            off.status == "completed"
            and off.run_state == "stopped_confirmed"
            and set(off.off_confirmed_device_keys)
            == {device.device_key for device in manifest.devices},
            code,
        )
        _require(
            len(audits) == 2
            and {event.action for event in audits}
            == {"reactionx.uncertainty.requested", "reactionx.uncertainty.archived"},
            code,
        )
        _require(
            all(
                event.resource_id == run.id
                and event.actor_user_id == run.actor_account_id
                and event.outcome == "allowed"
                and event.purpose == request["reason"]
                and event.event_metadata
                == {"operation_id": request["id"], "session_id": request["issuer_session"]}
                for event in audits
            ),
            code,
        )
    except (ValueError, TypeError, KeyError):
        raise ReactionxE2EEvidenceError(code) from None


def verify(db: Session, settings: Settings) -> dict[str, int]:
    require_environment(settings)
    receipts = list(db.scalars(select(ReactionxResultReceipt)))
    _require(len(receipts) == 5, "missing_results")
    _require(
        not db.scalar(select(UserAccount.id).where(UserAccount.person_id.in_(SUBJECT_IDS))),
        "accountless_subject_changed",
    )
    formal_subjects: set[UUID] = set()
    manual = disconnected = events_total = 0
    formal_receipts: list[ReactionxResultReceipt] = []
    for receipt in receipts:
        run = db.get(ReactionxRun, receipt.run_id)
        _require(run is not None, "missing_original_run")
        assert run is not None
        connector = db.get(ReactionxConnector, run.connector_id)
        session = db.get(AuthSession, run.browser_session_id)
        manifest = RunManifest.model_validate(run.manifest)
        envelope = ResultEnvelope.model_validate(receipt.envelope)
        _require(connector is not None and session is not None, "missing_original_identity")
        assert connector is not None and session is not None
        _require(
            run.actor_account_id
            == E2E_ADMIN_ACCOUNT_ID
            == connector.actor_account_id
            == session.user_id,
            "original_operator_changed",
        )
        _require(
            run.subject_person_id in SUBJECT_IDS
            and manifest.subject_person_id == str(run.subject_person_id)
            and manifest.actor_account_id == str(run.actor_account_id)
            and manifest.connector_id == str(run.connector_id)
            and run.browser_tab_id == connector.control_tab_id
            and run.browser_session_id == connector.browser_session_id,
            "original_subject_or_binding_changed",
        )
        _require(
            manifest.manifest_digest
            == run.manifest_digest
            == canonical_digest(manifest, exclude={"manifest_digest"})
            and receipt.digest == canonical_digest(envelope),
            "digest_changed",
        )
        events = list(
            db.scalars(
                select(ReactionxEvent)
                .where(ReactionxEvent.run_id == run.id)
                .order_by(ReactionxEvent.seq)
            )
        )
        _require(
            [event.event for event in events] == receipt.envelope["events"], "event_storage_changed"
        )
        _require(
            [event.seq for event in events] == list(range(1, len(events) + 1)),
            "event_sequence_changed",
        )
        metrics = validate_result_envelope(manifest, envelope)
        _require(metrics.model_dump(mode="json") == receipt.metrics, "stored_metrics_changed")
        _require(
            db.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.action == "reactionx.result.saved",
                    AuditEvent.resource_id == receipt.result_id,
                    AuditEvent.actor_user_id == run.actor_account_id,
                )
            )
            == 1,
            "saved_audit_not_once",
        )
        if manifest.mode == "manual":
            manual += 1
            _require(
                not receipt.eligible and receipt.ranking_reason == "practice_only", "manual_ranked"
            )
        else:
            _require(
                manifest.mode == "standard"
                and receipt.eligible
                and metrics.eligible
                and envelope.end.kind == "task_completed"
                and receipt.ranking_reason is None,
                "formal_not_natural",
            )
            formal_subjects.add(run.subject_person_id)
            formal_receipts.append(receipt)
        if envelope.end.kind == "disconnected":
            disconnected += 1
            _require(not receipt.eligible, "interruption_ranked")
        events_total += len(events)
    _require(
        manual == 2
        and disconnected == 1
        and formal_subjects == set(SUBJECT_IDS)
        and events_total == 182,
        "missing_acceptance_paths",
    )
    _require(
        (
            db.scalar(
                select(func.count())
                .select_from(ReactionxConnector)
                .where(ReactionxConnector.status == "revoked")
            )
            or 0
        )
        >= 1,
        "missing_revoke",
    )
    now = datetime.now(UTC)
    live_session = db.scalar(
        select(AuthSession).where(
            AuthSession.user_id == E2E_ADMIN_ACCOUNT_ID,
            AuthSession.revoked_at.is_(None),
            AuthSession.expires_at > now,
        )
    )
    account = db.get(UserAccount, E2E_ADMIN_ACCOUNT_ID)
    _require(live_session is not None and account is not None, "missing_verifier_actor")
    assert live_session is not None and account is not None
    person = db.get(Person, account.person_id)
    assert person is not None
    ranking = ReactionxRankingService(
        db, ReactionxAccess(db, AuthContext(account=account, person=person, session=live_session))
    )
    for receipt in formal_receipts:
        view = ranking.for_result(receipt.result_id)
        _require(
            view.status == "ranked" and view.participant_count == 3 and len(view.top_three) == 3,
            "ranking_missing_three",
        )
        _require(
            all(len(row.gaps) == len(view.metrics) for row in view.top_three), "ranking_gap_missing"
        )
    archives = list(
        db.scalars(select(ReactionxRun).where(ReactionxRun.uncertainty_record.is_not(None)))
    )
    _require(len(archives) == 1, "missing_uncertainty_archive")
    archived = archives[0]
    connector = db.get(ReactionxConnector, archived.connector_id)
    original_session = db.get(AuthSession, archived.browser_session_id)
    _require(connector is not None and original_session is not None, "missing_archive_binding")
    assert connector is not None and original_session is not None
    _require(
        connector.actor_account_id == original_session.user_id == archived.actor_account_id
        and connector.browser_session_id == archived.browser_session_id
        and connector.control_tab_id == archived.browser_tab_id,
        "archive_binding_changed",
    )
    archive_events = (
        db.scalar(
            select(func.count())
            .select_from(ReactionxEvent)
            .where(ReactionxEvent.run_id == archived.id)
        )
        or 0
    )
    archive_results = (
        db.scalar(
            select(func.count())
            .select_from(ReactionxResultReceipt)
            .where(ReactionxResultReceipt.run_id == archived.id)
        )
        or 0
    )
    verify_archive_evidence(
        archived,
        list(db.scalars(select(ReactionxCommand).where(ReactionxCommand.run_id == archived.id))),
        list(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.resource_id == archived.id,
                    AuditEvent.action.in_(
                        ("reactionx.uncertainty.requested", "reactionx.uncertainty.archived")
                    ),
                )
            )
        ),
        event_count=archive_events,
        result_count=archive_results,
    )
    return {
        "results": len(receipts),
        "formal_subjects": len(formal_subjects),
        "manual_results": manual,
        "disconnected_results": disconnected,
        "events": events_total,
        "uncertainty_archives": len(archives),
        "uncertainty_archive_audits": 2,
        "uncertainty_archive_events": archive_events,
        "uncertainty_archive_results": archive_results,
    }


def probe_closed(origin: str) -> None:
    from urllib.parse import urlsplit

    import httpx

    url = urlsplit(origin)
    if (
        url.scheme != "http"
        or url.hostname != "127.0.0.1"
        or not url.port
        or url.path
        or url.query
        or url.fragment
        or url.username
    ):
        raise ReactionxE2ERefused("requires loopback API origin")
    # Metadata intentionally stays closed even in a synthetic-enabled process.
    # Exercise a valid execution request; ONLY its exact policy denial proves
    # runtime closure. The same inputs are characterized against enabled policy
    # in the real-API regression. No run/result/receipt is preseeded here.
    fixtures = json.loads(
        (Path(__file__).resolve().parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    template = RunManifest.model_validate(
        next(case["manifest"] for case in fixtures["cases"] if case["name"] == "standard")
    )
    validate_manifest(template)
    definition = template.model_dump(mode="json", include={"mode", "config", "devices", "layout"})
    credential = E2E_CREDENTIALS["admin"]
    secret = secrets.token_urlsafe(32)
    tab_id = str(uuid4())
    connector_id: str | None = None
    pairing_id: str | None = None
    unexpected_run: str | None = None

    def checked(response: httpx.Response, status: int, code: str) -> dict[str, Any]:
        _require(response.status_code == status, code)
        try:
            body = response.json()
        except ValueError:
            raise ReactionxE2EEvidenceError(code) from None
        _require(isinstance(body, dict), code)
        return dict(body)

    with (
        httpx.Client(base_url=origin, timeout=15, trust_env=False) as client,
        httpx.Client(base_url=origin, timeout=15, trust_env=False) as machine,
    ):
        response = client.post(
            "/api/v1/auth/login",
            json={"identifier": credential.identifier, "password": credential.password},
        )
        login = checked(response, 200, "closed_probe_login")
        _require(
            isinstance(login.get("csrf_token"), str) and login["csrf_token"], "closed_probe_login"
        )
        headers = {"X-CSRF-Token": login["csrf_token"]}
        machine_headers: dict[str, str] = {}
        try:
            response = client.get("/api/v1/reactionx/modes")
            _require(response.status_code == 200, "closed_probe_modes")
            modes = response.json()
            _require(
                len(modes) == 9
                and all(item["available"] is (item["mode"] == "manual") for item in modes),
                "default_formal_open",
            )
            pairing = checked(
                client.post(
                    "/api/v1/reactionx/pairings", headers=headers, json={"browser_tab_id": tab_id}
                ),
                200,
                "closed_probe_pairing",
            )
            pairing_id = str(UUID(pairing["pairing_id"]))
            claim = checked(
                machine.post(
                    "/api/v1/reactionx-client/pairings/claim",
                    json={
                        "challenge": pairing["challenge"],
                        "credential_digest": sha256(secret.encode("ascii")).hexdigest(),
                        "client_info": {
                            "origin": get_settings().reactionx_public_origin,
                            "name": "ReactionX closed-policy probe",
                        },
                    },
                ),
                200,
                "closed_probe_pairing",
            )
            connector_id = str(UUID(claim["connector_id"]))
            machine_headers = {"Authorization": f"ReactionX {connector_id}.{secret}"}
            phrase = {"verification_phrase": pairing["verification_phrase"]}
            checked(
                client.post(
                    f"/api/v1/reactionx/pairings/{pairing_id}/confirm", headers=headers, json=phrase
                ),
                200,
                "closed_probe_pairing",
            )
            confirmed = checked(
                machine.post(
                    f"/api/v1/reactionx-client/pairings/{pairing_id}/confirm",
                    headers=machine_headers,
                    json=phrase,
                ),
                200,
                "closed_probe_pairing",
            )
            _require(
                confirmed.get("status") == "active"
                and confirmed.get("browser_confirmed") is True
                and confirmed.get("device_confirmed") is True,
                "closed_probe_pairing",
            )
            heartbeat = checked(
                machine.post(
                    "/api/v1/reactionx-client/heartbeat",
                    headers=machine_headers,
                    json={
                        "protocol_version": 1,
                        "connector_version": "0.3.0",
                        "instance_id": str(uuid4()),
                        "devices": [
                            {
                                **device,
                                "label": f"probe-{device['display_number']}",
                                "connected": True,
                            }
                            for device in definition["devices"]
                        ],
                    },
                ),
                200,
                "closed_probe_inventory",
            )
            _require(
                heartbeat["connector"].get("online") is True
                and heartbeat["connector"].get("compatible") is True
                and heartbeat["connector"].get("locally_confirmed") is True,
                "closed_probe_inventory",
            )
            response = client.post(
                "/api/v1/reactionx/runs/prepare",
                headers=headers,
                json={
                    **definition,
                    "operation_id": str(uuid4()),
                    "person_id": str(SUBJECT_IDS[0]),
                    "connector_id": connector_id,
                    "tab_id": tab_id,
                },
            )
            if response.status_code == 202:
                unexpected_run = str(UUID(response.json()["run_id"]))
            denial = checked(response, 409, "default_formal_open")
            _require(denial.get("code") == "reactionx_hardware_unverified", "default_formal_open")
        finally:
            # If the supposedly closed process accepted prepare, cancel it
            # without ever delivering prepare or issuing start. No result is
            # fabricated to clean up this negative test.
            if unexpected_run is not None:
                stopped = checked(
                    client.post(
                        f"/api/v1/reactionx/runs/{unexpected_run}/stop",
                        headers=headers,
                        json={"operation_id": str(uuid4()), "tab_id": tab_id},
                    ),
                    202,
                    "closed_probe_cleanup",
                )
                polled = checked(
                    machine.get("/api/v1/reactionx-client/commands", headers=machine_headers),
                    200,
                    "closed_probe_cleanup",
                )
                _require(
                    any(
                        command["kind"] == "stop"
                        and command["operation_id"] == stopped["operation_id"]
                        for command in polled["commands"]
                    ),
                    "closed_probe_cleanup",
                )
                terminal = checked(
                    machine.post(
                        f"/api/v1/reactionx-client/commands/{stopped['operation_id']}/ack",
                        headers=machine_headers,
                        json={
                            "status": "completed",
                            "run_state": "stopped_confirmed",
                            "off_confirmed_device_keys": [
                                device["device_key"] for device in definition["devices"]
                            ],
                        },
                    ),
                    200,
                    "closed_probe_cleanup",
                )
                _require(terminal.get("run_status") == "cancelled", "closed_probe_cleanup")
            if connector_id is not None:
                _require(
                    client.delete(
                        f"/api/v1/reactionx/connectors/{connector_id}", headers=headers
                    ).status_code
                    == 204,
                    "closed_probe_cleanup",
                )
            elif pairing_id is not None:
                _require(
                    client.delete(
                        f"/api/v1/reactionx/pairings/{pairing_id}", headers=headers
                    ).status_code
                    == 204,
                    "closed_probe_cleanup",
                )
            _require(
                client.post("/api/v1/auth/logout", headers=headers).status_code == 204,
                "closed_probe_cleanup",
            )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--probe-closed", metavar="LOOPBACK_ORIGIN")
    args = parser.parse_args(argv)
    try:
        settings = get_settings()
        require_environment(settings)
        settings.validate_runtime_secrets()
        if args.probe_closed:
            probe_closed(args.probe_closed)
            print("ReactionX closed-policy HTTP probe passed")
            return 0
        from app.core.database import SessionLocal

        with SessionLocal() as db:
            if args.verify:
                print(json.dumps({"reactionx_persisted": verify(db, settings)}, sort_keys=True))
            else:
                seed(db, settings)
                print("ReactionX synthetic fixture seed ready; no outcomes seeded")
    except (ReactionxE2ERefused, ReactionxE2EEvidenceError) as error:
        # Only fixed diagnostic codes are emitted, never SQL/URLs/identifiers.
        print("ReactionX acceptance refused: " + str(error))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
