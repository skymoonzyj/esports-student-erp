# ERP 七项天赋测试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将本地七项测试原生接入 ERP，保存同轮进度，完成后生成有明确适用限制、按当前关系授权的训练参考报告。

**Architecture:** 现有 FastAPI/PostgreSQL 增加独立 `talent_tests` 模块；React ERP 移植纯规则引擎和交互，不运行第二套网站。后端负责评分、权限、幂等、原子完成和只读报告快照；前端负责真实游戏、可恢复提交和按权限展示。先冻结接口，再并行移植独立项目；共享文件、数据库测试及 NAS 发布由主流程串行统筹。

**Tech Stack:** Python 3.12、现有 FastAPI/Pydantic 2/SQLAlchemy 2/Alembic/PostgreSQL，React 19/TypeScript/Vite/Vitest/Playwright。复用现有依赖；不增加 Next.js、Tailwind 全局样式、Redis、外部 AI 服务或 GPU 服务。

**Spec:** `/Users/skymoonzyj/Documents/new project3/.nas-release-sync/docs/superpowers/specs/2026-09-20-talent-tests-design.md`（用户 2026-09-20 已确认）。

**Status:** 用户已于 2026-09-20 确认规格并要求按计划开发，实施中；本文代码块是实施说明，不是测试结果。实际进度与发布证据见 `docs/verification/2026-09-20-talent-tests.md`。

## Global Constraints

- 发布源码为 `/Users/skymoonzyj/Documents/new project3/.nas-release-sync`；下文相对路径均相对此目录。
- 原 `/Users/skymoonzyj/Documents/humanbenchmark-clone` 保持不动；不导入匿名 localStorage 成绩，不复制原站品牌 SVG/favicon。
- 目录 `talent-training-v1` 恰好七项：`reactiontime`、`aim`、`sequence`、`number-memory`、`verbal-memory`、`chimp`、`memory`；中文打字不进入目录、路由、校验、进度分母或报告。
- 固定版本：评分 `talent-score-v1`、报告 `talent-report-v1`、中文词库 `zh-192-v1`；各项目引擎版本使用 `<test_id>-clone-v1`。这些是本实现的稳定标识，不是原站版本。
- 每项 0–100 分；0–39 较低，40–69 中等，70–89 较高，90–100 超高。先对计时均值 half-up 到整数毫秒，再 Decimal 插值并向下取整。
- 分数列 `[0,25,50,70,90,100]` 的原始锚点分别为：反应 `[500,275,225,175,150,100]`；瞄准 `[1000,550,450,350,300,250]`；顺序 `[0,8,10,18,25,35]`；数字 `[0,7,9,13,16,20]`；词语 `[0,20,50,100,200,300]`；黑猩猩 `[0,9,11,15,20,25]`；视觉 `[0,6,10,18,25,35]`。
- 只用于训练参考；不显示 IQ、全球排名、官方百分位、职业潜力或七项平均“总天赋分”。词语和视觉全部锚点标明内部试行训练目标。
- 一人最多一轮进行中；同轮同项目一份结果；七项全部属于当前轮，不拼接历史最好成绩；完成报告不随新规则重算。
- 创建、提交、放弃分别持久化 UUID 操作键与规范化摘要；先授权，再判重放；所有提交及放弃共用轮次行锁。
- 第七份结果、报告、完成状态和完成审计同一事务；开始/放弃也与其审计同一事务。
- take 只允许 self；测试要求本人 take 和 read 同时有效；显式 deny 优先。学员 self、监护人 family、当前负责教练 assigned_class、超管 global read；普通管理员等不默认获得新权限。
- 授权新增/撤销/预览均要求实际有效超管身份及有效 `permissions.manage`；仅获授权的普通管理员不能转授本模块权限。
- 未完成轮次仅本人及有 global read 的实际超管可读；其他读者仅已完成报告。列表 SQL 先授权过滤后统计分页；越权与不存在统一私有 404。
- 所有模块响应包括错误均 `Cache-Control: no-store, private`；沿用会话、CSRF、停用与密码更新失效机制。
- 请求最多 64KiB；单项总时长最多 86,400,000ms；整数计数 0–1,000,000，另加引擎约束；计时数组恰好 5 或 30。
- 每账号模块写操作 30 次/60 秒，数据库共享频控；429 返回中文和 `Retry-After`；不按全营共用公网 IP 独占配额。
- 异常但协议合法的计时保存并标“需复测”；不计入优势/待提升判断；0ms 不是 422。负数、非有限值、矛盾字段、错误版本等 422 不消耗结果键。
- 页面隐藏中止未完成游戏；待提交结果按账号/轮次/项目隔离存于 sessionStorage，最长 24 小时；成功、登出、换账号清理，不存令牌/联系方式/答案。
- 历史列表默认 20、最多 100；业务时间服务器 UTC，界面日期中国时区 YYYY-MM-DD，审计保留时分，游戏毫秒保留。
- 比较历史须同引擎、评分、语言、输入方式及几何配置，且两项无质量警告；基准是该项目最近的可比已完成报告并持久化 ID/变化值。
- 账号软删除保留业务历史；新表无级联物理删除路径；不修改账号现有删除语义。
- 仅一个重置同一测试库的测试进程；不得用生产库、真实学员数据或生产邮件作测试。
- 本文审核后才能实施。业务更新通过本地检查后必须运行主目录 `scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'`；失败阻止发布，不绕过脚本，HTTPS 18080/域名/数据卷不变。

## Review Focus

1. 保存成功但响应丢失，刷新或切账号后不能重复计分/串账号：Task 4 终态重放、Task 7 待提交缓存与迟到响应测试。
2. 最后一项与放弃同时到达，不能完成无报告或放弃后补入成绩：Task 4 使用独立数据库连接的两种锁顺序测试。
3. 普通管理员的隐式全部权限、撤销监护/任教、deny 与多身份不能泄露人数/姓名：Task 2 种子断言、Task 3 SQL 范围和私有 404 测试。
4. 触屏、后台切换、0ms、设备尺寸改变不能误判超高/进步或永远卡在 6/7：Task 1 异常规则、Task 5 比较规则、Task 7/9/16 浏览器行为测试。
5. 失败关卡和通过关卡、反复点击错误格、词语第三次错误不能使成绩虚增：Task 10–14 引擎/遥测测试及 Task 1 跨字段校验。

---

## 执行边界、检查命令与并行所有权

执行前先读取规格、根目录 AGENTS.md、发布源 git 状态和当前迁移 head。发布源是独立 Git checkout，并非主目录的 linked worktree；其未提交内容包含既有发布功能，不能新建纯 HEAD 工作树后当作完整发布源。若要另建隔离工作树，先取得用户对工作状态迁移方式的确认；不要自动复制隐藏配置、凭证或抹掉现有修改。默认沿用用户已指定的发布源，记录本任务开始时的差异以区分旧改动。

本计划的 Python 命令在 `apps/api` 执行，先设：

```bash
export PY='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
```

必须先运行 `"$PY" -m app.cli.e2e_database_safety`；确认是隔离库，端口可达、依赖齐全后才测试。DB 环境缺失导致 SKIP 不能算通过。不使用会删除数据卷的 `check.sh`。前端命令在发布源执行 `npm --prefix apps/web ...`。

每项任务按下面 checkbox 顺序执行，单个步骤保持约 2–5 分钟的小改动/检查；大段表格逐行落地，每行一个可测增量，不一次提交整套未经验证功能。

提交规则：所有任务独立审核后才 checkpoint。新增文件按该任务 Files 清单精确 stage；已脏文件用逐 hunk stage，只包含本任务差异。执行 `git diff --cached --check` 和 `git diff --cached --stat`，如有别人已 staged 内容则停止提交协调，不动其 index。每个 checkpoint 用 `ERP_NAS_AUTO_DEPLOY=0 git commit -m '<该任务消息>'`，这是暂缓未完成开发的发布；最终 Task 16 必须显式部署，不长期关闭自动发布。禁止 `git add .`、`commit -a`、reset/checkout 用户改动。

执行依赖图：`1 → 2 → 3 → 5 → 4 → 6` 为后端主线（先完成报告构造/读取，写服务才能原子生成真实报告）；`1 → 7 → (8,9,10,11,12,13,14)` 为前端移植；`3,6,7,8–14 → 15 → 16`。Task 5 的纯报告函数可在 Task 1 后并行，查询测试直接建 Task 2 模型夹具，不依赖 Task 4 写服务。编号是任务标识，不覆盖这里的依赖顺序。

