# 队员、队伍管理与分部归属 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 ERP 中交付独立队员身份、按队伍授权的管理、唯一在队归属和可追溯调队，并完成本地验收与 NAS 发布。

**Architecture:** 扩展既有 `GameProject`、`ProfessionalTeam`，新增 `team_management` 领域；名单授权、业务历史及幂等变更独立于全局教学目录。复用 ERP 账号、拒绝优先的 RBAC、事务、审计和响应式页面；现有身份及财务等模块不变。

**Tech Stack:** 现有 FastAPI / SQLAlchemy / Alembic / PostgreSQL；React / TypeScript / TanStack Query / Vitest / Playwright。不新增运行时依赖。

**Spec:** `/Users/skymoonzyj/Documents/new project3/docs/superpowers/specs/2026-09-29-team-roles-and-divisions-design.md`（2026-09-29 用户已确认）。

## Global Constraints

- 新角色键 `team_member`（队员）、`team_manager`（队伍管理）；同一账号可以兼任队员、学员及其他已有角色。
- 首版分部为“王者荣耀”“和平精英”“三角洲行动”；复用已有同名项目，不合并别名，不覆盖原项目资料或重新启用停用项目。
- 同一人员在所有分部合计，同一时间最多属于一支队伍；调队保留历史。
- 管理人员可负责多队，一队可有多个管理人员；管理绑定不产生队员身份。
- 队伍管理只增减本队已有合格队员；在队人员跨队调动、分配管理人员只由有效超管操作，仍遵守显式拒绝。
- 不新增 PersonType，不自动生成学生／员工档案，不从旧教学班或教练分配推导新成员／管理绑定。
- 所有名单变更立即生效；业务日期为中国时间 `YYYY-MM-DD`，年份仅四位；审计保留时间。
- 新名单接口不返回完整身份证、手机号、邮箱、出生日期、家庭关系、财务数据或密码相关字段。
- 不给新角色默认授予全局 `teaching.read`、`professional_teams.manage`、`people.list` 或财务、账号管理权限。
- 只读自己的归属不等于可以读队友；无权读取归属显示“无查看权限”，不能显示成“未分配队伍”。
- 不开发客户端、赛事、训练排期、合同薪酬、分成、自动评价、自助入退队、名单导出、批量导入或推送。
- 数据库回归只在隔离测试库串行运行；不在 NAS 真实账号上制造测试名单、绑定或调动记录。
- ERP 代码通过本地检查后必须运行根工作区 `scripts/deploy-nas.sh --source <已审核发布源>`；预检、备份恢复或部署失败阻断发布，不绕过脚本。

## Review Focus

1. 多身份及临时停用：离队不删除学生身份；停用保留归属、删号关闭关系、撤角色遵守不同规则（任务 4）。
2. 旧页面与失去授权：列表、数量、候选、历史、重放请求与人员投影都不能泄露其他队资料（任务 2、5、7）。
3. 并发和网络重试：两队抢同一人、调队撞离队、撤绑定撞提交，只能形成一个有效且已授权结果（任务 3、4）。
4. 日期与历史：北京时间跨日、同日退后重入、停用分部的旧历史，顺序正确且旧关系 ID 不能操作新关系（任务 3、5）。
5. 真实名录及移动端：单字／昵称／首字母、同名人、超过一页、软键盘、切换账号，不能漏筛或出现残留数据（任务 5、6、7）。

---

## 执行边界、文件责任与依赖

本轮只交付计划，尚未实施。代码依据是 `/Users/skymoonzyj/Documents/new project3/.nas-release-sync`，不是主目录较旧的应用代码；其中大量未提交变更属于已有工作。执行时先按 worktree 技能检查现有可用工作区，保全已审核发布源，再在隔离目录开展；不能仅从旧 HEAD 新建后覆盖当前产品，也不能全量提交当前脏目录。以下相对代码路径均以该次确认的发布源为根。

2026-09-29 只读核验迁移头为 `0060_esports_assessments`；本计划新增 `0061_team_management`。执行前重新核对，如迁移头已变更，顺延版本并统一调整 manifest、测试和发布证据，禁止制造双头。

