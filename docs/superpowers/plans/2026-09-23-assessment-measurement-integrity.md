# 天赋评测 2 测量修正与提交完整性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复双端切换计时、手游移动射击、双端躲避测量及提前提交问题，兼容旧成绩并经验证发布到NAS。

**Architecture:** 用显式版本映射注册四套v3目录，新玩法使用独立引擎与必要的新raw判别类型；v1/v2配置、序列化、评分和历史报告冻结。正式和练习共享63秒首次提交下限，成功幂等重放优先。公共契约先完成，独立引擎、提交守卫和报告按依赖并行，最后统一启用默认v3及发布。

**Tech Stack:** React、TypeScript、Zod、Three.js／Canvas、Vitest、Playwright；FastAPI、Pydantic、SQLAlchemy、PostgreSQL、pytest；现有NAS Docker发布脚本。不增加产品依赖。

**Spec:** `/Users/skymoonzyj/.codex/worktrees/esports-assessment-two/new project3/docs/superpowers/specs/2026-09-22-assessment-measurement-integrity-design.md`（2026-09-23用户批准）。执行者必须先完整阅读规格及本计划。

## Global Constraints

- 每项60秒；switching为20组×3000ms；move-fire为20轮×3000ms；dodge为30轮×2000ms。
- 有效输入区间为`[0,60000ms)`，允许最后结算时间恰为60000ms，不接受该时刻的输入回填。
- FPS新目标完整在刷新时当前视野内，生成后固定世界坐标；俯仰±70°、水平FOV90°、射击间隔200ms不变。
- 有效移动命中：连续实际水平速度≥1m/s达到100ms，满速2m/s；每轮重新累计，中途低速或停止重置。
- MOBA世界1000×562.5，玩家半径12／速度180，安全圆半径32／有效中心半径20；正式预警PC1000ms、手机1100ms，爆发150ms。
- 首次提交区间`[created_at+63s,submit_until)`；90秒运行租约、30分钟提交窗口与成功幂等重放保持区分。
- v1/v2规则、DTO序列化及历史报告冻结；新旧不直接比较；旧pending不能丢失；没有数据库表结构迁移。
- 不开放普通账号，不改原天赋测试模块，不新增测试项目，不将分档宣传为人群常模或反作弊认证。
- 只用隔离本地数据库；任何时刻仅一个DB pytest／E2E进程。各路可并行跑纯单元测试，但必须向协调方领取DB执行时段。
- 所有本地文件编辑使用apply_patch。保留已有dirty改动；不使用reset／checkout、整目录覆盖、全量git add或批量提交。
- 本轮执行前读取systematic-debugging、test-driven-development及其必读参考；已有复现证据可复用，但新增回归必须先见RED再见GREEN。

## Review Focus

1. 完成一半的旧场次／旧pending在升级后仍可提交，旧请求摘要不能因默认字段变化而冲突：任务1、2、9。
2. 同一时刻发生移动停止、射击冷却、轮结束或最后爆发结算时，不错误归属或重复命中：任务4、5、6。
3. 浏览器刷新升级、撤权及迟到保存响应不能丢pending或跨账号展示结果：任务8、9。
4. 狭窄横屏、极限俯仰及边角位置仍有可见可达目标，控件／文字不能挡住新反馈：任务2、4、5、6、8。
5. 浏览器虚拟快进不能替代服务器63秒；真实时钟与模拟结果证据必须分开：任务3、9、10。

## 工作区、检查命令与检查点

实现工作树：`/Users/skymoonzyj/.codex/worktrees/esports-assessment-two/new project3`，已有分支`codex/esports-assessment-two`。先按using-git-worktrees核对它是现有linked worktree，不再创建同用途工作树。

主仓库：`/Users/skymoonzyj/Documents/new project3`。唯一发布源：主仓库的`.nas-release-sync`。实施前分别采集工作树／发布源的tracked+untracked可见文件哈希和权限清单，记录既有差异；已有dirty内容不是本轮新增修改。

下列shell变量仅用于命令示例，在每个新终端设置；不得误指向生产库：

```sh
WT='/Users/skymoonzyj/.codex/worktrees/esports-assessment-two/new project3'
ROOT='/Users/skymoonzyj/Documents/new project3'
PY="$ROOT/.venv/bin/python"
RUFF="$ROOT/.venv/bin/ruff"
MYPY="$ROOT/.venv/bin/mypy"
export TEST_DATABASE_URL='postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e'
```

每个任务最后执行其测试命令和`git diff --check`，提交变更路径清单、RED／GREEN摘要及未覆盖项给协调方，由独立审查者审查增量。既有dirty文件不能整文件提交夹带；先形成可审查检查点，协调方确定安全的精确提交范围。中间检查点不触发NAS发布；文档提交或确需提交中间代码时，仅对该次commit显式设置`ERP_NAS_AUTO_DEPLOY=0`，最终必须执行正式发布脚本。

## 分工与依赖

| 任务 | 负责人边界 | 前置 |
| --- | --- | --- |
| 1 后端版本契约 | 注册、raw、seed计划、公共后端fixtures | 无 |
| 2 前端版本契约 | contracts、scene类型、前端fixtures、FPS视口预检 | 1 |
| 3 最早提交校验 | lifecycle与两类服务、专用边界测试 | 无；使用未改玩法precision |
| 4 切换引擎 | switchingV3、FPS公共factory | 2 |
| 5 移动射击引擎 | moveFireV3、mobileFps分派 | 2 |
| 6 躲避引擎 | DodgeChoice、MOBA世界运动边界及两套factory | 2 |
| 7 后端评分报告 | scoring、reporting及其测试 | 1 |
| 8 指引与呈现 | renderer、Host、反馈、结果和报告页面 | 2；最终验证依赖4—7 |
| 9 默认启用与兼容集成 | 统一目录切换、服务回归、E2E和时间轴 | 1—8 |
| 10 总体验证与NAS | 协调方与独立最终审查 | 9 |

