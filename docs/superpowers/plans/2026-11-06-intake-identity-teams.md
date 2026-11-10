# 统一建档身份与人员级队内岗位 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建档时在同一人员身份区域选择主要/兼任身份及相应队伍，让未激活工作人员立即入名单，激活后按批准范围获得队伍只读访问。

**Architecture:** 统一身份合同由服务端推导既有业务身份和激活角色；新增人员级队内岗位与不可变授权证据，账号创建前即是真实业务归属。账号激活原子兑现到既有 `TeamStaffAssignment`，不接入旧管理型经理关系。名单兼容合并新旧来源，生命周期和幂等由后端统一处理。

**Tech Stack:** 现有 Python 3.12 / FastAPI / Pydantic / SQLAlchemy / PostgreSQL / Alembic；React / TypeScript / React Query；pytest / Vitest / Playwright，不新增运行时依赖。

**Spec:** `docs/superpowers/specs/2026-11-06-intake-identity-teams-design.md`（用户已确认，含已展示的布局）。

## Global Constraints

- 取消管理建档页面上方独立的“账号激活角色”大块勾选区，将选择整合进“人员身份”。
- 未激活的经理、教练、助教建档后立即出现在相应队伍工作人员名单中，明确标注“未激活”；账号登录和队伍数据访问权限仍在激活后生效。
- 队员沿用当前 `TeamMembership`：一人最多一个有效队伍；工作人员人员×队伍×岗位唯一，可多队、多岗。
- 工作人员队伍分配保持当前实际超管＋`team_manager_assignments.manage` 门槛，不因本次表单合并而扩大到所有管理员、行政或有建档权限者。
- 管理员/超管不可在建档授予；财务仅实际超管批准；额外角色继续检查 `permissions.manage`、完整委派权限包、范围和期限，显式 deny 优先。
- 不将新“队伍经理”接到旧版可修改名单的经理管理绑定；不额外授予入住、调房、名单编辑、财务或分成权限。
- 普通送审、重新提交、事实更正和公开注册不接受新授权字段；旧请求、历史审批签名及计划保持可验证。
- 资料更正、邮箱认领不得替换角色或队伍原批准人；无部分写入、提前授权或重复兑现。
- 两套主题、窄屏、键盘导航、展开兼任及多队选择均可用；页面焦点切换不重置输入，失效选项需显式纠正。
- 本次不更改房态统计、保洁、房卡、付款分成、采购、备份配置及域名端口；不以真实人员记录验收。
- 当前工作树为 `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`，外部托管、detached、已有大量改动；保留原工作，不 stage/commit/reset，不创建替代工作树。用任务前文件快照与任务后 diff 归属本次改动，代替每任务提交。
- PostgreSQL 测试严格单消费者；浏览器、迁移、恢复测试也占用同一数据库通道。不终止其他项目或擅自复用生产库。
- 受影响自动回归、独立审查、真实桌面/手机与 A/B 最小截图通过后，使用 `NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"` 发布，核验活动版本、健康、新资源和备份恢复，不绕过脚本。

## Review Focus

- 已打开的旧页面/旧草稿只持有 `kind + roleKeys`：必须保留兼任与队伍，要求明确主要身份，不随数组顺序猜测；旧 HTTP 重放摘要不变（Tasks 1、4）。
- 激活与撤权、重新确认、换岗或结束岗位竞态：不能新增晚锁、死锁或半份账号/凭据消费；失效授权只在验证后提示（Task 3）。
- 同人同队兼两岗、兼任多队、激活前后双来源及跨页列表：稳定行标识、先去重后分页与计数，不重复岗位也不遗漏独立岗位（Tasks 2、5）。
- 无分队权但有建档/角色权、停用候选或显式 deny：不把“不可选”误当成功，不扩大名单读权，拒绝整单并保留可保留草稿（Tasks 2–4）。
- 未激活人员的历史来源、单岗位重新确认、删除/角色重授和恢复备份：保留授权链，只影响相关岗位，不借资料更正或恢复复活已结束关系（Tasks 1–3、6）。

## 文件边界、依赖与运行约定

