# 委托管理与战队工作人员 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成受财务隔离的指定账号委托管理，以及经理/教练/助教分类、队内只读归属。
**Architecture:** 现有 RBAC 加统一受保护账号 guard；新增只读 TeamStaffAssignment，原管理关系保持写语义。人员主分类、账号角色、队内岗位独立。
**Tech Stack:** FastAPI/Python、SQLAlchemy/PostgreSQL/Alembic、React/TypeScript、Vitest/Playwright。
**Spec:** docs/superpowers/specs/2026-11-06-delegated-management-team-staff-design.md

## Global Constraints

- 用户明确直接实施；不再次等待设计批准。P=/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3，现有 linked detached 工作区，保留全部已有脏改动；不 stage/commit/push。
- 禁 payments.read/manage、refunds.manage、payment_imports.manage、全部 revenue_share.*；采购 procurement.* 与订票 travel_reports.* 允许。指定账号不赋 super_admin/finance，超管专属门槛不扩大。
- protected-target 是 super_admin/finance 或尚未过期（含未来）非 self/family 核心财务 allow；不以采购、订票、本人分成作为财务身份。保护适用于非超管的账号、岗位、授权 mutation；actor/target 人员→账号锁后重查。
- 新岗位 manager/coach/assistant_coach→角色 team_manager/coach/assistant_coach；新绑定只读。不能混入建档提交使用的 managed_team_ids。旧经理管理绑定功能不变。
- 不读/写真实业务测试数据，指定账号授权仅主代理通过正常已登录超管流程执行。无权限不能偷取登录 token 或伪造审计操作者。
- 所有55433数据库测试由主代理发放独占时段；PYTHONPATH=apps/api当前目录，TEST_DATABASE_URL=postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:55433/erp_e2e。主 venv /Volumes/MacDev/Projects/new project3/.venv/bin。
- 修改前保存实际工作字节快照，不能以 HEAD diff 归属已有脏代码。任务报告须 RED/GREEN、命令/结果、精确文件和未验收项。

## Review Focus

1. 财务身份先撤后重置、未来生效权限、锁等待间变更：Task1真实事务/HTTP回归。
2. 助教或新经理错误获得名单/协助录入/住房写权限：Task2/3拒绝测试，旧管理正例。
3. 同人同队多岗位导致重复总数/某岗位解绑影响另一岗位：Task2/3多岗位和撤销测试。
4. 换岗/删号跨多队的锁序和旧关系历史：Task2生命周期、Task6集成并发。
5. UI与后端StaffType遗漏、拒绝后旧权限私有缓存仍显示：Task4/5表单/筛选/失权测试。

---

### Task 1: 受保护账号与授权上限

**Files:** Create app/rbac/account_protection.py；Modify app/auth/service.py、app/auth/admin_router.py、app/rbac/service.py；Test tests/api/test_account_management_boundaries.py、新 tests/services/test_delegated_account_protection.py、新 tests/db/test_delegated_authority_concurrency.py、相关 tests/api/test_permissions.py（如有需要先通知主代理）。路径均在 apps/api。
**Interfaces:** `protected_management_target(db: Session, account_id: UUID, *, now: datetime | None = None) -> bool` 为共享纯数据库判断（无内部commit），由已锁参与者的现有权威校验调用。核心键取规格；不硬编码指定人名。保留现有 PermissionDenied/中文Problem映射。该任务独占 auth/service.py 与 rbac/service.py；主代理后续在释放后添加 Task2 生命周期接线。
- [ ] 写 RED：普通可重置；finance/root以及非个人金融授权目标拒绝，零凭据/会话副作用；采购/订票/本人订单和分成不触发保护；金融角色/override先删或加deny同样拒绝；未来生效金融授权也保护。
- [ ] 在独占数据库时段运行上述新测试，记录预期失败；可先做纯策略单测 RED，不以导入错误代替行为失败。
- [ ] 实现共享 guard；账号管理、角色赋予/撤销/岗位切换、override新增/删除均锁actor/target后检查当前权限及目标；override的普通分支也使用现有排序参与者锁。保护不能被缓存、旧预览或角色降级绕过。
- [ ] 验证授权上限：不能删自己的金融deny、不能通过普通第二账号回授超权限/更宽scope/更长有效期，不能发finance/root角色；root管理保留，最后超管保护保留。
- [ ] 跑覆盖API/并发/role权限回归及Ruff/mypy，提交实际字节diff与报告；无Git提交。