推荐最多4条实现工作流同时运行，审查按完成顺序插入；不为凑并发上限拆小任务。公共文件由表中负责人独占，任何跨边界修改先协调转交。

---

### Task 1: 后端冻结注册、raw与事件计划

**Files:** Create `apps/api/app/esports_assessments/rule_versions.py`、`apps/api/tests/unit/test_esports_v3_contracts.py`、`apps/api/tests/unit/esports_v3_samples.py`；Modify `config_schemas.py`、`catalog.py`、`raw_schemas.py`、`schemas.py`（均在`app/esports_assessments/`）、`apps/api/tests/esports_helpers.py`。新增跨语言fixture由任务2接收，旧样本文件不改成最新版。

**Interfaces:** 新增纯函数`registered_rule(suite_id: str, test_id: str, catalog_version: str) -> RuleIdentity`，`RuleIdentity`为冻结dataclass，包含`raw_kind: str, scoring_version: str, report_version: str`。继续使用`get_task(..., catalog_version=...)`和`validate_raw(config,value)`；在catalog新增`dodge_choice_schedule(seed: int, warning_ms: float) -> tuple[DodgeChoiceEvent,...]`，事件为TypedDict：`safe_option_id:int, warning_at_ms:int, activation_at_ms:float, resolved_at_ms:float`。任务期间默认目录仍为v2。

- [ ] **1. 保存旧序列化摘要并写RED。** 从现有v1/v2请求固定字面JSON及operation_digest结果，测试升级前后完全一致。写以下新目录测试；另参数化四套五档、新旧raw交叉错配、NaN／布尔／多余字段、移动字段缺失及switching先失败后命中。

```python
from app.esports_assessments.catalog import get_task, validate_raw
import pytest

def test_v3_moving_result_requires_new_kind():
    config = get_task('mobile-fps', 'move-fire', catalog_version='mobile-fps-v3')
    assert config.params.kind == 'move_fire_v3'
    assert config.scoring_version == config.report_version == 'mobile-fps-v2'
    with pytest.raises(ValueError):
        validate_raw(config, {'kind': 'move_fire', 'rounds': [
            {'hit': True, 'reached': True} for _ in range(20)
        ], 'shots': 20})
```

- [ ] **2. 运行RED。** `cd "$WT/apps/api"; "$PY" -m pytest tests/unit/test_esports_v3_contracts.py -x`。预期v3尚未登记而失败，不接受语法／导入环境错误作为业务RED。
- [ ] **3. 实现注册与约束。** 原参数、旧raw类原样保留；新`MoveFireV3Params`继承FpsParams，新`DodgeChoiceParams`继承MobaParams。前者登记`target_offset_min_deg/target_offset_max_deg/min_moving_ms/min_moving_speed_m_s`及原移动点参数；后者登记`safe_radius=32,candidate_offset=20,candidate_distance=100,warning_start_min_ms=100,warning_start_max_ms=600,burst_ms=150`、30轮／2000ms及规格五档warning。所有配置必须与注册版本精确相等。

```python
class MoveFireV3Round(StrictModel):
    hit: StrictBool
    reached: StrictBool
    hit_latency_ms: Time | None
    hit_speed_m_s: NonNegative | None
    pre_hit_moving_ms: Time | None

# 放在catalog.py，复用同文件现有next_u32；每次计划恰好推进60次。
def dodge_choice_schedule(seed: int, warning_ms: float) -> tuple[DodgeChoiceEvent, ...]:
    state = seed
    result: list[DodgeChoiceEvent] = []
    for index in range(30):
        state = next_u32(state)
        warning = index * 2000 + 100 + state % 501
        state = next_u32(state)
        result.append({'safe_option_id': state % 4, 'warning_at_ms': warning,
                       'activation_at_ms': warning + warning_ms,
                       'resolved_at_ms': warning + warning_ms + 150})
    return tuple(result)
```

给新round加规格完整一致性validator；新raw固定20／30条，不能加默认null来容忍缺字段。`validate_schedule`只对新版dodge核对上述四字段；旧识别计划保持。新增`sample_raw_v3(config:TaskConfig, *, perfect:bool, seed:int=123)->dict[str,object]`于新样本文件；修改`result_request`增加可选`catalog_version:str|None=None`，显式v3才用新样本，旧调用序列化保持旧语义，任务9再逐调用选择正确版本。
- [ ] **4. GREEN并冻结接口。** 跑`test_esports_v3_contracts.py test_esports_contracts.py test_esports_catalog.py test_esports_versions.py`及Ruff/mypy作用文件；向后续任务交付四套v3的每档配置、seed0/123/4294967295事件字面golden向量和旧摘要golden。检查点审查不涉及服务或评分文件。

### Task 2: 前端契约、场景扩展与视口预检

**Files:** Create `apps/web/src/esportsAssessments/versionRules.ts`、`ruleSchedule.ts`、`contractsV3.test.ts`、`testSupport/catalog-v3.json`、`testSupport/fpsActions.ts`；Modify `contracts.ts`、`engineRuntime/types.ts`、`testSupport/fixtures.ts`、`fps/schedule.ts`、`fps/precision.ts`、`fps/recognition.ts`及相关测试。以上路径均相对`apps/web/src/esportsAssessments/`。

