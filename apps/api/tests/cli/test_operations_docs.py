from __future__ import annotations

import re
import shlex
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
HEADING_PATTERN = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
NON_CURRENT_PREFIX_PATTERN = re.compile(
    r"(?:"
    r"(?:尚未|仍未|并未|未|并非|不是|没有)"
    r"(?:声称|宣称|写成|描述为|视为|认为)?[‘’“”\"'`：:\s]*|"
    r"(?:不得|禁止|不能|不可|不应)(?:据此)?"
    r"(?:声称|宣称|写成|描述为|视为|认为)?[‘’“”\"'`：:\s]*|"
    r"不表示[‘’“”\"'`：:\s]*|"
    r"(?:历史|旧版|此前)(?:记录|文档|版本)?(?:曾经?)?"
    r"(?:误写|写作|声称|描述为|称为)?[‘’“”\"'`：:\s]*|"
    r"曾(?:经)?(?:误写|写作|声称|描述为)?[‘’“”\"'`：:\s]*|"
    r"误写(?:为)?[‘’“”\"'`：:\s]*|"
    r"无(?:任何|可维护的|可用的)?\s*"
    r")$"
)
NON_CURRENT_SUFFIX_PATTERN = re.compile(
    r"^[‘’“”\"'`]*[\s，,:：]*(?:"
    r"(?:的)?(?:这一?|该)?(?:说法|表述|描述)(?:已)?(?:被)?(?:禁止|否定|纠正)|"
    r"(?:已)?(?:被)?(?:否定|纠正)|"
    r"(?:仍)?(?:延期|待后续)[^。；！？]{0,12}"
    r"(?:实现|开发|开放|完成|上线)(?=[。；！？]|$)|"
    r"(?:是|为|系|属于|来自)(?:历史|旧版|此前)(?:记录|版本|误写|表述)|"
    r"(?:并非|不是)(?:当前|现有|本单元)?(?:状态|事实|实现|结论)|"
    r"(?:现)?已纠正"
    r")"
)


def _text(path: str) -> str:
    return (REPOSITORY_ROOT / path).read_text(encoding="utf-8")


def _markdown_section(document: str, title: str) -> str:
    lines = document.splitlines()
    start: int | None = None
    level: int | None = None
    for index, line in enumerate(lines):
        match = HEADING_PATTERN.match(line)
        if match is not None and match.group("title").strip(" #") == title:
            start = index
            level = len(match.group("marks"))
            break

    assert start is not None and level is not None, f"missing Markdown section: {title}"
    end = len(lines)
    for index in range(start + 1, len(lines)):
        match = HEADING_PATTERN.match(lines[index])
        if match is not None and len(match.group("marks")) <= level:
            end = index
            break
    return "\n".join(lines[start:end])


