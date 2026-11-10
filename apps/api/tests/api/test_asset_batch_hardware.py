from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.asset_management.models import Asset
from app.audit.models import AuditEvent
from tests.api.test_assets import sign_in

HARDWARE = {
    "cpu_model": "Ryzen 7",
    "memory_capacity": "32GB",
    "gpu_model": "核显",
    "disk_capacity": "1TB SSD + 2TB HDD",
}


def batch_payload(**asset):
    return {
        "operation_key": str(uuid4()),
        "quantity": 3,
        "asset": {
            "name": "批量租用电脑",
            "category": "台式电脑",
            "ownership_type": "rented",
            "rental_started_on": "2026-01-01",
            "rental_expires_on": "2027-01-01",
            **HARDWARE,
            **asset,
        },
    }


def test_batch_creates_distinct_assets_audits_replays_and_conflicts(db_client, platform_users, db):
    csrf = sign_in(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}
    payload = batch_payload(cpu_model="  Ryzen 7  ")
    first = db_client.post("/api/v1/assets/batch", headers=headers, json=payload)
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["total"] == 3
    assert len({x["id"] for x in result["items"]}) == 3
    assert len({x["asset_code"] for x in result["items"]}) == 3
    assert all(
        x["status"] == "available" and x["current_assignment"] is None and x["photo"] is None
        for x in result["items"]
    )
    assert all(x["cpu_model"] == "Ryzen 7" for x in result["items"])
    payload["asset"]["cpu_model"] = "Ryzen 7"
    replay = db_client.post("/api/v1/assets/batch", headers=headers, json=payload)
    assert replay.status_code == 201
    assert [x["id"] for x in replay.json()["items"]] == [x["id"] for x in result["items"]]
    ids = [UUID(x["id"]) for x in result["items"]]
    assert (
        db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "asset.create", AuditEvent.resource_id.in_(ids))
        )
        == 3
    )
    payload["quantity"] = 2
    assert db_client.post("/api/v1/assets/batch", headers=headers, json=payload).status_code == 409
    assert db.scalar(select(func.count()).select_from(Asset).where(Asset.id.in_(ids))) == 3
    sign_in(db_client, platform_users, "self_student")
    assert (
        db_client.post(
            "/api/v1/assets/batch",
            headers={"X-CSRF-Token": sign_in(db_client, platform_users, "self_student")},
            json=payload,
        ).status_code
        == 403
    )


@pytest.mark.parametrize("quantity", [0, -1, 101, 1.5, True, "3"])
def test_batch_rejects_invalid_quantities(db_client, platform_users, quantity):
    csrf = sign_in(db_client, platform_users, "admin")
    payload = batch_payload()
    payload["quantity"] = quantity
    assert (
        db_client.post(
            "/api/v1/assets/batch", headers={"X-CSRF-Token": csrf}, json=payload
        ).status_code
        == 422
    )


@pytest.mark.parametrize(
    "changes",
    [
        {
            "ownership_type": "owned",
            "acquisition_method": "purchase",
            "acquisition_channel": "offline",
            "rental_started_on": None,
            "rental_expires_on": None,
        },
        {"serial_number": "SN-duplicate"},
    ],
)
def test_batch_rejects_owned_assets_and_serial_copy(db_client, platform_users, changes):
    csrf = sign_in(db_client, platform_users, "admin")
    assert (
        db_client.post(
            "/api/v1/assets/batch", headers={"X-CSRF-Token": csrf}, json=batch_payload(**changes)
        ).status_code
        == 422
    )