- Task 1：纯身份解析、版本化合同和数据库模型/迁移；确定供其他任务共用的 DTO。
- Task 2：人员级岗位的维护、统一名单查询、归属状态；不负责账号激活入口。
- Task 3：建档/激活事务、权限重查与生命周期；独占 `approval_service.py`、`role_grants.py`、`claims/locking.py`、RBAC/auth 连接点。
- Task 4：管理建档表单和草稿；Task 5：队伍工作人员名单和已有激活角色编辑反馈。两路各自拥有前端文件；API/type 共享文件先合入 Task 4 的合同，再由 Task 5 小范围扩展，不能并改。
- Task 6：隔离联调、恢复证据、独立最终审查和 NAS 发布。任务实现后先做规格检查，再做代码质量/安全检查；阻断项修复后再进入依赖任务。
- 可以并行：Task 1 合同稳定后 Task 2 与 Task 4；Task 2 合并后 Task 3 与 Task 5。共享后端生命周期文件由 Task 3 串行收口。数据库测试统一排队。
- 下文 backend 命令 cwd=`apps/api`；`PY=/Volumes/MacDev/Projects/new project3/.venv/bin/python`，同目录 `ruff`、`mypy`。执行前验证 Python 版本和 `TEST_DATABASE_URL` 指向专用可重置本地测试库；若未配置，先查仓库隔离测试约定，不猜生产 DSN。
- frontend 命令 cwd=`apps/web`，沿用现有依赖。每个任务记录 RED、GREEN、静态检查结果及实际变更文件；本计划阶段不运行测试数据库、不改产品代码。

---

### Task 1: 统一身份合同与可审计数据基础

**Files:**
- Create: `apps/api/app/personnel_intake/identity_selection.py`、`identity_models.py`。
- Create: `apps/api/app/team_management/person_staff_models.py`、`person_staff_schemas.py`。
- Modify: `apps/api/app/personnel_intake/{schemas,models}.py`、`apps/api/app/team_management/models.py`（模型注册），不改变旧审批 payload 签名结构。
- Create: `apps/api/migrations/versions/0087_intake_identity_teams.py`，`down_revision=0086_intake_activation_roles`。
- Modify: `scripts/db-schema-manifest.sh`、`apps/api/tests/db/test_migrations.py`、`apps/api/tests/cli/{test_operations_docs,test_local_container_acceptance_launcher,test_personnel_intake_manifest}.py`。
- Test: 新增 `apps/api/tests/unit/test_intake_identity_selection.py`、`apps/api/tests/db/test_intake_identity_teams_migration.py`。

**Interfaces:**
- `IdentityKey` 固定为 `student, team_member, team_manager, coach, assistant_coach, administration, customer_service, life_teacher, property, finance, guardian, staff, registered_user`；中文队伍经理只改显示，不改 `team_manager` 键。
- `ManagedIdentityInput(primary_identity: IdentityKey, concurrent_identities: list[IdentityKey], primary_staff_identity: IdentityKey | None = None)`：禁止重复、主兼重复、未知键；工作人员集合为经理/教练/助教/行政/客服/生活老师/物业/财务/其他员工。
- `StaffTeamSelection(staff_role: Literal['manager','coach','assistant_coach'], team_ids: list[UUID])`：每岗位仅一组、组内无重复、只接受已选身份对应岗位。空组规范为不存在；完整选择按岗位和 UUID 排序入指纹。
- `resolve_managed_identity(value: ManagedIdentityInput) -> ResolvedIntakeIdentity`，返回 `{selection, kind, staff_type, selected_role_keys, activation_role_keys}`。业务 kind 从学员/队员组合推导为 student/player/both，否则为 staff；额外角色为选中集合减既有 `activation_role_keys(kind)` 最低本人角色。
- 工作人员映射：`coach/team_manager/assistant_coach/life_teacher/customer_service/administration` 同名，`property/finance/staff` 为 other。主要身份为工作人员时主分类唯一确定；非工作人员主身份兼任一岗自动确定，兼多岗必须明确 `primary_staff_identity` 且属于所选工作人员。没有工作人员且 kind=staff 的旧兼容载体保持 other，不隐含授予 staff 角色。
- `IntakeIdentitySelection` / `intake_identity_selections`：按 person 唯一，保存来源 approval、原始主要/兼任/主工作人员身份、resolved staff_type、创建人/时间、规范化完整操作摘要。它是不可变建档选择证据，不代替后续角色计划；角色编辑不回写原始身份历史。
- `PersonTeamStaffPosition` / `person_team_staff_positions`：id、person_id、team_id、staff_role、origin_approval_id、created_by_user_id、started_at、ended_at/ended_by_user_id/end_reason、version、可空 `account_assignment_id`。FK RESTRICT；未结束 person×team×role 唯一、非空 account_assignment 唯一；索引 person/current、team/current、来源 approval。origin approval 必须属于同一 person；绑定账号岗位时事务校验其人/队/岗完全一致。
- `PersonTeamStaffAuthorization` / `person_team_staff_authorizations`：不可变 position_id+version 唯一，authorizer_user_id、authorized_role_key、origin role_plan_id（可空但需由当前真实账号角色证明）、operation_id、evidence_digest、created_at；角色与 staff_role 映射一致。operation_id为审计关联UUID：建档用外层approval.id，公共维护用持久化TeamOperation.id，不强行把建档写进TeamOperation幂等域。新证据一律复用LookupDigest和独立domain（`intake-identity-selection`、`person-team-staff-authorization`）生成带密钥摘要，不能只用可重算的无密钥hash。重新确认只追加证据，不覆盖旧授权人；结束审计保留证据。
- 三张表出现任一历史行时拒绝破坏性 downgrade，检查前锁表排除并发写入；空表可回到0086；不回填或修改旧用户/岗位。

