"""Opt-in exact encrypted Web save/load, tree and owned-namespace HTTP proof."""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


owned = load("web_restore_ownership", Path(__file__).with_name("test_assisted_personnel_backup_docker.py"))
fixtures = load("web_restore_fixtures", Path(__file__).with_name("test_web_assets_backup.py"))


class WebDocker(owned.OwnedDocker):
    """Extend only known seams; no arbitrary passthrough or global monkeypatch."""

    def __init__(self, images, helper):
        super().__init__(images, helper, helper, {})
        self.web = None
        self.saved_sha = None
        self.loads = 0
        self.image_removed = False
        self.container_images = {}
        self.exact_input = False
        self.force_missing_once = False

    def guard(self, target):
        identifier = super().guard(target)
        actual = self.inspect(identifier)["Image"]
        expected = self.container_images.setdefault(identifier, actual)
        if actual != expected or actual not in self.images:
            raise ValueError("owned_image_mismatch")
        return identifier

    def image_guard(self):
        value = json.loads(subprocess.run(
            ["docker", "image", "inspect", self.web], capture_output=True, check=True, timeout=30
        ).stdout)[0]
        if value["Id"] != self.web or (
            not self.exact_input and value["Config"].get("Labels", {}).get(owned.OWNER_LABEL) != self.owner
        ):
            raise ValueError("unowned_image_refused")

    def remove_image(self):
        if self.exact_input:
            raise ValueError("shared_input_removal_forbidden")
        self.image_guard()
        if any(self.inspect(cid)["Image"] == self.web for cid in self.records):
            raise ValueError("image_still_in_use")
        self.write_event("image_removal_requested", image_id=self.web)
        subprocess.run(["docker", "image", "rm", self.web], capture_output=True, check=True, timeout=60)
        self.image_removed = True
        self.write_event("image_removed", image_id=self.web)

    def popen(self, command, **kwargs):
        if command[0:2] != ["docker", "cp"] or command[3:] != ["-"]:
            raise ValueError("unapproved_stream_operation")
        cid, path = command[2].split(":", 1)
        cid = self.guard(cid)
        if (self.container_images[cid] != self.web
                or path not in ("/etc/erp-web/assets-manifest.json", "/usr/share/nginx/html/.")):
            raise ValueError("unapproved_stream_copy")
        return subprocess.Popen(["docker", "cp", cid + ":" + path, "-"], **kwargs)

    def commit_fixture(self, cid):
        cid = self.guard(cid)
        result = subprocess.run(["docker", "commit", "--change", "USER nginx",
            "--change", 'ENTRYPOINT ["/docker-entrypoint.sh"]', "--change", 'CMD ["nginx","-g","daemon off;"]',
            "--change", "LABEL cn.jingxue.erp.web-assets-contract=1",
            "--change", "LABEL " + owned.OWNER_LABEL + "=" + self.owner, cid],
            capture_output=True, check=True, timeout=60)
        self.web = owned.require_image(result.stdout.decode().strip())
        self.image_guard()
        self.images.add(self.web)
        self.write_event("owned_image_created", image_id=self.web, input_image=self.container_images[cid])

    def run(self, command, **kwargs):
        command = list(command)
        if command[0] != "docker":
            return super().run(command, **kwargs)
        if command[1:3] == ["image", "inspect"] and self.force_missing_once:
            if set(command[3:]) != self.images or not self.exact_input or not self.saved_sha:
                raise ValueError("unapproved_absence_seam")
            self.force_missing_once = False
            self.write_event("simulated_missing_decision_only", image_id=self.web)
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(1, command, stderr=b"isolated_missing_decision")
            return subprocess.CompletedProcess(command, 1, b"", b"isolated_missing_decision")
        if command[1:3] == ["image", "save"]:
            if set(command[3:]) != self.images or self.saved_sha is not None:
                raise ValueError("unapproved_image_save")
            self.image_guard()
            return subprocess.run(command, check=kwargs.pop("check", False), **kwargs)
        if command[1:4] == ["image", "load", "--input"]:
            archive = Path(command[4])
            if (len(command) != 5 or self.scratch is None or archive.is_symlink()
                    or archive != self.scratch / "restore/recovery/images.tar"
                    or self.saved_sha is None or fixtures.backup.digest(archive) != self.saved_sha
                    or not (self.image_removed or self.exact_input) or self.loads):
                raise ValueError("unapproved_image_load")
            manifest = json.loads((self.scratch / "restore/manifest.json").read_text())
            inventory = fixtures.backup.file_inventory(self.scratch / "restore")
            inventory.pop("manifest.json")
            if inventory != manifest["files"]:
                raise ValueError("unauthenticated_image_inventory")
            self.write_event("authenticated_image_load", image_id=self.web, archive_sha256=self.saved_sha)
            result = subprocess.run(command, check=kwargs.pop("check", False), **kwargs)
            self.loads += 1
            self.image_removed = False
            self.image_guard()
            self.write_event("exact_image_restored", image_id=self.web)
            return result
        if command[1] == "create":
            expected = ["docker", "create", "--pull=never", "--network", "none", "--label"]
            if (command[:6] != expected or len(command) != 10
                    or command[7:9] != ["--entrypoint", "/bin/true"] or command[-1] != self.web):
                raise ValueError("unapproved_web_create")
            label = tuple(command[6].split("=", 1))
            if label[0] != "erp.backup.web.verify" or not re.fullmatch(r"[a-f0-9]{32}", label[1]):
                raise ValueError("unapproved_web_owner")
            created = subprocess.run(command, check=kwargs.pop("check", False), **kwargs)
            cid = created.stdout.decode().strip()
            if not re.fullmatch(r"[a-f0-9]{64}", cid):
                raise ValueError("container_id_not_captured")
            self.records[cid] = (label, "none", ())
            self.container_images[cid] = self.web
            self.write_event("captured", cid)
            self.guard(cid)
            return created
        if command[1] == "rm" and len(command) == 3:
            return self.remove(command[-1])
        return super().run(command, **kwargs)


