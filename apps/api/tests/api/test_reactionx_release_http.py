"""Actual session/RBAC checks around reviewed frozen release descriptors."""

from uuid import uuid4

import pytest
from sqlalchemy import delete
from starlette.requests import ClientDisconnect

from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS
from app.reactionx import router
from app.reactionx.releases import ReleaseService
from tests.api.conftest import _login_as
from tests.cli.test_reactionx_release import FILENAME, make_bundle

WEB = "/api/v1/reactionx/releases"


@pytest.fixture
def release_root(monkeypatch, tmp_path):
    def service(*, authorize):
        return ReleaseService(tmp_path, authorize=authorize)

    monkeypatch.setattr(router, "ReleaseService", service, raising=False)
    return tmp_path


def test_release_metadata_absent_is_unavailable_not_fabricated(
    db_client, login_as_admin, release_root
):
    response = db_client.get(WEB + "/current")
    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "release_id": None,
        "version": None,
        "source_commit": None,
        "protocol_version": None,
        "filename": None,
        "sha256": None,
        "size": None,
        "signature_status": None,
        "distribution_status": "unavailable",
    }
    assert response.headers["cache-control"] == "no-store, private"


def test_both_metadata_and_download_require_live_actual_superadmin(
    db_client, db, login_as_admin, release_root, platform_users
):
    payload, _ = make_bundle(release_root)
    current = db_client.get(WEB + "/current")
    assert current.status_code == 200
    download = WEB + f"/{current.json()['release_id']}/download"
    response = db_client.get(download)
    assert response.status_code == 200 and response.content == payload
    assert response.headers["content-disposition"] == f'attachment; filename="{FILENAME}"'
    assert response.headers["cache-control"] == "no-store, private"
    db.execute(delete(UserRole).where(UserRole.user_id == login_as_admin.account_id))
    db.commit()
    assert db_client.get(WEB + "/current").status_code == 403
    assert db_client.get(download).status_code == 403
    _login_as(db_client, platform_users, "coach")
    assert db_client.get(WEB + "/current").status_code == 403
    assert db_client.get(download).status_code == 403
    db_client.cookies.clear()
    assert db_client.get(WEB + "/current").status_code == 401
    assert db_client.get(download).status_code == 401


@pytest.mark.parametrize("fault", ["signed", "hash", "tamper", "symlink"])
def test_invalid_bundle_has_generic_errors_without_paths(
    db_client, login_as_admin, release_root, fault
):
    make_bundle(
        release_root,
        **(
            {"signature_status": "signed"}
            if fault == "signed"
            else {"sha256": "0" * 64}
            if fault == "hash"
            else {}
        ),
    )
    if fault == "tamper":
        (release_root / FILENAME).write_bytes(b"tampered")
    if fault == "symlink":
        (release_root / FILENAME).unlink()
        (release_root / FILENAME).symlink_to(release_root.parent / "outside.exe")
    current = db_client.get(WEB + "/current")
    assert current.status_code == 503
    assert current.json()["code"] == "reactionx_release_unavailable"
    download = db_client.get(WEB + "/" + "0" * 64 + "/download")
    assert download.status_code == 404
    assert str(release_root) not in current.text + download.text


def test_stale_identity_cannot_select_files_and_stream_descriptor_closes(
    db_client, login_as_admin, release_root, monkeypatch
):
    make_bundle(release_root)
    opened = []
    original = ReleaseService.open_verified

    def observed(self, identity):
        verified = original(self, identity)
        opened.append(verified)
        return verified

    monkeypatch.setattr(ReleaseService, "open_verified", observed)
    current = db_client.get(WEB + "/current").json()
    assert db_client.get(WEB + f"/{current['release_id']}/download").status_code == 200
    assert len(opened) == 1 and opened[0].file.closed
    assert db_client.get(WEB + "/" + "f" * 64 + "/download").status_code == 404
    assert db_client.get(WEB + f"/{uuid4()}/download").status_code == 404


def test_machine_credentials_cannot_be_used_as_browser_download_authority(db_client, release_root):
    response = db_client.get(WEB + "/current", headers={"Authorization": "ReactionX synthetic"})
    assert response.status_code == 401


def test_explicit_manage_deny_blocks_already_discovered_download(
    db_client, db, login_as_admin, release_root
):
    make_bundle(release_root)
    current = db_client.get(WEB + "/current")
    assert current.status_code == 200
    download = WEB + f"/{current.json()['release_id']}/download"
    db.add(
        UserPermissionOverride(
            user_id=login_as_admin.account_id,
            permission_id=PERMISSIONS["reactionx.manage"].id,
            scope_kind="global",
            scope_id=None,
            effect="deny",
        )
    )
    db.commit()
    assert db_client.get(WEB + "/current").status_code == 403
    assert db_client.get(download).status_code == 403


def test_download_cleanup_survives_send_failure_before_first_chunk(tmp_path):
    import asyncio

    make_bundle(tmp_path)
    service = ReleaseService(tmp_path, authorize=lambda: None)
    verified = service.open_verified(service.current().release_id)
    response = router.VerifiedDownloadResponse(verified)

    async def receive():
        return {"type": "http.disconnect"}

    async def broken_send(message):
        raise OSError("synthetic disconnected transport")

    with pytest.raises(ClientDisconnect):
        asyncio.run(
            response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, broken_send)
        )
    assert verified.file.closed
