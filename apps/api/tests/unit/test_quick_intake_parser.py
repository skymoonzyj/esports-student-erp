from datetime import date

import pytest


def parse(text: str):
    from app.personnel_intake.quick_import_parser import parse_table

    return parse_table(text, import_date=date(2026, 11, 7))


def test_tsv_keeps_empty_departure_without_shifting_identity_or_phone():
    row = parse("4-101\t单人间\t测试甲\t2026/11/5\t测试项目 助教\t\t110101199001010015\t19990000001")[0]
    assert row.room_code == "4-101"
    assert row.room_type == "single"
    assert row.arrival_date == date(2026, 11, 5)
    assert row.departure_date is None
    assert row.identity_number == "110101199001010015"
    assert row.phone == "19990000001"
    assert row.errors == []


def test_markdown_header_separator_reordered_and_empty_arrival():
    rows = parse("| 姓名 | 房号 | 身份证号 | 手机号 | 入住时间 | 岗位（队伍） | 退宿时间 |\n"
                 "| --- | --- | --- | --- | --- | --- | --- |\n"
                 "| 测试乙 | A-301 | 110101199001010015 | 19990000001 | | 助教 | |").copy()
    assert len(rows) == 1
    assert rows[0].row_number == 3
    assert rows[0].arrival_date == date(2026, 11, 7)
    assert rows[0].room_type is None
    assert rows[0].job_text == "助教"


def test_headerless_markdown_and_fullwidth():
    row = parse("| ａ－１０１ | 双人房 | 测试丙 | ２０２６／１１／７ | 学员 | 2027-01-07 | １１０１０１１９９００１０１００１５ | １９９９００００００１ |")[0]
    assert row.room_code == "A-101"
    assert row.room_type == "double"
    assert row.departure_date == date(2027, 1, 7)
    assert row.phone == "19990000001"


def test_seven_columns_without_room_type():
    row = parse("4-101\t测试丁\t\t教练\t\t110101199001010015\t19990000001")[0]
    assert row.full_name == "测试丁"
    assert row.arrival_date == date(2026, 11, 7)
    assert row.room_type is None


@pytest.mark.parametrize("day", ["26/11/5", "20261-11-05", "2026-02-30", "明天"])
def test_invalid_dates_are_not_defaulted(day):
    row = parse(f"4-101\t测试丁\t{day}\t教练\t\t110101199001010015\t19990000001")[0]
    assert row.errors
    assert row.arrival_date is None


def test_extra_cells_and_unknown_header_do_not_silently_shift():
    row = parse("4-101\t单人间\t测试\t\t教练\t\t110101199001010015\t19990000001\t多余")[0]
    assert row.errors
    with pytest.raises(ValueError):
        parse("房号\t姓名\t身份证号\t未知列\n4-101\t测试\t110101199001010015\t1")


def test_duplicate_headers_rejected():
    with pytest.raises(ValueError):
        parse("房号\t姓名\t身份证号\t手机号\t岗位\t入住日期\t入住时间\n1\t测试\tX\t1\t助教\t\t")


def test_limits_reject_entire_batch():
    line = "4-101\t测试丁\t\t教练\t\t110101199001010015\t19990000001"
    assert len(parse("\n".join([line] * 100))) == 100
    with pytest.raises(ValueError):
        parse("\n".join([line] * 101))
    with pytest.raises(ValueError):
        parse("x" * 200001)
    with pytest.raises(ValueError):
        parse("   ")


def test_phone_annotation_preserved_for_validation_not_stripped():
    row = parse("4-101\t测试丁\t\t教练\t\t110101199001010015\t19990000001 后面说明")[0]
    assert row.phone == "19990000001 后面说明"


@pytest.mark.parametrize("separator", [" ", "   ", "\u3000"])
def test_plain_space_row_has_anchored_fields_and_preserves_job_spaces(separator):
    row = parse(separator.join([
        "4-401", "单人间", "测试甲", "2026/11/5", "无畏契约 经理",
        "110101199001010015", "19990000001", "常驻",
    ]))[0]
    assert row.errors == []
    assert row.room_code == "4-401"
    assert row.room_type == "single"
    assert row.full_name == "测试甲"
    assert row.arrival_date == date(2026, 11, 5)
    assert row.departure_date is None
    assert row.job_text == "无畏契约 经理"
    assert row.identity_number == "110101199001010015"
    assert row.phone == "19990000001"


@pytest.mark.parametrize("departure", ["", "2026/12/7", "常驻", "常住"])
def test_plain_space_optional_departure_without_room_type(departure):
    row = parse(f"A-401 测试乙 2026-11-05 测试项目  助教 {departure} "
                "110101199001010015 19990000001")[0]
    assert row.errors == []
    assert row.room_type is None
    assert row.full_name == "测试乙"
    assert row.job_text == "测试项目  助教"
    assert row.departure_date == (date(2026, 12, 7) if departure == "2026/12/7" else None)


