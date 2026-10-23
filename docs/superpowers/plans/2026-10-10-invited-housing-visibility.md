# 邀请人及组织查看受邀人员住宿 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 邀请人和超管指定的组织负责人能够在独立“我的邀请”页面查看其受邀人员的住宿摘要，同时保持住宿管理、财务及其他人员数据隔离。

**Architecture:** 在现有邀请来源快照上增加组织负责人关联，用独立 RBAC 开关与每次请求的关系范围共同授权。新的批量住宿投影只读白名单字段，不复用带室友的个人门户接口；前端以账号及来源隔离查询，并在焦点及 30 秒重验期间隐藏旧数据。

**Tech Stack:** FastAPI、SQLAlchemy、PostgreSQL、Alembic、React、TypeScript、TanStack Query、Zod、Vitest、Playwright。

**Spec:** `docs/superpowers/specs/2026-10-10-invited-housing-visibility-design.md`（用户已确认）。

## Global Constraints

- “受邀人员”指已完成注册、已写入 `StudentReferralBinding` 的学员，不含待审批或待验证的申请，不扩展多级邀请链。
- 只提供当前住宿摘要及必要的最近一次退房信息，不开放完整住宿历史、导出、支付记录或分成金额。
- 不展示室友、身份证和手机号；白名单也排除邮箱、住宿备注及完整人员档案。
- 日期按中国时间显示 `YYYY-MM-DD`，不显示小时分钟；计划日期缺失显示“未确定”，长期入住显示“长期入住”。未发生的实际日期显示“—”。
- 分页默认每页 20 人，上限 100；总人数、页数和筛选均在授权范围内计算。
- 管理负责人仅限真实 `super_admin` 角色且具有 `revenue_share.manage` 全局权限；普通管理员、财务、负责人本人均不能因此操作负责人关联。
- `referrals.read_invited_housing` 标准角色默认 `self`；显式禁止优先。global/person allow 不能扩大邀请关系范围。
- 移除负责人提交后，所有后续读取请求立即重新判定；显示页面定期重新校验（最长 30 秒），失败时不继续展示过期私有列表。
- 停用邀请码保留既有邀请关系；停用组织、移除负责人或禁用负责人账号停止对应组织授权。机构创建人不自动成为负责人。
- 不把住宿列表写入持久化浏览器存储；不新增共享组织账号，不改邀请归属或结算受益人。
- 发布源为 `/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3`；保留现有脏文件和已完成的房态筛选修复，不自动 stage/commit，避免提前触发部署钩子。
- 本计划审核前不写产品代码、执行迁移或发布。实施后必须从主仓库运行规定的 NAS 脚本；失败阻断交付，不绕过脚本。

## Review Focus

- 同一人仍负责 A 组织、但 B 组织关联被撤销：B 参数和列表失效，不能因总 capability 仍为 true 继续显示 B；任务 2、4 覆盖。
- 超管预检查后角色被移除、目标账号被禁用，或两人同时授权：事务内重验、固定锁顺序及唯一有效映射；任务 1 覆盖。
- 调房旧记录、过期未确认预约、实际退房时间早于最后修改时间、同时预约：摘要选择与 SQL 状态筛选一致；任务 2 覆盖。
- 断网/500、来源切换、退出重登、晚到响应：隐藏旧私有数据，过期响应不能恢复缓存；任务 4 覆盖。
- 两个同名组织/人员、person deny 跨分页、已删除受邀人员、异常多个当前入住：以 ID 判定，先授权后计数，异常安全报错；任务 1、2、3 覆盖。

## 文件与执行边界

任务 1 负责关联模型、迁移、负责人管理 API、权限种子及审计白名单；任务 2 负责邀请范围和住宿摘要查询；任务 3 负责超管配置 UI；任务 4 负责个人入口、刷新隔离及浏览器验收。任务 2、3 消费任务 1 的接口；任务 4 消费任务 2 的接口。父代理负责共享文件整合、串行数据库测试及发布。

新增模块保持独立：`app/revenue_share/organization_manager_*` 处理负责人管理；`app/referrals/` 处理邀请授权和分页；`app/housing/invited_projection.py` 只处理已授权人员的摘要；`src/invitations/` 只处理个人页面，不调用财务 API。

API 固定采用 `/api/v1/referrals/me/...`；浏览器路径为 `/me/invitations`，不混用。全部新增读接口 `Cache-Control: no-store`，新写接口沿用会话/CSRF，错误响应使用现有 ProblemDetails。组织范围不合法统一私有 404；整项功能禁止为 403；登录失效为 401。