- 主流程唯一负责模型/迁移、RBAC 种子、API 总路由、AppShell/router、部署与共享测试夹具。
- 后端业务 worker 负责 `talent_tests` 服务/查询及其测试，不同时修改迁移或种子。
- 前端基础 worker 负责 contracts/client/host/cache；接口完成并通过测试后才分派游戏。
- 游戏 worker 仅修改自己目录与对应测试，不碰共享 host、目录和路由；可按实际资源并行 3–5 路，最多 8 个子任务，不为凑数拆任务。
- 所有 worker 明确“不是独占代码库，不撤销他人修改”；共享文件由 owner 集成。DB pytest 由主流程统一排队，前端/纯函数测试可并行。
- 每项先实现者验证，再独立审查规格和代码；最后整体验收。不能把本计划阅读/审核当成实现完成。

## 文件布局与冻结契约

新增后端 `apps/api/app/talent_tests/`：`__init__.py`、`catalog.py` 版本常量、`schemas.py` 严格请求/响应、`scoring.py` 纯评分、`models.py` 三表、`access.py` SQL 授权、`service.py` 写事务、`reporting.py` 纯快照构造、`queries.py` 分页读取、`rate_limit.py` 共享频控、`router.py` HTTP、`errors.py` 中文业务错误。不要把所有职责写成单一巨型 service。

新增前端 `apps/web/src/talentTests/`：`contracts.ts`、`api.ts`、`pending.ts`、`useTalentSession.ts`、`GameHost.tsx`、`GameChrome.tsx`、`TalentTestsPage.tsx`、`TalentHistoryPage.tsx`、`TalentReportPage.tsx`、`talentTests.css` 和七个 `games/<名称>/` 子目录；同目录 colocated Vitest 测试。

### 请求协议（Task 1 定义，所有任务使用）

```ts
export type TestId = 'reactiontime' | 'aim' | 'sequence' | 'number-memory'
  | 'verbal-memory' | 'chimp' | 'memory'
export type InputKind = 'mouse' | 'touch' | 'pen' | 'mixed' | 'unknown'
export type Grade = 'low' | 'medium' | 'high' | 'exceptional'
export type SessionStatus = 'in_progress' | 'completed' | 'abandoned'
export type Geometry = { width: number; height: number; target_diameter: number | null }
export type DeviceSnapshot = {
  input_kind: InputKind; viewport_width: number; viewport_height: number
  geometry: Geometry | null
}
export type RawByTest = {
  reactiontime: { samples_ms: number[]; too_soon_count: number }
  aim: { intervals_ms: number[]; miss_count: number; total_elapsed_ms: number }
  sequence: { reached_level: number; completed_level: number; failed_step: number; end_reason: 'wrong_tile' }
  'number-memory': { correct_digits: number; failed_digits: number; display_duration_ms: number }
  'verbal-memory': { correct_count: number; error_count: number; total_count: number; word_bank_version: 'zh-192-v1' }
  chimp: { reached_count: number; completed_count: number; strikes: number; end_reason: 'strikes' | 'engine_cap' }
  memory: { reached_level: number; completed_level: number; accepted_misses: number; lives_used: number; end_reason: 'lives' }
}
export type GameResult<K extends TestId> = {
  test_id: K; raw: RawByTest[K]; device: DeviceSnapshot
  duration_ms: number; background_interrupted: boolean
}
export type CompletedGame = { [K in TestId]: GameResult<K> }[TestId]
export type Submission = { [K in TestId]: GameResult<K> & {
  submission_id: string; engine_version: string; locale: 'zh-CN'
}}[TestId]
export type GameProps<K extends TestId> = {
  onComplete: (result: GameResult<K>) => void
  onInterrupted: (reason: 'hidden' | 'geometry_changed') => void
}
```

不用泛型强制断言掩盖错误 project/raw 组合；Pydantic 用 `test_id` discriminator 的七种模型，`extra='forbid'`。`submission_id` 由 host 生成，游戏组件不联网。raw 不接收客户端分数、等级、建议或答案正文。

Python 在 `schemas.py` 定义 `RawResultInput`（七种模型的 union），`RuleSnapshot`（catalog/scoring/report_version、ordered test IDs、每项 engine/anchors/source_label）、`NormalizedResult`（test_id、raw_value:int、unit:str、score:int、grade:Grade、quality_flags:list[str]、原始统计、设备、engine/scoring_version/locale）、`PersonDisplay(id:UUID,name:str,nickname:str|None)`、`ReportComparison(baseline_report_id:UUID,raw_delta:int,score_delta:int)`。`ReportSnapshot` 字段为 person、session_id、completed_at、rules、results、strengths:list[TestId]、suggestions:list[str]、comparisons:dict[TestId,ReportComparison]、mixed_devices:bool、disclaimer:str。每项 normalized 结果用共同结构；不要把规则对象实例直接存到 JSONB，使用 Pydantic JSON 模式序列化。

响应 `ResultView` 继承 NormalizedResult，额外含 id、submission_id、created_at；`SessionView`：id、person_id、status、created_at、completed_at、abandoned_at、rules:RuleSnapshot、results:list[ResultView]、report_id:UUID|null。`ResultSaveView`：session:SessionView、result:ResultView。`SessionSummary`：id、status、created_at、completed_at、abandoned_at、saved_count、report_id；`ReportSummary`：id、session_id、person:PersonDisplay、completed_at、catalog_version、scoring_version；`ReportView` 继承 ReportSnapshot，额外含 id。`Page<T>`：items:list[T]、total:int、page:int、page_size:int。`ContextView`：catalog:RuleSnapshot、can_take:bool、can_read_self:bool、can_read_reports:bool、active_session_id:UUID|null；上下文能力来自服务端，不能只判断角色名。普通列表只返回摘要，不带 raw；轮次详情和报告才带成绩详情。以上响应同时在 Python schemas 和 TS contracts 定义，UUID/时间在 TS 为 string，所有 nullable 字段明确包含 null。

HTTP 固定如下，写入不接收自由指定的 person_id：

| 方法/路径（前缀 `/api/v1/talent-tests`） | 输入 | 输出 |
| --- | --- | --- |
| GET `/context` | 无 | ContextView |
| POST `/sessions` | request_id:UUID, catalog_version:str | SessionView（首次 201，重放 200） |
| GET `/sessions` | page=1,page_size=20 | 本人 Page<SessionSummary> |
| GET `/sessions/{session_id}` | UUID | 授权 SessionView |
| POST `/sessions/{session_id}/results` | Submission | ResultSaveView（首次 201，重放 200） |
| POST `/sessions/{session_id}/abandon` | request_id:UUID | SessionView（200） |
| GET `/subjects` | page,page_size,name 可选、最多 80 字 | Page<PersonDisplay>，仅有可见已完成报告的人 |
| GET `/reports` | person_id 可选,page,page_size | Page<ReportSummary> |
| GET `/reports/{report_id}` | UUID | ReportView |

`GET /reports?person_id=` 的不存在/越权对象统一 404；未指定时返回授权集合。分页参数必须整数、page>=1、1<=page_size<=100。用户提供无效 UUID 返回通用 422，不披露对象信息。

---

### Task 1: 严格统计契约、七项评分与质量规则

**Files:** Create `apps/api/app/talent_tests/{__init__,catalog,schemas,scoring,errors}.py`、`apps/api/tests/unit/test_talent_scoring.py`、`apps/api/tests/unit/test_talent_contracts.py`；Create `apps/web/src/talentTests/contracts.ts`、`apps/web/src/talentTests/contracts.test.ts`。

**Interfaces:** Produces `get_rules(catalog_version: str) -> RuleSnapshot`、`score_value(test_id: str, raw_value: int, rules: RuleSnapshot) -> int`、`normalize_result(value: RawResultInput, rules: RuleSnapshot) -> NormalizedResult`、`fingerprint(value: BaseModel) -> str`。TS 类型即上节；常量 `TEST_IDS` 按目录顺序，不能从用户提交动态生成。

- [ ] 写边界和排除打字的失败测试：

```python
@pytest.mark.parametrize('test_id,x,expected', [
    ('reactiontime', 175, 70), ('reactiontime', 150, 90),
    ('reactiontime', 176, 69), ('sequence', 18, 70),
    ('number-memory', 16, 90), ('verbal-memory', 200, 90),
    ('chimp', 40, 100), ('memory', 25, 90),
])
def test_thresholds(test_id, x, expected):
    assert score_value(test_id, x, get_rules('talent-training-v1')) == expected

def test_catalog_is_exactly_seven_without_typing():
    assert tuple(get_rules('talent-training-v1').test_ids) == (
        'reactiontime', 'aim', 'sequence', 'number-memory',
        'verbal-memory', 'chimp', 'memory',
    )
```

- [ ] 运行 `"$PY" -m pytest tests/unit/test_talent_scoring.py tests/unit/test_talent_contracts.py -q`；预期缺少新模块/函数而失败，保留 RED 证据。
- [ ] 实现版本映射与插值。端点外截断；内段公式用 Decimal，`ROUND_FLOOR`。均值用 `sum(Decimal(str(v)) for v in samples)/len(samples)` 后 `quantize(Decimal('1'), rounding=ROUND_HALF_UP)`。摘要为模型 JSON 模式、key 排序、紧凑分隔、禁止 NaN 的 SHA256，剔除操作键后计算；规范化等价数值如 200 与 200.0 得相同摘要。

