"""Portable Web restore contracts; Docker boundary doubles never pull images."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[4]
spec = importlib.util.spec_from_file_location("web_backup", ROOT / "scripts/erp-backup.py")
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)
API, DB, WEB = ("sha256:" + c * 64 for c in "abc")
LEGACY = {"status": "legacy_unavailable", "current_files": None,
          "retained_files": None, "manifest_sha256": None}


def archive_fixture(tmp_path, monkeypatch, *, contract=False):
    payload = tmp_path / "fixture"
    (payload / "recovery").mkdir(parents=True)
    (payload / "recovery/images.tar").write_bytes(b"authenticated saved images")
    manifest = {"backup_id": "backup-20261019T000000Z-12345678", "api_image": API,
                "postgres_image": DB, "runtime_images": {"web": WEB}}
    if contract:
        (payload / "recovery/.release-metadata").write_text(
            "web_assets_contract=1\nweb_assets_status=verified\n")
    manifest["files"] = backup.file_inventory(payload)
    (payload / "manifest.json").write_text(json.dumps(manifest))
    plain = tmp_path / "input.tar.gz"
    with tarfile.open(plain, "w:gz") as target:
        for child in payload.iterdir():
            target.add(child, arcname=child.name)
    monkeypatch.setattr(backup, "validate_encrypted", lambda *_: {"backup_id": manifest["backup_id"]})
    monkeypatch.setattr(backup, "crypt", lambda _, dest, *args, **kw: dest.write_bytes(plain.read_bytes()))
    monkeypatch.setattr(backup, "verify_payload", lambda *_: {"verified": True})
    workspace = tmp_path / "work"
    workspace.mkdir()
    return payload, plain, workspace, manifest


def docker_boundary(monkeypatch, *, missing=False, label=None):
    events = []
    loaded = False

    def run(command, **kwargs):
        nonlocal loaded
        events.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            if missing and WEB in command and not loaded:
                raise backup.BackupError("docker_command_failed")
            return json.dumps([{"Id": image, "Config": {"Labels": {
                "cn.jingxue.erp.web-assets-contract": label} if label else {}}}
                               for image in command[3:]]).encode()
        if command[:3] == ["docker", "image", "load"]:
            loaded = True
            return b"loaded"
        raise AssertionError("unexpected Docker operation")

    monkeypatch.setattr(backup, "run", run)
    return events


def test_missing_web_image_requires_archive_load_even_when_api_exists(tmp_path, monkeypatch):
    _, _, workspace, _ = archive_fixture(tmp_path, monkeypatch)
    events = docker_boundary(monkeypatch, missing=True)
    backup.verify_archive(tmp_path, workspace, SimpleNamespace(key_file=tmp_path / "key"), b"key")
    loads = [c for c in events if c[:3] == ["docker", "image", "load"]]
    assert len(loads) == 1
    assert events[-1][:3] == ["docker", "image", "inspect"] and WEB in events[-1]
    assert not any("pull" in c for c in events)


def test_new_contract_cannot_fall_back_to_legacy(tmp_path, monkeypatch):
    _, _, workspace, _ = archive_fixture(tmp_path, monkeypatch, contract=True)
    docker_boundary(monkeypatch)
    with pytest.raises(backup.BackupError, match="web_assets"):
        backup.verify_archive(tmp_path, workspace, SimpleNamespace(key_file=tmp_path / "key"), b"key")


def test_image_protocol_cannot_fall_back_to_legacy(tmp_path, monkeypatch):
    _, _, workspace, _ = archive_fixture(tmp_path, monkeypatch)
    docker_boundary(monkeypatch, label="1")
    with pytest.raises(backup.BackupError, match="web_assets"):
        backup.verify_archive(tmp_path, workspace, SimpleNamespace(key_file=tmp_path / "key"), b"key")


def test_legacy_backup_not_claimed_verified(tmp_path, monkeypatch):
    _, _, workspace, _ = archive_fixture(tmp_path, monkeypatch)
    docker_boundary(monkeypatch)
    result = backup.verify_archive(tmp_path, workspace, SimpleNamespace(key_file=tmp_path / "key"), b"key")
    assert result["web_assets_verification"] == LEGACY


def test_damaged_image_inventory_rejected_before_load(tmp_path, monkeypatch):
    payload, plain, workspace, _ = archive_fixture(tmp_path, monkeypatch)
    (payload / "recovery/images.tar").write_bytes(b"tampered")
    with tarfile.open(plain, "w:gz") as target:
        for child in payload.iterdir():
            target.add(child, arcname=child.name)
    events = docker_boundary(monkeypatch, missing=True)
    with pytest.raises(backup.BackupError, match="checksum"):
        backup.verify_archive(tmp_path, workspace, SimpleNamespace(key_file=tmp_path / "key"), b"key")
    assert not events


def web_fixture(payload):
    files = {"index.html": b'<script src="/assets/new.js"></script>',
             "build-info.json": b'{"build_id":"r2","release_id":"r2"}',
             "assets/new.js": b"new-js", "assets/old.js": b"old-js",
             "assets/new.css": b"new-css", "assets/old.css": b"old-css"}
    def entry(path):
        value = files[path]
        return {"path": path, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
    manifest = {"schema_version": 1, "build_id": "r2", "release_id": "r2",
                "predecessor_image_id": API, "input_sha256": "d" * 64,
                "total_bytes": sum(map(len, files.values())),
                "documents": [entry("index.html"), entry("build-info.json")],
                "sets": [{"release_id": "r2", "build_id": "r2", "superseded_by_release_id": None,
                          "files": [entry("assets/new.js"), entry("assets/new.css")]},
                         {"release_id": "r1", "build_id": "r1", "superseded_by_release_id": "r2",
                          "files": [entry("assets/old.js"), entry("assets/old.css")]}]}
    raw = json.dumps(manifest).encode()
    recovery = payload / "recovery"
    recovery.mkdir(exist_ok=True)
    (recovery / "web-assets-manifest.json").write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    metadata = {"source_release_id": "r2", "web_assets_contract": "1",
                "web_assets_status": "verified", "source_tree_sha256": "e" * 64,
                "web_image_id": WEB, "web_assets_manifest_sha256": sha,
                "activated_verified_utc": "2026-10-18T00:00:00+00:00",
                "web_assets_input_sha256": "d" * 64, "web_predecessor_image_id": API}
    (recovery / ".release-metadata").write_text("".join(f"{k}={v}\n" for k, v in metadata.items()))
    contract = {"protocol": 1, "manifest_sha256": sha, "build_id": "r2", "release_id": "r2",
                "web_image_id": WEB, "input_sha256": "d" * 64, "predecessor_image_id": API,
                "activation_receipt": {"schema_version": 1, "release_id": "r2",
                    "source_tree_sha256": "e" * 64, "web_image_id": WEB,
                    "web_assets_manifest_sha256": sha, "activated_verified_utc": metadata["activated_verified_utc"]}}
    return files, raw, contract


def tree_boundary(monkeypatch, files, raw, *, tamper=False):
    cid, owner = "f" * 64, {}
    events = []
    def run(command, **kwargs):
        events.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            return json.dumps([{"Id": WEB, "Config": {"Labels": {
                "cn.jingxue.erp.web-assets-contract": "1"}}}]).encode()
        if command[1] == "create":
            owner["label"] = command[command.index("--label") + 1].split("=", 1)
            assert "--pull=never" in command and command[-1] == WEB
            return cid.encode()
        if command[1] == "inspect":
            return json.dumps([{"Id": cid, "Image": WEB,
                "Config": {"Labels": dict([owner["label"]])}, "Mounts": [],
                "HostConfig": {"NetworkMode": "none", "PortBindings": None, "Binds": None}}]).encode()
        if command[1] == "cp":
            content = {"assets-manifest.json": raw} if "/etc/" in command[2] else dict(files)
            if tamper and "/etc/" not in command[2]:
                content["assets/old.js"] = b"tampered"
            with tarfile.open(fileobj=kwargs["stdout"], mode="w", format=tarfile.USTAR_FORMAT) as tar:
                for path, data in content.items():
                    member = tarfile.TarInfo(path)
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
            return b""
        if command[1] == "rm":
            assert command[-1] == cid
            return b""
        raise AssertionError(command)
    monkeypatch.setattr(backup, "run", run)
    def popen(command, **kwargs):
        stream = io.BytesIO()
        run(command, stdout=stream)
        stream.seek(0)
        return SimpleNamespace(stdout=stream, wait=lambda **_: 0, poll=lambda: 0, kill=lambda: None)
    monkeypatch.setattr(backup, "subprocess", SimpleNamespace(
        Popen=popen, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired))
    return events


def test_current_and_retained_tree_must_both_verify(tmp_path, monkeypatch):
    files, raw, contract = web_fixture(tmp_path)
    events = tree_boundary(monkeypatch, files, raw)
    result = backup.verify_web_assets(tmp_path, {"web_assets": contract, "runtime_images": {"web": WEB}})
    assert result == {"status": "verified", "current_files": 4, "retained_files": 2,
                      "manifest_sha256": hashlib.sha256(raw).hexdigest()}
    assert events[-1] == ["docker", "rm", "f" * 64]


@pytest.mark.parametrize("mutation", ["hash", "boolean", "unknown", "receipt", "receipt_boolean", "input", "predecessor", "image", "missing", "status", "tree"])
def test_protocol_one_damage_never_verified(tmp_path, monkeypatch, mutation):
    files, raw, contract = web_fixture(tmp_path)
    tree_boundary(monkeypatch, files, raw, tamper=mutation == "tree")
    if mutation == "hash":
        contract["manifest_sha256"] = "0" * 64
    elif mutation == "boolean":
        contract["protocol"] = True
    elif mutation == "unknown":
        contract["protocol"] = 2
    elif mutation == "receipt":
        contract["activation_receipt"]["source_tree_sha256"] = "0" * 64
    elif mutation == "receipt_boolean":
        contract["activation_receipt"]["schema_version"] = True
    elif mutation in ("input", "predecessor", "image"):
        contract[{"input": "input_sha256", "predecessor": "predecessor_image_id", "image": "web_image_id"}[mutation]] = "0" * 64
    elif mutation == "missing":
        del contract["build_id"]
    elif mutation == "status":
        path = tmp_path / "recovery/.release-metadata"
        path.write_text(path.read_text().replace("status=verified", "status=admitted"))
    with pytest.raises(backup.BackupError, match="web_assets"):
        backup.verify_web_assets(tmp_path, {"web_assets": contract, "runtime_images": {"web": WEB}})


def test_backup_captures_private_manifest_and_verified_receipt(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    files, raw, contract = web_fixture(payload)
    release = payload / "recovery"
    tree_boundary(monkeypatch, files, raw)
    assert backup.capture_web_assets(release, payload, WEB) == contract


def test_source_protocol_cannot_fall_back_to_legacy(tmp_path, monkeypatch):
    (tmp_path / "recovery").mkdir()
    with tarfile.open(tmp_path / "recovery/source.tar.gz", "w:gz") as archive:
        value = b'{"schema_version":1,"required_protocol":1}'
        info = tarfile.TarInfo("scripts/web-assets-contract.json")
        info.size = len(value)
        archive.addfile(info, io.BytesIO(value))
    docker_boundary(monkeypatch)
    with pytest.raises(backup.BackupError, match="web_assets"):
        backup.verify_web_assets(tmp_path, {"runtime_images": {"web": WEB}})


def test_shared_path_counts_once_and_remains_hash_verified(tmp_path, monkeypatch):
    files, raw, _ = web_fixture(tmp_path)
    manifest = json.loads(raw)
    manifest["sets"][1]["files"].append(dict(manifest["sets"][0]["files"][0]))
    raw = json.dumps(manifest).encode()
    (tmp_path / "recovery/web-assets-manifest.json").write_bytes(raw)
    path = tmp_path / "recovery/.release-metadata"
    metadata = backup.web_metadata(path)
    metadata["web_assets_manifest_sha256"] = hashlib.sha256(raw).hexdigest()
    path.write_text("".join(f"{key}={value}\n" for key, value in metadata.items()))
    tree_boundary(monkeypatch, files, raw)
    contract = backup.web_contract(raw, metadata, WEB)
    result = backup.verify_web_assets(tmp_path, {"web_assets": contract, "runtime_images": {"web": WEB}})
    assert result["current_files"] == 4 and result["retained_files"] == 2


def test_null_legacy_image_labels_are_accepted(tmp_path, monkeypatch):
    (tmp_path / "recovery").mkdir()
    monkeypatch.setattr(backup, "run", lambda *_args, **_kw: json.dumps(
        [{"Id": WEB, "Config": {"Labels": None}}]).encode())
    assert backup.verify_web_assets(tmp_path, {"runtime_images": {"web": WEB}}) == LEGACY


def test_rejected_web_copy_stops_child_without_reading_oversized_payload(monkeypatch):
    assert hasattr(backup, "web_cp"), "bounded streaming Docker cp required"
    killed = []
    class HeaderOnly(io.BytesIO):
        def read(self, count=-1):
            assert self.tell() + count <= 512, "must not read the rejected payload"
            return super().read(count)
    header = tarfile.TarInfo("assets/bad.js")
    header.type = tarfile.SYMTYPE
    header.size = 536870913
    stream = HeaderOnly(header.tobuf())
    child = SimpleNamespace(stdout=stream, poll=lambda: None,
                            kill=lambda: killed.append(True), wait=lambda **_: 0)
    monkeypatch.setattr(backup, "subprocess", SimpleNamespace(
        Popen=lambda *_args, **_kw: child, PIPE=subprocess.PIPE, DEVNULL=subprocess.DEVNULL,
        TimeoutExpired=subprocess.TimeoutExpired))
    with pytest.raises(ValueError, match="ARCHIVE"), backup.web_cp("f" * 64, "/public", lambda: None) as source:
        backup.web_assets._archive_header(backup.web_assets._read_exact(source, 512))
    assert killed == [True]
