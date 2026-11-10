# 工作台退宿／结营提醒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在工作台提供按现有权限过滤、按人员合并的今日／近三日退宿及结营提醒，不改动业务状态。

**Architecture:** 两类业务事实各自授权后组成统一只读查询，在数据库按人员汇总、排序、分页。独立提醒接口返回能力、服务端业务日期及最小摘要，不改动现有门户上下文契约。前端沿用门户复核权限的生命周期，单独隔离提醒数据缓存，再接入工作台的普通与后台兜底分支。

**Tech Stack:** 现有 Python 3.12、FastAPI、SQLAlchemy、PostgreSQL、Pydantic、React、TanStack Query、Zod、Vitest、Playwright；不新增运行时依赖。

**Spec:** `docs/superpowers/specs/2026-10-23-workbench-departure-reminders-design.md`（用户 2026-10-24 确认）。

## Global Constraints

- “近三日”包括今天、明天、后天；服务端 `Asia/Shanghai` 为准，默认 `three_days`，可切换 `today`。
- 营期结束取报名有效时间，沿用排他上界显示规则；计划退宿日期不减一天。
- 住宿仅有效 `checked_in`／`checkout_pending`；教学仅非取消的 `active`／`completed` 报名及未取消营期。
- 同一人员 UUID 只计一次；每个来源独立鉴权，未授权事实不得影响内容、计数或排序。
- 保留所有现有角色门槛、关系有效期、显式拒绝及 owner-only 限制；不修改 RBAC 默认授权。
- 不自动退宿、退营、释放床位或修改保洁；不导出、不推送、不显示证件、联系方式、财务或室友资料。
- 每页默认 20 人，上限 50 人；无能力隐藏，有能力零命中显示空态，失败不能冒充空态。
- 只在专属、经所有权核验的本机临时数据库运行破坏性测试／虚拟数据种子；同一库仅一个测试进程。
- 保留已有 dirty/untracked 文件，不 reset、清理或整体 stage；本计划不授权提交混合工作树。每任务保留 scoped diff 和验证记录，最终是否提交另行处理。
- ERP 代码通过本地检查后必须执行标准 `scripts/deploy-nas.sh`；失败是发布阻断，不得旁路复制或改生产数据。

## Review Focus

- 旧非零点教学结束时间与新排他零点记录并存：两者必须与现有页面同日；任务 1 增加一致性测试。
- 同一个人同时有多个授权关系，其中一个被拒绝／撤销：不能通过另一事实泄露数据；任务 2 增加分路径对照测试。
- 查询计数与列表之间发生退房或撤权：响应应来自同一只读快照，下一请求反映变更；任务 3 增加独立连接并发测试。
- 浏览器设备时钟不准、页面休眠后跨日：使用服务端时间间隔安排刷新，不依赖客户端日历；任务 4 增加虚拟时钟测试。
- 同一人多条有效在住记录或同名人员：不得静默选择一个错误房号；任务 3 验证去重与既有数据一致性错误，任务 5 验证失败提示。

---

## 0. 执行环境与协作边界

当前计划及已发布房态能力所在工作树：`/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`，下文路径均相对此目录。

主工作区工具路径：`/Users/skymoonzyj/Documents/new project3/.venv/bin/`。执行前重新核实 Git worktree、HEAD、工作树改动及工具路径；不得把其他工作树的实现覆盖进来。该树已有隔离身份，但含先前交付的未提交变更，应记录文件级基线。

建议沿用子任务协作，任务 1 契约完成后，任务 2–3 后端链和任务 4 前端数据层可并行；任务 5 在二者之后集成，任务 6 验收及发布串行。通常 2–3 个实现流足够，不为用满并发而并行改同一文件。数据库测试由主代理排队调度。

每个任务均按红灯测试 → 最小实现 → 绿灯测试 → 新审查者复核完成。任务文件归对应实现者独占；跨任务接口变化先通知相邻实现者。最后增加一次全功能复核，不以单任务通过代替整体验收。

### 文件责任图

