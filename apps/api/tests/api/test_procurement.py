from __future__ import annotations

from io import BytesIO
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.asset_management.models import Asset
from app.audit.models import AuditEvent
from app.rbac.models import UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES


def _login(
    client: TestClient,
    platform_users: dict[str, Any],
    key: str,
) -> dict[str, Any]:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _create_center(client: TestClient, csrf: str, name: str = "青训营运营中心") -> dict[str, Any]:
    response = client.post(
        "/api/v1/procurement/settlement-centers",
        headers={"X-CSRF-Token": csrf},
        json={"name": name},
    )
    assert response.status_code == 201
    return response.json()


def _create_purchase(
    client: TestClient,
    csrf: str,
    center_id: str,
    *,
    item_name: str = "饮用水",
    quantity: str = "2.500",
    unit_price_cents: int = 1299,
    purchase_channel: str | None = None,
    product_url: str | None = None,
    uses_reserve_fund: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation_key": str(uuid4()),
        "purchase_date": "2026-08-28",
        "item_name": item_name,
        "quantity": quantity,
        "unit_price_cents": unit_price_cents,
        "settlement_center_id": center_id,
        "uses_reserve_fund": uses_reserve_fund,
        "is_reimbursed": False,
        "is_settled": False,
        "settled_on": None,
        "note": "日常补货",
    }
    if purchase_channel is not None:
        payload["purchase_channel"] = purchase_channel
    if product_url is not None:
        payload["product_url"] = product_url
    response = client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": csrf},
        json=payload,
    )
    assert response.status_code == 201
    return response.json()