### Task 2: 战队只读岗位绑定后端

**Files:** Own app/team_management/ 下 models.py、schemas.py、router.py、access.py、query_service.py、lifecycle.py、allocation.py、operations.py/write_support.py（所需）、新 staff_service.py；migration0085_team_staff_assignments.py；新 tests/api/test_team_staff.py、tests/services/test_team_staff.py、tests/db/test_team_staff_concurrency.py、tests/db/test_team_staff_migration.py。不得改auth/rbac/identity/intake源文件。
**Interfaces:** API严格采用spec的staff-candidates/staff-assignments/end及DTO，TeamMutationResult保持原形。导出 `active_staff_team_ids(db, account_id, *, role_keys: set[str]) -> set[UUID]`（新staff_service.py，或同模块轻量查询；只返回当前匹配岗位、有效队伍/分部），供Task3用。生命周期新增 `staff_team_ids_for_account(db, account_id, *, role_keys: set[str] | None = None) -> set[UUID]`、`close_staff_assignments_for_roles(db, account_id, removed_role_keys: set[str], *, operation_id: UUID, occurred_at: datetime) -> None`；账号删除可传三种映射角色全部关闭。具体与旧operation模型约束不兼容时先通知主代理确定，不自行改别人文件。
- [ ] 写 RED：三岗位候选、创建/重复幂等、单条结束、多队兼任；新绑定能read不能manage，旧绑定write仍成功；失活/停队/解绑/失权即时拒绝。
- [ ] 最小实现新表及0085（down0084），新表历史RESTRICT；角色移除关闭相应新岗位；旧manager关闭逻辑仍保留，目录在用检查纳入新绑定；有历史则加锁后拒绝降级。
- [ ] `TeamAccessService` read合并新有效岗位，manage仅原管理；去重、deny优先、分页前授权，所有写沿原人物/账号/目录锁序，候选不得泄漏完整资料。
- [ ] 运行单元/API/真实并发/迁移回归（数据库先申请独占），Ruff/mypy。报告给root auth/rbac目录生命周期需要接线的精确函数与参数，不能编辑共享文件。
- [ ] 保存diff/报告、无Git提交。

### Task 3: 本队人员与住宿只读投影

**Files:** Own app/personnel_intake/access.py、queries.py、app/housing/access.py、person_access.py（如存在）、person_router.py 只在必要时；相关 tests/services/test_managed_people_housing.py、tests/api/test_managed_people_housing.py、新 tests/services/test_team_staff_housing.py、tests/api/test_person_housing.py必要限定。不得写team_management/rbac/auth/identity或前端。
**Interfaces:** 消费Task2 `active_staff_team_ids(db, account_id, role_keys=roles)`；在 IntakeAccessSnapshot 增加独立 `readable_staff_team_ids`（末尾默认空，保留旧constructor兼容）；不能修改managed_team_ids来让新岗位提交建档。assistant角色已由Task4迁移/Task6seed提供personnel_intake.housing.read/global，实际只读范围始终依绑定。
- [ ] RED测试：三岗位各可看到自己队伍当前成员住房摘要，其他队伍/未绑定/已离队不可见；无全局人员、室友、身份电话信息；总数分页与多角色/多岗位去重；被拒绝路径不可被coach路径救回。
- [ ] 合并读取谓词，只在读取路径使用新staff集合；保留协助提交旧资格；新assistant/manager不能借允许override办理入住、调退房、增加成员、提交学员。
- [ ] 撤销绑定、角色、停用账号、停用分部实时失权；必要住房owner-only角色判断纳入assistant，仍无完整房间目录。
- [ ] 独占DB回归原managed_people/住房/离营可见性相关模块、Ruff/mypy，保存字节diff与报告。

### Task 4: 人员岗位类型与角色显示