- `portal/departure_dates.py`、`departure_schemas.py`：业务日期及公共响应契约（任务 1）。
- `portal/departure_access.py` 与必要的小公共授权适配器：复用现有权限，输出可查询范围（任务 2）。
- `portal/departure_query.py`、`departure_service.py`、`departure_router.py`：只读事实查询、合并与 HTTP（任务 3）。
- `portal/departureApi.ts`、`useDepartureReminders.ts`：解析、缓存、跨日及复核生命周期（任务 4）。
- `portal/DepartureRemindersPanel.tsx`、`PortalDashboardPage.tsx`：页面展示与入口集成（任务 5）。
- 任务专属测试种子／安全运行器、浏览器用例、验证报告：任务 2 创建最小数据库运行器，任务 6 补浏览器验收。

## Task 1: 锁定日期语义及前后端契约

**Files:**
- Create: `apps/api/app/portal/departure_dates.py`
- Create: `apps/api/app/portal/departure_schemas.py`
- Create: `apps/api/tests/unit/portal/test_departure_dates.py`
- Create: `apps/api/tests/unit/portal/test_departure_schemas.py`
- Test/update: `apps/web/src/shared/date.test.ts`

**Interfaces:**
- `DepartureWindow = Literal["today", "three_days"]`。
- `housing_departure_date(value: datetime) -> date`；`enrollment_departure_date(value: datetime) -> date`：均拒绝无时区时间，后者与 `exclusiveEndToDateInput` 一致。
- `departure_window_dates(now: datetime, window: DepartureWindow) -> tuple[date, date]`：含首尾日期；`next_business_midnight(now: datetime) -> datetime`。
- `DepartureEvent` 为按 `kind` 区分的 Pydantic union：公共字段 `source_id: UUID, date: date, detail_path: str | None`；housing 分支 `kind="planned_checkout", building_name, room_number`；teaching 分支 `kind="enrollment_end", project_name, cohort_name, enrollment_status: Literal["active","completed"]`。只允许本计划规定的站内详情路径，默认无链接。
- `DeparturePerson`：`person_id: UUID, full_name: str, nickname: str | None, events: list[DepartureEvent]`。
- `DepartureRemindersResponse`：`available: bool, observed_at: datetime, business_date: date, timezone: Literal["Asia/Shanghai"], next_refresh_at: datetime, window: DepartureWindow, today_count: int, three_day_count: int, page: int, page_size: int, total: int, items: list[DeparturePerson]`。
- 数量均为授权后不同人员数量；两种 count 总是各自窗口的总人数，`total` 为当前窗口人数。无能力时数量及 items 全空，依然返回业务日期和合法分页。

- [x] **1.1 写失败测试。** `test_midnight_end_is_previous_day_but_housing_is_not` 断言 `2026-10-24T00:00:00+08:00` 的教学日期为 `2026-10-23`、住房日期为 `2026-10-24`；`test_legacy_non_midnight_end_keeps_display_date` 断言当天 18:00 不减日。参数覆盖 UTC 等价值、月末、年末、闰日、秒与毫秒，严格与前端现有函数对照。
- [x] **1.2 验证红灯。** API 目录运行 `$PY -m pytest tests/unit/portal/test_departure_dates.py tests/unit/portal/test_departure_schemas.py`，应因新模块／类型缺失失败，而非数据库或环境错误。
- [x] **1.3 实现日期与 DTO。** 用现有中国时区工具，拒绝 naive datetime；日期窗口固定 D..D+2。schema 拒绝负数、未知 kind、跨来源混入字段及非法外部 detail_path；`page_size` 为 1..50。正常返回日期字符串，不暴露 ORM。
- [x] **1.4 验证绿灯及前端一致性。** 上述 pytest 通过；Web 目录 `npm test -- src/shared/date.test.ts --maxWorkers=2` 通过。保留同一组边界期望到验证记录，不修改现有显示行为。
- [x] **1.5 范围复核。** 仅检查本任务新增文件和 date 测试新增段，确认公共字段名称供任务 3、4 使用；不自动提交混合工作树。

## Task 2: 建立安全验证入口及可复用的授权范围