def _markdown_lead(document: str) -> str:
    lines = document.splitlines()
    end = next(
        (index for index, line in enumerate(lines) if line.startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[:end])


def _line_asserts_claim(line: str, claim: str) -> bool:
    for match in re.finditer(re.escape(claim), line):
        prefix = line[: match.start()]
        suffix = line[match.end() :]
        if NON_CURRENT_PREFIX_PATTERN.search(prefix) is not None:
            continue
        if NON_CURRENT_SUFFIX_PATTERN.search(suffix) is not None:
            continue
        return True
    return False


def test_schema_manifest_tracks_current_head_and_all_required_tables() -> None:
    manifest = _text("scripts/db-schema-manifest.sh")
    assert "EXPECTED_ALEMBIC_HEAD=0011_housing_real_name_reporting" in manifest
    match = re.search(r"REQUIRED_TABLES=\(\n(?P<body>.*?)\n\)", manifest, re.DOTALL)
    assert match is not None
    required_tables = shlex.split(match.group("body"))
    assert len(required_tables) == 49
    assert {
        "service_accounts",
        "payments",
        "payment_import_events",
        "payment_conflicts",
        "payment_refunds",
        "admission_cases",
        "admission_case_payments",
        "enrollments",
        "enrollment_events",
        "enrollment_withdrawals",
        "evaluation_bridge_requests",
        "evaluation_bridge_tickets",
        "evaluation_staff_roles",
        "cohort_staff_assignments",
        "cohort_staff_assignment_permissions",
        "student_game_accounts",
        "student_auxiliary_phones",
        "project_analysis_profiles",
        "evaluation_media_blobs",
        "evaluation_media_assets",
        "housing_buildings",
        "housing_floors",
        "housing_rooms",
        "housing_import_batches",
        "housing_report_batches",
        "housing_report_batch_scopes",
    } <= set(required_tables)


def test_complete_gate_rehearses_backup_and_restore() -> None:
    check_script = _text("scripts/check.sh")
    assert "scripts/backup-db.sh" in check_script
    assert "scripts/restore-db.sh" in check_script
    assert "erp_e2e_restore_test" in check_script


def test_production_image_contains_admissions_runtime_but_not_e2e_seed() -> None:
    dockerfile = _text("apps/api/Dockerfile")
    production = dockerfile.split("FROM base AS production", maxsplit=1)[1].split(
        "FROM base AS e2e", maxsplit=1
    )[0]
    assert "COPY app/admissions ./app/admissions" in production
    assert "COPY app/portal ./app/portal" in production
    assert "COPY app ./app" not in production
    assert "COPY app/cli ./app/cli" not in production
    assert "seed_e2e.py" not in production


def test_readme_documents_admissions_operations_and_current_boundaries() -> None:
    readme = _text("README.md")
    required_operator_guidance = (
        "## 招生报名与付款运营",
        "创建、轮换或停用机器凭证",
        "签名规范串",
        "手工登记付款",
        "退款不自动取消报名",
        "按剩余日期退款",
        "退还全部剩余已付款",
        "回滚应用代码",
        "不回退已经提交的付款数据",
    )
    for phrase in required_operator_guidance:
        assert phrase in readme

    required_deferred_boundaries = (
        "截图上传与自动评价",
        "本地实名入住报送",
        "公安在线自动报送",
        "住宿生命周期四视口浏览器旅程已通过",
        "支付平台适配器",
        "录像或 GPU 分析",
    )
    for phrase in required_deferred_boundaries:
        assert phrase in readme


@pytest.mark.parametrize(
    "required_text",
    [
        "教练下一期学员名单",
        "家庭教学与多学员切换",
        "学员本人教学信息",
        "本人付款与退款",
        "学习评价将在下一阶段开放",
        "Docker/NAS 打包仍在最终阶段",
    ],
)
def test_readme_documents_portal_operations(required_text: str) -> None:
    assert required_text in _text("README.md")


def test_design_status_does_not_claim_container_gate_before_it_runs() -> None:
    design = _text(
        "docs/superpowers/specs/2026-07-27-admissions-teaching-operations-design.md"
    )
    assert "**状态：** 阶段 1B 第二模块待容器门禁复核" in design


def test_readme_documents_local_evaluation_integration_only() -> None:
    readme = _text("README.md")
    required = {
        "评价中心页面整合",
        "game-score-tool",
        "http://localhost:8010",
        "EVALUATION_TOOL_SERVICE_TOKEN",
        "SCORE_TOOL_SESSION_SECRET",
        "scripts/run-evaluation-page-e2e.sh",
        "截图、评分、日报、周报和营期问卷尚未开放",
        "Docker/NAS 仍延期到全部 ERP 项目完成后",
    }
    assert all(phrase in readme for phrase in required)
    assert "截图上传与自动评价、游戏 ID 绑定" not in readme
    assert "截图上传与自动评价、游戏 ID 绑定、住宿房态" not in readme

    forbidden_completion_claims = {
        "截图上传已完成",
        "评分处理已完成",
        "录像分析已完成",
        "GPU 服务已完成",
        "Docker/NAS 已完成",
        "NAS 部署已完成",
    }
    assert forbidden_completion_claims.isdisjoint(readme.splitlines())


def test_readme_documents_evaluation_data_and_media_foundation_boundaries() -> None:
    readme = _text("README.md")
    required = {
        "游戏 ID、辅助手机号、工作人员服务历史和手动分析配置已可用",
        "私有媒体存储、校验和读取原语",
        "python -m app.cli.evaluation_media_manifest",
        "截图上传、评价任务、OCR/评分、审核和发布仍未开放",
        "旧 game-score-tool 数据未读取、未迁移、未修改或删除",
        "Docker/NAS 仍延期到全部 ERP 项目完成后",
    }
    assert all(phrase in readme for phrase in required)


def test_evaluation_e2e_launcher_runs_both_local_specs_without_docker() -> None:
    launcher = _text("scripts/run-evaluation-page-e2e.sh")
    assert "E2E_PYTHON_BIN" in launcher
    assert "python_bin\" -m app.cli.e2e_database_safety" in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index(
        "-m alembic upgrade head"
    )
    assert "evaluation-page-integration.spec.ts" in launcher
    assert "evaluation-data-media-foundation.spec.ts" in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()


def test_housing_room_status_launcher_runs_four_local_projects_without_docker() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-room-status-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8001" in launcher
    assert "E2E_WEB_PORT:-5176" in launcher
    assert "housing-room-status-foundation.spec.ts" in launcher
    assert "python_bin\" -m app.cli.seed_e2e" in launcher
    assert "python_bin\" -m app.cli.e2e_database_safety" in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index(
        "-m alembic upgrade head"
    )
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()
    readme = _text("README.md")
    assert "有效连接目标的数据库名严格为 `erp_e2e` 或以 `_test` 结尾" in readme
    assert "拒绝数据库目标选择器查询参数" in readme
    assert "凭据中的 `@` 必须写成百分号编码 `%40`" in readme
    assert "无 authority 的本地 PostgreSQL socket URL 仍可使用" in readme


def test_housing_occupancy_launcher_runs_both_specs_for_four_local_projects() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-occupancy-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8002" in launcher
    assert "E2E_WEB_PORT:-5177" in launcher
    assert "housing-occupancy-lifecycle.spec.ts" in launcher
    assert "housing-occupancy-revocation.spec.ts" in launcher
    assert '"$python_bin" -m app.cli.seed_e2e' in launcher
    assert "seed_housing_portal_e2e" not in launcher
    assert '"$python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index(
        "-m alembic upgrade head"
    )
    assert 'E2E_API_URL="$api_origin"' in launcher
    assert 'E2E_BASE_URL="$web_origin"' in launcher
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()