| 责任单元 | 文件及职责 | 依赖 |
| --- | --- | --- |
| 1 数据与初始化 | `apps/api/app/team_management/{__init__,models}.py`；`teaching/models.py`；`rbac/{models,seed}.py`；迁移与 schema manifest | 无 |
| 2 授权与契约 | `team_management/{schemas,errors,access}.py`；RBAC scope、对象验证／目录；权限策略测试 | 1 |
| 3 成员及管理写入 | `team_management/{write_support,membership_service,assignment_service}.py`；审计动作白名单；并发／幂等测试 | 2 |
| 4 生命周期接入 | `team_management/lifecycle.py`；`auth/service.py`、`rbac/service.py`、`teaching/catalog_service.py` 的窄接入点 | 3 |
| 5 查询与 API | `team_management/{query_service,projection,router,dependencies}.py`；identity 查询／投影；API 路由注册 | 3 |
| 6 队伍页面 | `apps/web/src/teams/{api,keys,TeamManagementPage,MyTeamPanel,TeamActionDialogs,TeamManagerPanel}` 及样式／测试；教学目录表单提取 | 2 契约冻结后可并行，集成依赖 5 |
| 7 入口与既有页面 | router、AppShell、人员中心、权限管理页面、对应 TS 契约与测试 | 4、5、6 |
| 8 验收发布 | 专用隔离 E2E fixture／launcher、全回归、部署和证据 | 全部 |

任务 4 与 5 可并行，任务 6 可在契约冻结后并行；其他共享文件按上述顺序交接。主代理负责集成，子任务明确文件所有权，不能撤销其他人的修改。测试库由主代理串行调度，纯前端检查可并行。子任务数量随实际依赖安排，不为凑数量拆任务。

测试命令约定：API 工作目录 `apps/api`，`$PY=/Users/skymoonzyj/Documents/new project3/.venv/bin/python`；数据库命令显式使用已核验的本机 `TEST_DATABASE_URL`，禁止从 NAS `.env` 取值。Web 工作目录 `apps/web`。每个任务先写失败测试、确认因本任务缺失而失败，再实现；下面的测试断言为待落地的验收约束，不是已通过证据。

每个代码任务末尾只提交本任务明确文件／增量，并记录命令和结果；开发阶段有意暂停自动部署时设置 `ERP_NAS_AUTO_DEPLOY=0`，不能把它留成全局关闭。最终集成发布必须显式运行部署脚本。隔离基线中混有既有改动的文件不得整文件无审查纳入提交。

## 共享契约（任务 1、2 定义，后续任务直接使用）

- `TeamMembership`：`id/person_id/team_id/joined_on/left_on/created_at/ended_at/created_by_user_id/ended_by_user_id/open_operation_id/close_operation_id/end_kind/transfer_reason`；`end_kind = left | transferred | account_deleted`，开放关系结束字段为空。调队原因只写已关闭原关系，业务历史仅超管或本人可读原因，不向管理人员泄露其他队去向。
- `TeamManagerAssignment`：`id/account_id/team_id/created_at/ended_at/created_by_user_id/ended_by_user_id/open_operation_id/close_operation_id/end_kind`；`end_kind = unassigned | role_removed | account_deleted`。
- `TeamOperation`：`id/actor_account_id/idempotency_key/action/request_fingerprint/result_ids/created_at`；唯一 `(actor_account_id,idempotency_key)`，操作之间共用键域。只保存操作类型、规范化摘要及结果 UUID，不存完整请求、联系人或姓名快照。成功写入业务及成功审计时一并提交。
- 四个能力键固定为 `team_memberships.read`（self/assigned_team/global）、`team_memberships.manage`（assigned_team/global）、`team_memberships.transfer`（global）、`team_manager_assignments.manage`（global）。成员／绑定由删号或撤角色关闭时 `close_operation_id` 可空，使用原生命周期成功审计关联，不伪造一次交互操作。
- `TeamAction = Literal['read','manage','transfer','assign_managers']`；`TeamPage[T]={items,page,page_size,total}`，默认 20、最大 100；候选用 `TeamCandidatePage={items,next_cursor}`，最多 20，无总数。
- `TeamCandidate={account_id,person_id,full_name,nickname,age,is_minor}`；`TeamSummary={id,project_id,project_name,name,is_active,member_count,allowed_actions}`；`TeamMemberView` 增加成员关系 ID、规格名单字段、`account_status=active|disabled|deleted`，不返回原始 DOB。
- `TeamAssignmentView={id,account_id,full_name,nickname,account_status,started_on,ended_on}`；`MyTeamResponse={current:TeamAffiliation|null,history:TeamPage[TeamMemberView]}`；所有业务日期字段为 date，DTO ID 为 UUID，允许的动作采用 `list[TeamAction]`。
- `PersonTeamProjection={visibility:'allowed'|'denied',current:TeamAffiliation|null}`；`TeamAffiliation={membership_id,project_id,project_name,team_id,team_name,joined_on}`。无权时 current 必须空，权限不足与无归属不可混同。
- `TeamContext={can_open_management,can_read_self,can_manage_catalog,can_assign_managers,can_transfer}`；这里只控制入口，资源动作再校验实际权限。无绑定 manager 不开放管理入口。
- 请求均 `extra='forbid'`：`JoinTeamInput={person_id,idempotency_key}`；`LeaveTeamInput={idempotency_key}`；`TransferTeamInput={target_team_id,reason,idempotency_key}`（去空白后原因 1–200 字）；`AssignManagerInput={account_id,idempotency_key}`；`UnassignManagerInput={idempotency_key}`。关系 ID 来自资源路径，不接受生效日期。
- `TeamMutationResult={operation_id,membership_id:UUID|null,closed_membership_id:UUID|null,assignment_id:UUID|null}`；首次／重放均返回 200 同一结果。前端 UUID 操作键在确认弹窗一次操作内固定，失败不换键。