**Files:**
- Create: `apps/api/app/portal/departure_access.py`
- Modify: `apps/api/app/personnel_intake/queries.py`
- Modify as needed: `apps/api/app/housing/portal_service.py`, `apps/api/app/portal/learning_service.py`
- Create: `apps/api/tests/services/test_departure_access.py`
- Test/update: `apps/api/tests/services/test_managed_people_housing.py`, `test_housing_portal.py`, `test_portal_learning.py`
- Modify: `scripts/housing-care-owned-gate.py`（只抽取配置参数，旧默认行为保持不变）
- Create: `scripts/run-departure-reminders-checks.sh`, `apps/api/app/cli/departure_e2e_runtime.py`, `apps/api/tests/cli/test_departure_owned_gate.py`
- Test: `apps/api/tests/cli/test_housing_care_owned_gate.py`

**Interfaces:**
- `ManagedPeopleQuery.authorized_person_ids_statement(self, state: IntakeAccessSnapshot, as_of: datetime) -> Select[tuple[UUID]]`：提取现有 affiliations、未删除、合法学员／队员过滤，原 list 与提醒共同使用。
- `DepartureAccess(db: Session, actor: AuthContext, now: datetime)`。
- `DepartureAccess.has_scope() -> bool`：依据当前合法权限／关系范围，不依赖三日内是否有事实；有范围但无命中仍为 true。
- `DepartureAccess.housing_criteria() -> ColumnElement[bool]`：作用于查询中统一的 `HousingStay`／`HousingRoom` 实体。
- `DepartureAccess.enrollment_criteria() -> ColumnElement[bool]`：作用于统一 `Enrollment`／`TeachingClass`／`Person` 实体；分页前用于 SQL 过滤。
- `DepartureAccess.detail_paths(housing_rows: Sequence[DepartureHousingRow], enrollment_rows: Sequence[DepartureEnrollmentRow]) -> dict[tuple[str, UUID], str]`：只对当前页批量复核完整详情权；key为 `(kind, source_id)`，没有该权省略 key。两个 frozen dataclass 于本模块定义：`DepartureHousingRow(stay_id: UUID, person_id: UUID, room_id: UUID)`；`DepartureEnrollmentRow(enrollment_id: UUID, person_id: UUID, class_id: UUID)`，不载入敏感字段。
- 安全运行器新增明确 `--profile departure-reminders --phase api|browser|all` 参数；旧 housing-care 为默认 profile 且旧命令原样可用。`GateProfile` 配置固定在代码中，不能传任意 shell 命令／数据库 URL。departure profile 固定库名 `erp_departure_reminders_test`、任务标记 `departure-reminders-20261024`、独立 runtime ledger。
- `require_departure_database(settings: Settings) -> None`：在任何 schema reset／seed 前核验本次控制器生成的私有 ledger 与 DSN、容器／卷身份；只允许本机临时映射，拒绝 55433、生产／外部 DSN。