def test_admin_manages_filters_settles_and_exports_daily_purchases(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "Main Center")
    assert center["reserve_fund_total_cents"] == 0
    assert center["reserve_fund_used_cents"] == 0
    assert center["reserve_fund_remaining_cents"] == 0
    duplicate = db_client.post(
        "/api/v1/procurement/settlement-centers",
        headers={"X-CSRF-Token": csrf},
        json={"name": "main center"},
    )
    assert duplicate.status_code == 409

    record = _create_purchase(db_client, csrf, center["id"], item_name="=SUM(A1:A2)")
    assert record["quantity"] == "2.500"
    assert record["unit_price_cents"] == 1299
    assert record["total_price_cents"] == 3248
    assert record["settlement_center_name"] == "Main Center"
    assert record["purchase_channel"] == "offline"
    assert record["product_url"] is None
    assert record["is_reimbursed"] is False

    listed = db_client.get(
        "/api/v1/procurement",
        params={
            "settlement_center_id": center["id"],
            "reimbursement_status": "unreimbursed",
            "settlement_status": "unsettled",
            "start_date": "2026-08-28",
            "end_date": "2026-08-28",
        },
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["total_amount_cents"] == 3248
    assert listed.json()["reimbursed_amount_cents"] == 0
    assert listed.json()["unreimbursed_amount_cents"] == 3248
    assert listed.json()["unsettled_amount_cents"] == 3248

    settled = db_client.patch(
        f"/api/v1/procurement/{record['id']}/settlement",
        headers={"X-CSRF-Token": csrf},
        json={"is_settled": True, "settled_on": "2026-08-29"},
    )
    assert settled.status_code == 200
    assert settled.json()["is_settled"] is True

    reimbursed = db_client.patch(
        f"/api/v1/procurement/{record['id']}/reimbursement",
        headers={"X-CSRF-Token": csrf},
        json={"is_reimbursed": True},
    )
    assert reimbursed.status_code == 200
    assert reimbursed.json()["is_reimbursed"] is True
    assert reimbursed.json()["is_settled"] is True

    reimbursement_list = db_client.get(
        "/api/v1/procurement",
        params={"reimbursement_status": "reimbursed"},
    )
    assert reimbursement_list.status_code == 200
    assert reimbursement_list.json()["total"] == 1
    assert reimbursement_list.json()["reimbursed_amount_cents"] == 3248
    assert reimbursement_list.json()["unreimbursed_amount_cents"] == 0

    locked = db_client.patch(
        f"/api/v1/procurement/{record['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"quantity": "3"},
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "procurement_purchase_locked"

    exported = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "purpose": "采购月度对账",
            "settlement_center_id": center["id"],
            "reimbursement_status": "reimbursed",
            "settlement_status": "settled",
            "start_date": "2026-08-01",
            "end_date": "2026-08-31",
        },
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith("text/csv")
    exported_text = exported.content.decode("utf-8-sig")
    assert "'=SUM(A1:A2)" in exported_text
    assert "是否已报销" in exported_text
    assert "是" in exported_text

    reopened = db_client.patch(
        f"/api/v1/procurement/{record['id']}/settlement",
        headers={"X-CSRF-Token": csrf},
        json={"is_settled": False, "settled_on": None},
    )
    assert reopened.status_code == 200
    still_reimbursed = db_client.patch(
        f"/api/v1/procurement/{record['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"quantity": "3"},
    )
    assert still_reimbursed.status_code == 409
    assert still_reimbursed.json()["code"] == "procurement_purchase_reimbursed"
    reimbursement_reversed = db_client.patch(
        f"/api/v1/procurement/{record['id']}/reimbursement",
        headers={"X-CSRF-Token": csrf},
        json={"is_reimbursed": False},
    )
    assert reimbursement_reversed.status_code == 200
    updated = db_client.patch(
        f"/api/v1/procurement/{record['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"quantity": "3"},
    )
    assert updated.status_code == 200
    assert updated.json()["total_price_cents"] == 3897


def test_reimbursed_purchase_requires_reversal_before_financial_changes(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = _login(db_client, platform_users, "admin")["csrf_token"]
    headers = {"X-CSRF-Token": csrf}
    center = _create_center(db_client, csrf, "报销保护中心")
    other_center = _create_center(db_client, csrf, "报销保护其他中心")
    record = _create_purchase(db_client, csrf, center["id"])
    url = f"/api/v1/procurement/{record['id']}"
    assert db_client.patch(
        url + "/reimbursement", headers=headers, json={"is_reimbursed": True}
    ).status_code == 200

    for change in (
        {"quantity": "9"},
        {"unit_price_cents": 9900},
        {"settlement_center_id": other_center["id"]},
        {"uses_reserve_fund": True},
        {"purchase_date": "2026-09-01"},
        {"item_name": "修改已报销物品"},
    ):
        rejected = db_client.patch(url, headers=headers, json=change)
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "procurement_purchase_reimbursed"
        assert "先撤销报销" in rejected.json()["detail"]

    listed = db_client.get("/api/v1/procurement", params={"settlement_center_id": center["id"]})
    assert listed.status_code == 200
    current = next(item for item in listed.json()["items"] if item["id"] == record["id"])
    assert current["quantity"] == record["quantity"]
    assert current["unit_price_cents"] == record["unit_price_cents"]
    assert current["total_price_cents"] == record["total_price_cents"]
    assert current["settlement_center_id"] == center["id"]
    assert current["is_reimbursed"] is True
    assert current["uses_reserve_fund"] is False
    assert listed.json()["reimbursed_amount_cents"] == record["total_price_cents"]

    reopened = db_client.patch(
        url + "/reimbursement", headers=headers, json={"is_reimbursed": False}
    )
    assert reopened.status_code == 200
    changed = db_client.patch(url, headers=headers, json={"quantity": "3"})
    assert changed.status_code == 200
    assert float(changed.json()["quantity"]) == 3
    assert changed.json()["total_price_cents"] == 3 * record["unit_price_cents"]
    assert changed.json()["is_reimbursed"] is False


def test_purchase_can_create_individually_tracked_assets(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "资产采购中心")

    response = db_client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": "purchase-assets-create"},
        json={
            "operation_key": str(uuid4()),
            "purchase_date": "2026-08-30",
            "item_name": "训练平板",
            "quantity": "2",
            "unit_price_cents": 250000,
            "settlement_center_id": center["id"],
            "uses_reserve_fund": False,
            "is_reimbursed": False,
            "is_settled": False,
            "settled_on": None,
            "add_to_assets": True,
            "asset_category": "训练设备",
            "asset_warranty_expires_on": "2028-08-30",
        },
    )
    assert response.status_code == 201
    purchase_id = response.json()["id"]
    assets = list(
        db.scalars(select(Asset).where(Asset.source_purchase_id == purchase_id).order_by(Asset.id))
    )
    assert len(assets) == 2
    assert len({asset.asset_code for asset in assets}) == 2
    assert {asset.name for asset in assets} == {"训练平板"}
    assert {asset.category for asset in assets} == {"训练设备"}
    assert {asset.purchase_date.isoformat() for asset in assets if asset.purchase_date} == {
        "2026-08-30"
    }
    assert {
        asset.warranty_expires_on.isoformat() for asset in assets if asset.warranty_expires_on
    } == {"2028-08-30"}
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == "purchase-assets-create"))
    assert audit is not None
    assert audit.event_metadata["item_count"] == 2


