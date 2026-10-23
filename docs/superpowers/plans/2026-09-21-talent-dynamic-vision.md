# 动态视力八项天赋测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将动态视力（瞬时数字识别）原生加入 ERP 天赋测试，让新轮次完成八项后获得系统评价，同时完整保留原七项轮次及历史报告。

**Architecture:** 在现有 talent_tests 模块中增加版本化规则、严格原始结果契约和服务端确定性计分；复用现有事务、鉴权、幂等保存、报告快照及待保存恢复流程。前端增加独立纯函数引擎和 ERP 风格五题游戏，不嵌入或请求外站。新轮次使用 v2，旧轮次继续使用其冻结的 v1 规则。

**Tech Stack:** 现有 Python / FastAPI / Pydantic / SQLAlchemy / PostgreSQL / Alembic，React / TypeScript / Vitest / Playwright；不增加运行时依赖。

**Spec:** `docs/superpowers/specs/2026-09-20-talent-dynamic-vision-design.md`（用户 2026-09-21 确认）；未改变部分继承 `docs/superpowers/specs/2026-09-20-talent-tests-design.md`。

状态：用户于2026-09-21确认实施计划，开始子任务协作实施；本文件不是实现、测试或上线完成证明。

## Global Constraints

- 项目 ID `dynamic-vision`；引擎版本 `dynamic-vision-v1`；名称“动态视力”，副标题“瞬时数字识别”。
- 用户任选 1—5 级难度，只完成所选级别的 5 题；开题即锁定难度。中文打字仍不接入。
- 总分 = 答对题数 × 每题基础分 10 × 所选难度系数；系数依次为 1.2 / 1.4 / 1.6 / 1.8 / 2.0；五题全对依次 60 / 70 / 80 / 90 / 100 分。
- 数字位数依次 3 / 4 / 5 / 5 / 6；标称闪现时间依次 100 / 100 / 100 / 36 / 36ms。各位独立随机，可重复，可前导零；整串相同才正确。
- 每题准备显示“2”“1”各 600ms，空白准备 1300ms，随后闪现；隐藏后方可输入。答错和跳过计零，不另加选级或胜利奖励。
- 档位为 0—39 较低、40—69 中等、70—89 较高、90—100 超高。有质量警告显示“试算分／需复测”，不列为优势。
- 字号为 56 CSS px；视口宽度低于 600px 时 40 CSS px。六位数字单行不截断，使用等宽数字，记录字号和测试区尺寸。
- 每题实测展示时长为有限正数且 ≤10000ms；差值严格超过 `max(30ms, 标称时长 × 50%)` 才提示偏差。等于边界不触发。
- 后台、离开、刷新、数字字号或测试区尺寸变化时，未完成五题整项中断；不得拼接不同尝试。已保存项目不受影响。
- 待保存结果未对账前不得换难度；本轮保存成功后只读；沿用固定提交键、24 小时白名单缓存及账号切换/退出清理。
- 随机数字与输入答案只存在当次游戏内存，不进入 API、sessionStorage、数据库、审计、日志或截图诊断；不保存半套题目。
- 新目录 `talent-training-v2`、评分 `talent-score-v2`、报告 `talent-report-v2`；原七个引擎版本不变。v1 目录、评分和报告不改写、不重算。
- 完成条件来自轮次冻结的必需项目集合；旧轮次 7/7，新轮次 8/8；历史分页返回 `required_count`。
- 新迁移 `0059_talent_dynamic_vision`，前驱 `0058_talent_tests`；只扩展 CHECK，不新增表。存在 v2 轮次/报告或新结果时拒绝降级，不删业务数据。
- 动态视力只比较同难度、引擎、评分、标称参数、字号、区域及现有可比输入环境；不跨版本或跨难度推导进步，不生成平均“总天赋指数”。
- 本项仅为内部训练参考，不是医学视力检查、IQ、官方百分位、职业潜力或防作弊认证。环境敏感优势须限定“本次操作环境下”。
- 不扩大原有角色权限；保留实时关系撤销、显式拒绝、私有 404、CSRF、no-store、限流及完成事务原子性。
- 不改原独立项目、不请求外站、不更改域名和 HTTPS 18080、不操作生产模拟成绩。
- 适用本地检查通过后必须从主工作区运行 `scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'`；预检、备份、部署或恢复校验失败均阻塞发布，不得绕过。

## Review Focus

1. **升级前打开的 v1 页面与已成功的创建键**：已有轮次可完成、创建键可重放；全新 v1 创建请求明确提示刷新，不能偷偷创建八项或再开七项。Task 2 的版本/重放数据库与 HTTP 测试负责。
2. **第五题连点、保存响应丢失与账号切换**：只产生一个结果，重试沿用同键；另一账号不能接收或恢复该结果。Task 4 的 GameHost/cache 测试和 Task 5 的浏览器断网恢复负责。
3. **同尺寸 ResizeObserver 通知与手机软键盘**：相同测试区尺寸不误中断；真正测试区/字号改变必须中断，不能把视觉视口高度变化本身一律当成失败。Task 4 的受控矩形测试与 Task 5 的真实触屏负责。
4. **36ms 短闪现、后台及偏差恰好等于边界**：计时按真实 DOM 展示区间记录；后台立即终止，66ms 不警告而 66.001ms 警告；不得靠放宽质量阈值通过验收。Task 1 的数值边界与 Task 4 的受控时钟测试负责。
5. **低难度五题全对**：一级仍为 60 分，报告展示封顶原因并建议新轮次尝试更高难度，不给 100 分、不写能力缺陷。Task 1 的 30 组独立期望及 Task 2/4 的报告测试负责。

---

## 工作区、所有权和执行顺序

所有下述代码路径相对于发布源 `/Users/skymoonzyj/Documents/new project3/.nas-release-sync`；部署命令例外，在主工作区执行。

- 已有隔离发布源分支 `codex/audit-repairs-20260914`，起点 HEAD `d6e1accd0692dca155b12e1197d0fc154f2e832c`，含大量未提交的七项功能及既有业务修改。先核实分支与工作区，保留全部既有内容，不新建一个丢失这些修改的空白工作树。
- 新工作记录目录 `.superpowers/sdd/2026-09-21-talent-dynamic-vision/`。各任务开始前保存自己拥有文件的基线、结束后保存定向 diff 和 RED/GREEN 记录；所有 worker 明确“不是独自在仓库工作，不撤回他人修改”。
- 沿用当前未发布工作区的逐文件检查点，不在任务中自动 stage/commit（提交钩子会部署，不能越过发布门槛）。任务检查点不是 Git 提交或已发布证明。
- Task 1（后端纯规则）与 Task 3（前端纯引擎）可并行；Task 2 消费 Task 1；Task 4 消费 Task 1、3 的已冻结接口，且须串行拥有前端共享文件；Task 5 消费 Task 2、4；Task 6 负责最终冻结与发布。
- 数据库及浏览器 harness 共用可重建隔离库，任何时刻只允许一个持有 DB 租约的任务运行；纯单测、静态检查可并行。
- 每个开发任务完成其自测后做定向独立复核；问题回到同一 implementer 集中修复，不对已通过的原七项范围重复发起整轮审核。

