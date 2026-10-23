# 天赋评测 2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 ERP 增加独立的「天赋评测 2」，交付 PC FPS、手机 FPS、PC MOBA、手机 MOBA 四套共16项原创正式测试、自由练习与隔离的历史报告。

**Architecture:** 共享 ERP 认证、事务和权限基础设施，新建独立业务模块、五表、API、前端宿主和缓存。正式轮次和单项练习采用不同业务表，服务端冻结配置并重算成绩；纯游戏引擎通过统一输入／时钟接口输出有界统计，渲染不掌管业务保存。旧 talent_tests 的七／八项数据、规则、报告和接口保持不变。

**Tech Stack:** 现有 FastAPI/Pydantic/SQLAlchemy/PostgreSQL、React 19/TypeScript/Vite/Vitest/Playwright；新增本地打包 `three@0.186.0` 与开发类型 `@types/three@0.186.0`，FPS用WebGL2、MOBA用Canvas2D。不新增React Three Fiber、物理引擎、外部AI或独立游戏服务器。

**Spec:** [已批准规格](../specs/2026-09-21-talent-assessment-2-design.md)，用户已回复「确认规格」，并于2026-09-22回复「确认 进行开发吧」批准执行本计划。实施进度和审查裁定记录在本计划的独立SDD台账中，不以本文件的计划内容代替验证证据。

## Global Constraints

- 独立菜单名称「天赋评测 2」，前端根 `/esports-assessments`，API根 `/api/v1/esports-assessments`；共用ERP账号，不嵌套在旧天赋测试下。
- 四套为 `pc-fps`、`mobile-fps`、`pc-moba`、`mobile-moba`，每套4项；有本模块权限即可自由选择，不按报名项目限制。
- 正式每项60秒，试练最多20秒，倒计时3秒；计时区间 `[0, 60000ms)`，模拟步长1/120秒；无计时中暂停或拼接。
- 正式固定难度；练习五档，难度3与正式参数相同；练习只有原始指标，不补正式进度、不出正式评分／报告。
- 灵敏度0.5—2.0，步长0.05；手机按钮48—96 CSS px、摇杆96—160 CSS px，正式前冻结设置；手游手机横屏、多指。
- PC场景至少800×450 CSS px、手机560×280 CSS px；FPS水平视野90°、俯仰±70°；MOBA固定逻辑1000×562.5并等比缩放。
- 单次帧间隙>500ms中断；>50ms帧间隙累计>3000ms时显示需复测。连续跟踪按时长积分，不按帧数计分。
- 正式0—100整数分，ROUND_HALF_UP；0—39较低、40—69中等、70—89较高、90—100超高；均为内部训练规则，不宣称人群常模或防作弊认证。
- 每人每套最多一轮正式进行中；每账号同时最多一个实际运行尝试；运行占用90秒，提交宽限30分钟；未确认结果先对账再重测。
- 独立权限 `esports_assessments.take`（self）和 `.read`（self/family/assigned_class/person/global）；显式拒绝优先，读取先授权过滤；修改授权须实际有效超管和permissions.manage。
- 请求最大64KiB；正式history与practice分别分页20、最大100；新pending前缀 `erp:esports-assessments:`，最多24小时，但提交截止仍是30分钟。
- 写入总预算30/60秒，正式创建6/10分钟，练习创建12/分钟；使用独立限流命名空间和独立事务，返回中文429及Retry-After。
- 禁止改旧talent_tests目录/成绩/缓存语义，不将旧动态视力输入框修复混入此计划，不覆盖任何已有未提交改动。
- 全部适用验证成功后只通过主仓库`scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'`发布；保留域名、HTTPS18080、密钥和数据卷，不在NAS写模拟成绩。

## Review Focus

1. sessionStorage满额或被禁用：测试仍可完成并在线保存，明确提示刷新恢复受限，不能把写缓存成功当成成绩已保存。测试归Task 7。
2. 用户离开页面／换账号时懒加载渲染器才返回：旧模块不得重启RAF、接管鼠标或显示他人结果。测试归Task 9和15。
3. 手机时钟偏差／创建响应延迟：90秒占用按服务器时间和单调计时预算处理，不因设备日期快慢误判，也不启动注定超时的尝试。测试归Task 5和15。
4. 横屏安全区、页面缩放或屏幕旋转导致控件越界：开始前明确拒绝，运行中只中断当前项，保留已保存进度；不静默裁切／改变命中区。测试归Task 8和18。
5. 待保存结果期间教练关系／权限撤销或账号停用：幂等重放也先鉴权，不恢复旧账号数据，不新增结果／报告／完成审计。测试归Task 4、6和15。

---

## 0. 执行环境、所有权与命令约定

实际发布源码为 `/Users/skymoonzyj/Documents/new project3/.nas-release-sync`，不是主仓库旧的apps副本。文件路径除特别注明外均相对该发布源码。当前文档基线为`c2f0eb2`，大量业务文件已有未提交改动，不能把HEAD等同完整发布基线。

执行开始时使用using-git-worktrees技能检查隔离环境；若建立隔离工作树，必须先以受控补丁／本地归档保全并带入当前发布源码的已部署未提交内容，禁止从旧HEAD直接开工后覆盖发布源。不能安全保留时停下确认，不自动stash、reset、checkout或删除已有文件。此计划阶段不创建工作树。

只有协调者修改公共接线文件（rbac/seed、rbac/service、router、AppShell、AuthProvider、manifest、Dockerfile、check.sh与锁文件）。场景工作者只写分配目录，不能回退他人改动。按依赖启用有实际工作量的子任务，不为凑8路而拆分；纯引擎／单测可并行，所有共享DB与浏览器harness必须串行。

执行检查的固定上下文：

```sh
cd '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
export PY='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'
export RUFF='/Users/skymoonzyj/Documents/new project3/.venv/bin/ruff'
export MYPY='/Users/skymoonzyj/Documents/new project3/.venv/bin/mypy'
export PYTHONPATH="$PWD/apps/api"
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
```

此URL仅是已约定的本地可丢弃测试库，不是生产凭据。`postgres_engine`每个pytest进程会重置该库schema；必须协调独占，不能同时运行两套DB测试，不能在harness使用中运行`scripts/check.sh`。测试库不可用时不能把skip计为验证通过。

各任务先RED后GREEN，代码片段是关键实现／测试契约，不是允许省略其余验收的示例。每项审查通过后只提交本项实际文件，使用显式路径，禁止`git add .`。开发中的中间提交故意暂停自动发布，命令作用域使用`ERP_NAS_AUTO_DEPLOY=0`；最终发布必须显式运行部署脚本。生产数据、密码、完整日志和测试密钥不写计划或最终消息。

## 1. 文件边界、类型和任务依赖

| 边界 | 文件／目录 | 责任 |
| --- | --- | --- |
| 后端纯规则 | `apps/api/app/esports_assessments/{schemas,raw_schemas,config_schemas,catalog,scoring,quality,reporting}.py` | 封闭契约、冻结配置、纯计分、质量和报告 |
| 后端业务 | `apps/api/app/esports_assessments/{models,access,queries,locks,lifecycle,formal_service,practice_service,rate_limit,errors}.py` | 五表、授权、分页、运行占用、幂等事务 |
| HTTP | `apps/api/app/esports_assessments/{router,dependencies,http_support}.py` | API、中文problem、私有响应、体积限制 |
| 前端业务 | `apps/web/src/esportsAssessments/{contracts,api,keys,pending,availability,useAssessmentSession}.ts` | 独立契约、请求、缓存和会话恢复 |
| 页面 | `apps/web/src/esportsAssessments/{AssessmentHome,AssessmentSessionPage,PracticePage,AssessmentHistoryPage,AssessmentReportPage,AssessmentReportsPanel,AssessmentHost,AssessmentLayout}.tsx` | 菜单入口、模式、宿主、记录与报告 |
| 公共运行时 | `apps/web/src/esportsAssessments/engineRuntime/`、`input/`、`settings/` | 时钟、几何、质量、能力、键鼠／多指和控件布局；避开部署对runtime目录的排除 |
| FPS共用 | `apps/web/src/esportsAssessments/fps/` | 纯射击／跟踪引擎、Three渲染器和场景组件 |
| MOBA共用 | `apps/web/src/esportsAssessments/moba/` | 纯移动／攻击／弹道／双任务引擎、Canvas渲染器 |
| 四套装配 | `apps/web/src/esportsAssessments/suites/{pcFps,mobileFps,pcMoba,mobileMoba}.ts` | 四套工厂，不含网络／保存逻辑 |
| 独立验收 | `apps/api/tests/*/test_esports_*`、`apps/web/src/esportsAssessments/**/*.test.*`、`apps/web/tests/esports-*.spec.ts` | 单元、权限、并发、真实浏览器与手机记录 |

新API DTO使用snake_case，前端保持同名，避免后端／前端分别发明字段。`contracts.ts`必须与OpenAPI DTO对应，不以`any`或无边界Record替代原始统计。

核心新接口由Task 1落地：

```ts
type SuiteId = 'pc-fps' | 'mobile-fps' | 'pc-moba' | 'mobile-moba'
type Mode = 'formal' | 'practice'
type TestId = 'precision' | 'tracking' | 'switching' | 'recognition'
  | 'move-fire' | 'last-hit' | 'orb-walk' | 'dodge' | 'map-awareness'
  | 'skill-aim' | 'move-attack'
type RunState = 'running' | 'awaiting_submission' | 'interrupted' | 'expired' | 'submitted'
```

