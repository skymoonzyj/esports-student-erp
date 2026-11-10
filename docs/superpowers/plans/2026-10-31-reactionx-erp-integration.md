# ReactionX 网页接入与整体交付 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** ERP 管理实体灯配对、人员、九模式训练、可靠保存和八模式排行榜，先由超管在真实 Windows 与六灯上试测。

**Architecture:** ERP 保存身份、运行清单、命令与最终成绩；Windows 连接器主动 HTTPS 轮询领取有限命令，本地执行和计时，持久化后可靠回传。网页不连接本机端口，不在网络往返中测反应时间。网页单项榜、设备接入、Windows 连接器分为三份计划，接口和发布关卡在本文件汇合。

**Tech Stack:** 现有 FastAPI、SQLAlchemy、PostgreSQL、React/TypeScript；Windows 复用 Python/bleak，详见配套计划；初版不增加 WebSocket 服务、消息队列或公开本机 HTTP 服务。

**Spec:** [已确认规格](../specs/2026-10-31-reactionx-erp-integration-design.md)。配套：[A 单项榜](2026-10-31-talent-item-rankings.md)、[C Windows 连接器](2026-10-31-reactionx-windows-connector.md)。

## Global Constraints

- 手动控制只保留训练记录，不进公共排名；其余八模式按固定规则独立排名；不与网页鼠标/触屏混榜。
- 每人每项历史最佳入榜、本次成绩单独对照；匿名前三及差距始终在结果区域有真实状态，未保存不得伪造排名。
- 默认有效超管才能配对、管理和现场代测；普通管理员、教练、领队不自动获得权限。读报告仍按已有本人、有效监护与授权范围。
- 首装、唤起与配对须由用户确认；无开机自启、静默安装或静默更新；拒绝任意站点/脚本/文件路径的 URI 参数。
- Windows 11 x64，验收 Chrome/Edge；不把 macOS 单元测试当 Windows/BLE 验收，不自动开发其他平台。
- 实际 ERP 源 `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`，当前 head 为 `0078_hk_mo_permit_documents`。执行开始重新核验；已有脏改动不撤销、不混入提交。
- 必须读实际源码 `AGENTS.md`；通过适用检查后运行 `scripts/deploy-nas.sh --source "$PWD"`，失败即发布阻断。不改变 HTTPS 18080、NAS 秘密或业务数据卷。
- 本文是计划而非验收结果。不自动 push 两个仓库；签名证书、真实 Windows 和灯具不可用时如实停在相应验收关卡，不以模拟成功冒充正式完成。

## Review Focus

1. 两个标签/电脑、旧 Tk 与连接器同时抢灯：只准当前运行持有控制权，B3/C1/C5 覆盖。
2. 账号停用、退出、撤权后旧命令或补传到达：拒绝新操控，保留原始归属，不借新账号上传，B2/B4 覆盖。
3. 停止请求遇到网络挂起或休息倒计时：本地可停止，未收到熄灯确认不显示“已停止”，B3/C1/C4 覆盖。
4. 已保存但回执丢失、JSON 内容变更却复用同一键：相同包返回原结果，冲突包拒绝，B4/C3 覆盖。
5. 只有扫描/单灯成功、计时分辨率误当精度：每模式及六灯实际验证之前禁止正式参榜，B1/B6/C6 覆盖。

---

## 总顺序和并行所有权

先完成 A1 与 B1 的公共契约；A2/A3 可以与 C1/C2 并行。ERP 数据库迁移由主控串行协调：A4 后才创建 B2 revision，所有数据库测试同库串行；B2→B3→B4 后 B5 接入真实接口。C1→C3→C4，C2 为 C4 提供系统保护，C5/C6 完成 Windows 集成。最终 B6 汇总并发布。

建议最多同时三条实现流，按任务实况调整，不为了占满并发槽拆碎任务。A 流只拥有网页单项榜/共享排名类型，B 流拥有 ERP reactionx 模块与总注册点，C 流拥有 Windows 仓库。共享类型在 A1/B1 确认后冻结；接口变更通知全部消费者，不允许各自猜测。