```python
def interpolate(x: int, anchors: tuple[int, ...]) -> int:
    points = sorted(zip(anchors, (0, 25, 50, 70, 90, 100)))
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, s0), (x1, s1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            value = Decimal(s0) + Decimal(x - x0) * (s1 - s0) / Decimal(x1 - x0)
            return int(value.to_integral_value(rounding=ROUND_FLOOR))
    raise ValueError('invalid score anchors')
```

此 helper 定义在 scoring.py，score_value 选择冻结规则中的 anchors 后调用；未知ID/version先由catalog拒绝。
- [ ] 增加全表 42 锚点、每段单调/阈值前后、半毫秒均值、封顶及非有限值测试，再实现逐字段校验：

| 项目 | 校验与归一化 |
| --- | --- |
| 反应 | samples 长度 5，非负有限；too_soon_count 整数；任何 sample<80 或汇总<100 标记 `timing_anomaly` |
| 瞄准 | intervals 长度 30；Decimal总和与 total_elapsed_ms 差的绝对值<=0.001ms，容纳 JS 累加尾差而不使用整数四舍五入掩盖误差；duration_ms 容差相同；geometry 必填、宽高正数、直径在场地内；均值<150 标记 `timing_anomaly` |
| 顺序 | reached>=1, completed=reached-1；1<=failed_step<=reached；reason=wrong_tile |
| 数字 | failed_digits=correct_digits+1；display_duration_ms=1000+800*failed_digits；原始分为 correct_digits |
| 词语 | error_count=3；total_count=correct_count+3；词库严格 zh-192-v1；原始分为 correct_count |
| 黑猩猩 | 4<=reached<=40；strikes 0..3；strikes 结束必须3次，completed=0（reached4）或 reached-1；cap结束 reached=completed=40且strikes<3 |
| 视觉 | reached>=1, completed=reached-1；lives_used=3；accepted_misses>=9；reason=lives；错误次数不必恰好9，因为成功关卡也可能误点 |

共同 duration<=86,400,000、整数<=1,000,000；拒绝 bool 伪整数/额外字段。视口及几何使用有限正数、最大 1,000,000，避免任意巨大值；duration 必须覆盖已知展示/计时片段。输入 unknown 标记 `unknown_input`；background_interrupted 标记 `background_interrupted`；touch/pen 标注设备适用限制，但单凭触屏不否定有效结果。0ms 协议接受，质量需复测。
- [ ] 给 TS `Submission` 写 `satisfies` 契约样本；错误 raw/test 组合用 `@ts-expect-error` 验证编译时拒绝。后端所有七种最小合法请求用 JSON fixtures 与前端保持字段一致。
- [ ] 重跑两个 pytest 文件及 `npm --prefix apps/web test -- src/talentTests/contracts.test.ts`；预期全 PASS，评分无随机数/时钟/网络依赖。
- [ ] 精确 stage 本任务新增文件，审查 staged diff，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: define seven-test contracts and deterministic scoring'`。

### Task 2: 三张表、迁移、种子权限和备份清单

**Files:** Create `apps/api/app/talent_tests/models.py`、`apps/api/tests/db/test_talent_models.py`；Create 迁移候选 `apps/api/migrations/versions/0058_talent_tests.py`；Modify `apps/api/migrations/env.py`、`apps/api/app/rbac/seed.py`、`apps/api/tests/db/test_migrations.py`、`scripts/db-schema-manifest.sh`、`apps/api/tests/cli/test_operations_docs.py`、`apps/api/tests/cli/test_local_container_acceptance_launcher.py`。

**Interfaces:** Produces SQLAlchemy `TalentTestSession`、`TalentTestResult`、`TalentTestReport`。planning 时 head=`0057_procurement_idempotency`，0058 只是候选：执行时先核对真实 head，若已前进先更新本计划的精确迁移路径/父版本，不创建分叉、不提前占号。

- [ ] 写 DB 失败测试（复用 `db` 夹具和现有 platform seed）：

```python
def test_talent_default_grants_are_not_admin_global(db):
    rows = db.execute(select(Role.key, Permission.key, RolePermission.scope_kind)
        .join(RolePermission, RolePermission.role_id == Role.id)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(Permission.key.in_(['talent_tests.take', 'talent_tests.read']))).all()
    assert set(rows) == {
        ('super_admin', 'talent_tests.take', 'self'),
        ('super_admin', 'talent_tests.read', 'global'),
        ('student', 'talent_tests.take', 'self'),
        ('student', 'talent_tests.read', 'self'),
        ('guardian', 'talent_tests.read', 'family'),
        ('coach', 'talent_tests.read', 'assigned_class'),
    }
```

- [ ] 运行 `"$PY" -m pytest tests/db/test_talent_models.py -q`；预期新表/权限尚不存在而失败。
- [ ] 依规格 7.2 创建三模型及以下命名约束。ID 用 UUID；所有归属 FK `ON DELETE RESTRICT`；timestamps timezone=True；JSONB 存规则/raw/device/summary/report；无级联 delete relationship。

| 对象 | 名称/定义 |
| --- | --- |
| sessions | `uq_talent_sessions_person_creation`(person_id,creation_key) |
| sessions | `uq_talent_sessions_active_person` UNIQUE(person_id) WHERE status='in_progress' |
| sessions | `ix_talent_sessions_person_created`(person_id,created_at,id) |
| sessions | `ck_talent_sessions_state_times` 状态与完成/放弃时间互斥一致；`ck_talent_sessions_abandon_pair` 放弃键/摘要成对且只在abandoned存在 |
| results | `uq_talent_results_session_test`、`uq_talent_results_session_submission` |
| results | `ck_talent_results_score` 0..100、`ck_talent_results_test_id` 七ID白名单 |
| reports | `uq_talent_reports_session`、`ix_talent_reports_created`(created_at,id) |

`creation_key`/submission UUID NOT NULL；摘要 64 hex CHECK；session 必须保留 catalog/scoring/report版本、rules_snapshot、created_by_user_id、person_id；result 保留 engine/locale/raw/device/normalized/score/quality；report 保留 immutable snapshot。模型和迁移一致，约束不靠 UI 保证。
- [ ] 在 PERMISSIONS 新增两个 key（现有最后序号 000077，执行确认无占用再用 000078/000079）；take 加入 SELF_SCOPE_ONLY_PERMISSIONS，并显式超管/student self；read 加入“普通管理员默认排除”集合，避免 blanket comprehension 自动 global。不要把授权维护规则误塞进只有超管可使用的权限集合，导致教练/学员自己不能用。
- [ ] Migration upgrade 建表/索引并幂等种子新权限；downgrade 仅移除本模块数据结构及本模块 role grants/overrides/permissions，不能删其他权限。隔离库演练 downgrade/upgrade，证明既有表行数不变；应用代码调用 seed 再次执行也不得授予 admin。
- [ ] manifest 更新 HEAD/MIGRATION_CHAIN、三表 REQUIRED_TABLES/ROW_COUNT_TABLES、上表索引约束、两权限；env.py 导入模型；同步三个现有 hardcoded head 测试。
- [ ] 运行 `"$PY" -m pytest tests/db/test_talent_models.py tests/db/test_migrations.py tests/cli/test_operations_docs.py tests/cli/test_local_container_acceptance_launcher.py -q`，以及发布源 `bash -n scripts/db-schema-manifest.sh`；预期 PASS。增加软删除账号保留三表记录与 FK 不级联测试。
- [ ] 本任务精确 hunk stage，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: persist talent batteries and seed least-privilege grants'`。

### Task 3: 实时人员范围与超管授权维护边界

**Files:** Create `apps/api/app/talent_tests/access.py`、`apps/api/tests/services/test_talent_access.py`；Modify `apps/api/app/rbac/service.py`、`apps/api/app/rbac/router.py`、`apps/api/tests/api/test_permissions.py`。HTTP 私有响应测试由 Task 6 创建，此任务先测权限 service 和现有权限维护 routes。

**Interfaces:** `TalentAccess(db: Session, actor: AuthContext, now: datetime)` 提供 `require_take() -> None`、`visible_person_ids() -> Select[tuple[UUID]]`、`require_person(person_id: UUID) -> None`、`require_session(session: TalentTestSession) -> None`。Produces 统一 `TalentNotFound`/`TalentForbidden`（errors.py）。扩展现有 `RbacService.preview_override` 为 keyword-only `actor_user_id: UUID, account_id: UUID, input: PermissionOverrideCreate -> PolicyDecision`；所有 router/service 测试调用点同时传入真实 actor，不提供可绕过默认值。

- [ ] 新增直接 service 调用的失败测试，不只测按钮：