- `TaskConfig`：suite_id、test_id、catalog_version、engine_version、scoring_version、report_version、duration_ms、difficulty、所属引擎的类型化参数；服务器返回深拷贝不可变快照。
- `SuiteConfig`：suite_id、catalog_version、四项test_ids、以test_id为键的TaskConfig；正式目录只使用difficulty=3。
- `ControlSettings`：sensitivity、按钮/摇杆合法矩形数组，FPS鼠标模式；不相关字段拒绝。`EnvironmentSnapshot`：输入类别、手机环境声明、场地宽高、DPR、能力实测，不收设备唯一ID。
- `QualityInput`：duration_ms、max_frame_gap_ms、long_frame_gap_total_ms、input_kind、hidden/interrupted标记；禁止NaN/Infinity、布尔冒充数值。
- `AttemptView`：id、mode、session_id（practice为null）、suite_id、test_id、state、server_now、issued_at、run_until、submit_until、seed、config、settings、environment。seed为服务器产生的32位无符号整数。
- `ResultSubmit`：operation_key、raw、quality；不接收person_id、score或grade。`ScoredResult`：metrics、score（整数）、grade（非null）、quality_flags；`PracticeResult`：metrics、score:null、grade:null、quality_flags。
- `SessionView`：id、suite_id、status、冻结members（4项）、results、report_id。`ResultSaveView`是mode判别联合：formal含result_id、scored:ScoredResult、session:SessionView；practice含result_id、scored:PracticeResult、session:null，使用grade前必须收窄到formal。
- `AssessmentReportView`：id、session_id、套系／规则／人员展示快照、4项结果、建议、可比历史及不可比原因；不包含联系方式。

原始统计联合类型按kind判别，机会总数再由TaskConfig校验：

| kind | 必需字段及计数 |
| --- | --- |
| aim | trials[{hit,latency_ms}]，precision=30、switching=40；shots、wrong_target_shots；后者precision固定0、switching计非当前高亮靶命中，≤未正确命中的发射数；失败latency取该窗口上限 |
| tracking | coverage_ms、loss_count、reacquisition_ms数组（≤loss_count）；累计覆盖≤60000 |
| recognition | trials[{target_kind,shot,hit,latency_ms}]共30，敌友各15，target_kind与冻结seed调度对应 |
| move_fire | rounds[{hit,reached}]共20、shots；共同完成必须同一索引 |
| last_hit | waves[{success,early,late,offset_ms}]共15；early/late为非负攻击次数、每波合计不超过4，未成功offset_ms为null |
| rhythm | rounds[{attacked,reached,cancelled_count}]共30；取消次数为非负整数，受该窗口攻击间隔上限约束 |
| dodge | threats[{hit,response_ms}]共30；无反应response_ms为null |
| skill_aim | trials[{released,hit,angle_error_deg,release_latency_ms,hit_latency_ms}]共30；未释放不允许hit，未释放/未命中对应时间为null，成功命中时间不能早于释放 |
| map | main_hits布尔数组30、events[{response,latency_ms}]共12、unprompted_count；response为correct/wrong/missed |

每一类另含对应kind；时间有限且在本机会范围内，布尔严格类型。shots最大300且不少于有效命中；攻击取消等按固定60秒可执行次数检查；无事件点击计数最高60000，只是载荷安全界限，不能截断分母或将多次点击变为多次正确。

共享纯引擎接口由Task 8定义并冻结：

```ts
interface GameEngine {
  input(action: InputAction): void
  advanceTo(elapsed_ms: number): void
  scene(): SceneSnapshot
  finish(): RawResult
}
type InputAction =
  | {kind:'look'; at_ms:number; dx:number; dy:number}
  | {kind:'move'; at_ms:number; x:number; y:number}
  | {kind:'fire'; at_ms:number; pressed:boolean}
  | {kind:'point'; at_ms:number; x:number; y:number; button:0|2}
  | {kind:'skill'; at_ms:number; x:number; y:number; release:boolean; cancel?:boolean}
interface GameFactory {
  create(config: TaskConfig, seed: number, settings: ControlSettings, viewport: Size): GameEngine
}
```

`SceneSnapshot`在Task 8中定义为fps/moba判别联合：fps含相机yaw/pitch/位置、目标及检查点；moba含角色、目标、弹道和小地图事件。对象有稳定ID、逻辑坐标、类型和显示状态；渲染层不能改引擎状态。动作的at_ms是从正式开始的单调偏移，迟到／越界动作按契约忽略，不回滚模拟。

依赖顺序：

```text
1 契约 → 2 计分、3 数据、7 客户端契约
3 → 4 授权 → 5 正式 → 6 练习/API
1 + 7 → 8 输入时钟 → 9 FPS共用 → 10 PC FPS / 11 手机FPS
                  └→ 12 MOBA共用 → 13 PC MOBA / 14 手机MOBA
6 + 7 + 8 + 10/11/13/14 → 15 宿主 → 16 历史报告
15 + 16 → 17 后端全链路 → 18 浏览器/真机 → 19 最终核验/发布
```

公共契约未通过审查前不并行改消费端；Task 3-6的DB验证串行。Task 9与12可并行，之后四套装配可并行；每个任务仍需独立实现审查和最终集成审查。

### Task 1: 冻结四套目录、严格契约与测试样本

**Files:** Create `apps/api/app/esports_assessments/__init__.py`、`schemas.py`、`raw_schemas.py`、`config_schemas.py`、`catalog.py`；Create `apps/api/tests/unit/test_esports_contracts.py`、`test_esports_catalog.py`、`esports_samples.py`；Create `apps/web/src/esportsAssessments/contracts.ts`、`testSupport/catalog-v1.json`、`testSupport/fixtures.ts`。

**Interfaces:** 产出本计划§1全部DTO、`get_suite(suite_id: str) -> SuiteConfig`、`get_task(suite_id: str, test_id: str, difficulty: int = 3) -> TaskConfig`，`validate_raw(config: TaskConfig, value: object) -> RawResult`和`validate_schedule(config: TaskConfig, raw: RawResult, seed: int) -> None`；后者在服务层用已存seed检查敌我顺序等可重建约束，绝不接受客户端指定seed。测试样本`sample_raw(suite_id, test_id, *, perfect: bool, seed: int = 123) -> dict`逐项目返回明确计数，不直接导入旧talent辅助函数。catalog定义`AVAILABLE_SUITES: frozenset[str] = frozenset()`；配置查询函数仍可供纯测试读取，业务创建单独检查开放状态。

前端用现有zod建立TaskConfig等DTO的运行时schema；`fixtureConfig(suite_id,test_id)`由schema解析catalog-v1.json，不能只做类型断言。另提供`fixtureSettings(suite_id)`、`fixtureQuality(input_kind)`、`fixturePending()`、`fixtureAttempt()`。默认灵敏度1、duration60000、max_frame_gap16、long_frame_gap_total0、hidden/interrupted=false；pending使用固定测试UUID、formal/pc-fps/precision、30个失败trial（latency2000）、shots0、wrong_target_shots0和固定created_at；attempt默认formal/running、issued_at=server_now、run_until加90秒、submit_until加30分钟。后续测试片段的config/settings/value/view显式由对应fixture取得。布局测试仍在本文件给出具体矩形，不以空对象强转。

- [ ] **1. RED：目录不得跨套接受项目，map保留42个机会。** 写以下起点并补足4套×4项、正式禁选难度、五档倍率、未知版本／额外字段／非有限数用例。

```python
def test_four_suites_have_frozen_four_members():
    assert get_suite("pc-fps").test_ids == (
        "precision", "tracking", "switching", "recognition")
    assert len(get_suite("mobile-moba").test_ids) == 4

def test_map_has_two_complete_channels():
    raw = validate_raw(get_task("pc-moba", "map-awareness"),
                       sample_raw("pc-moba", "map-awareness", perfect=True))
    assert (len(raw.main_hits), len(raw.events)) == (30, 12)
```

- [ ] **2. 运行RED：** `"$PY" -m pytest apps/api/tests/unit/test_esports_contracts.py apps/api/tests/unit/test_esports_catalog.py -q`；预期缺新模块或契约断言失败，不能用语法错误充当RED。
- [ ] **3. GREEN：** StrictModel统一`ConfigDict(extra="forbid", frozen=True)`，整数用StrictInt、数字先拒绝bool再允许有限float；成员／类型／数量由catalog校验。四套配置逐项录入规格§5—6，不从旧目录复制可变对象。固定seed算法采用xorshift32，零种子规范为0x6D2B79F5；洗牌采用Fisher–Yates：i从length−1降到1，每步推进一次RNG，j=state%(i+1)，交换i/j；敌我target_kind必须与该冻结顺序一致。练习按参数列修改深拷贝，不影响正式对象。

```python
def next_u32(value: int) -> int:
    value = value or 0x6D2B79F5
    value ^= (value << 13) & 0xFFFFFFFF
    value ^= value >> 17
    value ^= (value << 5) & 0xFFFFFFFF
    return value & 0xFFFFFFFF
```

- [ ] **4. 验证：** 重跑上述两文件，`"$RUFF" check apps/api/app/esports_assessments apps/api/tests/unit/test_esports_contracts.py apps/api/tests/unit/test_esports_catalog.py apps/api/tests/unit/esports_samples.py`；TS DTO编译通过。冻结一个0种子和一个非0种子的前10值金样，后续JS不得更换RNG。catalog-v1.json完整记录四套get_suite的JSON输出（先输出查看，再用apply_patch保存）；后端目录单测读取该文件并逐字段比较，防止前后端测试快照漂移。
- [ ] **5. 审查／提交：** 只提交上述Task 1新文件，提交说明`feat: define esports assessment contracts and catalogs`；检查无旧目录变更。

### Task 2: 服务器计分、质量标志与确定性报告

**Files:** Create `apps/api/app/esports_assessments/scoring.py`、`quality.py`、`reporting.py`；Create `apps/api/tests/unit/test_esports_scoring.py`、`test_esports_quality.py`、`test_esports_reporting.py`。

**Interfaces:** 消费TaskConfig/RawResult/QualityInput；产出`score_result(config, raw, quality) -> ScoredResult`、`build_report(session_id, person_display, rules, results, completed_at) -> AssessmentReportView`、`comparison_key(config, settings, environment) -> str`；报告只接受精确4项、零重复的冻结成员。

- [ ] **1. RED：** 每个公式手算0/中间/100；增加失败按完整分母、跨轮成功不拼接、敌我全不操作0、小地图只做一边0、ROUND_HALF_UP和四档边界。

```python
def test_one_half_channel_is_not_full_map_score():
    raw = sample_raw("pc-moba", "map-awareness", perfect=True)
    raw["main_hits"] = [True] * 15 + [False] * 15
    result = score_result(get_task("pc-moba", "map-awareness"),
                          validate_raw(get_task("pc-moba", "map-awareness"), raw),
                          QualityInput(duration_ms=60000, max_frame_gap_ms=16,
                                       long_frame_gap_total_ms=0,
                                       input_kind="mouse", hidden=False, interrupted=False))
    assert result.score == 71
```