命令公共上下文（每个新 shell 设置；不是生产连接）：

```sh
cd '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
export PY='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'
export RUFF='/Users/skymoonzyj/Documents/new project3/.venv/bin/ruff'
export MYPY='/Users/skymoonzyj/Documents/new project3/.venv/bin/mypy'
export PYTHONPATH="$PWD/apps/api"
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
```

## 冻结的跨任务接口

### 数据契约

API 原始结果的新增 `raw` 仅有以下四个键，公共字段沿用 `SubmissionBase`：

```json
{
  "selected_level": 4,
  "outcomes": ["correct", "correct", "incorrect", "skipped", "correct"],
  "measured_display_ms": [36, 36, 36, 36, 36],
  "font_size_px": 56
}
```

- `selected_level` 为非布尔整数 1—5；`outcomes` 恰好五个封闭枚举；`measured_display_ms` 恰好五个有限 JSON 数值，`0 < x <= 10000`；不接受数值字符串或布尔数值。
- `font_size_px` 为严格整数 40 或 56，与已记录的视口宽度阈值一致；`device.geometry` 必填，宽高沿用正有限数约束，`target_diameter=null`。
- `duration_ms` 至少覆盖五次测得的展示时间；不把展示时长改写为标称值；公共未知输入/后台标记保持现有协议。
- `raw_value` 为答对数，`unit="count"`；原始五个结局足以推导正确/跳过/基础分，不新增客户端可赋值的 score、coefficient、grade 字段。

新规则与旧锚点规则并存；旧规则 JSON 不添加默认 `kind` 字段，避免无意改写历史快照：

```ts
type DynamicVisionLevel = {
  level: 1 | 2 | 3 | 4 | 5
  digit_count: 3 | 4 | 5 | 6
  nominal_display_ms: 36 | 100
  coefficient_tenths: 12 | 14 | 16 | 18 | 20
}
type DynamicVisionRule = {
  kind: "dynamic_vision"
  engine_version: "dynamic-vision-v1"
  source_label: "内部试行训练规则"
  unit: "count"
  base_points_per_correct: 10
  levels: [DynamicVisionLevel, DynamicVisionLevel, DynamicVisionLevel,
    DynamicVisionLevel, DynamicVisionLevel]
}
```

`coefficient_tenths` 是整数定点表示，显示时除以 10；服务端使用冻结规则中的该值，不根据用户提交系数计算。五行顺序、位数、标称值和系数必须与上表完全一致。

Python 保留 `TestRule` 原类型，新增 `DynamicVisionRule`、`DynamicVisionLevel`、`DynamicVisionRaw`、`DynamicVisionResultInput`；`RuleSnapshot.tests` 值类型改为两种规则联合。TypeScript 保留 `TestRule` 为旧锚点规则，新增对应类型。

稳定公共名：

```text
Python catalog:
  LEGACY_TEST_IDS: tuple[TestId, ...]             # 固定七项
  TEST_IDS: tuple[TestId, ...]                    # 支持的八项
  CURRENT_CATALOG_VERSION = "talent-training-v2"
  get_rules(catalog_version: str) -> RuleSnapshot
  get_test_rule(test_id: str, rules: RuleSnapshot) -> TestRule | DynamicVisionRule
  get_dynamic_vision_level(rule: DynamicVisionRule, level: int) -> DynamicVisionLevel
Python scoring:
  normalize_result(value: RawResultInput, rules: RuleSnapshot) -> NormalizedResult
  score_value(test_id: str, raw_value: int, rules: RuleSnapshot) -> int
    # 仅用于原七项锚点规则；动态视力必须走 normalize_result，不伪造 anchors
HTTP history:
  SessionSummary.required_count: int             # 每行自己的 rules_snapshot.test_ids 长度
Quality flag:
  display_duration_deviation -> 显示时长偏差，建议复测
```

### Task 1: 版本化目录、原始结果校验与服务端计分

**Files:**
- Modify: `apps/api/app/talent_tests/schemas.py`, `catalog.py`, `scoring.py`
- Modify: `apps/api/tests/unit/talent_test_helpers.py`, `test_talent_contracts.py`, `test_talent_scoring.py`
- Create: `apps/api/tests/unit/test_dynamic_vision_scoring.py`

**Interfaces:**
- Consumes: 现有 `SubmissionBase`、`Geometry`、`StrictModel`、`_require_json_number`、`RawResultInput` 判别联合、`fingerprint(BaseModel) -> str`。
- Produces: 上述冻结数据契约与目录函数；新增原始类型进入 `RawResultInput` / `RawStatistics`，原七项输入形状不变；在 `SessionSummary` 增加必填 `required_count` 字段，由 Task 2 更新查询构造处。Task 1 只跑纯函数检查，不在 Task 2 接好前执行或发布完整 API。

- [ ] **Step 1: 写 30 组独立期望分数及 v1 不变的 RED 测试。**

```py
import pytest
from pydantic import TypeAdapter
from app.talent_tests.catalog import get_rules
from app.talent_tests.schemas import RawResultInput
from app.talent_tests.scoring import normalize_result
from tests.unit.talent_test_helpers import valid_payload

EXPECTED = (
    (0, 12, 24, 36, 48, 60), (0, 14, 28, 42, 56, 70),
    (0, 16, 32, 48, 64, 80), (0, 18, 36, 54, 72, 90),
    (0, 20, 40, 60, 80, 100),
)

@pytest.mark.parametrize("level", range(1, 6))
@pytest.mark.parametrize("correct", range(6))
def test_exact_score_matrix(level, correct):
    payload = valid_payload("dynamic-vision")
    payload["raw"].update(
        selected_level=level,
        outcomes=["correct"] * correct + ["incorrect"] * (5 - correct),
        measured_display_ms=[100 if level <= 3 else 36] * 5,
    )
    parsed = TypeAdapter(RawResultInput).validate_python(payload)
    result = normalize_result(parsed, get_rules("talent-training-v2"))
    assert (result.raw_value, result.score) == (correct, EXPECTED[level - 1][correct])

def test_v1_rules_remain_seven_and_v2_appends_one():
    old, new = get_rules("talent-training-v1"), get_rules("talent-training-v2")
    assert len(old.test_ids) == 7
    assert new.test_ids[:-1] == old.test_ids
    assert new.test_ids[-1] == "dynamic-vision"
    for test_id in old.test_ids:
        assert old.tests[test_id].model_dump() == new.tests[test_id].model_dump()
```