```python
def test_non_superadmin_cannot_preview_talent_grant(db, platform_users):
    actor_id = platform_users['accounts']['coach'].id
    permission = db.scalar(select(Permission).where(Permission.key == 'talent_tests.read'))
    rule = PermissionOverrideCreate(permission_id=permission.id, effect='allow',
        scope_kind='global', scope_id=None, grant_reason='test')
    with pytest.raises(PermissionDelegationDenied):
        RbacService(db).preview_override(actor_user_id=actor_id,
            account_id=actor_id, input=rule)
```

这段测试放在 `tests/api/test_permissions.py`，其目录能访问 `platform_users`；services 目录自行创建账号，不隐式引用 api 子目录夹具。另给该coach有效的 `permissions.manage` global override，验证即使已有管理权限仍因非超管而拒绝；同时给真实 admin 角色账号覆盖三入口（`platform_users['accounts']['admin']` 实为 super_admin，不能用它冒充普通管理员）。
- [ ] 运行 `"$PY" -m pytest tests/services/test_talent_access.py tests/api/test_permissions.py -k 'talent' -q`；预期缺失边界而 FAIL。
- [ ] 从 `RbacService.decision_context()` 获取有效 role_rules/overrides，SQL 编译每种 talent read scope 为 person 谓词：self=actor.person.id；person=scope_id；family=有效 GuardianRelationship EXISTS；assigned_class=当前 CoachAssignment 与该学生有效 Enrollment/TeachingClass EXISTS；global=true。按 `OR(allow) AND NOT OR(deny)` 合成后用于查询，禁止先读全量人员再 Python 过滤。
- [ ] 任教有效期使用现有 CoachEligibility、valid_from/valid_until/ended_at 及当前报名/班级判定；复用教学模块真实 enrollment 状态约定，不能只检查同 game_project。把这些表达式抽为 `current_coach_student_predicate(actor_person_id: UUID, person_id_column, now: datetime) -> ColumnElement[bool]` 并在本文件定义/测试。family 不按年龄失效；assigned_class 指定 scope_id 时只匹配该班，null 时匹配当前任教各班。
- [ ] 在 add/remove/preview 三服务入口调用新 `_require_talent_grant_manager(actor_user_id: UUID, target_account_id: UUID, permission: Permission) -> None`：活跃账号+有效实际 super_admin+针对目标账号的 permissions.manage；talent take 拒绝非 self，read 拒绝未支持 scope。写路径沿用既有角色/账号锁顺序，在锁内重新授权；移除时先定位对应 permission 再授权，不能允许猜 override_id 绕过。
- [ ] 补齐矩阵测试：student、guardian、coach、superadmin、真实admin、administration、finance、assistant；双身份 allow/deny、停止监护、成年但有效监护、教练任期结束、只同项目、person授权、global授权普通人不能读partial、禁用账号、grant预览/add/remove三边界、非self take。service 对无权存在UUID与随机UUID均抛 TalentNotFound，AuditEvent及三业务表行数不变；visible_person_ids SQL结果不能带越权人员。HTTP body/cache/total由Task6整合测试。
- [ ] 重跑上述测试及 `"$PY" -m pytest tests/api/test_permissions.py -q`；预期 PASS。对象授权公共接口冻结后通知后端/frontend owner。
- [ ] 本任务 hunk stage，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'fix: enforce scoped talent privacy and superadmin delegation'`。

### Task 4: 轮次命令、重放、并发与审计原子性

**Files:** Create `apps/api/app/talent_tests/service.py`、`apps/api/tests/services/talent_helpers.py`、`apps/api/tests/services/test_talent_sessions.py`、`apps/api/tests/db/test_talent_concurrency.py`、`apps/api/tests/services/test_talent_atomicity.py`；Modify `apps/api/app/audit/service.py`（仅新增事件/metadata白名单）。

**Interfaces:** `TalentTestService(db: Session)` 的 keyword-only 方法 `start(actor: AuthContext, request_id: UUID, catalog_version: str, now: datetime) -> tuple[SessionView,bool]`、`submit(actor: AuthContext, session_id: UUID, value: RawResultInput, now: datetime) -> tuple[ResultSaveView,bool]`、`abandon(actor: AuthContext, session_id: UUID, request_id: UUID, now: datetime) -> SessionView`；bool 表示是否首次创建。Consumes Task 1/3、Task 5 `build_report` 和 `find_comparable_baselines`；集成 Task 5 前不伪造完成报告。

- [ ] 写真实 DB 基础测试，新增 `tests/services/talent_helpers.py` 定义 `student_actor(db)->AuthContext`（有效student角色、账号、Person）、`reaction_submission(submission_id:UUID)->RawResultInput`（5×200ms、mouse、duration=10000，完整metadata）及 `complete_battery(service, actor, now)->SessionView`（依次提交本计划每项合法最小结果）：

```python
def test_create_replay_after_abandon_returns_old_battery(db):
    actor = student_actor(db)
    service = TalentTestService(db)
    key, now = uuid4(), datetime.now(UTC)
    first, created = service.start(actor=actor, request_id=key,
        catalog_version='talent-training-v1', now=now)
    assert created is True
    service.abandon(actor=actor, session_id=first.id, request_id=uuid4(), now=now)
    replay, created = service.start(actor=actor, request_id=key,
        catalog_version='talent-training-v1', now=now)
    assert replay.id == first.id and replay.status == 'abandoned'
    assert created is False
```

测试名另加完成版本；这里先覆盖 abandoned 终态，完成版本用七项 helpers，不能用直接改status跳过报告事务。
- [ ] 运行 `"$PY" -m pytest tests/services/test_talent_sessions.py -q`；预期新 service 缺失而 FAIL。
- [ ] start 使用 `atomic_write_transaction(db)`，锁 Person→账号并刷新有效身份，再查同创建键（先同键后active），摘要不符409；不同键有active409，响应仅本人active_session_id；否则存全版本快照与 start 审计。提交/放弃只接受actor本人归属，即使超管也不可代测；支持超管读取不等于代写。
- [ ] submit 在同一atomic事务锁session、刷新state、重新权限；先查已成功submission_id并比摘要，相同则返回既有结果；再查test_id已有不同结果409；再判terminal409；normalize通过后写结果。完整set必须严格等于七IDs，然后调用 `build_report`，写唯一report、completed_at/status、完成审计。不要在insert失败后复用aborted Session，业务冲突应预查/以savepoint处理明确唯一冲突。
- [ ] abandon 在同一轮次行锁内检查持久abandon_key/digest，同键返回已有state且不写audit；terminal新键409；否则abandoned_at/state/key/hash/audit同事务。共用锁保证与任意submit竞争有线性顺序。
- [ ] 实现 audit action `talent_tests.started`、`talent_tests.abandoned`、`talent_tests.completed`，仅允许 session_id/person_id/catalog_version/scoring_version/report_id/操作键及必要计数，禁止raw/姓名/证件/答案。AuditService.append异常必须回滚业务，不捕获后当成功。
- [ ] 增加独立连接并发测试，沿 `test_manual_payment_concurrency.py` 的 committed UUID fixtures、Barrier/Event、每线程一个Session模式：同人同/不同创建键；同项目不同键；同键同body、同键不同body；最后项同键重放；第6/7项并发；abandon先锁和submit先锁两种顺序；第1项和abandon竞争也覆盖。每种检查轮次数、结果数、报告数、审计数以及事务后Session可继续使用。
- [ ] 增加422不占键、修正相同键可提交；重新游戏新键；完成/放弃后的成功旧请求重放；授权撤销后旧请求也不能读回；三类audit抛错freshSession断言零变化；断线不触发双完成。
- [ ] 集成 Task 5 后串行运行 `"$PY" -m pytest tests/services/test_talent_sessions.py tests/db/test_talent_concurrency.py tests/services/test_talent_atomicity.py -q`；预期全部 PASS；不能仅用fixture savepoint测试代替真实并发。此时原子性测试调用service，Task6再通过HTTP注入audit异常检查500与回滚。
- [ ] 精确 stage，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: save talent batteries atomically with replay protection'`。

### Task 5: 不可变报告、可比基准与授权分页读取

**Files:** Create `apps/api/app/talent_tests/reporting.py`、`apps/api/app/talent_tests/queries.py`、`apps/api/tests/unit/test_talent_reporting.py`、`apps/api/tests/services/test_talent_queries.py`。

**Interfaces:** `build_report(*, session_id: UUID, person: PersonDisplay, completed_at: datetime, rules: RuleSnapshot, results: Sequence[NormalizedResult], baselines: Mapping[str, tuple[UUID,NormalizedResult]]) -> ReportSnapshot`；`find_comparable_baselines(db: Session, *, person_id: UUID, results: Sequence[NormalizedResult], before: datetime) -> dict[str,tuple[UUID,NormalizedResult]]`；`TalentQueries(db:Session,actor:AuthContext,now:datetime).context()->ContextView`、`.sessions(page:int,page_size:int)->Page[SessionSummary]`、`.session(session_id:UUID)->SessionView`、`.subjects(name:str|None,page:int,page_size:int)->Page[PersonDisplay]`、`.reports(person_id:UUID|None,page:int,page_size:int)->Page[ReportSummary]`、`.report(report_id:UUID)->ReportView`。

