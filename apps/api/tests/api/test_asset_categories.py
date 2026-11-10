from __future__ import annotations

from io import BytesIO
from uuid import uuid4

from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy.orm import Session

from app.asset_management.models import Asset
from app.rbac.models import RolePermission, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from tests.api.test_assets import sign_in

EXPECTED_GROUPS = [
    (
        "电脑与电竞设备",
        [
            "台式电脑",
            "笔记本电脑",
            "显示器",
            "训练手机",
            "平板电脑",
            "键盘",
            "鼠标",
            "电竞耳机",
            "游戏手柄",
            "VR设备",
        ],
    ),
    ("服务器与网络设备", ["服务器", "NAS与存储设备", "路由器", "交换机", "无线AP", "UPS电源"]),
    ("办公与教学设备", ["打印复印扫描设备", "投影仪", "教学大屏", "办公电话", "会议设备"]),
    ("家具与宿舍设施", ["办公桌", "办公椅", "电竞椅", "床与床垫", "衣柜与储物柜", "沙发与茶几"]),
    ("生活电器", ["空调", "风扇", "冰箱", "洗衣机", "饮水机", "热水器", "清洁电器"]),
    ("餐厨设备", ["炊事设备", "消毒设备", "商用冷藏冷冻设备", "餐桌餐椅", "餐厨器具"]),
    ("影音与直播设备", ["摄影摄像设备", "麦克风与声卡", "音响", "灯光与直播设备"]),
    ("安防与消防设备", ["监控与录像设备", "门禁设备", "消防器材"]),
    ("维修与保洁工具", ["维修工具", "保洁工具"]),
    ("体育与训练器材", ["健身器材", "运动器材", "训练辅助设备"]),
    ("交通与搬运设备", ["车辆", "电动车", "推车与搬运设备"]),
    ("其他资产", ["其他"]),
]


def test_global_reader_gets_complete_grouped_directory_without_caching(
    db_client: TestClient,
    platform_users,
) -> None:
    sign_in(db_client, platform_users, "admin")
    response = db_client.get("/api/v1/assets/categories")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, private"
    body = response.json()
    assert set(body) == {"groups", "legacy_categories"}
    assert body["legacy_categories"] == []
    assert [(group["label"], group["options"]) for group in body["groups"]] == EXPECTED_GROUPS
    keys = [group["key"] for group in body["groups"]]
    assert len(set(keys)) == 12
    assert all(key and key.isascii() for key in keys)
    assert all(set(group) == {"key", "label", "options"} for group in body["groups"])