def test_housing_reporting_launcher_runs_both_specs_for_four_local_projects() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-reporting-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8004" in launcher
    assert "E2E_WEB_PORT:-5179" in launcher
    assert "housing-reporting-lifecycle.spec.ts" in launcher
    assert "housing-reporting-authorization.spec.ts" in launcher
    assert '"$python_bin" -m app.cli.seed_e2e' in launcher
    assert '"$python_bin" -m app.cli.seed_housing_reporting_e2e' in launcher
    assert launcher.index("-m app.cli.seed_e2e") < launcher.index(
        "-m app.cli.seed_housing_reporting_e2e"
    )
    assert '"$python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index(
        "-m alembic upgrade head"
    )
    assert 'E2E_PYTHON_BIN="$python_bin"' in launcher
    assert 'E2E_API_URL="$api_origin"' in launcher
    assert 'E2E_BASE_URL="$web_origin"' in launcher
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()
    specs = "\n".join(
        _text(path)
        for path in (
            "apps/web/tests/housing-reporting-lifecycle.spec.ts",
            "apps/web/tests/housing-reporting-authorization.spec.ts",
        )
    )
    assert "waitForTimeout" not in specs
    assert "networkidle" not in specs


def test_readme_documents_local_reporting_gate_and_deployment_deferrals() -> None:
    readme = _text("README.md")
    boundary = _markdown_section(readme, "住宿生命周期与个人住宿门户")
    required_local_reporting = {
        "/housing/reports",
        "PostgreSQL 加密不可变快照",
        "Excel 两个工作表",
        "CSV ZIP",
        "楼栋/楼层范围授权",
        "人工报送、作废和更正链",
        "scripts/run-housing-reporting-e2e.sh",
        "API `8004` 和 Web `5179`",
        "实名报送四视口浏览器旅程已通过",
        "共 8 条实名报送浏览器旅程全部成功",
    }
    assert all(phrase in boundary for phrase in required_local_reporting)
    for deferred in (
        "Docker/NAS",
        "公安在线 API",
        "生产密钥轮换",
        "生产备份恢复",
        "录像/GPU",
    ):
        assert deferred in boundary
    assert "实名导出和人工报送仍未完成" not in readme
    assert "实名住宿导出与人工报送或录像分析" not in readme