- [ ] 在 `test_talent_reporting.py` 定义 `normalized_seven()`（由Task1合法请求normalize得到七项）、`report_for(results,baselines=None)`（固定UUID/person/date/rules调用build_report），写失败测试：

```python
def test_warning_never_becomes_strength_or_weakness():
    results = normalized_seven()
    reaction = next(r for r in results if r.test_id == 'reactiontime')
    reaction.quality_flags = ['timing_anomaly']
    reaction.score = 100
    report = report_for(results)
    assert 'reactiontime' not in report.strengths
    assert '需复测' in '\n'.join(report.suggestions)
    assert '总天赋分' not in report.model_dump_json()
```

- [ ] 运行 `"$PY" -m pytest tests/unit/test_talent_reporting.py -q`；预期 FAIL。
- [ ] 实现报告：校验七ID各一次；按目录排序；quality项显示需复测，优先提示复测但不推导能力；无warning且score>=70进入strengths；建议优先low，否则medium，否则保持。固定提示逐字保存：“本报告用于训练参考，受设备、环境和练习影响，不作为智商、诊断或职业潜力判定。”
- [ ] 建议常量版本化：反应“规律作息，固定设备进行少量练习”；瞄准“先保持准确，再练习平稳移动”；顺序“分组记忆位置与顺序”；数字“尝试分组记忆并循序增加位数”；词语“练习词义联想和重复辨认”；黑猩猩“观察数字空间位置，再按顺序回忆”；视觉“分区观察图形位置与结构”。设备敏感项加“本次操作环境下”，touch/pen/mixed/unknown显示内部参考，不输出诊断/选拔/职业结论。
- [ ] 比较键=`engine_version,scoring_version,locale,input_kind,viewport_width,viewport_height,geometry`；unknown/mixed或双方warning不产生进步结论；逐项目 SQL 查此前已完成结果，按 completed_at desc/report_id desc取最近可比，最多七个有界查询，不载入全部历史；存 baseline ID、raw_delta=current-baseline、score_delta=current-baseline，计时改善用下降解释而非原始正数。mixed_devices按本轮各项输入/视口是否不同推导，geometry跨项目天然不同不能单独判换设备。
- [ ] 所有列表 `.where(person_id.in_(access.visible_person_ids()))` 再 count/order/offset/limit；self sessions只当前人；其他subjects仅已完成report。报告读取返回持久snapshot，不重新跑规则或用当前名字覆盖快照；历史新旧版本可并存。索引覆盖person+created排序，追加执行计划检查不出现Python全量过滤。
- [ ] 增加测试：新的catalog规则不改变旧snapshot；不可比设备/不同几何/不同locale不给delta；最近不可比则向前选最近可比，而不是历史最好；不同项目可用各自baseline；七项不来自别轮；all-warning也能完成；无低项/全高/混合设备文案；分页total不漏授权，稳定排序，无邮箱/证件字段。
- [ ] 运行 `"$PY" -m pytest tests/unit/test_talent_reporting.py tests/services/test_talent_queries.py -q`；预期 PASS。查询测试直接插入一致的冻结模型/snapshot，不依赖尚未实现的Task4；Task4完成后再一起验证真实写读流程。
- [ ] 精确 stage，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: generate immutable scoped training reports'`。

### Task 6: HTTP、私有错误和跨 worker 频控

**Files:** Create `apps/api/app/talent_tests/rate_limit.py`、`apps/api/app/talent_tests/router.py`、`apps/api/tests/api/test_talent_routes.py`、`apps/api/tests/api/test_talent_privacy.py`、`apps/api/tests/db/test_talent_rate_limit.py`；Modify `apps/api/app/api/router.py`、`apps/api/app/main.py`（模块路径响应保护）。

**Interfaces:** `TalentWriteLimiter(session_factory: Callable[[],Session], pepper: bytes).record(account_id:UUID,now:datetime)->None`；`TalentRateLimited(retry_after:int)`；新增 `get_talent_write_limiter(request)->TalentWriteLimiter` FastAPI dependency，生产工厂用现有 `SessionLocal`，测试注入隔离engine的独立Session。router依Task1冻结端点。

- [ ] 写 limiter 并发测试，同账号连续30通过，第31抛错，两个实例/不同连接共享预算：

```python
def test_budget_survives_business_rollback(postgres_engine):
    factory = sessionmaker(bind=postgres_engine)
    limiter = TalentWriteLimiter(factory, b't' * 32)
    account_id, now = uuid4(), datetime.now(UTC)
    for _ in range(30):
        limiter.record(account_id, now)
    with pytest.raises(TalentRateLimited) as caught:
        TalentWriteLimiter(factory, b't' * 32).record(account_id, now)
    assert caught.value.retry_after == 60
```

- [ ] 运行 `"$PY" -m pytest tests/db/test_talent_rate_limit.py tests/api/test_talent_routes.py -q`；预期新limiter/routes缺失 FAIL。
- [ ] 实现 DB AuthRateLimit：HMAC(pepper,`talent-write\0<account_id>`)；独立transaction先advisory锁该digest再select for update，首次窗口attempts=1，窗口>=60秒重置，满30拒绝且不延长窗口；在transaction退出后再raise429，不能把计数附着到业务回滚。Retry-After=ceil(windowend-now)最少1；每次有界清理过期记录，沿现有索引。不要复用登录失败语义/账号锁定机制。
- [ ] 路由先会话/CSRF/当前基础权限，然后频控，再业务事务（无权请求不占业务预算）；合法schema但409/业务422仍计入请求频控。读取不调用limiter；频控不写成绩/audit。Payload超过64KiB在JSON解析前拒绝413，不能只信Content-Length；流式累积最多上限+1并封装回传body，不无限`request.body()`后才检查。
- [ ] 对 `/api/v1/talent-tests` 所有响应建立窄作用域ASGI包装：成功、404、422、401/403、413、429和500均覆写 `Cache-Control: no-store, private`；不能只在成功route的Response上设置。错误映射中文：不可见404“未找到可访问的测评记录”，active409“已有进行中的测评，请继续或放弃本轮”，冲突409“本项目已保存，请刷新查看”，422“测试数据不符合当前规则，请重新完成本项”，429“操作过于频繁，请稍后重试”。不把Pydantic输入值/raw打印到响应或日志。
- [ ] API tests覆盖context能力、九端点、准确状态码、CSRF、chunked大body、unknown typing、协议失败零写入、429header、不同账号同出口不共限额、跨连接31并发只30通过；业务audit失败后预算保留而成绩回滚。无权现有和随机对象相同body/header且零业务audit。
- [ ] 运行 `"$PY" -m pytest tests/api/test_talent_routes.py tests/api/test_talent_privacy.py tests/services/test_talent_atomicity.py tests/db/test_talent_rate_limit.py tests/api/test_auth_sessions.py -q`；预期 PASS。
- [ ] 精确 stage，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: expose private talent APIs with shared write limits'`。

### Task 7: 前端自动保存、恢复缓存与统一游戏容器

**Files:** Create `apps/web/src/talentTests/{api.ts,pending.ts,useTalentSession.ts,GameHost.tsx,GameChrome.tsx,talentTests.css}`及 `api.test.ts`、`pending.test.ts`、`GameHost.test.tsx`；Modify `apps/web/src/auth/AuthProvider.tsx`、`AuthProvider.test.tsx`（仅清理模块缓存生命周期）。

**Interfaces:** API函数 `getTalentContext():Promise<ContextView>`、`createTalentSession(request_id:string,catalog_version:string):Promise<SessionView>`、`getTalentSession(sessionId:string):Promise<SessionView>`、`submitTalentResult(sessionId:string,value:Submission):Promise<ResultSaveView>`、`abandonTalentSession(sessionId:string,request_id:string):Promise<SessionView>`；使用现有 `apiFetch<T>(path,init)`，不重造CSRF。`putPending(accountId:string,sessionId:string,value:Submission,now:number):void`、`readPending(accountId:string,sessionId:string,testId:TestId,now:number):Submission|null`、`removePending(accountId:string,sessionId:string,testId:TestId):void`、`clearTalentPending():void`；now为epoch毫秒。`useTalentSession(accountId:string,sessionId:string)`暴露 `session:SessionView|null,saveState:'idle'|'saving'|'pending'|'saved'|'rejected',submit(value:CompletedGame):Promise<void>,retry():Promise<void>,abandon():Promise<void>`；`GameHost({accountId:string,sessionId:string,testId:TestId})`负责版本组件映射。`GameChrome({title:string,instructions:string,children:ReactNode,statusText:string|null})`只负责展示，游戏组件 Props 沿冻结契约。

- [ ] 写 cache 隔离/到期/失败重试测试：

