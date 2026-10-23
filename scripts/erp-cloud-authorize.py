#!/usr/bin/env python3
"""Private, bounded short-poll authorization runner; never log credentials."""

import argparse
import fcntl
import json
import re
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from cloud_auth.candidate import (
    CandidateAccess,
    CandidateOwnership,
    CandidateRef,
    probe_candidate,
)
from cloud_auth.openlist_admin import OpenListAdmin, OpenListAPI, RegisteredConnector
from cloud_auth.provider_types import (
    DeviceGrant,
    ImportedTokenGrant,
    ProviderError,
    QrPresentation,
    TokenGrant,
)
from cloud_auth.recovery import load_registry
from erp_cloud_transport import Transport


class AuthRunnerError(RuntimeError):
    pass


class ReleaseMismatch(AuthRunnerError):
    pass


def validate_active_release(app):
    """Repeat the wrapper gate under each operation's deployment lock."""
    try:
        app = app.resolve(strict=True)
        marker, state_file = app / ".active-release", app / ".deployment-state"
        if not marker.is_file() or not state_file.is_file():
            raise ValueError()
        if marker.stat().st_size > 4096 or state_file.stat().st_size > 4096:
            raise ValueError()
        state = dict(
            line.split("=", 1) for line in state_file.read_text().splitlines() if "=" in line
        )
        eligible = state.get("status") in ("healthy", "complete", "backup_failed") or (
            state.get("status") == "attempt_failed" and state.get("active_unchanged") == "true"
        )
        release = Path(marker.read_text().strip()).resolve(strict=True)
        source = Path(__file__).resolve(strict=True)
        if (
            not eligible
            or not release.is_dir()
            or not release.is_relative_to(app)
            or release != (app / "current").resolve(strict=True)
            or state.get("release") != str(release)
            or source != release / "scripts/erp-cloud-authorize.py"
        ):
            raise ValueError()
    except (OSError, ValueError):
        raise ReleaseMismatch("cloud_runner_release_changed") from None


def wire(value):
    if isinstance(value, (UUID, datetime)):
        return str(value) if isinstance(value, UUID) else value.isoformat()
    raise TypeError("unsupported private wire value")