- [ ] **2. 运行RED：** `"$PY" -m pytest apps/api/tests/unit/test_esports_scoring.py apps/api/tests/unit/test_esports_quality.py apps/api/tests/unit/test_esports_reporting.py -q`。
- [ ] **3. GREEN：** 使用精确Fraction保留计分中间比例，最后一次HALF_UP舍入；sqrt分数以精确半分界平方比较判定。此执行修订修复了Decimal中间除法将精确5.5误算到5的边界问题，指标展示仍可使用Decimal。全部公式对应规格表，跟踪覆盖最多60000ms。质量边界`>500`中断、累计`>3000`警告，等于边界不触发。正式报告不平均16项或4项，练习不调用build_report；有警告不列优势／短板。

```python
rounded = (2 * value.numerator + value.denominator) // (2 * value.denominator)
score = min(100, max(0, rounded))
grade = "low" if score < 40 else "medium" if score < 70 else "high" if score < 90 else "exceptional"
```

- [ ] **4. 验证：** 上述三文件全绿；非法duration/计数在schema层拒绝，不能被clamp悄悄修正。版本、输入／尺寸／设置任一不同时comparison_key不同；输出无联系方式、轨迹或“职业天赋”等断言。
- [ ] **5. 审查／提交：** 限本任务六文件，`feat: score esports assessment results and reports`。

### Task 3: 五表、可保留数据的迁移与生产打包

**Files:** Create `apps/api/app/esports_assessments/models.py`；Create 当前唯一head之后的`apps/api/migrations/versions/0060_esports_assessments.py`（仅当实施时仍是0059，否则使用实际下一编号并同步本文）；Modify `apps/api/migrations/env.py`、`apps/api/Dockerfile`、`scripts/db-schema-manifest.sh`；Create `apps/api/tests/db/test_esports_models.py`、`test_esports_migration.py`；Modify `apps/api/tests/db/test_migrations.py`、`apps/api/tests/cli/test_operations_docs.py`、`test_local_container_acceptance_launcher.py`。

**Interfaces:** SQLAlchemy模型`AssessmentSession`、`AssessmentAttempt`、`AssessmentResult`、`AssessmentReport`、`AssessmentPracticeRun`，字段／唯一性依规格§10。不修改旧talent表CHECK。migration+manifest是一个原子审查单元。

- [ ] **1. RED：** 新模型metadata恰有5表；同人同套进行中冲突、不同套可同时进行；同轮同项目第二结果冲突；account软删除不删结果；有业务数据降级明确拒绝。

```python
def test_new_tables_have_independent_names():
    assert AssessmentSession.__tablename__ == "esports_assessment_sessions"
    assert AssessmentResult.__tablename__ != "talent_test_results"
    assert AssessmentPracticeRun.__tablename__ == "esports_assessment_practice_runs"
```

- [ ] **2. 运行RED：** 独占DB后`"$PY" -m pytest apps/api/tests/db/test_esports_models.py apps/api/tests/db/test_esports_migration.py -q`；提前记录`alembic heads`，只在隔离库做升降级。
- [ ] **3. GREEN：** 使用UUID、JSONB、timezone-aware时间；session进行中部分唯一(person,suite)，result唯一(session,test)/attempt/creator+submission_key，report唯一session；外键不得级联清空历史。尝试running可过期，不能以一个永不回收的状态索引阻止90秒后释放。迁移降级在检测任意新业务行时抛明确错误。将5表加入REQUIRED_TABLES及ROW_COUNT_TABLES，API Dockerfile显式COPY新模块。

```python
Index("uq_ea_open_person_suite", "person_id", "suite_id", unique=True,
      postgresql_where=text("status = 'in_progress'"))
UniqueConstraint("session_id", "test_id", name="uq_ea_result_session_test")
```

- [ ] **4. 验证：** 新DB测试和`test_migrations.py`、两个manifest CLI测试通过；迁移测试finally回到新head。插入旧v1/v2样本后升级，新旧行数与内容不变。记录新增5表，不能沿用旧备份表数作新验证。
- [ ] **5. 审查／提交：** 只含本任务列明文件，`feat: persist independent esports assessment records`；中间提交保持自动部署暂停。

### Task 4: 默认权限、实时访问范围和私有查询

**Files:** Create `apps/api/app/esports_assessments/access.py`、`queries.py`、`errors.py`；Modify `apps/api/app/rbac/seed.py`、`service.py`；Create `apps/api/tests/api/test_esports_access.py`、`apps/api/tests/services/test_esports_queries.py`；Modify `apps/api/tests/db/test_rbac_seed.py`、`apps/api/tests/api/test_permissions.py`。

**Interfaces:** `AssessmentAccess(db, actor: AuthContext, now).require_take()`、`.visible_reports()`（SQL条件）、`.require_private_run(owner_person_id)`；`AssessmentQueries.history(actor, mode, cursor, limit)`、`.reports(actor, filters, cursor, limit)`、`.report(actor, report_id)`；抛`AssessmentForbidden/AssessmentNotFound/AssessmentConflict`，HTTP映射由Task 6完成。

- [ ] **1. RED：** 普通admin默认无新read/take，student+self、guardian+family、coach+assigned_class、superadmin+global按规格；新授权add/remove/preview均要求实际超管；拒绝优先，过期关系无权。

```python
def test_new_permissions_never_default_to_admin_global():
    assert "esports_assessments.take" in SELF_SCOPE_ONLY_PERMISSIONS
    assert "esports_assessments.read" in ADMIN_DEFAULT_EXCLUDED_PERMISSIONS
    grants = {permission for role, permission, scope in ROLE_PERMISSION_GRANTS if role == "admin"}
    assert "esports_assessments.read" not in grants
```

- [ ] **2. 运行RED：** `"$PY" -m pytest apps/api/tests/db/test_rbac_seed.py apps/api/tests/api/test_permissions.py apps/api/tests/api/test_esports_access.py apps/api/tests/services/test_esports_queries.py -q`，真实角色fixture要移除默认super_admin，不能仅更改显示名称。
- [ ] **3. GREEN：** 新UUID先查当前权限占用；take加入self-only，read加入admin-default-excluded，显式种子默认角色；现有`_require_talent_grant_manager`和相关锁路径识别两组独立权限而不泛化所有模块。访问层实时查询Person/Account及RBAC，不复用旧TalentAccess对象；SQL先授权、过滤completed，再分页计数；practice/未完成仅本人和有效global超管。

```python
restricted = permission_code.startswith(("talent_tests.", "esports_assessments."))
```

将该判断用在现有`_require_talent_grant_manager()`及其配套加锁／委派判断中，保留原参数、完整角色／权限校验和事务保护，不新增跳过锁的快捷入口。
- [ ] **4. 验证：** 权限撤销后读历史／重放均私有404，无姓名／数量泄漏；后台SQL分页稳定，默认20最大100，cursor包含排序时间+ID并严格校验。先过滤可比条件再取最近一条，不取最近不合格记录后放弃比较。
- [ ] **5. 审查／提交：** 权限公共文件由协调者合并后显式提交，`feat: enforce independent esports assessment access`。

### Task 5: 正式轮次与单项运行生命周期

**Files:** Create `apps/api/app/esports_assessments/locks.py`、`lifecycle.py`、`formal_service.py`；Create `apps/api/tests/services/test_esports_formal.py`、`apps/api/tests/db/test_esports_concurrency.py`、`apps/api/tests/esports_helpers.py`；Modify `apps/api/app/audit/service.py`。

**Interfaces:** `FormalAssessmentService(db).create(actor, request, now) -> SessionView`、`.start_attempt(actor, session_id, request, now) -> AttemptView`、`.submit(actor, attempt_id, ResultSubmit, now) -> ResultSaveView`、`.interrupt(actor, attempt_id, operation_key, now)`、`.abandon(actor, session_id, operation_key, now)`。`effective_run_state(state, run_until, submit_until, now) -> RunState`为纯函数；`lock_actor_and_occupancy(db, actor, now)`内部先按既有顺序锁person→account、重验权限，再获得新模块账号命名空间锁，随后正式session→attempt，避免与既有账户管理反向死锁。

- [ ] **1. RED：** 90秒占用／30分钟提交分离、幂等相同返回旧对象、异载荷409、同套并发创建唯一、4项乱序仅最后一次产生报告。

```python
def test_run_lease_and_submission_window_are_different():
    now = datetime(2026, 9, 21, tzinfo=UTC)
    assert effective_run_state("running", now + timedelta(seconds=90),
        now + timedelta(minutes=30), now + timedelta(seconds=90)) == "awaiting_submission"
    assert effective_run_state("running", now + timedelta(seconds=90),
        now + timedelta(minutes=30), now + timedelta(minutes=30)) == "expired"
```

- [ ] **2. 运行RED：** `"$PY" -m pytest apps/api/tests/services/test_esports_formal.py apps/api/tests/db/test_esports_concurrency.py -q`；并发测试每线程独立Session和已提交账号，使用Barrier/Event，不用savepoint假装独立事务。
- [ ] **3. GREEN：** server_now与两种截止时间一起返回；种子`secrets.randbits(32)`仅创建时生成。锁后refresh实体，鉴权→幂等检查→父轮次状态→validate_raw/validate_schedule（用已存配置和seed）→写结果／精确成员完成判断／报告／审计一个事务。放弃先胜则未提交尝试interrupted，迟到提交拒绝；提交先胜则原键仍可重放。已完成轮次不允许放弃。

```python
def effective_run_state(state, run_until, submit_until, now):
    if state in {"submitted", "interrupted", "expired"}:
        return state
    if now >= submit_until:
        return "expired"
    return "awaiting_submission" if now >= run_until else "running"
```

- [ ] **4. 验证：** 新用例包含放弃/提交两个胜出顺序、未提交同项不能新开、已到90秒其他项可开始但原项还能补交、30分钟边界、服务端失败回滚无半份报告。错误载荷不占结果操作键；审计元数据严格白名单不含raw。`esports_helpers.py`新建已提交actor及counts/cleanup，仅操作新表，不导入旧talent业务helper。
- [ ] **5. 审查／提交：** `feat: implement formal assessment lifecycle and idempotency`。