```ts
it('隔离账号且 24 小时后丢弃待保存结果', () => {
  const a = 'f1000000-0000-4000-8000-000000000001'
  const b = 'f1000000-0000-4000-8000-000000000002'
  const session = 'f1000000-0000-4000-8000-000000000003'
  const value = reactionSubmission('f1000000-0000-4000-8000-000000000004')
  putPending(a, session, value, 1000)
  expect(readPending(b, session, 'reactiontime', 1001)).toBeNull()
  expect(readPending(a, session, 'reactiontime', 1001)).toEqual(value)
  expect(readPending(a, session, 'reactiontime', 86_401_001)).toBeNull()
})
```

在同文件定义 `reactionSubmission(id:string):Submission`：test_id=reactiontime、samples_ms=五个200、too_soon_count=0、mouse、viewport=1440×900、geometry=null、duration_ms=10000、background_interrupted=false、engine_version=reactiontime-clone-v1、locale=zh-CN、submission_id=id；生产解析与测试均使用合法UUID。
- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/pending.test.ts src/talentTests/GameHost.test.tsx`；预期模块缺失/行为失败。
- [ ] 实现 account/session/test命名空间和版本化storage envelope `{savedAt,value}`，try/catch处理quota损坏/禁用storage；缓存只做便捷恢复，提交仍经后端。只保存完成统计，不保存序列/数字/逐词答案。创建/放弃请求也稳定保留操作键，在请求不明时不能换键创建；账号命名空间内保存 `{request_id,catalog_version}` 或 `{request_id,session_id}`，成功清理。
- [ ] 保存状态固定 `idle|saving|pending|saved|rejected`。先固化Submission+UUID→写pending→POST；成功核对服务端session/result再saved并清理；网络/5xx保持原键重试；409先GET详情判断是否同submission已存，否则中文冲突；422丢无效pending允许重新游戏新键；429按Retry-After倒计时手动重试，禁止无限轮询。401/403/私有404停止提交、移除当前渲染，重新登录验证身份归属后才处理pending。
- [ ] 监听AuthProvider身份变更/注销清模块sessionStorage并移除queryKey `['talent-tests',accountId,...]`。所有异步回调捕获账号generation，迟到上一个账号的响应不得更新新账号进度。页面刷新GET服务端后匹配已存submission_id，只有仍归本账号且未保存才允许原键重试。
- [ ] 统一 visibilitychange 取消未完成游戏timer/RAF并递增game instance key，显示“页面切换已中止本项，请重新开始”；已经生成pending的结果不能因隐藏而丢弃。useRef终态门闩保证onComplete一次；resize瞄准几何改变中止本项，不混合几何。version→component显式映射，未知版本提示联系管理员且不静默换新规则。输入采集使用pointerType实际事件而非UA，混合标mixed。
- [ ] 测试补齐先成功后断响应/刷新、logout pending、切账号迟到response、invalid storage、storage满、隐藏前后不同阶段、429、三种冲突、组件StrictMode重复effect、已保存项目不能重做。所有pending页面给出清空浏览器数据可能丢失未提交结果的说明。
- [ ] 重跑新增测试及AuthProvider测试；预期 PASS，计分只显示服务器score，前端不自制另一评分算法。
- [ ] 精确 stage，checkpoint：`ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: add recoverable talent submissions and shared game host'`。

## 七项引擎移植共同来源

源根目录 `S=/Users/skymoonzyj/Documents/humanbenchmark-clone/src/components/sites/humanbenchmark-com-d891757d`。目标均在 `apps/web/src/talentTests/games/`。每项测试的 engine 导出名称保留源代码，适配层只补遥测/Props/ERP样式。使用 apply_patch 新建代码，不改S。保留每个源游戏的展示时长、生命周期和计分语义，不在移植中顺便优化规则。

保留 `/Users/skymoonzyj/Documents/humanbenchmark-clone/LICENSE` 的 MIT 全文至 `apps/web/src/talentTests/THIRD_PARTY_NOTICES.md` 并列本次复用文件；版权为 JCodesMore 2025。原站提取 SVG 不随 MIT 项目整包复制。该说明由 Task 8 首次创建，后续owner只提供来源路径，由主流程集中补齐清单。

### Task 8: 反应时间

**Files:** Create `apps/web/src/talentTests/games/reaction/{engine.ts,ReactionGame.tsx,engine.test.ts,ReactionGame.test.tsx}`、`THIRD_PARTY_NOTICES.md`。

**Interfaces:** 源 `S/tests-reactiontime-4b259c35/reactionTimeLogic.ts`、`ReactionTimeGame.tsx`；导出 `ReactionGame(props:GameProps<'reactiontime'>)`，保留 `measureReactionTime`、`appendReactionSample`、`averageReactionTime`。

- [ ] 写失败测试：

```ts
it('五次有效反应后才汇总，抢点不算试次', () => {
  expect(averageReactionTime([200, 220, 240, 260])).toBeNull()
  expect(averageReactionTime([200, 220, 240, 260, 281])).toBe(240)
  expect(appendReactionSample([218, 241], null)).toEqual([218, 241])
  expect(measureReactionTime(100, 100.2)).toBe(0)
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/reaction`；预期 FAIL。
- [ ] 原样移植纯engine，去掉Next/旧saveScore。组件以performance.now计时；源engine每次measure已round，raw发送这些有效samples，不伪称更精确值。waiting→tooSoon时计抢点一次，提示页连续点不增加；第5有效试次构造 `{test_id:'reactiontime',raw:{samples_ms,too_soon_count},device,duration_ms,background_interrupted:false}`，只调用一次onComplete。
- [ ] 组件测试假timer/受控random覆盖等待、变色、抢点、5次、双击末次、pointer类型；visibility中断让host重新开始，保留其他已保存项目。不要把响应时间0丢掉。
- [ ] 重跑同命令 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port reaction-time test to ERP'`。

### Task 9: 瞄准训练

**Files:** Create `apps/web/src/talentTests/games/aim/{engine.ts,AimGame.tsx,AimGame.css,engine.test.ts,AimGame.test.tsx}`。

**Interfaces:** 源 `S/tests-aim-a98ccd39/{aimEngine.ts,AimGame.tsx,AimGame.module.css}`；导出 `AimGame(GameProps<'aim'>)`；保留 createAimState/beginAimTest/recordAimHit/recordAimMiss/targetDiameterForWidth/randomTargetPosition/clampTargetPosition。

- [ ] 写失败测试：

```ts
it('固定 30 次命中，失误不重置计时', () => {
  let state = beginAimTest(createAimState(), 5000)
  expect(recordAimMiss(state)).toBe(state)
  for (let i = 1; i <= 30; i++) state = recordAimHit(state, 5000 + i * 100)
  expect(state.phase).toBe('result')
  expect(state.scoreMs).toBe(100)
  expect(targetDiameterForWidth(700)).toBe(100)
  expect(targetDiameterForWidth(699)).toBe(64)
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/aim`；预期 FAIL。
- [ ] 移植源engine和局部CSS，geometry来自实际stage的ResizeObserver；每次命中收集**engine采用的** `Math.max(1,now-targetStartedAt)` 间隔，30项之和发送total_elapsed_ms，不另取未经clamp的0间隔。场内未命中仅playing pointerdown增加miss_count，按钮/开始前/命中冒泡不计失误；不扣分。geometry变动调用onInterrupted('geometry_changed')，旧timer清理并重启本项。
- [ ] 测试32个交错hit/miss、空场点击、触屏、目标边界、最后双击不超30、timer后台终止、resize不混合成绩；100ms合法但服务器标需复测，不能在UI自动判超高。
- [ ] 重跑 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port geometry-aware aim test'`。

### Task 10: 顺序记忆

**Files:** Create `apps/web/src/talentTests/games/sequence/{engine.ts,SequenceGame.tsx,engine.test.ts,SequenceGame.test.tsx}`。

**Interfaces:** 源 `S/tests-sequence-dba3dd7b/{sequenceRules.ts,SequenceGame.tsx}`；导出 `SequenceGame(GameProps<'sequence'>)`；保留 createInitialSequenceState/startSequence/beginResponse/submitSequenceTile/restartSequence。

- [ ] 写失败测试：

```ts
it('失败第一关是到达 1、通过 0，不丢失快速相同格输入', () => {
  const first = beginResponse(startSequence(createInitialSequenceState(), 0.01))
  const lost = submitSequenceTile(first, 8, 0.99)
  expect(lost.phase).toBe('result')
  expect(lost.level).toBe(1)
  const second = submitSequenceTile(first, 0, 0.99)
  expect(second.level).toBe(2)
  expect(second.sequence).toEqual([0, 8])
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/sequence`；预期 FAIL。
- [ ] 移植原交互的600/400/300ms序列展示及300ms反馈；反馈闪烁不能disable responding输入。错误动作前记录 `failed_step=inputIndex+1`，终态raw reached=level/completed=level-1；展示期间点格忽略，不记录成失败。
- [ ] 组件测试连续相同tile快点、showing期间忽略、错误步骤、隐藏中止展示、最后一次onComplete；界面明确“到达”，不显示“成功通过”同数。
- [ ] 重跑 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port sequence-memory test with reached-level semantics'`。