- [x] **2.1 先写安全入口红灯测试。** 未知 profile、外部 Docker endpoint、外来 DSN、伪造／错配 ledger、cleanup 所有权改变均拒绝；旧 profile 仍发出原 launcher 命令。API 目录运行 `$PY -m pytest tests/cli/test_departure_owned_gate.py tests/cli/test_housing_care_owned_gate.py`，确认新增用例确因缺接口失败。
- [x] **2.2 实现最小运行器。** 复用已有本机 Unix Docker、不可变镜像、随机 owner、精确 ID／标签／卷核验、受限临时目录及进程组清理；配置参数替换硬编码，不重写这些安全检查。API phase 维护显式 pytest 文件列表，本任务先纳入任务1/2用例，后续任务完成时增加，不按文件是否存在自动跳过；browser phase 留到任务 6 接通前明确拒绝，不误报通过。绿灯后才能创建本任务数据库；禁止运行旧的共享 `scripts/check.sh` 或沿用其他项目库。
- [x] **2.3 写授权红灯测试。** `test_room_scope_matches_housing_access`（global/building/floor/owner-only）；`test_managed_people_matches_existing_query`（当前责任人、队伍任职与成员、同人 team deny）；`test_invited_scope_all_sources_and_revocation`；`test_self_family_keeps_current_guards`；`test_teaching_paths_do_not_grant_housing`；`test_housing_paths_do_not_grant_teaching`。均与现有授权服务的同一虚拟场景作差分比较，不只用超管。
- [x] **2.4 验证红灯。** 从工作树根运行 `$PY scripts/housing-care-owned-gate.py --python "$PY" --profile departure-reminders --phase api`。运行器先核验、创建专属库，串行运行指定用例，失败原因应为新增授权功能缺失；结束必须清理本次 owned 资源。
- [x] **2.5 实现范围组合。** 住房管理直接使用 `HousingAccessService.room_permission_criteria("housing.read")`；managed 使用上面公共 statement；invited 使用现成 `authorized_people_statement(source_kind="all", organization_id=None, ...)`；self/family 提取现有 subjects 授权部分，不调用带室友的 `get_housing()`。
- [x] **2.6 实现教学范围。** 通用读取保留 `teaching_visibility("enrollments.read")` 的 excluded_class_ids；教练及学习门户保留各自角色与组合权限门槛。复用 `RbacDecisionContext.decide`、批量 person/class tokens 的判定语义，将有限的当前范围／override 结果组织成 SQL 条件或子查询；不得循环调用单报名权限接口、先分页后过滤、重新解释 deny 优先级，或套用只支持未来营期／active 报名的现有候选过滤。公共小适配器必须让旧路径一起复用，并以差分测试证明语义不变。
- [x] **2.7 绿灯与审核。** 重跑 owned API phase，涵盖新测试及 managed、invited access、housing portal、portal learning/coach、housing access 原回归。比较 1 与 100 个候选的 SQL 数量，不能出现每人一次授权查询。独立审查先验权限，再交任务 3；复杂 SQL scope 无法证明等价时停止集成并报告，不改成放宽授权。

## Task 3: 两类事实合并、计数、分页与只读 API

**Files:**
- Create: `apps/api/app/portal/departure_query.py`, `departure_service.py`, `departure_router.py`
- Modify: `apps/api/app/portal/dependencies.py`, `apps/api/app/api/router.py`
- Create: `apps/api/tests/services/test_departure_reminders.py`, `apps/api/tests/api/test_departure_reminders.py`, `apps/api/tests/db/test_departure_read_consistency.py`
- Create: `apps/api/tests/departure_helpers.py`（只在测试使用的虚拟场景）

**Interfaces:**
- `PortalDepartureService(db: Session).list_reminders(*, actor: AuthContext, now: datetime, window: DepartureWindow, page: int, page_size: int) -> DepartureRemindersResponse`。
- `DepartureQuery(db: Session, access: DepartureAccess).read_page(*, now: datetime, window: DepartureWindow, page: int, page_size: int) -> DepartureRemindersResponse`。
- `get_portal_departure_service(db: Annotated[Session, Depends(get_db)]) -> PortalDepartureService`。
- `GET /api/v1/portal/departure-reminders?window=three_days&page=1&page_size=20`；使用 `require_standard_account`，服务端统一生成一个 now。无范围返回 200 available=false 空名单；未登录／受限账号沿用认证错误；参数不合法 422；真实查询失败沿用脱敏错误，不返回部分结果。路径已有 `PrivatePortalResponseMiddleware` 的 no-store/private 覆盖。