### Task 6: 独立练习、恢复入口、限流与完整HTTP接线

**Files:** Create `apps/api/app/esports_assessments/practice_service.py`、`rate_limit.py`、`router.py`、`dependencies.py`、`http_support.py`；Modify `apps/api/app/esports_assessments/errors.py`、`apps/api/app/api/router.py`、`apps/api/app/main.py`、`apps/api/tests/conftest.py`；Create `apps/api/tests/api/test_esports_routes.py`、`test_esports_privacy.py`、`apps/api/tests/services/test_esports_practice.py`、`apps/api/tests/db/test_esports_rate_limit.py`。

**Interfaces:** `PracticeAssessmentService(db).start/submit/interrupt`与正式使用相同actor/operation_key/now约定，start无session_id；`AssessmentQueries.recovery(actor,cursor,limit)`仅本人元数据；router实现规格§10.2全部路由。`AssessmentWriteLimiter.record(account_id, operation_kind, now)`为独立事务预算。errors.py补充`AssessmentExpired`、`AssessmentUnavailable`及`AssessmentRateLimited(retry_after: int)`，不复用旧模块错误代码。

新模块需可用套系的服务/API测试使用`esports_enabled` fixture显式注入；在tests/conftest.py定义但不设全局autouse，不改变既有测试环境。Task 5实现时可提前落此fixture，协调者单独负责该公共文件；模块内用`pytestmark = pytest.mark.usefixtures("esports_enabled")`或测试参数引用。生产开关保持关闭，禁用用例在自身内部再置空覆盖fixture。

```python
@pytest.fixture
def esports_enabled(monkeypatch):
    from app.esports_assessments import catalog
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES",
        frozenset({"pc-fps", "mobile-fps", "pc-moba", "mobile-moba"}))
```

- [ ] **1. RED：** API新建正式/练习、CSRF拒绝、旧权限不放行、未知owner私有404一致；context/recovery能发现本地缓存丢失的旧attempt，practice提交不产生正式result/report。

```python
def test_create_formal_suite(db_client, platform_users):
    identifier, password, _ = platform_users["credentials"]["self_student"]
    login = db_client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
    response = db_client.post("/api/v1/esports-assessments/sessions",
        json={"suite_id": "pc-fps", "operation_key": str(uuid4())},
        headers={"X-CSRF-Token": login.json()["csrf_token"]})
    assert response.status_code == 201
    assert len(response.json()["members"]) == 4
```

- [ ] **2. 运行RED：** `"$PY" -m pytest apps/api/tests/api/test_esports_routes.py apps/api/tests/api/test_esports_privacy.py apps/api/tests/services/test_esports_practice.py apps/api/tests/db/test_esports_rate_limit.py -q`。
- [ ] **3. GREEN：** practice使用独立表但占用检查同时查formal/practice，过期不依赖定时任务；同键按mode隔离，提交同样调用validate_raw/validate_schedule但只生成原始metrics和quality，不保存正式score/grade。GET仅计算有效状态，不因访问别人的ID触发清理写入。429含Retry-After；403未授权、404私有对象、409重复/冲突/中断、410未保存尝试过期、413超过64KiB、422契约错误均中文说明。私有no-store中间件覆盖本前缀，包括异常响应；体积限制按读取字节计数，不只信Content-Length。

```python
headers = {"Cache-Control": "no-store, private"}
if isinstance(error, AssessmentRateLimited):
    headers["Retry-After"] = str(error.retry_after)
```

- [ ] **4. 验证：** 独立事务并发第31次写429；6个正式新建/10分钟和12个练习/分钟边界，不能因业务回滚归还预算。覆盖先鉴权后重放、撤销权限时无结果和审计写入、角色read范围拒绝、恢复只本人、练习跨套载荷拒绝。新模块生产镜像import不缺文件。
- [ ] **5. 审查／提交：** `feat: expose assessment practice recovery and private APIs`。

### Task 7: 独立客户端、身份缓存与ERP菜单接线

**Files:** Create `apps/web/src/esportsAssessments/api.ts`、`keys.ts`、`pending.ts`、`availability.ts`、`AssessmentHome.tsx`、`AssessmentLayout.tsx`、`assessment.css`、`testSupport/renderAssessmentPage.tsx`及对应`api.test.ts`、`pending.test.ts`、`AssessmentHome.test.tsx`；Modify `apps/web/src/auth/AuthProvider.tsx`、`apps/web/src/layout/AppShell.tsx`、`apps/web/src/app/router.tsx`、`apps/web/src/settings/PermissionsPage.tsx`及各自已有测试。

**Interfaces:** `assessmentApi.context/suites/createSession/readSession/startAttempt/submitResult/interruptAttempt/abandonSession/startPractice/readPractice/submitPractice/interruptPractice/recovery/history/reports/report`逐一映射Task 6路径；消费Task 1 contracts.ts中声明的`PendingSubmission {account_id, mode, suite_id, session_id, test_id, attempt_id, operation_key, raw, quality, created_at}`；`savePending(value) -> {persisted:boolean}`、`loadPending(account_id) -> PendingSubmission[]`、`clearAssessmentPending(account_id?)`。`availability.ts`导出四套`SuiteId -> boolean`，对外发布前全false；前端可测条件必须是本地已实现开关与服务端context可用集的交集。Task 18隔离浏览器验收临时开启本地源码，Task 19才持久化通过验收的开放范围；不能仅前端隐藏。

`renderAssessmentPage(ui, {initialEntries, account})`在testSupport定义，包装独立QueryClient（retry:false）、MemoryRouter、真实AuthProvider；测试mock `/auth/me`与新API，传入的account限合成fixture。每测试清理QueryClient和storage，不导入既有router.test的私有工具。

- [ ] **1. RED：** 正式／练习／4套键不碰撞、旧缓存不被误删、退出清理新缓存、429重试键不变；导航只高亮新入口，普通admin无入口，有新take/read才显示正式入口。

```ts
it('storage blocked does not fake a saved result', () => {
  const value = fixturePending()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new DOMException('quota', 'QuotaExceededError')
  })
  const result = savePending(value)
  expect(result.persisted).toBe(false)
  expect(loadPending(value.account_id)).toEqual([value])
})
```

测试中的`value`使用Task 1完整fixturePending，不用强转空对象。另测同一API响应schema错误不得写入缓存；前端测试通过vi.mock可用映射与mock context显式控制开关，不改变生产默认值。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/api.test.ts src/esportsAssessments/pending.test.ts src/esportsAssessments/AssessmentHome.test.tsx src/auth/AuthProvider.test.tsx src/layout/AppShell.test.tsx src/app/router.test.tsx src/settings/PermissionsPage.test.tsx`。
- [ ] **3. GREEN：** 复用`apiFetch<T>`（它自动加/api/v1，传入`/esports-assessments/...`而非重复前缀）；query key含account+mode+suite+resource，所有write固定operation_key。缓存失败改为内存暂存并显示中文恢复限制，不能阻止在线提交；清理函数同时清掉内存。新路由用React.lazy/Suspense，不静态导入Three。权限管理增加中文说明与独立前缀规则，包含导航scope白名单。

```ts
export const assessmentKeys = {
  root: (account: string) => ['esports-assessments', account] as const,
  session: (account: string, suite: SuiteId, id: string) => ['esports-assessments', account, 'formal', suite, id] as const,
  practice: (account: string, suite: SuiteId, id: string) => ['esports-assessments', account, 'practice', suite, id] as const,
}
const AssessmentHome = lazy(() => import('../esportsAssessments/AssessmentHome'))
```

- [ ] **4. 验证：** 上述单测全绿，缓存过24小时、数据损坏、身份不符忽略并安全清理；满额／禁用时有明确提示，刷新前不谎称已持久化。首屏没有Three依赖，旧天赋菜单与旧页面测试不变；未完成场景显示开发中而非可测。
- [ ] **5. 审查／提交：** `feat: add independent assessment navigation and client state`，公共文件只由协调者提交。

### Task 8: 统一时钟、质量、设备检查与多指设置

**Files:** Create `apps/web/src/esportsAssessments/engineRuntime/types.ts`、`clock.ts`、`geometry.ts`、`quality.ts`、`capabilities.ts`；Create `input/desktop.ts`、`input/touch.ts`、`settings/layout.ts`、`settings/ControlSettingsPanel.tsx`、`settings/DeviceCheck.tsx`（均位于新模块），每个生产文件配同名test文件。

**Interfaces:** §1的GameEngine/InputAction/SceneSnapshot/GameFactory在engineRuntime/types.ts导出；`SimulationClock(startedAt).frame(now)`返回elapsed_ms/待执行tick时刻数组/interrupted/finished；`TouchInputRouter(layout, emit).down/move/up/cancel(event)`管理触点；`validateLayout(layout, bounds, safeArea) -> string[]`；`checkCapabilities(suite, observed) -> {allowed, reasons}`。look的dx/dy为适配器已应用灵敏度后的角度，move为[-1,1]方向向量，point/skill为逻辑坐标。skill取消须cancel=true且release=false。GameFactory第四参为冻结的实际场地Size；时钟输出n=1…7200的tick边界，最后advanceTo(60000)完成最后一段，输入仍只接受[0,60000)。

- [ ] **1. RED：** 30/60/120Hz均7200固定tick且覆盖60000ms；500ms允许、500.01ms中断；同时3触点互不抢占、非主指针不能丢弃；布局越界、重叠、控件遮地图拒绝。

```ts
it('keeps independent fingers and clears cancelled controls', () => {
  const emit = vi.fn()
  const router = new TouchInputRouter(layout, emit)
  router.down({pointerId:1, x:60, y:240, at_ms:0})
  router.down({pointerId:2, x:400, y:160, at_ms:0})
  router.down({pointerId:3, x:590, y:100, at_ms:0})
  expect(router.activeCount).toBe(3)
  router.cancel(10)
  expect(router.activeCount).toBe(0)
  expect(emit).toHaveBeenCalledWith(expect.objectContaining({kind:'fire', pressed:false}))
})
```

layout在该测试定义为640×360内互不重叠的左摇杆、右瞄准区与开火按钮，位置对应三个坐标；safeArea矩形单独覆盖左右翻转测试。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/engineRuntime src/esportsAssessments/input src/esportsAssessments/settings`。
- [ ] **3. GREEN：** 以tick整数推导时间避免累加漂移；大于500ms直接中断而不补算。pointer capture绑定职责、pointercancel/失焦统一松开。PC请求Pointer Lock需主动点击，拒绝时保持准备页；手机手动横屏可用，不依赖orientation.lock成功。场地几何变化才中断，滚动／只改变visualViewport但场地未变不误判。设置正规化与服务端一致。

