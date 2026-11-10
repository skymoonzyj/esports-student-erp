# 建档角色与激活授权 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 管理建档选择非管理员角色，激活时安全授予，未激活人员可重新确认授权。

**Architecture:** 独立版本化激活角色计划保存授权事实；前后端使用服务端允许角色目录。激活原子消费既有邮箱证明并复用当前 RBAC 委派边界，预锁完整参与者避免新锁序。

**Tech Stack:** FastAPI/Pydantic、SQLAlchemy/PostgreSQL/Alembic、React/TypeScript、Vitest/pytest/Playwright。

**Spec:** docs/superpowers/specs/2026-11-06-intake-activation-roles-design.md

## Global Constraints

- admin、super_admin 不可通过建档或激活角色计划授予；finance 仅实际有效超管可批准。
- 额外角色需要当前 permissions.manage 与既有完整角色委派上限；指定行政人员的金融 deny 不可绕过。
- 历史没有显式计划的档案和普通送审/公开注册保持原规则；不自动创建账号、关系、付款或重写现有权限。
- 角色计划审核人不因档案/邮箱/认领更正而被替换；激活复核原授权人的当前资格，失败原子回滚。
- 单一 PostgreSQL 测试消费者；不使用真实账号/真实业务数据做写入测试。
- 已处于外部托管 dirty linked worktree；不 stage/commit/reset；通过实际文件快照生成本次 diff，保存其他工作。
- 通过本地验证后使用 scripts/deploy-nas.sh --source "$PWD"，NAS_SSH_PORT=202；发布失败阻断，不直接复制部署。

## Review Focus

- 授权人变更、停用或撤权与激活并发：不得旧快照赋权，锁序一致（Task 1）。
- 纠正邮箱、监护资料或认领时：不偷换角色批准人、不丢角色计划（Task 1）。
- 普通申请额外传角色、篡改管理员角色或角色包升级：拒绝且零部分写入（Task 1）。
- 草稿恢复/切换账号/权限重新验证：选项与提交载荷正确，不借旧页面越权（Task 2）。
- 重新确认角色与已激活账号、幂等键复用及版本过期：清晰反馈且无重复授权（Tasks 1–3）。

### Task 1: 后端授权计划、建档与激活原子链路

**Files:**
- Create: apps/api/app/personnel_intake/role_grants.py（模型逻辑可另置 role_grant_models.py）。
- Modify: personnel_intake/{schemas,router,approval_service,codec,activation_service,activation_router,claims/locking,claims/binding,models}.py；必要的 RBAC 委派复用 helper；仅相关 identity 创建/展示接口。
- Create: apps/api/migrations/versions/0086_intake_activation_roles.py，down_revision=0085_team_staff_assignments。
- Modify: scripts/db-schema-manifest.sh、现有 migration/manifest 当前 head 断言。
- Test: apps/api/tests/services/test_intake_activation_roles.py、tests/api/test_intake_activation_roles.py、tests/db/test_intake_activation_roles_migration.py，原相关回归。

**Interfaces:**
- DirectCreateInput 与 DirectCreatePendingInput 接收可选 activation_role_keys:string[]，省略/空数组保留最低身份规则；普通 IntakeInput/公开注册禁止该字段。严格管理建档增加 staff，普通送审不增加 staff。
- GET /api/v1/personnel-intake/activation-role-options → {items:[{key,name,assignable,reason:string|null}]}；只向可直接建档的管理者返回，排除 admin/super_admin；按当前授权上限计算，不以菜单权限替代服务端验证。
- GET /api/v1/personnel-intake/people/{person_id}/activation-roles → {person_id,version,role_keys,role_names,can_edit,account_activated}；version=0 表示无显式计划；只向有权人员管理者返回。
- PersonDetail.activation_roles_available:boolean（默认 false）表示可查询该辅助建档人员的角色计划；要求当前 admin/root + people.update + global personnel_intake.review，与计划 GET 相同；已激活可读，写入另由 can_edit 决定。
- PUT 同一路径，{role_keys:string[],expected_version:int,idempotency_key:UUID}，返回同一 view；未激活且有授权资格可保存/重新确认；已有账号不可修改。
- Role plan immutable version rows：person、原始来源 approval、authorizer、role_keys、version、operation key+fingerprint、created_at；每人版本唯一；每操作者操作键唯一。角色集合最多现有目录数量，拒绝重复/不存在/admin/root。记录关联、降级保护与索引。
- 激活额外角色授权复用 actor-required RBAC 校验；保留 activation_role_keys(kind) 最低本人角色。读取计划和授权人预发现，合入既有 sorted people/accounts 锁，锁后复读并确认计划没有漂移。assign_role 必须在外层 atomic_write_transaction 及完整参与者锁下调用，绝不 actor=None。独立 OTP 猜测计数事务保留在原子块外。
- 失败用独立中文 403/409/422 问题码；公开发送验证码不泄露，已验证激活才可提示“激活角色授权已失效，请联系管理员重新确认”。审批/纠正不修改原角色计划。

