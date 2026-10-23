"""Opt-in encrypted 0065-to-0069 restore; never uses shared database fixtures."""

from __future__ import annotations

import ast
import base64
import copy
import datetime as dt
import hashlib
import importlib.util
import io
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
OWNER_LABEL = "erp.assisted.restore.owner"


def load_probe():
    spec = importlib.util.spec_from_file_location(
        "restore_probe_pure", Path(__file__).with_name("assisted_restore_probe.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_restore_services_share_clock_and_observe_cross_purpose_email_cooldown():
    from app.security.encryption import EnvelopeCipher, LookupDigest

    fixture = load_probe().pending_service_fixture(
        None, EnvelopeCipher(b"c" * 32), LookupDigest(b"d" * 32)
    )
    fixture.clock.now = dt.datetime(2026, 10, 21, 12, tzinfo=dt.UTC)
    claim_sent_at = fixture.public.verification._now()
    assert fixture.public._now() == fixture.activation._now() == claim_sent_at
    fixture.clock.after_claim_cooldown()
    assert fixture.public.verification._now() > claim_sent_at + dt.timedelta(seconds=60)
    assert fixture.activation._now() == fixture.public.verification._now()
    assert fixture.public._now() == fixture.activation._now()


def test_restore_activation_password_satisfies_real_account_policy():
    from app.security.passwords import PasswordService

    PasswordService.validate(load_probe().PENDING_ACTIVATION_PASSWORD)


@pytest.mark.parametrize("passed", [False, True])
def test_owned_0069_cleanup_reports_or_removes_exact_owned_scratch(tmp_path, passed):
    docker = OwnedDocker(("sha256:" + "a" * 64,), "", "", {})
    scratch = tmp_path / "assisted-restore-0069.test_owned"
    scratch.mkdir(mode=0o700)
    docker.scratch = scratch
    private_write(scratch / "owner", docker.owner)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    status = docker.cleanup_owned(passed=passed, parent=tmp_path)
    assert status["cleanup_failed"] is False
    assert status["owned_containers_remaining"] == 0
    assert scratch.exists() is not passed
    assert status["retained_directory"] == (None if passed else scratch.name)
    assert unrelated.is_dir()


def test_legacy_person_seed_runs_against_pre_pending_schema_without_docker():
    """Current ORM client defaults must not add 0069 columns to a 0065 INSERT."""
    from sqlalchemy import create_engine, text

    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE people (id TEXT PRIMARY KEY, full_name TEXT, sex TEXT, date_of_birth TEXT, identity_ciphertext TEXT, identity_digest TEXT, phone_ciphertext TEXT, phone_digest TEXT, email_ciphertext TEXT, email_digest TEXT)"
            )
        )
        connection.execute(
            load_probe().legacy_person_statement(),
            {
                "id": "original-person",
                "full_name": "Synthetic legacy",
                "sex": "male",
                "date_of_birth": "1988-01-02",
                "identity_ciphertext": "encrypted",
                "identity_digest": "identity",
                "phone_ciphertext": "phone-cipher",
                "phone_digest": "phone",
                "email_ciphertext": "email-cipher",
                "email_digest": "email",
            },
        )
        assert connection.execute(text("SELECT id,date_of_birth FROM people")).fetchall() == [
            ("original-person", "1988-01-02")
        ]
    engine.dispose()


@pytest.mark.parametrize(
    "missing",
    [
        "migrations/versions/0069_pending_profile_intake.py",
        "app/identity/document_identity.py",
        "app/personnel_intake/claims/completion.py",
        "app/personnel_intake/readiness.py",
    ],
)
def test_recovery_archive_fails_closed_when_pending_dependency_missing(tmp_path, missing):
    api = ROOT / "apps/api"
    for relative in (
        "alembic.ini",
        "pyproject.toml",
        "migrations/versions/0068_deferred_email_claims.py",
        "migrations/versions/0069_pending_profile_intake.py",
        "app/personnel_intake/claims/models.py",
        "app/personnel_intake/claims/schemas.py",
        "app/personnel_intake/claims/proof.py",
        "app/personnel_intake/claims/binding.py",
        "app/personnel_intake/claims/completion.py",
        "app/personnel_intake/activation_service.py",
        "app/personnel_intake/readiness.py",
        "app/identity/document_identity.py",
        "Dockerfile",
        "tests/cli/assisted_restore_probe.py",
        "tests/cli/test_assisted_personnel_backup_docker.py",
        "tests/db/test_assisted_personnel_migration.py",
    ):
        if relative != missing:
            target = tmp_path / "apps/api" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(api / relative, target)
    helper = tmp_path / "scripts/erp_backup_container.py"
    helper.parent.mkdir()
    shutil.copyfile(ROOT / "scripts/erp_backup_container.py", helper)
    with pytest.raises(ValueError, match="recovery_source_incomplete"):
        write_source_archive(tmp_path, tmp_path / "source.tar.gz")


