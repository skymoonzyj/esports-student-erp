# 端游 MOBA 五分钟兵线 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用可选择近战/远程角色的原创 3D 五分钟兵线补刀替换新端游 MOBA 套题，成绩在 ERP 保存并按模式独立排名。

**Architecture:** 新 pc-moba-v4 单成员快照及独立 lane 引擎；纯逻辑战斗与 Three.js 渲染分离。模式进入配置快照，沿用授权、幂等、历史、成绩与排名基础设施。

**Tech Stack:** TypeScript / React / Three.js / Vitest / Playwright；FastAPI / Pydantic / SQLAlchemy / pytest；标准 NAS 部署。

**Spec:** `docs/superpowers/specs/2026-11-10-pc-moba-lane-design.md`

## Global Constraints

- 实际源 `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`，外部管理 detached 工作树。不得提交、重置、清理已有修改；基于开始时快照审查本次增量。
- 用户选择“替换端游测试，按推荐方案推进”，覆盖整套 pc-moba 新轮次；不影响 FPS、手机或历史。无需额外规格/计划等待，但保留规格及技术复核。
- pc-moba-v4 / lane_last_hit / 唯一 last-hit，正式 300000ms，旧规则 60000ms。仅新 pc-moba 要求 attack_mode；固定 mode 进入 params 及排名快照。
- 双方 [0,300) 每30000ms生成3近战2远程，共10波。只正补；人物移动/普通攻击，无技能、敌方英雄或死亡机制。近战/远程等具体数值严格使用规格表。
- 正式/服务端练习5分钟，本地试操作20秒。标准难度3，不给新模式显示五档练习。固定50机会评分，死亡补刀率与未结算兵数另列，不误称未死亡为漏刀。
- 只用现有 Three.js 程序化原创低模，不新装引擎或外部素材。原模型/旧引擎保留。
- 对外可参与套题 pc-fps、pc-moba；手机开放状态不动，原权限和超管预览限制不变。无数据库迁移。
- 数据库测试只在专用端口55441串行；浏览器验收另用55442。不得用正式数据、降低质量门槛或模拟鼠标锁定来冒充验收。
- 主代理做最终整合及标准 NAS 发布（SSH202），不得复制文件或手工 Docker 旁路。保留原始失败与最终结果。

## Review Focus

1. 300秒扩展不得放松旧60秒验证或让旧租期失效：Task1/4精确版本和时钟/输入边界测试。
2. 旧操作键重放、新模式切换、刷新恢复和延迟响应不能偷换模式：Task1/4幂等及控制器身份测试。
3. 弹道晚到、同时致死、点地取消、5:00边界：Task2真实事件测试，不只断言常数。
4. 3D倾斜、DPR和不同画布尺寸不能造成点兵偏移/遮挡：Task3反投影和Task4真实场景验收。
5. 单成员报告与新榜不得混入旧四项或另一人物模式：Task1服务持久化与Task4页面测试。

---

### Task 1: 版本契约 服务器计分及保存

**Files:**
- Modify: `apps/api/app/esports_assessments/{config_schemas,catalog,raw_schemas,schemas,formal_service,practice_service,lifecycle,scoring,reporting,ranking,rule_versions}.py` 及需要的 query_schemas；`apps/api/app/audit/service.py`。
- Modify: `apps/web/src/esportsAssessments/{contracts,versionRules}.ts`。
- Create: `apps/web/src/esportsAssessments/testSupport/{catalog-moba-v4.json,fixturesMobaV4.ts}`；`contractsMobaV4.test.ts`。
- Create/test: `apps/api/tests/{unit,services,api}/test_esports_moba_v4.py`；更新明确依赖当前默认/开放状态的既有测试，保留显式历史覆盖。

**Interfaces:**
- `AttackMode = Literal['melee','ranged']` / TS同名联合。
- `LaneLastHitParams`：继承 MobaParams；kind='lane_last_hit'；attack_mode；wave_interval_ms=30000，wave_count=10，melee_per_wave=3，ranged_per_wave=2，attack_windup_ms/attack_interval_ms/attack_damage/projectile_speed，player_radius=12、player_speed=250、attack_range；逻辑尺寸1000×562.5。人物字段值按规格表。兵属性由固定engine版本常量定义。
- `get_task/get_suite/_registered_params` 增加 keyword-only `attack_mode: AttackMode | None = None`；未传的目录预览默认melee；mode仅适用于新pc-moba-v4。配置重建从冻结params取mode，不丢失ranged。TaskConfig仅新v4可300000，其余60000。登记新模式难度1..5使用相同固定参数以维持既有目录构造接口；用户界面与正式始终难度3。
- `LaneLastHitRaw`：kind，deaths（最多50个，unit_id0..49，at_ms整数[0,300000)，by_player布尔），attacks整数>=0，cancelled_attacks整数0..15000；按规格唯一/排序/生成时间/攻击上限校验。新前摇仅固定20ms逻辑帧开始，取消不施加完整攻击冷却；同帧多个点选不能制造无限前摇。
- 原始数据metrics键：last_hits, enemy_deaths, missed, unresolved, last_hit_rate, opportunity_completion, last_hits_per_minute, attacks, cancelled_attacks, minute_1_last_hits…minute_5_last_hits。score由50分母计算；原grade沿用。
- TS `isPcMobaLaneCatalog(suiteId,catalogVersion): boolean`；`fixtureMobaV4Config(mode: AttackMode='melee'): TaskConfig`；`previewTaskConfig`保留mode，支持从默认目录生成mode专用预览。新增创建请求字段可选，但新服务创建必须有mode；旧重放先核对原摘要，缺省mode不进入旧摘要。