**Interfaces:** `registeredRule(suiteId:SuiteId,testId:TestId,catalogVersion:string):{raw_kind:string;scoring_version:string;report_version:string}`；`buildDodgeChoicePlan(seed:number,params:Extract<TaskConfig['params'],{kind:'dodge_choice'}>):readonly DodgeChoicePlanEntry[]`（字段与任务1同名）；`fixtureV3Config(suite:string,test:string):TaskConfig`及`fixtureV3Raw(suite:string,test:string,perfect?:boolean,seed?:number):RawResult`。保留现有`fixtureConfig`＝v1和`fixtureCurrentConfig`＝v2，后续不得悄悄重绑这些历史fixtures。

- [ ] **1. 写RED并运行。** 在`contractsV3.test.ts`解析任务1交付的完整v3配置；验证旧fixture JSON.stringify不变，新旧raw互斥、少字段失败，golden事件逐字段相等。`npm test -- src/esportsAssessments/contractsV3.test.ts`预期新版本被拒。

```ts
it('does not accept legacy moving statistics for v3', () => {
  const config = fixtureV3Config('mobile-fps','move-fire')
  expect(config.params.kind).toBe('move_fire_v3')
  expect(() => validateRaw(config,fixtureRaw('mobile-fps','move-fire'))).toThrow()
})
```

测试先由任务1交付数据新增catalog-v3.json，再新增按现有schema解析该数据的fixtureV3Config，运行时由尚未支持v3的schema产生RED。对旧JSON的断言使用任务1记录的字面golden，不能两边同时调用新版模型生成期望。
- [ ] **2. 镜像后端联合类型和显式版本表。** 不新增隐式默认属性。用同一xorshift运算顺序实现事件计划；previewTaskConfig按冻结版本生成五档，v1/v2值完全保留。只对明确登记v2/v3启用当前视野刷新，precision／recognition相同seed和输入的raw及scene必须等价。

```ts
export interface MobaDangerChoiceSnapshot {
  readonly kind: 'safe-circle-v3'
  readonly phase: 'waiting' | 'warning' | 'active' | 'resolved'
  readonly safe_zone: null | { readonly position: Vector2; readonly radius: number }
}
// 在MobaSceneSnapshot增加可选danger_choice，不为旧scene写默认null。
// 在FpsSceneSnapshot增加可选movement_feedback，旧scene同样省略。
export interface FpsMovementFeedback {
  readonly state: 'ready' | 'moving' | 'stationary-hit' | 'hit'
  readonly qualifying_ms: number
}
```

`MobaSceneSnapshot.danger_choice?:MobaDangerChoiceSnapshot`及`FpsSceneSnapshot.movement_feedback?:FpsMovementFeedback`由本任务统一加好；保持GameEngine/GameFactory签名不变。`DodgeChoicePlanEntry`在ruleSchedule.ts导出，内容为任务1的四个number字段。
- [ ] **3. 写视口和动作helper测试再实现。** v3 switching预检必须检查完整切换角距，不再只检查半角距；move-fire检查最大偏移角及全部支持俯仰。覆盖844×390、640×360、1440×900和1—5难度；不满足登记几何的场地在申请尝试前拒绝。动作helper须先积分到输入时刻再计算真实相机瞄准：

```ts
export function aimHighlighted(g: GameEngine, at_ms: number): void {
  g.input({ kind: 'look', at_ms, dx: 0, dy: 0 })
  const s = g.scene()
  if (s.kind !== 'fps') throw new Error('expected fps')
  const t = s.targets.find(t => t.state === 'highlighted')
  if (!t) throw new Error('missing target')
  const a = vectorToDirection({ x: t.position.x-s.camera.position.x,
    y: t.position.y-s.camera.position.y, z: t.position.z-s.camera.position.z })
  g.input({ kind:'look', at_ms, dx:a.yaw_deg-s.camera.yaw_deg,
    dy:s.camera.pitch_deg-a.pitch_deg })
}
export function pulse(g: GameEngine, at_ms: number): void {
  g.input({kind:'fire',at_ms,pressed:true})
  g.input({kind:'fire',at_ms:at_ms+1,pressed:false})
}
```

此helper放testSupport/fpsActions.ts，导入现有GameEngine与vectorToDirection；以现有v2factory验证，不依赖未来引擎即可GREEN。
- [ ] **4. GREEN及接口审查。** 跑contractsV3、apiContracts、testSupport/contracts、previewTaskConfig、fpsVersions、spawnViewport测试，加`npx tsc -b`。schema新增kind造成的穷尽分支报错须在所属转换处明确拒绝不支持的kind，不能`as any`或默认套旧玩法。不得启用默认v3或改suite factories。

### Task 3: 服务端63秒首次提交守卫

**Files:** Modify `apps/api/app/esports_assessments/lifecycle.py`、`formal_service.py`、`practice_service.py`；Create `apps/api/tests/unit/test_esports_submission_timing.py`、`apps/api/tests/services/test_esports_submission_timing.py`。不改共享fixtures或全局旧测试时间轴。

**Interfaces:** 新增`require_submission_elapsed(created_at:datetime,config:TaskConfig,now:datetime)->None`；复用AssessmentConflict，错误码`esports_result_too_early`。正式/练习现有submit签名不变。