def test_purchase_asset_creation_rejects_consumables_and_oversized_batches(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "资产数量校验中心")
    base = {
        "operation_key": str(uuid4()),
        "purchase_date": "2026-08-30",
        "item_name": "数量校验物品",
        "unit_price_cents": 100,
        "settlement_center_id": center["id"],
        "uses_reserve_fund": False,
        "is_reimbursed": False,
        "is_settled": False,
        "settled_on": None,
        "add_to_assets": True,
    }
    for quantity in ("1.5", "101"):
        response = db_client.post(
            "/api/v1/procurement",
            headers={"X-CSRF-Token": csrf},
            json={**base, "quantity": quantity},
        )
        assert response.status_code == 422


def test_purchase_asset_creation_requires_asset_management_permission(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "资产权限校验中心")
    db.add(
        UserPermissionOverride(
            user_id=platform_users["accounts"]["admin"].id,
            permission_id=PERMISSIONS["assets.manage"].id,
            effect="deny",
            scope_kind="global",
            scope_id=None,
        )
    )
    db.commit()

    response = db_client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": csrf},
        json={
            "operation_key": str(uuid4()),
            "purchase_date": "2026-08-30",
            "item_name": "无资产权限采购",
            "quantity": "1",
            "unit_price_cents": 100,
            "settlement_center_id": center["id"],
            "uses_reserve_fund": False,
            "is_reimbursed": False,
            "is_settled": False,
            "settled_on": None,
            "add_to_assets": True,
        },
    )
    assert response.status_code == 403