---

### Task 1: 组织负责人关联、权限种子及超管管理 API

**Files:**
- Modify: `apps/api/app/revenue_share/models.py`, `apps/api/app/rbac/seed.py`, `apps/api/app/rbac/service.py`, `apps/api/app/api/router.py`, `apps/api/app/audit/service.py`
- Create under `apps/api/app/revenue_share/`: `organization_manager_service.py`, `organization_manager_schemas.py`, `organization_manager_router.py`
- Create: `apps/api/migrations/versions/0065_invited_housing_visibility.py`
- Modify: `apps/api/app/housing/models.py`, `scripts/db-schema-manifest.sh`, `apps/api/tests/db/test_migrations.py`, `apps/api/tests/cli/test_operations_docs.py`, `apps/api/tests/cli/test_local_container_acceptance_launcher.py`
- Test: `apps/api/tests/services/test_organization_manager_assignments.py`, `apps/api/tests/api/test_organization_managers.py`, `apps/api/tests/api/test_permissions.py`, `apps/api/tests/unit/test_audit_metadata.py`

**Interfaces:**
- `RevenueShareOrganizationManagerAssignment`: `id, organization_id, account_id, granted_at, granted_by_user_id, revoked_at, revoked_by_user_id`。
- `OrganizationManagerService(db).list_managers(*, actor_user_id: UUID, organization_id: UUID, request_id: str) -> list[OrganizationManagerResponse]`。
- `.list_candidates(*, actor_user_id: UUID, q: str, page: int, page_size: int, request_id: str) -> OrganizationManagerCandidatePage`。
- `.grant_manager(*, actor_user_id: UUID, organization_id: UUID, account_id: UUID, request_id: str, occurred_at: datetime | None = None) -> OrganizationManagerResponse`；`.revoke_manager(...) -> None`，相同关键字参数。
- 候选 DTO 仅 `account_id, full_name, nickname`；UI 以 UUID 作为账号标识。负责人 DTO 增加 `organization_id, granted_at`，不包含联系方式。
- `GET /revenue-share/organization-manager-candidates?q=&page=1&page_size=20`；`GET/PUT/DELETE /revenue-share/organizations/{organization_id}/managers[/{account_id}]`。PUT 重复有效授权返回同一映射且不重复审计；DELETE 重复撤销为 204 且不重复审计。

- [ ] **Step 1: 编写失败测试。** `test_real_admin_and_finance_cannot_manage_organization_managers` 使用真实普通 admin（现有 `platform_users['admin']` 实际是超管，不能用于负例）；`test_grant_twice_has_one_active_assignment_and_one_audit`；`test_revoke_then_regrant_preserves_history`；`test_write_rechecks_actor_role_and_target_status`；独立连接并发 grant 最终仅一个有效映射；停用组织不可新增、可撤销。
- [ ] **Step 2: 运行 RED。** 在 `apps/api` 用隔离 `TEST_DATABASE_URL` 执行 `python -m pytest tests/services/test_organization_manager_assignments.py tests/api/test_organization_managers.py -q`；应因模型/接口缺失失败，不接受连接错误作为 RED。
- [ ] **Step 3: 实现模型与迁移。** revision `0065_invited_housing_visibility`，down revision `0064_cloud_auth_builtin_import`（实施前核对是否有新迁移）。新表 `revenue_share_organization_manager_assignments`，FK RESTRICT；撤销人/时间成对、撤销不早于授权；部分唯一 `(organization_id, account_id) WHERE revoked_at IS NULL` 与反向访问索引。新增绑定来源 person/org 的部分索引，以及住宿 `(person_id, actual_check_out_at DESC, id) WHERE status='checked_out'` 索引。不得回填机构创建人为负责人，不改绑定、付款或分成行。
- [ ] **Step 4: 实现权限与管理服务。** 新 permission ID `20000000-0000-4000-8000-000000000086`，中文“查看受邀人员住宿”，sensitive=true；13 个现有标准角色各有 self grant，将其加入 `SELF_SCOPE_ONLY_PERMISSIONS` 阻止隐式 global 默认授权。账号追加支持 self/global/person，其他范围 422。所有管理读写真实超管＋global manage；写事务以 Person→UserAccount 的既有锁顺序重验操作者与目标，再锁组织/映射，使用唯一约束防重复。不允许给已删除/禁用账号新增关联。
- [ ] **Step 5: 实现最小响应、审计及防回滚丢数据。** 审计动作 `referral_organization_manager.assign/revoke/list/candidates`，UUID 型 `organization_id/target_account_id/assignment_id` 白名单；拒绝携带任意姓名/联系方式元数据。迁移种子与运行种子一致；downgrade 在负责人历史或自定义新权限规则存在时拒绝破坏性降级。更新 schema manifest 和 head 断言，保留既有0064测试。
- [ ] **Step 6: 运行 GREEN 与迁移验证。** 重跑 Step 2；再执行 `python -m pytest tests/db/test_migrations.py tests/api/test_permissions.py tests/unit/test_audit_metadata.py tests/cli/test_operations_docs.py tests/cli/test_local_container_acceptance_launcher.py -q`。核验升级前后已有邀请/收益数据不变、重复有效关联被约束拒绝、无业务数据时可安全降级。数据库测试只允许单进程串行运行。
- [ ] **Step 7: 独立审查本任务差异。** 修复审查问题后记录证据；不 stage/commit，不单独部署尚未组合验收的功能。