def cleanup(docker, *, passed, parent):
    error = None
    try:
        for cid in list(docker.records):
            docker.remove(cid)
        if docker.web and not docker.image_removed and not docker.exact_input:
            docker.remove_image()
    except BaseException as failure:  # noqa: BLE001 - always retain and report cleanup state.
        error = failure
    try:
        docker.cleanup_owned(passed=passed and error is None, parent=parent)
    except BaseException as failure:  # noqa: BLE001 - preserve the first cleanup failure.
        error = error or failure
    finally:
        print(json.dumps({"cleanup_failed": error is not None, "owned_image_removed": docker.image_removed,
                          "shared_input_preserved": docker.exact_input,
                          "owned_containers_remaining": len(docker.records)}, sort_keys=True))
    if error is not None:
        if not isinstance(error, Exception):
            raise error
        raise ValueError("owned_web_cleanup_failed") from None


@pytest.mark.parametrize("operation", ["commit", "stream", "load", "shared_remove"])
def test_unowned_web_operations_never_reach_docker(monkeypatch, operation):
    docker = WebDocker((fixtures.API, fixtures.DB), "")
    def forbidden(*_args, **_kwargs):
        raise AssertionError("external_process_must_not_run")
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with pytest.raises(ValueError):
        if operation == "commit":
            docker.commit_fixture("existing-container")
        elif operation == "stream":
            docker.popen(["docker", "cp", "existing-container:/usr/share/nginx/html/.", "-"])
        elif operation == "load":
            docker.run(["docker", "image", "load", "--input", "/untrusted/images.tar"])
        else:
            docker.exact_input = True
            docker.remove_image()