- [ ] **Step 1:** 写映射失败测试：`student + coach` ⇒ kind=student、staff_type=coach、额外coach；`student + team_member` ⇒ both；非工作人员主身份兼coach+assistant且未指定主岗拒绝；13键保留，admin/root/重复/冲突拒绝；团队组序排列不改变规范结果。

  首个精确用例 `test_student_coach_has_two_business_profiles_contract` 的纯解析断言：
  ```python
  value = resolve_managed_identity(ManagedIdentityInput(
      primary_identity="student", concurrent_identities=["coach"]
  ))
  assert value.kind == "student"
  assert value.staff_type == "coach"
  assert value.activation_role_keys == ["coach"]
  ```
- [ ] **Step 2:** 运行 `"$PY" -m pytest tests/unit/test_intake_identity_selection.py`，确认因新合同未实现而失败；不得以夹具导入失败当有效RED。
- [ ] **Step 3:** 实现上述解析器/DTO/模型；校验限制不得只放前端。新合同放管理专属类型，不扩大 `IntakeInput`、`AssistedProfileCorrection`、`PendingProfileCorrection`。
- [ ] **Step 4:** 写并运行迁移RED：0086旧行逐字段保持；人员级重复当前岗位失败、不同岗位/队伍通过、已结束可重建；有身份/岗位/授权历史时降级拒绝且数据不丢；空表往返及并发检查-写入互斥。
- [ ] **Step 5:** 实现0087并注册模型、head/chain/表/行数/索引/约束清单；更新旧迁移测试中新增表集合，不改历史revision预期。执行前重查head，若别的工作已新增迁移，重新对齐链而非覆盖。
- [ ] **Step 6:** 运行两份新测试和 `tests/cli/test_operations_docs.py tests/cli/test_local_container_acceptance_launcher.py tests/cli/test_personnel_intake_manifest.py`，全通过；Ruff/mypy新模块，保存差异供独立审查。

### Task 2: 人员级岗位维护与统一工作人员名单

**Files:**
- Create: `apps/api/app/team_management/person_staff_service.py`、`person_staff_query.py`、`person_staff_router.py`。
- Modify: `apps/api/app/team_management/{router,staff_service,allocation,write_support}.py`、Task 1 的 `person_staff_schemas.py`。
- Test: 新增 `apps/api/tests/services/test_person_staff.py`、`apps/api/tests/api/test_person_staff.py`；扩展 `tests/{api,services}/test_team_staff.py`、`tests/services/test_team_allocation.py`。

**Interfaces:**
- `PersonStaffService(db, *, lookup)`：`create_in_transaction(actor, *, person_id, approval_id, selections: list[StaffTeamSelection], operation_id: UUID, occurred_at: datetime) -> list[PersonTeamStaffPosition]`。只接受外层完整参与者/目录锁和同一原子事务；整体幂等由上层拥有，不循环调用公共 assign 或生成随机子键。authorization version 初值1。
- 公共命令 `assign_person(actor, team_id, command: PersonStaffAssignInput) -> TeamMutationResult`、`end(actor, position_id, command: PersonStaffEndInput) -> TeamMutationResult`、`reconfirm(actor, position_id, command: PersonStaffReconfirmInput) -> TeamMutationResult`。assign输入 person_id/staff_role/idempotency_key；end/reconfirm输入 expected_version>=1、reason(1–200)、idempotency_key；保留现有 TeamOperation 操作者+key全域幂等。实施核实后将新公共原因上限收敛到现有审计purpose的200字，完整保留原因并复用隐私校验，不截断或仅保存摘要；历史end_reason列仍为500。
- 仅有真实对应激活角色计划的未激活管理档案可 assign_person；已经有账号的新增旧式岗位继续使用原 assignStaff，避免新增两套赋权流程。reconfirm检查当前队伍分配权、对象合法角色来源及队伍状态，追加本岗位授权版本，不替换角色计划批准人。
- 新路由均在 `/api/v1/team-management`：`GET /teams/{team_id}/staff-positions`（status/page/page_size沿用旧列表）、`GET /teams/{team_id}/person-staff-candidates`（staff_role/query/cursor/page_size，未激活候选）、`POST /teams/{team_id}/person-staff-positions`、`POST /person-staff-positions/{position_id}/end`、`POST /person-staff-positions/{position_id}/reconfirm`。均按原工作人员名单 `assign_managers` 门槛，private/no-store；不改旧API合同。
- `PersonStaffRow`：row_key=`person:<position_id>`或`account:<assignment_id>`、source_kind(`person/account`)、person_position_id|null、account_assignment_id|null、person_id、account_id|null、full_name、nickname、staff_role、account_status(`not_activated/active/disabled/deleted`)、authorization_status(`valid/reconfirmation_required`)、started_on、ended_on、version|null、allowed_actions。账号状态与授权状态分开，不输出证件/联系方式。未兑现关系按当前证据/批准人资格显示待确认；已兑现关系沿用现有有效账号/岗位校验，不新增“原批准人离职则批量撤销历史已生效岗位”的规则。
- `PersonStaffQuery.list(actor, team_id, *, status, page, page_size) -> TeamPage[PersonStaffRow]`：current按人/队/岗去重后计数分页；已兑现来源优先人员行，row_key兑现前后不变；历史只消除同一链接的双来源，不能吞掉同人再次任职的独立历史。候选用独立nullable account DTO，不放宽旧候选schema。
- `GET /api/v1/personnel-intake/management-identity-context` 留给Task 3路由；Task 2提供 `staff_team_options(actor) -> StaffTeamOptions`：有门槛才返回可见可用分部/队伍(id/project_id/project_name/name)，无权返回can_assign=false和原因，不泄露团队列表。