```ts
const stepMs = 1000 / 120
const targetTick = Math.min(7200, Math.floor((elapsedMs + 1e-7) / stepMs))
const angularDx = isTouch ? deltaX / stageWidth * 180 * sensitivity : deltaX * 0.1 * sensitivity
```

- [ ] **4. 验证：** RF4尺寸/安全区/旋转覆盖；混合输入不能静默当手机；所有输入发生于正式开始前或60000ms后不计分；开始按钮手势不延续成攻击。未知环境有明确提示，无设备指纹采集；固定时钟与几何纯函数不依赖React渲染频率。
- [ ] **5. 审查／提交：** `feat: implement assessment input timing and device checks`。

### Task 9: FPS公共引擎与按需加载的Three场景

**Files:** Create `apps/web/src/esportsAssessments/fps/math.ts`、`schedule.ts`、`precision.ts`、`tracking.ts`、`switching.ts`、`renderer.ts`、`FpsStage.tsx`及各自test；Modify `apps/web/package.json`、`apps/web/package-lock.json`。

**Interfaces:** `createFpsEngine(config, seed, settings, viewport) -> GameEngine`支持precision/tracking/switching；`createFpsRenderer(canvas) -> {render(scene), resize(width,height,dpr), dispose()}`；`FpsStage`只消费SceneSnapshot与输入回调，不处理API或分数。pure引擎不import three。viewport使用冻结实际场地；生成时按设计参考视线限制可见性，不随玩家转头瞬移靶或重置镜头。

- [ ] **1. RED：** 30目标固定机会、40切换固定窗口、无输入跟踪不凭空全覆盖、5发/秒上限、不同帧率相同轨迹／统计，重复finish返回同一结果；帧中途命中只计一个目标。

```ts
it('counts all missed opportunities rather than zero denominators', () => {
  const config = fixtureConfig('pc-fps', 'precision')
  const settings = fixtureSettings('pc-fps')
  const game = createFpsEngine(config, 123, settings, {width:800,height:450})
  game.advanceTo(60000)
  const raw = game.finish()
  expect(raw.kind).toBe('aim')
  if (raw.kind !== 'aim') throw new Error('wrong raw kind')
  expect(raw.trials).toHaveLength(30)
  expect(raw.trials.every(t => !t.hit && t.latency_ms === 2000)).toBe(true)
})
```

config为Task 1服务端PC precision快照的完整测试镜像，快照契约由目录单测逐字段校验；不能改变它只为前端通过。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/fps`；渲染单测只mock资源生命周期，不能把jsdom通过称为WebGL实测。
- [ ] **3. GREEN：** 执行`npm --prefix apps/web install --save-exact three@0.186.0`和`npm --prefix apps/web install --save-dev --save-exact @types/three@0.186.0`，只改锁文件相关依赖；本轮计划阶段不执行。按固定角度调度，水平90°换算垂直FOV；每次卸载dispose geometry/material/renderer并停止RAF。

```ts
const verticalFov = 2 * Math.atan(Math.tan(Math.PI / 4) / aspect) * 180 / Math.PI
const camera = new PerspectiveCamera(verticalFov, aspect, 0.1, 1000)
```

资源加载使用取消代次标记：异步import返回后若组件已卸载，立即dispose或不创建场景，不重新接管鼠标。WebGL2缺失／上下文丢失分别提示不可开始／中断，无Canvas假射击降级混入同一成绩。
- [ ] **4. 验证：** 纯引擎、渲染生命周期单测通过；RF2离页后import完成不留RAF；`npm --prefix apps/web run build`检查独立Three chunk，无运行时CDN。正式tracking只测准星覆盖，不要求开火；靶移动边界反射可复现。
- [ ] **5. 审查／提交：** `feat: add deterministic FPS scenes and lazy renderer`。

### Task 10: PC FPS四项装配与敌我识别

**Files:** Create `apps/web/src/esportsAssessments/fps/recognition.ts`、`recognition.test.ts`、`suites/pcFps.ts`、`suites/pcFps.test.ts`。

**Interfaces:** `createRecognitionEngine(config, seed, settings, viewport) -> GameEngine`；`pcFpsFactory: GameFactory`只接受pc-fps四项，其余抛明确本地配置错误，不回退第一个游戏。

- [ ] **1. RED：** 15敌/15友、形状和文字提示齐全；每轮第一次真实射击决定结局；友方空枪也是误射；1200ms后800ms空白不接受射击。

```ts
it('no input yields fifteen misses and zero friendly shots', () => {
  const config = fixtureConfig('pc-fps', 'recognition')
  const settings = fixtureSettings('pc-fps')
  const game = createRecognitionEngine(config, 123, settings, {width:800,height:450})
  game.advanceTo(60000)
  const raw = game.finish()
  if (raw.kind !== 'recognition') throw new Error('wrong raw kind')
  expect(raw.trials.filter(t => t.target_kind === 'enemy')).toHaveLength(15)
  expect(raw.trials.every(t => !t.shot && !t.hit)).toBe(true)
})
```

- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/fps/recognition.test.ts src/esportsAssessments/suites/pcFps.test.ts`。
- [ ] **3. GREEN：** 15敌/友各固定集合由seed洗牌，不改变数量；四项绑定Task 9+recognition；场景说明写清鼠标、按键及ESC退出。敌我使用不同形状+文字，不单凭颜色判断。

```ts
export const pcFpsFactory: GameFactory = {
  create(config, seed, settings, viewport) {
    if (config.suite_id !== 'pc-fps') throw new Error('套系配置不匹配')
    return config.test_id === 'recognition'
      ? createRecognitionEngine(config, seed, settings, viewport)
      : createFpsEngine(config, seed, settings, viewport)
  },
}
```

- [ ] **4. 验证：** 控制设置锁定、输入丢锁中断覆盖，配置中不含中文打字或旧memory项目；四项原始统计通过Task 1对应服务端schema，不直接生成客户端权威分。
- [ ] **5. 审查／提交：** `feat: assemble PC FPS assessment suite`。

### Task 11: 手机FPS四项装配与移动射击协同

**Files:** Create `apps/web/src/esportsAssessments/fps/moveFire.ts`、`moveFire.test.ts`、`suites/mobileFps.ts`、`suites/mobileFps.test.ts`、`input/TouchFpsControls.tsx`、`TouchFpsControls.test.tsx`。

**Interfaces:** `createMoveFireEngine(config, seed, settings, viewport) -> GameEngine`；`mobileFpsFactory: GameFactory`支持precision/tracking/switching/move-fire；TouchFpsControls消费Task 8路由和冻结layout。

- [ ] **1. RED：** 20轮每轮3秒，命中和到位不同轮不能合成成功；触屏同时移动／瞄准／射击；抬一指不释放其他职责。

```ts
it('preserves joint outcomes by round', () => {
  const raw = {kind:'move_fire', rounds:Array.from({length:20}, (_, i) =>
    ({hit:i < 10, reached:i >= 10})), shots:10}
  expect(raw.rounds.filter(r => r.hit && r.reached)).toHaveLength(0)
})
```

另用引擎真实输入得到同形状raw再断言；上述测试不能独自证明引擎正确。引擎场景推进可从公开SceneSnapshot读取检查点坐标，发move/fire动作，不允许直接改raw或内部状态。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/fps/moveFire.test.ts src/esportsAssessments/suites/mobileFps.test.ts src/esportsAssessments/input/TouchFpsControls.test.tsx`。
- [ ] **3. GREEN：** 第n轮末同时确定命中与检查点到达；每轮目标一次得分，射击精度仍按实际发射。世界坐标检查点以当轮角色位置为起点，±1.2m，容差0.35m，移动2m/s；按钮布局影响触摸控制但不改变目标角尺寸。

```ts
const reached = distance(playerPosition, checkpoint) <= 0.35
rounds.push({hit: hitThisRound, reached})
```

`distance(a:{x:number,z:number},b:{x:number,z:number})`在本任务moveFire.ts定义为Math.hypot；共同完成由服务器按同索引hit/reached计算，不上传额外权威总分。
- [ ] **4. 验证：** 三指取消、按钮重排、反复开始清理输入、横屏非手机环境拒绝；mobile配置参数不得误用PC角径。纯引擎与指针测试通过后仍保持真机验收未完成状态。
- [ ] **5. 审查／提交：** `feat: assemble mobile FPS touch assessment suite`。

### Task 12: MOBA公共模拟、躲避、小地图与Canvas

**Files:** Create `apps/web/src/esportsAssessments/moba/world.ts`、`collision.ts`、`schedule.ts`、`dodge.ts`、`mapAwareness.ts`、`rhythm.ts`、`renderer.ts`、`MobaStage.tsx`及同名test。

**Interfaces:** `createDodgeEngine(config, seed, settings)`、`createMapEngine(config, seed, settings)`、`createRhythmEngine(config, seed, settings)`均返回GameEngine；`createMobaRenderer(canvas) -> {render(scene), resize(width,height,dpr), dispose()}`；`world.ts`导出moveToward(position,target,speed,dt)和攻击状态机，供Task 13/14使用。`AttackState {started_at_ms:number|null, cancelled:boolean, resolved:boolean}`；`advanceAttack(state, now_ms, windup_ms) -> {state:AttackState, landed:boolean}`，调用者必须保留返回state，resolved保证不能重复落地。rhythm共同引擎归本任务所有，后续两套装配无相互依赖。

- [ ] **1. RED：** 静止面对30次定向威胁全部受击而非默认满分；每条最多一次，最后一条在60秒前结算；小地图30+12事件完整，错后不能改对，无事件误报独立计数。

```ts
it('settles the final projectile before test end', () => {
  const config = fixtureConfig('pc-moba', 'dodge')
  const settings = fixtureSettings('pc-moba')
  const game = createDodgeEngine(config, 123, settings)
  game.advanceTo(60000)
  const raw = game.finish()
  if (raw.kind !== 'dodge') throw new Error('wrong raw kind')
  expect(raw.threats).toHaveLength(30)
  expect(raw.threats.every(t => t.hit)).toBe(true)
})
it('resolves an attack once at its windup boundary', () => {
  const first = advanceAttack({started_at_ms:0, cancelled:false, resolved:false}, 250, 250)
  expect(first.landed).toBe(true)
  expect(advanceAttack(first.state, 251, 250).landed).toBe(false)
})
```

- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/moba`。
- [ ] **3. GREEN：** 固定逻辑坐标映射与边界clamp，圆/线段连续碰撞避免高速穿过；弹道距离240、每轮2秒销毁，按规格预警/速度；练习各倍率仍验证有解且本轮结算。map从3000ms起每5000ms一事件，k=0…11，首选择消耗事件，main目标独立30个；Canvas只读SceneSnapshot。