- [x] **3.1 写合并与日期红灯测试。** `test_merges_by_person_not_name` 断言同人两来源 total=1、同名异人 total=2；`test_independent_end_overrides_cohort`；`test_completed_custom_end_is_not_lost`；`test_long_term_and_nonresident_enrollment`；`test_unactivated_confirmed_person_is_included`；`test_voided_checked_out_planned_and_cancelled_are_excluded`。用任务 1 日期断言核对 SQL 转换一致性。
- [x] **3.2 写计数、隐私与快照红灯测试。** 45 人且跨来源重叠，page_size=20 应为 20/20/5，不因分页来源缺人；隐藏来源不得改变排序与 count；仅返回选定窗口 events。`test_duplicate_effective_current_stays_fails_closed` 沿用现有一致性错误，不能任选房间。独立连接在读取过程中退宿／撤权，结果不得混用前后快照；下一次请求必须看到变化。API 测试递归断言无敏感键，并比较人员、入住、报名、床位、保洁调用前后快照相同。
- [x] **3.3 owned API phase 跑红灯。** 把上述文件纳入允许测试列表，失败需能定位到缺失的新查询／路由，不接受 skipped DB tests 作为验证。
- [x] **3.4 实现 SQL 投影。** 使用任务 2 criteria 与 `effective_stay_clause()`；教学按 effective schedule、active/completed 与未取消营期处理。日期候选先收敛到 D..D+2，映射到统一的 person/source/date 事实 CTE；按 person_id 分组后排序分页，再读取当前页 events。两种 counts 为授权集合的 distinct 人数，均不能使用事后 Python 过滤；不载入完整历史、照片或联系方式。
- [x] **3.5 保证一致快照及详情。** counts、page、events 由同一 SQL 语句／快照获取；权限判断依赖多条查询时，使用专用只读 REPEATABLE READ 会话包裹本次授权和投影，不在已有事务内切隔离级别。HTTP 路径在此快照中重新核对账号当前有效性；批量详情权不比列表授权宽。完整房间权链接 `/housing/rooms/{room_id}`；完整报名详情权链接 `/teaching/enrollments/{enrollment_id}`；其余默认 null，不给摘要用户伪造跳转。
- [x] **3.6 接入 HTTP 及绿灯。** 独立 router 挂入总路由，保持现有 context DTO 不变。owned API phase 跑完所有新测试，加 `tests/api/test_portal.py`、`test_permissions.py` 及相关住房、报名读取回归；确认默认分页20、上限50、私有成功与错误头、不泄露未知对象。
- [x] **3.7 性能及独立审核。** 在 owned 库记录 1/100/1000 条三日候选的 SQL 次数及 EXPLAIN；不出现按人查询增长或 Python 全量历史加载。确需索引时新增独立迁移及 upgrade/downgrade 保留数据测试，并更新 schema manifest；不改已有迁移。任务 reviewer 重点检查 SQL 日期与授权闭包。

## Task 4: 前端契约解析、授权缓存与跨日刷新

**Files:**
- Create: `apps/web/src/portal/departureApi.ts`, `departureApi.test.ts`
- Create: `apps/web/src/portal/useDepartureReminders.ts`, `useDepartureReminders.test.tsx`
- Test/update: `apps/web/src/auth/AuthProvider.test.tsx`

**Interfaces:**
- `getDepartureReminders(input: { window: "today" | "three_days"; page: number; pageSize?: number }, signal?: AbortSignal): Promise<DepartureReminders>`：与任务 1 字段逐一匹配、Zod 解析、脱敏错误。
- `departureKeys.root(accountId: string)` 返回 `["portal", accountId, "departures"]`；`page(accountId, contextEpoch, window, page, pageSize)` 后续追加相应字段，保留在已有 portal 退出清理范围内。
- `useDepartureReminders(input: { accountId: string | null; contextReady: boolean; contextEpoch: number })` 返回 `{ query, window, setWindow, page, setPage, retry }`；contextEpoch 使用既有 `contextQuery.dataUpdatedAt`，仅代表本地复核代次，不声称服务器权限版本。

- [x] **4.1 写失败测试并运行。** Web 目录 `npm test -- src/portal/departureApi.test.ts src/portal/useDepartureReminders.test.tsx --maxWorkers=2`。断言 URL 默认three_days/20、AbortSignal传入、错误JSON不保留敏感片段、错误union/非法日期/外链响应拒绝。
- [x] **4.2 实现 API 与 hook。** context未完成时禁用并隐藏旧数据；每次context复核成功使用新epoch查询，旧请求取消，旧epoch缓存删除。`staleTime=0/gcTime=0`，不使用 keepPreviousData，不独立抢在门户context前做focus刷新。退出登录清理同一portal前缀，账号或权限代次变化重置 page=1，普通focus保留窗口选择。
- [x] **4.3 实现服务端跨日刷新。** 用 `next_refresh_at - observed_at` 计算延迟，减去请求耗时，再安排一次性定时器；不使用设备本地日期决定名单。定时到期／页面恢复可见时先触发门户context复核，再取提醒。错误重试有界，无零延时循环，后台标签休眠醒来重新确认。分页 total 减少导致空越界页时仅回第一页一次，不无限重试。
- [x] **4.4 扩展并通过生命周期测试。** `test_old_account_response_cannot_repopulate`、`test_revoked_context_hides_and_cancels_previous_data`、`test_focus_keeps_window_and_reauthorizes_first`、`test_server_midnight_with_skewed_device_clock`、`test_hidden_tab_resume_after_midnight`、`test_out_of_range_page_resets_once`。同时跑 AuthProvider 退出／撤权缓存回归，确保不保留身份证等字段于错误对象。
- [x] **4.5 审核契约。** 对照任务 1 DTO 与任务 3真实JSON，避免假数据字段漂移；本任务可在后端完成前并行，但最终必须用真实 API 再验收。