- [ ] **Step 1:** 写RED：未激活coach入两个队、同队coach+assistant均可见，无UserAccount/UserRole/TeamStaffAssignment，无读取范围；current total按不同岗位计数，分页不重；虚构无角色计划对象、非超管/deny/跨范围/停用队伍全部拒绝零写入。

  `test_unactivated_staff_visible_without_account_grant` 建立虚构coach岗位后，用真实查询断言：
  ```python
  assert page.total == 1
  assert page.items[0].account_id is None
  assert page.items[0].account_status == "not_activated"
  assert db.scalar(select(func.count()).select_from(UserAccount)
      .where(UserAccount.person_id == person.id)) == 0
  assert page.items[0].account_assignment_id is None
  ```
- [ ] **Step 2:** 运行 `"$PY" -m pytest tests/services/test_person_staff.py tests/api/test_person_staff.py`，记录对应未实现行为失败。
- [ ] **Step 3:** 实现人员岗位创建/维护、证据摘要校验与明确中文403/409/422；复用TeamOperation和锁序，版本过期拒绝；相同命令重放无新历史/审计，不同payload同key冲突。
- [ ] **Step 4:** 实现统一列表/候选和staff_team_options；SQL先过滤、去重再分页，批量状态读取防逐行授权/账号查询；为旧账号来源保留原可用操作。将真实person岗位纳入allocation `_relationships`，结束最后归属同步assigned→pending。
- [ ] **Step 5:** 写并通过维护测试：追加/单岗结束/重新确认只影响该岗，旧证明保留；来源已绑定旧账号assignment时，旧end入口与新end入口统一结束两边；无关联旧assignment行为不变；旧API字段集合测试不变。
- [ ] **Step 6:** 运行新tests及 `tests/api/test_team_staff.py tests/services/test_team_staff.py tests/services/test_team_allocation.py`；检查本模块Ruff/mypy和真实查询次数上界，交独立复核。

### Task 3: 建档、激活和撤权的同事务闭环

**Files:**
- Create: `apps/api/app/personnel_intake/identity_service.py`、`staff_activation.py`。
- Modify: `apps/api/app/personnel_intake/{router,approval_service,role_grants,activation_service,claims/locking}.py`。
- Modify: `apps/api/app/team_management/{lifecycle,staff_service}.py`、`apps/api/app/rbac/service.py`、`apps/api/app/auth/service.py`；`apps/api/app/teaching/catalog_service.py`仅已有停用链路所需扩展。
- Test: 新增 `apps/api/tests/api/test_intake_identity_teams.py`、`tests/services/test_intake_staff_activation.py`、`tests/db/test_intake_identity_team_locks.py`、`tests/services/test_person_staff_lifecycle.py`。