Run: `"$PY" -m pytest apps/api/tests/unit/test_dynamic_vision_scoring.py -q`
Expected RED：新增 ID/目录尚不存在，测试因明确的 schema/catalog 不支持失败，而非测试导入路径或数据库错误。

- [ ] **Step 2: 扩展测试载荷、严格模型和目录注册表。**

```py
DynamicVisionDuration = Annotated[
    float, BeforeValidator(_require_json_number),
    Field(gt=0, le=10000, allow_inf_nan=False),
]

class DynamicVisionRaw(StrictModel):
    selected_level: Annotated[StrictInt, Field(ge=1, le=5)]
    outcomes: list[Literal["correct", "incorrect", "skipped"]] = Field(
        min_length=5, max_length=5
    )
    measured_display_ms: list[DynamicVisionDuration] = Field(min_length=5, max_length=5)
    font_size_px: StrictInt

    @model_validator(mode="after")
    def require_supported_font(self):
        if self.font_size_px not in (40, 56):
            raise ValueError("font_size_px must be 40 or 56")
        return self
```

`DynamicVisionResultInput` 继承 `SubmissionBase`，固定 `test_id` 与 engine；after validator 验证 geometry、字号/视口及 duration 总和。五级规则使用冻结表构造，不由输入建立。`_RULES_V1` 保持旧值，`_RULES_V2` 深复制旧七规则后追加新规则，版本注册表返回 deep copy。`get_test_rule` 先查已知版本，再检查 `rules.test_ids` 和 `rules.tests` 中的成员，不使用全局八项判断当前轮次成员。

- [ ] **Step 3: 编写并运行输入拒绝与时长阈值 RED 测试。**

```py
from pydantic import ValidationError

@pytest.mark.parametrize("level", [True, False, 0, 6, 1.5, "1"])
def test_reject_non_level_integer(level):
    payload = valid_payload("dynamic-vision")
    payload["raw"]["selected_level"] = level
    with pytest.raises(ValidationError):
        TypeAdapter(RawResultInput).validate_python(payload)

@pytest.mark.parametrize("actual, flagged", [(66, False), (66.001, True)])
def test_level_four_display_threshold(actual, flagged):
    payload = valid_payload("dynamic-vision")
    payload["raw"].update(selected_level=4, measured_display_ms=[actual] * 5)
    result = normalize_result(
        TypeAdapter(RawResultInput).validate_python(payload), get_rules("talent-training-v2")
    )
    assert ("display_duration_deviation" in result.quality_flags) is flagged
```

同时参数化 100ms 的 50/150 边界与边界外值；duration 的 0、负数、NaN、Infinity、10000/10000.001、布尔和字符串；结局/时长长度 4/6、未知结局、额外 `answers` / `digits` / `score` / `coefficient`；空 geometry、错误字号、错误 engine；全部 skipped 为零。保留并运行 `_grade` 的 39/40/69/70/89/90/100 测试，不能只验证可达分数。

- [ ] **Step 4: 在 normalize_result 中按规则种类分支计分，保留通用质量提示。**

```py
rule = get_test_rule(value.test_id, rules)
if isinstance(value, DynamicVisionResultInput):
    if not isinstance(rule, DynamicVisionRule):
        raise UnknownTestId(value.test_id)
    level = get_dynamic_vision_level(rule, value.raw.selected_level)
    raw_value = value.raw.outcomes.count("correct")
    score = raw_value * rule.base_points_per_correct * level.coefficient_tenths // 10
    nominal = Decimal(str(level.nominal_display_ms))
    limit = max(Decimal(30), nominal / 2)
    deviation = any(
        abs(Decimal(str(actual)) - nominal) > limit
        for actual in value.raw.measured_display_ms
    )
```

将计算结果交给现有 NormalizedResult 构造，偏差只追加固定 flag，不丢弃协议有效结果；engine 和 scoring 使用当前冻结规则。`score_value` 遇动态规则明确拒绝，旧锚点参数化测试改为 `LEGACY_TEST_IDS`，不得因此减少旧七项覆盖。

- [ ] **Step 5: GREEN 与检查点。**

```sh
"$PY" -m pytest apps/api/tests/unit/test_dynamic_vision_scoring.py apps/api/tests/unit/test_talent_contracts.py apps/api/tests/unit/test_talent_scoring.py -q
"$RUFF" check apps/api/app/talent_tests apps/api/tests/unit/test_dynamic_vision_scoring.py
"$MYPY" --config-file apps/api/pyproject.toml apps/api/app/talent_tests/schemas.py apps/api/app/talent_tests/catalog.py apps/api/app/talent_tests/scoring.py
```

记录测试数、退出码、定向 diff；不部署。向 Task 2/4 交付固定 raw 和规则 JSON 示例。

### Task 2: 数据迁移、混合版本轮次与不可变报告

**Files:**
- Create: `apps/api/migrations/versions/0059_talent_dynamic_vision.py`
- Modify: `apps/api/app/talent_tests/models.py`, `service.py`, `queries.py`, `reporting.py`（消费 Task 1 已添加的 `SessionSummary.required_count`，不并行编辑 schemas.py）
- Modify: `apps/api/tests/services/talent_helpers.py`, `test_talent_sessions.py`, `test_talent_queries.py`, `test_talent_atomicity.py`
- Modify: `apps/api/tests/api/test_talent_routes.py`, `test_talent_privacy.py`
- Modify: `apps/api/tests/db/test_talent_models.py`, `test_talent_concurrency.py`, `test_migrations.py`
- Modify: `apps/api/tests/unit/test_talent_reporting.py`
- Modify: `scripts/db-schema-manifest.sh`, `apps/api/tests/cli/test_operations_docs.py`, `test_local_container_acceptance_launcher.py`

**Interfaces:**
- Consumes: Task 1 的目录、raw 联合及 normalize_result；原 `TalentTestService.start/submit/abandon` 参数保持不变。
- Produces: 新建 v2、已有 v1 重放/完成、`SessionSummary.required_count`；原 `build_report(*, session_id, person, completed_at, rules, results, baselines) -> ReportSnapshot` 签名不变；`find_comparable_baselines(...)` 对动态视力筛选更严格。
- Test-only helper: `seed_legacy_session(db: Session, actor: AuthContext, request_id: UUID, now: datetime) -> SessionView`；不是生产接口，不为测试增加服务 bypass。