- [ ] **1. 写边界RED。** 纯函数覆盖62.999秒拒绝、63秒允许和naive datetime拒绝；服务用`student_actor/session_request/attempt_request/result_request`测试正式precision，用PracticeCreate直接构造练习precision。开启`esports_enabled`；不假设不存在的practice helper。

```python
def test_early_first_save_keeps_original_operation(db):
    actor = student_actor(db)
    service = FormalAssessmentService(db)
    now = datetime(2026, 9, 23, 2, tzinfo=UTC)
    session = service.create(actor, session_request(), now)
    run = service.start_attempt(actor, session.id, attempt_request('pc-fps','precision'), now)
    request = result_request('pc-fps','precision',seed=run.seed)
    with pytest.raises(AssessmentConflict) as error:
        service.submit(actor,run.id,request,now+timedelta(milliseconds=62999))
    assert error.value.code == 'esports_result_too_early'
    assert db.scalar(select(func.count()).select_from(AssessmentResult)) == 0
    saved = service.submit(actor,run.id,request,now+timedelta(seconds=63))
    assert service.submit(actor,run.id,request,now+timedelta(minutes=31)) == saved
```

示例测试须同时导入datetime/UTC/timedelta、pytest、sqlalchemy.func/select、现有服务/model/helper；文件级`pytestmark=pytest.mark.usefixtures('esports_enabled')`。追加断言失败前后尝试状态、报告数及提交审计数量不变。
- [ ] **2. 运行RED。** 先纯测试，再协调方串行执行服务测试；预期原服务不抛早到错误。
- [ ] **3. 实现守卫并接入。** 在成功幂等分支之后、首次评分前调用；不重取now、不修改状态投影。

```python
def require_submission_elapsed(created_at: datetime, config: TaskConfig, now: datetime) -> None:
    _aware(created_at, 'created_at')
    _aware(now, 'now')
    if now < created_at + timedelta(milliseconds=3000 + config.duration_ms):
        raise AssessmentConflict('测试尚未完成，请等待完整测试结束后再保存',
                                 code='esports_result_too_early')
```

在lifecycle导入timedelta及AssessmentConflict；已有锁／授权顺序不变。补练习同组用例、90秒后补交、30分钟边界、同键异payload、提交成功换键、停用后重放拒绝。
- [ ] **4. GREEN与检查点。** 跑本任务两个文件；记录既有综合测试中的60/61秒时间轴由任务9集中调整，不借此修改生产时长。其他任务不得并行启动DB测试。

### Task 4: 双端真实切换引擎

**Files:** Create `apps/web/src/esportsAssessments/fps/switchingV3.ts`、`switchingV3.test.ts`；Modify `fps/index.ts`。不改旧switching.ts、contracts.ts、schedule.ts。

**Interfaces:** `createSwitchingV3Engine(config:TaskConfig,seed:number,settings:ControlSettings,viewport:Size):GameEngine`；复用FpsTimeline、sampleVisibleDirection、nearestAngularHit、freezeResult和任务2 helper。

- [ ] **1. 写RED，走suite factory。**

```ts
it.each(['pc-fps','mobile-fps'])('measures actual switching: %s', suite => {
  const factory = suite === 'pc-fps' ? pcFpsFactory : mobileFpsFactory
  const g = factory.create(fixtureV3Config(suite,'switching'),123,
    fixtureSettings(suite),{width:844,height:390})
  expect(g.scene().targets).toHaveLength(1)
  aimHighlighted(g,200); pulse(g,200)
  aimHighlighted(g,1400); pulse(g,1400)
  const raw = g.finish()
  if (raw.kind !== 'aim') throw new Error('aim required')
  expect(raw.trials.slice(0,2)).toEqual([
    {hit:true,latency_ms:200},{hit:true,latency_ms:1200}])
})
```

运行`npm test -- src/esportsAssessments/fps/switchingV3.test.ts`，预期无法分派或计时／可见目标数错误。
- [ ] **2. 实现每组状态机。** 使用`groupIndex:number, firstHitAt:number|null, activeIndex:0|1|null, target:FpsTargetSnapshot|null`，预填40条失败记录。onAdvance以固定组边界推进；onShot只处理当前一个靶，命中第一靶立即spawn第二靶，但此次shot返回前不再次判定。

```ts
// onShot内命中后的两条记录分别由当前靶索引决定；at为绝对运行毫秒。
if (activeIndex === null || target === null) return true
const trialIndex = groupIndex * 2 + activeIndex
const origin = activeIndex === 0 ? groupIndex * 3000 : firstHitAt!
trials[trialIndex] = {hit:true,latency_ms:at-origin}
```

`firstHitAt`使用绝对运行时间，切勿混用组内时间；第一靶超时整组失败、第二靶窗口右端无效、60000ms不生成第21组。射击与完整屏内spawn使用共享实现，不自行改冷却。
- [ ] **3. 补边界RED→GREEN。** 首靶失败、首靶1499ms命中、第二靶到1500ms截止、持续开火、同发不能双中、无靶时空枪、30/60/120Hz、所有五档／±70°／屏内投影；完整raw必须40条。旧v1/v2原测试不改期望。
- [ ] **4. 验证与独立审查。** 跑switchingV3、switching、spawnViewport、fpsVersions和suites/pcFps、suites/mobileFps测试。产出前一命中200、后一命中1400得到1200ms的证据。

### Task 5: 手游有效移动命中引擎

**Files:** Create `fps/moveFireV3.ts`、`fps/moveFireV3.test.ts`；Modify `suites/mobileFps.ts`（相对前端模块）。不编辑任务4拥有的fps/index.ts。