@pytest.mark.parametrize("departure", ["常驻", "常住"])
@pytest.mark.parametrize("separator", ["\t", "|"])
def test_structured_departure_resident_aliases_mean_no_end_date(departure, separator):
    row = parse(separator.join([
        "4-401", "单人间", "测试甲", "2026/11/5", "经理", departure,
        "110101199001010015", "19990000001",
    ]))[0]
    assert row.errors == []
    assert row.departure_date is None


@pytest.mark.parametrize("arrival", ["26/11/5", "2026/02/30", "明天", "常驻"])
def test_plain_space_invalid_arrival_is_never_defaulted(arrival):
    row = parse(f"4-401 测试甲 {arrival} 经理 110101199001010015 19990000001")[0]
    assert row.errors
    assert row.arrival_date is None


@pytest.mark.parametrize("departure", [
    "26/11/5", "2026/02/30", "明天", "20261107", "随便一天", "未知离开时间",
])
def test_plain_space_supplied_invalid_departure_is_not_absorbed_into_job(departure):
    row = parse(f"4-401 测试甲 2026/11/5 经理 {departure} "
                "110101199001010015 19990000001")[0]
    assert row.errors
    assert row.departure_date is None


def test_plain_space_complete_numeric_room_code_is_supported():
    row = parse("401 测试甲 2026/11/5 经理 110101199001010015 19990000001")[0]
    assert row.errors == []
    assert row.room_code == "401"


def test_plain_space_unknown_job_boundary_requires_table_format():
    row = parse("4-401 测试甲 2026/11/5 测试项目 未识别岗位 "
                "110101199001010015 19990000001")[0]
    assert row.errors
    assert any("表格" in error for error in row.errors)


@pytest.mark.parametrize("tail", ["常住", "常驻 常驻", "常驻 备注", "短住", "备注"])
def test_plain_space_conflicting_repeated_or_unknown_tail_is_rejected(tail):
    row = parse("4-401 测试甲 2026/11/5 经理 2026/12/7 "
                f"110101199001010015 19990000001 {tail}")[0]
    assert row.errors


@pytest.mark.parametrize("job_and_departure", [
    "经理 2026/12/7 2026/12/8", "经理 常驻 常住", "经理 2026/12/7 助教",
    "经理 短住", "经理 明天 助教",
])
def test_plain_space_extra_date_or_status_token_is_rejected(job_and_departure):
    row = parse(f"4-401 测试甲 2026/11/5 {job_and_departure} "
                "110101199001010015 19990000001")[0]
    assert row.errors


def test_plain_space_repeated_resident_status_before_and_after_identity_is_rejected():
    row = parse("4-401 测试甲 2026/11/5 经理 常驻 "
                "110101199001010015 19990000001 常住")[0]
    assert row.errors


@pytest.mark.parametrize("name_and_arrival", ["测试甲", "测试 甲 2026/11/5"])
def test_plain_space_missing_arrival_or_multitoken_name_gives_table_guidance(name_and_arrival):
    row = parse(f"4-401 {name_and_arrival} 经理 110101199001010015 19990000001")[0]
    assert row.errors
    assert row.arrival_date is None
    assert any("表格" in error for error in row.errors)


@pytest.mark.parametrize("identity_and_phone", [
    "11010119900101001 19990000001", "110101199001010015 1380000000",
    "110101199001010015 19990000001 后面说明",
])
def test_plain_space_malformed_identity_or_phone_has_no_silent_shift(identity_and_phone):
    row = parse(f"4-401 测试甲 2026/11/5 经理 {identity_and_phone}")[0]
    assert row.errors


def test_plain_space_row_numbers_and_batch_limit_are_preserved():
    line = "4-401 测试甲 2026/11/5 经理 110101199001010015 19990000001 常住"
    rows = parse("\n" + "\n".join([line] * 100))
    assert len(rows) == 100
    assert rows[0].row_number == 2
    assert all(not row.errors for row in rows)
    with pytest.raises(ValueError):
        parse("\n".join([line] * 101))


@pytest.mark.parametrize("separator", ["\t", "|"])
@pytest.mark.parametrize("room_type", [[], ["单人间"]])
@pytest.mark.parametrize("status", ["常驻", "常住"])
def test_headerless_structured_tail_status_with_omitted_departure_column(
    separator, room_type, status,
):
    row = parse(separator.join([
        "4-401", *room_type, "测试甲", "2026/11/5", "无畏契约 经理",
        "110101199001010015", "19990000001", status,
    ]))[0]
    assert row.errors == []
    assert row.full_name == "测试甲"
    assert row.room_type == ("single" if room_type else None)
    assert row.job_text == "无畏契约 经理"
    assert row.departure_date is None
    assert row.identity_number == "110101199001010015"
    assert row.phone == "19990000001"