默认在 B6 从审查通过的完整集成源统一发布。A 的提前交付只有在独立、可验证的发布源不含 B 未完改动时才允许；不能直接把共享工作树当成 A 的发布包。打包选择仍走仓库发布机制，不绕过部署脚本。

三个子计划各自可验证：A 交付可独立使用的网页单项榜；B 在隔离假连接器下交付受限的 ERP 全链路；C 在假 ERP 和假设备下交付连接器并补真机验收。B/C 的模拟验收不等于真实硬件交付。

## 共同协议与界面约定

以下是实施者在规格允许范围内选择的 v1 默认值，必须有边界测试。

- 出站 HTTPS 连接保持复用；空闲轮询 2 秒、活动轮询 500 毫秒；单请求超时 5 秒，失败指数退避至 30 秒。网络线程不得阻塞 BLE 或本地停止。明确网络失败立即停止生成新刺激；静默挂起最迟由租约截止触发停止。
- 浏览器控制租约 15 秒，每 5 秒续期，绑定原网页登录 session、账号、tab 控制 ID、connector、run；只接受当前租约。设备按单调时钟执行收到的剩余 TTL，不依赖修改后的系统时间。恢复网络不自动续跑。
- 配对挑战 256 位随机值，有效 5 分钟、一次领取；同账号每分钟最多创建 5 个。同 IP claim 每分钟 20 次，超出 429/Retry-After。两端显示配对校验短语并分别确认后生效；超期/取消的 key 不可恢复使用。
- 连接器本机生成 256 位随机 credential，经 TLS 配对登记其摘要；服务端仅存 SHA-256 digest，机端使用当前用户 DPAPI。请求使用独立 `Authorization: ReactionX <connector_id>.<secret>`，不使用 ERP cookie，也不能访问人员/财务等通用路由。确认前仅能查配对状态；每次请求检查凭据和原操作人仍 active。
- URI 仅 `reactionx-erp://pair?challenge=<opaque>` 或 `reactionx-erp://open`；站点来自安装时明确确认的 HTTPS 配置，不接受 URI 改站点。当前站点 `https://skymoonzyj.l.cd:18080`；迁移须明确确认并重新配对，不把旧队列自动发往新站点。
- `CommandEnvelope`：`protocol_version,operation_id,run_id,actor_account_id,subject_person_id,connector_id,sequence,expires_at,lease_remaining_ms,kind,payload`。未关联运行的扫描等字段可为空，但按 kind 用严格联合类型校验；禁止任意 BLE bytes/系统命令。
- `RunManifest` 冻结 `run_id,actor_account_id,subject_person_id,connector_id,rule_version,mode,config,devices,layout,manifest_digest`。devices 含本轮稳定 opaque key、显示灯号、协议版本、顺序；不能仅凭灯号区分设备。服务端不接受客户端改被测者。
- `RunEvent`：连续 `seq`、`activation_id`、`device_key`、`kind`、`host_elapsed_ms`、可空 `device_ticks_10ms`、`round/group/cycle`、`stimulus_role`、颜色与终止原因；所有数值有限且有界。时间用整数 tick/ms，避免跨语言浮点摘要不同。
- `ResultEnvelope`：manifest digest、运行 ID、事件、结束类型、活动时长、协议/引擎版本；规范 JSON 为 UTF-8、排序键、紧凑分隔、禁止 NaN/Infinity。`RunEnd` 为 `task_completed|time_completed|all_lit|user_stopped|lease_expired|disconnected|process_interrupted|device_error|storage_error` 加活动时长；只能在规则允许的结束类型形成有效成绩。服务端重算摘要与指标；最终 `ResultReceipt` 为 `run_id,result_id,digest,saved_at`。
- 每轮最大 900 秒、10,000 事件；事件分批至多 100 条/256 KiB，完整终包至多 8 MiB；超限停止并保留诊断，不截断后当完整成绩。边界检查同时在连接器、API 和反向代理请求体设置生效。
- 单连接器、单浏览器控制租约、单运行；start 默认有效期 10 秒、prepare 30 秒、stop 30 秒。序号持久化；stop 高于排队 start，取消后的 start 永远不能重放。网页“已停止”以设备终态确认而非 HTTP 202 为准。