**Files:** Own apps/api/app/identity/models.py、schemas.py、router.py、service.py；migration0084_team_staff_roles.py（down0083）；相关staff类型后端测试、新tests/db/test_team_staff_roles_migration.py。Own apps/web/src/people 下 api.ts、presentation.ts、PersonForm.tsx、StaffProfilePanel.tsx、PeoplePage.tsx、PersonAccountRolesPanel.tsx 及对应测试；apps/web/src/settings/AuditPage.tsx中文角色标签及测试。不得改rbac/service.py、rbac/seed.py；由root集成。
**Interfaces:** StaffType增加team_manager/assistant_coach；角色assistant_coach ID10000000-0000-4000-8000-000000000015，默认仅team_memberships.read/assigned_team与personnel_intake.housing.read/global；coach增加team_memberships.read/assigned_team。迁移明确SQL常量，不导入可变seed；存量人员/角色不改。root同步seed和staffjobrole映射。人员分类统计新增两个字段使用同样模式，UI/API合同配套。
- [ ] RED：新建/编辑/查询/统计新StaffType，不能隐式授予角色；主类型单值与多账号岗位并存；岗位界面明确保留兼任，新增两项可选。
- [ ] 更新所有该类显式枚举/中文显示；公共注册身份审核若引用旧StaffType需兼容读取但不得隐式放开新自助角色审批；发现额外文件先报root。
- [ ] 0084加锁降级guard：有新类型、助教角色指派或自定义助教权限不降级，默认仅新role/grants可安全移除；不丢现有资料。
- [ ] 测试工作人员表单/人员列表/岗位切换/中文显示，Ruff/mypy/前端lint+定向Vitest，相关DB独占运行。保存diff/报告，不commit。

### Task 5: 队伍工作人员 UI

**Files:** Own apps/web/src/teams/api.ts、TeamManagementPage.tsx、presentation.ts、新TeamStaffPanel.tsx/css及测试；可改既有team-management E2E spec/合成seed的前端部分但先协调root，不能改Task4people文件。
**Interfaces:** 消费spec API与TeamStaffAssignmentView；can_assign_managers控制操作，既有can_open_management让新岗位查看名单；旧TeamManagerPanel不移除，文案明确原名单管理。新增staff查询key包含身份、队伍、岗位，突变正确invalidate。
- [ ] RED：选岗位→只显示该岗位候选→确认绑定；显示多岗位、解绑一条保留其他条，失败保留operationkey与表单，权限变动清私有状态。
- [ ] 实现“队内工作人员（只读）”面板，与旧管理面板分开展示；工作人员入口能看自己队伍但无新增/移出队员或房态编辑按钮。
- [ ] 遵循A/B主题及窄屏布局，不引入新UI库；404/403/401和版本/并发冲突清楚中文提示；不显示身份证/电话/邮箱。
- [ ] 相关Vitest/lint/build；真实E2E由root安排独立测试环境，保持旧spec注册归属。diff/报告无Git提交。

### Task 6: 共享集成、完整验证、发布与指定账号授权

**Files:** Root owns app/rbac/seed.py、shared audit/metadata/router manifest/head tests、staff角色中央映射；Task1释放后才改auth/service.py、rbac/service.py生命周期接线。新迁移按0084→0085，metadata、scripts/db-schema-manifest.sh及新表行数清单同步。Root owns live operations; noworkerNASwrites。
- [ ] RED/GREEN验证seed新助教最小授权；集成三岗位生命周期：收集受影响新/旧队伍统一锁目录，再按被移除岗位关闭对应关系；删号关闭全部。stafftype映射与显式保留兼任一致。
- [ ] 更新审计受控元数据和中文action/岗位，拒绝私有输入；更新schema head/chain/tablecount及旧迁移完整断言，按历史版本读取历史列。
- [ ] 独立任务复核、全任务最终复核；完整前端测试/lint/build、后端unit/相关API服务并发+全部迁移+相关CLI、真实合成浏览器多角色操作。整项目测试若未完成必须明确，不称全量通过。数据库单消费者。
- [ ] 正常已登录超管页面精确查指定人员，确认唯一账号和当前权限。生成差异清单：保留业务角色，补admin/administration、允许权限管理/采购/订票及可授权非财务业务，拒绝支付/退款/imports/allrevenue，超管专属不放开。不能把未来新增权限自动全部允许。
- [ ] 本地验证通过后标准 `PATH=<venv>:$PATH NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`，直到complete、健康、加密备份/隔离恢复通过。
- [ ] 发布后仅通过真实超管正常授权流程应用指定账号差异，安全顺序先加金融deny再加管理角色/允许；复读有效权限。不得重置真实财务或任何无关人员密码来测试；不能用SQL直接篡改业务/伪造操作者。浏览器未登录则请求用户完成登录，不谎报授权已完成。
- [ ] 保存可验证发布/授权结果与未完成项，保留工作区审计资料，无Git提交/推送。
