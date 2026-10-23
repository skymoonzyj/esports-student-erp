from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from typing import Any
from xml.etree import ElementTree
from zipfile import ZipFile

from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.models import AuditEvent
from app.travel_reports.models import TravelReportConversion

_SPREADSHEET_NAMESPACE = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _without_formula_cache(content: bytes) -> bytes:
    source = BytesIO(content)
    output = BytesIO()
    with ZipFile(source) as archive, ZipFile(output, "w") as rebuilt:
        for member in archive.infolist():
            payload = archive.read(member.filename)
            if member.filename == "xl/worksheets/sheet1.xml":
                root = ElementTree.fromstring(payload)
                for cell in root.iter(f"{{{_SPREADSHEET_NAMESPACE}}}c"):
                    if cell.find(f"{{{_SPREADSHEET_NAMESPACE}}}f") is None:
                        continue
                    value = cell.find(f"{{{_SPREADSHEET_NAMESPACE}}}v")
                    if value is not None:
                        value.text = None
                ElementTree.register_namespace("", _SPREADSHEET_NAMESPACE)
                payload = ElementTree.tostring(
                    root,
                    encoding="utf-8",
                    xml_declaration=True,
                )
            rebuilt.writestr(member, payload)
    return output.getvalue()


def _source_workbook(
    *,
    billing_period: str = "2026.08.01-2026.08.31",
    cost_center: str = "苍渊",
) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "账单汇总表"
    for row in (
        ["杭州飞巴旅行社有限公司对账单"],
        ["客户信息"],
        ["公司名称", "测试文化创意有限公司"],
        ["账单周期", billing_period],
        ["成本中心名称", cost_center],
        ["类目", "金额"],
        ["本期账单消费金额", 200],
    ):
        summary.append(row)
    detail = workbook.create_sheet("汇总明细")
    detail.append(["序号", "所属业务", "企业应付金额"])
    detail.append([1, "国内机票", 1150])
    detail.append([2, "国内机票", -950])
    domestic = workbook.create_sheet("国内机票")
    domestic.append([
        "序号", "费用承担部门", "乘机人", "航线", "航班号", "出发时间",
        "标准票面价", "优惠金额(大客户/政采)", "行程单票面价", "燃油费",
        "机建费", "改签费", "退票费", "代订机票费", "企业应付金额",
        "出差申请单号", "员工备注",
    ])
    domestic.append([
        1, "项目组", "学员甲", "成都-上海", "MU5410", "2026-08-01 12:25",
        1000, 0, 1000, 100, 50, 0, 0, 0, 1150, "审批-1", "",
    ])
    domestic.append([
        2, "项目组", "学员甲", "成都-上海", "MU5410", "2026-08-01 12:25",
        -1000, 0, -1000, -100, -50, 0, 200, 0, -950, "", "退票",
    ])
    international = workbook.create_sheet("国际机票")
    international.append([
        "序号", "费用承担部门", "乘机人", "乘机人中文名", "航线", "航班号",
        "出发时间", "标准票面价", "税费", "升舱费", "改签费", "退票费",
        "服务费", "企业应付金额", "出差申请单号", "员工备注",
    ])
    train = workbook.create_sheet("火车")
    train.append([
        "序号", "费用承担部门", "乘客", "出发时间", "出发站点", "到达站点",
        "车次", "票面价", "改签费", "退票费", "出票服务费", "代打费",
        "企业应付金额", "出差申请单号", "员工备注",
    ])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _login(client: TestClient, platform_users: dict[str, Any], key: str) -> dict[str, Any]:
    phone, password, _ = platform_users["credentials"][key]
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": phone, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def test_super_admin_imports_lists_and_downloads_travel_reports(
    db_client: TestClient,
    db: Session,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    content = _source_workbook()
    imported = db_client.post(
        "/api/v1/travel-reports/imports",
        headers={"X-CSRF-Token": login["csrf_token"]},
        files={
            "file": (
                "飞巴商旅26年8月账单.xlsx",
                content,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert imported.status_code == 201
    body = imported.json()
    assert body["domestic_air_count"] == 2
    assert body["source_total_amount"] == "200.00"
    assert body["converted_total_amount"] == "200.00"
    assert body["warnings"] == []

    listed = db_client.get("/api/v1/travel-reports")
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "no-store, private"
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["imported_by_name"]
    assert listed.json()["filter_options"] == {
        "billing_periods": [
            {
                "billing_period_start": "2026-08-01",
                "billing_period_end": "2026-08-31",
            }
        ],
        "cost_centers": ["苍渊"],
    }

    report = db.get(TravelReportConversion, body["id"])
    assert report is not None
    legacy_content = _without_formula_cache(report.converted_content)
    report.converted_content = legacy_content
    report.converted_sha256 = sha256(legacy_content).hexdigest()
    report.converted_byte_size = len(legacy_content)
    db.flush()

    converted = db_client.get(f"/api/v1/travel-reports/{body['id']}/converted")
    assert converted.status_code == 200
    assert "filename*=UTF-8''" in converted.headers["content-disposition"]
    db.refresh(report, attribute_names=["converted_content", "converted_sha256"])
    assert report.converted_content == converted.content
    assert report.converted_sha256 == sha256(converted.content).hexdigest()
    workbook = load_workbook(BytesIO(converted.content), data_only=False)
    assert workbook["Sheet1"]["F4"].value == -1000
    assert workbook["Sheet1"]["N5"].value == "=SUM(N3:N4)"
    workbook.close()
    cached_workbook = load_workbook(BytesIO(converted.content), data_only=True)
    assert Decimal(str(cached_workbook["Sheet1"]["N5"].value)) == Decimal("200.00")
    cached_workbook.close()

    source = db_client.get(f"/api/v1/travel-reports/{body['id']}/source")
    assert source.status_code == 200
    assert source.content == content

    duplicate = db_client.post(
        "/api/v1/travel-reports/imports",
        headers={"X-CSRF-Token": login["csrf_token"]},
        files={"file": ("duplicate.xlsx", content)},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "travel_report_duplicate"

    actions = set(
        db.scalars(
            select(AuditEvent.action).where(AuditEvent.resource_id == body["id"])
        )
    )
    assert {
        "travel_report.import",
        "travel_report.converted.download",
        "travel_report.source.download",
    }.issubset(actions)


def test_travel_report_list_filters_by_period_and_cost_center(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "admin")
    for filename, period, cost_center in (
        ("8月账单.xlsx", "2026.08.01-2026.08.31", "苍渊"),
        ("9月账单.xlsx", "2026.09.01-2026.09.30", "露神"),
    ):
        imported = db_client.post(
            "/api/v1/travel-reports/imports",
            headers={"X-CSRF-Token": login["csrf_token"]},
            files={
                "file": (
                    filename,
                    _source_workbook(billing_period=period, cost_center=cost_center),
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
            },
        )
        assert imported.status_code == 201

    all_reports = db_client.get("/api/v1/travel-reports").json()
    assert all_reports["total"] == 2
    assert {
        (period["billing_period_start"], period["billing_period_end"])
        for period in all_reports["filter_options"]["billing_periods"]
    } == {
        ("2026-08-01", "2026-08-31"),
        ("2026-09-01", "2026-09-30"),
    }
    assert set(all_reports["filter_options"]["cost_centers"]) == {"苍渊", "露神"}

    september_lushen = db_client.get(
        "/api/v1/travel-reports",
        params={
            "billing_period_start": "2026-09-01",
            "billing_period_end": "2026-09-30",
            "cost_center": "露神",
        },
    )
    assert september_lushen.status_code == 200
    assert september_lushen.json()["total"] == 1
    assert september_lushen.json()["items"][0]["cost_center"] == "露神"

    mismatched = db_client.get(
        "/api/v1/travel-reports",
        params={
            "billing_period_start": "2026-08-01",
            "billing_period_end": "2026-08-31",
            "cost_center": "露神",
        },
    )
    assert mismatched.status_code == 200
    assert mismatched.json()["total"] == 0
    assert mismatched.json()["items"] == []
    assert set(mismatched.json()["filter_options"]["cost_centers"]) == {"苍渊", "露神"}


def test_coach_cannot_read_or_import_travel_reports(
    db_client: TestClient,
    platform_users: dict[str, Any],
) -> None:
    login = _login(db_client, platform_users, "coach")
    assert db_client.get("/api/v1/travel-reports").status_code == 403
    denied = db_client.post(
        "/api/v1/travel-reports/imports",
        headers={"X-CSRF-Token": login["csrf_token"]},
        files={"file": ("billing.xlsx", _source_workbook())},
    )
    assert denied.status_code == 403