def test_housing_personal_portal_launcher_runs_four_local_projects_without_docker() -> None:
    path = REPOSITORY_ROOT / "scripts/run-housing-personal-portal-e2e.sh"
    launcher = path.read_text(encoding="utf-8")

    assert path.stat().st_mode & 0o111
    assert "E2E_API_PORT:-8003" in launcher
    assert "E2E_WEB_PORT:-5178" in launcher
    assert "housing-personal-portal.spec.ts" in launcher
    assert 'python_bin" -m app.cli.seed_e2e' in launcher
    assert 'python_bin" -m app.cli.seed_housing_portal_e2e' in launcher
    assert 'python_bin" -m app.cli.e2e_database_safety' in launcher
    assert launcher.index("app.cli.e2e_database_safety") < launcher.index(
        "-m alembic upgrade head"
    )
    for project in ("desktop-1440", "desktop-1024", "tablet-768", "mobile-360"):
        assert project in launcher
    assert "docker " not in launcher.lower()
    assert "docker-compose" not in launcher.lower()


def test_evaluation_docs_mark_second_unit_complete_after_final_gate() -> None:
    design = _text(
        "docs/superpowers/specs/2026-07-30-teaching-evaluation-center-design.md"
    )
    readme = _text("README.md")
    completed = "第二实施单元本地实现与非 Docker 门禁已完成"
    assert completed in design
    assert completed in readme
    assert "第二实施单元计划已编写，待执行" not in design