- [ ] **Step 1: 写旧轮次迁移前语义的 fixture 和 RED 测试。**

在 `talent_helpers.py` 用真实模型构造既有 v1 行：

```py
def seed_legacy_session(db, actor, request_id, now):
    rules = get_rules("talent-training-v1")
    row = TalentTestSession(
        person_id=actor.person.id, created_by_user_id=actor.account.id,
        creation_key=request_id,
        creation_request_digest=fingerprint(SessionCreateRequest(
            request_id=request_id, catalog_version=rules.catalog_version)),
        catalog_version=rules.catalog_version, scoring_version=rules.scoring_version,
        report_version=rules.report_version, rules_snapshot=rules.model_dump(mode="json"),
        status="in_progress", created_at=now, updated_at=now, results=[], report=None,
    )
    db.add(row)
    db.commit()
    return TalentQueries.session_view(row)
```

测试拥有的 actor 由现有 `student_actor(db)` 创建。旧 fixture 不伪造审计；测试记录的基线从 seed 之后计算。

```py
@pytest.mark.parametrize("version, required", [("talent-training-v1", 7), ("talent-training-v2", 8)])
def test_completion_uses_its_frozen_catalog(db, version, required):
    actor, svc = student_actor(db), TalentTestService(db)
    if version.endswith("v1"):
        current = seed_legacy_session(db, actor, uuid4(), NOW)
    else:
        current, _ = svc.start(actor=actor, request_id=uuid4(), catalog_version=version, now=NOW)
    for index, test_id in enumerate(current.rules.test_ids, 1):
        saved, _ = svc.submit(actor=actor, session_id=current.id, value=submission(test_id), now=NOW)
        assert saved.session.status == ("completed" if index == required else "in_progress")
        assert (saved.session.report_id is not None) is (index == required)
```

增加动态视力最先提交、最后提交两种顺序；v1 新项目拒绝后结果数/报告数/业务审计数不变。默认新轮次 helper 改用当前目录；旧版本断言显式使用 legacy fixture，而不是全局替换字符串后丢掉历史覆盖。

Run: `"$PY" -m pytest apps/api/tests/services/test_talent_sessions.py -q`（持 DB 租约）
Expected RED：当前完成判断仍为全局七项或当前目录尚未切换。

- [ ] **Step 2: 写并执行 0058→0059→0058 的保留/拒绝迁移测试。**

在 `test_migrations.py` 使用已有 `_alembic_config(postgres_engine)` 和 account fixture，断言旧七项快照和行数升降级不变；0059 可插入动态结果；分别只有空 v2 轮次、含动态结果、含 v2 报告时降级都抛明确错误且 revision 保持 0059。无新数据时允许退回 0058。测试在 finally 回到当前 head。

迁移核心：

```py
revision = "0059_talent_dynamic_vision"
down_revision = "0058_talent_tests"

def upgrade():
    op.drop_constraint("ck_talent_results_test_id", "talent_test_results", type_="check")
    op.create_check_constraint(
        "ck_talent_results_test_id", "talent_test_results",
        "test_id IN ('reactiontime','aim','sequence','number-memory',"
        "'verbal-memory','chimp','memory','dynamic-vision')",
    )

def downgrade():
    has_new_data = op.get_bind().scalar(sa.text("""
        SELECT EXISTS(SELECT 1 FROM talent_test_sessions
                      WHERE catalog_version = 'talent-training-v2')
        OR EXISTS(SELECT 1 FROM talent_test_results WHERE test_id = 'dynamic-vision')
        OR EXISTS(SELECT 1 FROM talent_test_reports
                  WHERE report_snapshot->'rules'->>'catalog_version' = 'talent-training-v2')
    """))
    if has_new_data:
        raise RuntimeError("存在八项天赋测试数据，拒绝降级；未删除任何记录")
    op.drop_constraint("ck_talent_results_test_id", "talent_test_results", type_="check")
    op.create_check_constraint(
        "ck_talent_results_test_id", "talent_test_results",
        "test_id IN ('reactiontime','aim','sequence','number-memory',"
        "'verbal-memory','chimp','memory')",
    )
```

同步 ORM CHECK、manifest head/chain 和 CLI 假迁移头；新增表数为零。不得改写 0058 或现有 backup/restore 逻辑。

- [ ] **Step 3: 保留鉴权→旧键重放顺序，再拒绝全新旧目录请求。**

`start` 中旧键查找、digest 验证和原对象返回之后加入：

```py
rules = get_rules(catalog_version)
if rules.catalog_version != CURRENT_CATALOG_VERSION:
    raise TalentConflict("测试目录已更新，请刷新页面后开始新一轮", code="talent_catalog_updated")
```

其余活跃轮次冲突及锁序不变。context 返回当前目录；submit 使用 `set(rules.test_ids)` 判断完成；report `_validate_results` 同样按当前快照检查重复/缺项/多项。历史 `required_count=len(rules_snapshot["test_ids"])`，不硬编码 8。

新增 `test_legacy_creation_key_replays_after_upgrade`：old seed→完成/放弃→新开 v2→重放 old key，仍得到原 v1；新 UUID + v1 返回 409 `talent_catalog_updated` 且零写入。HTTP 验证中文错误、no-store 与请求键不被失败占用；权限撤销后旧键也不得重放泄露。

- [ ] **Step 4: v2 报告建议与严格比较。**

`_ADVICE_BY_REPORT_VERSION` 保留 v1 原值，v2 增加动态建议；`_DEVICE_SENSITIVE` 加新 ID。规则表和 raw 推导所选参数，不保存第二份可漂移系数。低难度正确数 ≥4 且无质量警告时优先写“可在新一轮尝试更高难度”。

可比键在现有 key 后追加动态参数：

```py
if result.test_id == "dynamic-vision":
    raw = DynamicVisionRaw.model_validate(result.raw.model_dump())
    rule = get_test_rule("dynamic-vision", get_rules("talent-training-v2"))
    assert isinstance(rule, DynamicVisionRule)
    level = get_dynamic_vision_level(rule, raw.selected_level)
    dynamic_key = (raw.selected_level, raw.font_size_px, level.digit_count, level.nominal_display_ms)
else:
    dynamic_key = ()
```

进入上述动态可比键分支前必须检查 `result.scoring_version == "talent-score-v2"`，不满足时按不可比处理，不抛出会阻断旧报告读取的错误。若未来存在更多动态评分版本，未知版本不得回退使用 v2 推断；当前 v2 的标称参数由不可变 scoring_version 确定。SQL 候选筛选加 raw `selected_level` 和 `font_size_px` 等值约束，保留 geometry/engine/scoring/input/no-quality 条件；最终报告用同一可比检查，不先选一个不匹配的最近报告而错过较早匹配报告。