**Interfaces:** `createMoveFireV3Engine(config,seed,settings,viewport):GameEngine`，参数类型同任务4；scene可写任务2的movement_feedback，finish输出move_fire_v3。

- [ ] **1. 写RED并运行。** 每个运动门槛用独立engine，避免200ms冷却遮蔽99/100ms边界。

```ts
it.each([[99,false],[100,true]] as const)('requires continuous movement %i', (at, hit) => {
  const g = mobileFpsFactory.create(fixtureV3Config('mobile-fps','move-fire'),123,
    fixtureSettings('mobile-fps'),{width:844,height:390})
  g.input({kind:'move',at_ms:0,x:1,y:0})
  aimHighlighted(g,at); pulse(g,at)
  const raw = g.finish()
  if (raw.kind !== 'move_fire_v3') throw new Error('new raw required')
  expect(raw.rounds[0].hit).toBe(hit)
  expect(raw.rounds[0].hit_latency_ms).toBe(hit ? 100 : null)
})
```

- [ ] **运行本任务RED。** `cd "$WT/apps/web"; npm test -- src/esportsAssessments/fps/moveFireV3.test.ts`。预期新版分派／有效命中条件尚未实现而失败。
- [ ] **2. 实现实际运动连续计数。** 在每次恒定运动段从位移长度／段时长计算speed；小于1立即清零，否则累计本轮时长。输入速度在命中当刻也必须达标；朝向改变不应凭空增加距离。轮边界先完成该轮再重置计数。

```ts
if (durationMs > 0) {
  const speed = travelledMeters * 1000 / durationMs
  qualifyingMs = speed >= p.min_moving_speed_m_s ? qualifyingMs + durationMs : 0
}
// 命中判定同时要求当前实际速度达标，不把最后一段旧速度当作当前速度。
const eligible = currentSpeed >= p.min_moving_speed_m_s && qualifyingMs >= p.min_moving_ms
```

`qualifyingMs/currentSpeed/travelledMeters`为引擎私有状态／本次积分计算值。用seed抽取[min,max]偏移角，再调用sampleVisibleDirection；生成后不跟随相机。原地打中仅增加shots、产生stationary-hit反馈；有效命中才失活并保存三个测量字段。轮末用原distance容差检查到位。
- [ ] **3. 补RED→GREEN矩阵。** 原地一枪后走到点联合成功0；原地打中后可再次有效命中；速度0.99/1、短暂停止、同毫秒启移动射击、跨轮重置、只走／只射、零shots、30/60/120Hz、raw不含非有限数。数值误差容差仅处理浮点舍入，不能把99ms宽松成100ms。
- [ ] **4. 验证与检查点。** 跑moveFireV3、moveFire、spawnViewport、mobileFps、fpsVersions，确认新靶不贴准星且完整可见，旧样本不变。

### Task 6: 随机安全圆MOBA引擎

**Files:** Create `moba/dodgeChoice.ts`、`moba/dodgeChoiceGeometry.ts`、`moba/dodgeChoice.test.ts`、`moba/dodgeChoiceGeometry.test.ts`、`moba/dodgeChoiceStrategies.test.ts`；Modify `moba/world.ts`、`suites/pcMoba.ts`、`suites/mobileMoba.ts`。不改旧dodge.ts或renderer。

**Interfaces:** `createDodgeChoiceEngine(config:TaskConfig,seed:number,settings:ControlSettings):GameEngine`；纯几何`dodgeSafeCenters(player:Vector2,size:Size):readonly Vector2[]`、`firstCircleEntry(from:Vector2,to:Vector2,center:Vector2,radius:number):number|null`（返回线段首次进入比例0—1）。基类新增`protected nextMovementBoundaryAt():number`；只有新引擎使用该到达目的地／碰墙时刻，旧引擎的推进不变。

- [ ] **1. 几何RED与最小实现。** 测四角、墙边、中心及101×101网格；圆全部在场内、有效区互斥、起点不安全。

```ts
it('keeps all corner candidates visible and mutually exclusive', () => {
  for (const p of [{x:12,y:12},{x:988,y:12},{x:12,y:550.5},{x:988,y:550.5}]) {
    const centers = dodgeSafeCenters(p,{width:1000,height:562.5})
    expect(centers).toHaveLength(4)
    for (const [i,c] of centers.entries()) {
      expect(c.x).toBeGreaterThanOrEqual(32)
      expect(c.x).toBeLessThanOrEqual(968)
      expect(c.y).toBeGreaterThanOrEqual(32)
      expect(c.y).toBeLessThanOrEqual(530.5)
      expect(Math.hypot(c.x-p.x,c.y-p.y)).toBeGreaterThan(20)
      for (const other of centers.slice(i+1)) {
        expect(Math.hypot(c.x-other.x,c.y-other.y)).toBeGreaterThan(40)
      }
    }
  }
})
```

先运行`npm test -- src/esportsAssessments/moba/dodgeChoiceGeometry.test.ts`观察缺少新增几何行为的RED，再实现：

```ts
export function dodgeSafeCenters(p: Vector2, size: Size): readonly Vector2[] {
  const sx = p.x <= size.width/2 ? 1 : -1
  const sy = p.y <= size.height/2 ? 1 : -1
  return [0,30,60,90].map(deg => ({
    x:p.x+sx*(20+100*Math.cos(deg*Math.PI/180)),
    y:p.y+sy*(20+100*Math.sin(deg*Math.PI/180)),
  }))
}
```