@pytest.mark.parametrize("separator", ["\t", "|"])
@pytest.mark.parametrize("departure", ["2026/12/7", "明天", "常驻"])
def test_structured_departure_plus_tail_status_cannot_shift_columns(separator, departure):
    row = parse(separator.join([
        "4-401", "单人间", "测试甲", "2026/11/5", "经理", departure,
        "110101199001010015", "19990000001", "常驻",
    ]))[0]
    assert row.errors


@pytest.mark.parametrize("tail", ["常驻 常住", "备注", "短住"])
def test_headerless_structured_unknown_tail_cannot_be_reinterpreted(tail):
    row = parse("4-401\t单人间\t测试甲\t2026/11/5\t经理\t"
                f"110101199001010015\t19990000001\t{tail}")[0]
    assert row.errors


def test_explicit_header_mapping_is_not_reordered_for_trailing_status():
    row = parse("房号\t房型\t姓名\t入住日期\t岗位\t身份证号\t手机号\t退宿日期\n"
                "4-401\t单人间\t测试甲\t2026/11/5\t经理\t110101199001010015\t19990000001\t常驻")[0]
    assert row.errors == []
    assert row.identity_number == "110101199001010015"
    assert row.phone == "19990000001"
    assert row.departure_date is None


def test_plain_space_row_cannot_override_explicit_table_header_order():
    row = parse("姓名\t房号\t入住日期\t岗位\t身份证号\t手机号\n"
                "4-401 测试甲 2026/11/5 经理 110101199001010015 19990000001")[0]
    assert row.errors
    assert any("表格" in error for error in row.errors)


@pytest.mark.parametrize("job", ["物业", "客服", "其他工作人员", "其他员工", "工作人员"])
def test_plain_space_all_supported_staff_endings_are_boundaries(job):
    row = parse(f"4-401 测试甲 2026/11/5 测试项目 {job} "
                "110101199001010015 19990000001")[0]
    assert row.errors == []
    assert row.job_text == f"测试项目 {job}"


@pytest.mark.parametrize("separator", ["\t", "|"])
@pytest.mark.parametrize("room_type", [[], ["单人间"]])
@pytest.mark.parametrize("status", ["常驻", "常住"])
@pytest.mark.parametrize("name", ["测试甲", "  测试甲  "])
def test_headerless_empty_departure_column_with_independent_tail_status(
    separator, room_type, status, name,
):
    row = parse(separator.join([
        "4-401", *room_type, name, "2026/11/5", "测试项目 经理", "  ",
        "110101199001010015", "19990000001", status,
    ]))[0]
    assert row.errors == []
    assert row.full_name == "测试甲"
    assert row.room_code == "4-401"
    assert row.room_type == ("single" if room_type else None)
    assert row.arrival_date == date(2026, 11, 5)
    assert row.departure_date is None
    assert row.job_text == "测试项目 经理"
    assert row.identity_number == "110101199001010015"
    assert row.phone == "19990000001"


@pytest.mark.parametrize("separator", ["\t", "|"])
@pytest.mark.parametrize("departure", ["2026/12/7", "明天", "常驻", "常住"])
def test_no_room_type_nonempty_departure_cannot_be_overridden_by_tail_status(
    separator, departure,
):
    row = parse(separator.join([
        "4-401", "测试甲", "2026/11/5", "经理", departure,
        "110101199001010015", "19990000001", "常驻",
    ]))[0]
    assert row.errors


@pytest.mark.parametrize("room_type", [[], ["单人间"]])
@pytest.mark.parametrize("tail", ["备注", "短住", "常驻 常住"])
def test_empty_departure_does_not_allow_unknown_extra_column(room_type, tail):
    row = parse("\t".join([
        "4-401", *room_type, "测试甲", "2026/11/5", "经理", "",
        "110101199001010015", "19990000001", tail,
    ]))[0]
    assert row.errors


@pytest.mark.parametrize("identity,phone", [
    ("11010119900101001", "19990000001"),
    ("110101199001010015", "1380000000"),
    ("110101199001010015", "19990000001 备注"),
])
def test_empty_departure_tail_status_requires_complete_identity_and_phone(identity, phone):
    row = parse("4-401\t单人间\t测试甲\t2026/11/5\t经理\t\t"
                f"{identity}\t{phone}\t常驻")[0]
    assert row.errors


def test_empty_departure_extra_tail_status_cannot_override_explicit_header():
    row = parse("房号\t房型\t姓名\t入住日期\t岗位\t退宿日期\t身份证号\t手机号\n"
                "4-401\t单人间\t测试甲\t2026/11/5\t经理\t\t"
                "110101199001010015\t19990000001\t常驻")[0]
    assert row.errors