测试：1级5对分60及建议；4级4对分72；质量警告不进入 strengths；不同 level/font/geometry/scoring 不比较；最近不同难度但更早相同条件时可找到后者；v1报告读出内容不变，不生成八项空占位。

- [ ] **Step 5: 并发、原子性、隐私及 GREEN。**

复用真实独立连接 fixture：两个相同最后提交键只保存一个 result/report/completed audit；不同键抢同一项目无覆盖；abandon 与最后提交互斥；audit 写入失败回滚全部业务状态；未知项目/错误字段不占用成功提交键。权限回归覆盖监护关系撤销、教练当前/未来负责关系、无权显式 subject、私有 404。

```sh
"$PY" -m pytest apps/api/tests/services/test_talent_sessions.py apps/api/tests/services/test_talent_queries.py apps/api/tests/services/test_talent_atomicity.py apps/api/tests/api/test_talent_routes.py apps/api/tests/api/test_talent_privacy.py apps/api/tests/db/test_talent_models.py apps/api/tests/db/test_talent_concurrency.py apps/api/tests/db/test_migrations.py apps/api/tests/unit/test_talent_reporting.py apps/api/tests/cli/test_operations_docs.py apps/api/tests/cli/test_local_container_acceptance_launcher.py -q
```

记录最终行数、唯一测试结果及迁移保留证据，释放 DB 租约并保存定向 diff；不部署。

### Task 3: 动态视力纯前端引擎

**Files:**
- Create: `apps/web/src/talentTests/games/dynamicVision/engine.ts`
- Create: `apps/web/src/talentTests/games/dynamicVision/engine.test.ts`

**Interfaces:**
- Consumes: 本计划固定五级表；不依赖 React、DOM、网络、主 contracts 文件。
- Produces:

```ts
export type DynamicVisionDifficulty = 1 | 2 | 3 | 4 | 5
export type DynamicVisionOutcome = "correct" | "incorrect" | "skipped"
export const DYNAMIC_VISION_LEVELS: readonly {
  level: DynamicVisionDifficulty; digit_count: number
  nominal_display_ms: number; coefficient_tenths: number
}[]
export function createDynamicVisionDigits(level: DynamicVisionDifficulty, random?: () => number): string
export function normalizeDynamicVisionInput(value: string, maxLength: number): string
export function judgeDynamicVisionAnswer(expected: string, answer: string): "correct" | "incorrect"
export function summarizeDynamicVision(level: DynamicVisionDifficulty, outcomes: readonly DynamicVisionOutcome[]): {
  correctCount: number; skippedCount: number; baseScore: number; coefficient: number; score: number
}
```

- [ ] **Step 1: 写纯函数 RED 测试。**

```ts
it("retains leading zeroes and repeated digits", () => {
  expect(createDynamicVisionDigits(5, () => 0)).toBe("000000")
  expect(createDynamicVisionDigits(1, () => 0.999)).toBe("999")
  expect(judgeDynamicVisionAnswer("001", "001")).toBe("correct")
  expect(judgeDynamicVisionAnswer("001", "1")).toBe("incorrect")
  expect(normalizeDynamicVisionInput("00x1234", 3)).toBe("001")
})
it("scores selected level only", () => {
  expect(summarizeDynamicVision(4, ["correct", "correct", "correct", "correct", "skipped"]))
    .toEqual({ correctCount: 4, skippedCount: 1, baseScore: 40, coefficient: 1.8, score: 72 })
})
```

Run: `npm --prefix apps/web test -- src/talentTests/games/dynamicVision/engine.test.ts`
Expected RED：新引擎尚不存在。

- [ ] **Step 2: 实现无状态生成、清洗、判题及试算。**

```ts
export function normalizeDynamicVisionInput(value: string, maxLength: number): string {
  return value.replace(/[^0-9]/g, "").slice(0, maxLength)
}
export function judgeDynamicVisionAnswer(expected: string, answer: string): "correct" | "incorrect" {
  return expected === answer ? "correct" : "incorrect"
}
export function summarizeDynamicVision(level: DynamicVisionDifficulty, outcomes: readonly DynamicVisionOutcome[]) {
  if (outcomes.length !== 5) throw new RangeError("需要完成五题")
  const correctCount = outcomes.filter(value => value === "correct").length
  const skippedCount = outcomes.filter(value => value === "skipped").length
  return { correctCount, skippedCount, baseScore: correctCount * 10,
    coefficient: (10 + 2 * level) / 10, score: correctCount * (10 + 2 * level) }
}
```

生成函数按 `digit_count` 每位调用 `Math.floor(random()*10)` 并拼接字符串；默认 Math.random，不缓存题库、不去重数字。五级常量表精确写入冻结值；不使用以难度指数增长的旧数字记忆时长公式。

- [ ] **Step 3: 添加独立 30 组期望与 GREEN。**

```ts
const rows = [[0,12,24,36,48,60], [0,14,28,42,56,70],
  [0,16,32,48,64,80], [0,18,36,54,72,90], [0,20,40,60,80,100]]
for (const [index, expected] of rows.entries()) {
  for (let correct = 0; correct <= 5; correct += 1) {
    it(`level ${index + 1}, correct ${correct}`, () => {
      const outcomes: DynamicVisionOutcome[] = Array.from({ length: 5 },
        (_, i) => i < correct ? "correct" : "skipped")
      expect(summarizeDynamicVision((index + 1) as DynamicVisionDifficulty, outcomes).score)
        .toBe(expected[correct])
    })
  }
}
```

运行同一 engine 测试命令，全部通过；记录检查点。Task 4 的最终分仍来自服务端，纯引擎试算不能直接当作已保存结果。

### Task 4: 可用的前端纵向集成（游戏、缓存、轮次、报告）

**Files:**
- Create: `apps/web/src/talentTests/games/dynamicVision/DynamicVisionGame.tsx`, `DynamicVisionGame.css`, `DynamicVisionGame.test.tsx`
- Modify: `apps/web/src/talentTests/contracts.ts`, `pending.ts`, `useTalentSession.ts`, `GameHost.tsx`
- Modify: `apps/web/src/talentTests/TalentTestsPage.tsx`, `TalentHistoryPage.tsx`, `TalentReportPage.tsx`, `talentTests.css`
- Modify: `apps/web/src/talentTests/contracts.test.ts`, `pending.test.ts`, `GameHost.test.tsx`, `GameHost.status.test.tsx`, `TalentTestsPage.test.tsx`, `TalentHistoryPage.test.tsx`, `TalentReportPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`, `router.test.tsx`（只在现有 allowlist 需要时）；`apps/web/src/talentTests/THIRD_PARTY_NOTICES.md`（记录行为参考，不称复制源码）