firstCircleEntry解`|from+t(to-from)-center|²≤radius²`，零位移在圆内返回0、圆外null；切线与端点按闭圆处理。运动边界须切片到碰墙／停在点击目的地，不能把实际折线当一段匀速直线。补“已在墙上继续推杆／已到点击目的地／零方向”用例，nextMovementBoundaryAt不能反复返回当前时刻导致死循环，无下一次运动变化返回Infinity。
- [ ] **2. 引擎RED。** waiting不得泄露safe_zone；预警后圆固定；不操作30轮全hit；正确到圆心并保持可30轮成功；进入后短暂离开爆发区失败。用真实input/advanceTo驱动，不直接赋值player。
- [ ] **3. 实现事件推进。** 继承MobaEngine，预先消费buildDodgeChoicePlan；nextEventAt取预警／爆发／结算／轮末／运动边界的最小未来值。预警才按当时player冻结四选一位置；首次入圈时间用交点比例换算，scene读取不推进RNG。爆发片段任一位置不安全就锁定hit，结算后不可反转。

```ts
// scene()在现有snapshot结果上有条件扩展，新字段只属于新版dodge。
return { ...base, danger_choice: {
  kind:'safe-circle-v3', phase,
  safe_zone: center ? {position:{...center},radius:32} : null,
} }
```

这里`base`为现有snapshot返回，`phase/center`为当前私有状态。waiting时center必须null；resolved可保留圆显示但不得继续伤害。world.ts仅对明确登记v2/v3允许PC左键和现有反馈格式v2；旧scene不新增属性。
- [ ] **4. 大样本与时间边界验收。** 预登记seed1—1000，每种盲策略独立运行：横移每2000ms反向、切向绕世界中心、每200ms用独立固定seed随机选方向、不动。都不能读取当前safe_zone来选路；均分≤40、95分位（排序后第950项）≤50。读预警后以真实移动到圆心的oracle为100分；检验最终raw用正式30轮／100分换算。另跑各难度、渲染频率、重复scene、resolved=60000先结算后结束、60000输入忽略。失败不得换seed集。
- [ ] **5. 检查点。** 跑新三份测试、mobaVersions、moba/feedbackV2、suites/pcMoba和mobileMoba；向任务8交付scene样本，不移交引擎所有权。

### Task 7: 新版服务端评分与报告

**Files:** Modify `apps/api/app/esports_assessments/scoring.py`、`reporting.py`；Create `apps/api/tests/unit/test_esports_v3_scoring.py`、`test_esports_v3_reporting.py`。依赖任务1；不修改契约和fixtures。

**Interfaces:** 保留score_result/practice_result/summarize_metrics/build_report公共签名；内部根据已验证config选择原版或新版分支。新增指标键：`switching_group_successes,switching_group_rate,acquisition_success_average_ms,switching_success_average_ms,switching_scoring_average_ms,moving_hits,safe_entries,safe_entry_average_ms`；复用`accuracy,shots,reached,joint_successes,joint_rate,avoided,hits_taken,avoid_rate`。

- [ ] **1. 字面算例RED。** 使用现有QualityInput及sample工具，但期望分数必须独立给定，不能用被测公式计算期望。

```python
def test_real_slow_switching_is_not_perfect():
    config = get_task('pc-fps','switching',catalog_version='pc-fps-v3')
    raw = validate_raw(config, {'kind':'aim', 'trials': [
        row for _ in range(20) for row in (
            {'hit':True,'latency_ms':200}, {'hit':True,'latency_ms':1200})
    ], 'shots':40, 'wrong_target_shots':0})
    quality = QualityInput(duration_ms=60000,max_frame_gap_ms=16,
        long_frame_gap_total_ms=0,input_kind='mouse',hidden=False,interrupted=False)
    assert score_result(config,raw,quality).score == 55
```

运行`cd "$WT/apps/api"; "$PY" -m pytest tests/unit/test_esports_v3_scoring.py tests/unit/test_esports_v3_reporting.py`；预期旧公式／模板与明确新版期望不符。
- [ ] **2. 实现分派与精确舍入。** 不让新版切换落入通用AimRaw旧公式。使用现有Fraction、_fraction_ratio、_speed_fraction、_round_fraction，先完成两靶组计数，再只取奇数项耗时计算速度：

```python
score = _round_fraction(Fraction(100) * joint_rate
    * (Fraction(2,5) + Fraction(3,5)*speed)
    * (Fraction(1,2) + Fraction(1,2)*accuracy))
```

joint_rate、speed、accuracy来自规格定义和校验后的raw；新move-fire以有效移动hit代替旧hit，新dodge按未受击数／30。practice只返回指标、score/grade保持null。
- [ ] **3. 报告RED→GREEN。** v3受影响项目使用v2模板；旧_LABELS/_ADVICE保留。新dodge名称“危险区域识别与躲避”；新移动和切换建议解释各自指标，不称作统一反应速度。无成功样本指标null，不能0；相同旧报告fixture逐字段不变；跨版本comparison为不可比较、不输出score_delta。
- [ ] **4. 验证审查。** 跑v3_scoring/v3_reporting及全部旧scoring/reporting/quality测试；覆盖0/100、39/40/69/70/89/90、半分舍入、失败惩罚、射击分母和全部失败样本。纯测试可与引擎并行。

### Task 8: 新版反馈、指引、结果与恢复文案