```ts
const mapTimes = Array.from({length:12}, (_, k) => 3000 + 5000 * k)
const logicalX = (clientX - stageLeft) * 1000 / stageWidth
const logicalY = (clientY - stageTop) * 562.5 / stageHeight
```

rhythm每轮2秒，记录本轮攻击落地与结束时到位；取消后可重试但受1000ms攻击间隔约束。PC参数前摇250ms／距离100／容差25，手机300ms／90／30；每轮固定独立成功标记，不将不同轮攻击和到位拼接。
- [ ] **4. 验证：** seed重复结果一致、无解配置不能生成、边缘角色仍有合法躲避方向；触控UI地图点击不能误记为主任务或移动；帧率不同碰撞结果一致；DPR变化只影响渲染分辨率，不修改逻辑命中区。rhythm.test对两套参数、30轮计数、提前移动取消和重复落地分别断言，world.test覆盖取消/恰好边界/边界前/已结算四种状态。
- [ ] **5. 审查／提交：** `feat: add MOBA simulation and attention scenes`。

### Task 13: PC MOBA补刀与走A装配

**Files:** Create `apps/web/src/esportsAssessments/moba/lastHit.ts`、`lastHit.test.ts`；Create `suites/pcMoba.ts`、`suites/pcMoba.test.ts`（新模块内）。

**Interfaces:** 产出`createLastHitEngine(config,seed,settings) -> GameEngine`；消费Task 12的createRhythmEngine/createDodgeEngine/createMapEngine，`pcMobaFactory`只装配last-hit/orb-walk/dodge/map-awareness。

- [ ] **1. RED：** 前摇250ms、间隔1000ms；攻击到达而非按下时判断；提前移动取消；走A不同轮的攻击与到位不能拼接；补刀15波完结。

```ts
it('cancelled windup does not become an attack', () => {
  const next = advanceAttack({started_at_ms:0, cancelled:true, resolved:false}, 250, 250)
  expect(next.landed).toBe(false)
})
```

advanceAttack使用Task 12定义的签名；lastHit引擎保存返回state，另测连续advanceTo不得把一次攻击记为两次伤害。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/moba/lastHit.test.ts src/esportsAssessments/moba/rhythm.test.ts src/esportsAssessments/suites/pcMoba.test.ts`。
- [ ] **3. GREEN：** 补刀窗口起点2000/2250/2500/2750/3000各3波，经seed洗牌；条形血量在窗口前>10、窗口内1—10、窗口后0，过早攻击不改变该轨迹。走A每轮2秒，攻击完成与结束时到位各记录本轮；取消后可在该窗口重试，不能被多次pointer事件重复完成。

```ts
const success = arrivalMs >= windowStart && arrivalMs < windowStart + windowLength
const reached = Math.hypot(player.x - checkpoint.x, player.y - checkpoint.y) <= config.params.checkpoint_tolerance
```

- [ ] **4. 验证：** 四项配置与schema一致；鼠标右键场景内不弹菜单，场景外保留；连续按键、0输入和2秒/4秒边界均有结果，控件点击不发攻击。协调者合并对world.ts的必要共同修订，工作者不独自改公共接口。
- [ ] **5. 审查／提交：** `feat: assemble PC MOBA assessment suite`。

### Task 14: 手机MOBA技能方向与移动攻击装配

**Files:** Create `apps/web/src/esportsAssessments/moba/skillAim.ts`、`skillAim.test.ts`、`suites/mobileMoba.ts`、`suites/mobileMoba.test.ts`、`input/TouchMobaControls.tsx`、`TouchMobaControls.test.tsx`。

**Interfaces:** `createSkillAimEngine(config,seed,settings) -> GameEngine`；`mobileMobaFactory`装配dodge/skill-aim/move-attack/map-awareness；rhythm沿用Task 12共同引擎但读取手机前摇300ms、距离90、容差30，不改PC配置，不依赖Task 13文件。

- [ ] **1. RED：** 松手才发技能、pointercancel不发射、每轮首次释放后不重发；目标在移动时按真正弹道相交判定，不能按释放时是否指向目标给分。

```ts
it('cancel is not a skill release', () => {
  const emit = vi.fn()
  const router = new TouchInputRouter(layout, emit)
  router.down({pointerId:9, x:skillCenter.x, y:skillCenter.y, at_ms:0})
  router.cancel(10)
  expect(emit.mock.calls.flat().some(a => a.kind === 'skill' && a.release)).toBe(false)
})
```

此测试layout包含右侧技能按钮和左摇杆，skillCenter取按钮中心；对正常pointerup另测恰好一次release。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/moba/skillAim.test.ts src/esportsAssessments/suites/mobileMoba.test.ts src/esportsAssessments/input/TouchMobaControls.test.tsx`。
- [ ] **3. GREEN：** 技能目标速度80、半径22，弹道速度600半径10最大500；首释放后持续模拟碰撞到本轮末，过晚未命中计失败。攻击按钮只选唯一可攻击目标，不把自动移动当作完成检查点；地图点击触点与摇杆职责分离。

```ts
if (action.kind === 'skill' && action.release && !trial.released) {
  trial.released = true
  projectile = {origin: player, direction: normalizedDirection, travelled: 0}
}
```

normalizedDirection在skillAim.ts以当前玩家到拖动目标的向量归一化，零向量不产生有效释放；不接受NaN方向。
- [ ] **4. 验证：** 30轮各一释放、四项配置冻结、控件布局不遮小地图；多指和中断取消不误发；不同手机画布尺寸不改变逻辑速度。手机专用测试仍需Task 18真实设备证据。
- [ ] **5. 审查／提交：** `feat: assemble mobile MOBA touch assessment suite`。

### Task 15: 正式／练习宿主、恢复与保存后导航

**Files:** Create `apps/web/src/esportsAssessments/useAssessmentSession.ts`、`hostState.ts`、`AssessmentHost.tsx`、`AssessmentSessionPage.tsx`、`PracticePage.tsx`和各自test；Modify `AssessmentHome.tsx`、`AssessmentLayout.tsx`、`assessment.css`、`apps/web/src/app/router.tsx`及接线测试；窄扩展`pending.ts`的全身份／原操作键匹配删除和`contracts.ts`的登记参数预览生成器及其测试。Task15独占router并接入Task16页面导出。

**Interfaces:** `useAssessmentSession({mode,suite_id,session_id?,practice_id?})`返回serverView、start、submit、interrupt、reconcile及错误状态；`AssessmentHost`消费AttemptView和GameFactory，结果只经该hook保存。host状态为intro/preflight/trial/starting/countdown/running/pending/saved/interrupted；`acceptResponse(requestGeneration,currentGeneration,requestAccount,currentAccount)`必须全部相等才接受晚响应。

- [ ] **1. RED：** 从任意项目开始、4项乱序、服务端保存成功前不增加进度、下一项按钮、最后一项跳报告、同键重试、原页触摸不能误点新按钮；刷新无缓存仍能恢复占用。

```ts
it('ignores an old account response', () => {
  expect(acceptResponse(1, 2, 'account-a', 'account-b')).toBe(false)
  expect(acceptResponse(2, 2, 'account-b', 'account-b')).toBe(true)
})
it('does not start with an insufficient server lease', () => {
  const view = fixtureAttempt()
  expect(remainingLeaseMs(view, 28000)).toBe(62000)
})
```