## 八种正式试测规则 v1

这些规则用于内部训练对照，不是科学人群常模；每项保持 `unverified`，直到 C6 真机通过后仅在超管试测中标记可用。随机种子记录但不独立分榜；固定生成算法、平衡条件、设备布局和完整配置参与比较键。

| 模式代码／现有名称 | 完整任务与排序键（前者优先） | 必须新增的可核验信息 |
| --- | --- | --- |
| standard／标准训练 | 固定 30 次刺激全部解决；漏拍少优先，其次有效命中平均时间短 | 每次激活/结束、主机超时与硬件超时区分 |
| sequence／顺序模式 | 固定 5 次完整设备序列；漏拍少优先，其次平均命中时间短 | 冻结设备顺序，不能按扫描顺序 |
| simultaneous／同步模式 | 固定 5 个完整组；漏拍少优先，其次平均组完成时间短 | 组号、每灯启用确认、组结束；不宣称硬件同时触发 |
| base／基地模式 | 固定 10 个“目标→基地”完整循环；漏拍少优先，其次平均循环时间短 | target/base 角色及循环边界，不在半循环结束 |
| auditory／听声辨位 | 固定 30 次声音刺激；漏拍少优先，其次平均命中时间短 | 声音配置、布局、感应方式，独立于视觉刺激 |
| true_false／真假难辨 | 30 次解决刺激，正确目标/干扰各 15 次；正确判断多优先，其次正确命中平均时间短 | 确切目标角色、正确忽略、误拍、漏拍及受控调度 |
| counter／计数器 | 30 秒有效活动时间，累计有效触发数多优先 | 冻结灯数与重启间隔，计时排除准备/休息 |
| whack／消消乐 | 自然全亮失败或最多 60 秒；生存时间长优先，其次有效消除数多 | 自然失败/手动中止/时间达标的独立结束码 |

手动模式 `manual` 始终 `practice_only`。所有模式必须冻结灯数、传感、布局、间隔与超时；正式默认刺激间隔 500 ms、超时 3,000 ms，计数器重启间隔 80 ms；不同配置不同榜。布局记录各灯相对位置（厘米）和测试姿态，未完成布局登记不得正式入榜。完整反应类任务无有效命中时显示原因不计有效反应排名；人工提前停止不是完成，消消乐自然失败则可以是有效完成。mean 使用整数 tick/ms 的精确总和与计数比较，显示四舍五入不得反向改变名次；所有排序维度分别显示单位和差距，不用一项任意加权总分掩盖错误。

### Task 1: B1 冻结跨仓库 v1 契约及规则

**Files:** 新建 `apps/api/app/reactionx/{__init__,schemas,rule_versions,metrics}.py`、`tests/unit/test_reactionx_contracts.py`、`tests/unit/test_reactionx_metrics.py`、`shared/reactionx/v1/{schema.json,fixtures.json}`、`apps/web/src/reactionx/contracts.ts` 与测试。C 仓库对应消费 `contracts/v1`，由本任务定稿后复制，记录 SHA-256。

**Interfaces:** 产生上述 `CommandEnvelope,RunManifest,RunEvent,ResultEnvelope,ResultReceipt`；`validate_manifest(manifest: RunManifest) -> RuleDefinition`、`calculate_metrics(manifest: RunManifest, events: list[RunEvent], end: RunEnd) -> MetricResult`，后者含 `eligible,reason,values,comparison_key`，不接受客户端 rankable 决定资格。

- [ ] 写 fixture 驱动失败测试：九模式均可解析；manual 不参榜；同分值/时间字段合法，未知字段、重复设备、半个同步组/基地循环、空命中、true_false 非 15/15、人工停止冒充自然失败均不能形成有效榜成绩。断言 `manual.reason == 'practice_only'`、30 秒 counter 12 次高于 11 次、8 个正确判断不得靠更快时间超过 9 个。
- [ ] 运行 `"$PY" -m pytest tests/unit/test_reactionx_contracts.py tests/unit/test_reactionx_metrics.py -q` 和前端 contracts 定向测试，先证失败。
- [ ] 实现模式/协议/规则的严格类型、canonical digest 和服务端重算；把 fixture 的字节摘要作为跨语言固定用例。规则包含错误优先级、precision 和完整结束约束；不改既有网页评分。
- [ ] 同一 fixture 在 API/TypeScript/C2 Python 校验结果一致后冻结 v1；定向测试 0 failed，记录跨仓库变更清单。