### Task 2: 邀请关系授权、白名单住宿投影和分页接口

**Files:**
- Create under `apps/api/app/referrals/`: `__init__.py`, `access.py`, `query_service.py`, `schemas.py`, `errors.py`, `router.py`
- Create: `apps/api/app/housing/invited_projection.py`
- Modify: `apps/api/app/api/router.py`, `apps/api/app/portal/context_service.py`, `apps/api/app/portal/schemas.py`, `apps/api/app/audit/service.py`
- Test: `apps/api/tests/services/test_invited_housing_access.py`, `apps/api/tests/services/test_invited_housing_projection.py`, `apps/api/tests/api/test_invited_housing.py`
- Modify tests: `apps/api/tests/services/test_portal_context.py`, `apps/api/tests/api/test_portal.py`

**Interfaces:**
- `InvitedHousingStatus = Literal['unarranged','reserved','check_in_pending','checked_in','checkout_pending','checked_out']`。
- `InvitationSource = {source_kind: 'person'|'organization', organization_id: UUID|null, name: str}`；`InvitationSourcesResponse = {sources: list[InvitationSource]}`。
- `InvitedHousingStay`: `building_name, floor_name, room_name, duration_mode` 与四个 `planned_check_in_at/planned_check_out_at/actual_check_in_at/actual_check_out_at`；时间带时区，除计划入住外均可空，沿用数据库约束。
- `InvitedHousingSummary = {status, stay: InvitedHousingStay|null, next_planned_stay: InvitedHousingStay|null}`。
- `InvitedPersonHousing = {person_id, full_name, nickname, source: InvitationSource, housing: InvitedHousingSummary}`；`InvitedHousingPage = {as_of, items, total, page, page_size}`。Pydantic DTO 白名单且 extra=forbid。
- `InvitedHousingAccess(db).has_sources(*, user_id: UUID, actor_person_id: UUID, as_of: datetime) -> bool`；`.list_sources(...) -> list[InvitationSource]`；`.authorized_people_statement(*, user_id, actor_person_id, source_kind: Literal['all','person','organization'], organization_id: UUID|None, as_of) -> Select`，返回邀请绑定与姓名/昵称所需列，不加载完整人员对象。
- `invited_housing_status_expression(person_id: ColumnElement[UUID], *, as_of: datetime) -> ColumnElement[str]`；`InvitedHousingProjectionService(db).summaries_for_people(*, authorized_person_ids: Collection[UUID], as_of: datetime) -> dict[UUID, InvitedHousingSummary]`。
- `InvitedHousingQueryService(db).list_housing(*, user_id, actor_person_id, source_kind, organization_id, status, page, page_size, as_of, request_id) -> InvitedHousingPage`。
- `GET /referrals/me/sources`、`GET /referrals/me/housing?source_kind=all|person|organization&organization_id=&status=&page=1&page_size=20`。默认 all 为本人及有效组织的并集；organization 必须有 organization_id，其他类型禁止该参数。不新增任意 person_id 详情、历史或导出端点。

