# 端游 FPS 固定靶区 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 网格射击与六目标射击使用固定靶区，所有目标在整个可转动范围内完整可见，保留老成绩与独立版本排名。

**Architecture:** 新增 pc-fps-v5 固定网格引擎，v4 引擎原样保留。前后端登记同一版本参数；计分/报告复用 v4，排名沿用完整配置快照隔离。保持现有 16:9 游戏画布及跟枪行为。

**Tech Stack:** React / TypeScript / Three.js / Vitest / Playwright；FastAPI / Pydantic / SQLAlchemy / pytest；标准 NAS 发布脚本。

**Spec:** `docs/superpowers/specs/2026-11-10-pc-fps-fixed-board-design.md`

**执行记录（2026-11-10）：** Task 1–3 已完成并分别审查通过；最终增量审查通过。前端 331 文件 / 3917 测试、后端相关 1723 测试、ESLint、生产构建及后端静态检查通过。Chrome 原生鼠标锁定和窗口/全屏验收通过，三项各 60 秒正式结果已在隔离服务端保存并复核。超宽/DPR 1/2 为补充布局测试，不代表所有实体显示器验收。Task 4 正在进行 NAS 标准发布；详细检查和发布结果追加在 `.superpowers/sdd/2026-11-10-pc-fps-fixed-board/progress.md`。以下保留原实施步骤。

## Global Constraints

- 执行源码工作树：`/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`，不是默认主目录。保留当前未提交成果；记录本次增量，不自动提交、清理或重置既有脏工作树。
- catalog_version、engine_version 为 `pc-fps-v5`；scoring_version、report_version 为 `pc-fps-v4`；raw/params 仍 `multi_target`。
- 网格：3 目标、5×3、间距 9°、yaw ±18°、pitch ±9°；六目标：6 目标、5×5、间距 6°、yaw/pitch ±12°。水平 FOV 90°。
- 固定原点 (0,1.6,0)、距离 10；最低完整轮廓边距 1°。16:9 canvas，舍入容差 `abs(width-height*16/9)<=1 CSS px`，实际视锥仍须过边距检查。
- 正式测试和服务端练习均为 60 秒，不取票的本地试操作为 20 秒；目标直径、练习乘数、参考命中数、50ms 间隔及计分不改；90°跟枪不改。
- v1–v4 的登记参数/引擎/历史/恢复不改；禁止清空 pending、重算旧成绩、偷偷缩靶；其他套题开放状态不改。
- 只用隔离数据验证；数据库用例串行运行；真实鼠标锁定验收须前台窗口。没有真实验收证据不能声称验收通过。
- 实施完成并通过检查后运行标准 NAS 发布，不旁路；本计划文档阶段不部署。

## Review Focus

1. 最大练习目标、相反角点与大幅鼠标输入：全部目标轮廓仍可见、可瞄准；Task 2 的独立投影和可达性测试锁定。
2. 同一时刻输入/命中、重复补位和触边反向输入：未命中目标静止、无随机耗尽、无隐藏余量；Task 2 时间线回归锁定。
3. 升级时老操作键、旧练习票、待提交正式轮次：返回旧配置而非误用 v5；Task 1 服务测试锁定。
4. 超宽外屏、全屏过渡和分数 CSS 像素：保持实际 16:9、无 HUD 遮挡、预检发生在取票前；Task 2/3 测试锁定。
5. 新旧三项与旧四项历史并存：文案/前三/本人排名正确隔离、跟枪不误用限幅；Task 1/3 测试锁定。

---

## 文件边界与执行顺序

Task 1 负责后端版本与共享契约；Task 2 只负责 FPS 几何/引擎；Task 3 负责展示、回归夹具/验收。Task 1 先冻结契约，Task 2/3 再并行；同一文件仅一个实现者。主代理整合复核、测试和发布。各实现者必须尊重同工作树其他人的修改。

### Task 1: 登记 v5，同时保留 v4 数据链路

