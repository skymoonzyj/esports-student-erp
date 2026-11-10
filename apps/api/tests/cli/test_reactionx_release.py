"""Pure release-boundary tests: synthetic PE bytes, never a deliverable or DB."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from app.reactionx.release_bundle import BundleError, freeze_bundle
from app.reactionx.releases import ReleaseService

ROOT = Path(__file__).resolve().parents[4]
FILENAME = "ReactionXConnectorSetup.exe"


def pe_bytes(*, certificate: bool = False) -> bytes:
    value = bytearray(512)
    value[:2] = b"MZ"
    struct.pack_into("<I", value, 60, 128)
    value[128:132] = b"PE\0\0"
    struct.pack_into("<HH", value, 132, 0x8664, 1)
    struct.pack_into("<H", value, 148, 240)
    struct.pack_into("<H", value, 152, 0x20B)
    struct.pack_into("<I", value, 260, 16)
    if certificate:
        struct.pack_into("<II", value, 296, 480, 32)
    return bytes(value)


def make_bundle(path: Path, **changes: object) -> tuple[bytes, dict[str, object]]:
    path.mkdir(exist_ok=True)
    payload = pe_bytes()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "protocol_version": 1,
        "version": "0.3.0",
        "source_commit": "a" * 40,
        "filename": FILENAME,
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
        "signature_status": "unsigned",
    }
    manifest.update(changes)
    (path / FILENAME).write_bytes(payload)
    (path / "manifest.json").write_text(json.dumps(manifest))
    return payload, manifest


def allow() -> None:
    pass


def test_absent_or_empty_bundle_is_honestly_unavailable(tmp_path: Path) -> None:
    for root in (tmp_path / "absent", tmp_path):
        view = ReleaseService(root, authorize=allow).current()
        assert view.available is False
        assert view.release_id is None


def test_verified_metadata_and_download_are_internal_only(tmp_path: Path) -> None:
    payload, manifest = make_bundle(tmp_path)
    service = ReleaseService(tmp_path, authorize=allow)
    view = service.current()
    assert view.available is True
    assert view.distribution_status == "internal_test"
    assert view.signature_status == "unsigned"
    assert view.sha256 == manifest["sha256"]
    assert view.filename == FILENAME
    assert view.protocol_version == 1
    with service.open_verified(view.release_id or "") as verified:
        assert b"".join(verified.chunks()) == payload
        assert verified.view == view
    assert verified.file.closed


def test_every_lookup_rechecks_authority_and_revocation(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    permitted = True

    def authorize() -> None:
        if not permitted:
            raise PermissionError("revoked")

    service = ReleaseService(tmp_path, authorize=authorize)
    view = service.current()
    permitted = False
    with pytest.raises(PermissionError):
        service.current()
    with pytest.raises(PermissionError):
        service.open_verified(view.release_id or "")


def test_download_is_pinned_against_replacement_and_in_place_mutation(tmp_path: Path) -> None:
    payload, _ = make_bundle(tmp_path)
    service = ReleaseService(tmp_path, authorize=allow)
    with service.open_verified(service.current().release_id or "") as verified:
        (tmp_path / FILENAME).write_bytes(b"mutated in place")
        (tmp_path / FILENAME).unlink()
        (tmp_path / FILENAME).write_bytes(b"replacement")
        assert b"".join(verified.chunks()) == payload


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"protocol_version": "1"},
        {"protocol_version": 2},
        {"version": "01.2.3"},
        {"version": "0.3.0-beta"},
        {"source_commit": "main"},
        {"filename": "../ReactionXConnectorSetup.exe"},
        {"filename": "/tmp/x.exe"},
        {"sha256": "0" * 64},
        {"size": True},
        {"size": 512 * 1024 * 1024 + 1},
        {"size": 511},
        {"signature_status": "signed"},
        {"unknown": "ignored?"},
    ],
)
def test_invalid_or_false_manifest_claim_fails(tmp_path: Path, changes: dict[str, object]) -> None:
    make_bundle(tmp_path, **changes)
    with pytest.raises(BundleError):
        ReleaseService(tmp_path, authorize=allow).current()


@pytest.mark.parametrize(
    "fault",
    [
        "tamper",
        "extra",
        "missing",
        "duplicate",
        "oversize",
        "symlink_file",
        "symlink_manifest",
        "symlink_parent",
        "not_pe",
        "embedded_certificate",
    ],
)
def test_untrusted_files_fail_closed(tmp_path: Path, fault: str) -> None:
    root = tmp_path / "bundle"
    payload, manifest = make_bundle(root)
    if fault == "tamper":
        (root / FILENAME).write_bytes(payload + b"x")
    elif fault == "extra":
        (root / "other.exe").write_bytes(b"x")
    elif fault == "missing":
        (root / FILENAME).unlink()
    elif fault == "duplicate":
        raw = json.dumps(manifest)[:-1] + ',"version":"0.3.0"}'
        (root / "manifest.json").write_text(raw)
    elif fault == "oversize":
        (root / "manifest.json").write_bytes(b" " * (16 * 1024 + 1))
    elif fault.startswith("symlink_"):
        name = "manifest.json" if fault == "symlink_manifest" else FILENAME
        if fault == "symlink_parent":
            link = tmp_path / "link"
            link.symlink_to(root, target_is_directory=True)
            root = link
        else:
            outside = tmp_path / "outside"
            (root / name).rename(outside)
            (root / name).symlink_to(outside)
    else:
        data = b"not a PE" if fault == "not_pe" else pe_bytes(certificate=True)
        (root / FILENAME).write_bytes(data)
        manifest.update(sha256=hashlib.sha256(data).hexdigest(), size=len(data))
        (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(BundleError):
        ReleaseService(root, authorize=allow).current()


def test_release_id_cannot_select_a_path_or_stale_version(tmp_path: Path) -> None:
    make_bundle(tmp_path)
    service = ReleaseService(tmp_path, authorize=allow)
    for release_id in ("../x", "/tmp/x", "0" * 64):
        with pytest.raises(BundleError):
            service.open_verified(release_id)


def test_manifest_requires_portable_utf8(tmp_path: Path) -> None:
    _, manifest = make_bundle(tmp_path)
    (tmp_path / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-16"))
    with pytest.raises(BundleError):
        ReleaseService(tmp_path, authorize=allow).current()


def test_freeze_is_allowlisted_verified_and_rejects_nonempty_destination(tmp_path: Path) -> None:
    payload, _ = make_bundle(tmp_path / "input")
    frozen = tmp_path / "frozen"
    digest = freeze_bundle(tmp_path / "input", frozen)
    assert len(digest) == 64
    (tmp_path / "input" / FILENAME).write_bytes(b"changed")
    assert (frozen / FILENAME).read_bytes() == payload
    assert set(os.listdir(frozen)) == {"manifest.json", FILENAME}
    with pytest.raises(BundleError):
        freeze_bundle(tmp_path / "input", frozen)


def test_cli_requires_explicit_package_and_checks_expected_digest(tmp_path: Path) -> None:
    command = [sys.executable, str(ROOT / "scripts/reactionx-release-bundle.py")]
    missing = subprocess.run(
        command + ["verify", str(tmp_path / "missing")], capture_output=True, check=False
    )
    assert missing.returncode != 0
    make_bundle(tmp_path / "input")
    frozen = tmp_path / "frozen"
    result = subprocess.run(
        command + ["freeze", str(tmp_path / "input"), str(frozen)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    valid = subprocess.run(
        command + ["verify", str(frozen), result.stdout.strip()],
        capture_output=True,
        text=True,
        check=False,
    )
    assert valid.returncode == 0, valid.stderr
    wrong = subprocess.run(
        command + ["verify", str(frozen), "0" * 64], capture_output=True, check=False
    )
    assert wrong.returncode != 0


def test_guard_release_mount_enforces_candidate_read_only_api_only(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location(
        "nas_release_guard", ROOT / "scripts/nas-deploy-guard.py"
    )
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    target = "/app/runtime/reactionx-release"
    mount = {
        "type": "bind",
        "source": str(tmp_path / "runtime/reactionx-release"),
        "target": target,
        "read_only": True,
        "bind": {"create_host_path": False},
    }
    cfg = {
        "services": {
            "api": {"volumes": [mount], "environment": {"REACTIONX_RELEASE_ROOT": target}},
            "scheduler": {"volumes": []},
        }
    }
    guard.validate_reactionx_mount(cfg, tmp_path)
    for key, bad in (("read_only", False), ("source", "/tmp/previous"), ("type", "volume")):
        original = mount[key]
        mount[key] = bad
        with pytest.raises(SystemExit):
            guard.validate_reactionx_mount(cfg, tmp_path)
        mount[key] = original
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "runtime").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SystemExit):
        guard.validate_reactionx_mount(cfg, tmp_path)
    (tmp_path / "runtime").unlink()
    cfg["services"]["scheduler"]["volumes"] = [mount]
    with pytest.raises(SystemExit):
        guard.validate_reactionx_mount(cfg, tmp_path)


def normalized_release_config(tmp_path: Path) -> dict:
    # Literal Compose 2.40.3 config --format json shape: explicit YAML false
    # and an empty YAML bind mapping both normalize to "bind": {}.
    cfg = json.loads("""{
        "services": {
            "api": {
                "environment": {"REACTIONX_RELEASE_ROOT": "/app/runtime/reactionx-release"},
                "volumes": [{
                    "type": "bind",
                    "source": "/candidate/runtime/reactionx-release",
                    "target": "/app/runtime/reactionx-release",
                    "read_only": true,
                    "bind": {}
                }]
            },
            "scheduler": {"volumes": []}
        }
    }""")
    cfg["services"]["api"]["volumes"][0]["source"] = str(tmp_path / "runtime/reactionx-release")
    return cfg


def normalized_release_guard():
    spec = importlib.util.spec_from_file_location(
        "nas_normalized_release_guard", ROOT / "scripts/nas-deploy-guard.py"
    )
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    return guard


def test_guard_accepts_compose_2403_normalized_false_bind(tmp_path: Path) -> None:
    normalized_release_guard().validate_reactionx_mount(normalized_release_config(tmp_path), tmp_path)


@pytest.mark.parametrize("value", [True, None, 0, 1, "false", "true", "", [], {}])
def test_guard_rejects_normalized_bind_create_host_path_unless_strict_false(
    tmp_path: Path, value: object
) -> None:
    cfg = normalized_release_config(tmp_path)
    cfg["services"]["api"]["volumes"][0]["bind"] = {"create_host_path": value}
    with pytest.raises(SystemExit, match="read-only existing host bind"):
        normalized_release_guard().validate_reactionx_mount(cfg, tmp_path)


@pytest.mark.parametrize("value", [None, False, True, "", "false", [], [{}], 0])
def test_guard_rejects_malformed_normalized_bind_mapping(tmp_path: Path, value: object) -> None:
    cfg = normalized_release_config(tmp_path)
    cfg["services"]["api"]["volumes"][0]["bind"] = value
    with pytest.raises(SystemExit, match="read-only existing host bind"):
        normalized_release_guard().validate_reactionx_mount(cfg, tmp_path)


def test_guard_rejects_missing_normalized_bind_mapping(tmp_path: Path) -> None:
    cfg = normalized_release_config(tmp_path)
    del cfg["services"]["api"]["volumes"][0]["bind"]
    with pytest.raises(SystemExit, match="read-only existing host bind"):
        normalized_release_guard().validate_reactionx_mount(cfg, tmp_path)


@pytest.mark.parametrize(
    ("key", "value"),
    [("read_only", False), ("read_only", "true"), ("type", "volume"), ("source", "/tmp/previous")],
)
def test_normalized_false_bind_preserves_other_mount_guards(
    tmp_path: Path, key: str, value: object
) -> None:
    cfg = normalized_release_config(tmp_path)
    cfg["services"]["api"]["volumes"][0][key] = value
    with pytest.raises(SystemExit):
        normalized_release_guard().validate_reactionx_mount(cfg, tmp_path)


def test_normalized_false_bind_remains_api_only_and_rejects_source_symlink_escape(
    tmp_path: Path,
) -> None:
    guard = normalized_release_guard()
    cfg = normalized_release_config(tmp_path)
    mount = cfg["services"]["api"]["volumes"][0]
    cfg["services"]["scheduler"]["volumes"] = [mount]
    with pytest.raises(SystemExit, match="API-only"):
        guard.validate_reactionx_mount(cfg, tmp_path)
    cfg["services"]["scheduler"]["volumes"] = []
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (tmp_path / "runtime").symlink_to(outside, target_is_directory=True)
    with pytest.raises(SystemExit, match="belong to the candidate"):
        guard.validate_reactionx_mount(cfg, tmp_path)