def test_anonymous_and_personal_admin_cannot_read_directory(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    assert db_client.get("/api/v1/assets/categories").status_code == 401
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
    db.commit()
    sign_in(db_client, platform_users, "customer_service")
    assert db_client.get("/api/v1/assets/categories").status_code == 403


def test_manage_only_gets_presets_without_historical_categories(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    db.add(Asset(asset_code="CAT-MANAGE-OLD", name="旧资产", category="内部保密旧类"))
    account = platform_users["accounts"]["customer_service"]
    db.add(UserRole(user_id=account.id, role_id=ROLES["admin"].id))
    db.add(
        RolePermission(
            role_id=ROLES["admin"].id,
            permission_id=PERMISSIONS["assets.manage"].id,
            scope_kind="global",
        )
    )
    db.commit()
    sign_in(db_client, platform_users, "customer_service")
    response = db_client.get("/api/v1/assets/categories")
    assert response.status_code == 200
    assert len(response.json()["groups"]) == 12
    assert response.json()["legacy_categories"] == []
    assert db_client.get("/api/v1/assets").status_code == 403


def test_global_reader_sees_distinct_historical_values_across_pages_and_statuses(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    for index, category, status in [
        (0, "  旧教学器材  ", "retired"),
        (1, "旧教学器材", "maintenance"),
        (2, "旧教学器材", "available"),
        (3, "台式电脑", "retired"),
        (4, "   ", "available"),
        (5, None, "available"),
    ]:
        db.add(
            Asset(
                asset_code=f"CAT-OLD-{index:03}",
                name=f"旧资产{index}",
                category=category,
                status=status,
            )
        )
    db.flush()
    for index in range(25):
        db.add(Asset(asset_code=f"CAT-NEW-{index:03}", name=f"新资产{index}", category="其他"))
    db.commit()
    sign_in(db_client, platform_users, "admin")
    page = db_client.get("/api/v1/assets", params={"page": 1, "page_size": 20})
    assert page.status_code == 200
    assert page.json()["total"] == 31
    response = db_client.get("/api/v1/assets/categories")
    assert response.status_code == 200
    assert response.json()["legacy_categories"] == ["  旧教学器材  ", "旧教学器材"]


def test_existing_free_text_and_preset_values_survive_asset_and_purchase_paths(
    db_client: TestClient,
    platform_users,
) -> None:
    csrf = sign_in(db_client, platform_users, "admin")
    created = db_client.post(
        "/api/v1/assets",
        headers={"X-CSRF-Token": csrf},
        json={"name": "旧分类兼容资产", "category": "以前的自定义分类"},
    )
    assert created.status_code == 201
    asset_id = created.json()["id"]
    assert created.json()["category"] == "以前的自定义分类"
    updated = db_client.patch(
        f"/api/v1/assets/{asset_id}",
        headers={"X-CSRF-Token": csrf},
        json={"category": "电竞耳机"},
    )
    assert updated.status_code == 200
    assert updated.json()["category"] == "电竞耳机"
    listed = db_client.get("/api/v1/assets", params={"category": "电竞耳机"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [asset_id]
    exported = db_client.post(
        "/api/v1/assets/exports",
        headers={"X-CSRF-Token": csrf},
        json={"format": "xlsx", "purpose": "核对分类导出", "category": "电竞耳机"},
    )
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    rows = list(workbook.active.values)
    assert any("电竞耳机" in row for row in rows)
    center = db_client.post(
        "/api/v1/procurement/settlement-centers",
        headers={"X-CSRF-Token": csrf},
        json={"name": "分类采购中心"},
    )
    assert center.status_code == 201
    purchase = db_client.post(
        "/api/v1/procurement",
        headers={"X-CSRF-Token": csrf},
        json={
            "operation_key": str(uuid4()),
            "purchase_date": "2026-08-30",
            "item_name": "直播麦克风",
            "quantity": "1",
            "unit_price_cents": 1000,
            "settlement_center_id": center.json()["id"],
            "uses_reserve_fund": False,
            "is_reimbursed": False,
            "is_settled": False,
            "settled_on": None,
            "add_to_assets": True,
            "asset_category": "麦克风与声卡",
        },
    )
    assert purchase.status_code == 201
    purchased = db_client.get("/api/v1/assets", params={"category": "麦克风与声卡"})
    assert purchased.status_code == 200
    assert [item["source_purchase_id"] for item in purchased.json()["items"]] == [
        purchase.json()["id"]
    ]


def test_export_category_filter_preserves_exact_legacy_whitespace(
    db_client: TestClient,
    db: Session,
    platform_users,
) -> None:
    padded = "  旧教学器材  "
    db.add(Asset(asset_code="CAT-EXACT-PADDED", name="留有空格的旧分类", category=padded))
    db.add(Asset(asset_code="CAT-EXACT-PLAIN", name="无空格的旧分类", category=padded.strip()))
    db.commit()
    csrf = sign_in(db_client, platform_users, "admin")

    directory = db_client.get("/api/v1/assets/categories")
    assert directory.status_code == 200
    assert padded in directory.json()["legacy_categories"]
    listed = db_client.get("/api/v1/assets", params={"category": padded})
    assert listed.status_code == 200
    assert [item["asset_code"] for item in listed.json()["items"]] == ["CAT-EXACT-PADDED"]

    exported = db_client.post(
        "/api/v1/assets/exports",
        headers={"X-CSRF-Token": csrf},
        json={"format": "xlsx", "purpose": "核对原样历史分类", "category": padded},
    )
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content), read_only=True)
    rows = list(workbook.active.values)
    assert [(row[0], row[2]) for row in rows[1:]] == [("CAT-EXACT-PADDED", padded)]