- [x] 写失败测试：root 可批准13个非admin/root角色；禁用/篡改/重复字段拒绝；普通管理员越权角色/finance 拒绝，允许的岗位成功；普通申请不能传角色；无计划旧激活保持。
- [x] 执行对应 pytest 测试并记录预期 RED；单个数据库进程。
- [x] 实现模型、迁移、角色目录/计划服务及接口，真实当前权限检查、幂等和版本守卫、审计。
- [x] 实现 direct create 两种模式原子写计划；授权必须在现有确认并创建人员后、事务提交前进行，失败全回滚；请求指纹包含角色集合。
- [x] 实现激活预锁/重读/授权、已验证错误反馈及资料更正不变更计划。补实际独立连接并发撤权/重确认测试，失败不得留下账号/角色或消耗 OTP。
- [x] 运行新测试与 services/test_assisted_activation.py、test_personnel_intake.py、相关 API/claims、迁移回归；Ruff/mypy，保存 RED/GREEN/实际文件清单和结果，不提交代码。

### Task 2: 管理建档和人员详情角色交互

**Files:**
- Create: apps/web/src/assistedPeople/ActivationRolesPanel.tsx、ActivationRolePicker.tsx 及对应测试。
- Modify: apps/web/src/assistedPeople/{api,types,AssistedDirectCreatePage,AssistedPersonForm,PendingPersonForm}.tsx/ts、相关测试；人员详情 lazy panel 挂载及普通新增人员入口提示；auth 激活文案及对应测试。

**Interfaces:** 消费 Task 1 三个接口和 activation_role_keys 字段；普通申请和纠正表单不接入该参数；服务端目录为权威，无 admin/root。

- [x] 写并运行失败测试：root全部可选、受限者显示不可选原因、两种建档模式载荷、staff严格完整资料、原送审无角色字段、待补草稿恢复/清除与账号切换、保存后显示生效时点。
- [x] 实现账号隔离 query key 的目录/计划 API 和严格 DTO 解析；失败/重验证不允许提交角色操作。选择字段明示“账号激活角色（激活后生效）”，保留最低身份角色并说明监护身份不自动创建监护关系。
- [x] 人员详情仅在相应 capability 下挂载角色计划面板；未激活可显式保存/重新确认并显示结果，已激活引导已有权限管理；版本冲突刷新但不伪报成功。
- [x] 有管理建档权限者在普通新增人员页得到“管理建档并设置激活角色”入口；普通 people.create 用户原流程不变。不扩大原资料纠正接口。
- [x] 运行相关 Vitest RED/GREEN、全前端 npm test、npm run lint、npm run build；保存报告和改动清单，不提交。

### Task 3: 联调验收与发布

**Files:** 前后端隔离 E2E 夹具和 tests/intake-activation-roles.spec.ts；相关验收记录。

**Interfaces:** 使用 Tasks 1–2 实际 API 与页面；以隔离生成用户完成建档和激活，不使用 NAS 真实人员。

- [x] 先增加浏览器失败验收，再补专用虚构 fixture：超管建档选择物业/助教、未激活查看角色、重新确认、受限账号不可分配财务/admin/root；移动窄屏和双主题最小截图。
- [x] 运行真实后端/前端链路和完整 backend pytest（单消费者），完整前端测试、静态检查、迁移。明确区分既有失败、实测通过和未验收部分。
- [x] 生成实际快照 diff 交独立最终安全审查；修正阻断项并复验。
- [x] 使用 mandatory NAS 脚本发布，核验活动 release、0086、健康与备份恢复、线上页面资源；不向真实人员赋权做验收。
