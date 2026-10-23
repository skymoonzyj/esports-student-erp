"""Test-only care recovery proof. Importing this file never opens a database."""

import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

CARE_REVISION = "0071_housing_room_care"
CARE_DSN = "postgresql+psycopg://erp_verify@127.0.0.1:5432/erp_verify"


@contextmanager
def care_http_client(app):
    """Exercise real HTTP without production-excluded httpx/TestClient packages."""
    import http.cookiejar
    import socket
    import threading
    import time
    import urllib.error
    import urllib.request
    from types import SimpleNamespace
    from uuid import uuid4

    import uvicorn

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app, host="127.0.0.1", port=port, access_log=False, log_level="critical", lifespan="off"
        )
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )

    def send(method, path, *, headers=None, json=None, data=None, files=None):
        request_headers = dict(headers or {})
        body = None
        if files is not None:
            boundary = "housing-care-" + uuid4().hex
            pieces = []
            for name, value in (data or {}).items():
                pieces.append(
                    (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                    ).encode()
                )
            for name, (filename, content, mime_type) in files.items():
                pieces.append(
                    (
                        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\nContent-Type: {mime_type}\r\n\r\n'
                    ).encode()
                    + content
                    + b"\r\n"
                )
            body = b"".join(pieces) + f"--{boundary}--\r\n".encode()
            request_headers["Content-Type"] = "multipart/form-data; boundary=" + boundary
        elif json is not None:
            body = globals()["json"].dumps(json).encode()
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}" + path, data=body, headers=request_headers, method=method
        )
        try:
            response = opener.open(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw, status = response.read(), response.status
        return SimpleNamespace(status_code=status, json=lambda: globals()["json"].loads(raw))

    client = SimpleNamespace(
        **{
            method.lower(): (lambda path, _method=method, **kwargs: send(_method, path, **kwargs))
            for method in ("GET", "POST", "PUT", "PATCH", "DELETE")
        }
    )
    thread.start()
    try:
        for _ in range(100):
            if not thread.is_alive():
                raise AssertionError("owned_http_server_exited")
            if server.started and client.get("/api/v1/health/live").status_code == 200:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("owned_http_server_not_ready")
        yield client
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        if thread.is_alive():
            raise AssertionError("owned_http_server_shutdown_failed")


def care_sha256(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def care_fingerprint(value):
    return care_sha256(json.dumps(value, sort_keys=True, default=str))


def require_local_docker_endpoint(endpoint):
    # No TCP/SSH, including loopback TCP proxies. This is the task's local engine.
    expected = "unix:///Users/skymoonzyj/.colima/default/docker.sock"
    if endpoint != expected:
        raise ValueError("local_docker_endpoint_required")
    return endpoint


def require_local_docker_environment(host, context, inspected_endpoint):
    # Docker's explicit context overrides DOCKER_HOST. Validate the engine the
    # CLI will actually use, then the controller pins that exact local endpoint.
    selected = inspected_endpoint if context or not host else host
    return require_local_docker_endpoint(selected)


def verify_care_source(expected, root=Path("/app")):
    if not expected:
        raise ValueError("api_image_source_mismatch")
    for name, digest in expected.items():
        path = root / name
        if (
            Path(name).is_absolute()
            or ".." in Path(name).parts
            or path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(root.resolve())
            or care_sha256(path.read_bytes()) != digest
        ):
            raise ValueError("api_image_source_mismatch")


def care_proof_coherent(case):
    def digest(value):
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None

    if not all(
        digest(case.get(key))
        for key in (
            "people_sha256",
            "accounts_sha256",
            "stays_sha256",
            "transfers_sha256",
        )
    ):
        return False
    rooms, photos, audits = (case.get(name) or [] for name in ("rooms", "photos", "audits"))
    if not rooms or not photos or not audits:
        return False
    room_ids = {room.get("id") for room in rooms}
    if None in room_ids or len(room_ids) != len(rooms):
        return False
    for room in rooms:
        if (
            type(room.get("version")) is not int
            or room["version"] < 4
            or room.get("daily_cleaning_enabled") is not True
            or room.get("cleaning_status") != "ready"
            or not room.get("cleaning_pending_at")
            or not room.get("cleaning_completed_at")
            or not room.get("cleaning_completed_by_user_id")
            or not digest(room.get("row_sha256"))
        ):
            return False
        if not any(
            audit.get("resource_id") == room["id"]
            and audit.get("actor_user_id") == room["cleaning_completed_by_user_id"]
            and audit.get("action") == "housing.room.cleaning.complete"
            and audit.get("outcome") == "allowed"
            for audit in audits
        ):
            return False
    slots = set()
    for photo in photos:
        slot = (photo.get("room_id"), photo.get("slot"))
        if (
            photo.get("room_id") not in room_ids
            or slot in slots
            or photo.get("slot") not in (1, 2, 3)
            or not photo.get("id")
            or not digest(photo.get("original_sha256"))
            or photo["original_sha256"] != photo.get("stored_sha256")
            or not digest(photo.get("thumbnail_sha256"))
            or not digest(photo.get("row_sha256"))
            or not isinstance(photo.get("byte_size"), int)
            or photo["byte_size"] < 1
            or not isinstance(photo.get("thumbnail_byte_size"), int)
            or photo["thumbnail_byte_size"] < 1
        ):
            return False
        slots.add(slot)
        if not any(
            audit.get("resource_id") == photo["id"]
            and audit.get("metadata", {}).get("room_id") == photo["room_id"]
            and audit.get("action") == "housing.room_photo.put"
            and audit.get("outcome") == "allowed"
            for audit in audits
        ):
            return False
    return all(digest(audit.get("row_sha256")) for audit in audits)


def validate_care_case(observed, expected):
    checks = {
        "complete_coherent_seed_and_restore": care_proof_coherent(expected)
        and care_proof_coherent(observed),
        "original_photo_bytes_preserved": observed.get("photos") == expected.get("photos"),
        "thumbnail_bytes_preserved": observed.get("photos") == expected.get("photos"),
        "care_state_version_and_events_preserved": observed.get("rooms") == expected.get("rooms"),
        "care_photo_audits_preserved": observed.get("audits") == expected.get("audits"),
        **{
            name + "_preserved": observed.get(name) == expected.get(name)
            for name in (
                "people_sha256",
                "accounts_sha256",
                "stays_sha256",
                "transfers_sha256",
            )
        },
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise AssertionError("housing_care_verification_failed:" + ",".join(failed))
    return checks


def collect_care_case(engine, room_ids):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        result = {}
        for key, table in (
            ("people", "people"),
            ("accounts", "user_accounts"),
            ("stays", "housing_stays"),
            ("transfers", "housing_transfers"),
        ):
            rows = (
                db.execute(text(f"SELECT to_jsonb(t) FROM {table} t ORDER BY id")).scalars().all()
            )
            result[key + "_sha256"] = care_fingerprint(rows)
        rooms, photos = [], []
        for room_id in room_ids:
            row = db.execute(
                text("SELECT to_jsonb(t) FROM housing_rooms t WHERE id=:id"), {"id": UUID(room_id)}
            ).scalar_one()
            rooms.append(
                {
                    **{
                        key: row[key]
                        for key in (
                            "id",
                            "version",
                            "daily_cleaning_enabled",
                            "cleaning_status",
                            "cleaning_pending_at",
                            "cleaning_completed_at",
                            "cleaning_completed_by_user_id",
                        )
                    },
                    "row_sha256": care_fingerprint(row),
                }
            )
            raw_photos = db.execute(
                text(
                    "SELECT id,slot,content,thumbnail_content,sha256,byte_size,thumbnail_byte_size,"
                    "to_jsonb(t)-'content'-'thumbnail_content' FROM housing_room_photos t "
                    "WHERE room_id=:id ORDER BY slot"
                ),
                {"id": UUID(room_id)},
            ).all()
            for (
                photo_id,
                slot,
                content,
                thumb,
                stored_hash,
                size,
                thumb_size,
                metadata,
            ) in raw_photos:
                photos.append(
                    {
                        "id": str(photo_id),
                        "room_id": str(room_id),
                        "slot": slot,
                        "original_sha256": care_sha256(bytes(content)),
                        "stored_sha256": stored_hash,
                        "thumbnail_sha256": care_sha256(bytes(thumb)),
                        "byte_size": size,
                        "thumbnail_byte_size": thumb_size,
                        "row_sha256": care_fingerprint(metadata),
                    }
                )
        raw_audits = (
            db.execute(
                text(
                    "SELECT to_jsonb(a) FROM audit_events a WHERE action IN "
                    "('housing.room.cleaning.complete','housing.room_photo.put') ORDER BY id"
                )
            )
            .scalars()
            .all()
        )
        result.update(
            rooms=rooms,
            photos=photos,
            audits=[{**row, "row_sha256": care_fingerprint(row)} for row in raw_audits],
        )
        return result


def seed_care_case(seed_history):
    """Real route writes on synthetic migrated data; never initializes a shared DB."""
    global CARE_PROBE_PHASE
    CARE_PROBE_PHASE = "imports"
    import io
    from datetime import UTC, datetime
    from uuid import UUID

    CARE_PROBE_PHASE = "import_pillow"
    from PIL import Image

    CARE_PROBE_PHASE = "import_sqlalchemy"
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    CARE_PROBE_PHASE = "import_auth_models"
    from app.auth.models import UserAccount
    from app.housing.models import HousingStay

    CARE_PROBE_PHASE = "import_app"
    from app.main import create_app
    from app.security.passwords import PasswordService

    # The composed program provides the existing synthetic source/target fixture.
    # It is migrated to CARE_REVISION before current ORM models are imported.
    CARE_PROBE_PHASE = "history_seed"
    prior = seed_history()
    ids = prior["identifiers"]
    engine = create_engine(CARE_DSN, hide_parameters=True)
    try:
        CARE_PROBE_PHASE = "actor_setup"
        with Session(engine) as db, db.begin():
            actor = db.get(UserAccount, UUID(ids["actor"]))
            actor.password_hash = PasswordService().hash("CareRestore7x!")
            room_id = str(db.get(HousingStay, UUID(ids["target"])).room_id)
        with care_http_client(create_app()) as client:
            CARE_PROBE_PHASE = "login"
            login = client.post(
                "/api/v1/auth/login",
                json={
                    "identifier": "13900000071",
                    "password": "CareRestore7x!",
                },
            )
            assert login.status_code == 200, "care_restore_actor_login_failed"
            headers = {"x-csrf-token": login.json()["csrf_token"]}

            def room_version():
                detail = client.get(f"/api/v1/housing/rooms/{room_id}/detail")
                assert detail.status_code == 200, "care_restore_detail_failed"
                return detail.json()["room"]["version"]

            CARE_PROBE_PHASE = "daily_update"
            changed = client.patch(
                f"/api/v1/housing/rooms/{room_id}",
                headers=headers,
                json={
                    "version": room_version(),
                    "daily_cleaning_enabled": True,
                },
            )
            assert changed.status_code == 200, "care_restore_daily_update_failed"
            CARE_PROBE_PHASE = "checkout_request"
            requested = client.post(
                f"/api/v1/housing/stays/{ids['target']}/request-checkout",
                headers=headers,
                json={
                    "version": 1,
                    "actual_check_out_at": datetime.now(UTC).isoformat(),
                },
            )
            assert requested.status_code == 200, "care_restore_checkout_request_failed"
            CARE_PROBE_PHASE = "checkout_confirm"
            confirmed = client.post(
                f"/api/v1/housing/stays/{ids['target']}/confirm-checkout",
                headers=headers,
                json={"version": requested.json()["version"]},
            )
            assert confirmed.status_code == 200, "care_restore_checkout_failed"
            before = collect_care_case(engine, [room_id])
            CARE_PROBE_PHASE = "clean_complete"
            completed = client.post(
                f"/api/v1/housing/rooms/{room_id}/cleaning/complete",
                headers=headers,
                json={"version": room_version()},
            )
            assert completed.status_code == 200, "care_restore_complete_failed"
            raw = io.BytesIO()
            Image.new("RGB", (720, 520), (50, 100, 150)).save(raw, format="PNG")
            CARE_PROBE_PHASE = "photo_upload"
            uploaded = client.put(
                f"/api/v1/housing/rooms/{room_id}/photos/1",
                headers=headers,
                data={"version": str(room_version())},
                files={"file": ("synthetic.png", raw.getvalue(), "image/png")},
            )
            assert uploaded.status_code == 200, "care_restore_photo_upload_failed"
        CARE_PROBE_PHASE = "compare_snapshot"
        expected = collect_care_case(engine, [room_id])
        for key in ("people_sha256", "accounts_sha256", "stays_sha256", "transfers_sha256"):
            assert before[key] == expected[key], "care_seed_changed_original_person_or_stay"
        validate_care_case(expected, expected)
        return {"room_ids": [room_id], "expected": expected}
    finally:
        engine.dispose()


def verify_care_case(evidence):
    from sqlalchemy import create_engine

    engine = create_engine(CARE_DSN, hide_parameters=True)
    try:
        return validate_care_case(
            collect_care_case(engine, evidence["room_ids"]), evidence["expected"]
        )
    finally:
        engine.dispose()