def test_export_scopes_and_pending_reimbursement_summary(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "报销导出中心")
    other_center = _create_center(db_client, csrf, "其他结算中心")
    _create_purchase(db_client, csrf, center["id"], item_name="待办采购")
    reimbursed = _create_purchase(db_client, csrf, center["id"], item_name="已报销采购")
    settled = _create_purchase(db_client, csrf, center["id"], item_name="已结算采购")
    completed = _create_purchase(db_client, csrf, center["id"], item_name="全部完成采购")
    _create_purchase(db_client, csrf, other_center["id"], item_name="其他中心采购")

    assert (
        db_client.patch(
            f"/api/v1/procurement/{reimbursed['id']}/reimbursement",
            headers={"X-CSRF-Token": csrf},
            json={"is_reimbursed": True},
        ).status_code
        == 200
    )
    for record in (settled, completed):
        assert (
            db_client.patch(
                f"/api/v1/procurement/{record['id']}/settlement",
                headers={"X-CSRF-Token": csrf},
                json={"is_settled": True, "settled_on": "2026-08-29"},
            ).status_code
            == 200
        )
    assert (
        db_client.patch(
            f"/api/v1/procurement/{completed['id']}/reimbursement",
            headers={"X-CSRF-Token": csrf},
            json={"is_reimbursed": True},
        ).status_code
        == 200
    )

    request_id = "procurement-unreimbursed-export"
    pending_export = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf, "X-Request-ID": request_id},
        json={
            "format": "xlsx",
            "scope": "unreimbursed",
            "purpose": "导出待报销明细",
            "settlement_center_id": center["id"],
            "reimbursement_status": "reimbursed",
            "settlement_status": "settled",
        },
    )
    assert pending_export.status_code == 200
    assert "pending-reimbursements-" in pending_export.headers["content-disposition"]
    workbook = load_workbook(BytesIO(pending_export.content), data_only=False)
    sheet = workbook["待报销汇总"]
    assert sheet["A1"].value == "日常采购报销单（汇总）"
    assert sheet["B2"].value == platform_users["people"]["admin"].full_name
    assert sheet["B3"].value == "日常采购待报销（2 项）"
    assert {sheet["D6"].value, sheet["D7"].value} == {
        "待办采购（2.5 × ¥12.99）",
        "已结算采购（2.5 × ¥12.99）",
    }
    assert sheet["F8"].value == "=SUM(F6:F7)"
    assert sheet["F9"].value == "=F8"
    assert sheet["H2"].value == "=F9"
    assert sheet.page_setup.orientation == "portrait"
    assert sheet.print_area == "'待报销汇总'!$A$1:$I$9"

    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == request_id))
    assert audit is not None
    assert audit.action == "procurement.purchase.export"
    assert audit.purpose == "导出待报销明细"
    assert audit.event_metadata["export_scope"] == "unreimbursed"
    assert audit.event_metadata["settlement_center_id"] == center["id"]
    assert audit.event_metadata["format"] == "xlsx"
    assert audit.event_metadata["item_count"] == 2

    scope_expectations = {
        "reimbursed": ({"已报销采购", "全部完成采购"}, {"待办采购", "已结算采购"}),
        "unsettled": ({"待办采购", "已报销采购"}, {"已结算采购", "全部完成采购"}),
        "settled": ({"已结算采购", "全部完成采购"}, {"待办采购", "已报销采购"}),
    }
    for scope, (included, excluded) in scope_expectations.items():
        exported = db_client.post(
            "/api/v1/procurement/exports",
            headers={"X-CSRF-Token": csrf},
            json={
                "format": "csv",
                "scope": scope,
                "purpose": f"核对{scope}范围",
                "settlement_center_id": center["id"],
                "reimbursement_status": "unreimbursed",
                "settlement_status": "settled",
            },
        )
        assert exported.status_code == 200
        exported_text = exported.content.decode("utf-8-sig")
        assert all(item in exported_text for item in included)
        assert all(item not in exported_text for item in excluded)
        assert "其他中心采购" not in exported_text

    current_export = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "scope": "current",
            "purpose": "核对当前筛选",
            "query": "待办采购",
        },
    )
    assert current_export.status_code == 200
    current_text = current_export.content.decode("utf-8-sig")
    assert "待办采购" in current_text
    assert "已报销采购" not in current_text

    all_export = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "scope": "all",
            "purpose": "核对全部采购",
            "query": "不存在的记录",
            "invoice_status": "invoiced",
            "start_date": "2099-01-01",
        },
    )
    assert all_export.status_code == 200
    all_text = all_export.content.decode("utf-8-sig")
    assert all(
        item in all_text
        for item in (
            "待办采购",
            "已报销采购",
            "已结算采购",
            "全部完成采购",
            "其他中心采购",
        )
    )

    center_all_export = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "scope": "all",
            "purpose": "核对指定结算中心全部采购",
            "settlement_center_id": center["id"],
            "query": "不存在的记录",
        },
    )
    assert center_all_export.status_code == 200
    center_all_text = center_all_export.content.decode("utf-8-sig")
    assert "待办采购" in center_all_text
    assert "其他中心采购" not in center_all_text

    rejected_csv = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "scope": "unreimbursed",
            "purpose": "待报销格式校验",
        },
    )
    assert rejected_csv.status_code == 422