**Files:**
- Modify: `apps/api/app/esports_assessments/{config_schemas,catalog,rule_versions,ranking}.py`、`apps/api/app/audit/service.py`
- Modify: `apps/web/src/esportsAssessments/{contracts,versionRules}.ts`
- Create: `apps/web/src/esportsAssessments/testSupport/catalog-v5.json`、`fixturesV5.ts`
- Create: `apps/api/tests/unit/test_esports_v5.py`、`apps/api/tests/services/test_esports_v5.py`
- Test: `apps/api/tests/{unit,services}/test_esports_v4.py`、`apps/api/tests/services/test_esports_rankings.py`、现有 frontend contracts / versionRules 测试

**Interfaces:**
- `registered_catalog_version('pc-fps', None)` → `'pc-fps-v5'`；显式 v1–v4 继续有效。
- `members_for_catalog('pc-fps', 'pc-fps-v5')` → `('gridshot','sixshot','spheretrack90')`；v4 同三项，v1–v3 老四项。
- 新增前端 `isPcFpsTriadCatalog(suiteId: SuiteId, catalogVersion: string): boolean`，仅 pc-fps-v4/v5 为 true，Task 3 复用。
- 新增 `fixtureV5Config(testId: 'gridshot'|'sixshot'|'spheretrack90', difficulty = 3): TaskConfig`，从新 catalog-v5 fixture 读取。保持 fixturesV4、fixtureCurrentConfig 的既有含义。

- [ ] 写失败测试 `test_v5_catalog_registered_and_v4_unchanged`：默认 v5，精确比较两端 v5 fixture；grid spacing/pitch 为 9/9、six 为 6/12，v4 仍 10/70 和 6/70；每项五档。`test_v5_rule_identities` 断言 engine v5、score/report v4、raw 仍原 kind。改一处登记参数必须被拒绝。
- [ ] 写失败服务测试：`test_v4_pending_creation_replay_after_v5_default`、`test_v4_pending_submission_after_v5_default`、`test_v4_practice_ticket_after_v5_default`；保留创建操作键、快照、报告。`test_v4_v5_rankings_are_separate` 用已完成的 v4/v5 轮次验证各单项及总榜前三/本人/样本数互不混入。
- [ ] 在 `apps/api` 运行 `python -m pytest tests/unit/test_esports_v5.py -q`，确认因 v5 未登记而失败；服务测试在隔离 PostgreSQL 环境运行，不连接 NAS。
- [ ] 最小实现版本登记与精确参数映射。v5 跟枪参数原样，评分和报告复用 v4；排名 top-three v4-only 分支扩为显式 v4/v5。仅 catalog 审计白名单增加 v5；不改 scoring/reporting/raw schemas，不新增 SQL migration。
- [ ] 前端同步版本注册、精确参数和 `registeredRule`；实现两个新导出，生成新 fixture；禁止覆盖旧 fixtures。
- [ ] 运行 v4/v5 单元和服务测试、ranking 回归，前端 contracts/versionRules 测试。预期全部通过；对已知原始结果同时喂 v4/v5，断言分数相同；跟枪参数和得分逐字段一致。
- [ ] 保存本任务增量 diff，进行任务规格与代码审查；不提交整个既有脏工作树。

### Task 2: 固定靶区引擎与连续可见性

**Files:**
- Create: `apps/web/src/esportsAssessments/fps/fixedGrid.ts`、`fixedGrid.test.ts`
- Create: `apps/web/src/esportsAssessments/fps/multiTargetV5.ts`、`multiTargetV5.test.ts`
- Modify/Test: `apps/web/src/esportsAssessments/fps/{index,schedule}.ts` 及同名 `.test.ts`
- Preserve: `fps/multiTarget.ts`、`gridCandidates.ts`、全局 `math.ts` 的旧行为；跟枪引擎不改。