## Task 5: 工作台提醒卡片及入口集成

**Files:**
- Create: `apps/web/src/portal/DepartureRemindersPanel.tsx`, `DepartureRemindersPanel.test.tsx`, `departureReminders.css`
- Modify: `apps/web/src/portal/PortalDashboardPage.tsx`, `PortalDashboardPage.test.tsx`
- Test/update: `apps/web/src/app/router.test.tsx`

**Interfaces:**
- `DepartureRemindersPanel({ state }: { state: ReturnType<typeof useDepartureReminders> })`：展示层，不再自行请求context。
- PortalDashboardPage统一调用hook，传现有account/contextReady/dataUpdatedAt；普通门户、纯后台fallback均渲染同一组件恰好一次。提醒 available 纳入工作台能力判断，仅有 managed／invited 等范围也不误显示“当前暂无可用工作项”。不修改 AppShell 的其他菜单及授权。

- [x] **5.1 写展示红灯测试并运行。** `npm test -- src/portal/DepartureRemindersPanel.test.tsx src/portal/PortalDashboardPage.test.tsx --maxWorkers=2`。断言标题“退宿／结营提醒”、说明“依据计划日期提示，不代表已实际离开”、今日／近三日去重数量；两来源分开日期、同名独立行、昵称可选、已完成标“教学已完成”。
- [x] **5.2 实现最小展示。** 无能力隐藏，授权无命中显示“所选日期内暂无退宿或结营提醒”；错误显示“退宿／结营提醒暂时无法更新”及重试，不降级为0人。请求／权限复核时隐藏旧摘要。仅有detail_path时显示站内查看入口，不出现入住、退房、编辑或导出按钮。
- [x] **5.3 入口及响应式集成。** 保留原coach/learning卡片和后台概览；新查询失败不冒充旧卡片错误。模板中只挂一次panel，使用稳定person_id、kind/source_id键。360px换行显示，不横向滚动，按钮支持键盘和可见焦点、加载／错误可读。
- [x] **5.4 绿灯及页面分支回归。** 普通门户＋后台混合角色、纯后台、仅关联住宿、零权限四类均测试；查找模块数量必须0或1符合授权。跑上述测试、router测试，检查切页返回、焦点恢复不会重置“今日／近三日”。
- [x] **5.5 独立审核。** 检查没有绕过后台scope的前端角色硬编码，不把模块更新变成整个页面重载；交任务6做真实API与浏览器连通。

## Task 6: 真实链路验收、整体复核与 NAS 发布

**Files:**
- Create: `apps/api/app/cli/seed_departure_reminders_e2e.py`, `apps/api/tests/cli/test_seed_departure_reminders_e2e.py`
- Create: `apps/web/tests/departure-reminders.spec.ts`
- Complete: `scripts/run-departure-reminders-checks.sh` browser/all phase
- Create: `docs/superpowers/verification/2026-10-24-workbench-departure-reminders.md`

**Interfaces:**
- `seed_departure_reminders(db: Session, settings: Settings, now: datetime) -> None`：只向经过任务2所有权验证的本机临时库种入虚构场景，不接受生产目标或外部名单。
- browser phase 在该专属库启动API/Vite，绑定空闲本机端口；记录进程所有权与服务身份，只结束自己启动的进程，禁止根据端口杀进程。全部认证／下载／截图使用虚构账号，临时目录0700，日志不输出凭据。