def test_computer_hardware_create_update_and_safe_projections(db_client, platform_users, db):
    csrf = sign_in(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}
    assert (
        db_client.post(
            "/api/v1/assets", headers=headers, json={"name": "电脑", "category": "台式电脑"}
        ).status_code
        == 422
    )
    created = db_client.post(
        "/api/v1/assets",
        headers=headers,
        json={"name": "电脑", "category": "笔记本电脑", **HARDWARE},
    )
    assert created.status_code == 201, created.text
    asset = created.json()
    assert all(asset[k] == v for k, v in HARDWARE.items())
    scan = db_client.get(f"/api/v1/assets/scan/{asset['asset_code']}").json()
    assert all(scan[k] == v for k, v in HARDWARE.items())
    assert "serial_number" not in scan
    url = f"/api/v1/assets/{asset['id']}"
    assert db_client.patch(url, headers=headers, json={"gpu_model": "   "}).status_code == 422
    changed = db_client.patch(url, headers=headers, json={"cpu_model": "  Core i7  "})
    assert changed.status_code == 200
    assert changed.json()["cpu_model"] == "Core i7"
    # Historical incomplete computers are legitimate migration state.
    legacy = db.get(Asset, UUID(asset["id"]))
    legacy.cpu_model = legacy.memory_capacity = legacy.gpu_model = legacy.disk_capacity = None
    db.flush()
    assert db_client.patch(url, headers=headers, json={"location": "新位置"}).status_code == 200
    assert db_client.patch(url, headers=headers, json={"cpu_model": "Core i7"}).status_code == 422
    assert db_client.patch(url, headers=headers, json=HARDWARE).status_code == 200
    other = db_client.post(
        "/api/v1/assets", headers=headers, json={"name": "桌子", "category": "桌椅"}
    ).json()
    assert other["cpu_model"] is None
    assert (
        db_client.patch(
            f"/api/v1/assets/{other['id']}", headers=headers, json={"category": "台式电脑"}
        ).status_code
        == 422
    )


@pytest.fixture
def committed_batch(postgres_engine, test_settings):
    import base64
    import hashlib
    import hmac
    from datetime import UTC, date, datetime

    from fastapi.testclient import TestClient
    from sqlalchemy import delete, text
    from sqlalchemy.orm import Session

    from app.auth.models import AuthSession, UserAccount
    from app.core.database import get_db
    from app.identity.models import Person
    from app.main import create_app
    from app.rbac.models import UserPermissionOverride, UserRole
    from app.rbac.seed import ROLES

    marker = uuid4().hex
    token, csrf = uuid4().hex, uuid4().hex
    with Session(postgres_engine, expire_on_commit=False) as setup, setup.begin():
        person = Person(
            full_name="批量隔离测试",
            identity_ciphertext=marker,
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        setup.add(person)
        setup.flush()
        actor = UserAccount(
            person_id=person.id,
            email_normalized=f"{marker}@example.test",
            password_hash="unused",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        setup.add(actor)
        setup.flush()
        setup.add(UserRole(user_id=actor.id, role_id=ROLES["super_admin"].id))
        pepper = base64.urlsafe_b64decode(test_settings.session_token_pepper.get_secret_value())
        setup.add(
            AuthSession(
                user_id=actor.id,
                token_digest=hmac.new(pepper, token.encode(), hashlib.sha256).hexdigest(),
                csrf_digest=hmac.new(pepper, csrf.encode(), hashlib.sha256).hexdigest(),
                expires_at=datetime(2099, 1, 1, tzinfo=UTC),
            )
        )
    app = create_app(test_settings)

    def request_db():
        with Session(postgres_engine) as session:
            yield session

    app.dependency_overrides[get_db] = request_db

    def client_factory():
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("erp_session", token, path="/api")
        client.headers["X-CSRF-Token"] = csrf
        return client

    try:
        yield client_factory, actor.id
    finally:
        with postgres_engine.begin() as cleanup:
            cleanup.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update"))
            cleanup.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == actor.id))
            cleanup.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update"))
            cleanup.execute(
                text("DELETE FROM asset_batch_operations WHERE actor_user_id=:actor"),
                {"actor": actor.id},
            )
            cleanup.execute(delete(Asset).where(Asset.created_by_user_id == actor.id))
            cleanup.execute(delete(AuthSession).where(AuthSession.user_id == actor.id))
            cleanup.execute(delete(UserRole).where(UserRole.user_id == actor.id))
            cleanup.execute(
                delete(UserPermissionOverride).where(UserPermissionOverride.user_id == actor.id)
            )
            cleanup.execute(delete(UserAccount).where(UserAccount.id == actor.id))
            cleanup.execute(delete(Person).where(Person.id == person.id))


