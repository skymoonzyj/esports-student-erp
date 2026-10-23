from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, event, func, select, text
from sqlalchemy.orm import Session

from app.asset_management.models import Asset
from app.audit.models import AuditEvent
from app.audit.service import AuditService
from app.auth.dependencies import require_standard_account
from app.auth.models import UserAccount
from app.auth.service import AuthContext
from app.core.database import get_db
from app.identity.models import Person
from app.main import create_app
from app.procurement.models import (
    DailyPurchaseRecord,
    ProcurementInvoice,
    ProcurementInvoicePurchase,
    ProcurementReserveFundEntry,
    ProcurementSettlementCenter,
)
from app.procurement.schemas import PurchaseCreate, SettlementCenterCreate
from app.procurement.service import ProcurementService
from app.rbac.models import UserRole
from app.rbac.seed import ROLES


@pytest.fixture
def committed_procurement(postgres_engine, test_settings):
    """Real commits, separate request/observer connections, no fixture savepoint."""
    with Session(postgres_engine, expire_on_commit=False) as db, db.begin():
        marker = uuid4().hex
        person = Person(
            full_name="采购回归",
            identity_ciphertext=marker,
            identity_digest=marker,
            sex="male",
            date_of_birth=date(1990, 1, 1),
        )
        db.add(person)
        db.flush()
        account = UserAccount(
            person_id=person.id,
            email_normalized=f"{marker}@example.test",
            password_hash="unused",
            status="active",
            force_password_change=False,
            failed_login_count=0,
        )
        db.add(account)
        db.flush()
        db.add(UserRole(user_id=account.id, role_id=ROLES["super_admin"].id))
        center = ProcurementService(db).create_center(
            SettlementCenterCreate(name=marker), actor_user_id=account.id
        )
        center_id = center.id
        purchase = ProcurementService(db).create_purchase(
            PurchaseCreate(
                purchase_date=date(2026, 9, 14),
                item_name="Existing",
                quantity="1",
                unit_price_cents=1000,
                settlement_center_id=center_id,
            ),
            actor_user_id=account.id,
        )
        purchase_id = purchase.id
    app = create_app(test_settings)

    def request_db():
        with Session(postgres_engine) as db:
            yield db

    app.dependency_overrides[get_db] = request_db
    app.dependency_overrides[require_standard_account] = lambda: AuthContext(
        session=None, account=account, person=person
    )
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, account.id, center_id, purchase_id
    finally:
        with postgres_engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_update")
            )
            connection.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == account.id))
            connection.execute(
                text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_update")
            )
            connection.execute(
                delete(ProcurementInvoicePurchase).where(
                    ProcurementInvoicePurchase.purchase_id.in_(
                        select(DailyPurchaseRecord.id).where(
                            DailyPurchaseRecord.created_by_user_id == account.id
                        )
                    )
                )
            )
            connection.execute(
                delete(ProcurementInvoice).where(
                    ProcurementInvoice.uploaded_by_user_id == account.id
                )
            )
            for model in (
                Asset,
                DailyPurchaseRecord,
                ProcurementReserveFundEntry,
                ProcurementSettlementCenter,
            ):
                connection.execute(delete(model).where(model.created_by_user_id == account.id))
            connection.execute(delete(UserRole).where(UserRole.user_id == account.id))
            connection.execute(delete(UserAccount).where(UserAccount.id == account.id))
            connection.execute(delete(Person).where(Person.id == person.id))


def _payload(center_id, **extra):
    return {
        "operation_key": str(uuid4()),
        "purchase_date": "2026-09-14",
        "item_name": "New",
        "quantity": "1",
        "unit_price_cents": 1000,
        "settlement_center_id": str(center_id),
        **extra,
    }


def _png():
    output = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, "PNG")
    return output.getvalue()


def _invoice(client, purchase_id):
    return client.post(
        "/api/v1/procurement/invoices",
        data={
            "amount_cents": 1000,
            "invoice_date": "2026-09-14",
            "purchase_ids": str(purchase_id),
        },
        files={"file": ("invoice.png", _png(), "image/png")},
    )