### Task 2: B2 持久化、配对、权限与审计

**Files:** 新建 `reactionx/{models,pairing,access,dependencies,router,client_router,http_support}.py` 和 `tests/api/test_reactionx_pairing.py`；修改 `api/router.py`、`main.py`、`rbac/{seed,service}.py`、`settings/PermissionsPage.tsx`、`migrations/env.py`、schema manifest 与迁移测试。A4 后新增单一 `reactionx_foundation` revision，具体数字按当时 head 顺接。

**Interfaces:** `ReactionxAccess(db,actor).require_manage()/require_conduct(person_id)`；`PairingService.create(actor,request_id)->PairingView`、`claim(challenge,credential_digest,client_info)->ClaimView`、`confirm_browser(...)`、`confirm_device(...)`、`revoke(connector_id,actor)`。网页登录路由在 `/api/v1/reactionx`，机器路由在 `/api/v1/reactionx-client`，后者独立机器认证，不扩大通用 CSRF 豁免。

- [ ] 写失败测试：未登录、普通管理员、已被撤权超管、错误当前账号、过期/重复 claim、撞配对、凭据错误和其他站点均拒绝；浏览器变更仍要 CSRF。未激活档案可被有效超管选中，但设备不能读取人员列表。未确认两端前没有可用凭据；429 有 Retry-After。
- [ ] 在安全隔离库运行 `"$PY" -m pytest tests/api/test_reactionx_pairing.py -q`，确认不是 skip。
- [ ] 建 `ReactionxConnector/Pairing/Command/Run/Event/ResultReceipt` 表，actor 与 subject 分开；事件 `(run_id,seq)`、receipt run ID、命令 operation ID 唯一。新增 `reactionx.manage` 和 `reactionx.conduct`，默认只授超管并接入授权管理的作用域校验、拒绝优先和中文名称；试测开关下仍强制有效超管。只给有现场代测权限的网页登录请求提供最小人员选择响应（ID、姓名、昵称），连接器无此接口权限。报告读取复用 `TalentAccess` 的人员范围，不授予账号管理等权限。敏感操作记审计且日志排除 secret/challenge/事件内容。
- [ ] 重跑 pairing、现有权限/撤权回归和迁移升级/降级测试；确认原业务表和历史数据不被改写，默认普通管理员未获新权限。

### Task 3: B3 运行与命令租约状态机

**Files:** 新建 `reactionx/{commands,runs,queries}.py`、`tests/services/test_reactionx_commands.py`、`tests/api/test_reactionx_runs.py`；扩展 B2 路由。

**Interfaces:** `RunService.prepare(actor,person_id,connector_id,config,operation_id)->RunView`、`command(actor,run_id,kind,operation_id)->CommandView`、`renew_lease(actor,run_id,tab_id)->LeaseView`；`ClientService.poll(connector,cursor)->PollView`、`ack(connector,operation_id,ack)->CommandView`。网页 GET run、POST prepare/start/stop/lease；机器 poll/ack 只能触及所属运行。

- [ ] 写失败测试：两个独立数据库连接并发 start 恰好一个运行成功；同 operation 同参数重试返回相同结果，不同参数 409；第二标签不能接管；stop 后迟到 start 不执行。原网页登录 session 注销、actor disable/revoke、lease 超时均阻止下一命令；反复 HTTP 202 不得令状态变成已停止。
- [ ] 运行 `"$PY" -m pytest tests/services/test_reactionx_commands.py tests/api/test_reactionx_runs.py -q` 先失败。
- [ ] 用事务/行锁和唯一活跃运行约束实现准备→就绪→运行→停止中→待同步/失败状态；每次 poll/ack/lease 重新查活跃 actor/session、权限和归属。stop 命令优先，接收设备失败/失联时状态保留未确认，不用 web 定时器直接改已停止。
- [ ] 定向测试通过，并断言系统时间倒退不延长设备租约；假连接器按合同返回 ACK 验证断线/重连只恢复状态、不自动亮灯。