- [ ] **Step 1: 编写失败的授权/API测试。** A/B 邀请人、双组织双负责人、只有 student/registered_user 角色的负责人、无关机构创建人。断言跨组织为不暴露存在性的404；global/person allow 不扩大关系；self/global deny 阻止功能；person deny 在总数及分页前排除；禁用账号、撤负责人、停用组织后的下一请求拒绝；邀请码停用不抹去已有关系；已删除学员和未完成注册申请不进入计数；无受邀人员与未安排住宿两种空状态不同。
- [ ] **Step 2: 编写失败的投影测试并运行 RED。** 覆盖六种状态、当前＋下一预约、调房目标优先、取消忽略、最早预约按时间＋ID、最近退房按实际时间而非 updated_at、跨中国日期边界、长期入住；SQL 状态筛选与投影一致；构造两个逻辑当前入住并断言安全错误，不因分页/状态过滤静默任选。运行 `python -m pytest tests/services/test_invited_housing_access.py tests/services/test_invited_housing_projection.py tests/api/test_invited_housing.py -q`。
- [ ] **Step 3: 实现每请求关系授权。** 先校验 active 当前账号/未删除人员和 `decide(permission, {'global', f'self:{actor_person_id}'})`；个人范围由绑定快照 person ID 确定，组织范围以启用组织＋未撤映射 EXISTS 限定；排除有效 person deny，再计数/分页。`has_sources` 另外接受本人邀请码或历史个人绑定，无被邀请人时仍可进入；不能用邀请码字符串、组织名称或 created_by 授权。
- [ ] **Step 4: 实现批量住宿投影及查询。** 仅选择房号快照和白名单日期；当前入住取全部以检查重复；预约和退房分别用按 person 分组的 row_number 取最早/最近一条，不拉取完整历史。优先级：当前 checked_in/checkout_pending → 最早 planned（到期但未确认=check_in_pending）→ 最近真实 checked_out → unarranged。存在当前时下一预约独立呈现；计划离开不冒充实际离开。用一致 SQL 表达式在分页前筛选，固定排序 `bound_at DESC, student_person_id ASC`；异常多个当前入住返回不含个人信息的409 `invited_housing_inconsistent`。
- [ ] **Step 5: 接入路由、capability 和审计。** 采用任务1权限与管理映射；`PortalContextResponse.invitations_available: StrictBool`，get_context 调用 has_sources。分页/来源读取的审计不保存响应/房号/姓名，住房列表动作 `referrals.housing.read`，只记录允许的来源类型、组织ID及结果数。失败响应和成功响应均禁止缓存，不赋予 housing.read 或 revenue_share.read_self。
- [ ] **Step 6: 运行 GREEN 和泄露/性能回归。** 重跑 Step2，再执行 `python -m pytest tests/services/test_portal_context.py tests/api/test_portal.py tests/services/test_housing_portal.py -q`。断言 JSON 不含室友、电话、证件、邮箱、备注、分成及付款；来源授权完成后仅做有界批量查询，1人与20人的 SQL 次数相同，100人分页也不逐人查询；混入未授权数据不改变已授权计数。现有本人/监护人门户继续通过。
- [ ] **Step 7: 独立隐私/规格审查。** 核对每条规格授权及状态口径，修复后冻结DTO，交给任务4，不提前发布。

### Task 3: 超管负责人配置界面

**Files:**
- Create under `apps/web/src/revenueShare/`: `organizationManagersApi.ts`, `OrganizationManagersPanel.tsx`, `OrganizationManagersDialog.tsx`
- Modify: `apps/web/src/revenueShare/RevenueShareManagementPage.tsx`, `apps/web/src/revenueShare/revenue-share.css`, `apps/web/src/settings/AuditPage.tsx`, `apps/web/src/settings/PermissionsPage.tsx`
- Test under `apps/web/src/revenueShare/`: `organizationManagersApi.test.ts`, `OrganizationManagersPanel.test.tsx`, `OrganizationManagersDialog.test.tsx`
- Modify tests: `apps/web/src/revenueShare/RevenueShareManagementPage.test.tsx`, `apps/web/src/settings/AuditPage.test.tsx`, `apps/web/src/settings/PermissionsPage.test.tsx`

**Interfaces:**
- `listOrganizationManagers(organizationId: string, signal?: AbortSignal)`；`listOrganizationManagerCandidates({q,page,page_size}, signal?)`；`grantOrganizationManager(organizationId, accountId)`；`revokeOrganizationManager(organizationId, accountId)`，严格对应任务1路径和DTO。
- `OrganizationManagersPanel({accountId, organizations, canManageManagers})` 使用现有 `RevenueShareOrganization[]`，按组织ID打开独立Dialog；dialog成功变更只使该组织负责人查询失效，不触发收益重算。
- 查询键分别为 `['revenue-share',accountId,'organization-managers',organizationId]` 和 `['revenue-share',accountId,'organization-manager-candidates',q,page,pageSize]`。