def test_batch_independent_concurrent_requests_create_only_once(committed_batch, postgres_engine):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from sqlalchemy.orm import Session

    factory, actor_id = committed_batch
    payload = batch_payload()
    barrier = Barrier(2)

    def request():
        with factory() as client:
            barrier.wait(timeout=10)
            response = client.post("/api/v1/assets/batch", json=payload)
            assert response.status_code == 201, response.text
            return [item["id"] for item in response.json()["items"]]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(request) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert results[0] == results[1]
    with Session(postgres_engine) as observer:
        assert (
            observer.scalar(
                select(func.count()).select_from(Asset).where(Asset.created_by_user_id == actor_id)
            )
            == 3
        )
        assert (
            observer.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == actor_id, AuditEvent.action == "asset.create")
            )
            == 3
        )


def test_batch_late_audit_failure_rolls_back_every_asset_and_operation(
    committed_batch, postgres_engine, monkeypatch
):
    from sqlalchemy import text
    from sqlalchemy.orm import Session

    from app.audit.service import AuditService

    factory, actor_id = committed_batch
    original = AuditService.append
    calls = 0

    def failing_audit(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("test-only audit outage")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AuditService, "append", failing_audit)
    with factory() as client:
        response = client.post("/api/v1/assets/batch", json=batch_payload())
    assert response.status_code == 500
    with Session(postgres_engine) as observer:
        assert (
            observer.scalar(
                select(func.count()).select_from(Asset).where(Asset.created_by_user_id == actor_id)
            )
            == 0
        )
        assert (
            observer.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == actor_id)
            )
            == 0
        )
        assert (
            observer.scalar(
                text("SELECT count(*) FROM asset_batch_operations WHERE actor_user_id=:actor"),
                {"actor": actor_id},
            )
            == 0
        )


@pytest.mark.parametrize("revocation", ["role", "session", "read"])
def test_batch_replay_checks_current_session_and_permissions(
    committed_batch, postgres_engine, revocation
):
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.auth.models import AuthSession
    from app.rbac.models import Permission, UserPermissionOverride, UserRole

    factory, actor_id = committed_batch
    payload = batch_payload()
    with factory() as client:
        assert client.post("/api/v1/assets/batch", json=payload).status_code == 201
        with Session(postgres_engine) as revoker, revoker.begin():
            if revocation == "role":
                revoker.execute(delete(UserRole).where(UserRole.user_id == actor_id))
            elif revocation == "session":
                revoker.execute(delete(AuthSession).where(AuthSession.user_id == actor_id))
            else:
                permission = revoker.scalar(
                    select(Permission).where(Permission.key == "assets.read")
                )
                revoker.add(
                    UserPermissionOverride(
                        user_id=actor_id,
                        permission_id=permission.id,
                        effect="deny",
                        scope_kind="global",
                    )
                )
        response = client.post("/api/v1/assets/batch", json=payload)
    assert response.status_code == (401 if revocation == "session" else 403), response.text