### Task 4: B4 结果幂等入库、恢复与实体排名

**Files:** 新建 `reactionx/{results,ranking,recovery}.py`、`tests/api/test_reactionx_results.py`、`tests/services/test_reactionx_ranking.py`，扩展 router/client_router。

**Interfaces:** `ResultService.submit(connector,envelope)->ResultReceipt`、`ResultService.list_visible(actor,person_id: UUID|None,cursor: str|None,limit: int=20)->ResultPage`、`ReactionxRankingService(db,access).for_result(result_id)->ItemRankingView`（A1）；`RecoveryService.authorize_upload(actor,run_id)->UploadPermit`、`RecoveryService.authorize(actor,run_id,reason,confirmation)->RecoveryPermit`。结果 POST/GET receipt、授权 GET report/ranking/list；列表 cursor 绑定当前身份与筛选且不泄漏不可见总数。超管恢复只签发绑定原 run/actor/subject/digest 的一次性提交许可，不重写原操作者。

- [ ] 写失败测试：提交后丢响应、再传同包仅一个结果和一条入库审计；变更 digest/subject/seq/manifest、缺事件、未知灯、超体积或把人工中止改正常均拒绝。原账号撤权的队列不准用新账号代传；恢复许可有理由和二次确认，仅允许原包，仍保留原归属。
- [ ] 运行 `"$PY" -m pytest tests/api/test_reactionx_results.py tests/services/test_reactionx_ranking.py -q`，先证失败。
- [ ] 事务写入不可变事件/指标/receipt，服务器按 B1 重算资格；组键含规则与完整可比条件。正常补传窗口 30 天（以服务端 run 创建时间为准），逾期须显式超管恢复，不能根据客户端时间延长。原网页登录 session 退出后暂停旧包自动补传；原账号重新登录且仍有权限时只能为原包签发上传许可，不续跑测试。停用/撤权时须超管专门核验，不能用新账号令牌冒充原操作者。读取保留权限检查，匿名前三复用 A1/A2 语义，不复用网页评分锚点。原始训练与已验证正式测试分组，manual 永远 practice_only。
- [ ] 重跑结果、排行、权限回归；加入 10,000 条隔离历史查询验证，SQL 不向应用返回全部候选；相同人不同操作者只能一席，排行榜与数据库 ACK 一致。

### Task 5: B5 ERP 操作页、状态和安装引导

**Files:** 新建 `apps/web/src/reactionx/{api.ts,ReactionxPage.tsx,ReactionxReportPage.tsx,ReactionxReportsPanel.tsx,ConnectionPanel.tsx,RunPanel.tsx,reactionx.css}` 及对应 tests；修改 `app/router.tsx`、`talentTests/TalentTestsPage.tsx`、`layout/AppShell.tsx`、`people/PersonDetailPage.tsx` 及其懒加载回归。当前 `esportsAssessments/AssessmentReportsPanel.tsx` 专供天赋评测 2，不挪用为实体灯面板、不改变其请求和权限。

**Interfaces:** `/talent-tests/reactionx`、`/talent-tests/reactionx/reports/:resultId`；API 消费 B2/B3/B4，成绩面板复用 A3。安装下载从 `GET /api/v1/reactionx/releases/current` 得固定 version/digest/signature_status，授权下载受控文件，不把 GitHub 凭据交给客户端。

- [ ] 写失败测试：未检测到不等于未安装；配对两端确认/超时；Windows 非兼容版本只读；普通角色没有操控按钮且直接调用也受后端拒绝。保存中、待同步、已保存、失败、停止未确认分别可辨；manual 显示“不参与排名”；离线不显示假的成功或第 0 名。
- [ ] 运行 `npm --prefix apps/web test -- src/reactionx`，先确认新增用例失败。
- [ ] 实现懒加载页面和显式点击唤起/下载；控制绑定当前账号与 tab，首次取得控制须经当前 tab 的本地唤起挑战确认，不能将账号名下另一台在线电脑显示为本机连接器。租约不能被别的标签无声抢占。登出尽力发 stop/revoke 后清空页面，安全边界仍由服务端 session 和 TTL 保证；回前台读取运行状态不自动 start。固定规则测试与自由训练清楚区分；布局和设备顺序在准备时确认。档案中的 ReactionxReportsPanel 独立懒加载并有失败重试边界，不能再次导致整个档案白屏。报告只读沿用现有权限，不通过 UI 顺带扩大人员列表权限。
- [ ] 定向测试、build/lint 通过，A/B 主题及窄屏查看核验；安装包未建立验证清单时显示“安装包尚未就绪”，不得假链接到未核验 GitHub EXE。