## Task 1：模型、迁移和无破坏初始化

**Files:** 新建 `apps/api/app/team_management/{__init__,models}.py`、`apps/api/migrations/versions/0061_team_management.py`、`apps/api/tests/db/test_team_management_migration.py`、`test_team_management_models.py`。修改 `apps/api/app/teaching/models.py`、`apps/api/app/rbac/{models,seed}.py`、`apps/api/migrations/env.py`、`scripts/db-schema-manifest.sh`、`apps/api/tests/db/{test_migrations,test_rbac_seed}.py` 及 `tests/cli/{test_operations_docs,test_local_container_acceptance_launcher}.py` 中迁移头断言。

**Interfaces:** 产出上述三个 ORM 模型，表名 `team_memberships`、`team_manager_assignments`、`team_operations`；`GameProject.is_team_division: bool`，默认 false。现有 `seed_roles_and_permissions(db: Session) -> None` 支持新角色／能力。

- [ ] 写 `test_preserves_existing_catalog_and_profiles`：`assert after_ids == before_ids`，`assert inactive_project.is_active is False`，`assert unrelated_rows == original_rows`；重复种子后 `assert new_role_count == 2` 且旧角色授权不变。
- [ ] 写唯一／日期约束测试：`assert open_memberships_for_person == 1`；允许多条关闭历史及同日入离队；结束早于开始、开放关系带结束字段、重复开放管理绑定均由数据库拒绝。迁移 upgrade→downgrade→upgrade 保留旧教学／账号数据，降级只允许隔离无新业务数据 fixture。
- [ ] 运行 `$PY -m pytest tests/db/test_team_management_migration.py tests/db/test_team_management_models.py -q`，确认缺少表／标志导致失败。
- [ ] 实现模型、外键 RESTRICT、两个开放关系 partial unique、队伍／人员历史索引及时间配对 CHECK；仅迁移初始化三个分部，缺失才建项目，已有只设置分部标志。迁移后的启动种子不得把超管取消的分部标志重新打开；不产生任何成员／管理绑定。
- [ ] 角色固定 ID 使用现有 `10000000-0000-4000-8000-000000000012/013`；四个能力沿用 `20000000-0000-4000-8000-000000000082` 至 `085`，对应 read/manage/transfer/assignments，执行时核对未占用。队员仅新增 read/self；管理仅新增 read/manage 的 assigned_team；超管四项 global；其余角色无新增名单授权。扩展三个 RBAC CHECK 接受 assigned_team（账号覆盖必须带 UUID）。
- [ ] 同时更新 Alembic import、manifest 及头断言，运行本任务测试和 `tests/db/test_migrations.py tests/db/test_rbac_seed.py` 全通过；检查迁移未导入会随版本变化的业务种子函数。保存独立提交和结果。

## Task 2：名单访问边界与角色范围契约

**Files:** 新建 `apps/api/app/team_management/{schemas,errors,access}.py`、`apps/api/tests/services/test_team_access.py`、`apps/api/tests/unit/test_team_contracts.py`。修改 `apps/api/app/rbac/{policy,schemas,service,router}.py`，相关 `tests/api/test_permissions.py`。

**Interfaces:** `TeamAccessService(db: Session)`；`require_team(actor: AuthContext, team_id: UUID, action: TeamAction) -> ProfessionalTeam`；`visible_team_ids(actor: AuthContext, action: TeamAction) -> Select[tuple[UUID]]`（返回授权 SQL 子查询）；`require_self(actor: AuthContext) -> UUID`；`context(actor: AuthContext) -> TeamContext`。schemas 定义全部共享 DTO；errors 定义后续稳定错误码。