**Interfaces:**
- 输入复用 `GridGeometry`、`Size`、`AngleDirection`；`Camera` 为现有 `FpsSceneSnapshot['camera']`，不要另造相机类型。
- `FixedGridBounds` 包含 readonly `yaw_min_deg, yaw_max_deg, pitch_min_deg, pitch_max_deg: number`。
- `FixedGridLayout` 包含 readonly `candidates: readonly AngleDirection[]`、`bounds: FixedGridBounds`、`padding_deg: 1`。
- `createFixedGridLayout(params: GridGeometry, viewport: Size): FixedGridLayout`。
- `validateFixedGridViewport(params: GridGeometry, viewport: Size): void`，失败抛有意义的中文 Error；内部复用同一布局/解析验证，避免互相递归。
- `applyFixedGridLook(camera: FpsSceneSnapshot['camera'], dx: number, dy: number, bounds: FixedGridBounds): FpsSceneSnapshot['camera']`。
- `createMultiTargetV5Engine(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine`，只接受登记的 pc-fps-v5 gridshot/sixshot。

- [ ] 先写 `fixedGrid.test.ts`：精确候选数 15/25、角点范围、固定顺序；五档 × 全候选 × 边界/内部相机，完整球边缘余量 ≥1°；所有候选中心均可瞄准。独立使用 Three 相机投影每球 64 个轮廓点，不只调用生产可见性函数；解析下界另外断言 >2.98° / >2.78°（最大目标、标准 16:9）。
- [ ] 写 `multiTargetV5.test.ts`：种子 123、1600×900 复现旧版转动后遗留目标出界，新版必须全部可见；每项五档、至少 100 个 seed、每 seed 200 次命中补位，保证数量恒定、无重叠、不复用刚命中格、未命中目标坐标逐字段不变、重放一致。
- [ ] 补输入边界测试：极大有限 dx/dy 到边界、反向小量立即生效、NaN/Infinity 不污染相机；同时间 look/fire、60 秒结束及 finish 重复调用维持原规则；v4 170°转动原测试继续通过。
- [ ] 在 `apps/web` 运行 `npm test -- src/esportsAssessments/fps/fixedGrid.test.ts src/esportsAssessments/fps/multiTargetV5.test.ts`，确认因新增模块/行为缺失而失败。
- [ ] 实现固定候选与规格中的解析边距验证：固定世界 yaw/pitch 网格、原点/距离不变；边界由网格半跨度导出且 pitch 与登记限制一致。明确拒绝有限性、正尺寸、比例或余量不合格的 viewport。
- [ ] 实现新引擎，沿用 ClickFpsTimeline、createRng、nearestAngularHit、freezeResult；只有候选来源、世界锚点及相机限幅改变。候选不足立即报错，不无限循环或改目标尺寸；旧引擎保持独立。
- [ ] `createFpsEngine` 对 exact engine_version v5 的 grid/six 路由新引擎；其余登记旧版本维持原路径。`validateFpsViewport` 在相同分支调用新验证，跟枪不走固定网格验证。
- [ ] 运行 `npm test -- src/esportsAssessments/fps`，再复核全部新行为。补尺寸测试：标准 16:9、小数像素容差、容差外的超宽画布拒绝；不同 DPR 不改变候选；跟枪 v5/v4 同输入同场景/结果（除版本元数据）。
- [ ] 保存增量并接受任务审查，尤其检查解析证明适用范围、完整球轮廓及目标中心可达性。

### Task 3: 页面兼容、取票前验证与隔离端到端

**Files:**
- Modify/Test: `apps/web/src/esportsAssessments/{AssessmentHome,AssessmentSessionPage,AssessmentReportPage,AssessmentHost}.tsx` 及同名测试
- Modify/Test: `apps/api/app/cli/seed_esports_assessments_e2e.py`、`apps/api/tests/cli/test_seed_esports_assessments_e2e.py`
- Modify/Test: `apps/api/tests/api/test_esports_public_opening.py`、`test_esports_routes.py`
- Modify: `apps/web/tests/esports-pc.spec.ts` 及实际引用的隔离 fixture helper