def test_procurement_computer_hardware_propagates_to_every_asset_and_replay(
    db_client, platform_users, db
):
    from tests.api.test_procurement import _create_center

    csrf = sign_in(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}
    center = _create_center(db_client, csrf)
    payload = {
        "operation_key": str(uuid4()),
        "purchase_date": "2026-10-28",
        "item_name": "采购电脑",
        "quantity": "2",
        "unit_price_cents": 100,
        "settlement_center_id": center["id"],
        "add_to_assets": True,
        "asset_category": "台式电脑",
        "asset_hardware": HARDWARE,
    }
    created = db_client.post("/api/v1/procurement", headers=headers, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["total_price_cents"] == 200
    purchase_id = UUID(created.json()["id"])
    items = list(db.scalars(select(Asset).where(Asset.source_purchase_id == purchase_id)))
    assert len(items) == 2
    assert all(
        all(getattr(item, key) == value for key, value in HARDWARE.items()) for item in items
    )
    assert all(item.ownership_type == "owned" for item in items)
    assert db_client.post("/api/v1/procurement", headers=headers, json=payload).status_code == 201
    assert len(list(db.scalars(select(Asset).where(Asset.source_purchase_id == purchase_id)))) == 2
    payload["asset_hardware"] = {**HARDWARE, "cpu_model": "Core i9"}
    assert db_client.post("/api/v1/procurement", headers=headers, json=payload).status_code == 409


def test_personal_and_person_profile_projections_include_hardware(db_client, platform_users):
    csrf = sign_in(db_client, platform_users, "admin")
    headers = {"X-CSRF-Token": csrf}
    created = db_client.post(
        "/api/v1/assets",
        headers=headers,
        json={"name": "办公电脑", "category": "台式电脑", **HARDWARE},
    ).json()
    admin_id = str(platform_users["people"]["admin"].id)
    assigned = db_client.post(
        f"/api/v1/assets/{created['id']}/assign",
        headers=headers,
        json={"person_id": admin_id, "assigned_at": "2026-10-28T09:00:00+08:00"},
    )
    assert assigned.status_code == 200
    for path in ("/api/v1/assets/me", f"/api/v1/assets/person/{admin_id}"):
        response = db_client.get(path)
        assert response.status_code == 200, response.text
        item = response.json()[0]
        assert all(item[key] == value for key, value in HARDWARE.items())


def test_operation_key_is_scoped_to_actor(db_client, platform_users, db):
    from app.rbac.models import UserRole
    from app.rbac.seed import ROLES

    payload = batch_payload()
    payload["quantity"] = 1
    csrf = sign_in(db_client, platform_users, "admin")
    first = db_client.post("/api/v1/assets/batch", headers={"X-CSRF-Token": csrf}, json=payload)
    assert first.status_code == 201
    other = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=other.id, role_id=ROLES["administration"].id))
    db.flush()
    csrf = sign_in(db_client, platform_users, "customer_service")
    second = db_client.post("/api/v1/assets/batch", headers={"X-CSRF-Token": csrf}, json=payload)
    assert second.status_code == 201, second.text
    assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]


@pytest.mark.parametrize("replay", [False, True])
def test_fresh_authorization_catches_revocation_after_initial_route_check(
    committed_batch, postgres_engine, monkeypatch, replay
):
    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.asset_management import router as asset_router
    from app.rbac.models import UserRole

    factory, actor_id = committed_batch
    payload = batch_payload()
    fresh = asset_router._fresh_asset_actor

    def revoke_then_authorize(*args, **kwargs):
        with Session(postgres_engine) as revoker, revoker.begin():
            revoker.execute(delete(UserRole).where(UserRole.user_id == actor_id))
        return fresh(*args, **kwargs)

    with factory() as client:
        if replay:
            assert client.post("/api/v1/assets/batch", json=payload).status_code == 201
        monkeypatch.setattr(asset_router, "_fresh_asset_actor", revoke_then_authorize)
        response = client.post("/api/v1/assets/batch", json=payload)
    assert response.status_code == 403, response.text
    with Session(postgres_engine) as observer:
        assert observer.scalar(
            select(func.count()).select_from(Asset).where(Asset.created_by_user_id == actor_id)
        ) == (3 if replay else 0)