- [ ] 写 `test_scope_requires_live_role_and_assignment`：给无绑定账号任意 global allow 后 `assert access_denied`；A 对 B 队访问 `assert error_code == 'team_not_found'`，与随机 UUID 同码同中文；显式 deny 对 manager 和 superadmin 均 `assert allowed is False`。纯队员只能本人，普通管理员有旧教学目录权仍 `assert roster_access is False`。
- [ ] 写 `test_assigned_team_override_is_object_specific`：`assert scope_id_required`，无关能力使用新 scope 拒绝；有有效绑定且允许规则可用、有效期结束／deny／角色撤销不可用。合约测试断言：额外生效日期字段 422、原因空白或 201 字拒绝、候选 page_size 21 拒绝。
- [ ] 运行 `$PY -m pytest tests/services/test_team_access.py tests/unit/test_team_contracts.py -q`，确认访问服务／scope 缺失造成失败。
- [ ] 实现 SQL 授权过滤：先标准可用账号、有效角色，再自本人／实时绑定／实际超管分支，再 deny-first RBAC；manager 不能用额外 global allow 绕过绑定。superadmin 没有相应能力也拒绝。`require_team`／`visible_team_ids` 只服务管理名单，不接受纯队员 self 权限；纯队员经 `require_self` 只查询 person_id 本人关系。team 专用 token 包含真实 `assigned_team:<id>`，不能将新 scope 广泛并入其他领域的 global。
- [ ] 更新 RBAC 允许 scope 表、对象存在与委派校验、中文 label 和权限目录；新增有 `permissions.manage` 才可读的 `/permissions/directory/teams` 分页选项。team 权限覆盖写入统一锁定受影响账号，配合任务 3 的账号锁；其他权限路径保持不变。
- [ ] 运行本任务测试及 `$PY -m pytest tests/api/test_permissions.py -q`；记录 role、binding、有效能力任意一项不足均拒绝的证据（有角色基础授权不必额外创建 override），提交本任务增量。

## Task 3：幂等入离队、原子调队与管理绑定

**Files:** 新建 `apps/api/app/team_management/{write_support,membership_service,assignment_service}.py`、`apps/api/tests/services/{test_team_memberships,test_team_assignments}.py`、`apps/api/tests/db/test_team_concurrency.py`。修改 `apps/api/app/audit/service.py` 的动作与安全字段白名单。

**Interfaces:** `TeamMembershipService(db: Session)` 提供 `join(actor, team_id, input: JoinTeamInput)`、`leave(actor, membership_id, input: LeaveTeamInput)`、`transfer(actor, membership_id, input: TransferTeamInput)`；`TeamAssignmentService(db: Session)` 提供 `assign(actor, team_id, input: AssignManagerInput)`、`unassign(actor, assignment_id, input: UnassignManagerInput)`，均返回 `TeamMutationResult`，actor 为 `AuthContext`、资源 ID 为 UUID。服务自身保持事务、访问验证和成功审计，不依赖路由先查保证安全。

- [ ] 写 `test_retry_returns_original_result_without_duplicate_audit`：`assert result1 == result2`，`assert membership_count == 1`、`assert success_audit_count == 1`；同 actor/key 改 action 或 payload：`assert code == 'team_operation_conflict'`。撤销当前权限后旧 key：`assert private_404`。
- [ ] 写 `test_transfer_rollback_and_old_membership_rejection`：失败 `assert before == after`；同队目标拒绝；同日离队重入生成不同 ID，旧 ID 不得关闭新关系；冻结 UTC 时间在上海跨日前后验证 `assert joined_on == expected_china_date`。调队必填原因只存历史，不写审计全文。
- [ ] 独立数据库连接 barrier 测试：双队并发入同人 `assert sorted(statuses) == [200,409]`、开放关系一条；同 key 两请求结果相同；两个反向调动无死锁、调队撞离队最多一个旧关系变更成功；模拟成功审计写入异常 `assert no_business_or_operation_rows_committed`。超管转队和管理绑定才允许，manager 的同负责人跨队仍拒绝。
- [ ] 运行 `$PY -m pytest tests/services/test_team_memberships.py tests/services/test_team_assignments.py tests/db/test_team_concurrency.py -q`，确认 RED 后实现。
- [ ] `write_support.py` 提供 `lock_operation(db, actor_account_id, idempotency_key) -> None`（SHA-256 命名空间 `team-operation` 的事务 advisory lock）、`lock_participants(db, person_ids: Sequence[UUID], account_ids: Sequence[UUID]) -> None`（排序锁并刷新 ORM）、`fingerprint(action: str, payload: Mapping[str, object]) -> str`（UUID、去空白原因、明确字段稳定 JSON 摘要）。不包含服务器当前日期，避免跨日重试变成新操作。
- [ ] 固定锁顺序：操作 advisory → 全部相关 Person 按 UUID → 全部相关 UserAccount 按 UUID → 若改变角色才锁 Role/UserRole → GameProject 按 UUID → ProfessionalTeam 按 UUID → 当前成员／绑定行按 UUID；先只读定位 IDs，锁后刷新、重查，不在已锁 team 后追加 person/account 锁。利用账号锁协调角色／停用／权限撤销，team 锁协调绑定撤销，project 锁协调停用。事务内先重验授权再重放；重放仅取已存结果 UUID，不要求旧成员仍开放。新操作再校验原关系 ID、资格及目标状态。
- [ ] 入队必须标准可用、启用、未删除、具有 team_member、当前无队；无需额外要求手机号或学生档案。leave 可结束本队停用账号的关系，不要求目标能登录。管理绑定要求启用的 team_manager；去重及当前关系由数据库兜底。使用 `atomic_write_transaction`，成功业务、操作账本、审计一并提交；转队关闭旧行与插入新行同事务，返回封闭中文冲突，不带其他队名称。
- [ ] 运行上述测试及 Ruff / mypy 对新增文件检查，所有并发用真实独立连接，不把死锁当正常重试掩盖；提交本任务增量。