**Interfaces:**
- Consumes: Task 1 的 JSON 契约、Task 3 的全部导出、原 `GameProps<K>` 的 onComplete/onInterrupted；GameChrome 仍由宿主负责。
- Produces: `DynamicVisionGame(props: GameProps<"dynamic-vision">)`；新增 `ENGINE_VERSIONS` 显式映射；`RuleSnapshot.tests: Partial<Record<TestId, TestRule | DynamicVisionRule>>` 与安全成员检查。
- `Submission` 的 `engine_version` 改为 `(typeof ENGINE_VERSIONS)[K]`，旧七项值不变。`SessionSummary.required_count:number`；不假设历史含动态规则。
- 本任务一起交付共享类型、保存适配及真实可用组件，不接受只把新 ID 加入 union、组件仍空白的中间交付。

- [ ] **Step 1: 契约/缓存/旧轮访问的 RED。**

```ts
export const ENGINE_VERSIONS = {
  reactiontime: "reactiontime-clone-v1", aim: "aim-clone-v1",
  sequence: "sequence-clone-v1", "number-memory": "number-memory-clone-v1",
  "verbal-memory": "verbal-memory-clone-v1", chimp: "chimp-clone-v1",
  memory: "memory-clone-v1", "dynamic-vision": "dynamic-vision-v1",
} as const
```

在现有 pending 测试的真实读写 helper 中新增载荷：只接受四个 raw 键；带额外 answers/digits 的缓存需丢弃这些字段或拒绝整个无效条目，绝不再序列化；缺少必需字段、错误 engine/level/时长/长度不恢复。使用现有账号/轮次/项目和过期测试分别覆盖新 ID。

在 GameHost 既有 session fixture 中传入 v1 + `testId="dynamic-vision"`：

```tsx
expect(await screen.findByRole("alert")).toHaveTextContent("本轮不包含该测试项目")
expect(screen.queryByRole("button", { name: "开始测试" })).not.toBeInTheDocument()
```

并断言既有 API 提交 mock 未调用。必须在访问 `rules.tests[testId]` 前守卫；升级刷新冲突显示服务端中文提示、可重新读取目录，不静默改写已发请求的创建键载荷。

Run: `npm --prefix apps/web test -- src/talentTests/pending.test.ts src/talentTests/GameHost.test.tsx`
Expected RED：旧全局 clone engine 推导与 v1 新路由缺少守卫。

- [ ] **Step 2: 游戏状态机及可控时钟 RED。**

状态固定为 `intro | countdown-two | countdown-one | blank | displaying | answer | review | completed`；题号 0—4；terminals 和提交锁使用 ref 阻止同一 tick 连点。

测试用 `vi.useFakeTimers()` 并使 `performance.now()` 与 fake clock 同步；测量从 DOM 显示提交后开始，不能在预定显示前启动时钟。测试伪 RNG 固定零但不将题目写进日志。

```tsx
it("locks one level, hides before input, and keeps leading zeroes", async () => {
  vi.useFakeTimers()
  vi.spyOn(Math, "random").mockReturnValue(0)
  const onComplete = vi.fn()
  render(<DynamicVisionGame onComplete={onComplete} onInterrupted={vi.fn()} />)
  fireEvent.click(screen.getByRole("radio", { name: /第 4 级/ }))
  fireEvent.click(screen.getByRole("button", { name: "开始测试" }))
  expect(screen.getByRole("radio", { name: /第 1 级/ })).toBeDisabled()
  await act(async () => vi.advanceTimersByTime(600))
  expect(screen.getByTestId("dynamic-vision-stage")).toHaveTextContent("1")
  await act(async () => vi.advanceTimersByTime(600))
  expect(screen.getByTestId("dynamic-vision-stage")).toBeEmptyDOMElement()
  await act(async () => vi.advanceTimersByTime(1300))
  expect(screen.getByTestId("dynamic-vision-stimulus")).toHaveAttribute("aria-hidden", "true")
  expect(screen.queryByRole("textbox", { name: "输入数字" })).not.toBeInTheDocument()
  await act(async () => vi.advanceTimersByTime(36))
  expect(screen.queryByTestId("dynamic-vision-stimulus")).not.toBeInTheDocument()
  fireEvent.change(screen.getByRole("textbox", { name: "输入数字" }), { target: { value: "00000" } })
  fireEvent.click(screen.getByRole("button", { name: "提交答案" }))
  expect(screen.getByText("回答正确")).toBeInTheDocument()
  expect(onComplete).not.toHaveBeenCalled()
})
```

Run: `npm --prefix apps/web test -- src/talentTests/games/dynamicVision/DynamicVisionGame.test.tsx`
Expected RED：新组件不存在；先补真实实现再运行，不用快照代替交互断言。

- [ ] **Step 3: 实现展示计时和一次性提交。**

用 `useLayoutEffect` 在 displaying 的实际 DOM 提交时记 `performance.now()`，定时请求切到 answer；在 answer 的 layout effect 隐藏 DOM 已提交后记录差值。这样包括事件循环/React 延迟；不声称测得硬件真实曝光。所有 timeout 在中断、卸载时取消；effect 不因 onComplete 函数身份变化重新开始。

```tsx
useLayoutEffect(() => {
  if (phase === "displaying" && displayStartedAt.current === null) {
    displayStartedAt.current = performance.now()
    timerRef.current = setTimeout(() => {
      if (!terminalRef.current && !document.hidden) setPhase("answer")
    }, selectedRule.nominal_display_ms)
  }
  if (phase === "answer" && displayStartedAt.current !== null) {
    measuredRef.current.push(performance.now() - displayStartedAt.current)
    displayStartedAt.current = null
  }
}, [phase, selectedRule.nominal_display_ms])
```

严格模式 effect 清理/重挂测试必须覆盖；若计时被清理导致丢失 timer，采用单独卸载清理 effect 和稳定 ref；不得用填入标称时间掩盖零时长。运行期发现非有限/≤0/超过10000 的测量时中止并显示无法可靠测量、可重新开始，不构造无效提交。

第五题处理核心：