view由fixture构造server_now到run_until恰好90000ms；`remainingLeaseMs(view,elapsedSinceRequest)`返回`max(0,parse(run_until)-parse(server_now)-elapsedSinceRequest)`，elapsed来自performance.now而非用户日期。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/hostState.test.ts src/esportsAssessments/AssessmentHost.test.tsx src/esportsAssessments/AssessmentSessionPage.test.tsx src/esportsAssessments/PracticePage.test.tsx`。
- [ ] **3. GREEN：** 完成能力／布局／20秒试练后才申请attempt；当剩余<63000ms不进入倒计时。确认设置后start，计时中禁止控件编辑；页面隐藏/旋转/取消中断本项，保留其他已存结果。pending先保存白名单再POST，UI明确缓存是否可靠；对账GET先判已提交，再考虑interrupt，过期cache不换attempt ID。429显示秒数与手动重试；409展示已有状态；401/403清理当前身份界面。

```ts
const generation = identityGeneration.current
const saved = await assessmentApi.submitResult(attempt.id, submission)
if (!acceptResponse(generation, identityGeneration.current, owner, currentAccount.current)) return
await queryClient.invalidateQueries({queryKey: assessmentKeys.root(owner)})
if (!acceptResponse(generation, identityGeneration.current, owner, currentAccount.current)) return
if (saved.session?.report_id) navigate(`/esports-assessments/reports/${saved.session.report_id}`)
```

PC FPS预检按钮命名「检查设备并进入试练」，在点击处理器中同步请求Pointer Lock，不把请求放到网络await之后。试练可用「结束试练」提前结束，之后显示「开始正式测试」；同一个canvas节点贯穿试练／starting／倒计时／正式，避免换节点丢失锁。若说明页阶段已主动释放锁，开始按钮必须在手势内重新请求并等待确认，能力拒绝仍留在准备页、不得启动计时。创建响应回来后再次确认锁与身份有效，失败则对账／中断释放占用。增加网络延迟与canvas重挂载的浏览器回归。

按钮导航另验证press目标与click目标一致，detail=0键盘激活允许；游戏手势触发保存时取消指针控制再显示结果。手机沉浸布局不覆盖全站快捷导航，仅当前正式场景隐藏外壳并保留退出。
- [ ] **4. 验证：** RF2加载晚到、RF3设备时钟±10分钟/响应28秒、RF5权限撤销；pending再开标签页只能恢复不能同项重开；正式与practice互换URL不错误提交；后端500后仍保留原键，记录按钮支持读屏标签和键盘操作。
- [ ] **5. 审查／提交：** `feat: connect assessment hosts and resilient result saving`。

### Task 16: 记录、报告、历史比较与人员档案入口

**Files:** Create `apps/web/src/esportsAssessments/AssessmentHistoryPage.tsx`、`AssessmentReportPage.tsx`、`AssessmentReportsPanel.tsx`及同名test、独立`reports.css`及必要报告展示／练习比较helper与测试；Modify `apps/web/src/people/PersonDetailPage.tsx`及测试；补充`apps/api/app/esports_assessments/queries.py`、`formal_service.py`和相应`test_esports_queries.py`、`test_esports_formal.py`。比较组装修改必须在Task5/6审查完成后执行，不能竞争公共文件。前端可依冻结API与Task15并行；不修改router、assessment.css或Layout，路由由Task15接入。

**Interfaces:** 页面只消费Task 6 API；`AssessmentReportsPanel({personId})`先查询本模块可见报告，不凭people.read推导权限。服务端列表响应`{items, next_cursor, visible_total}`，total必须按授权范围计算；正式和practice标签明确分离。

- [ ] **1. RED：** 练习无正式分／四档、4项报告无总天赋分、同配置取最近可比而非最高分，陌生对象404不暴露姓名；没有新read权限不加载人员档案报告面板。

```ts
it('practice never shows a formal grade', async () => {
  const user = userEvent.setup()
  renderAssessmentPage(<AssessmentHistoryPage />, {
    initialEntries:['/esports-assessments/history'], account:studentFixture,
  })
  await user.click(screen.getByRole('tab', {name:'自由练习'}))
  expect(await screen.findByText('原始指标')).toBeVisible()
  expect(screen.queryByText('超高表现')).not.toBeInTheDocument()
})
```

本测试以独立QueryClient、MemoryRouter和mock API提供练习score=null/grade=null数据，不能复用旧路由测试里的私有helper；Task 7 testSupport提供新renderAssessmentPage包装器，studentFixture为仅有本模块self take/read的合成AuthUser，使用已有AuthUser类型构造所有必需字段。
- [ ] **2. 运行RED：** `npm --prefix apps/web test -- src/esportsAssessments/AssessmentHistoryPage.test.tsx src/esportsAssessments/AssessmentReportPage.test.tsx src/esportsAssessments/AssessmentReportsPanel.test.tsx src/people/PersonDetailPage.test.tsx`；查询改动另跑`"$PY" -m pytest apps/api/tests/services/test_esports_queries.py -q`（独占DB）。
- [ ] **3. GREEN：** 日期中国时区YYYY-MM-DD；4项raw指标有单位，score质量警告并列；超高等依据服务端grade，前端不二次计算。report snapshot不可变，权限每次重查；配置/设备不同显示原因不计算进步百分比。筛选suite/person/date严格校验，不加载全库到浏览器排序。

```ts
const scoreLabel = saved.mode === 'practice' ? '原始指标'
  : saved.scored.quality_flags.length > 0 ? '试算分／需复测' : gradeLabels[saved.scored.grade]
```

`gradeLabels`定义在contracts.ts的展示映射为low较低表现/medium中等表现/high较高表现/exceptional超高表现，仅formal非null结果可访问。

正式比较优先使用Task4最近的干净可比结果；找不到时查询最近既往同人／同套／同项已完成结果，冻结`previous_report_id`与具体`config_changed`、`settings_changed`、`environment_changed`、`previous_quality_warning`等差异原因，`score_delta=null`。没有任何既往结果才使用`no_comparable_history`；`current_quality_warning`始终阻止差值。页面按当前权限读取既往不可变报告并列展示，不能在读取时重写本报告或计算新的正式差值。

练习比较由用户从当前分页的本人练习记录中显式选两条；同套／同项／同难度、完整配置／设置／环境相同且双方无质量警告时，展示数值型原始指标差值与单位，null不做差。不生成正式分、档位、进步百分比或优劣结论；条件不符只并列并解释差异，不为比较抓取全历史或新增API。
- [ ] **4. 验证：** readonly guardian/coach仅见授权完成报告，不能打开practice、进行中或开始按钮；分页切筛选重置cursor，重复ID稳定排序；人员中心其他面板不闪烁、不被本模块失败阻断。
- [ ] **5. 审查／提交：** `feat: present independent assessment records and reports`。

### Task 17: 后端全链路、并发与旧模块回归

**Files:** Create `apps/api/tests/api/test_esports_integration.py`；Extend `test_esports_concurrency.py`、`test_esports_privacy.py`、`test_esports_migration.py`；最小修正`tests/cli/test_e2e_launchers.py`默认兄弟评分工作树测试的临时fixture，使其不依赖本机目录布局，不改生产launcher发现规则；Create `docs/verification/2026-09-21-talent-assessment-2.md`（验收记录，未执行项明确标未验证）。

**Interfaces:** 只使用已实现API、独立测试库与已有fixtures。并发测试helper`committed_assessment_actor(engine)`、`assessment_counts(db, person_id)`、`cleanup_assessment_actor(engine, person_id)`在tests/esports_helpers.py定义，账户fixture可参考API的IdentityService/AccountService但不能import conftest私有函数或旧talent清理函数。

- [ ] **1. RED：** 4套完整正式流程→16结果4报告；每套一个练习→4练习且正式数不变；双连接最后结果／放弃竞争、修改权限与提交竞争；未提交过期和已提交幂等重放不同。

```python
def test_formal_and_practice_counts_stay_separate(db, completed_four_suites):
    counts = assessment_counts(db, completed_four_suites.person_id)
    assert counts == {"formal_results":16, "formal_reports":4, "practice_results":4}
```

`completed_four_suites`是本文件fixture，通过create/start/submit逐项目调用新服务产生上述记录，固定now并在上次提交后才开下一运行；`assessment_counts`只数submitted练习和正式新表，报告按归属person JOIN。
- [ ] **2. 运行RED：** 独占DB后执行`"$PY" -m pytest apps/api/tests/api/test_esports_integration.py apps/api/tests/db/test_esports_concurrency.py apps/api/tests/api/test_esports_privacy.py apps/api/tests/db/test_esports_migration.py -q`。若全绿，确认新测试确实命中真实实现分支，不伪造RED。
- [ ] **3. 修复集成差异：** 只修正契约接缝和既有规格不符合，不放宽权限/expiry/计分断言。给并发Barrier添加超时和finally清理，所有线程独立Session；失败时仅汇报记录数和状态，不打印成绩载荷或凭据。审计只允许中文事件定义和安全元数据。

```python
with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [pool.submit(submit_with_independent_session, operation) for operation in operations]
    outcomes = [future.result(timeout=20) for future in futures]
assert len({outcome.session.report_id for outcome in outcomes}) == 1
with Session(postgres_engine) as verification_db:
    assert assessment_counts(verification_db, person_id)["formal_reports"] == 1