**Files:** Modify `moba/renderer.ts`及测试、`MobaFeedback.tsx`、`MobaInstructions.tsx`及测试、`AssessmentHost.tsx`及测试、`hostState.ts`、`reportPresentation.tsx`、`AssessmentReportPage.tsx`、`AssessmentSessionPage.tsx`、`PracticePage.tsx`、`useAssessmentSession.ts`及测试、`assessment.css`（如需固定反馈高度）；Create `V3Guidance.test.tsx`、`V3Reports.test.tsx`。均相对前端模块。pending.ts仅在回归发现实际兼容问题时由本任务修复。

**Interfaces:** 新增`assessmentTaskLabel(config:TaskConfig):string`于hostState.ts，新增报告专用`reportTaskLabel(config:TaskConfig):string`于reportPresentation.tsx；各自保留旧标签映射。Metrics新增可选`emptyLabel?:string`，默认旧“暂无有效样本”，仅新版改变的报告传入“—”。

- [ ] **1. 用scene fixture写RED。** 新dodge waiting无安全圈、warning黄色纹理、active红色区域、resolved不再伤害绘制；旧无danger_choice分支绘制调用不变。固定反馈尺寸切换状态不使canvas移动。用明确文本断言“移动中命中才有效”“安全区进入用时”，不能只做snapshot。

```tsx
it('shows no fabricated zero for an empty v3 switching sample', () => {
  render(<Metrics metrics={{switching_success_average_ms:null}} emptyLabel="—" />)
  expect(screen.getByText('—')).toBeVisible()
  expect(screen.queryByText('0 ms')).not.toBeInTheDocument()
})
```

运行`npm test -- src/esportsAssessments/V3Guidance.test.tsx src/esportsAssessments/V3Reports.test.tsx`，预期新文案／空值呈现未实现；测试文件导入现有Testing Library和Metrics，不mock其渲染实现。
- [ ] **2. 实现只读呈现。** Canvas以evenodd路径填充全场减去安全圆，区域绘制在角色之前，圆使用逻辑半径32而非20：

```ts
context.beginPath()
context.rect(0,0,scene.logical_size.width,scene.logical_size.height)
context.moveTo(zone.position.x+zone.radius,zone.position.y)
context.arc(zone.position.x,zone.position.y,zone.radius,0,Math.PI*2)
context.fill('evenodd')
```

`zone`来自当前非null的danger_choice.safe_zone；绘制前save、结束restore，resolved只画边界和文字。测试canvas mock须覆盖fill参数和现有方法。Host读取movement_feedback和danger_choice，不自行计算成功率或改引擎时间。
- [ ] **3. 版本化指引和指标。** 新切换解释首次命中后第二靶出现；移动解释持续100ms及半速门槛；dodge解释完整进入安全圆并保持爆发期间安全。其它项目v3沿用v2说明。旧报告名称／空样本展示不变化；新报告不出现原“首次移动平均用时”。
- [ ] **4. 保存错误与恢复RED→GREEN。** Mock首次submit返回409/`esports_result_too_early`，保留pending和原operation_key，显示中文原因和手动重试；模拟GET确认已保存则移除对应pending并显示原结果，不发第二次create。分别覆盖撤权、切账号后旧响应、旧v1/v2缓存、未知目录提示刷新，不能把所有409都标成“未完成”。
- [ ] **5. 验证审查。** 跑V3Guidance/V3Reports、MobaFeedback/MobaGuidance、AssessmentHost、useAssessmentSession、pending、AssessmentNavigation及旧报告测试；ESLint、tsc。外观验收交任务9，不能用mock宣称手机真机通过。

### Task 9: 启用默认v3与真实流程集成

**Files:** Modify 默认注册处`apps/api/app/esports_assessments/config_schemas.py`、必要前端默认目录处、`apps/api/app/audit/service.py`登记白名单；更新`apps/api/tests/services/test_esports_version_compatibility.py`、`tests/api/test_esports_routes.py`及`tests/{services,api,db}/test_esports_*.py`中受影响时间轴；Modify `apps/web/tests/esports-pc.spec.ts`、`esports-touch.spec.ts`、`esports-recovery.spec.ts`、`tests/helpers/esportsAssessment.ts`、必要`esportsArtifactIdentity.ts`和后端`app/cli/summarize_esports_assessments_e2e.py`及对应CLI tests。公共文件进入本任务前原负责人冻结。

**Interfaces:** 不新建业务API、权限或测试时钟后门；只使用现有/create/start/submit/query/恢复流程。新默认改为`<suite>-v3`，所有旧版本仍显式支持。先校验本地seed和fixtures的时间轴再跑服务测试。

- [ ] **1. 写默认v3与旧场次完整回归RED。** 创建旧v1/v2四套场次，保存一项，模拟升级目录后继续余下三项；每项以`started_at + 63s`提交、下一项至少`started_at + 64s`开始；旧摘要重放保持一致。创建新v3全部四套，分别确认snapshot、报告版本与跨版本不可比。不要只改单次60→63而让后次开始落在前次结束之前。
- [ ] **2. 启用默认及集中修复调用点。** 指定旧公式测试继续用旧目录；新版服务测试显式用run.config版本生成payload。安全审计白名单增加真实出现的新目录／评分／报告值，不扩大可记录敏感payload范围。检查6处严格v2判断和未改十一项的v2/v3等价。
- [ ] **3. 改造E2E真实时间边界。** 当前`EA-PC-SIM-002`、`EA-TOUCH-SIM-002`快速提交不能通过新增服务器守卫。将所有期待真实API持久化成功的项目改用真实60秒运行；仅不落库的纯UI／引擎测试保留快进。相应案例ID改为REAL-002，更新汇总器和artifact白名单，测试超时按项目数设置而非放宽业务规则。