def test_housing_docs_record_local_lifecycle_and_personal_portal_boundaries() -> None:
    readme = _text("README.md")
    parent_design = _text(
        "docs/superpowers/specs/2026-08-03-housing-room-status-design.md"
    )
    personal_design = _text(
        "docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md"
    )
    readme_boundary = _markdown_section(readme, "住宿生命周期与个人住宿门户")
    parent_boundary = "\n".join(
        (
            _markdown_lead(parent_design),
            _markdown_section(parent_design, "当前本地实现边界"),
        )
    )
    personal_boundary = _markdown_lead(personal_design)
    current_boundaries = (readme_boundary, parent_boundary)
    required_catalog_facts = {
        "4 幢楼、11 个楼层、188 个房间条目",
        "171 间住宿房需要逐间或批量核对",
        "scripts/run-housing-room-status-e2e.sh",
        "scripts/run-housing-occupancy-e2e.sh",
    }
    assert all(
        text in boundary
        for boundary in current_boundaries
        for text in required_catalog_facts
    )

    required_local_implementation = {
        "住宿生命周期后端 API 已在本地实现",
        "学员/监护人个人住宿门户已在本地实现",
        "管理员入住、调房、退房对话框和动态房态管理 UI 已在本地实现",
        "入住生命周期本地实现与非 Docker 门禁已完成",
        "住宿生命周期四视口浏览器旅程已通过",
    }
    assert all(
        text in boundary
        for boundary in current_boundaries
        for text in required_local_implementation
    )
    assert (
        "住宿模块的 Docker/NAS、公安在线接口、生产备份恢复和生产环境均未验证"
        in parent_boundary
    )
    assert "scripts/run-housing-personal-portal-e2e.sh" in readme_boundary
    assert "默认使用 API `8002` 和 Web `5177`" in readme_boundary
    assert "个人住宿门户本地实现与非 Docker 四视口门禁已完成" in personal_boundary
    assert "Docker/NAS、公安在线报送和生产部署仍未验证" in personal_boundary

    truthful_catalog_boundaries = {
        "楼栋 → 楼层 → 房间",
        "三层目录",
        "房间源自一次初始导入批次",
        "导入批次仅由初始种子写入",
        "保留导入批次元数据",
        "楼栋和房间都没有导入批次外键",
        "管理员可以逐间或批量核对这些房间",
        "没有导入批次 API 或管理界面",
        "管理员不能创建、编辑或删除导入批次",
    }
    assert all(
        text in boundary
        for boundary in current_boundaries
        for text in truthful_catalog_boundaries
    )

    forbidden_catalog_overclaims = {
        "导入批次 → 楼栋 → 楼层 → 房间",
        "四级源目录",
        "管理员可以维护导入批次",
        "导入批次 CRUD",
        "管理员可以新增导入批次",
        "管理员可以编辑导入批次",
        "管理员可以删除导入批次",
        "管理员可以维护四级目录",
        "管理员可维护四级目录",
        "系统提供导入批次管理页面",
        "导入批次支持 CRUD",
        "导入批次支持增删改查",
        "导入批次 API 已完成",
        "导入批次 UI 已完成",
        "导入批次 API 已上线",
        "导入批次 UI 已上线",
        "系统提供导入批次 API",
        "系统提供导入批次 UI",
        "导入批次支持 API",
        "导入批次支持 UI",
    }
    for boundary in current_boundaries:
        for claim in forbidden_catalog_overclaims:
            assert not any(
                _line_asserts_claim(line, claim) for line in boundary.splitlines()
            )

    forbidden_completion_claims = {
        "阶段 3 已完成",
        "住宿模块已完成",
        "入住功能已完成",
        "管理员动态房态 UI 已完成",
        "实名导出已完成",
        "公安接口已完成",
        "Docker/NAS 已完成",
        "生产房态已上线",
    }
    for boundary in current_boundaries:
        for claim in forbidden_completion_claims:
            assert not any(
                _line_asserts_claim(line, claim) for line in boundary.splitlines()
            )

    stale_unavailable_claims = {
        "当前人数固定为 0",
        "尚未开放入住、预约、问题房、调房、退房和实名导出",
        "管理员完整入住、调房、退房对话框和完整动态房态管理 UI 仍未完成",
        "截图上传与自动评价、住宿房态或录像分析",
        "OCR/自动评价、住宿房态、实名导出",
        "截图上传与自动评价、住宿房态、入住/派出所名单导出",
    }
    assert all(claim not in readme_boundary for claim in stale_unavailable_claims)


def test_housing_designs_mark_lifecycle_gate_complete_without_deployment() -> None:
    parent_design = _text(
        "docs/superpowers/specs/2026-08-03-housing-room-status-design.md"
    )
    personal_design = _text(
        "docs/superpowers/specs/2026-08-05-housing-personal-portal-design.md"
    )
    assert (
        "- **状态：** 入住生命周期本地实现与非 Docker 门禁已完成；"
        "个人住宿门户非 Docker 门禁已完成；实名导出和部署仍待后续单元"
    ) in parent_design
    assert (
        "**状态：** 个人住宿门户本地实现与非 Docker 四视口门禁已完成；"
        "Docker/NAS、公安在线报送和生产部署仍未验证"
    ) in personal_design


def test_readme_restore_verification_follows_current_schema_manifest() -> None:
    restore_section = _markdown_section(_text("README.md"), "数据库备份与恢复演练")

    assert re.search(r"33\s*张", restore_section) is None
    assert "当前 schema manifest" in restore_section
    assert "清单内每张必要表" in restore_section