def test_image_cleanup_failure_retains_private_evidence(tmp_path, monkeypatch, capsys):
    docker = WebDocker((), "")
    scratch = tmp_path / "assisted-restore-0066.unit"
    scratch.mkdir(mode=0o700)
    docker.scratch = scratch
    owned.private_write(scratch / "owner", docker.owner)
    docker.web = fixtures.WEB
    def fail():
        raise ValueError("DO_NOT_PRINT_SECRET")
    monkeypatch.setattr(docker, "remove_image", fail)
    with pytest.raises(ValueError, match="owned_web_cleanup_failed"):
        cleanup(docker, passed=True, parent=tmp_path)
    assert scratch.exists() and (scratch / "ownership.jsonl").stat().st_mode & 0o777 == 0o600
    output = capsys.readouterr().out
    assert "DO_NOT_PRINT_SECRET" not in output
    assert json.loads(output.splitlines()[-1])["cleanup_failed"] is True


@pytest.mark.skipif(os.environ.get("ERP_BACKUP_DOCKER_TEST") != "1", reason="opt-in Docker rehearsal")
def test_encrypted_web_assets_exact_restore(monkeypatch):
    api, postgres, web_base = (owned.require_image(os.environ.get("ERP_BACKUP_TEST_" + name + "_IMAGE"))
                               for name in ("API", "POSTGRES", "WEB"))
    for key in ("DATABASE_URL", "TEST_DATABASE_URL", "DATABASE_URL_FILE"):
        monkeypatch.delenv(key, raising=False)
    backup = load("web_restore_production", ROOT / "scripts/erp-backup.py")
    helper = (ROOT / "scripts/erp_backup_container.py").read_text()
    docker = WebDocker((api, postgres), helper)
    parent = Path.home() / ".codex/tmp/task5-web-restore"
    parent.mkdir(exist_ok=True, mode=0o700)
    if parent.is_symlink() or parent.stat().st_mode & 0o777 != 0o700:
        raise ValueError("private_parent_required")
    # Existing cleanup verifies this exact pattern; the private parent is new.
    scratch = Path(tempfile.mkdtemp(prefix="assisted-restore-0066.", dir=parent))
    os.chmod(scratch, 0o700)
    docker.scratch = scratch
    owned.private_write(scratch / "owner", docker.owner)
    monkeypatch.setattr(backup, "subprocess", SimpleNamespace(
        run=docker.run, Popen=docker.popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
        CalledProcessError=subprocess.CalledProcessError, TimeoutExpired=subprocess.TimeoutExpired,
        SubprocessError=subprocess.SubprocessError))
    old_umask = os.umask(0o077)
    passed = False
    summary = {}
    try:
        # Inspect explicit cached inputs only, no pull/build from mutable tags.
        subprocess.run(["docker", "image", "inspect", api, postgres, web_base],
                       capture_output=True, check=True, timeout=30)
        payload = scratch / "payload"
        payload.mkdir()
        docker.exact_input = os.environ.get("ERP_BACKUP_TEST_WEB_EXACT") == "1"
        if docker.exact_input:
            receipt = Path(os.environ["ERP_BACKUP_TEST_WEB_RECEIPT_FILE"])
            if receipt.is_symlink() or not receipt.is_file() or receipt.stat().st_size > 65536:
                raise ValueError("explicit_receipt_required")
            (payload / "recovery").mkdir()
            shutil.copyfile(receipt, payload / "recovery/.release-metadata")
            docker.web = web_base
            docker.images.add(docker.web)
            contract = backup.capture_web_assets(payload / "recovery", payload, docker.web)
            if contract is None:
                raise ValueError("exact_protocol_one_required")
            raw = (payload / "recovery/web-assets-manifest.json").read_bytes()
        else:
            files, raw, contract = fixtures.web_fixture(payload)
            context = scratch / "web-context"
            context.mkdir()
            for path, data in files.items():
                owned.private_write(context / "public" / path, data)
            owned.private_write(context / "manifest.json", raw)
            shutil.copyfile(ROOT / "apps/web/nginx.conf", context / "nginx.conf")
            # Dockerfile FROM raw image IDs can resolve as registry tags. Use
            # daemon create --pull=never by the exact ID, then owned cp/commit.
            docker.images.add(web_base)
            label = (owned.OWNER_LABEL, docker.owner)
            created = subprocess.run(["docker", "create", "--pull=never", "--network", "none",
                "--label", "=".join(label), "--user", "root", "--entrypoint", "/bin/sh", web_base,
                "-c", "rm -rf /usr/share/nginx/html && mkdir -p /usr/share/nginx/html /etc/erp-web"],
                capture_output=True, check=True, timeout=60)
            cid = created.stdout.decode().strip()
            if not re.fullmatch(r"[a-f0-9]{64}", cid):
                raise ValueError("container_id_not_captured")
            docker.records[cid] = (label, "none", ())
            docker.container_images[cid] = web_base
            docker.write_event("captured", cid)
            docker.guard(cid)
            subprocess.run(["docker", "start", "--attach", cid], capture_output=True, check=True, timeout=60)
            for source_path, destination in ((context / "public", "/usr/share/nginx/html"),
                    (context / "manifest.json", "/etc/erp-web/assets-manifest.json"),
                    (context / "nginx.conf", "/etc/nginx/conf.d/default.conf")):
                for child in [source_path, *source_path.rglob("*")] if source_path.is_dir() else [source_path]:
                    child.chmod(0o755 if child.is_dir() else 0o644)
                docker.guard(cid)
                subprocess.run(["docker", "cp", str(source_path) + ("/." if source_path.is_dir() else ""),
                    cid + ":" + destination], capture_output=True, check=True, timeout=60)
            docker.guard(cid)
            docker.commit_fixture(cid)
            docker.remove(cid)
            docker.images.remove(web_base)
            metadata = payload / "recovery/.release-metadata"
            metadata.write_text(metadata.read_text().replace(fixtures.WEB, docker.web))
            contract["web_image_id"] = docker.web
            contract["activation_receipt"]["web_image_id"] = docker.web
            assert backup.capture_web_assets(payload / "recovery", payload, docker.web) == contract
        asset_manifest = json.loads(raw)
        current = next(group for group in asset_manifest["sets"] if group["release_id"] == asset_manifest["release_id"])
        current_paths = {item["path"] for item in current["files"]}
        retained_paths = {item["path"] for group in asset_manifest["sets"] for item in group["files"]} - current_paths
        for paths in (current_paths, retained_paths):
            assert any(path.endswith(".js") for path in paths) and any(path.endswith(".css") for path in paths)
        expected_web = {"status": "verified", "current_files": len(asset_manifest["documents"]) + len(current_paths),
                        "retained_files": len(retained_paths), "manifest_sha256": hashlib.sha256(raw).hexdigest()}
        assert backup.verify_web_assets(payload, {"web_assets": contract, "runtime_images": {"web": docker.web}}) == expected_web
        config = {name: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
                  for name in ("field_encryption_master_key", "field_lookup_key", "session_token_pepper",
                      "registration_verification_pepper", "registration_session_token_pepper",
                      "evaluation_tool_service_token", "evaluation_tool_web_service_token", "evaluation_tool_worker_service_token")}
        for name, value in config.items():
            owned.private_write(payload / "recovery/secrets" / name, value)
            owned.private_write(payload / "recovery/mounted-api-secrets" / name, value)
        owned.private_write(payload / "recovery/runtime-config.json", json.dumps(config))
        owned.private_write(payload / "recovery/.env", "APP_ENV=test\n")
        with tarfile.open(payload / "recovery/source.tar.gz", "w:gz") as archive:
            archive.add(ROOT / "scripts/erp_backup_container.py", arcname="scripts/erp_backup_container.py")
        source = backup.run(["docker", "run", "--detach", "--name", "web-source-" + docker.owner,
            "--network", "none", "-e", "POSTGRES_HOST_AUTH_METHOD=trust", "-e", "POSTGRES_USER=erp_verify",
            "-e", "POSTGRES_DB=erp_verify", postgres]).decode().strip()
        for _ in range(60):
            try:
                backup.run(["docker", "exec", source, "pg_isready", "-h", "127.0.0.1", "-U", "erp_verify", "-d", "erp_verify"], timeout=5)
                break
            except backup.BackupError:
                time.sleep(1)
        else:
            raise AssertionError("isolated_source_unavailable")
        producer = helper.split('if __name__ == "__main__":')[0] + '''
config = json.load(sys.stdin)
with psycopg.connect("postgresql://erp_verify@127.0.0.1:5432/erp_verify") as db:
    db.execute("CREATE TABLE alembic_version(version_num varchar(32) NOT NULL PRIMARY KEY)")
    db.execute("INSERT INTO alembic_version VALUES ('web-isolated')")
    db.execute("CREATE TABLE web_proof(value text NOT NULL)")
    db.execute("INSERT INTO web_proof VALUES ('synthetic-only')")
    db.commit()
    manifest = database_manifest(db)
manifest.update(media=[], key_canary=cipher_for(config).encrypt('erp-backup-recovery-v1', field_name='backup'),
    lookup_canary=hashlib.sha256(('erp-backup-lookup-v1:' + config['field_lookup_key']).encode()).hexdigest())
print(json.dumps(manifest))
'''
        database = json.loads(backup.run(["docker", "run", "--rm", "--interactive", "--network", "container:" + source,
            "--entrypoint", "python", api, "-c", producer], input=json.dumps(config).encode()))
        with (payload / "database.dump").open("xb") as output:
            backup.run(["docker", "exec", source, "pg_dump", "-U", "erp_verify", "-d", "erp_verify", "--format=custom", "--no-owner"], stdout=output)
        docker.remove(source)
        with (payload / "recovery/images.tar").open("xb") as output:
            backup.run(["docker", "image", "save", *sorted(docker.images)], stdout=output)
        docker.saved_sha = backup.digest(payload / "recovery/images.tar")
        identity = "backup-" + dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(4)
        manifest = {"backup_id": identity, "created_at": backup.now(), "release": contract["release_id"], "database": database,
            "api_image": api, "postgres_image": postgres, "postgres_major": 16,
            "runtime_images": {"web": docker.web}, "web_assets": contract, "files": backup.file_inventory(payload)}
        backup.atomic_json(payload / "manifest.json", manifest)
        key_file = scratch / "key"
        owned.private_write(key_file, base64.urlsafe_b64encode(secrets.token_bytes(48)))
        candidate = scratch / "backups" / identity
        candidate.mkdir(parents=True, mode=0o700)
        key = backup.key_bytes(key_file, candidate.parent)
        plain = scratch / "archive.tar.gz"
        with tarfile.open(plain, "w:gz") as archive:
            for child in payload.iterdir():
                archive.add(child, arcname=child.name)
        encrypted = candidate / "archive.tar.gz.enc"
        backup.crypt(plain, encrypted, key_file)
        meta = {"format_version": 1, "backup_id": identity, "sha256": backup.digest(encrypted),
                "hmac_sha256": backup.digest(encrypted, key)}
        backup.atomic_json(candidate / "complete.json", backup.seal_metadata(meta, key))
        shutil.rmtree(payload)
        plain.unlink()
        if docker.exact_input:
            # Controller-approved narrow seam: ONLY the initial absence decision
            # is synthetic. Save/encryption/load/exact post-inspect are native.
            docker.force_missing_once = True
        else:
            docker.remove_image()
            absent = subprocess.run(["docker", "image", "inspect", docker.web], capture_output=True, timeout=30, check=False)
            assert absent.returncode != 0
        if docker.exact_input:
            subprocess.run(["docker", "image", "inspect", api, postgres], capture_output=True, check=True, timeout=30)
        else:
            backup.run(["docker", "image", "inspect", api, postgres])
        result = backup.verify_archive(candidate, scratch, SimpleNamespace(key_file=key_file), key)
        assert docker.loads == 1 and docker.probes == 1
        assert result["verified"] and result["table_count"] == 2
        assert result["web_assets_verification"] == expected_web
        # No ports, no external network: probe joins only our restored server.
        server = backup.run(["docker", "run", "--detach", "--network", "none", docker.web]).decode().strip()
        program = '''
import hashlib,json,time,urllib.request,urllib.error,sys
value=json.load(sys.stdin)
files=value['files']
for attempt in range(40):
    try:
        urllib.request.urlopen('http://127.0.0.1:5173/healthz',timeout=2).read();break
    except OSError:time.sleep(.25)
else:raise AssertionError('http_unavailable')
for path,sha in files.items():
    response=urllib.request.urlopen('http://127.0.0.1:5173/'+path,timeout=5)
    assert hashlib.sha256(response.read()).hexdigest()==sha
html=urllib.request.urlopen('http://127.0.0.1:5173/index.html').read()
assert any(path.encode() in html for path in value['current'])
assert all(path.encode() not in html for path in value['retained'])
for path in ('/etc/erp-web/assets-manifest.json','/assets-manifest.json','/.release-metadata','/assets/missing.js'):
    try:urllib.request.urlopen('http://127.0.0.1:5173'+path)
    except urllib.error.HTTPError as error:assert error.code==404
    else:raise AssertionError('private_or_missing_exposed')
print('HTTP_HASHES_CURRENT_HTML_PRIVATE_MANIFEST_PASS')
'''
        output = backup.run(["docker", "run", "--rm", "--interactive", "--network", "container:" + server,
            "--entrypoint", "python", api, "-c", program], input=json.dumps({
                "files": {item["path"]: item["sha256"] for item in [*asset_manifest["documents"],
                    *(item for group in asset_manifest["sets"] for item in group["files"])]},
                "current": sorted(current_paths), "retained": sorted(retained_paths)}).encode())
        assert output.strip() == b"HTTP_HASHES_CURRENT_HTML_PRIVATE_MANIFEST_PASS"
        docker.remove(server)
        if not docker.exact_input:
            # Exercise the unchanged-exact-input mode too, using this explicitly
            # synthetic image. This is NOT final-product/NAS activation evidence.
            with monkeypatch.context() as exact:
                exact.setenv("ERP_BACKUP_TEST_WEB_EXACT", "1")
                exact.setenv("ERP_BACKUP_TEST_WEB_IMAGE", docker.web)
                exact.setenv("ERP_BACKUP_TEST_WEB_RECEIPT_FILE", str(scratch / "restore/recovery/.release-metadata"))
                test_encrypted_web_assets_exact_restore(exact)
            docker.image_guard()
        summary = {"web_image": docker.web, "input_web_image": web_base, "api_image": api, "postgres_image": postgres,
            "encrypted_sha256": meta["sha256"], "images_tar_sha256": docker.saved_sha,
            "actual_loads": docker.loads, "database_verified": True, "web_assets_verification": result["web_assets_verification"],
            "mode": "exact_input_with_missing_decision_seam" if docker.exact_input else "owned_synthetic_physical_missing",
            "input_image_preserved": True,
            "http_current_old_hashes_current_only_html_private_manifest": True}
        passed = True
    finally:
        try:
            cleanup(docker, passed=passed, parent=parent)
        finally:
            os.umask(old_umask)
    summary.update(owned_containers_remaining=len(docker.records), owned_image_removed=docker.image_removed,
                   private_scratch_removed=not scratch.exists())
    print(json.dumps(summary, sort_keys=True))
