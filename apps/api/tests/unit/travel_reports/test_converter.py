from __future__ import annotations

from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from app.travel_reports.converter import (
    MAX_BUSINESS_ROWS,
    TravelReportWorkbookError,
    convert_travel_report,
    formula_results_are_cached,
)

DOMESTIC_HEADERS = [
    "序号", "费用承担部门", "乘机人", "航线", "航班号", "出发时间",
    "标准票面价", "优惠金额(大客户/政采)", "行程单票面价", "燃油费",
    "机建费", "改签费", "退票费", "代订机票费", "企业应付金额",
    "出差申请单号", "员工备注",
]
INTERNATIONAL_HEADERS = [
    "序号", "费用承担部门", "乘机人", "乘机人中文名", "航线", "航班号",
    "出发时间", "标准票面价", "税费", "升舱费", "改签费", "退票费",
    "服务费", "企业应付金额", "出差申请单号", "员工备注",
]
TRAIN_HEADERS = [
    "序号", "费用承担部门", "乘客", "出发时间", "出发站点", "到达站点",
    "车次", "票面价", "改签费", "退票费", "出票服务费", "代打费",
    "企业应付金额", "出差申请单号", "员工备注",
]


def _source_workbook(*, unsupported: bool = False, missing_domestic_amount: bool = False) -> bytes:
    workbook = Workbook()
    summary = workbook.active
    summary.title = "账单汇总表"
    summary.append(["杭州飞巴旅行社有限公司对账单"])
    summary.append(["客户信息"])
    summary.append(["公司名称", "测试文化创意有限公司"])
    summary.append(["账单周期", "2026.08.01-2026.08.31"])
    summary.append(["成本中心名称", "苍渊"])
    summary.append(["类目", "金额"])
    summary.append(["本期账单消费金额", 1405])

    detail = workbook.create_sheet("汇总明细")
    detail.append(["序号", "所属业务", "企业应付金额"])
    for index, (business, amount) in enumerate(
        [("国内机票", 1150), ("国内机票", -950), ("国际机票", 1100),
         ("火车", 505), ("火车", -400)],
        start=1,
    ):
        detail.append([index, business, amount])

    domestic = workbook.create_sheet("国内机票")
    headers = [value for value in DOMESTIC_HEADERS if not (
        missing_domestic_amount and value == "企业应付金额"
    )]
    domestic.append(headers)
    domestic_rows = [
        [1, "和平项目组", "汪妍", "成都-上海", "MU5410", "2026-08-01 12:25",
         1000, 0, 1000, 100, 50, 0, 0, 0, 1150, "审批-1", ""],
        [2, "和平项目组", "汪妍", "成都-上海", "MU5410", "2026-08-01 12:25",
         -1000, 0, -1000, -100, -50, 0, 200, 0, -950, "", "=HYPERLINK(\"x\")"],
    ]
    for row in domestic_rows:
        if missing_domestic_amount:
            row = row[:14] + row[15:]
        domestic.append(row)

    international = workbook.create_sheet("国际机票")
    international.append(INTERNATIONAL_HEADERS)
    international.append([
        1, "赛事部", "WANG/KE", "王科", "上海-巴黎", "MU553", "2026-08-02 00:15",
        1000, 100, 0, 0, 0, 0, 1100, "审批-2", "",
    ])

    train = workbook.create_sheet("火车")
    train.append(TRAIN_HEADERS)
    train.append([
        1, "媒介组", "熊政", "2026-08-03 19:44:00", "成都东", "上海虹桥", "D974",
        500, 0, 0, 5, 0, 505, "审批-3", "",
    ])
    train.append([
        2, "媒介组", "熊政", "2026-08-03 19:44:00", "成都东", "上海虹桥", "D974",
        -500, 0, 100, 0, 0, -400, "", "退票",
    ])

    if unsupported:
        hotel = workbook.create_sheet("国内酒店")
        hotel.append(["序号", "企业应付金额"])
        hotel.append([1, 200])

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def test_converts_supported_businesses_preserves_refunds_and_recalculates_totals() -> None:
    result = convert_travel_report(_source_workbook(), "飞巴商旅账单.xlsx")

    assert result.domestic_air_count == 2
    assert result.international_air_count == 1
    assert result.train_count == 2
    assert result.source_total_amount == Decimal("1405.00")
    assert result.converted_total_amount == Decimal("1405.00")
    assert result.warnings == ()
    assert result.converted_filename == "飞巴商旅26年8月账单-苍渊.xlsx"
    assert formula_results_are_cached(result.converted_content) is True

    workbook = load_workbook(BytesIO(result.converted_content), data_only=False)
    sheet = workbook["Sheet1"]
    assert sheet.calculate_dimension() == "A1:AC11"
    assert sheet["F4"].value == -1000
    assert sheet["L4"].value == 200
    assert sheet["N5"].value == "=SUM(N3:N4)"
    assert sheet["O4"].value == "'=HYPERLINK(\"x\")"
    assert sheet["Q7"].value == "火车明细报表 - 苍渊"
    assert sheet["AB11"].value == "=SUM(AB9:AB10)"
    assert sheet.max_column == 29
    workbook.close()

    cached_workbook = load_workbook(BytesIO(result.converted_content), data_only=True)
    cached_sheet = cached_workbook["Sheet1"]
    assert Decimal(str(cached_sheet["N5"].value)) == Decimal("200.00")
    assert Decimal(str(cached_sheet["AB4"].value)) == Decimal("1100.00")
    assert Decimal(str(cached_sheet["AB11"].value)) == Decimal("105.00")
    cached_workbook.close()


def test_warns_when_source_contains_a_business_without_a_target_block() -> None:
    result = convert_travel_report(_source_workbook(unsupported=True), "billing.xlsx")

    assert result.warnings == (
        "国内酒店有 1 条明细，当前目标模板尚未定义该区块，已保留在原始文件中。",
    )


def test_rejects_missing_required_headers_with_a_clear_error() -> None:
    with pytest.raises(TravelReportWorkbookError) as raised:
        convert_travel_report(
            _source_workbook(missing_domestic_amount=True),
            "billing.xlsx",
        )

    assert raised.value.code == "headers"
    assert "企业应付金额" in str(raised.value)


@pytest.mark.parametrize("row_number", [MAX_BUSINESS_ROWS + 2, MAX_BUSINESS_ROWS + 3, 1_048_576])
def test_rejects_sparse_order_past_the_supported_row_range(row_number: int) -> None:
    workbook = load_workbook(BytesIO(_source_workbook()))
    worksheet = workbook["国内机票"]
    worksheet.cell(row_number, 1, "不能遗漏的订单")
    worksheet.cell(row_number, 3, "测试乘客")
    worksheet.cell(row_number, 15, 100)
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    assert len(output.getvalue()) < 100_000
    with pytest.raises(TravelReportWorkbookError) as error:
        convert_travel_report(output.getvalue(), "sparse.xlsx")
    assert error.value.code == "rows"
    assert str(row_number) in str(error.value)


def test_allows_formatting_only_rows_beyond_the_business_limit() -> None:
    workbook = load_workbook(BytesIO(_source_workbook()))
    worksheet = workbook["国内机票"]
    worksheet.cell(MAX_BUSINESS_ROWS + 3, 1).number_format = "0.00"
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    result = convert_travel_report(output.getvalue(), "formatted.xlsx")
    assert result.domestic_air_count == 2
    assert result.converted_total_amount == Decimal("1405.00")