def bridge(config, action, **payload):
    project = config.get("project")
    if not isinstance(project, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", project):
        raise AuthRunnerError("cloud_auth_bridge_failed")
    try:
        raw = json.dumps(
            {"action": action, **payload}, default=wire, separators=(",", ":")
        ).encode()
        if len(raw) > 65536:
            raise ValueError()
        with tempfile.TemporaryFile() as output:
            result = subprocess.run(
                [
                    "docker",
                    "exec",
                    "-i",
                    project + "-api-1",
                    "python",
                    "-m",
                    "app.operations.cloud_auth_bridge",
                ],
                input=raw,
                stdout=output,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
            if result.returncode or output.tell() > 65536:
                raise ValueError()
            output.seek(0)
            value = json.loads(output.read(65537))
            if not isinstance(value, dict):
                raise TypeError()
            return value
    except (ValueError, TypeError, OSError, subprocess.SubprocessError):
        raise AuthRunnerError("cloud_auth_bridge_failed") from None


@contextmanager
def locked(path, *, shared=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as stream:
        try:
            fcntl.flock(stream, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise AuthRunnerError("cloud_target_busy") from None
        yield


@contextmanager
def operation_locks(app):
    # Keep the same global order as replication, backup, deployment and recovery.
    with (
        locked(app / ".cloud-backup.lock"),
        locked(app / ".backup.lock", shared=True),
        locked(app / ".deploy.lock", shared=True),
        locked(app / ".cloud-connector.lock"),
    ):
        yield


def ownership(value):
    if value is None:
        return None
    fields = dict(value)
    for key in ("operation_id", "connector_id", "candidate_id"):
        fields[key] = UUID(fields[key])
    return CandidateOwnership(**fields)


class RemoteJournal:
    def __init__(self, config, work, call):
        self.config, self.work, self.call = config, work, call

    def checkpoint(self, kind, operation_id, **payload):
        result = self.call(
            self.config,
            "checkpoint",
            fence=self.work["fence"],
            kind=kind,
            operation_id=str(operation_id),
            **payload,
        )
        if result.get("ok") is not True:
            raise AuthRunnerError("cloud_auth_revision_changed")
        return result

    def read(self, operation_id):
        return ownership(self.checkpoint("read", operation_id).get("record"))

    def reserve(self, record):
        return ownership(
            self.checkpoint("reserve", record.operation_id, record=asdict(record))["record"]
        )

    def save(self, record):
        self.checkpoint("save", record.operation_id, record=asdict(record))

    def reserve_probe(self, operation_id, ref, directory, path, sha256):
        self.checkpoint(
            "reserve_probe", operation_id, directory=directory, path=path, sha256=sha256
        )

    def finish_probe(self, operation_id, error_code, cleanup_pending):
        self.checkpoint(
            "finish_probe", operation_id, error_code=error_code, cleanup_pending=cleanup_pending
        )


def provider_for(provider):
    if provider == "baidu":
        from cloud_auth.baidu import BaiduProvider

        return BaiduProvider()
    if provider == "115":
        from cloud_auth.drive115 import Drive115Provider

        return Drive115Provider()
    raise AuthRunnerError("cloud_provider_unsupported")


def token(value):
    if value.get("mode") == "openlist_builtin_import":
        data = dict(value)
        data["refreshed_at"] = datetime.fromisoformat(data["refreshed_at"])
        data["expires_at"] = (
            datetime.fromisoformat(data["expires_at"]) if data["expires_at"] else None
        )
        return ImportedTokenGrant(**data)
    return TokenGrant(
        value["access_token"], value["refresh_token"], datetime.fromisoformat(value["expires_at"])
    )


SOURCE_FIELDS = (
    "protocol_version",
    "mode",
    "auth_config_id",
    "auth_config_revision",
    "app_revision",
)


def work_source(work):
    """Version 1 is only the known legacy own-app wire, never a builtin fallback."""
    version = work.get("protocol_version", 1)
    fence = work["fence"]
    if version == 1:
        if (
            work.get("mode", "own_app") != "own_app"
            or fence.get("protocol_version", 1) != 1
            or fence.get("mode", "own_app") != "own_app"
        ):
            raise AuthRunnerError("cloud_auth_revision_changed")
        return {}
    if version != 2 or any(key not in work for key in SOURCE_FIELDS):
        raise AuthRunnerError("cloud_auth_revision_changed")
    source = {key: work[key] for key in SOURCE_FIELDS}
    mode, provider = source["mode"], work["provider"]
    app_revision = source["app_revision"]
    if mode == "own_app":
        valid = (
            type(app_revision) is int
            and app_revision >= 1
            and source["auth_config_revision"] == app_revision
            and source["auth_config_id"] == "own-app:" + provider
        )
    else:
        valid = (
            mode == "openlist_builtin_import"
            and app_revision is None
            and type(source["auth_config_revision"]) is int
            and source["auth_config_revision"] == 1
            and source["auth_config_id"] == "openlist-builtin-import-v1:" + provider
        )
    if (
        provider not in {"baidu", "115"}
        or not valid
        or any(fence.get(key) != value for key, value in source.items())
    ):
        raise AuthRunnerError("cloud_auth_revision_changed")
    candidate = work.get("candidate")
    if candidate is not None and any(candidate.get(key) != value for key, value in source.items()):
        raise AuthRunnerError("cloud_auth_revision_changed")
    return source


def perform_import(config, work, call, source):
    from cloud_auth.builtin_import import get_import_identity, refresh_import

    if source.get("mode") != "openlist_builtin_import" or source.get("protocol_version") != 2:
        raise AuthRunnerError("cloud_auth_revision_changed")
    phase, record = work["refresh_phase"], work.get("refresh_checkpoint") or {}

    def checkpoint(phase, **payload):
        reply = call(
            config,
            "refresh_checkpoint",
            fence=work["fence"],
            operation_id=work["id"],
            phase=phase,
            **payload,
        )
        if reply.get("ok") is not True:
            code = reply.get("error_code")
            raise AuthRunnerError(
                code
                if code in {"cloud_import_credentials_reused", "cloud_import_account_mismatch"}
                else "cloud_auth_revision_changed"
            )

    if phase in {"sent", "unknown"}:
        raise AuthRunnerError("cloud_refresh_outcome_unknown")
    if phase == "verified":
        return {"action": "verify_import", **source}
    if phase == "not_started":
        original = get_import_identity(work["provider"], work["payload"]["access_token"])
        checkpoint("sent", identity=asdict(original))
        # This is the sole refresh call. Any failure after the sent boundary burns the old grant.
        try:
            refreshed = refresh_import(
                work["provider"], work["payload"]["refresh_token"], now=datetime.now(UTC)
            )
        except Exception:  # noqa: BLE001 - outcome is uncertain even when provider details are unavailable
            try:
                checkpoint("unknown")
            except Exception:  # noqa: BLE001, S110 - expired sent lease will be burned by claim
                pass
            raise AuthRunnerError("cloud_refresh_outcome_unknown") from None
        try:
            checkpoint("refreshed", tokens=asdict(refreshed))
        except Exception:  # noqa: BLE001 - write may have committed despite a lost reply
            raise AuthRunnerError("cloud_refresh_outcome_unknown") from None
    elif phase == "refreshed":
        refreshed = token(record["tokens"])
        original = record["identity"]
    else:
        raise AuthRunnerError("cloud_import_checkpoint_invalid")
    try:
        actual = get_import_identity(work["provider"], refreshed.access_token)
    except ProviderError as error:
        if error.code in {"provider_network_error", "provider_throttled"}:
            raise AuthRunnerError("cloud_auth_bridge_failed") from None
        raise
    original_id = original["account_id"] if isinstance(original, dict) else original.account_id
    if actual.provider != work["provider"] or actual.account_id != original_id:
        raise AuthRunnerError("cloud_import_account_mismatch")
    checkpoint("verified", identity=asdict(actual))
    return {"action": "verify_import", **source}


def device(value):
    qr = dict(value["qr"])
    qr["expires_at"] = datetime.fromisoformat(qr["expires_at"])
    return DeviceGrant(
        value["secret_payload"],
        QrPresentation(**qr),
        datetime.fromisoformat(value["expires_at"]),
        value["interval_seconds"],
    )


def connector_admin(config, work, journal):
    path = Path(
        config.get(
            "connector_registry", str(Path(config["app_dir"]) / "cloud-connector/registry.json")
        )
    )
    registry = load_registry(path)
    candidate = work["candidate"]
    if candidate is None or registry["connector_id"] != candidate["connector_id"]:
        raise AuthRunnerError("cloud_connector_unavailable")
    connector = RegisteredConnector(
        UUID(registry["connector_id"]),
        registry["origin"],
        registry.get("connector_version", "4.2.6"),
        True,  # load_registry has already verified explicit ERP management.
    )
    secret = Path(registry["admin_secret_file"]).read_text().strip()
    api = OpenListAPI.from_admin_password(connector, secret)
    return OpenListAdmin(
        connector,
        api,
        journal,
        lambda provider, tokens: provider_for(provider).get_identity(tokens),
        candidate_context=lambda operation_id: (
            UUID(candidate["id"]),
            candidate["config_revision"],
        ),
        **(
            {"auth_source": lambda operation_id: work_source(work)}
            if work.get("protocol_version") == 2
            else {}
        ),
    )


def access_wire(access):
    return {"ref": asdict(access.ref), "username": access.username, "password": access.password}


def recover_probe(operation_id, proof, record, journal):
    identity = UUID(str(operation_id))
    if record.directory != proof["directory"] or not re.fullmatch(
        r"/erp-probe-" + identity.hex + r"-[a-f0-9]{32}\.bin", proof.get("path", "")
    ):
        raise AuthRunnerError("cloud_config_changed")
    Transport("http://127.0.0.1:5244", record.username, record.password).delete(proof["path"])
    journal.checkpoint("recover_probe", identity)


def perform(config, work, call):
    action, identity = work["action"], UUID(work["id"])
    source = work_source(work)
    result = {"action": action, **source}
    if action == "verify_import":
        return perform_import(config, work, call, source)
    if action in {"create_qr", "poll"}:
        adapter, app = provider_for(work["provider"]), work["application"]
        if action == "create_qr":
            result["grant"] = asdict(
                adapter.start(app["client_id"], app.get("client_secret"), datetime.now(UTC))
            )
        else:
            result["poll"] = asdict(
                adapter.poll(
                    device(work["grant"]),
                    app["client_id"],
                    app.get("client_secret"),
                    datetime.now(UTC),
                )
            )
        return result
    journal = RemoteJournal(config, work, call)
    admin = connector_admin(config, work, journal)
    candidate = work["candidate"]
    prepare_id = UUID(candidate["operation_id"])
    if action in {"apply", "unbind"}:
        record = journal.read(prepare_id)
        if record is None or not record.storage_id or not record.webdav_user_id:
            raise AuthRunnerError("cloud_connector_failed")
        ref = CandidateRef(
            record.operation_id,
            record.storage_id,
            record.webdav_user_id,
            record.mount_path,
            record.account_fingerprint,
        )
        if action == "apply":
            admin.verify_access(ref, candidate["directory"])
        else:
            admin.verify_unbind(ref)
        return {**result, "host_drain_receipt": str(uuid4())}
    if action == "cleanup":
        record = journal.read(prepare_id)
        if record is not None:
            for recovery in work.get("probe_recoveries", []):
                recover_probe(recovery["operation_id"], recovery["proof"], record, journal)
            admin.cleanup_operation(prepare_id)
        return {**result, "cleaned": True}
    if action == "prepare":
        access = admin.prepare(
            prepare_id,
            work["provider"],
            work.get("application"),
            token(work["tokens"]) if work.get("tokens") else None,
            **({"source": source} if source else {}),
        )
        return {**result, "access": access_wire(access)}
    ref = admin.reconcile(prepare_id)
    if ref is None:
        raise AuthRunnerError("cloud_connector_failed")
    if action == "list_directories":
        return {
            **result,
            "entries": [
                asdict(entry) for entry in admin.list_directories(ref, work["payload"]["parent"])
            ],
        }
    if action == "select_directory":
        return {
            **result,
            "access": access_wire(admin.select_directory(ref, work["payload"]["directory"])),
        }
    if action == "probe":
        record = journal.read(prepare_id)
        if not record or record.scope_pending or record.creation_unknown:
            raise AuthRunnerError("cloud_config_changed")
        if work.get("probe_recovery"):
            recover_probe(identity, work["probe_recovery"], record, journal)
        access = CandidateAccess(
            ref,
            record.username,
            record.password,
            "http://127.0.0.1:5244",
            record.directory,
            record.candidate_id,
            record.config_revision,
        )
        return {
            **result,
            "evidence": asdict(
                probe_candidate(
                    access,
                    work["payload"].get("directory", candidate["directory"]),
                    identity,
                    journal=journal,
                )
            ),
        }
    raise AuthRunnerError("cloud_provider_unsupported")


def safe_error(error):
    code = getattr(error, "code", str(error))
    return {
        "cloud_readback_failed": "cloud_remote_corrupt",
        "provider_throttled": "cloud_provider_throttled",
        "provider_reauthorize": "cloud_authorization_invalid",
        "provider_identity_unconfirmed": "cloud_account_unverified",
    }.get(
        code,
        code
        if code
        in {
            "cloud_target_busy",
            "cloud_connector_unavailable",
            "cloud_invalid_path",
            "cloud_remote_corrupt",
            "cloud_redirect_rejected",
            "cloud_auth_revision_changed",
            "cloud_config_changed",
            "cloud_connector_failed",
            "cloud_cleanup_pending",
            "cloud_delivery_unknown",
            "cloud_directory_delivery_unknown",
            "cloud_refresh_outcome_unknown",
            "cloud_import_account_mismatch",
            "cloud_import_credentials_reused",
            "cloud_import_checkpoint_invalid",
            "cloud_auth_bridge_failed",
        }
        else "cloud_auth_failed",
    )


def execute(config, work, call):
    stop, lost = threading.Event(), threading.Event()

    def renew():
        while not stop.wait(30):
            try:
                if call(config, "renew", fence=work["fence"]).get("ok") is not True:
                    lost.set()
                    return
            except Exception:  # noqa: BLE001 - lease failure closes the forward action
                lost.set()
                return

    thread = threading.Thread(target=renew, daemon=True)
    thread.start()
    try:
        try:
            result = perform(config, work, call)
        except Exception as error:  # noqa: BLE001 - only allowlisted codes cross the bridge
            result = {"action": work["action"], "error_code": safe_error(error)}
            if work.get("protocol_version") == 2:
                result.update({key: work[key] for key in SOURCE_FIELDS})
        if not lost.is_set():
            call(config, "complete", fence=work["fence"], result=result)
    finally:
        stop.set()
        thread.join(timeout=31)


def run(config, *, deadline_seconds=55, call=bridge):
    if deadline_seconds <= 0:
        return
    app = Path(config["app_dir"])
    deadline = time.monotonic() + min(deadline_seconds, 55)
    with locked(app / ".cloud-auth-runner.lock"):
        registry_path = Path(
            config.get("connector_registry", str(app / "cloud-connector/registry.json"))
        )
        while time.monotonic() < deadline:
            try:
                with operation_locks(app):
                    validate_active_release(app)
                    from cloud_auth.recovery import (
                        DockerRuntime,
                        SnapshotBridge,
                        recover_interrupted_snapshot,
                    )

                    registry = load_registry(registry_path)
                    recover_interrupted_snapshot(
                        registry_path, DockerRuntime(), SnapshotBridge(config["project"])
                    )
                    call(config, "heartbeat", connector_id=registry["connector_id"])
                    if time.monotonic() >= deadline:
                        break
                    work = call(config, "claim").get("work")
                    if work is not None:
                        execute(config, work, call)
            except ReleaseMismatch:
                return
            except Exception:  # noqa: BLE001, S110 - no unbounded secret-bearing host logs
                # Safe inactivity during another bounded host operation or pending installation.
                pass
            time.sleep(min(1, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    try:
        run(json.loads(Path(args.config).read_text()))
    except Exception:  # noqa: BLE001 - CLI errors must never print secrets
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