def test_pending_restore_inputs_preserve_unknowns_and_both_binding_origins():
    module = load_probe()
    cases = module.pending_fixture_inputs()
    assert set(cases) == {
        "pending_passport",
        "pending_passport_unknown_sex",
        "pending_staff",
        "binding_introduced",
        "binding_preserved",
        "revoke_introduced",
        "revoke_preserved",
        "complete_unassigned",
    }
    passport = cases["pending_passport"]
    assert passport.identity_number.get_secret_value() == " zz-rst-0069 "
    assert passport.document_issuing_country is passport.date_of_birth is None
    assert passport.sex == "male" and passport.phone is passport.email is None
    assert cases["pending_passport_unknown_sex"].sex is None
    for name, value in cases.items():
        assert value.kind == "staff" and value.team_id is None and value.coach_person_ids == []
        assert value.team_allocation_status == "pending"
        if name.endswith("preserved"):
            assert (
                value.email.get_secret_value()
                == f"restore-{name.replace('_', '-')}-0069@example.com"
            )
        elif name != "complete_unassigned":
            assert value.email is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("raw_document_plaintext_hash", "tampered"),
        ("date_of_birth", "1900-01-01"),
        ("staff_type", "coach"),
        ("email_binding_origin", "preserved"),
        ("stay_ids", ["replacement-stay"]),
        ("session_submission_version", 1),
    ],
)
def test_pending_restore_case_probe_rejects_tampered_domain_values(field, value):
    original = {
        "person": "original-person",
        "staff_profile_person": "original-person",
        "staff_type": "other",
        "date_of_birth": None,
        "raw_document_plaintext_hash": "original-hash",
        "email_binding_origin": "introduced",
        "stay_ids": ["original-stay"],
        "session_submission_version": 2,
    }
    observed = copy.deepcopy(original)
    load_probe().assert_pending_restored_case(observed, original)
    observed[field] = value
    with pytest.raises(AssertionError, match="pending_restore_case_changed"):
        load_probe().assert_pending_restored_case(observed, original)


def write_source_archive(root, target):
    """Bundle complete Python dependencies and bind the API image to exact bytes."""
    api = root / "apps/api"
    sources = sorted(
        {api / "alembic.ini", api / "pyproject.toml"}
        | set((api / "app").rglob("*.py"))
        | set((api / "migrations").rglob("*.py"))
    )
    required = {
        "migrations/versions/0068_deferred_email_claims.py",
        "migrations/versions/0069_pending_profile_intake.py",
        "app/identity/document_identity.py",
        "app/personnel_intake/readiness.py",
        "app/personnel_intake/claims/completion.py",
        "app/personnel_intake/claims/models.py",
        "app/personnel_intake/claims/schemas.py",
        "app/personnel_intake/claims/proof.py",
        "app/personnel_intake/claims/binding.py",
        "app/personnel_intake/activation_service.py",
    }
    if not required <= {path.relative_to(api).as_posix() for path in sources}:
        raise ValueError("recovery_source_incomplete")
    inventory = {}
    with tarfile.open(target, "x:gz") as archive:
        archived_only = [
            root / "scripts/erp_backup_container.py",
            api / "Dockerfile",
            api / "tests/cli/assisted_restore_probe.py",
            api / "tests/cli/test_assisted_personnel_backup_docker.py",
            api / "tests/db/test_assisted_personnel_migration.py",
        ]
        for path in [*archived_only, *sources]:
            if path.is_symlink() or not path.is_file() or path.resolve() != path.absolute():
                raise ValueError("recovery_source_invalid")
            raw = path.read_bytes()
            member = tarfile.TarInfo(path.relative_to(root).as_posix())
            member.size, member.mode = len(raw), 0o600
            archive.addfile(member, io.BytesIO(raw))
            if path in sources:
                relative = path.relative_to(api).as_posix()
                # Dockerfile production intentionally excludes E2E-only seed
                # modules. Keep them in the recovery source archive, but bind
                # image bytes only for the explicitly shipped CLI entry points.
                production_cli = {
                    "app/cli/__init__.py",
                    "app/cli/create_admin.py",
                    "app/cli/run_teaching_scheduler.py",
                }
                if not relative.startswith("app/cli/") or relative in production_cli:
                    inventory[relative] = hashlib.sha256(raw).hexdigest()
    return inventory


def test_source_archive_covers_claim_code_and_image_fingerprints_without_docker(tmp_path):
    """Missing a DTO/dependency from the recovery bundle must not silently pass."""
    target = tmp_path / "source.tar.gz"
    inventory = write_source_archive(ROOT, target)
    with tarfile.open(target) as archive:
        for relative in (
            "migrations/versions/0068_deferred_email_claims.py",
            "migrations/versions/0069_pending_profile_intake.py",
            "app/identity/document_identity.py",
            "app/personnel_intake/readiness.py",
            "app/personnel_intake/claims/completion.py",
            "app/personnel_intake/claims/models.py",
            "app/personnel_intake/claims/schemas.py",
            "app/personnel_intake/claims/proof.py",
            "app/personnel_intake/claims/binding.py",
            "app/personnel_intake/activation_service.py",
        ):
            raw = (ROOT / "apps/api" / relative).read_bytes()
            assert archive.extractfile("apps/api/" + relative).read() == raw
            assert inventory[relative] == hashlib.sha256(raw).hexdigest()
        assert (
            archive.extractfile("scripts/erp_backup_container.py").read()
            == (ROOT / "scripts/erp_backup_container.py").read_bytes()
        )
        seed = "app/cli/seed_assisted_personnel_e2e.py"
        assert (
            archive.extractfile("apps/api/" + seed).read()
            == (ROOT / "apps/api" / seed).read_bytes()
        )
        assert seed not in inventory, "production image deliberately excludes test seed modules"
        assert "app/cli/create_admin.py" in inventory