## Task 4：停用、删除、角色撤销和旧目录兼容

**Files:** 新建 `apps/api/app/team_management/lifecycle.py`、`apps/api/tests/services/test_team_lifecycle.py`。修改 `apps/api/app/auth/service.py`、`apps/api/app/rbac/service.py`、`apps/api/app/teaching/{catalog_service,schemas,router,errors}.py`；扩展 `tests/api/{test_accounts,test_permissions,test_teaching}.py`、`tests/db/test_team_concurrency.py`。

**Interfaces:** `TeamLifecycleService(db)` 的 `before_role_removal(account_id: UUID, role_key: str, actor_account_id: UUID|None, occurred_at: datetime) -> None`、`close_for_deleted_account(account_id, actor_account_id, occurred_at) -> None`、`assert_team_clear(team_id: UUID) -> None`、`assert_project_clear(project_id: UUID) -> None`。这些帮助函数只能加入调用方已持有账号／目录锁的事务，不自行提交，不追加身份锁。

- [ ] 写 `test_disable_delete_and_role_removal_have_distinct_effects`：停用 `assert membership_still_open` 且会话失效；重启仍同关系；删号 `assert membership_and_bindings_closed` 且历史存在；移除 team_member 在队时409、不在队成功且 student role 保留；移除 team_manager 关闭全部绑定，再授予 `assert assignments_not_restored`。
- [ ] 写 `test_legacy_catalog_cannot_bypass_roster_guard`：有任一开放成员或绑定时，旧 API 停队／停分部／取消分部均409且状态不变；更换 team.project_id 仍拒绝；普通管理员不能改变 is_team_division，但原目录权限不被无故删除。注入审计失败 `assert role_and_relationship_changes_rolled_back`。
- [ ] 扩展并发：撤 manager role／override deny／解除绑定撞 join，结果须对应某一完整串行顺序，无权限失效后新写入；账号删除撞 join、停队撞 join 同理。运行 `$PY -m pytest tests/services/test_team_lifecycle.py tests/db/test_team_concurrency.py -q` 确认 RED。
- [ ] 在原有 people→accounts 锁后调用生命周期；关闭原因、结束时间和安全审计同事务。删号不 cascade 历史，不改停用保留归属规则；所有角色修改入口共用服务，不能仅路由加判断。生命周期关闭是原账号／角色事务结果，不要求用户另外提交 team 操作键。
- [ ] 目录 mutation 改为 project→team 固定锁序，守卫锁后查开放关系，不为了停队去锁成员账号。`is_team_division` 请求字段可省略以保持旧客户端兼容；只有字段值实际改变时校验真实超管及 `projects.manage`，普通目录更新保留原标志。关联未清空不可停用／取消，旧资料可继续读取。
- [ ] 运行本任务及原账号、权限、教学回归，记录无额外权限和无历史丢失，提交增量。将 `rbac/service.py` 所有权交给任务 5/7 之前确认任务 2 修改已合并。

## Task 5：安全查询、候选搜索、人员投影与 API

**Files:** 新建 `apps/api/app/team_management/{query_service,projection,router,dependencies}.py`、`apps/api/tests/api/{test_team_management,test_team_privacy}.py`、`apps/api/tests/services/test_team_queries.py`。修改 `apps/api/app/api/router.py`、`apps/api/app/identity/{service,schemas,router}.py` 及 `tests/api/test_people.py`。