- [x] **6.1 先验证seed安全红灯，再实现。** 未设置／错配ledger必须在reset或SessionLocal前拒绝；虚拟日期相对服务端中国日期生成，禁止靠改NAS时间测试边界。覆盖超管、行政范围、教练、队伍管理、邀请、监护、本人及无范围账号；构造未激活在住、常住结营、独立日期已完成报名、同名、多来源和超过一页场景。
- [x] **6.2 浏览器红灯至绿灯。** owned runner browser phase串行运行 `departure-reminders.spec.ts` 的 desktop-1440、tablet-768、mobile-360。至少包含：登录工作台真实名单、两种日期筛选、翻页、账号切换、取消授权后focus不显示旧名单、无修改按钮、只摘要用户无详情、无横向溢出。日期/并发边界以数据库用例为准，不用截图代替断言；完成后验证 owned 资源全部移除。
- [x] **6.3 执行组合质量门。** `$PY scripts/housing-care-owned-gate.py --python "$PY" --profile departure-reminders --phase all` 零退出，DB用例不得因缺连接被skip。Web目录运行 `npm test -- --maxWorkers=2`、`npm run lint`、`npm run build`；API目录运行 `$RUFF check app tests` 和 `$MYPY app`。如有既存失败，记录基线并确认与本任务无关，不能宣称全量通过；阻断当前功能的失败先修复。最终完整后端 9,031 通过、8 项既有 opt-in 演练跳过、0 失败；真实浏览器 12/12，所有自有临时资源清理完成。
- [x] **6.4 独立全功能代码复核。** 对照规格逐项检查日期、scope闭包、只读事务、response schema和缓存撤权；核对所有改动diff归属，确认未改其他任务的行为。修复发现后重跑对应验证，不用之前的成功记录替代最新检查。整体复核的两项测试入口问题已由单一修补轮处理并通过原审查者的针对性复审；完整质量门仍按 6.3 独立执行。
- [x] **6.5 标准 NAS 发布。** 发布前重新核对当前source、标准脚本与其guard配对、线上active release和Alembic head；只读核查，不输出secret。由本工作树运行：

  ```bash
  cd '/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3'
  PATH='/Users/skymoonzyj/Documents/new project3/.venv/bin:'"$PATH" \
    bash scripts/deploy-nas.sh --source "$PWD"
  ```

  此工作树脚本是此前发布配对入口；若已漂移／预检不兼容，报告并修正标准流程，不能改用未经核验的另一份launcher或直接复制文件。备份或部署失败立即标为未发布；不改域名、18080端口、证书或真实业务记录。

- [x] **6.6 线上只读核验及交付。** 核验脚本零退出、active release、deployment-state完成、容器健康、前端资源与API版本一致；只读访问公开健康页和已有超管会话的工作台（无会话则请用户验收，不能索取密码）。不在线上造测试住客或教学记录。报告明确列出实际测试数量、构建结果、release ID、未完成事项及“今日／近三日”的用户验收入口；线上未成功不得写“已完成发布”。Release `20261024T062157Z-62cb1a29b325-5788714156a9`，标准脚本退出 0、备份恢复通过、状态 complete；外网 200、未登录提醒接口 401/no-store、线上与本地 JS 哈希一致。实际线上角色工作台由用户登录验收，未造生产测试数据。

## 自审记录与交付状态

- 规格第1–4节由任务1/3覆盖；第5节权限由任务2/3覆盖；第6节展示由任务4/5覆盖；第7节性能和缓存由任务2–4覆盖；第8节非目标由只读快照及UI断言约束；第9节验收由任务1–6逐项覆盖。
- 契约使用 `kind/date/source_id`、`available/business_date/next_refresh_at`；前后端同名，路由与page_size边界统一。
- Review Focus 的五类边界均分配具体测试；不让详情链接或失效关系成为新授权路径。
- 用户于 2026-10-24 回复“开工”，已批准执行；任务完成情况记录于本计划专属 SDD ledger，验证后再勾选任务。
- 执行沿用上述协作方式，无需重新讨论已确认的日期与权限方案。