@pytest.mark.parametrize("mutation", ["changed", "missing", "symlink", "escape", "empty"])
@pytest.mark.parametrize(
    "relative",
    [
        "app/personnel_intake/claims/models.py",
        "app/personnel_intake/claims/schemas.py",
        "migrations/versions/0068_deferred_email_claims.py",
        "migrations/versions/0069_pending_profile_intake.py",
        "app/identity/document_identity.py",
        "app/personnel_intake/claims/completion.py",
    ],
)
def test_api_image_source_guard_rejects_unverified_source_without_docker(
    tmp_path, mutation, relative
):
    spec = importlib.util.spec_from_file_location(
        "restore_probe_source_guard", Path(__file__).with_name("assisted_restore_probe.py")
    )
    probe_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_module)
    source = tmp_path / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"expected")
    inventory = {relative: hashlib.sha256(b"expected").hexdigest()}
    probe_module.verify_api_source(inventory, root=tmp_path)
    if mutation == "changed":
        source.write_bytes(b"old-image-source")
    elif mutation == "missing":
        source.unlink()
    elif mutation == "symlink":
        source.unlink()
        source.symlink_to(__file__)
    elif mutation == "escape":
        inventory = {"../" + relative: inventory[relative]}
    else:
        inventory = {}
    with pytest.raises(ValueError, match="api_image_source_mismatch"):
        probe_module.verify_api_source(inventory, root=tmp_path)


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5, 6, 7, 8])
def test_restore_synthetic_identity_inputs_are_valid_without_docker(index):
    """Bad checksum/date fixture data must fail before an expensive restore drill."""
    from app.personnel_intake.claims.schemas import ClaimStartInput
    from app.security.encryption import EnvelopeCipher, LookupDigest

    spec = importlib.util.spec_from_file_location(
        "restore_probe_synthetic_inputs", Path(__file__).with_name("assisted_restore_probe.py")
    )
    probe_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_module)
    cipher, lookup = EnvelopeCipher(b"c" * 32), LookupDigest(b"d" * 32)
    values = probe_module.person_values(index, cipher, lookup)
    identity = cipher.decrypt(values["identity_ciphertext"], field_name="identity_number")
    email = cipher.decrypt(values["email_ciphertext"], field_name="email")
    normalized = ClaimStartInput(
        full_name=values["full_name"], identity_number=identity, email=email
    )
    assert normalized.identity_number.get_secret_value() == identity
    assert normalized.email.get_secret_value() == email
    assert values["identity_digest"] == lookup.digest(identity)
    assert values["email_digest"] == lookup.digest_domain("person-email", email)