### Task 11: 数字记忆

**Files:** Create `apps/web/src/talentTests/games/number/{engine.ts,NumberGame.tsx,engine.test.ts,NumberGame.test.tsx}`。

**Interfaces:** 源 `S/tests-number-memory-1d143e99/{numberMemory.ts,NumberMemoryGame.tsx}`；导出 `NumberGame(GameProps<'number-memory'>)`；保留 createNumberString/memoryDurationMs/evaluateNumberAnswer。

- [ ] 写失败测试：

```ts
it('失败位数不计入最长正确位数', () => {
  expect(memoryDurationMs(1)).toBe(1800)
  expect(memoryDurationMs(5)).toBe(5000)
  const result = evaluateNumberAnswer('12', '21', 2)
  expect(result.correct).toBe(false)
  if (result.correct) throw new Error('expected failed answer')
  expect(result.scoreDigits).toBe(1)
  expect(result.failedLevel).toBe(2)
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/number`；预期 FAIL。
- [ ] 移植正确首位非零随机生成、展示→输入→检查流程；terminal构造correct_digits=scoreDigits、failed_digits=failedLevel、display_duration_ms=memoryDurationMs(failedLevel)。duration只用于总时长限制；不上传数字题目/输入原文。数字keyboard移动端可用，Enter/按钮双击不双保存。
- [ ] 测试首关失败0分位、进入下一关后失败、展示隐藏中止、前导零输入按源行为判断、payload没有answer/expected字段。
- [ ] 重跑 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port number memory with successful-digit scoring'`。

### Task 12: 中文词语记忆

**Files:** Create `apps/web/src/talentTests/games/verbal/{engine.ts,VerbalGame.tsx,engine.test.ts,VerbalGame.test.tsx}`。

**Interfaces:** 源 `S/tests-verbal-memory-005272b3/{verbalMemoryEngine.ts,VerbalMemoryGame.tsx}`；导出 `VerbalGame(GameProps<'verbal-memory'>)`；保留 CHINESE_WORDS/startVerbalMemory/answerVerbalMemory。

- [ ] 写失败测试：

```ts
it('累计正确判断而非不同词数量，第三错结束', () => {
  let state = startVerbalMemory(['山川'], () => 0)
  state = answerVerbalMemory(state, 'new', ['山川'], () => 0)
  state = answerVerbalMemory(state, 'seen', ['山川'], () => 0)
  expect(state.score).toBe(2)
  for (let i = 0; i < 3; i++) state = answerVerbalMemory(state, 'new', ['山川'], () => 0)
  expect(state.finished).toBe(true)
  expect(state.turn).toBe(5)
  expect(state.score).toBe(2)
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/verbal`；预期 FAIL。
- [ ] 移植192词库与50%seen/new选择策略；保留 answeredTurn 防重复处理；答错的首次词也进入seen集合。final raw `{correct_count:state.score,error_count:3,total_count:state.turn,word_bank_version:'zh-192-v1'}`；词库不重新翻译，不上传seen集合/逐词答案。
- [ ] 测试192词版本、第三错、错后再次出现同词、按钮连击只处理当前turn一次、隐藏中止；标题/报告均“中文词语记忆”“累计正确判断”，标内部试行目标。
- [ ] 重跑 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port Chinese verbal-memory test'`。

### Task 13: 黑猩猩测试

**Files:** Create `apps/web/src/talentTests/games/chimp/{engine.ts,ChimpGame.tsx,engine.test.ts,ChimpGame.test.tsx}`。

**Interfaces:** 源 `S/tests-chimp-e17b1ab1/{chimpRules.ts,ChimpGame.tsx}`；导出 `ChimpGame(GameProps<'chimp'>)`；保留 CHIMP_BOARD_SIZE/createChimpBoard/createInitialChimpState/startChimp/continueChimp/selectChimpCell/restartChimp。

- [ ] 写失败测试：

```ts
it('第三次错误结束当前难度，首关未通过不记为 4', () => {
  const board = createChimpBoard(4, () => 0)
  const state = { phase: 'playing' as const, numberCount: 4, strikes: 2,
    board, nextNumber: 1, masked: false }
  const lost = selectChimpCell(state, 1)
  expect(lost.phase).toBe('result')
  expect(lost.strikes).toBe(3)
  expect(lost.numberCount).toBe(4)
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/chimp`；预期 FAIL。
- [ ] 移植40格、从4开始、4难度首点击不mask、>=5首正确后mask、3strike逻辑；空格不strike。适配终态：失败end_reason=strikes，completed_count初始4失败为0，其余n-1；完成40为engine_cap且completed=40。评分25封顶不能把游戏提前结束在25。
- [ ] 测试空格点击、mask边界、重试同难度、累积第三错、完成40、按源padding适应触屏、最终payload只统计无棋盘；记录场地geometry供比较，不同geometry不报进步。
- [ ] 重跑 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port chimp test with distinct reached and completed difficulty'`。

### Task 14: 视觉记忆

**Files:** Create `apps/web/src/talentTests/games/visual/{engine.ts,VisualGame.tsx,VisualGame.css,engine.test.ts,VisualGame.test.tsx}`。

**Interfaces:** 源 `S/tests-memory-3a1d7d5f/{memoryLogic.ts,VisualMemoryGame.tsx,VisualMemoryGame.module.css}`；导出 `VisualGame(GameProps<'memory'>)`；保留 gridSizeForLevel/createRound/startVisualMemory/beginAnswering/selectTile/advanceAfterSuccess。

- [ ] 写失败测试：

```ts
it('同一错误格只累计一次，三个不同错误格扣命', () => {
  let state = beginAnswering(createRound(1, 3, () => 0))
  const wrong = Array.from({ length: 9 }, (_, i) => i).filter(i => !state.targets.includes(i))
  state = selectTile(state, wrong[0])
  expect(selectTile(state, wrong[0])).toBe(state)
  state = selectTile(state, wrong[1])
  state = selectTile(state, wrong[2])
  expect(state.phase).toBe('lost-life')
  expect(state.lives).toBe(2)
  expect(state.misses).toHaveLength(3)
})
```

- [ ] 运行 `npm --prefix apps/web test -- src/talentTests/games/visual`；预期 FAIL。
- [ ] 移植源引擎与500/1000/300/450ms展示/反馈常量。accepted_misses在源状态机真正接受新wrong tile时累计：`next !== before && next.misses.length > before.misses.length`；当前源第三错保留3个misses，因此会计入。createRound重置本轮misses但不重置累计计数；无效/重复点击不累加。结束raw reached=level/completed=level-1/lives_used=3/end_reason=lives。
- [ ] 测试同错格快速重复、3不同误点、失命后同格新round允许再累计、带误点成功升级、三命用尽到达1通过0、展示中输入忽略、后台中止；实际格子尺寸记录并标内部试行目标。
- [ ] 重跑 PASS；精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: port visual-memory test and accepted-miss telemetry'`。

### Task 15: ERP 菜单、目录/历史/报告与权限页面

**Files:** Create `apps/web/src/talentTests/{TalentTestsPage.tsx,TalentHistoryPage.tsx,TalentReportPage.tsx}`及三个 `.test.tsx`；Modify `GameHost.tsx`、`talentTests.css`、`apps/web/src/app/router.tsx`、`router.test.tsx`、`apps/web/src/layout/AppShell.tsx`、`AppShell.test.tsx`、`apps/web/src/settings/PermissionsPage.tsx`、`PermissionsPage.test.tsx`；Create `docs/features/talent-tests.md`。

**Interfaces:** 路由 `/talent-tests`、`/talent-tests/history`、`/talent-tests/sessions/:sessionId/:testId`、`/talent-tests/reports/:reportId`。新增 `AnyPermissionRoute({permissions:readonly string[],children:ReactNode})` 与现有PermissionRoute共用hasEffectivePermission；landing take/read任一允许，但游戏按context.can_take及后端所属状态。API补listTalentSessions/listTalentSubjects/listTalentReports/getTalentReport对应Task1路径/响应。

- [ ] 写回归测试：

```tsx
it('天赋菜单不能被教学重定向规则改写', () => {
  render(<MemoryRouter><AppShell permissions={new Set(['talent_tests.read'])} /></MemoryRouter>)
  const links = screen.getAllByRole('link', { name: '天赋测试' })
  expect(links).toHaveLength(2)
  for (const link of links) expect(link).toHaveAttribute('href', '/talent-tests')
  expect(screen.queryByRole('link', { name: '中文打字' })).not.toBeInTheDocument()
})
```