- [ ] **Step 1: 写失败测试。** 超管可管理，普通admin/finance即使看到原分成管理页也无负责人控件；不复用 configuration.people 人员ID；同名候选以account UUID区分、手机邮箱不出现；移除确认含组织和人员；重复提交禁用；切组织/账号后晚候选和晚保存结果不串页；现有分成配置行为不变。
- [ ] **Step 2: 运行 RED。** `npm test -- src/revenueShare/organizationManagersApi.test.ts src/revenueShare/OrganizationManagersPanel.test.tsx src/revenueShare/OrganizationManagersDialog.test.tsx`，预期因新接口/组件不存在失败。
- [ ] **Step 3: 实现独立组件和接口。** 新GET传 AbortSignal、no-store，变更使用现有 CSRF API。机构区域加入列表/“查看负责人”，真实超管标记且global permission双门槛控制组件，不更改原管理页准入；停用组织禁用添加但保留查看/移除。候选搜索仅加载有效账号，加载失败不回显上一组结果；撤销后刷新当前映射，错误中文提示。
- [ ] **Step 4: 补齐权限与中文审计显示。** 仅新邀请权限支持 self/global/person 范围选项，提示“仍只可查看已有邀请关系”；新增上述审计动作及字段中文标签，审计时间仍保留小时分钟。不得修改安全审计超管限制。
- [ ] **Step 5: 运行 GREEN 与相邻回归。** Step2通过后运行 `npm test -- src/revenueShare/RevenueShareManagementPage.test.tsx src/revenueShare/api.test.ts src/settings/AuditPage.test.tsx src/settings/PermissionsPage.test.tsx`。独立审查确认无财务接口/规则变更，记录证据，不提前发布。

### Task 4: 我的邀请页面、过期数据隔离与整体验收

**Files:**
- Create under `apps/web/src/invitations/`: `api.ts`, `keys.ts`, `useInvitationAccess.ts`, `MyInvitationsPage.tsx`, `InvitedHousingSummary.tsx`, `invitations.css`
- Modify: `apps/web/src/portal/api.ts`, `apps/web/src/portal/PortalDashboardPage.tsx`, `apps/web/src/layout/AppShell.tsx`, `apps/web/src/app/router.tsx`, `apps/web/src/revenueShare/MyRevenueSharePage.tsx`
- Test under `apps/web/src/invitations/`: `api.test.ts`, `keys.test.ts`, `MyInvitationsPage.test.tsx`, `InvitedHousingSummary.test.tsx`; additionally `apps/web/src/app/invitationsAccess.test.tsx`, `apps/web/tests/invited-housing.spec.ts`
- Update fixtures/tests: `apps/web/src/{portal,layout,app,auth}` 中所有构造 PortalContext 的测试；`apps/web/tests/cloud-authorization.spec.ts`, `system-evaluation-responsive.spec.ts`, `housing-filter-focus.spec.ts`。具体用 `rg -l 'housing_available:|housing_management_available:' apps/web` 核对，新增字段默认 false，不放宽Zod。
- Create docs: `docs/features/invited-housing.md`。

**Interfaces:**
- `getInvitationSources(signal?: AbortSignal): Promise<InvitationSourcesResponse>`；`getInvitedHousing(filters: InvitedHousingFilters, signal?: AbortSignal): Promise<InvitedHousingPage>` 对应任务2路径及类型，Zod仅投影白名单。
- `invitationKeys.root(accountId) = ['portal',accountId,'invitations']`；sources和housing子键包含来源类型/组织ID/住宿状态/page/pageSize。
- `useInvitationAccess(accountId: string)` 返回 `{sources, ready, validating, error, revalidate}`；sources每30秒、焦点及首次挂载重验，期间关闭住宿子页面；无来源或权限错误清理当前账号邀请树。
- `MyInvitationsPage` 外层按账号key隔离，内部来源/状态变更回第1页；默认“全部授权来源”，同时提供“我邀请的”和有效组织选项。`InvitedHousingSummary({summary})` 只显示任务2白名单和规定日期文案。