**Interfaces:**
- 只在管理 `DirectCreateInput/DirectCreatePendingInput` 增加 `identity_selection: ManagedIdentityInput | None` 和 `staff_teams: list[StaffTeamSelection]`。wrapper中kind可省略仅当有新identity；普通共享输入仍要求kind。新增resolver生成原 `DirectPersonInput/PendingIntakeInput`，其余事实验证不变。
- 新输入同时带旧kind/activation_role_keys时，显式传入部分必须与推导值一致（角色比较去除最低本人角色后），冲突422；没新输入时完整保留旧路径和旧指纹，禁止无新身份却带staff_teams。新版本指纹显式标记identity_contract=1并含事实/主兼身份/工作人员主分类/全部队伍；无关排列规范化，同键改身份或队伍409。
- `IntakeIdentityService(db, *, lookup).materialize_in_transaction(actor, *, person_id, approval_id, selection: ResolvedIntakeIdentity, staff_teams, operation_key, operation_digest, occurred_at) -> None`：保存原身份选择证据、增补/设置StaffProfile、复用激活角色服务与Task 2真实岗位；外层完整原子事务。学员兼教练必须有StudentProfile和StaffProfile，不能只赋角色。
- `GET /personnel-intake/management-identity-context` → `{role_options: ActivationRoleOption[], can_assign_staff_teams: bool, staff_team_reason: string|null, staff_team_options: {id,project_id,project_name,name}[]}`，仅管理建档者可读；角色沿用目录、队伍按Task 2资格过滤；旧activation-role-options保持。
- `PreparedStaffActivation.discover(db, person_id) -> PreparedStaffActivation`：只读不可变快照，包含岗位id/version/最新证据、全部队伍、各岗位授权人账号/人员id；`assert_unchanged(db) -> None`重读检测漂移。合入ClaimLockContext的人员/账号/credential/membership/团队集合，与既有PreparedActivationRoleLocks共同预发现，不能在下游发现新参与者。
- `realize_staff_positions_in_transaction(db, *, prepared: PreparedStaffActivation, account: UserAccount, lookup: LookupDigest, occurred_at: datetime) -> None`：验证证据摘要/当前原授权人/角色/人队分部，调用既有staff内部已锁方法写只读assignment并链接；不创建TeamManagerAssignment。不对外暴露新直接赋权接口。
- 角色计划PUT增加可选 `confirm_end_staff_positions: bool = False`（同时修改 `schemas.py` 中ActivationRoleUpdate，确认位纳入命令指纹；旧未传命令保持旧指纹）。若移除身份影响当前person岗位：先检查受影响队伍维护权，未明确确认返回409 `activation_roles_staff_positions_confirmation_required`；有权确认则版本核查、追加角色计划与结束这些岗位同事务。无权403。仅增加无关角色不重签队伍证据；证据的origin role_plan_id保留原始来源，不要求等于后来的最新计划ID，但最新计划仍须包含该岗位所需角色，且原角色/队伍批准人各自通过当前授权复查。
- 生命周期内部 `close_for_deleted_person(person_id, actor_account_id, occurred_at) -> None`供现有account删除复用，不新增无账号人员删除页面/API。role移除/换岗仅结束匹配岗位，普通停用只阻止读取不结束；角色重授不恢复旧岗位。

- [ ] **Step 1:** 写建档RED：两种模式、student+coach与player+manager、三类工作人员多队/同队多岗；旧严格学员负责教练/队员队伍必填，待补可缺；not_required+真实工作人员归属拒绝；任选一非法归属/角色整单零写入。普通送审/更正/注册新字段422。
- [ ] **Step 2:** 运行 `"$PY" -m pytest tests/api/test_intake_identity_teams.py tests/services/test_intake_staff_activation.py` 获取RED；实现管理wrapper、resolver/context、外层建档原子链路与新旧指纹分支。
- [ ] **Step 3:** 写激活RED：建档只显示未激活；邮箱验证完成后建立全部只读链接，名单row_key/total稳定；不同授权人分别撤权/停用/原计划变化/证据被改均失败且无账号、无角色、无assignment、OTP未消费；旧无新选择的激活保持。资料更正/认领不得改证据或批准人。

  `test_activation_preserves_roster_identity_without_manager_write_scope` 从同一真实列表取得before/after，除只读访问结果外还断言：
  ```python
  assert after.total == before.total
  assert after.items[0].row_key == before.items[0].row_key
  assert after.items[0].account_id == account.id
  assert after.items[0].account_assignment_id is not None
  assert db.scalar(select(func.count()).select_from(TeamManagerAssignment)
      .where(TeamManagerAssignment.account_id == account.id)) == 0
  ```
- [ ] **Step 4:** 实现预发现/锁/重读/兑现：沿用当前文档证件/credential/advisory → membership/request → guardian graph → 排序people/accounts → 全部roles → project/team目录 → 岗位/授权 → claim/session → challenge 的规范顺序；内部方法不得重新获取上游锁。未知参与者/计划漂移409重试，不能临时追加晚锁。OTP猜测计数仍在既有独立事务。
- [ ] **Step 5:** 实现角色计划移除确认、RBAC remove_role/change_job_role、account删除和队伍/分部clear检查。扩展全部相关关系发现函数，包括当前失效但未结束的岗位；旧账号end/撤权必须同事务联动人员来源。临时禁用不结束，重授不复活；只结束一岗不改变其他队/岗。
- [ ] **Step 6:** 写真实独立连接并发RED并实现到GREEN：activation对role撤销、position结束、reconfirm、角色计划重签、team停用；两个并发finish只产一个账号/链接；多队反序请求无死锁；部署测试用超时有界连接与栅栏，不以sleep假装并发。检查失败后OTP可重试与无半写入。
- [ ] **Step 7:** 运行四份新测试和 `tests/services/test_assisted_activation.py tests/services/test_intake_activation_roles.py tests/services/test_team_staff_account_lifecycle.py tests/services/test_team_lifecycle.py tests/api/test_team_lifecycle.py tests/api/test_profile_claims.py tests/api/test_profile_claim_review.py tests/db/test_intake_activation_role_locks.py tests/db/test_team_staff_lifecycle_concurrency.py`，单数据库消费者。Ruff/mypy受影响模块，独立安全审查锁序/授权/生命周期后才交联调。