**Interfaces:** `TeamQueryService(db)` 提供 `list_teams(actor, *, project_id: UUID|None, page: int, page_size: int) -> TeamPage[TeamSummary]`、`list_memberships(actor, team_id, *, status: Literal['current','left','all'], page: int, page_size: int) -> TeamPage[TeamMemberView]`、`candidates(actor, team_id, *, kind: Literal['member','manager'], query: str, cursor: str|None, page_size: int=20) -> TeamCandidatePage`（page_size 1–20）、`my_team(actor, *, page: int, page_size: int) -> MyTeamResponse`、`list_assignments(actor, team_id, *, page: int, page_size: int) -> TeamPage[TeamAssignmentView]`。actor 为 AuthContext、team_id 为 UUID。projection 提供 `project_person_teams(db: Session, actor: AuthContext, person_ids: Sequence[UUID]) -> dict[UUID,PersonTeamProjection]`，严格批量读取。

- [ ] 写 `test_private_team_surfaces_and_projection`：跨队列表／统计／候选／详情／历史／写入 `assert status == 404` 且与不存在相同；递归遍历 JSON `assert forbidden_keys.isdisjoint(all_keys)`；manager 看自己队历史，不出现其他队关系或调队原因；本人保留所有本人历史，取消分部后历史仍可读。无授权人员档案投影 `assert {'visibility':'denied','current':None} == projection`。
- [ ] 写 `test_search_and_pagination_do_not_leak_or_skip`：空白查询422；单字、昵称、首字母（复用现有 name_search，含大小写全角规范化）、同名不同 UUID、多页都可选择；候选只含标准启用无队 team_member，manager 候选不排除已负责别队的人。`assert len(items) <= 20` 且无 total；授权过滤在计数／分页之前，多个角色 `assert distinct_person_count == expected`。
- [ ] 写 `test_people_projection_has_bounded_queries`：20 与 100 人页查询次数相同量级，无逐人查库；role filter 和 role counts 在原 people 可见范围内工作，在队人数另命名且仅计 roster 可见范围。删号历史状态 deleted，不提供隐私链接；age 根据上海业务日计算，仅返回年龄与未成年布尔。
- [ ] 运行 `$PY -m pytest tests/api/test_team_management.py tests/api/test_team_privacy.py tests/services/test_team_queries.py -q` 确认 RED，然后实现上列查询与投影。
- [ ] 所有候选先验证目标队 manage（manager 候选为 assign_managers），仅选取最小账号／姓名／DOB 列，无邮箱密码等列。普通字查询在 SQL 中做字面包含匹配并转义 `%/_`；首字母复用 `name_initials`，对已通过资格过滤的最小投影按 UUID 分批扫描 200 行，匹配后最多返回请求 page_size（上限 20），游标指向最后消费行，不先取 20 再过滤、不全量载入，不向客户端暴露扫描数；查询改变前端重置游标。历史队伍列表保留有历史关系但已停用／取消分部用途的项目，不能因当前标志过滤丢掉已授权历史。
- [ ] 注册前缀 `/api/v1/team-management`：GET `/context`、`/teams`、`/teams/{team_id}/memberships`、`/teams/{team_id}/candidates`、`/teams/{team_id}/manager-candidates`、`/teams/{team_id}/manager-assignments`、`/me`；POST `/teams/{team_id}/memberships`、`/memberships/{membership_id}/leave`、`/memberships/{membership_id}/transfer`、`/teams/{team_id}/manager-assignments`、`/manager-assignments/{assignment_id}/end`。依赖沿用 `require_standard_account`、CSRF、ProblemError；静态路由先注册。GET `/context` 对正常无相关权限账号返回全 false，不返回名录。
- [ ] 稳定错误：私有404 `team_not_found`；409 `team_member_ineligible`、`team_already_assigned`、`team_membership_stale`、`team_inactive`、`team_has_active_relationships`、`team_operation_conflict`；422输入约束使用清晰中文，其他内部错误不泄露堆栈。现有 people API 增加独立 `roles` 标签、`role` 过滤和 `team` 投影；counts 增加两种角色人数及已授权在队人数，不能给 manager 借此打开 people.list。
- [ ] 服务测试、API 隐私测试及旧 people 回归全通过；读取不存在／不可见对象失败不写成功审计，提交增量。

## Task 6：队伍管理与个人归属页面

**Files:** 新建 `apps/web/src/teams/api.ts`、`keys.ts`、`TeamManagementPage.tsx`、`MyTeamPanel.tsx`、`TeamActionDialogs.tsx`、`TeamManagerPanel.tsx`、`teams.css` 及每个 TS/TSX 的相邻 `.test.ts(x)`；提取 `apps/web/src/teaching/TeachingCatalogForms.tsx`，修改 `TeachingCatalogPage.tsx`、`teaching/api.ts` 和相邻测试（不在本任务改 router／AppShell）。