- [ ] **Step 1: 编写失败的真实路由和组件测试。** `invitationsAccess.test.tsx` 用真实appRoutes/AuthProvider/page，仅mock网络：无财务权限负责人能进入；不调任何财务API；焦点pending隐藏名单；30秒重验；组织B撤权但A仍有效时B消失且旧参数不能恢复；A→B来源切换晚响应、A→B账号切换、退出同账号重登；断网/500/403/404均不继续显示旧内容。测试 API 严格剥离响应中额外室友/证件/财务字段。
- [ ] **Step 2: 运行 RED。** `npm test -- src/invitations src/app/invitationsAccess.test.tsx`；预期新入口/组件/接口缺失，而不是坏fixture造成的失败。
- [ ] **Step 3: 实现私有数据生命周期。** 独立sources query `refetchInterval:30000, refetchOnWindowFocus:true, staleTime:0, gcTime:0, retry:false`；重验中隐藏整个旧来源及列表，成功后重新验证选中来源并加载列表。列表不使用 keepPreviousData，fetching/error均先于data渲染；变化/退出清缓存，先同步remove再cancel并在取消完成后再次remove，晚响应有account/source/epoch或取消信号保护。不把全站portal轮询改为30秒，不改变既有HousingFilterState。
- [ ] **Step 4: 实现UI及入口。** 桌面表格、360px窄屏卡片，状态/计划与实际日期分栏或分组；空邀请与无住宿分别显示。使用 `formatDateOnly` 和明确定义的空值文案，不以教学营期exclusive-end工具减一天。增加portal schema/路由capability、侧栏“我的邀请”和首页入口；我的分成可加条件跳转，但我的邀请自身不读取财务。无编辑、调房、退房、完整历史、导出按钮。
- [ ] **Step 5: 跑 GREEN 和完整前端。** Step2通过后执行 `npm test -- --maxWorkers=4 && npm run lint && npm run build`。重点保留 `housingFocus.test.tsx` 已通过的筛选保留、权限版本变化和退出重登回归；新增必填capability的所有正负fixture都更新。
- [ ] **Step 6: 浏览器组合验证。** Playwright新spec先以合成API验证桌面1440/手机360界面、日期、30秒/焦点隐藏、晚响应；再在隔离E2E库创建仅测试用的邀请关系及负责人，验证超管授权→负责人读取→超管撤销→旧会话新请求404及UI清空。每视口独立目标，测试不操作NAS业务账号/数据。记录事件模拟与物理真机验收的区别。
- [ ] **Step 7: 独立整体验收审查。** 逐条核对规格第7节九项验收、四任务接口一致性和权限否定用例，完成 `docs/features/invited-housing.md`（配置入口、范围、撤权含义、日期、非导出边界）。修复发现的问题后再进入下方发布门禁。

## 组合验证与 NAS 发布门禁

本节由父代理串行执行；任务审查通过不是已发布。

- [ ] 在 `apps/api` 使用主仓库 `.venv/bin/python` 与专用本地 `erp_e2e` 测试库执行完整 `python -m pytest`；不得并行启动第二个重置相同数据库的测试进程。运行Ruff与适用mypy，确认新模型/路由加载及单一迁移头；修复本次错误，不掩盖或随意改无关业务。
- [ ] 最终冻结源码后重跑前端测试、lint/build和新浏览器用例；检查 `git diff --check`。生产差异保留先前房态筛选修复，不stage无关文件。
- [ ] 主仓库执行：`scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3'`。不绕过部署锁、迁移前备份、容器健康、加密备份与隔离恢复；不清生产数据、不创建测试财务记录。
- [ ] 核对 `.active-release` 与 `.deployment-state=complete`、新revision、容器健康、公网正常TLS、线上静态文件与本地构建哈希一致、新表进入恢复验证。配置为空是预期：不自动指定组织负责人。
- [ ] 在主仓库 `outputs/verification/invited-housing-YYYYMMDD/` 保存测试和发布证据，交付超管操作路径与复验步骤。任何部署/恢复失败明确报告阻断，不宣称已上线。

## 计划审核状态

- [x] 用户已确认书面规格。
- [x] 已核对当前代码、真实模型字段、权限种子、路由及测试夹具，并完成计划自审。
- [x] 用户审核本实施计划及执行方式（2026-10-10 已确认）。
- [ ] 业务代码实施、验证及NAS发布。

采用分任务子代理实施、每任务独立审查，由父代理整合共享接口并串行运行数据库测试。按现有偏好使用实际需要的并发量，不为凑满8路同时改同一文件。计划已批准，业务实施进行中，发布状态以验证记录为准。