沿现有AppShell.test.tsx导入MemoryRouter；桌面和移动导航各一个链接，不用getByRole单元素选择器误报重复。
- [ ] 运行 `npm --prefix apps/web test -- src/layout/AppShell.test.tsx src/app/router.test.tsx src/talentTests/TalentTestsPage.test.tsx`；预期新菜单/页面缺失 FAIL。
- [ ] AppShell navigation独立项目含take/read any-of；先将现有“凡有sectionPermissions就跳teachingDestination”的map限制为教学父菜单，再加入talent项。AuthenticatedLayout把两talent scoped_permissions映射到菜单可见集合；guard兼容self/family/assigned_class，不能只看global字符串。
- [ ] 页面七卡按catalog顺序、`x/7`、继续、明确放弃确认、新轮、自动保存状态；无take用户不显示游戏操作区。read-only guardian/coach进入授权人员和报告列表，无可读报告显示空态，不显示无权操作的占位按钮。历史20/page可翻页，查看详情只信服务端report_id。
- [ ] host映射七components与固定engine版本；未知test/typing URL显示不可用，不允许路由参数绕过catalog。报告原始值/单位、到达与通过区别、四档或需复测、规则依据、优势/建议/不可比原因；无总分/排名雷达误导；七项较多可用简明表格/移动端卡片。
- [ ] PermissionsPage新增两中文key、take仅self/read支持五scope，提示追加take须配套read。对普通权限管理员隐藏本模块增删预览操作但保留必要的只读权限展示；超管且权限有效可操作。前端显示不替代Task3服务校验。UI日期用现有formatShanghaiDate；来源/隐私/设备限制说明写入docs/features。
- [ ] 增加测试：scoped菜单可见、普通管理员无入口、superadmin explicitdeny无能力、多身份、只read不能游戏、直接URL无权、7/7仅服务器完成、报告异常项/四档/手机号证件不渲染、390/360宽无溢出、键盘数字输入可用；调用API403后清敏感query数据。
- [ ] 运行 `npm --prefix apps/web test -- --maxWorkers=4`、`npm --prefix apps/web run lint`、`npm --prefix apps/web run build`；预期全PASS。只更改本任务引入的失败，既有失败单独记录根因，不扩大到无关重构。
- [ ] 精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'feat: integrate talent testing and scoped reports into ERP navigation'`。

### Task 16: 全链路验收、完整回归、独立审查与 NAS 发布

**Files:** Create `apps/web/tests/talent-tests.spec.ts`、`apps/web/tests/talent-touch.spec.ts`、`apps/api/app/cli/seed_talent_e2e.py`、`apps/api/tests/cli/test_seed_talent_e2e.py`、`docs/verification/2026-09-20-talent-tests.md`；Modify `scripts/run-core-e2e.sh`（加入talent专用seed与两个spec，保留既有默认流程）、`apps/web/playwright.config.ts`（新增真正hasTouch/isMobile触屏project，避免只改宽度假冒触屏）。

**Interfaces:** `python -m app.cli.seed_talent_e2e` 只在existing e2e_database_safety认可的隔离库运行，创建固定测试用student/guardian/assigned coach/unrelated coach/真实admin及关系，复用现有E2E安全凭证生成/传递方式，不写入生产或代码中的通用默认密码。harness在talent spec前运行该seed，其他spec继续原seed_e2e。

- [ ] 写浏览器失败测试：真实点完七项（用测试时钟推进展示，受控Math.random只在Playwright page注入，不加生产“跳关/伪成绩”接口），断线一次重放后最终报告恰好一份：

```ts
test('七项保存可续接且最终报告没有中文打字', async ({ page }) => {
  await loginTalentStudent(page)
  await page.getByRole('link', { name: '天赋测试' }).click()
  await page.getByRole('button', { name: '开始新一轮' }).click()
  await completeReactionThroughUI(page)
  await page.reload()
  await expect(page.getByText('1/7', { exact: true })).toBeVisible()
  await completeRemainingSixThroughUI(page)
  await expect(page.getByRole('heading', { name: '训练参考报告' })).toBeVisible()
  await expect(page.getByText('中文打字', { exact: true })).toHaveCount(0)
})
```

在该spec定义 `loginTalentStudent`（登录label“账号”、测试seed凭证）、`completeReactionThroughUI`（5次waiting→green点击）、`completeRemainingSixThroughUI`（aim30hits，其余按原引擎可最早正常失败结束、非API补成绩）。源 `humanbenchmark-clone/tests/e2e/games.spec.ts` 提供真实交互参考；只移植交互，不使用其localStorage/saveAndCheck。触屏spec调用真实touch tap，完成七项并显示设备限制。
- [ ] 启动已核验隔离环境，用harness跑新增spec，记录首次因feature未接通而失败的断言（若Task15已接通则先加入缺失断网/角色切换断言；不得故意改坏生产代码造RED）。harness的新seed分支先用CLI unit测试验证生产URL拒绝和fixture幂等。
- [ ] 增加真实API+UI情景：任意次序、刷新继续、最后保存响应丢失、账号切换、后台中止、放弃确认、guardian当前可见/解除后404、coach撤任、普通admin无权、permission grant/deny、旧报告规则不变。只在隔离库操作关系；生产仅做只读健康检查。
- [ ] 完整后端串行执行：`"$PY" -m pytest --junitxml=../../docs/verification/talent-backend.xml`；回发布源运行 `'/Users/skymoonzyj/Documents/new project3/.venv/bin/ruff' check apps/api` 和 `'/Users/skymoonzyj/Documents/new project3/.venv/bin/mypy' apps/api/app`。纯测试失败先根因定位，不降低验证约束；记录唯一test数，重跑不能重复计入通过数。
- [ ] 完整前端运行 `npm --prefix apps/web test -- --maxWorkers=4`、`npm --prefix apps/web run lint`、`npm --prefix apps/web run build`；顺序再跑 `scripts/run-core-e2e.sh`（E2E_PYTHON_BIN 指主虚拟环境，所需FIELD/session/evaluation tokens仅隔离环境临时注入，不复制NAS秘密）。不得与DB pytest同时跑共享测试库。
- [ ] 审查最终全feature diff与原始基线：新源文件许可证、所有authorization gates、版本快照/迁移head、7项无typing、事务/并发证据、缓存跨账号、对现有教学/财务/登录无回归。新鲜独立reviewer只审本功能，不把旧dirty差异当成本任务；修复blocking findings再重测。验证文档写清命令、退出码、通过/skip/未测、任何限制，不写凭证/个人数据。
- [ ] 本地全部通过后精确stage/checkpoint `ERP_NAS_AUTO_DEPLOY=0 git commit -m 'test: verify talent batteries end to end and document release checks'`；准备部署前重新确认source状态/未审无关差异与既有NAS发布源一致，不能把新出现的别人未审核代码捎带上线。
- [ ] 在主工作区运行唯一发布入口：

```bash
scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
```

预期preflight、迁移前备份、新head、服务health、schedulerheartbeat、加密备份与隔离恢复全部通过。任何失败停止发布并报告，不直拷文件、手工重建Docker或改80/443来绕过。
- [ ] 发布后只读核对公共login/health TLS、活动release标识与镜像/source hash、新Alembichead、manifest三个表/约束及两权限、备份隔离恢复行数。不会在生产生成测试成绩/账号。若用户允许用真实账号验收，则只浏览其授权页面；真正游戏验收已在隔离环境完成。
- [ ] 向用户交付简要结果、入口“天赋测试”、七项/四档说明、通过证据及NAS同步状态；只有上述证据完成才可称已上线，说明浏览器自报成绩不具备监考防作弊保证。

## 计划自检清单（主流程，计划编写阶段）

- [x] 规格1–4：Task1与8–14覆盖七项来源/许可、中文打字排除、四档/anchors/half-up/floor及质量提示。
- [x] 规格5：Task4/5/7覆盖状态、幂等、原子完成、断线、immutable报告及兼容历史；Review Focus 1/2/4有独立测试。
- [x] 规格6：Task2/3/6/15覆盖种子、授权维护三入口、实时关系、deny、SQL分页、私有404/cache、audit最小字段；Review Focus 3有测试。
- [x] 规格7：Task1/2/6/7覆盖严格契约、三表、大小/频控边界、账号软删除、版本组件兼容；Review Focus 5在Task10–14具体测试。
- [x] 规格8–9：Task16覆盖隔离DB/E2E/迁移/备份恢复/强制部署，未把文档审核当业务上线。
- [x] 主流程自检已修正报告/写服务依赖顺序、请求重放所需submission_id响应、实际夹具字段、双导航测试选择器及浮点累计容差；跨任务接口与七项口径一致。以上勾选仅表示计划审核，不表示产品实现或测试通过。

本计划审核后推荐“子任务实施 + 独立复核”：先固定接口和后端约束，再并行七项移植。沿用用户“任务流数量看实际情况”的选择，不强制同时开满八路。