**Interfaces:** `api.ts` 镜像任务 2 DTO，导出与任务 3/5 同名的异步 HTTP 操作（不传 actor）；`teamKeys(accountId: string)` 产出以 `['teams',accountId]` 开头的 context、teams、members、candidates、assignments、self keys。`TeamManagementPage({accountId}: {accountId:string})`、`MyTeamPanel({accountId}: {accountId:string})`；子面板传 teamId、allowedActions、onChanged 回调，不能自行扩权。

- [ ] 写 `test_lost_response_retries_same_operation`：第一次响应丢失、重试 `expect(second.idempotency_key).toBe(first.idempotency_key)`，成功只显示一条记录；409保留输入并展示原因；关系冲突先刷新权威名单，不能换新键自动重新加人。正常重新开始的新操作才生成新 UUID。
- [ ] 写 `test_only_eligible_actions_and_minimal_fields_render`：manager 无调队／管理分配／目录编辑按钮；member 只本人信息；纯 superadmin 依 capabilities 显示；离队文案为“仅结束队伍归属，不删除账号、学员身份或历史”；无归属与无查看权限分别展示；同名候选可通过昵称和年龄区分，不增加联系方式。
- [ ] 运行 `npm test -- src/teams src/teaching/TeachingCatalogPage.test.tsx`，确认组件缺失对应 RED。
- [ ] 实现分部、队伍、状态筛选／授权数量／历史分页；增减名单、超管调队及 manager assignment 面板。没有候选提示先由有权限人员授予队员角色，不自动走创建账号或报名。目录表单从原文件提取保持旧 props/行为，复用原目录 API；is_team_division 切换只在真实超管且有目录能力时展示。
- [ ] 对失败请求保留表单与键；不确定成功时先刷新查询再用原键重试。成功后刷新 team、个人归属和人员中心相关 keys。请求对账不创建新记录；前一账号迟到的响应不能写入新账号 cache。中文日期沿用既有安全格式化，仅展示四位年年月日。
- [ ] 加窄屏卡片或局部横滚、可滚动弹窗、焦点与可点击区域；选人输入不被软键盘遮挡。运行本任务测试、`npm run build` 和 `npm run lint` 全通过，提交明确文件，交接 router 集成。

## Task 7：菜单、权限配置、人员中心与缓存接入

**Files:** 修改 `apps/web/src/app/{router.tsx,router.test.tsx}`、`apps/web/src/layout/{AppShell.tsx,AppShell.test.tsx}`、`apps/web/src/people/{api.ts,PeoplePage.tsx,PeoplePage.test.tsx,PersonDetailPage.tsx,PersonDetailPage.test.tsx,SelfProfilePanel.tsx,SelfProfilePanel.test.tsx,presentation.ts}`、`apps/web/src/settings/{api.ts,PermissionsPage.tsx,PermissionsPage.test.tsx,AuditPage.tsx,AuditPage.test.tsx}`；必要的 auth 清理入口只在 `apps/web/src/auth/{AuthProvider.tsx,AuthProvider.test.tsx}` 窄改。

**Interfaces:** 新路由 `/people/teams`；个人中心既有 SelfProfilePanel 嵌入 MyTeamPanel；AppShell 消费 `TeamContext` 独立能力，不以 people.list/teaching.read 替代。人员 DTO 接任务 5，permissions 的 assigned_team 下拉接任务 2 目录 endpoint。

- [ ] 写 `test_team_navigation_without_global_people_permission`：有效绑定 manager 看人员中心→队伍管理，但不能看全员名单；纯 member 在个人中心看我的队伍；既有管理员无新名单入口，student+member 原菜单不消失；新角色人数与原人数卡互不覆盖。
- [ ] 写 `test_role_filter_and_denied_projection_are_distinct`：两角色筛选可用、多身份正确展示；`expect(screen.getByText('无查看权限'))`，而非错误的未分队；assigned_team 覆盖必须选队且中文范围名称正确显示，授予多角色不覆盖旧角色。
- [ ] 更新并测试 AuditPage 的新动作、实体类型与变更字段中文映射，保留操作人／时间；`expect(visibleRawActionCodes).toEqual([])`，调队原因全文不进审计，安全审计入口仍只有超管可见。
- [ ] 写 `test_revocation_and_account_switch_clear_team_data`：撤绑定后 context false 或资源404／403立即清队数据并关闭编辑；401沿用注销清理；页面重新聚焦强制刷新 context，旧账号待完成请求不可使新账号看见名单。无推送承诺：别处撤权以服务端即刻拒绝、前台下一请求／聚焦刷新体现。
- [ ] 运行 `npm test -- src/app/router.test.tsx src/layout/AppShell.test.tsx src/people src/settings/PermissionsPage.test.tsx src/auth/AuthProvider.test.tsx` 确认 RED，再接入口与权限 UI。context 查询账号隔离、focus refetch；资源404清当前资源 cache，context撤权清全部 roster cache，不影响其他仍有权队伍的服务端范围。
- [ ] 运行以上用例及 Web 全测试、build、lint；检查父菜单默认折叠、选中才展开等既有行为不退化，提交增量。