- [ ] RED：测试新melee/ranged精确配置、300000；旧v3/FPS60k及完整fixture不变；伪造时长/参数拒绝；单成员对旧套题不放宽；两端fixture一致。
- [ ] RED：真实DB测试完整5分钟创建/提前提交拒绝/租期330s/保存一项即报告/幂等重试模式冲突/旧请求摘要重放；melee/ranged和v3榜单不混入，前三/本人正确。
- [ ] 实现版本登记、签发冻结、动态租期、质量时长范围及精确时长门禁；仅lane原始时间放宽，不改旧raw字段上限。请求错误明确中文。
- [ ] 实现服务器原始校验、指标、整数HALF_UP分数、报告建议和新榜。死亡事件伪造边界、同ID重复、时间越界、无死亡、满50正确评分和每分钟分段有单测。
- [ ] 注册pc-moba对外开放并验证原授权不扩权，手机仍关闭。更新API当前默认断言；历史回放仍显式旧配置。
- [ ] TS同步契约并产出fixture/helper。先报告接口冻结，之后跑所有相关后端、契约、静态检查；写报告并接受增量审查。不提交Git。

### Task 2: 五分钟真实兵线模拟

**Files:**
- Create: `apps/web/src/esportsAssessments/lane/{engine,simulation,constants,types}.ts` 及对应测试；可在必要时分离 `combat.ts`，不得无界增大单文件。
- Modify: `apps/web/src/esportsAssessments/engineRuntime/types.ts`，`suites/pcMoba.ts`及测试。

**Interfaces:**
- `createLaneEngine(config: TaskConfig, seed:number, settings:ControlSettings): GameEngine`，仅exactpc-moba-v4/lane_last_hit；GameEngine接口不变。
- `LaneUnitSnapshot`：id:string, team:'ally'|'enemy', kind:'hero'|'melee'|'ranged', position:Vector2, facing_rad:number, health:number, max_health:number, state:'idle'|'moving'|'windup'|'attacking'|'dying', attack_progress:number(0..1), hit_at_ms:number|null。
- `LaneSceneState`：attack_mode，units数组（含hero及可见死亡短动画），projectiles[{id,team,position:Vector2,target:Vector2}]，selected_target_id:string|null，move_destination:Vector2|null，wave:number，wave_count:10，last_hits:number，enemy_deaths:number，missed:number。
- `MobaSceneSnapshot` 增加可选 `lane?: LaneSceneState`（type-onlyimport来自lane/types），旧snapshot字节不变。lane引擎scene仍kind='moba'，logical_size原1000×562.5，player/targets提供兼容投影，minimap.visible=false，不用旧feedback伪造回合。
- 输入为逻辑世界point。render任务提供screen→world函数，Host负责变换；engine不依赖Three或DOM。

- [ ] RED：逐波双方兵种/数量、[0,300000)末界、固定种子重放、无输入完整5分钟能结束且无卡死，实体数有界。
- [ ] RED：真实AI互伤/移动、玩家攻击追到范围内/持续当前目标、死亡停攻、取消未释放前摇、远程弹道延迟及释放后移动、同刻击杀只结算1次、不能打友方/使用技能。
- [ ] 实现20ms步长独立引擎及固定统计，遵守规格单位数值和出生位置。合法points有限且有界；过期/倒序输入不能污染结果。避免全体单位堆成一个点，近邻分离但不改变候选/伤害时序非确定性。
- [ ] RED/GREEN：至少10seed、两种mode的5分钟推进；不同advanceTo拆分产生相同场景/原始结果；timestamp边界/NaN/极端坐标/finish幂等。用手写时间线证明伤害归属，不复制生产算法计算expected。
- [ ] exactv4 factory分支；旧last-hit/rhythm/dodge/map不改。先冻结LaneSceneState给Task3/4；报告测试及新增文件，接受独立审查。

### Task 3: 原创模型与3D兵线场景

**Files:**
- Create: `apps/web/src/esportsAssessments/lane/{models,projection,renderer}.ts`，`LaneStage.tsx`及对应测试。只拥有这些文件，不改Host或共享引擎类型。

**Interfaces:**
- 消费Task2 `LaneSceneState` 和MobaSceneSnapshot.lane。
- `laneScreenToWorld(point:Vector2):Vector2|null`：screen逻辑1000×562.5，固定相机center=(500,0,281.25)，位置center+(0,866.025403784,500)，ortho[-500,500]×[-281.25,281.25]；平面反投影x不变、y=(screen.y-281.25)/cos30°+281.25。边界外返回null。测试投影/反投影一致；单位模型高度/命中半径使模型主体可点击，不只地面脚点。
- `createLaneRenderer(canvas:HTMLCanvasElement)` → resize(width,height,dpr), render(scene:MobaSceneSnapshot|null), dispose()；export testable scene/model creation withoutWebGL backend。
- `LaneStage` props兼容MobaStage（含inputHandlers、canvasRef、viewport、onReady、onError），错误扩展webgl-context-lost并及时通知Host中断，不静默回退伪造验收。