### Task 4: 统一管理建档身份表单与草稿

**Files:**
- Create: `apps/web/src/assistedPeople/intakeIdentity.ts`、`IntakeIdentitySection.tsx`及对应`.test.ts/.test.tsx`，样式 `intakeIdentity.css`。
- Modify: `apps/web/src/assistedPeople/{types,api}.ts`、`{AssistedDirectCreatePage,AssistedPersonForm,PendingPersonForm}.tsx`及对应测试。
- Test: `apps/web/src/assistedPeople/{api,AssistedProfileCorrectionPanel,MyAssistedRequestsPage}.test.ts(x)`、`apps/web/src/people/PersonForm.activationRoles.test.tsx`。

**Interfaces:**
- `IntakeIdentityDraft`：`primary_identity: IdentityKey|null, concurrent_identities: IdentityKey[], primary_staff_identity: IdentityKey|null, player_team_id: string|null, staff_team_ids: Record<TeamStaffRole,string[]>`；coach_person_ids继续沿用表单事实草稿，不重复第二份来源。
- `migrateLegacyIdentityDraft(saved: LegacySavedDraft) -> {value: IntakeIdentityDraft, needsPrimaryConfirmation: boolean}`：保留kind隐含身份、roleKeys、单队和原attempt；新提交格式变化则按新规范指纹生成新attempt，普通失败重试同fingerprint复用原key。
- `previewIdentityChange(current: IntakeIdentityDraft, nextSelection: ManagedIdentityInput, coachPersonIds: string[]) -> {next: IntakeIdentityDraft, nextCoachPersonIds: string[], removedAssignments: RemovedAssignment[]}`（包含被隐藏队伍与负责人）；由UI确认后才同时更新身份/事实草稿；`normalizeIdentityInput(value: IntakeIdentityDraft) -> {identity_selection: ManagedIdentityInput, staff_teams: StaffTeamSelection[], team_id: string|null}`只校验/排序，不静默删除。`RemovedAssignment`包含kind(`player_team/staff_team/coach`)、id和可空staff_role，文案由已有候选名映射。
- `IntakeIdentitySection`受控props：value/onChange、context（Task 3）、coachOptions/teamOptions、mode(strict/pending)、disabled/errors；无自有请求/隐式默认角色。管理专属表单分支持有此合同，普通 `identityOnlyKind`/apply/resubmit 仍走原分支，不能全局扩展共享授权props。
- 管理草稿仍使用account-scoped key `['assisted-people',accountId,'pending-create-draft']`，新增schemaVersion=2、identity、draft、attempt；不把敏感事实保存到localStorage。旧草稿恢复时主身份空且要求确认，未认识/无权限旧角色不静默丢失；暂时网络错误保留，明确失权或换号清理。

- [ ] **Step 1:** 写纯函数RED：13身份、主兼/多工作人员主分类、单队与分岗多队规范化；旧both+coach+队伍完整恢复但主身份待确认；身份删除列出归属；相同集合顺序变化fingerprint相同、实际归属变化不同。运行 `npm test -- src/assistedPeople/intakeIdentity.test.ts`。

  `it('keeps legacy roles and requires primary confirmation')` 对现有合法完整草稿fixture加 `kind:'both'`、roleKeys=['coach'] 后断言：
  ```ts
  expect(migrated.needsPrimaryConfirmation).toBe(true)
  expect(migrated.value.primary_identity).toBeNull()
  expect(migrated.value.concurrent_identities).toEqual(
    expect.arrayContaining(['student', 'team_member', 'coach']))
  expect(migrated.value.player_team_id).toBe(saved.draft.team_id)
  ```