def test_administration_adds_and_spends_center_reserve_funds_without_overdraft(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "备用金测试中心")

    first_funding = db_client.post(
        f"/api/v1/procurement/settlement-centers/{center['id']}/reserve-fund-entries",
        headers={"X-CSRF-Token": csrf},
        json={"operation_key": str(uuid4()), "amount_cents": 10_000, "note": "首笔备用金"},
    )
    assert first_funding.status_code == 201
    assert first_funding.json()["reserve_fund_total_cents"] == 10_000
    second_funding = db_client.post(
        f"/api/v1/procurement/settlement-centers/{center['id']}/reserve-fund-entries",
        headers={"X-CSRF-Token": csrf},
        json={"operation_key": str(uuid4()), "amount_cents": 5_000, "note": "追加备用金"},
    )
    assert second_funding.status_code == 201
    assert second_funding.json()["reserve_fund_total_cents"] == 15_000

    purchase = _create_purchase(
        db_client,
        csrf,
        center["id"],
        item_name="备用金采购",
        quantity="1",
        unit_price_cents=6_000,
        uses_reserve_fund=True,
    )
    assert purchase["uses_reserve_fund"] is True
    centers = db_client.get(
        "/api/v1/procurement/settlement-centers",
        params={"include_inactive": True},
    )
    assert centers.status_code == 200
    balance = next(item for item in centers.json() if item["id"] == center["id"])
    assert balance["reserve_fund_used_cents"] == 6_000
    assert balance["reserve_fund_remaining_cents"] == 9_000

    overdrawn = db_client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": csrf},
        json={
            "operation_key": str(uuid4()),
            "purchase_date": "2026-08-29",
            "item_name": "超额备用金采购",
            "quantity": "1",
            "unit_price_cents": 9_001,
            "settlement_center_id": center["id"],
            "uses_reserve_fund": True,
        },
    )
    assert overdrawn.status_code == 409
    assert overdrawn.json()["code"] == "procurement_reserve_fund_insufficient"

    increased = db_client.patch(
        f"/api/v1/procurement/{purchase['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"quantity": "2"},
    )
    assert increased.status_code == 200
    assert increased.json()["total_price_cents"] == 12_000
    rejected_edit = db_client.patch(
        f"/api/v1/procurement/{purchase['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"quantity": "3"},
    )
    assert rejected_edit.status_code == 409
    assert rejected_edit.json()["code"] == "procurement_reserve_fund_insufficient"

    changed_source = db_client.patch(
        f"/api/v1/procurement/{purchase['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"uses_reserve_fund": False},
    )
    assert changed_source.status_code == 200
    assert changed_source.json()["uses_reserve_fund"] is False
    refreshed_centers = db_client.get("/api/v1/procurement/settlement-centers")
    refreshed = next(item for item in refreshed_centers.json() if item["id"] == center["id"])
    assert refreshed["reserve_fund_used_cents"] == 0
    assert refreshed["reserve_fund_remaining_cents"] == 15_000

    exported = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "purpose": "备用金采购核对",
            "settlement_center_id": center["id"],
        },
    )
    assert exported.status_code == 200
    assert "是否使用备用金" in exported.content.decode("utf-8-sig")


def test_online_purchase_links_validate_export_and_clear_when_switching_offline(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "线上采购中心")
    product_url = "https://shop.example.com/products/training-water?sku=12"
    record = _create_purchase(
        db_client,
        csrf,
        center["id"],
        item_name="线上训练饮用水",
        purchase_channel="online",
        product_url=product_url,
    )

    assert record["purchase_channel"] == "online"
    assert record["product_url"] == product_url

    invalid = db_client.patch(
        f"/api/v1/procurement/{record['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"product_url": "javascript:alert(1)"},
    )
    assert invalid.status_code == 422

    exported = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "purpose": "核对线上采购链接",
            "settlement_center_id": center["id"],
        },
    )
    assert exported.status_code == 200
    exported_text = exported.content.decode("utf-8-sig")
    assert "采购渠道" in exported_text
    assert "商品链接" in exported_text
    assert product_url in exported_text

    switched = db_client.patch(
        f"/api/v1/procurement/{record['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"purchase_channel": "offline"},
    )
    assert switched.status_code == 200
    assert switched.json()["purchase_channel"] == "offline"
    assert switched.json()["product_url"] is None

    forced_offline = _create_purchase(
        db_client,
        csrf,
        center["id"],
        item_name="线下采购用品",
        purchase_channel="offline",
        product_url="https://shop.example.com/must-not-remain",
    )
    assert forced_offline["purchase_channel"] == "offline"
    assert forced_offline["product_url"] is None