- [ ] RED：模型拥有可识别人形头身肢体/武器，近战和远程不同；小兵双方材质不同；动画改变对应肢体而非整体闪色，healthbar比例准确、资源可dispose。
- [ ] 实现原创低模人物和小兵、地面道路/简单环境、阴影、选中圈、移动点、血条、真实弹道及短死亡反馈。无外部贴图或资产包。复用geometry/material，按id更新，不每帧重建全部对象。
- [ ] RED/GREEN：正交相机完整平面、边缘输入、DPR1/2、resize、NaN防护、只删除消失单位、dispose/contextloss；无GPU模型/投影单测+Stage生命周期测试。
- [ ] 接口冻结后通知Task4，报告实际截图待根代理浏览器验证；测试通过不冒充视觉验收。

### Task 4: 入口 模式选择 时长和报告界面

**Files:**
- Modify/Test: `apps/web/src/esportsAssessments/{AssessmentHome,AssessmentSessionPage,AssessmentHost,AssessmentReportPage,PracticePage,useAssessmentSession,reportPresentation,hostState,MobaInstructions,MobaFeedback}`实际存在文件；`input/desktop.ts`、`engineRuntime/quality.ts`及相关测试；可新增`lane/LaneFeedback.tsx`。
- Modify/Test: `apps/web/tests/esports-pc.spec.ts`、helpers；`apps/api/app/cli/seed_esports_assessments_e2e.py`及其CLI测试/summary，仅在默认模式变化需要时。

**Interfaces:**
- Task1 AttackMode及预览helper；Task2lane场景；Task3LaneStage/反投影。DesktopInputLayout增加可选duration_ms，默认60000；仅新laneHost传300000。Host发给laneengine前将point屏幕逻辑坐标反投影，其他模式不改变。
- StartSettings增加attack_mode可选，模式必须随重试操作键冻结；已有正式/练习从serverView.config读取。新建时Home选择mode，practiceURL/身份带mode，切换mode须成为新请求身份不能复用旧操作键；恢复以服务器为准。

- [ ] RED：入口选择近战/远程创建相应请求，改mode不能沿用未定请求；单成员5分钟说明、旧四项历史仍显示旧模式；手机/FPS不变。
- [ ] Host全链路使用服务器config.duration_ms：clock、remaining、deadline、quality、输入窗口，trial仍20000。不要只修改显示倒计时。FrameQualityTracker支持5分钟累计但旧测试仍通过。
- [ ] 新lane用LaneStage，反馈位于画布外（剩余mm:ss、波次、补刀/漏刀）。左键流程完整可用；右键范围内preventDefault不干扰页面其他区域。未准备/非法viewport不得先取正式票。
- [ ] 练习只标准3；报告显示mode、指标/每分钟5段、前三/本人/差距。明确补刀率和总机会完成率口径、未结算兵非漏刀；新旧报告按保存的config而非当前默认猜测。
- [ ] 修改相关Vitest并运行。新增显式opt-in `ESPORTS_E2E_LANE_ACCEPTANCE=1` 专用Chrome验收，默认矩阵不添加跳过用例或额外成绩；不削弱strictsummary。
- [ ] native验收准备可复用假账号和独立DB，实际5分钟两mode保存、读取报告；不能用加速时钟作为唯一浏览器完整验收。写报告接受任务审查。

### Task 5: 最终验证和标准发布

- [ ] 主代理冻结源后运行前端全套、lint/build；专用DB串行后端完整测试（若环境限制需明确实际范围及失败）；静态检查。实际运行结果不沿用旧日志。
- [ ] Chrome视觉检查两模型/双方3+2/血条/弹道/点击；两种模式各完整300s、报告保存/排名分开；至少一个模式窗口变化/隐藏中断不得计入正常分。保留安全截图和证据，清理独占测试资源。
- [ ] 所有任务增量审查后，最终独立review（高能力模型）并解决问题。无未审修改或假成功。
- [ ] `PATH='/Volumes/MacDev/Projects/new project3/.venv/bin':"$PATH" NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`。预检/构建/备份/恢复失败均为发布阻断，不旁路。
- [ ] 只读核实active-release、健康/公网、运行中pc-moba-v4及mode/duration、旧pc-moba-v3可读/FPSv5不变；不生成线上成绩。报告需要刷新并新建轮次。

## 执行自检

规格各部分映射到Task1契约评分、Task2战斗、Task3模型、Task4交互、Task5验证发布。Task1先冻结共享契约；Task2先冻结类型后可与Task3/4并行，文件独占避免冲突。用增量快照代替提交，不动既有工作。用户已选择直接推进，此计划记录默认值并执行，不重复请求同类确认。