def require_image(value):
    if not isinstance(value, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
        raise ValueError("immutable_image_required")
    return value


def check_owned(details, identifier, label, network, volumes=None):
    host = details.get("HostConfig", {})
    actual_volumes = tuple(
        sorted(m.get("Name", "") for m in details.get("Mounts", []) if m.get("Type") == "volume")
    )
    if (
        details.get("Id") != identifier
        or details.get("Config", {}).get("Labels", {}).get(label[0]) != label[1]
        or host.get("NetworkMode") != network
        or host.get("PortBindings")
        or host.get("Binds")
        or any(m.get("Type") not in {"volume", "tmpfs"} for m in details.get("Mounts", []))
        or any(not re.fullmatch(r"[0-9a-f]{64}", name) for name in actual_volumes)
        or (volumes is not None and actual_volumes != volumes)
    ):
        raise ValueError("ownership_mismatch")
    return actual_volumes


@pytest.mark.parametrize("value", [None, "", "api:latest", "sha256:abc"])
def test_mutable_or_missing_image_is_rejected_without_docker(value):
    with pytest.raises(ValueError, match="immutable_image_required"):
        require_image(value)


def owned_details():
    return {
        "Id": "a" * 64,
        "Config": {"Labels": {"erp.assisted.restore.owner": "owned"}},
        "HostConfig": {"NetworkMode": "none", "PortBindings": {}, "Binds": None},
        "Mounts": [{"Type": "volume", "Name": "b" * 64}],
    }


@pytest.mark.parametrize("mutation", ["id", "label", "network", "port", "bind", "named", "volume"])
def test_ownership_mismatch_refuses_operations_without_docker(mutation):
    details = copy.deepcopy(owned_details())
    if mutation == "id":
        details["Id"] = "c" * 64
    elif mutation == "label":
        details["Config"]["Labels"] = {}
    elif mutation == "network":
        details["HostConfig"]["NetworkMode"] = "bridge"
    elif mutation == "port":
        details["HostConfig"]["PortBindings"] = {"5432/tcp": [{"HostPort": "56527"}]}
    elif mutation == "bind":
        details["Mounts"] = [{"Type": "bind", "Source": "/private"}]
    elif mutation == "named":
        details["Mounts"][0]["Name"] = "shared-data"
    else:
        details["Mounts"][0]["Name"] = "d" * 64
    with pytest.raises(ValueError, match="ownership_mismatch"):
        check_owned(details, "a" * 64, ("erp.assisted.restore.owner", "owned"), "none", ("b" * 64,))


def test_owned_private_container_is_accepted_without_docker():
    assert check_owned(
        owned_details(), "a" * 64, ("erp.assisted.restore.owner", "owned"), "none"
    ) == ("b" * 64,)


class OwnedDocker:
    """Intercept only this loaded backup module, not process-global subprocess.

    Capture IDs at create, replace exact known names with IDs, guard every
    exec/start/remove, and never attach to an inherited container or host port.
    """

    def __init__(self, images, helper, probe_program, evidence):
        self.images = set(images)
        self.owner = secrets.token_hex(16)
        self.records = {}
        self.names = {}
        self.removed = []
        self.helper, self.probe_program, self.evidence = helper, probe_program, evidence
        self.probes = 0
        self.scratch = None
        self.blocked = set()

    def write_event(self, event, identifier=None, **extra):
        """Durable private ownership evidence, never subprocess output/config."""
        scratch = self.scratch
        if (
            scratch is None
            or scratch.is_symlink()
            or not scratch.is_dir()
            or scratch.stat().st_mode & 0o777 != 0o700
            or (scratch / "owner").is_symlink()
            or (scratch / "owner").read_text() != self.owner
        ):
            raise ValueError("scratch_ownership_mismatch")
        entry = {"event": event, "owner": self.owner}
        if identifier is not None:
            label, network, volumes = self.records[identifier]
            entry.update(container_id=identifier, label=label, network=network, volumes=volumes)
        entry.update(extra)
        descriptor = os.open(
            scratch / "ownership.jsonl",
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w") as stream:
            details = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_mode & 0o777 != 0o600
                or details.st_nlink != 1
            ):
                raise ValueError("ledger_permissions_invalid")
            stream.write(json.dumps(entry, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(scratch, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def inspect(self, identifier):
        result = subprocess.run(
            ["docker", "inspect", identifier], capture_output=True, check=True, timeout=30
        )
        return json.loads(result.stdout)[0]

    def guard(self, target):
        identifier = self.names.get(target, target)
        if identifier not in self.records:
            raise ValueError("unowned_container_refused")
        if identifier in self.blocked:
            raise ValueError("retained_container_requires_diagnosis")
        label, network, volumes = self.records[identifier]
        check_owned(self.inspect(identifier), identifier, label, network, volumes)
        return identifier

    def remove(self, identifier):
        identifier = self.names.get(identifier, identifier)
        if identifier not in self.records:
            raise ValueError("unowned_container_refused")
        try:
            self.guard(identifier)
        except BaseException:
            self.blocked.add(identifier)
            self.write_event("removal_guard_failed", identifier)
            raise
        self.write_event("removal_requested", identifier)
        try:
            result = subprocess.run(
                ["docker", "rm", "--force", "--volumes", identifier],
                capture_output=True,
                check=True,
                timeout=60,
            )
        except BaseException:
            self.blocked.add(identifier)
            self.write_event("removal_failed", identifier)
            raise
        try:
            self.write_event("removed", identifier)
        finally:
            self.records.pop(identifier)
            self.removed.append(identifier)
        return result

    def cleanup_owned(self, *, passed, parent):
        """Always report retained scratch, even when a guard/rm/ledger fails."""
        error = None
        scratch = self.scratch
        try:
            for identifier in list(self.records):
                self.remove(identifier)
            if passed:
                if (
                    scratch.is_symlink()
                    or scratch.resolve().parent != parent.resolve()
                    or not re.fullmatch(r"assisted-restore-006[69]\.[A-Za-z0-9_]+", scratch.name)
                    or (scratch / "owner").is_symlink()
                    or (scratch / "owner").read_text() != self.owner
                ):
                    raise ValueError("scratch_ownership_mismatch")
                self.write_event("cleanup_complete")
                shutil.rmtree(scratch)
        except BaseException as exc:  # noqa: BLE001 - report, then re-raise interrupts below.
            error = exc
        status = {
            "cleanup_failed": error is not None,
            "owned_containers_remaining": len(self.records),
            "owned_containers_removed": len(self.removed),
            "private_scratch_removed": not scratch.exists(),
            "retained_directory": (
                scratch.name
                if re.fullmatch(r"assisted-restore-006[69]\.[A-Za-z0-9_]+", scratch.name)
                else "unverified"
            )
            if scratch.exists()
            else None,
        }
        try:
            if scratch.exists():
                self.write_event("cleanup_summary", **status)
        except BaseException as exc:  # noqa: BLE001 - reporting must survive ledger failure.
            error = error or exc
            status["cleanup_failed"] = True
        finally:
            # Never stringify the exception, command, stdin, stderr or private files.
            print(json.dumps(status, sort_keys=True))
        if error is not None:
            if not isinstance(error, Exception):
                raise error
            raise ValueError("owned_cleanup_failed") from None
        return status

    def run(self, command, **kwargs):
        command = list(command)
        check = kwargs.pop("check", False)
        if command[0] != "docker":
            return subprocess.run(command, check=check, **kwargs)
        action = command[1]
        if action == "image":
            if command[2] != "inspect" or not set(command[3:]) <= self.images:
                raise ValueError("unapproved_image_operation")
            return subprocess.run(command, check=check, **kwargs)
        if action == "save":
            if set(command[2:]) != self.images:
                raise ValueError("unapproved_image_operation")
            return subprocess.run(command, check=check, **kwargs)
        if action == "inspect":
            command[2] = self.guard(command[2])
            return subprocess.run(command, check=check, **kwargs)
        if action == "exec":
            offset = 3 if command[2] == "-i" else 2
            command[offset] = self.guard(command[offset])
            return subprocess.run(command, check=check, **kwargs)
        if action == "cp" and len(command) == 4:
            source = Path(command[2])
            target, destination = command[3].split(":", 1)
            identifier = self.guard(target)
            if (
                self.scratch is None
                or source.is_symlink()
                or not source.resolve().is_relative_to(self.scratch.resolve())
                or source.name != "sequences.list"
                or destination != "/tmp/erp-sequences.list"
            ):
                raise ValueError("unapproved_evidence_copy")
            command[3] = identifier + ":" + destination
            return subprocess.run(command, check=check, **kwargs)
        if action == "rm" and command[2:4] == ["--force", "--volumes"] and len(command) == 5:
            return self.remove(command[4])
        if action != "run":
            raise ValueError("unapproved_docker_operation")
        # A prior local inspect is not atomic with create. Never let the daemon
        # retrieve a missing image, including a caller-supplied conflicting flag.
        image_positions = [i for i, value in enumerate(command) if value in self.images]
        if len(image_positions) != 1:
            raise ValueError("immutable_image_required")
        options, tail = command[2 : image_positions[0]], command[image_positions[0] :]
        cleaned = []
        while options:
            option = options.pop(0)
            if option == "--pull":
                if not options or options.pop(0) != "never":
                    raise ValueError("pull_policy_refused")
            elif option.startswith("--pull="):
                if option != "--pull=never":
                    raise ValueError("pull_policy_refused")
            else:
                cleaned.append(option)
        command = ["docker", "run", *cleaned, *tail]
        if any(
            flag in command
            for flag in (
                "-v",
                "--volume",
                "--mount",
                "--volumes-from",
                "-p",
                "--publish",
                "--publish-all",
            )
        ):
            raise ValueError("host_resource_refused")
        network_index = command.index("--network") + 1
        network = command[network_index]
        if network.startswith("container:"):
            target = self.guard(network.removeprefix("container:"))
            network = command[network_index] = "container:" + target
        elif network != "none":
            raise ValueError("external_network_refused")
        image_positions = [i for i, value in enumerate(command) if value in self.images]
        if len(image_positions) != 1:
            raise ValueError("immutable_image_required")
        name = command[command.index("--name") + 1] if "--name" in command else None
        label = (OWNER_LABEL, self.owner)
        if "--label" in command:
            key, value = command[command.index("--label") + 1].split("=", 1)
            if key != "erp.backup.verify" or not re.fullmatch(r"[0-9a-f]{32}", value):
                raise ValueError("unexpected_restore_owner")
            label = (key, value)
        else:
            command[2:2] = ["--label", OWNER_LABEL + "=" + self.owner]
        if command[-1] == "verify":
            if command[-3:] != ["-c", self.helper, "verify"] or network == "none":
                raise ValueError("unrecognized_probe_seam")
            target_record = self.records[target]
            if target_record[0][0] != "erp.backup.verify":
                raise ValueError("unowned_restore_probe")
            self.probes += 1
            if self.probes != 1:
                raise ValueError("multiple_restore_probes")
            original = json.loads(kwargs["input"])
            original["domain_evidence"] = self.evidence
            kwargs["input"] = json.dumps(original).encode()
            command[-2] = self.probe_program
        detached = "--detach" in command
        create = ["docker", "create", "--pull=never"] + [
            arg for arg in command[2:] if arg not in {"--detach", "--rm"}
        ]
        self.write_event("creation_requested", label=label, network=network)
        created = subprocess.run(create, capture_output=True, check=True, timeout=120)
        identifier = created.stdout.decode().strip()
        if not re.fullmatch(r"[0-9a-f]{64}", identifier):
            raise ValueError("container_id_not_captured")
        # Register before validation: mismatched state is retained, never removed.
        self.records[identifier] = (label, network, None)
        self.write_event("captured", identifier)
        if name:
            if name in self.names:
                raise ValueError("duplicate_owned_name")
            self.names[name] = identifier
        try:
            volumes = check_owned(self.inspect(identifier), identifier, label, network)
        except BaseException:
            self.blocked.add(identifier)
            self.write_event("validation_failed", identifier)
            raise
        self.records[identifier] = (label, network, volumes)
        self.write_event("validated", identifier)
        self.guard(identifier)
        if detached:
            subprocess.run(
                ["docker", "start", identifier], capture_output=True, check=True, timeout=120
            )
            return subprocess.CompletedProcess(command, 0, (identifier + "\n").encode())
        try:
            return subprocess.run(
                ["docker", "start", "--attach", "--interactive", identifier], check=check, **kwargs
            )
        finally:
            self.remove(identifier)


def load_backup():
    spec = importlib.util.spec_from_file_location(
        "assisted_backup_drill", ROOT / "scripts/erp-backup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def private_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        os.chmod(path, 0o600)
        stream.write(value.encode() if isinstance(value, str) else value)


@pytest.mark.parametrize("operation", ["exec", "inspect", "remove", "helper"])
def test_unknown_container_never_reaches_docker(monkeypatch, operation):
    docker = OwnedDocker(("sha256:" + "a" * 64,), "", "", {})

    def forbidden(*args, **kwargs):
        raise AssertionError("external_process_must_not_run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    commands = {
        "exec": ["docker", "exec", "existing-worker", "pg_isready"],
        "inspect": ["docker", "inspect", "existing-worker"],
        "remove": ["docker", "rm", "--force", "--volumes", "existing-worker"],
        "helper": [
            "docker",
            "run",
            "--rm",
            "--network",
            "container:existing-worker",
            "sha256:" + "a" * 64,
        ],
    }
    with pytest.raises(ValueError, match="unowned_container_refused"):
        docker.run(commands[operation])


@pytest.mark.parametrize("kind", ["source", "helper", "verifier", "probe_helper"])
def test_create_missing_image_is_fail_closed_without_docker(monkeypatch, tmp_path, kind):
    image = "sha256:" + "a" * 64
    docker = OwnedDocker((image,), "original-helper", "domain-probe", {})
    scratch = tmp_path / "assisted-restore-0066.missing_image"
    scratch.mkdir(mode=0o700)
    docker.scratch = scratch
    private_write(scratch / "owner", docker.owner)
    details = owned_details()
    label = (OWNER_LABEL, "owned")
    if kind == "probe_helper":
        label = ("erp.backup.verify", "c" * 32)
        details["Config"]["Labels"] = {label[0]: label[1]}
    docker.records["a" * 64] = (label, "none", ("b" * 64,))
    monkeypatch.setattr(docker, "inspect", lambda identifier: details)
    calls = []

    def missing_image(command, **kwargs):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command, stderr=b"private failure")

    monkeypatch.setattr(subprocess, "run", missing_image)
    command = ["docker", "run", "--detach", "--network", "none", image]
    kwargs = {}
    if kind in {"helper", "probe_helper"}:
        command = [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--network",
            "container:" + "a" * 64,
            "--entrypoint",
            "python",
            image,
            "-c",
            "original-helper",
        ]
    if kind == "probe_helper":
        command.append("verify")
        kwargs["input"] = b"{}"
    if kind == "verifier":
        command[2:2] = ["--label", "erp.backup.verify=" + "d" * 32]
    with pytest.raises(subprocess.CalledProcessError):
        docker.run(command, **kwargs)
    assert len(calls) == 1
    assert calls[0][:3] == ["docker", "create", "--pull=never"]
    assert sum(arg.startswith("--pull") for arg in calls[0]) == 1


@pytest.mark.parametrize(
    "policy",
    [["--pull=always"], ["--pull", "missing"], ["--pull=never", "--pull=always"], ["--pull"]],
)
def test_conflicting_pull_policy_never_reaches_docker(monkeypatch, policy):
    image = "sha256:" + "a" * 64
    docker = OwnedDocker((image,), "", "", {})

    def forbidden(*args, **kwargs):
        raise AssertionError("external_process_must_not_run")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with pytest.raises(ValueError, match="pull_policy_refused"):
        docker.run(["docker", "run", *policy, "--network", "none", image])


def private_docker_fixture(tmp_path):
    image = "sha256:" + "e" * 64
    docker = OwnedDocker((image,), "", "", {})
    scratch = tmp_path / "assisted-restore-0066.test_owned"
    scratch.mkdir(mode=0o700)
    docker.scratch = scratch
    private_write(scratch / "owner", docker.owner)
    return docker, scratch, image


def read_ledger(scratch):
    return [json.loads(line) for line in (scratch / "ownership.jsonl").read_text().splitlines()]


def test_lifecycle_ledger_is_durable_before_external_actions(monkeypatch, tmp_path):
    docker, scratch, image = private_docker_fixture(tmp_path)
    details = owned_details()
    details["Config"]["Labels"] = {OWNER_LABEL: docker.owner}
    identifier = details["Id"]

    def fake_docker(command, **kwargs):
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, (identifier + "\n").encode())
        events = read_ledger(scratch)
        if command[1] == "inspect":
            assert any(
                row["event"] == "captured" and row["container_id"] == identifier for row in events
            )
            return subprocess.CompletedProcess(command, 0, json.dumps([details]).encode())
        if command[1] == "start":
            assert any(
                row["event"] == "validated" and row["volumes"] == ["b" * 64] for row in events
            )
        elif command[1] == "rm":
            assert events[-1]["event"] == "removal_requested"
            assert events[-1]["container_id"] == identifier
        else:
            raise AssertionError("unexpected_external_operation")
        return subprocess.CompletedProcess(command, 0, b"")

    monkeypatch.setattr(subprocess, "run", fake_docker)
    docker.run(["docker", "run", "--detach", "--network", "none", image])
    docker.remove(identifier)
    ledger = scratch / "ownership.jsonl"
    assert ledger.stat().st_mode & 0o777 == 0o600
    events = read_ledger(scratch)
    assert events[-1]["event"] == "removed"
    assert events[-1]["label"] == [OWNER_LABEL, docker.owner]
    assert events[-1]["network"] == "none"
    assert events[-1]["volumes"] == ["b" * 64]
    assert docker.records == {}


@pytest.mark.parametrize("failure", ["guard", "rm"])
def test_cleanup_failure_retains_ledger_and_reports_safely(monkeypatch, tmp_path, capsys, failure):
    docker, scratch, image = private_docker_fixture(tmp_path)
    details = owned_details()
    details["Config"]["Labels"] = {OWNER_LABEL: docker.owner}
    identifier = details["Id"]
    calls = []
    failing = False
    private_write(scratch / "private-fixture", "DO_NOT_PRINT_SECRET")

    def fake_docker(command, **kwargs):
        calls.append(command)
        if command[1] == "create":
            return subprocess.CompletedProcess(command, 0, (identifier + "\n").encode())
        if command[1] == "inspect":
            inspected = copy.deepcopy(details)
            if failing and failure == "guard":
                inspected["Config"]["Labels"] = {}
            return subprocess.CompletedProcess(command, 0, json.dumps([inspected]).encode())
        if command[1] == "rm" and failing:
            raise subprocess.CalledProcessError(1, command, stderr=b"DO_NOT_PRINT_SECRET")
        if command[1] != "start":
            raise AssertionError("unexpected_external_operation")
        return subprocess.CompletedProcess(command, 0, b"")

    monkeypatch.setattr(subprocess, "run", fake_docker)
    docker.run(["docker", "run", "--detach", "--network", "none", image])
    failing = True
    with pytest.raises(ValueError, match="owned_cleanup_failed"):
        docker.cleanup_owned(passed=True, parent=tmp_path)
    assert scratch.exists() and (scratch / "private-fixture").read_text() == "DO_NOT_PRINT_SECRET"
    events = read_ledger(scratch)
    expected = "removal_guard_failed" if failure == "guard" else "removal_failed"
    assert any(row["event"] == expected and row["container_id"] == identifier for row in events)
    assert events[-1]["event"] == "cleanup_summary"
    assert events[-1]["cleanup_failed"] is True
    assert (scratch / "ownership.jsonl").stat().st_mode & 0o777 == 0o600
    assert identifier in docker.records
    assert sum(command[1] == "rm" for command in calls) == (0 if failure == "guard" else 1)
    output = capsys.readouterr()
    status = json.loads(output.out)
    assert status["retained_directory"] == scratch.name
    assert status["cleanup_failed"] is True
    assert status["owned_containers_remaining"] == 1
    assert "DO_NOT_PRINT_SECRET" not in output.out + output.err
    assert str(tmp_path) not in output.out + output.err


@pytest.mark.skipif(
    os.environ.get("ERP_BACKUP_DOCKER_TEST") != "1", reason="opt-in Docker rehearsal"
)
def test_encrypted_0069_recovery_preserves_domain_and_provenance(monkeypatch):
    """Breaks if archive recovery loses values, encryption or enforcement."""
    api_image = require_image(os.environ.get("ERP_BACKUP_TEST_API_IMAGE"))
    postgres_image = require_image(os.environ.get("ERP_BACKUP_TEST_POSTGRES_IMAGE"))
    # No application/shared fixture and no ambient service configuration reaches helpers.
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "DATABASE_URL_FILE"):
        monkeypatch.delenv(key, raising=False)
    backup = load_backup()
    helper = (ROOT / "scripts/erp_backup_container.py").read_text()
    definitions = helper.split('if __name__ == "__main__":')[0]
    domain = Path(__file__).with_name("assisted_restore_probe.py").read_text()
    tree = ast.parse((ROOT / "apps/api/tests/db/test_assisted_personnel_migration.py").read_text())
    seed = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "seed_business_rows"
    )
    # AST extracts just the pure seed function; never executes fixture/reset imports.
    producer = (
        definitions
        + "\nfrom sqlalchemy import Engine\nfrom uuid import UUID\n"
        + domain
        + "\n"
        + ast.unparse(seed)
        + "\nvalue = json.load(sys.stdin)\n"
        + "verify_api_source(value['api_source'])\n"
        + "print(json.dumps(produce(value['runtime_config'], seed_business_rows, database_manifest)))\n"
    )
    probe_program = (
        definitions
        + "\n"
        + domain
        + """
import io
from contextlib import redirect_stdout
value = json.load(sys.stdin)
verify_api_source(value['domain_evidence']['api_source'])
sys.stdin = io.StringIO(json.dumps(value))
output = io.StringIO()
with redirect_stdout(output):
    verify()
probe(value['runtime_config'], value['domain_evidence'])
sys.stdout.write(output.getvalue())
"""
    )
    evidence = {}
    docker = OwnedDocker((api_image, postgres_image), helper, probe_program, evidence)
    monkeypatch.setattr(
        backup,
        "subprocess",
        SimpleNamespace(
            run=docker.run,
            PIPE=subprocess.PIPE,
            DEVNULL=subprocess.DEVNULL,
            CalledProcessError=subprocess.CalledProcessError,
            TimeoutExpired=subprocess.TimeoutExpired,
            SubprocessError=subprocess.SubprocessError,
        ),
    )
    parent = Path.home() / ".codex"
    scratch = Path(tempfile.mkdtemp(prefix="assisted-restore-0069.", dir=parent))
    docker.scratch = scratch
    os.chmod(scratch, 0o700)
    owner_file = scratch / "owner"
    private_write(owner_file, docker.owner)
    old_umask = os.umask(0o077)
    passed = False
    summary = {}
    try:
        config = {
            name: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
            for name in (
                "field_encryption_master_key",
                "field_lookup_key",
                "session_token_pepper",
                "registration_verification_pepper",
                "registration_session_token_pepper",
                "evaluation_tool_service_token",
                "evaluation_tool_web_service_token",
                "evaluation_tool_worker_service_token",
            )
        }
        payload = scratch / "payload"
        recovery = payload / "recovery"
        recovery.mkdir(parents=True, mode=0o700)
        api_source = write_source_archive(ROOT, recovery / "source.tar.gz")
        # Inspect local IDs only; never pulls, builds or loads a substitute image.
        backup.run(["docker", "image", "inspect", api_image, postgres_image])
        source = (
            backup.run(
                [
                    "docker",
                    "run",
                    "--detach",
                    "--name",
                    "assisted-source-" + docker.owner,
                    "--network",
                    "none",
                    "--memory",
                    "2g",
                    "--cpus",
                    "1",
                    "--security-opt",
                    "no-new-privileges:true",
                    "-e",
                    "POSTGRES_HOST_AUTH_METHOD=trust",
                    "-e",
                    "POSTGRES_USER=erp_verify",
                    "-e",
                    "POSTGRES_DB=erp_verify",
                    postgres_image,
                ]
            )
            .decode()
            .strip()
        )
        for _ in range(60):
            try:
                backup.run(
                    [
                        "docker",
                        "exec",
                        source,
                        "pg_isready",
                        "-h",
                        "127.0.0.1",
                        "-U",
                        "erp_verify",
                        "-d",
                        "erp_verify",
                    ],
                    timeout=5,
                )
                break
            except backup.BackupError:
                time.sleep(1)
        else:
            raise AssertionError("owned_source_unavailable")
        produced = json.loads(
            backup.run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--interactive",
                    "--network",
                    "container:" + source,
                    "--entrypoint",
                    "python",
                    api_image,
                    "-c",
                    producer,
                ],
                input=json.dumps({"runtime_config": config, "api_source": api_source}).encode(),
            )
        )
        evidence.update(produced["evidence"])
        evidence["api_source"] = api_source
        for name, value in config.items():
            private_write(recovery / "secrets" / name, value)
            private_write(recovery / "mounted-api-secrets" / name, value)
        private_write(recovery / "runtime-config.json", json.dumps(config))
        private_write(recovery / ".env", "APP_ENV=test\n")
        with (recovery / "images.tar").open("xb") as output:
            backup.run(["docker", "save", api_image, postgres_image], stdout=output)
        with (payload / "database.dump").open("xb") as output:
            backup.run(
                [
                    "docker",
                    "exec",
                    source,
                    "pg_dump",
                    "-U",
                    "erp_verify",
                    "-d",
                    "erp_verify",
                    "--format=custom",
                    "--no-owner",
                ],
                stdout=output,
            )
        identity = (
            "backup-"
            + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + secrets.token_hex(4)
        )
        manifest = {
            "backup_id": identity,
            "created_at": backup.now(),
            "release": "isolated-0069-pending-lifecycle-drill",
            "api_image": api_image,
            "postgres_image": postgres_image,
            "postgres_major": 16,
            "database": produced["database"],
            "files": backup.file_inventory(payload),
        }
        backup.atomic_json(payload / "manifest.json", manifest)
        key_file = scratch / "keys" / "backup.key"
        private_write(key_file, base64.urlsafe_b64encode(secrets.token_bytes(48)))
        candidate = scratch / "backups" / identity
        candidate.mkdir(parents=True, mode=0o700)
        key = backup.key_bytes(key_file, candidate.parent)
        plain = scratch / "archive.tar.gz"
        with tarfile.open(plain, "w:gz") as archive:
            for child in sorted(payload.iterdir()):
                archive.add(child, arcname=child.name)
        encrypted = candidate / "archive.tar.gz.enc"
        backup.crypt(plain, encrypted, key_file)
        meta = {
            "format_version": 1,
            "backup_id": identity,
            "created_at": manifest["created_at"],
            "sha256": backup.digest(encrypted),
            "hmac_sha256": backup.digest(encrypted, key),
            "size_bytes": encrypted.stat().st_size,
            "alembic_revision": produced["database"]["alembic_revision"],
            "release": manifest["release"],
            "restore_size_bytes": sum(item["size"] for item in manifest["files"].values()),
            "database_size_bytes": (payload / "database.dump").stat().st_size,
            "image_size_bytes": (recovery / "images.tar").stat().st_size,
        }
        backup.atomic_json(candidate / "complete.json", backup.seal_metadata(meta, key))
        shutil.rmtree(payload)
        plain.unlink()
        assert not plain.exists() and not payload.exists()
        result = backup.verify_archive(candidate, scratch, SimpleNamespace(key_file=key_file), key)
        assert docker.probes == 1
        assert set(result) == {
            "encrypted",
            "verified",
            "media_files",
            "table_count",
            "encrypted_samples",
        }
        assert result["encrypted"] and result["verified"] and result["encrypted_samples"] > 0
        assert all(
            produced["database"]["row_counts"][table] > 0
            for table in (
                "personnel_intake_requests",
                "personnel_intake_revisions",
                "personnel_credential_reservations",
                "assisted_approvals",
                "assisted_person_profiles",
                "coach_person_responsibilities",
                "assisted_activation_sessions",
                "profile_claim_sessions",
                "profile_claims",
                "profile_claim_bindings",
                "profile_claim_decisions",
            )
        )
        summary = {
            "api_image": api_image,
            "postgres_image": postgres_image,
            "backup_id": identity,
            "ciphertext_sha256": meta["sha256"],
            "snapshot_time": manifest["created_at"],
            "revision": produced["database"]["alembic_revision"],
            "recognized_probes": docker.probes,
            "restored_counts": {
                name: count
                for name, count in produced["database"]["row_counts"].items()
                if name
                in {
                    "personnel_intake_requests",
                    "personnel_intake_revisions",
                    "personnel_credential_reservations",
                    "assisted_approvals",
                    "assisted_person_profiles",
                    "coach_person_responsibilities",
                    "assisted_activation_sessions",
                    "profile_claim_sessions",
                    "profile_claims",
                    "profile_claim_bindings",
                    "profile_claim_decisions",
                }
            },
            "domain_values_constraints_triggers_decryption": True,
            "legacy_upgrade_preserved": evidence["old_upgrade_preserved"],
            "api_source_sha256": hashlib.sha256(
                json.dumps(api_source, sort_keys=True).encode()
            ).hexdigest(),
            "claim_statuses_proof_binding_immutability": True,
            "pending_passport_staff_binding_origins_ids_preserved": True,
            "pending_restore_case_count": len(evidence["pending_restore_cases"]),
            "pending_staff_count": sum(
                item["expected"]["staff_type"] == "other"
                for item in evidence["pending_restore_cases"].values()
            ),
            "pending_stay_count": sum(
                len(item["expected"]["stay_ids"])
                for item in evidence["pending_restore_cases"].values()
            ),
        }
        passed = True
    finally:
        try:
            docker.cleanup_owned(passed=passed, parent=parent)
        finally:
            os.umask(old_umask)
    summary.update(
        owned_containers_removed=len(docker.removed),
        owned_containers_remaining=len(docker.records),
        private_scratch_removed=not scratch.exists(),
    )
    print(json.dumps(summary, sort_keys=True))