def test_administration_has_full_procurement_permissions(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
) -> None:
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["administration"].id))
    db.commit()

    login = _login(db_client, platform_users, "customer_service")
    permissions = set(login["user"]["permissions"])
    assert {
        "procurement.read",
        "procurement.manage",
        "procurement.settle",
        "procurement.export",
        "procurement.centers.manage",
        "procurement.invoices.manage",
    } <= permissions
    center = _create_center(db_client, login["csrf_token"], "行政结算中心")
    record = _create_purchase(db_client, login["csrf_token"], center["id"])
    assert record["settlement_center_id"] == center["id"]
    reimbursed = db_client.patch(
        f"/api/v1/procurement/{record['id']}/reimbursement",
        headers={"X-CSRF-Token": login["csrf_token"]},
        json={"is_reimbursed": True},
    )
    assert reimbursed.status_code == 200
    assert reimbursed.json()["is_reimbursed"] is True


def test_finance_can_read_settle_and_export_but_not_enter_purchases(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
) -> None:
    admin = _login(db_client, platform_users, "admin")
    center = _create_center(db_client, admin["csrf_token"], "财务验收中心")
    record = _create_purchase(db_client, admin["csrf_token"], center["id"])

    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["finance"].id))
    db.commit()
    db_client.cookies.clear()
    finance = _login(db_client, platform_users, "customer_service")
    permissions = set(finance["user"]["permissions"])
    assert {
        "procurement.read",
        "procurement.settle",
        "procurement.export",
        "procurement.invoices.manage",
    } <= permissions
    assert "procurement.manage" not in permissions
    assert "procurement.centers.manage" not in permissions

    assert db_client.get("/api/v1/procurement").status_code == 200
    denied_center = db_client.post(
        "/api/v1/procurement/settlement-centers",
        headers={"X-CSRF-Token": finance["csrf_token"]},
        json={"name": "财务不可新增"},
    )
    assert denied_center.status_code == 403
    denied_purchase = db_client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": finance["csrf_token"]},
        json={
            "operation_key": str(uuid4()),
            "purchase_date": "2026-08-28",
            "item_name": "不应保存",
            "quantity": "1",
            "unit_price_cents": 100,
            "settlement_center_id": center["id"],
        },
    )
    assert denied_purchase.status_code == 403
    denied_reserve_fund = db_client.post(
        f"/api/v1/procurement/settlement-centers/{center['id']}/reserve-fund-entries",
        headers={"X-CSRF-Token": finance["csrf_token"]},
        json={"operation_key": str(uuid4()), "amount_cents": 10_000},
    )
    assert denied_reserve_fund.status_code == 403
    settled = db_client.patch(
        f"/api/v1/procurement/{record['id']}/settlement",
        headers={"X-CSRF-Token": finance["csrf_token"]},
        json={"is_settled": True, "settled_on": "2026-08-29"},
    )
    assert settled.status_code == 200
    denied_reimbursement = db_client.patch(
        f"/api/v1/procurement/{record['id']}/reimbursement",
        headers={"X-CSRF-Token": finance["csrf_token"]},
        json={"is_reimbursed": True},
    )
    assert denied_reimbursement.status_code == 403
    exported = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": finance["csrf_token"]},
        json={"format": "xlsx", "purpose": "财务采购核对"},
    )
    assert exported.status_code == 200
    assert exported.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )


def test_invoice_upload_auto_matches_filters_downloads_and_unlinks_purchases(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "发票匹配中心")
    first = _create_purchase(
        db_client,
        csrf,
        center["id"],
        item_name="清洁用品",
        quantity="1",
        unit_price_cents=3500,
    )
    second = _create_purchase(
        db_client,
        csrf,
        center["id"],
        item_name="打印纸",
        quantity="2",
        unit_price_cents=1250,
    )

    suggested = db_client.get(
        "/api/v1/procurement/invoices/match-suggestions",
        params={"amount_cents": 6000, "settlement_center_id": center["id"]},
    )
    assert suggested.status_code == 200
    suggestion = suggested.json()
    assert set(suggestion["suggested_purchase_ids"]) == {first["id"], second["id"]}
    assert suggestion["matched_total_cents"] == 6000

    uploaded = db_client.post(
        "/api/v1/procurement/invoices",
        headers={"X-CSRF-Token": csrf},
        data={
            "amount_cents": "6000",
            "invoice_date": "2026-08-28",
            "invoice_number": "FP-20260828-01",
            "note": "月末采购发票",
            "purchase_ids": [first["id"], second["id"]],
        },
        files={
            "file": (
                "august-invoice.pdf",
                b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF",
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 201, uploaded.text
    invoice = uploaded.json()
    assert invoice["amount_cents"] == 6000
    assert set(invoice["purchase_ids"]) == {first["id"], second["id"]}
    assert invoice["original_filename"] == "august-invoice.pdf"

    listed = db_client.get(
        "/api/v1/procurement",
        params={"invoice_status": "invoiced"},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    assert {item["invoice"]["id"] for item in listed.json()["items"]} == {invoice["id"]}
    assert all(item["has_invoice"] for item in listed.json()["items"])

    locked = db_client.patch(
        f"/api/v1/procurement/{first['id']}",
        headers={"X-CSRF-Token": csrf},
        json={"quantity": "2"},
    )
    assert locked.status_code == 409
    assert locked.json()["code"] == "procurement_purchase_locked"

    duplicate = db_client.post(
        "/api/v1/procurement/invoices",
        headers={"X-CSRF-Token": csrf},
        data={
            "amount_cents": "3500",
            "invoice_date": "2026-08-28",
            "purchase_ids": first["id"],
        },
        files={"file": ("duplicate.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "procurement_invoice_purchase_conflict"

    downloaded = db_client.get(f"/api/v1/procurement/invoices/{invoice['id']}/file")
    assert downloaded.status_code == 200
    assert downloaded.headers["content-type"] == "application/pdf"
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["content-disposition"].startswith("attachment;")

    exported = db_client.post(
        "/api/v1/procurement/exports",
        headers={"X-CSRF-Token": csrf},
        json={
            "format": "csv",
            "purpose": "核对采购发票",
            "invoice_status": "invoiced",
        },
    )
    assert exported.status_code == 200
    exported_text = exported.content.decode("utf-8-sig")
    assert "是否已开票" in exported_text
    assert "august-invoice.pdf" in exported_text

    removed = db_client.delete(
        f"/api/v1/procurement/invoices/{invoice['id']}",
        headers={"X-CSRF-Token": csrf},
    )
    assert removed.status_code == 204
    uninvoiced = db_client.get(
        "/api/v1/procurement",
        params={"invoice_status": "uninvoiced"},
    )
    assert uninvoiced.status_code == 200
    assert uninvoiced.json()["total"] == 2


def test_invoice_rejects_amount_mismatch_and_unsupported_file(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    csrf = login["csrf_token"]
    center = _create_center(db_client, csrf, "发票校验中心")
    record = _create_purchase(db_client, csrf, center["id"])

    mismatch = db_client.post(
        "/api/v1/procurement/invoices",
        headers={"X-CSRF-Token": csrf},
        data={
            "amount_cents": "9999",
            "invoice_date": "2026-08-28",
            "purchase_ids": record["id"],
        },
        files={"file": ("invoice.pdf", b"%PDF-1.4\n%%EOF", "application/pdf")},
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["code"] == "procurement_invoice_amount_mismatch"

    invalid = db_client.post(
        "/api/v1/procurement/invoices",
        headers={"X-CSRF-Token": csrf},
        data={
            "amount_cents": str(record["total_price_cents"]),
            "invoice_date": "2026-08-28",
            "purchase_ids": record["id"],
        },
        files={"file": ("invoice.svg", b"<svg></svg>", "image/svg+xml")},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "procurement_invoice_file_invalid"