```ts
if (phase !== "answer" || submittingRef.current || terminalRef.current) return
submittingRef.current = true
const outcome = skip ? "skipped" : judgeDynamicVisionAnswer(digits, answer)
const nextOutcomes = [...outcomesRef.current, outcome]
outcomesRef.current = nextOutcomes
if (nextOutcomes.length === 5) {
  terminalRef.current = true
  setPhase("completed")
  onCompleteRef.current({
    test_id: "dynamic-vision",
    raw: { selected_level: selectedLevel, outcomes: nextOutcomes,
      measured_display_ms: [...measuredRef.current], font_size_px: geometryRef.current.fontSize },
    device: { input_kind: observedInputKind(inputKindsRef.current),
      viewport_width: geometryRef.current.viewportWidth,
      viewport_height: geometryRef.current.viewportHeight,
      geometry: { width: geometryRef.current.width, height: geometryRef.current.height,
        target_diameter: null } },
    duration_ms: performance.now() - startedAtRef.current,
    background_interrupted: false,
  })
} else {
  setPhase("review")
}
```

`observedInputKind(kinds: ReadonlySet<InputKind>): InputKind` 在组件内按原 NumberGame 规则定义；`geometryRef` 存开始时实测的固定 stage 尺寸、字体与视口，不存题目。只有长度完全符合的非 skip 输入能进入此段；“下一题”才清提交锁。

输入框使用 `inputMode="numeric"`、`autoComplete="off"`，以字符串清洗；数字键盘 0—9、删除一位、清空、提交、跳过；物理 Enter 提交，不接收 composing 中输入。第五题 completed 保留末题反馈与摘要，但不再有下一题/换难度按钮；GameHost 的待保存/保存后状态接管后只显示安全摘要。

- [ ] **Step 4: 中断/布局/隐私 RED→GREEN。**

沿用 AimGame 的 ResizeObserverDouble，测试开始时固定 stage 矩形。相同 width/height/font 通知不改变题目；变化后清 timers/outcomes/答案，`onInterrupted("geometry_changed")` 一次。visibility hidden 调用 `onInterrupted("hidden")` 一次且不 onComplete。未完成刷新后无缓存；完成只缓存安全 payload。

CSS 保持 stage 固定高度与 `max-width:100%`，数字 `font-size:56px; font-variant-numeric:tabular-nums; white-space:nowrap; font-family:monospace;`；`@media(max-width:599px)` 为40px。读取实际 computed font size、stage bounding rect，比对而非监听每次 visualViewport resize 就中断。软键盘不改变 stage 时继续作答；若真正改了 stage 则提示重新开始。

添加五题完整测试：第1题前导零正确、第2题错误、第3题跳过、第4/5题正确；第5题重复click/Enter仅一次onComplete，outcomes恰好5、计时恰好5；result及sessionStorage无`digits/answer/answers`。额外单测已产生 pending 后尝试改难度、保存响应先丢失后同键重试、读取失败手动恢复、账号切换后旧响应晚到，覆盖 Review Focus 2。

- [ ] **Step 5: 页面进度、报告及规则表。**

入口渲染服务端 `rules.test_ids`，介绍与进度使用该轮次实际长度。历史每行 `saved_count/required_count`，测试同页同时出现 7/7、7/8、8/8；不能在加载前先显示“八项已完成”。

动态报告用独立分支渲染，不访问 anchors：

```tsx
<dl>
  <dt>本次成绩</dt><dd>第 {raw.selected_level} 级，答对 {correctCount}/5 题</dd>
  <dt>基础分</dt><dd>{correctCount * rule.base_points_per_correct}</dd>
  <dt>难度系数</dt><dd>{(level.coefficient_tenths / 10).toFixed(1)}</dd>
  <dt>计分方式</dt><dd>{correctCount} × 10 × {(level.coefficient_tenths / 10).toFixed(1)} = {result.score}</dd>
</dl>
```

这里 `raw` 从 `RawByTest["dynamic-vision"]` 安全缩窄，`level` 从报告冻结 `DynamicVisionRule.levels` 查询；另显示位数、标称时间、跳过数、档位、质量提示和五级表。没有动态规则的旧报告不渲染新项目。新增 `display_duration_deviation` 中文映射，动态优势加环境限定；有 quality flags 不作为优势。

测试 4级4对显示72、1级5对显示60及封顶/建议；旧七项报告不出现第八项；不同条件无进步文本；不显示平均总分。router 支持新 ID，但不能绕过 GameHost 轮次成员守卫。

- [ ] **Step 6: 前端 GREEN 与检查点。**

```sh
npm --prefix apps/web test -- src/talentTests src/app/router.test.tsx --maxWorkers=4
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

保持当前首次读取失败恢复、中文单位、低层输入防连点等七项修复不回退。记录警告（包括已有主包体积提示），不顺带重构整站；交付可运行完整前端，而非占位卡片。

### Task 5: 八项浏览器验收、混合版本及安全证据

**Files:**
- Modify: `apps/web/tests/talent-tests.spec.ts`, `talent-touch.spec.ts`
- Create: `apps/web/tests/talent-dynamic-vision.spec.ts`
- Modify: `scripts/run-core-e2e.sh`
- Modify: `apps/api/app/cli/seed_talent_e2e.py`, `apps/api/tests/cli/test_seed_talent_e2e.py`（只在添加旧轮验收种子所需范围）
- Create: `docs/verification/2026-09-21-talent-dynamic-vision.md`

**Interfaces:**
- Consumes: Task 2 的当前 v2 API、旧轮 fixture 语义与 Task 4 的无障碍标签/testid；沿用现有 `E2E_CORE_MODE=core|talent|all`。
- Produces: talent mode 中新测试在 desktop-1440、desktop-1024、tablet-768、mobile-360、touch-mobile-390 运行；旧 core 模式矩阵不缩小。诊断只记录固定类别和计数。

- [ ] **Step 1: 新浏览器断言先 RED，再更新真实种子和脚本。**

沿用现有仅隔离库可用的 seed 和浏览器登录，不访问生产。独立 dynamic spec 进入真实新轮次、选择4级、五题后检查服务端保存结果；另一用例以5级验证六位数字在所有视口均无截断。

```ts
await page.getByRole("link", { name: /动态视力/ }).click()
await page.getByRole("radio", { name: /第 4 级/ }).check()
await expect(page.getByText(/最高.*90/)).toBeVisible()
await page.getByRole("button", { name: "开始测试" }).click()
await expect(page.getByRole("radio", { name: /第 5 级/ })).toBeDisabled()
```

测试中控制时钟与 RNG 只用于确定性验收，不加入产品后门；真实鼠标/触屏动作使用已有测试模式。第四题前无结果写入，第五题只有一次；response oracle 断言 `engine_version=dynamic-vision-v1`、score和5个outcomes、payload无答案字段。旧整轮桌面/触屏测试改为八项完成且继续验证七项原游戏。

RED 命令为下方 talent harness；在 Task 4 完成前应因新 UI/完整八项流程不存在失败，Task 4 完成后应直接验证真实行为，勿人为破坏代码制造 RED。

- [ ] **Step 2: 扩展五视口和真实触屏，验证非模拟鼠标路径。**

`run_talent_specs` 保留现有完整桌面/触屏，再追加：

```sh
for project in desktop-1440 desktop-1024 tablet-768 mobile-360 touch-mobile-390; do
  run_seeded_spec talent-dynamic-vision.spec.ts "$project" app.cli.seed_talent_e2e