@pytest.mark.parametrize(
    "operation",
    [
        "center_create",
        "center_update",
        "reserve",
        "purchase",
        "update",
        "settlement",
        "reimbursement",
        "invoice",
        "delete_invoice",
    ],
)
def test_audit_failure_rolls_back_all_procurement_writes(
    committed_procurement,
    postgres_engine,
    monkeypatch,
    operation,
):
    client, actor, center_id, purchase_id = committed_procurement
    invoice_id = None
    if operation == "delete_invoice":
        uploaded = _invoice(client, purchase_id)
        assert uploaded.status_code == 201, uploaded.text
        invoice_id = uploaded.json()["id"]

    def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(AuditService, "append", fail_audit)
    base = "/api/v1/procurement"
    if operation == "center_create":
        result = client.post(f"{base}/settlement-centers", json={"name": uuid4().hex})
    elif operation == "center_update":
        result = client.patch(f"{base}/settlement-centers/{center_id}", json={"is_active": False})
    elif operation == "reserve":
        result = client.post(
            f"{base}/settlement-centers/{center_id}/reserve-fund-entries",
            json={"operation_key": str(uuid4()), "amount_cents": 5000},
        )
    elif operation == "purchase":
        result = client.post(base, json=_payload(center_id, add_to_assets=True))
    elif operation == "invoice":
        result = _invoice(client, purchase_id)
    elif operation == "delete_invoice":
        result = client.delete(f"{base}/invoices/{invoice_id}")
    else:
        suffix, payload = {
            "update": ("", {"item_name": "Changed"}),
            "settlement": ("/settlement", {"is_settled": True, "settled_on": "2026-09-14"}),
            "reimbursement": ("/reimbursement", {"is_reimbursed": True}),
        }[operation]
        result = client.patch(f"{base}/{purchase_id}{suffix}", json=payload)
    assert result.status_code == 500, result.text
    with Session(postgres_engine) as observer:
        for model, expected in (
            (DailyPurchaseRecord, 1),
            (ProcurementSettlementCenter, 1),
            (ProcurementReserveFundEntry, 0),
            (Asset, 0),
            (ProcurementInvoice, int(operation == "delete_invoice")),
        ):
            actor_column = (
                model.uploaded_by_user_id
                if model is ProcurementInvoice
                else model.created_by_user_id
            )
            assert (
                observer.scalar(
                    select(func.count()).select_from(model).where(actor_column == actor)
                )
                == expected
            )
        purchase = observer.get(DailyPurchaseRecord, purchase_id)
        assert purchase.item_name == "Existing"
        assert not purchase.is_settled and not purchase.is_reimbursed
        assert observer.get(ProcurementSettlementCenter, center_id).is_active


@pytest.mark.parametrize("kind", ["purchase", "reserve"])
def test_retries_are_deduplicated_and_payload_conflicts_are_rejected(committed_procurement, kind):
    client, _, center_id, _ = committed_procurement
    path = "/api/v1/procurement"
    payload = _payload(center_id)
    if kind == "reserve":
        path += f"/settlement-centers/{center_id}/reserve-fund-entries"
        payload = {"operation_key": str(uuid4()), "amount_cents": 10000}
    first = client.post(path, json=payload)
    retry = client.post(path, json=payload)
    assert first.status_code == retry.status_code == 201, (first.text, retry.text)
    assert first.json() == retry.json()
    changed = {
        **payload,
        ("item_name" if kind == "purchase" else "amount_cents"): (
            "Changed" if kind == "purchase" else 20000
        ),
    }
    assert client.post(path, json=changed).status_code == 409
    fresh = client.post(path, json={**payload, "operation_key": str(uuid4())})
    assert fresh.status_code == 201
    if kind == "purchase":
        assert fresh.json()["id"] != first.json()["id"]
    else:
        assert fresh.json()["reserve_fund_total_cents"] == 20000