```

`submit_with_independent_session(operation)`为该并发测试文件中的函数：在`Session(postgres_engine)`创建服务，Barrier后提交operation，返回服务的正式ResultSaveView；此用例是同键提交最后一项，两个响应指向同一报告，独立新Session断言数据库实际仅一行。operation包含actor ID、attempt ID和固定payload，person_id来自已提交fixture，线程内重新读取actor而非共享ORM对象。放弃竞争用例单独接收预期409，不能吞掉意外异常。
- [ ] **4. GREEN与全量：** `"$PY" -m pytest apps/api/tests -q`只运行一个进程；随后`"$RUFF" check apps/api/app apps/api/tests`及`"$MYPY" apps/api/app`。完整记录passed/failed/skipped及明确原因，不借用旧4096/4370等历史数字；旧talent七／八项、登录、权限、备份manifest不回归。
- [ ] **5. 审查／提交：** `test: verify assessment security concurrency and compatibility`。若发现非本功能既有失败，定位证据并报告，不掩盖或随意修改旧需求；发布前必须清楚区分已修和未验证。

### Task 18: 独立浏览器入口、真实触屏及手机验收

**Files:** Create `apps/api/app/cli/seed_esports_assessments_e2e.py`、`summarize_esports_assessments_e2e.py`；Create `apps/api/tests/cli/test_seed_esports_assessments_e2e.py`、`test_summarize_esports_assessments_e2e.py`、`test_esports_e2e_launcher.py`；Create `scripts/run-esports-assessments-e2e.sh`；Modify `scripts/run-core-e2e.sh`、`scripts/check.sh`、`apps/web/playwright.config.ts`；Create `apps/web/tests/esports-pc.spec.ts`、`esports-touch.spec.ts`、`esports-recovery.spec.ts`、`esports-capabilities.spec.ts`；Extend验收记录。

**Interfaces:** `seed_esports_assessments_e2e(db, *, settings,cipher,lookup,passwords)`使用测试专用账号，不继承生产资料；调用通用seed_e2e前必须通过APP_ENV=test和e2e_database_safety，已知seed会TRUNCATE，不允许在NAS运行。runner默认8014/5188，显式指定origin且校验；新E2E_CORE_MODE为esports，all加入本模块，旧core/talent行为不变。

- [ ] **1. RED：** launcher拒绝生产URL／已占用端口、缺环境变量时不启动；摘要拒绝缺case、skip、重试后“通过”冒充首轮绿。浏览器点击真实菜单／按钮，画布只输入实际指针动作，不用API直接塞成绩。

```ts
test('student reaches a saved result and can continue', async ({page}) => {
  await loginAsStudent(page)
  await page.getByRole('link', {name:'天赋评测 2', exact:true}).click()
  await page.getByRole('button', {name:'端游 FPS 正式测试'}).click()
  await page.getByRole('button', {name:'精准定位与微调'}).click()
  await page.getByRole('button', {name:'检查设备并进入试练'}).click()
  await expect(page.getByRole('button', {name:'结束试练'})).toBeVisible()
  await page.getByRole('button', {name:'结束试练'}).click()
  await page.getByRole('button', {name:'开始正式测试'}).click()
  await expect(page.getByText('结果已保存', {exact:true})).toBeVisible({timeout:90000})
  await expect(page.getByRole('link', {name:/继续下一项/})).toBeVisible()
})
```

此流程按钮名称与Task 15保持一致，配置使用合法默认值；完整spec在正式倒计时结束后对至少若干目标发真实输入并核对raw有所响应，不能仅靠全程0分证明输入工作。
- [ ] **2. 运行RED：** 新launcher CLI单测先在本地执行；之后独占DB，运行`bash scripts/run-esports-assessments-e2e.sh`。不存在的新脚本或能力错误不能当作测试逻辑有效RED，需确认fixture、服务器和浏览器已正确连接。

浏览器验收前，协调者先保存catalog.py与availability.ts两个开关区域的精确差异，仅在本地未发布源码用apply_patch临时开启已实现套系；整个验收期间禁止部署，中间提交保持`ERP_NAS_AUTO_DEPLOY=0`。安全runner仍强制隔离测试库／测试身份，不引入生产可调用的开关API或跳过权限的测试后门。验收失败、结束或需要暂停真机检查时，用反向精确补丁恢复原开关区域，不能checkout覆盖文件其他修改；Task 19只重新持久化有证据通过的套系。验收记录必须列出临时启用集合及恢复确认，真实手机只连同一隔离测试实例。
- [ ] **3. GREEN：** wrapper设置`E2E_CORE_MODE=esports`后exec既有安全runner，复用端口检查、隔离DB和测试环境剥离机制。新增desktop-1440、横屏touch-844×390、能力检查WebKit等专用项目，旧项目排除新spec，不能把竖屏/仅viewport变化当多指。所有新spec关闭trace/video/自动失败截图，独立输出脱敏最终页和summary。CLI仅测试构建可用，生产Docker不复制seed工具。

```sh
#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
export E2E_CORE_MODE=esports
exec bash "$root/scripts/run-core-e2e.sh"
```

浏览器测试只从测试环境凭据helper登录；清除浏览器进程中的DB/加密环境；建立每spec/project种子和用例数预期。主PC场景完成2套→8正式结果/2报告/2练习，主触屏完成2套同样8/2/2，recovery额外记录独立fixture计数。真实60秒计时至少每套一项，不用改生产常量加速；纯模拟时钟用例另列，不能替代实时时长。
- [ ] **4. 验证矩阵：**

| 环境 | 必须有的证据 |
| --- | --- |
| 本地Chromium桌面 | 两套8项、Pointer Lock进入/丢失、键鼠有效、独立记录、保存/继续/报告 |
| 本地真触屏事件模拟 | 两套8项、多pointerId、手势不穿透、横屏/旋转/安全区；标注模拟 |
| 响应式页面与结果区 | 360/390竖屏入口、1024/1440电脑页、手机横屏场地；保存前后结果区不跳动，菜单可达 |
| Windows Chrome/Edge、macOS Chrome/Safari | 能力检查、可用时启动与退出，不支持时中文原因，不静默降级 |
| 真实iPhone Safari | 手机FPS/MOBA横屏多指完整流程、后台/来电/返回、按钮设置、成绩保存 |
| 真实Android Chrome | 手机FPS/MOBA相同流程与浏览器栏/手势取消 |
| 原ERP | 旧天赋七／八项、登录及人员权限导航核心回归 |

真机未接入时向用户请求在本地隔离测试实例完成验证，使用只含测试账号的HTTPS实例；不要让用户在生产创建模拟数据。未获取证据则手机套系保持不开放，明确报告“尚未真机验收”，不能用模拟项目数量抵扣。旧核心可用`E2E_CORE_MODE=talent bash scripts/run-core-e2e.sh`回归；不是不存在的run-talent-tests-e2e.sh。
- [ ] **5. 审查／提交：** `test: validate assessment browsers touch and recovery`。验收记录需引用命令、退出码、唯一用例数、设备／浏览器版本、统计行数和安全截图，禁止贴原始请求/密码/个人信息。

### Task 19: 开放开关、最终审查、NAS部署与验收交付

**Files:** Modify `apps/web/src/esportsAssessments/availability.ts`、`apps/api/app/esports_assessments/catalog.py`；Create `docs/features/esports-assessments.md`；Update `docs/verification/2026-09-21-talent-assessment-2.md`和README中的用户入口；不编辑NAS秘密或直接复制产物。

**Interfaces:** 四套availability只有在各套接口、输入、报告和规定设备验收通过后才置true；后端创建必须检查相同可用集。若部分套系尚未验收，禁止报告全部完成；最终发布范围向用户清楚说明，未开放项保持“尚未开放”。

- [ ] **1. RED／发布防护：** 覆盖“前端开后端关”不可创建、“后端开前端关”不展示可测、“未开放套系直接URL访问”返回明确状态。文案不可误称正式职业评级。

```python
def test_unavailable_suite_cannot_create_session(db_client, login_as_admin, monkeypatch):
    monkeypatch.setattr(catalog, "AVAILABLE_SUITES", frozenset())
    response = db_client.post("/api/v1/esports-assessments/sessions",
        json={"suite_id":"mobile-fps", "operation_key":str(uuid4())},
        headers={"X-CSRF-Token":login_as_admin.csrf_token})
    assert response.status_code == 409
    assert response.json()["code"] == "esports_suite_unavailable"
```

- [ ] **2. 最终本地验证：** 独占DB后按顺序运行Task 17完整后端→全前端`npm --prefix apps/web test -- --maxWorkers=4`→`npm --prefix apps/web run lint`→`npm --prefix apps/web run build`→Task 18矩阵；根据实际环境执行`scripts/check.sh`全容器/备份链并记录opt-in跳过原因。任何新增失败必须定位，不能沿用上次绿色日志。root再做全分支规格／质量审查，检查只有本需求与必要接线被改变。
- [ ] **3. 提交与发布：** 文档说明入口、玩法、设备能力限制、内部试行规则和手机验收状态；只提交任务实际修改文件，保留用户其他dirty文件。最终执行：

```sh
cd '/Users/skymoonzyj/Documents/new project3'
scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
```

脚本预检、备份、迁移或构建失败即发布阻塞，不手工Docker/copy绕过，不降级清空新表。若已经迁移而镜像失败，用保留数据的前向修复／脚本支持回滚方式处理；不擅自还原全库覆盖其他业务新数据。
- [ ] **4. 线上只读验收：** 验证active-release、发布源码/资源hash、实际Alembic唯一新head、API/Web/代理健康、scheduler和operations心跳、加密备份及隔离恢复回执；检查登录和新菜单资源、权限错误，不能创建测试成绩或改真实账户。线上版本与本地计划/验收证据一致，失败项单列。
- [ ] **5. 交付：** 给出实际开放套系、验证结果、NAS发布版本和仍存在的设备限制。若任一必需项未完成，说明已完成部分和具体阻塞，不宣称全部交付；保留下一步为真实待办而非再跑无意义命令。

## 2. 计划自检与规格覆盖表

以下由主代理在保存计划时逐条检查，不委派给子代理替代自检；实施时继续以实际测试回填任务复选框。

| 规格章节 | 落实任务 |
| --- | --- |
| §1—2 独立模块、账号、旧数据隔离 | 1、3、4、7、17 |
| §3 正式/练习/进度/结果导航 | 5、6、7、15、16 |
| §4 场景、输入、时钟与设置 | 1、8、9、12 |
| §5.1 PC FPS四项 | 9、10、18 |
| §5.2 手机FPS四项 | 9、11、18 |
| §5.3 PC MOBA四项 | 12、13、18 |
| §5.4 手机MOBA四项 | 12、14、18 |
| §6 评分/四档/五档练习 | 1、2、6、16 |
| §7 浏览器/真实手机/中断/质量 | 8、9、15、18 |
| §8 报告/建议/可比历史 | 2、4、16 |
| §9 权限/隐私/限流 | 4、5、6、7、17 |
| §10 表/事务/API/缓存 | 1、3、5、6、7、15 |
| §11 验收/发布/回滚 | 17、18、19 |
| §12 排除项与旧动态视力待办 | 全局约束、19交付边界 |

复核要点：4套合计16项全部映射；三个FPS公共玩法仅共享引擎不共享成绩；MOBA地图保留30+12通道；两种模式有独立载荷／幂等键；90秒占用不等于30分钟补交；不把全局权限种子自动授予普通admin；后台与前端可用开关同时验证。

主代理已于2026-09-22完成计划自检：修正正式／练习返回类型、共享rhythm所有权、攻击单次结算、测试fixture、seed调度校验和本地验收开关流程。文档检查确认19项连续编号、每项五个步骤、文件／接口说明齐全、代码围栏成对、无待填占位或行尾空白；这只是计划检查，不代表业务测试、真机验收或NAS部署已通过。

技术选型已只读核实：npm registry在计划编写时返回three及@types/three均0.186.0；[Three安装说明](https://threejs.org/manual/pages/installation.html)支持npm+Vite本地构建，[WebGLRenderer说明](https://threejs.org/docs/pages/WebGLRenderer.html)明确使用WebGL2。因此能力检查要覆盖WebGL2，不依赖WebGL1回退；此时尚未安装依赖。

## 3. 执行方式与审核停点

延续用户此前要求的子任务协作，建议按上述依赖图采用 **subagent-driven-development**：公共契约/锁/菜单由协调者控制，四套场景具备依赖后并行；每项独立审查，末尾整体审查。可使用最多8个子任务，但实际只开有独立职责的工作流，DB验证始终单路。

用户已于2026-09-22确认开发，当前按subagent-driven-development在独立工作树执行；实际任务、验证与发布状态记录在本计划的SDD进度文件，不能将计划复选框或示例命令当作已通过证据。若用户改选单代理原生执行，则改用executing-plans，任务、验证和发布要求不变。
