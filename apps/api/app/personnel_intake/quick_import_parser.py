"""Bounded, deterministic pasted-table parsing. Never persists or logs input."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date


@dataclass
class ParsedImportRow:
    row_number: int
    room_code: str = ""
    room_type: str | None = None
    full_name: str = ""
    arrival_date: date | None = None
    departure_date: date | None = None
    job_text: str = ""
    identity_number: str = field(default="", repr=False)
    phone: str = field(default="", repr=False)
    errors: list[str] = field(default_factory=list)


_ALIASES = {
    "房号": "room_code", "房间号": "room_code", "房间": "room_code",
    "房型": "room_type", "房间类型": "room_type",
    "姓名": "full_name", "人员名字": "full_name", "人名": "full_name",
    "入住时间": "arrival_date", "入住日期": "arrival_date",
    "岗位": "job_text", "岗位(队伍)": "job_text", "岗位/队伍": "job_text",
    "身份": "job_text", "队伍": "job_text", "项目": "job_text",
    "离开时间": "departure_date", "离开日期": "departure_date",
    "退宿时间": "departure_date", "退宿日期": "departure_date",
    "退房日期": "departure_date", "退房时间": "departure_date",
    "身份证": "identity_number", "身份证号": "identity_number", "证件号码": "identity_number",
    "手机号": "phone", "手机": "phone", "手机号码": "phone",
}
_ROOM_TYPES = {"单人间": "single", "单人房": "single", "双人间": "double",
               "双人房": "double", "三人间": "triple", "三人房": "triple"}
_COLUMNS = ["room_code", "room_type", "full_name", "arrival_date", "job_text",
            "departure_date", "identity_number", "phone"]
_RESIDENT_STATUSES = {"常驻", "常住"}
# These endings identify plain-text field boundaries only. The service still
# determines the actual identity and whether the actor may assign it.
_SPACE_JOB_ENDINGS = (
    "队伍经理", "战队经理", "生活老师", "经理", "领队", "助教", "教练", "队员", "学员",
    "行政", "物业", "客服", "其他工作人员", "其他员工", "工作人员",
)
_SPACE_NON_JOB_TOKEN = re.compile(
    r"(?:[0-9].*[-/.年月日].*|[0-9]{6,}|常驻|常住|短住|暂住|长期|待定|未定|"
    r"今天|明天|后天|昨天|下周|下个月)"
)
_SPACE_GUIDANCE = "空格格式边界无法确认：请填写明确入住日期和岗位；姓名含空格或复杂内容请使用表格格式（制表符分列）"


def _space_values(line: str, errors: list[str]) -> dict[str, str] | None:
    # Linear token spans anchor both ends without collapsing internal job spaces
    # or backtracking over potentially 200000 characters of malformed input.
    tokens = list(re.finditer(r"\S+", line))
    end = len(tokens)
    tail = ""
    if tokens and tokens[-1].group() in _RESIDENT_STATUSES:
        tail = tokens[-1].group()
        end -= 1
    if (
        end < 6
        or not re.fullmatch(r"[0-9]{17}[0-9Xx]", tokens[end - 2].group())
        or not re.fullmatch(r"1[0-9]{10}", tokens[end - 1].group())
    ):
        errors.append(_SPACE_GUIDANCE)
        return None
    has_room_type = tokens[1].group() in _ROOM_TYPES
    name_index = 2 if has_room_type else 1
    body_start = name_index + 2
    body_end = end - 2
    if body_start >= body_end:
        errors.append(_SPACE_GUIDANCE)
        return None
    job_text = line[tokens[body_start].start():tokens[body_end - 1].end()]
    departure = ""
    if not job_text.endswith(_SPACE_JOB_ENDINGS):
        departure = tokens[body_end - 1].group()
        body_end -= 1
        job_text = line[tokens[body_start].start():tokens[body_end - 1].end()]
        if body_start >= body_end or not job_text.endswith(_SPACE_JOB_ENDINGS):
            errors.append(_SPACE_GUIDANCE)
            return None
    if any(_SPACE_NON_JOB_TOKEN.fullmatch(token) for token in job_text.split()):
        errors.append("岗位中含额外日期或住宿状态，请使用表格格式明确分列")
        return None
    if tail:
        if departure:
            errors.append("离开日期与末尾常驻或常住标记重复或冲突，请仅保留一处")
            return None
        departure = tail
    return {
        "room_code": tokens[0].group(),
        "room_type": tokens[1].group() if has_room_type else "",
        "full_name": tokens[name_index].group(),
        "arrival_date": tokens[name_index + 1].group(),
        "job_text": job_text,
        "departure_date": departure,
        "identity_number": tokens[end - 2].group(),
        "phone": tokens[end - 1].group(),
    }


def _date(value: str, label: str, errors: list[str]) -> date | None:
    if not value:
        return None
    if not re.fullmatch(r"[0-9]{4}[-/][0-9]{1,2}[-/][0-9]{1,2}", value):
        errors.append(f"{label}应为四位年份的年月日，例如 2026/11/5")
        return None
    try:
        year, month, day = (int(part) for part in re.split(r"[-/]", value))
        return date(year, month, day)
    except ValueError:
        errors.append(f"{label}不是有效日期")
        return None


def parse_table(text: str, *, import_date: date) -> list[ParsedImportRow]:
    if len(text) > 200000:
        raise ValueError("粘贴内容过长，最多 200000 个字符")
    rows: list[ParsedImportRow] = []
    header: list[str] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        if not raw.strip():
            continue
        line = unicodedata.normalize("NFKC", raw)
        plain_space = "\t" not in line and "|" not in line
        if "\t" in line:
            cells = [cell.strip() for cell in line.split("\t")]
        else:
            cells = [cell.strip() for cell in line.strip().removeprefix("|").removesuffix("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        if not rows and header is None and sum(cell in _ALIASES for cell in cells) >= 2:
            if any(cell not in _ALIASES for cell in cells):
                raise ValueError("表头含无法识别的列，请使用房号、房型、姓名、入住日期、岗位、退宿日期、身份证号、手机号")
            header = [_ALIASES[cell] for cell in cells]
            if len(set(header)) != len(header):
                raise ValueError("表头包含重复字段")
            if not {"room_code", "full_name", "job_text", "identity_number", "phone"} <= set(header):
                raise ValueError("表头缺少房号、姓名、岗位、身份证号或手机号")
            continue
        if len(rows) >= 100:
            raise ValueError("每次最多导入 100 行，请分批处理")
        row = ParsedImportRow(row_number=number)
        rows.append(row)
        if (
            header is None
            and len(cells) in (8, 9)
            and cells[-1] in _RESIDENT_STATUSES
            and cells[-4] == ""
            and re.fullmatch(r"[0-9]{17}[0-9Xx]", cells[-3])
            and re.fullmatch(r"1[0-9]{10}", cells[-2])
            and (len(cells) == 8 or cells[1] in _ROOM_TYPES)
        ):
            # A separate resident-status cell may follow the canonical layout,
            # but cannot override a supplied departure or an explicit header.
            cells = cells[:-1]
        columns = header or (_COLUMNS if len(cells) == 8 else [c for c in _COLUMNS if c != "room_type"])
        if plain_space:
            if header is not None:
                row.errors.append("已有表头时请使用表格格式并保留分隔符，不能混用空格格式")
                continue
            space_values = _space_values(line, row.errors)
            if space_values is None:
                continue
            values = space_values
        else:
            if len(cells) != len(columns):
                row.errors.append("列数不符，请保留空列；无表头时应为 8 列（或省略房型的 7 列）")
                continue
            if (
                header is None
                and cells[-1] in _RESIDENT_STATUSES
                and re.fullmatch(r"[0-9]{17}[0-9Xx]", cells[-3])
                and re.fullmatch(r"1[0-9]{10}", cells[-2])
                and (len(cells) == 7 or cells[1] in _ROOM_TYPES)
            ):
                # The departure cell was omitted, not left empty. Only this
                # independently anchored identity/mobile/status layout is safe.
                columns = [column for column in columns if column != "departure_date"]
                values = dict(zip(columns, cells[:-1], strict=True))
                values["departure_date"] = cells[-1]
            else:
                values = dict(zip(columns, cells, strict=True))
        row.room_code = re.sub(r"\s+", "", values.get("room_code", "")).upper()
        row.full_name = values.get("full_name", "")
        row.job_text = values.get("job_text", "")
        row.identity_number = values.get("identity_number", "").upper()
        row.phone = values.get("phone", "")
        room_type = values.get("room_type", "")
        row.room_type = _ROOM_TYPES.get(room_type)
        if room_type and row.room_type is None:
            row.errors.append("房型仅支持单人间、双人间、三人间；其他房型请留空并核对现有房间")
        arrival = values.get("arrival_date", "")
        row.arrival_date = _date(arrival, "入住日期", row.errors) if arrival else import_date
        if plain_space and row.arrival_date is None:
            row.errors.append(_SPACE_GUIDANCE)
        departure = values.get("departure_date", "")
        row.departure_date = (
            None if departure in _RESIDENT_STATUSES else _date(departure, "离开日期", row.errors)
        )
        for key, label in (("room_code", "房号"), ("full_name", "姓名"), ("job_text", "岗位"),
                           ("identity_number", "身份证号"), ("phone", "手机号")):
            if not values.get(key):
                row.errors.append(f"缺少{label}")
    if not rows:
        raise ValueError("请粘贴至少一行人员资料")
    return rows