- [ ] **Step 2:** 实现纯函数、DTO严格解析和管理context API；与Task 1字段完全一致，不自行硬编码可授权目录。保留旧创建API调用合同与旧activation-role-options用于详情页。
- [ ] **Step 3:** 写组件/页面RED：顶部无旧角色面板，主要身份+可展开兼任，按身份出现队伍；严格/待补必填不同；无分队权禁用解释但不影响无队建档；提交区确认；移除身份取消操作后数据不变；失败保留，重试同key；后台权限变化阻止旧选择提交。
- [ ] **Step 4:** 实现共用身份区域、联动验证和提交摘要；管理员/超管不出现，敏感说明按需显示。控件使用现有主题token与>=44px触控目标，标签可读、错误关联输入、键盘可操作；不要删除仍用于详情页的ActivationRolePicker。
- [ ] **Step 5:** 运行新tests和 `npm test -- src/assistedPeople src/people/PersonForm.activationRoles.test.tsx`；验证普通申请/事实更正不出现新授权、页面焦点回来不重置、账号切换不会泄露旧草稿。`npm run lint`、`npm run build`通过，提交独立UI/合同检查。

### Task 5: 未激活工作人员名单与安全维护交互

**Files:**
- Modify: `apps/web/src/teams/{api,keys,presentation}.ts`、`TeamStaffPanel.tsx`、`TeamStaffPanel.css`及相关tests。
- Modify: `apps/web/src/assistedPeople/{ActivationRolesPanel.tsx,api.ts,types.ts}`及相关tests；与Task 4共享文件变更串行。
- Test: `apps/web/src/teams/{TeamManagementPage,MyTeamPanel,TeamManagerPanel}.test.tsx`（存在的原角色边界保持）、`src/assistedPeople/ActivationRolesPanel.test.tsx`。

**Interfaces:**
- 新API客户端 `listStaffPositions(teamId, filters, signal)`、`personStaffCandidates(teamId, filters, signal)`、`assignPersonStaff(teamId, input, signal)`、`endPersonStaff(positionId,input,signal)`、`reconfirmPersonStaff(positionId,input,signal)`直接使用Task 2路径/DTO；旧assignStaff/候选/end客户端保留用于纯account来源。
- TeamStaffPanel原props不变；按row.source_kind和allowed_actions选择命令，person来源兑现后仍走person维护；pending候选单独“未激活人员”选项，与已激活账号候选清楚区分。query keys包含actor/account、team、query、staff_role、筛选/分页。
- `ActivationRolesPanel`移除有队内岗位角色遇Task 3确认码时展示后果确认，再带同版本 `confirm_end_staff_positions=true` 重新提交；确认payload使用新操作key，后续网络失败重试同key；不能删除更多角色来规避拒绝，也不直接调用多个结束API模拟事务。

- [ ] **Step 1:** 写RED：未激活姓名/昵称/岗位显示，账号状态与授权待确认分开；active+pending不得标为正常生效；多岗多队独立行；兑现后row_key稳定、total不变；普通人员无新入口/额外数据。

  `it('distinguishes active account from stale team authorization')` 渲染active/reconfirmation_required虚构行，断言：
  ```ts
  expect(screen.getByText('已激活')).toBeInTheDocument()
  expect(screen.getByText('授权待重新确认')).toBeInTheDocument()
  expect(screen.queryByText('未激活')).not.toBeInTheDocument()
  expect(screen.getByRole('button', {name: '重新确认授权'})).toBeEnabled()
  ```
- [ ] **Step 2:** 运行 `npm test -- src/teams/TeamStaffPanel.test.tsx src/assistedPeople/ActivationRolesPanel.test.tsx`，实现Task 2 DTO/API、统一列表和明确状态说明。
- [ ] **Step 3:** 写并通过交互测试：有权者追加/结束/单岗重新确认、二次请求重放、版本冲突、不匹配响应拒绝；无对应动作不呈现按钮；旧经理管理组件和旧account-only岗位不误用person_id；角色移除确认取消不写，确认原子提交。
- [ ] **Step 4:** 实现维护弹窗/原因/确认及查询失效；刷新后以服务端返回确认成功，不把乐观UI当已生效。名单不输出敏感字段、不扩展名单查看权限；旧manager分配入口语义不变。
- [ ] **Step 5:** 运行 `npm test -- src/teams src/assistedPeople/ActivationRolesPanel.test.tsx src/assistedPeople/api.test.ts`、`npm run lint`、`npm run build`；交独立审查。

### Task 6: 隔离端到端、恢复核验、最终审查与 NAS 发布

**Files:**
- Create: `apps/web/tests/intake-identity-teams.spec.ts`、`scripts/run-intake-identity-teams-e2e.sh`。
- Modify: `scripts/run-assisted-personnel-e2e.sh`（固定新suite intake-identity及财务不变性/截图收集）、`apps/api/app/cli/seed_assisted_personnel_e2e.py`（沿用生成式虚构资料/私有邮箱）、`apps/api/tests/cli/{test_assisted_personnel_e2e_launcher,test_check_e2e_orchestration,test_seed_assisted_personnel_e2e}.py`；新suite加入 `scripts/check.sh` 的固定完整门禁。
- Modify: `apps/api/tests/cli/{assisted_restore_probe,test_assisted_personnel_backup_docker}.py`，必要fixture；不改线上备份配置。
- Create: `docs/superpowers/verification/2026-11-06-intake-identity-teams.md`（实际证据，不预填通过）。