@pytest.mark.parametrize("kind", ["purchase", "reserve"])
def test_concurrent_retries_make_one_business_and_audit_write(
    committed_procurement,
    postgres_engine,
    kind,
):
    client, actor, center_id, _ = committed_procurement
    path, payload = "/api/v1/procurement", _payload(center_id, add_to_assets=True)
    if kind == "reserve":
        path += f"/settlement-centers/{center_id}/reserve-fund-entries"
        payload = {"operation_key": str(uuid4()), "amount_cents": 10000}
    barrier = Barrier(2)

    def submit():
        barrier.wait(timeout=10)
        return client.post(path, json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    assert [result.status_code for result in results] == [201, 201]
    assert results[0].json() == results[1].json()
    with Session(postgres_engine) as observer:
        model = DailyPurchaseRecord if kind == "purchase" else ProcurementReserveFundEntry
        assert observer.scalar(
            select(func.count()).select_from(model).where(model.created_by_user_id == actor)
        ) == (2 if kind == "purchase" else 1)
        assert (
            observer.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.actor_user_id == actor)
            )
            == 1
        )
        assert observer.scalar(
            select(func.count()).select_from(Asset).where(Asset.created_by_user_id == actor)
        ) == int(kind == "purchase")


@pytest.mark.parametrize("kind", ["purchase", "reserve"])
def test_missing_operation_key_is_clear_and_does_not_write(committed_procurement, kind):
    client, _, center_id, _ = committed_procurement
    payload = _payload(center_id)
    payload.pop("operation_key")
    path = "/api/v1/procurement"
    if kind == "reserve":
        path += f"/settlement-centers/{center_id}/reserve-fund-entries"
        payload = {"amount_cents": 1000}
    response = client.post(path, json=payload)
    assert response.status_code == 422
    assert "刷新" in response.text and "核对" in response.text


def test_normalized_purchase_retry_and_permission_recheck(committed_procurement, postgres_engine):
    from app.rbac.models import UserPermissionOverride
    from app.rbac.seed import PERMISSIONS

    client, actor, center_id, _ = committed_procurement
    payload = _payload(center_id, item_name="  Normalized  ", quantity="1.000", add_to_assets=True)
    first = client.post("/api/v1/procurement", json=payload)
    assert first.status_code == 201
    normalized = {**payload, "item_name": "Normalized", "quantity": "1"}
    assert client.post("/api/v1/procurement", json=normalized).json()["id"] == first.json()["id"]
    with Session(postgres_engine) as db, db.begin():
        db.add(
            UserPermissionOverride(
                user_id=actor,
                permission_id=PERMISSIONS["assets.manage"].id,
                effect="deny",
                scope_kind="global",
                scope_id=None,
            )
        )
    try:
        assert client.post("/api/v1/procurement", json=normalized).status_code == 403
    finally:
        with Session(postgres_engine) as db, db.begin():
            db.execute(
                delete(UserPermissionOverride).where(UserPermissionOverride.user_id == actor)
            )


def test_invoice_list_export_skip_binary_and_download_is_exact(
    committed_procurement, postgres_engine
):
    client, _, center_id, purchase_id = committed_procurement
    uploaded = _invoice(client, purchase_id)
    assert uploaded.status_code == 201, uploaded.text
    for _ in range(2):
        created = client.post("/api/v1/procurement", json=_payload(center_id))
        assert created.status_code == 201
        assert _invoice(client, created.json()["id"]).status_code == 201
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(postgres_engine, "before_cursor_execute", capture)
    try:
        listed = client.get("/api/v1/procurement")
        assert listed.status_code == 200
        assert any(row["has_invoice"] for row in listed.json()["items"])
        # Invoice metadata must be loaded in a batch, not once per purchase.
        assert sum("procurement_invoices" in sql for sql in statements) <= 1
        assert not any(".content" in sql for sql in statements)
        statements.clear()
        exported = client.post(
            "/api/v1/procurement/exports",
            json={"format": "csv", "purpose": "回归测试导出", "scope": "all"},
        )
        assert exported.status_code == 200, exported.text
        assert sum("procurement_invoices" in sql for sql in statements) <= 1
        assert not any(".content" in statement for statement in statements)
        downloaded = client.get(f"/api/v1/procurement/invoices/{uploaded.json()['id']}/file")
        assert downloaded.status_code == 200 and downloaded.content == _png()
        assert any(".content" in statement for statement in statements)
    finally:
        event.remove(postgres_engine, "before_cursor_execute", capture)