def test_markdown_section_stops_before_later_housing_scope() -> None:
    document = """\
# 房态设计

## 当前边界

尚未开放入住功能。

### 当前边界细节

没有导入批次 CRUD/API/UI。

## 后续目标

入住功能已完成。
"""

    section = _markdown_section(document, "当前边界")

    assert "尚未开放入住功能" in section
    assert "没有导入批次 CRUD/API/UI" in section
    assert "入住功能已完成" not in section


@pytest.mark.parametrize(
    ("line", "claim", "expected"),
    [
        ("住宿模块已完成。", "住宿模块已完成", True),
        ("入住功能已完成。", "入住功能已完成", True),
        ("公安接口已完成。", "公安接口已完成", True),
        ("Docker/NAS 已完成。", "Docker/NAS 已完成", True),
        ("生产房态已上线。", "生产房态已上线", True),
        ("系统提供导入批次管理页面。", "系统提供导入批次管理页面", True),
        ("导入批次 CRUD。", "导入批次 CRUD", True),
        ("生产房态已上线，Docker/NAS 延期。", "生产房态已上线", True),
        ("无异常，住宿模块已完成。", "住宿模块已完成", True),
        ("住宿模块尚未完成但入住功能已完成。", "入住功能已完成", True),
        ("无异常且住宿模块已完成。", "住宿模块已完成", True),
        ("住宿模块尚未完成同时入住功能已完成。", "入住功能已完成", True),
        ("无异常以及住宿模块已完成。", "住宿模块已完成", True),
        ("导入批次 API 已完成。", "导入批次 API 已完成", True),
        ("系统提供导入批次 UI。", "系统提供导入批次 UI", True),
        ("生产房态已上线，待后续验证。", "生产房态已上线", True),
        ("Docker/NAS 已完成，延期复核。", "Docker/NAS 已完成", True),
        ("公安接口已完成，待后续部署。", "公安接口已完成", True),
        ("住宿模块已完成，属于历史性成果。", "住宿模块已完成", True),
        ("公安接口已完成，已被禁止使用。", "公安接口已完成", True),
        ("生产房态已上线并非问题。", "生产房态已上线", True),
        ("住宿模块已完成不是坏事。", "住宿模块已完成", True),
        ("生产房态已上线，待后续完成公安接口。", "生产房态已上线", True),
        ("Docker/NAS 已完成，待后续开放生产房态。", "Docker/NAS 已完成", True),
        ("住宿模块尚未完成。", "住宿模块已完成", False),
        ("尚未声称住宿模块已完成。", "住宿模块已完成", False),
        ("并未写成入住功能已完成。", "入住功能已完成", False),
        ("不得声称住宿模块已完成。", "住宿模块已完成", False),
        ("旧版误写“入住功能已完成”，现已纠正。", "入住功能已完成", False),
        ("并非公安接口已完成。", "公安接口已完成", False),
        ("Docker/NAS 已完成这一说法被禁止。", "Docker/NAS 已完成", False),
        ("生产房态已上线待后续单元实现。", "生产房态已上线", False),
        ("“入住功能已完成”，这一说法已被禁止。", "入住功能已完成", False),
        ("生产房态已上线，待后续单元实现。", "生产房态已上线", False),
        ("Docker/NAS 已完成，系历史误写。", "Docker/NAS 已完成", False),
        ("住宿模块已完成的说法被禁止。", "住宿模块已完成", False),
        ("生产房态已上线并非当前状态。", "生产房态已上线", False),
        ("入住功能已完成是历史误写。", "入住功能已完成", False),
        ("无导入批次 CRUD/API/UI。", "导入批次 CRUD", False),
        ("系统未提供导入批次管理页面。", "系统提供导入批次管理页面", False),
    ],
)
def test_line_assertion_distinguishes_positive_and_non_current_claims(
    line: str, claim: str, expected: bool
) -> None:
    assert _line_asserts_claim(line, claim) is expected