## Task 8：集成验收、独立审查和 NAS 发布

**Files:** 新建 `apps/api/app/cli/seed_team_management_e2e.py`、`apps/api/tests/cli/test_team_management_e2e.py`、`apps/web/tests/team-management.spec.ts`、`scripts/run-team-management-e2e.sh`、`docs/verification/2026-09-29-team-management.md`；修改 `scripts/check.sh` 接入新 launcher。不修改 NAS secrets、证书、端口或真实人员名单。

**Interfaces:** 隔离 fixture 包含超管、各负责一队的 A/B、多队 manager、两个 manager 同队、未绑定 manager、纯 member、student+member、普通 admin、disabled/deleted 历史人；采用现有 E2E 数据库安全守卫和隐藏测试凭据方式。launcher `bash scripts/run-team-management-e2e.sh` 按桌面、手机串行重建 fixture 并运行专用 spec，不触碰线上。

- [ ] 写 launcher 安全测试：指向非隔离库 `assert rejected_before_reset`；E2E 断言登录不同角色、加人／退队／调队、撤权后旧页面拒绝、网络丢响应重试、人员中心与本人同步、窄屏无页面溢出和软键盘可操作。先确认缺实现时失败，再加入 fixture 与 runner。
- [ ] 主代理独占数据库测试运行，完成 `$PY -m pytest tests -q`（API 工作目录）；然后 Ruff 全 app/tests、mypy `--config-file apps/api/pyproject.toml apps/api/app`（发布源根目录），Web `npm test -- --maxWorkers=4`、`npm run build`、`npm run lint`。保留完整结果；失败先定位，不能降低授权／并发断言或用忽略旧失败假装全通过。
- [ ] 运行 `bash scripts/run-team-management-e2e.sh` 及受影响旧 core／teaching／registration E2E，禁止并行重置测试库。验证迁移基线升级、全新建库、备份在隔离库实际恢复；真实手机未测试时明确记录，不以桌面模拟代替宣称通过。
- [ ] 独立代码审查覆盖授权 SQL、所有生命周期入口、锁顺序、幂等重放、中文审计隐私、分页／N+1、旧目录兼容；修正发现后补对应失败测试再重跑。将规格第 11 节 12 个验收场景逐项绑定结果，检查未增加禁止范围功能。
- [ ] 保存验证报告和本轮提交／增量清单，确认待部署源包含本轮完整已验收代码，且没有混入未审核其他功能；核对 root 部署脚本与源 manifest 使用同一新迁移头，索引内不混入用户文件。
- [ ] 从 `/Users/skymoonzyj/Documents/new project3` 运行 `scripts/deploy-nas.sh --source '<已审核发布源绝对路径>'`。默认仍为 `.nas-release-sync`；若隔离执行产生其他源，必须是完整已测试源，由脚本部署，不手工复制到 NAS。脚本前置备份、迁移、恢复演练失败即停止发布，不改参数跳过。
- [ ] 只读核验 `.active-release`、源码与镜像标识、`0061_team_management` 迁移头（或已核实顺延头）、容器／API／调度健康、线上菜单资源、HTTPS 原 `18080` 入口和加密备份实际恢复证据。不用 `current` 旧链接作为活动版本证明，不在生产做模拟入队。报告已完成内容、检查数量、发布 ID、真实设备或其他未验收项。

## 主代理自审与交接

- 规格覆盖：第 1–3 节→任务 1/2；第 4–5 节→任务 2/5/6/7；第 6–7 节→任务 3/4；第 8–9 节→任务 3/5/6/7；第 10 节→全局范围限制；第 11 节→任务 8；未发现未分配需求。
- 接口一致：服务使用 AuthContext，HTTP 仅接资源 ID 和 DTO；幂等结果只含 UUID；人员投影显式区分无权／无队；前后端及种子使用同一角色／能力键。
- 五项 Review Focus 均已在归属任务安排断言。计划决定边界、接口和测试，不复制现有业务实现；执行时仍需先读取实际源并确认基线未漂移。
- 用户已于 2026-09-29 确认「开始」。实施已在隔离工作区按实际依赖并行推进；本地验证、独立审查和 NAS 发布仍须全部完成后，才能标记交付完成。