**Interfaces:** 新runner只接受无参数固定完整suite，复用已有专用DSN检查、合成账号种子、API/web进程管理、桌面/手机project、清理与财务baseline断言。证据只留虚构画面和脱敏结果。

- [ ] **Step 1:** 调取各任务开始前的head/文件快照/已有dirty清单（Task 1开始前即需留存，不能现在才补造基线）；确认一个数据库owner。为新runner写错误参数/非测试DSN/跳过用例/缺失截图会拒绝的CLI测试；先RED再扩展固定白名单，不新增绕过安全开关。
- [ ] **Step 1a:** `test_intake_identity_gate_refuses_partial_acceptance` 沿用launcher harness注入缺失手机结果与截图，断言 `result.returncode != 0`、没有发布成功标记、没有向NAS写入；正常合成完整输出则断言 `result.returncode == 0`，且固定两project、全部spec、财务后验和清理均出现。
- [ ] **Step 2:** 新E2E覆盖：超管严格coach+manager多队、待补student+player+assistant、未激活立即名单、资料补齐/邮箱激活后去重和只读范围、结束/重确认、角色移除确认、无分队权可建档但不可夹带队伍、旧草稿恢复。使用真实隔离API，不在NAS创建测试人员；A/B各有桌面和窄屏截图，虚拟键盘/焦点切换不丢输入。
- [ ] **Step 3:** 运行 `scripts/run-intake-identity-teams-e2e.sh`，必须完整suite退出0、无skip、账务baseline不变、进程/临时凭据清理通过；同时回归原 `scripts/run-intake-activation-roles-e2e.sh` 和涉及普通送审/队伍流程的固定suite。发现产品失败先按systematic-debugging定位，不能弱化断言。
- [ ] **Step 4:** 扩展恢复probe合成四类数据：未激活多岗、已兑现链接、已结束历史、重新确认版本；恢复后逐项核对FK/来源/摘要及行为（不提前授权、无重复名单、仍可维护、已结束不复活）。运行 `"$PY" -m pytest tests/cli/test_assisted_personnel_backup_docker.py`；需要Docker时在隔离项目内运行，不连接NAS业务DB。
- [ ] **Step 5:** 串行运行完整backend `"$PY" -m pytest`（明确记录optional skip原因，任何新增或受影响skip不可当通过），`ruff check app tests`、`mypy app`；前端 `npm test -- --maxWorkers=4`、`npm run lint`、`npm run build`。既有失败也要定位与影响评估，不把历史计数作为本轮结果。
- [ ] **Step 6:** 根代理按任务前后快照合成仅本次diff，交新独立审查员复核权限/事务/锁序/迁移/旧入口兼容与浏览器证据；修复阻断项并重跑相关和最终检查。禁止把源码审查通过表述为实机/发布通过。
- [ ] **Step 7:** 从本工作树运行 `NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`。预检、备份、迁移、构建或恢复验证任何失败均阻断发布；不直接scp代码或手工Docker替代，不碰其他项目/数据库卷。
- [ ] **Step 8:** 只读核验 `.active-release`、deployment complete、Alembic0087（或已对齐的新head）、服务健康、HTTPS18080资源、实际备份恢复报告。新界面查验使用已有授权后台但不提交真实人员数据；记录没有执行的真实用户激活，不声称已替用户建档。
- [ ] **Step 9:** 写最终验证记录，区分源码/自动测试/隔离浏览器/恢复/NAS状态，给出操作入口与未激活/激活后可见性。不在最终报告包含真实人员事实、账号密码、OTP或密钥。

## 根代理计划自审与执行交接

- 规格覆盖：身份/兼任/主分类 → Tasks 1、4；预激活真实岗位/名单维护 → Task 2、5；原子建档激活/权限及生命周期 → Task 3；兼容/多主题/恢复发布 → 全任务及Task 6。
- 五类Review Focus各有指定失败测试；新旧身份混传、旧指纹重放、角色移除确认与多来源分页均有显式决策。
- 合同统一使用 `identity_selection`、`primary_identity`、`concurrent_identities`、`primary_staff_identity`、`staff_teams`、`staff_role`、`person_position_id`，不混用队员 `team_id` 与工作人员归属。
- 仅文档计划完成不等于产品完成。保留此前已选择的子任务协作方式：依赖内串行、可独立的前后端任务并行，每任务新审查，根代理最终集成和部署。
- 计划交用户审阅；确认捕获需求后才执行Task 1，不提前改业务代码或线上权限。