**Interfaces:**
- 消费 Task 1 的 `isPcFpsTriadCatalog`、`fixtureV5Config`，Task 2 的原有 `validateFpsViewport` 公共入口；不修改上述接口。
- 开放套题仍仅 pc-fps；旧 v4 是旧三项，v1–v3 才是旧四项。新/旧排名以当前报告快照为准，不按当前默认 catalog 猜测。

- [ ] 写失败页面测试：v5 三项与前三/差距正常显示；v4 历史显示旧三项，不误称四项；v1–v3 四项提示保留。说明文本包含“目标固定在靶区内，视角到边缘会停止转动”。
- [ ] 写取票顺序测试：非法 v5 viewport 下 start-attempt 与 practice-ticket 请求次数均为 0；合法 16:9 可进入。全屏/窗口使用实际 canvas 尺寸；退出或尺寸变化维持既有中断安全机制，不能改参继续正式计分。
- [ ] 跑对应页面 Vitest，确认新版本提示/预检缺失导致预期失败；再替换 v4-only 展示判断并添加必要文案，不改路由安全或全屏控制。
- [ ] 隔离 CLI 新创建轮次使用 v5，显式历史 fixture 仍保持 v4；更新当前默认断言及端到端 helper，不批量重写历史测试。运行 CLI/API 和页面测试确认通过。
- [ ] 隔离 Chrome 验收：窗口/全屏分别进入两项练习，完整扫描边界、连打刷新；正式三项提交后看到服务端保存结果/独立报告；验证 v4 历史可读。检查超宽外屏留边、DPR 1/2、HUD 无遮挡和 90°跟枪原样。只使用虚拟账号并记录可复核证据，结束撤销测试会话和清理测试数据。
- [ ] 若真实鼠标锁定/前台条件不具备，记录确切阻断，不用模拟测试冒充真人浏览器验收，也不自行省略发布前验收。

### Task 4: 整合验证与 NAS 发布

- [ ] 主代理审查本次增量、历史 fixture 未变、无敏感数据或生产测试记录；独立最终代码审查并修正发现的问题。
- [ ] `apps/web`：`npm test -- --maxWorkers=4`、`npm run lint`、`npm run build` 均退出 0。保留原始测试数量与结果，不沿用上次绿色报告。
- [ ] `apps/api`：隔离数据库串行运行所有 `test_esports*.py` 单元/服务/API、相关 CLI fixture 和审计测试；运行 Ruff、mypy 对所有改动后端模块及直接依赖。执行前列出实际存在的用例文件，记录数量和耗时；没有运行完整后端套件时明确注明。
- [ ] 从本工作树运行 `PATH='/Volumes/MacDev/Projects/new project3/.venv/bin':"$PATH" NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`。任何预检、备份、恢复验证或部署失败均停止发布并报告；不复制文件或手工重建容器旁路。
- [ ] 只读核对 NAS `.active-release`、容器健康、HTTPS、当前 catalog v5 / 开放套题和旧 catalog 可读取；线上不创建成绩。确认发布代码与本次验收源一致。
- [ ] 提交用户结果摘要：固定靶区行为、新旧排名隔离、实际测试与发布结论、未验收项（如有）。部署成功前不得称“已上线”。

## 计划自检

- 规格映射：固定靶区/可见性→Task 2；版本/排名/历史→Task 1；窗口全屏/说明→Task 3；验证发布→Task 4。
- 五个 Review Focus 均有明确测试步骤；接口类型取自现有项目，9°/12° pitch 字段现为正数类型，可直接按版本登记。
- 无数据库迁移，无历史清理，无新评分算法；只增加必要版本分支及独立引擎。
- 状态：用户已确认并执行实施，代码与本地验收完成；NAS 发布状态以执行记录为准。