done
```

新触屏用例使用 `page.touchscreen.tap` / locator.tap，确保 input_kind 为 touch 或按混合操作如实为 mixed，不用 page.click 假称触屏。每个窗口检查 stage/六位文本无横向溢出、焦点可达、软键盘布局逻辑不误中断。

第五题丢一次响应→页面恢复 pending→手动重试/重读→结果数不变；hidden 中断→无缓存无成绩→重来五题；真实 geometry 改变中断；v1直接新路由只提示不可用；7/8不生成报告、8/8生成唯一报告。旧核心业务保持原断言，不降低视口覆盖。

- [ ] **Step 3: 隐私和部署前资料检查。**

不在闪现/反馈含答案时保存截图；需要截图只拍说明页、选择难度页或不含题目答案的保存摘要/报告。talent 测试按既有隐私措施关闭可能录入答案的 trace/video/失败截图；不要保存原始请求体、控制台内容或 token。使用固定合成测试人，不泄露真实账号。

DB/审计断言检查键集合：`raw_result`/`normalized_result.raw` 仅四个允许键，设备在既定字段，业务审计没有题目、答案或原始成绩载荷。诊断脱敏检查不能用删除功能断言来替代。

- [ ] **Step 4: 顺序运行 talent 和 core harness，写验证报告。**

先按现有 `scripts/check.sh` 的公开本地测试配置段准备测试环境，确认无生产 env；不得把该段的密钥值打印到报告。持 DB 租约，依次：

```sh
E2E_PYTHON_BIN="$PY" E2E_CORE_MODE=talent \
TALENT_E2E_ARTIFACT_DIR="$PWD/.superpowers/sdd/2026-09-21-talent-dynamic-vision/browser" \
scripts/run-core-e2e.sh
E2E_PYTHON_BIN="$PY" E2E_CORE_MODE=core \
TALENT_E2E_ARTIFACT_DIR="$PWD/.superpowers/sdd/2026-09-21-talent-dynamic-vision/browser" \
scripts/run-core-e2e.sh
```

两条均须退出0，记录每个项目/视口的通过、跳过原因和失败数；失败必须解决并重跑受影响范围。报告区分原七项证据和新增八项证据，记下运行源码快照、命令、退出码及已知局限。确认 harness 子进程和监听端口释放后才交还 DB 租约。

### Task 6: 最终复核、完整回归及 NAS 发布

**Files:**
- Modify: `docs/verification/2026-09-21-talent-dynamic-vision.md`
- Review only: Task 1—5 定向 diff、主工作区 `AGENTS.md` / `scripts/deploy-nas.sh`

**Interfaces:**
- Consumes: 所有任务 GREEN/定向复核证据、0059 schema manifest、未改变的 NAS 部署入口。
- Produces: 经过全量回归的源码、NAS 发布/健康/备份恢复证据，或准确的发布阻塞报告；不输出生产凭据。

- [ ] **Step 1: 冻结本轮源码，做一次整体接口复核。**

检查冻结 schema/engine 对应、v1重放和7/7、v2八项所有顺序、服务端重算、隐私边界与新约束；使用任务开始基线界定本轮 diff，不把既有 dirty 文件当成本轮修改。集中修复真正阻塞问题并定向复审，不重启已完成七项的无边界审核。

- [ ] **Step 2: 对最终源码运行完整本地测试与静态检查。**

```sh
"$PY" -m pytest apps/api/tests -q
"$RUFF" check apps/api
"$MYPY" --config-file apps/api/pyproject.toml apps/api/app
npm --prefix apps/web test -- --maxWorkers=4
npm --prefix apps/web run lint
npm --prefix apps/web run build
git diff --check
```

后端独占 DB；允许前端纯检查并行。保留第一次完整结果；若修正测试/代码后局部重跑，按唯一用例身份归并并准确说明源码时间，不累加重跑数量冒充全量通过。Task 5 浏览器若其后修改了相关产品代码须重跑。已有显式 opt-in Docker 演练 skip 必须注明，不能替代真正 NAS 发布备份恢复。

- [ ] **Step 3: 通过指定脚本发布，失败即阻塞。**

```sh
cd '/Users/skymoonzyj/Documents/new project3'
scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
```

必须检查退出码和实际脚本完成输出。脚本负责保留 NAS secrets/data、读取活动 Alembic、迁移前备份、构建与健康、加密备份及隔离恢复；不能复制文件绕过、删除数据卷、改端口或通过手改 revision 伪造迁移成功。

- [ ] **Step 4: 只读核实真正入口和发布证据，再交付。**

```sh
curl --silent --show-error --output /dev/null --write-out '%{http_code}\n' \
  'https://skymoonzyj.l.cd:18080/login'
```

按部署脚本返回的实际 release 记录本地/在线源码或镜像摘要一致、head为0059、API/Web/DB/proxy/scheduler健康、调度心跳、加密备份及隔离恢复。以 `.active-release` 为准，不用旧 `current` 符号链接猜测。不得在 NAS 创建模拟成绩来“测试上线”。需要受认证 UI 验证时仅只读打开获授权页面，不借此扩大账号操作。

最终简短告知：选难度五题、计分和系统评价入口、旧轮次保留、部署结果及真实限制。若任一门槛失败，清楚写“本地已完成／NAS尚未发布”和阻塞，不称上线成功。

## 主代理计划自审记录

- 规格覆盖：玩法/计分/严格校验→Task 1、3、4；旧轮/报告/比较/迁移→Task 2、4；响应恢复/设备中断/隐私→Task 4、5；权限/原子性/发布→Task 2、5、6。
- 五项 Review Focus 均有明确任务和失败断言；无以此前七项通过记录替代新八项验证的步骤。
- 原始字段固定 `selected_level` / `outcomes` / `measured_display_ms` / `font_size_px`，前后端相同；引擎不再靠所有 ID 拼 `-clone-v1`；历史不假定所有规则拥有 anchors。
- 六个交付边界均能独立验证；纯引擎可与后端并行，前端共享文件单一所有者，数据库单一运行者。
- 所有勾选框待实施时填入真实证据；用户审核本计划之前不写产品代码或部署。