@pytest.mark.parametrize("category", ["台式电脑", "笔记本电脑"])
def test_pre_feature_computer_purchase_replays_without_hardware_or_duplicate_assets(
    committed_batch, postgres_engine, category
):
    import hashlib
    import json
    from datetime import date
    from decimal import Decimal

    from sqlalchemy import delete
    from sqlalchemy.orm import Session

    from app.procurement.models import DailyPurchaseRecord, ProcurementSettlementCenter
    from tests.api.test_procurement import _create_center

    factory, actor = committed_batch
    key, purchase_id = uuid4(), uuid4()
    center_id = None
    try:
        with factory() as client:
            center = _create_center(client, client.headers["X-CSRF-Token"], f"legacy-{key}")
            center_id = UUID(center["id"])
            # Actual committed old-version operation and two legacy computers,
            # constructed independently of the new request schema and creator.
            old_payload = {
                "purchase_date": "2026-10-01",
                "item_name": "旧采购电脑",
                "purchase_channel": "offline",
                "product_url": None,
                "quantity": "2",
                "unit_price_cents": 100,
                "settlement_center_id": str(center_id),
                "uses_reserve_fund": False,
                "is_reimbursed": False,
                "is_settled": False,
                "settled_on": None,
                "add_to_assets": True,
                "asset_category": category,
                "asset_warranty_expires_on": None,
                "note": None,
            }
            fingerprint = hashlib.sha256(
                json.dumps(
                    old_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
            ).hexdigest()
            with Session(postgres_engine) as setup, setup.begin():
                setup.add(
                    DailyPurchaseRecord(
                        id=purchase_id,
                        operation_key=key,
                        request_fingerprint=fingerprint,
                        purchase_date=date(2026, 10, 1),
                        item_name="旧采购电脑",
                        purchase_channel="offline",
                        quantity=Decimal(2),
                        unit_price_cents=100,
                        total_price_cents=200,
                        settlement_center_id=center_id,
                        uses_reserve_fund=False,
                        is_reimbursed=False,
                        is_settled=False,
                        created_by_user_id=actor,
                        updated_by_user_id=actor,
                    )
                )
                setup.flush()
                for index in range(2):
                    setup.add(
                        Asset(
                            asset_code=f"OLD-COMPUTER-{key.hex[:12]}-{index}",
                            name="旧采购电脑",
                            category=category,
                            source_purchase_id=purchase_id,
                            ownership_type="owned",
                            acquisition_method="purchase",
                            acquisition_channel="offline",
                            created_by_user_id=actor,
                        )
                    )
            with Session(postgres_engine) as observer:
                audit_count = observer.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(AuditEvent.actor_user_id == actor)
                )
            response = client.post(
                "/api/v1/procurement", json={"operation_key": str(key), **old_payload}
            )
            assert response.status_code == 201, response.text
            assert response.json()["id"] == str(purchase_id)
            # A new key alone cannot bypass the new computer completeness rule.
            fresh_key = uuid4()
            rejected = client.post(
                "/api/v1/procurement", json={"operation_key": str(fresh_key), **old_payload}
            )
            assert rejected.status_code == 422, rejected.text
            changed = client.post(
                "/api/v1/procurement",
                json={"operation_key": str(key), **old_payload, "asset_hardware": HARDWARE},
            )
            assert changed.status_code == 409, changed.text
            with Session(postgres_engine) as observer:
                assets = list(
                    observer.scalars(select(Asset).where(Asset.created_by_user_id == actor))
                )
                assert len(assets) == 2
                assert all(
                    asset.cpu_model is None and asset.source_purchase_id == purchase_id
                    for asset in assets
                )
                assert (
                    observer.scalar(
                        select(func.count())
                        .select_from(DailyPurchaseRecord)
                        .where(DailyPurchaseRecord.created_by_user_id == actor)
                    )
                    == 1
                )
                assert (
                    observer.scalar(
                        select(func.count())
                        .select_from(DailyPurchaseRecord)
                        .where(DailyPurchaseRecord.operation_key == fresh_key)
                    )
                    == 0
                )
                assert (
                    observer.scalar(
                        select(func.count())
                        .select_from(AuditEvent)
                        .where(AuditEvent.actor_user_id == actor)
                    )
                    == audit_count
                )
    finally:
        with postgres_engine.begin() as cleanup:
            cleanup.execute(delete(Asset).where(Asset.created_by_user_id == actor))
            cleanup.execute(
                delete(DailyPurchaseRecord).where(DailyPurchaseRecord.id == purchase_id)
            )
            if center_id is not None:
                cleanup.execute(
                    delete(ProcurementSettlementCenter).where(
                        ProcurementSettlementCenter.id == center_id
                    )
                )