```ts
const save = observeUiSave(page,'formal')
const native = await observeNativeActiveRun(page)
try {
  await startMeasured(page,'formal')
  await expectPhase(page,'running')
  await exercisePcInput(page,suite,item)
  const result = await save.waitSaved()
  expect(await native.activeDurationMs()).toBeGreaterThanOrEqual(60000)
  expect(save.postCount()).toBe(1)
  expect(result.mode).toBe('formal')
} finally { save.dispose(); await native.dispose() }
```

示例沿用现有helpers的变量来源与页面流程；手机用既有触摸输入helpers。不要拦截请求伪造成功、回拨数据库created_at、禁用守卫或新增生产时钟覆盖。新增早到API测试明确期待409，并用真实时间后的同键恢复作为不同断言。
- [ ] **4. 串行DB回归，再跑隔离E2E。**

```sh
cd "$WT/apps/api"
PYTHONPATH="$PWD" "$PY" -m pytest tests/unit/test_esports_*.py tests/services/test_esports_*.py tests/api/test_esports_*.py tests/db/test_esports_*.py --junitxml=/tmp/esports-v3-api.xml
cd "$WT"
E2E_PYTHON_BIN="$PY" bash scripts/run-esports-assessments-e2e.sh
```

E2E脚本另要求FIELD_ENCRYPTION_MASTER_KEY、FIELD_LOOKUP_KEY、SESSION_TOKEN_PEPPER等现有隔离环境变量；按脚本校验加载本地测试配置，不读取或输出NAS密钥。先确认loopback origin和隔离库安全检查成功，两个命令不能并行。实际Pointer Lock需可聚焦的专用浏览器；失败记录能力阻断，不拿模拟时钟结果冒充。
- [ ] **5. 浏览器证据与局限。** 改动的五项各至少一次真实60秒正式结果入库；四套报告继续下一项闭环；一项断网待保存后恢复；PC真实Pointer Lock、手机横屏触摸与多指、控件遮挡和反馈不跳动。移动模拟与Android/iPhone真机分开记；无真机证据保持超管试测限制，不宣称正式手机验收完成。

### Task 10: 总体审查、发布源同步与NAS验证

**Files:** Update `docs/features/esports-assessments.md`；Create `docs/verification/2026-09-23-assessment-measurement-integrity.md`记录本轮检查；主仓库outputs写简明发布回执。只同步本轮变更清单，保留发布源独有文件和配置。

**Interfaces:** 复用现有`/api/v1/health`与发布脚本回执；继续使用当前域名HTTPS18080，不改端口、证书、秘密或数据卷。

- [ ] **1. 完整验证。** 根协调方依次领取DB执行权；前端可与后端纯检查并行，DB与E2E不得重叠。

```sh
cd "$WT/apps/api"
"$RUFF" check app tests
"$MYPY" app
PYTHONPATH="$PWD" "$PY" -m pytest --junitxml=/tmp/erp-v3-backend-full.xml
cd "$WT/apps/web"
npm run lint
npm run build
npm test -- --maxWorkers=4 --reporter=default --reporter=junit --outputFile.junit=/tmp/erp-v3-frontend-full.xml
```

对失败先定位新增回归或既有基线问题；修复后按唯一测试身份合并原报告和重跑记录，不能用只过失败子集声称全量通过。明确记录跳过项与真实设备未覆盖项。
- [ ] **2. 独立最终审查。** 提供任务增量、批准规格、全量日志和旧兼容证据。重点检验raw错配、旧摘要、guard位置、移动积分／MOBA交点、scene无副作用、v3未变项目、pending权限及测试时钟。所有重要问题修复并重跑相关检查。
- [ ] **3. 安全整合到发布源。** 比较执行前清单、当前工作树和当前发布源，生成仅本轮实际差异路径；逐文件用apply_patch应用增量，若发布源同路径已被他人更新先人工合并，不覆盖。新文件由apply_patch添加；不复制整个worktree或修改NAS。同步后对本轮路径字节/权限一致性和发布源构建再验证，更新验收回执。
- [ ] **4. 执行唯一发布入口。**

```sh
cd '/Users/skymoonzyj/Documents/new project3'
scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
```

发布预检、备份、迁移检查或健康检查失败立即作为发布阻断；不得绕过脚本。预期无新迁移，当前head应维持0060（执行时先以仓库与NAS新鲜结果确认，若已有合法新head则检查完整兼容，不倒退）。
- [ ] **5. 发布后只读核对。** `.active-release`为权威，不用旧current软链；核对公网构建hash、容器后端代码与发布源、数据库head、API/Web/DB/代理健康、调度心跳、加密备份和隔离恢复回执。仅通过授权正常页面看新规则入口／说明，不在NAS编造成绩或改真实账号。新raw落库后不可回滚至不认识它的旧后端；必要时暂停入口并向前修复。

## 计划自检与执行交接

- 规格1—2：任务1、2、9；规格3：任务4、7、8；规格4：任务5、7、8；规格5：任务1、2、6、7、8；规格6：任务3、8、9；规格7：任务7、8；规格8—9：任务9、10。
- 新增接口已在所属任务给出，后续任务不假设现有helper已经支持新版本。两个新增scene字段不写入旧快照。评分版本与反馈格式版本不混用。
- 已明确旧测试60／61秒时间轴与浏览器快进对新增守卫的影响；不以修改生产规则换测试通过。
- 本计划是待审阅的执行文档，不代表业务代码或NAS已经更新。推荐沿用当前会话的子任务并行执行方式；用户审阅计划确认后才开始任务1／3。