### Task 6: B6 全链路验收与受限发布

**Files:** 新建 `apps/web/tests/reactionx-integration.spec.ts`、`apps/api/app/reactionx/releases.py`、`apps/api/tests/cli/test_reactionx_release.py`、`docs/operations/reactionx-acceptance.md`；修改 reactionx router、核心 E2E 运行器和编排测试、`scripts/deploy-nas.sh` 与部署配置。包目录为只读受控运行时挂载，不改数据库卷，不把 EXE 提交进源码 Git。

**Interfaces:** 汇合 A、B、C；产出按模式的验证状态、固定包 SHA-256 清单和真实验收记录。发布只开放超管试测，不额外授权普通岗位。

- [ ] 写隔离 fake-connector E2E：配对→选人→prepare→start→stop/complete→ACK→报告/前三；切账号、无账号档案、重复终包、断线和 revoke。注册到新的 `reactionx` E2E 模式，并确保 all 只覆盖一次；测试运行器失败与产品失败分别报告。
- [ ] 实现 `ReleaseService.current()->ReleaseView` 与 `open_verified(release_id)->VerifiedFile`，接入 B5 的 current/download 路由。manifest 只含允许的固定文件名、版本、digest、实际签名状态；下载逐次验证授权，路径不可用户拼接、拒绝 symlink/越界和 hash 不符。部署脚本增加经本地校验的固定 release bundle 输入，通过原流程传送并校验，不用额外 scp 绕过脚本；缺包时明确 unavailable。写 release 用例验证错误签名声明、篡改包、错误摘要、路径穿越均失败。
- [ ] 运行相关完整后端回归、Ruff/Mypy、前端全测试/build/lint、隔离 `E2E_CORE_MODE=reactionx scripts/run-core-e2e.sh` 及既有 talent/esports 回归。任何与本改动相关的失败阻断发布；skip 的真机项单列，不能把它算通过。
- [ ] 按 C6 在 Windows 11/Chrome/Edge 和真实六灯验收九模式、八榜条件、迟到通知隔离、退出/休息中断、DPAPI、安装升级、停止失败提示。某模式无法证明通知归属则保留该模式关闭；未验收的模式只显示准确状态，禁止正式参榜。包摘要和签名状态必须实际读取，不根据构建命令推断。
- [ ] 主控审查集成差异后运行 `scripts/deploy-nas.sh --source "$PWD"`，收集备份、迁移、活跃版本与健康检查结果。失败停止、不绕开；源当前 detached/dirty 时不自动提交或推送。线上仅作只读核验，不生成虚假学员成绩，不扩大权限。
- [ ] 报告分别列出源码实现、模拟测试、Windows/六灯验收、安装包签名、NAS 发布、正式开放六项状态。缺用户硬件或签名材料时列出具体剩余动作，不能用“开发完成”覆盖未验收部分。

## 命令与文档自检

后端统一按 A 计划的 `$PY`/隔离数据库约定从 `apps/api` 运行；新增迁移及 E2E 名称要同步 manifest/编排测试。协议 fixture 与三个计划的类型名一致；A 的面板/响应先冻结后给 B 使用，B1 的 manifest/event/receipt 先冻结后给 C 使用。执行者必须保留每个失败→通过证据，不能只打勾。

## 当前状态与下一步

三份计划已形成，尚未实施。执行方式沿用用户此前授权的按实际任务规模并行：独立任务分工，数据库/迁移/发布主控串行。用户确认计划后进入实现；先做 A1、B1，再按依赖推进，不从下载执行未知 EXE 开始。
