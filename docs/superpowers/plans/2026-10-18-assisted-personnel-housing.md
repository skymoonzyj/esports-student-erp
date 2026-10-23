# 学员／队员代建档、审核、住宿与邮箱激活 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 工作人员提交完整学员／队员档案，管理员审核后即可安排住宿，本人以后验证预留邮箱激活原档案对应账号。

**Architecture:** 在现有 Person／UserAccount 分离模型上增加版本化申请、审核凭据和档案资格，不借用报名、付款或临时账号。住房和账号访问分别采用来源限定的资格策略；负责人住宿使用独立只读投影。复用现有验证码、加密、锁、审计与 NAS 发布设施，保护旧注册和监护业务。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、PostgreSQL、Alembic；现有 React／TypeScript、React Query、Zod、Vitest、Playwright；现有 Capacitor iOS 兼容层，不新增依赖或原生能力。

**Spec:** `docs/superpowers/specs/2026-10-18-assisted-personnel-housing-design.md`（2026-10-18 用户已确认）。

**执行状态：** 用户已确认计划；隔离实现完成，生产源码 `d06e57e` 与随后至 `3129677` 的测试契约／完整检查脚本修正已分别审核。最终Web／静态检查、具名回归、五套浏览器42测试及最终镜像恢复1通过0跳过已有实际证据。此前完整API79125失败，修正后62262实际exit0：7788通过／6项opt-in跳过／0失败，普通DB／迁移无跳过；完整check.sh未运行，不把独立矩阵冒称完整编排验收。NAS尚未部署，原生App／模拟器／真机未验收。以下任务清单保留原实施步骤，不以未勾选项否认已完成实现；当前结果以功能／发布文档及执行记录为准。

## Global Constraints

- 学员、队员身份可以兼任；独立队员不伪建 StudentProfile。姓名、身份证号、手机号、独立邮箱必填，身份证决定的生日／性别须一致。
- 未成年人至少一名成年监护人，姓名、身份证、手机号及关系必填；本流程监护邮箱可选、无需监护账号。档案审核不等于监护人验证、同意或家庭查看授权。
- 教练／领队／经理／仅行政身份的提交必须他人审核；实际管理员／超管且具有审核能力者可显式“管理建档”。普通提交不因兼任角色而自动通过。
- 待审核、退回、撤回不得入住或激活。已通过无需本人或监护人账号即可入住。入住／调房／退房继续由有范围权限的行政、管理员、超管办理。
- 教练只看有效负责学员，领队／经理只看有效负责队伍成员；仅住宿摘要，无室友、完整身份证、联系信息、监护、财务、历史或导出。
- 手机号可共享但不据此合并；本人邮箱不可与他人或其监护人共用。只开邮箱 OTP 激活，手机号不因此获得验证标记。
- 新账号开户来源不可事后变更；旧账号不因后来审核通过而放宽监护、锁定或停用规则。不得覆盖已验证联系方式、角色、邀请分成、队籍或住宿事实。
- 不做代报名、代付款、新餐饮入口、短信、自动登录、原生新能力或历史批量修正。原有招生／监护同意／家庭评价规则不变。
- 业务日期用中国时间年月日，审计保留小时分钟；敏感草稿、验证码、密码不得进入持久浏览器存储、URL、日志或普通审计负载。
- OTP：6 位数字、10 分钟有效、60 秒发送间隔、同邮箱 5 次／24 小时、来源 20 次／小时、5 次错误封闭挑战，跨用途共享额度；入口另共享标识 5 次／小时、来源 10 次／分钟及 20 次／小时。
- 密码复用 8–16 位、ASCII 大写／小写／数字同时存在（允许符号），Argon2id 现有 3／65536／4 参数；OTP 使用现有 32-byte pepper、HMAC-SHA256 与恒时比较。
- 所有测试只使用隔离数据库和虚拟人员／邮件发送器；数据库测试串行，不向真实人员发邮件，不操作 NAS 真实住宿。Web 测试不算 iOS 真机验收。
- 实现后必须运行经检查的 `scripts/deploy-nas.sh --source '<验收源目录>'`；失败即发布阻塞，不绕过预检、备份、迁移或脚本。当前最终发布仍待完成。

## Review Focus

1. 旧账号后来获批、已停用账号或多人共享手机号：不切换旧监护策略、不重启账号、不按电话误合并（任务 2、3、4、7）。
2. 未成年在提交、审核、入住或激活之间满 18 岁，或监护关系刚好失效：使用各业务日期；存量入住保留且可安全退房（任务 3、4、5）。
3. 负责人在送审／分页读取期间被撤销，仍留旧全局房态 allow 或前端迟到响应：新旧入口都不能泄漏他人，兼任行政仍保留合法范围（任务 3、6、8）。
4. 重复审批、激活双击、邮箱更正与旧注册同时争抢：同一份人员／账号／队籍，旧版本或挑战不可复用，不死锁、不留半条数据（任务 2、3、7）。
5. SMTP 故障、未知邮箱和跨用途验证码：不枚举档案、不伪称已发送、共享限流；失败不能产生已验证账号，旧注册仍工作（任务 7、9、10）。

---

## 工作目录、隔离与命令约定

调研源为 `/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3`，文档基线提交 `7a0f0bc`；启动仓库为 `/Users/skymoonzyj/Documents/new project3`。当前大量未提交文件包含已上线功能和未完成 iOS 工作，不能把 HEAD 或 `.nas-release-sync` 当成完整发布源，也不能整树提交／发布。

执行任务 1 前使用 using-git-worktrees 技能与应用工作树清单，记录现有脏文件清单；只读核对 NAS `.active-release`、实际 Alembic head 和发布源指纹，选出可重建、与线上一致的基线，再隔离本功能。若不能确定基线，停止实施集成并报告；不遗漏线上已有迁移，也不把未验收 iOS 改动带入发布。

完整路径相对经过核实的实施根目录；任务内 API 模块短路径（如 `auth/service.py`）统一相对 `apps/api/app/`，`tests/`、`migrations/` 相对 `apps/api/`；Web 模块短路径统一相对 `apps/web/src/`，`scripts/` 相对根目录。API 命令 cwd=`apps/api`，Web 命令 cwd=`apps/web`，脚本命令 cwd=实施根目录。`PYTHON='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'`，相邻 `ruff`／`mypy` 使用同一环境；命令写作 `"$PYTHON"` 避免空格路径错误。

执行器为本功能选择未占用端口、唯一 Compose 项目及一次性数据库，记录所有权后设置 `TEST_DATABASE_URL`；先跑 `app.cli.e2e_database_safety`。现有 pytest 会重建 schema，**只准一个 DB 测试进程**。不直接运行会重置共享 `esports-erp-e2e` 的 `scripts/check.sh`，不停止不属于本次的服务。无数据库导致 SKIP 不算通过。

每个任务提交前只 stage 该任务实际修改的文件／块，检查 staged diff，使用 `ERP_NAS_AUTO_DEPLOY=0 git commit ...` 表明开发期间有意暂停发布。最终任务统一人工调用 NAS 脚本；不得 `git add .`、重置别人的改动或对外 push。

## 文件与接口地图

| 单元 | 文件 | 职责 |
| --- | --- | --- |
| 新业务模块 | `apps/api/app/personnel_intake/{models,schemas,errors,locks,access,service,approval_service,guardian_records,eligibility,queries,responsibilities,router,dependencies}.py` | 申请、审核、资格、关系和私有只读查询；各文件按名称单一职责 |
| 激活 | `apps/api/app/personnel_intake/{activation_models,activation_schemas,activation_service,activation_router}.py` | 仅邮箱 OTP；复用 registration 服务，不复制限流实现 |
| 来源限定账号资格 | `apps/api/app/auth/account_readiness.py` | 根据不可变开户来源选择资格；旧 GuardianReadinessPolicy 保持业务语义 |
| 现有域集成 | `identity`、`auth`、`registration`、`team_management`、`housing`、`rbac` | 锁、联系人冲突、失效联动、无账号队员、住房与权限 |
| Web 工作人员入口 | `apps/web/src/assistedPeople/` | API／查询键／能力、提交／审核／负责名单及教练关系组件 |
| Web 激活 | `apps/web/src/auth/ActivateProfilePage.tsx`、`activationApi.ts` | 无登录态激活，复用注册布局／密码控件 |
| 存储迁移 | `apps/api/migrations/versions/0066_assisted_personnel.py` | 当前源 head 为 `0065_invited_housing_visibility`；执行前重新核对，若 head 已变须顺延新迁移，绝不改旧迁移 |
| 验收与发布 | 新 API／Web 测试、隔离 seed／launcher、`scripts/test_deploy_nas_packaging.py` | 功能链路、跨入口回归、排除测试产物、NAS 发布证据 |

共用 Python 类型在 `personnel_intake/schemas.py`：`PersonKind = Literal['student','player','both']`；`IntakeStatus = Literal['pending','returned','approved','withdrawn']`；`Actor` 不另造类型，统一 `AuthContext`。`IntakeInput` 含本人字段、`guardians: list[AssistedGuardianInput]`、`coach_person_ids: list[UUID]`、`team_id: UUID | None`；后端根据 kind 校验必需归属。身份／联系方式输入使用现有 SecretStr／加密设施，响应不复用含秘密的输入对象。

共用结果：`IntakeView(id: UUID, version: int, status: IntakeStatus, kind: PersonKind, allowed_actions: list[str], scoped_payload: IntakePayloadView | None, returned_reason: str | None, approved_person_id: UUID | None)`；`ApprovalResult(request_id: UUID | None, person_id: UUID, approval_id: UUID)`；`AssistedContext(can_submit: bool, can_review: bool, can_direct_create: bool, can_read_managed: bool, allowed_team_ids: list[UUID])`。`IntakePayloadView` 为 schemas.py 内显式字段的响应模型，与 IntakeInput 的业务字段一一对应，但只有详情授权通过后才解密映射；禁止把 SecretStr 输入模型直接 JSON 序列化。列表单独 `IntakeListItem`，不得带整份负载。

依赖顺序：1 → 2 → 3；3 后可并行 4 与 6；5 依赖 4，7 依赖 2／3／4；8 依赖 3／5／6；9 依赖 7；10 → 11 收口。前端可在接口确定后先写隔离 mock 测试；同一文件或数据库验证由主代理串行协调，按实际情况 3–4 路，不为凑数开 8 路。

## Task 1: 可恢复的数据模型与不可变开户来源

**Files:** 新建 `personnel_intake/{__init__,models,schemas,activation_models,errors}.py` 和迁移 `0066_assisted_personnel.py`；修改 `auth/models.py`、`registration/models.py`、`migrations/env.py`、`scripts/db-schema-manifest.sh`、`tests/db/test_migrations.py`、`tests/cli/test_operations_docs.py`、`tests/cli/test_local_container_acceptance_launcher.py`；新测试 `tests/db/test_assisted_personnel_migration.py`。

**Interfaces:** 产出 `PersonnelIntakeRequest`、`PersonnelIntakeRevision`、`PersonnelCredentialReservation`、`AssistedApproval`、`AssistedPersonProfile`、`CoachPersonResponsibility`、`AssistedActivationSession` ORM；schemas 产出上述 DTO。审批凭据可被资格、住房、激活引用，不把请求状态当权限证明。

- [ ] 写 `test_upgrade_preserves_legacy_and_provenance_is_immutable`：在旧 head 建旧账号／队籍／住宿／分成，升级后 `assert after_business_snapshot == before_business_snapshot`、`assert old_account.account_access_origin == 'legacy'`；非法改来源用 `pytest.raises(IntegrityError)`。写 `test_active_reservations_and_approval_are_unique`、`test_downgrade_refuses_existing_assisted_data`，断言重复活动预留／同申请重复批准失败，空新表可降级，有新业务数据拒绝破坏性降级。
- [ ] 运行 `"$PYTHON" -m pytest tests/db/test_assisted_personnel_migration.py -q`，预期因新迁移／表缺失失败，不接受连接失败作为 RED。
- [ ] 实现表：requests 当前版本／状态／提交人；revisions 唯一 `(request_id, version)`、完整加密负载与摘要；reservations `(kind,digest)` 活动唯一、归属 request/person、结束时间；approvals 不可变审核快照、具体监护关系版本、初审 request 唯一；profiles 按 person 唯一、当前 approval；responsibilities 有效 coach/person 对唯一且保留历史。索引覆盖状态／提交人、邮箱、有效关系和挑战到期；不用历史邮箱全局唯一索引破坏旧数据。
- [ ] 实现 `UserAccount.account_access_origin: legacy | assisted_activation` 和 `assisted_approval_id` 配对约束、审批与 account.person_id 一致的约束及禁止更新来源／原始凭据的触发器；旧账号仅默认 legacy、不补批准标记。activation session 保存 token HMAC、challenge FK（唯一）、person／approval／版本／邮箱摘要、requested/verified/completed 状态和过期时间；现有 challenge 只扩展 purpose CHECK 为 `assisted_activation`，不滥用 application_id。
- [ ] 重跑迁移测试及 `tests/db/test_migrations.py`，期望全通过；在隔离数据库分别验证旧 head→新 head、空数据 downgrade/re-upgrade、有数据前向恢复。同步 schema manifest／head 断言，保留审批／激活表审计与加密列清单。
- [ ] 检查 scoped diff 后提交 `feat: add assisted personnel storage and account provenance`。

## Task 2: 版本化提交、受控审核能力和跨入口身份占位

**Files:** 新建 `personnel_intake/{locks,access,service,dependencies,router}.py`；修改 `api/router.py`、`rbac/{seed,service}.py`、`registration/{router,finalization_service,verification_service,routing}.py`、`auth/service.py`、`identity/service.py`、`audit/service.py`；新测试 `tests/services/test_personnel_intake.py`、`tests/api/test_personnel_intake.py`、`tests/db/test_personnel_intake_conflicts.py`。

**Interfaces:** `PersonnelIntakeService.submit(actor: AuthContext, input: IntakeInput) -> IntakeView`；`resubmit(actor, request_id: UUID, expected_version: int, input: IntakeInput) -> IntakeView`；`withdraw(actor, request_id, expected_version) -> IntakeView`。`PersonnelIntakeAccess.context(actor) -> AssistedContext`、`require_submit(actor, input) -> None`。`CredentialReservationPolicy.require_available(*, identity: str, email: str, person_id: UUID | None, request_id: UUID | None) -> None`；`lock_personnel_credentials(db: Session, *, emails: set[str], identity_digests: set[str]) -> None`。

- [ ] 写 `test_versioned_submission_and_private_conflicts`：`assert pending.version == 1`；pending 修改拒绝；使用已退回 fixture 重提后 `assert resubmitted.version == 2`（真正退回 API 在任务3）；撤回释放占位但保留记录。参数化 student/player/both、必填字段、身份证、共享电话不合并、监护与本人邮箱相同、普通提交人跨队和隐私冲突。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_personnel_intake.py tests/api/test_personnel_intake.py tests/db/test_personnel_intake_conflicts.py -q`，预期新入口／服务缺失失败。
- [ ] 实现 `personnel_intake.submit`、`personnel_intake.review`、`personnel_intake.housing.read` 三能力。提交／所属读取默认授予 coach、team_manager、administration、admin、super_admin；审核仅 admin/super_admin 且须实际角色。教练限自身学员、队伍管理限有效负责队伍；兼任 both 须覆盖两个归属，行政仍不自审；显式拒绝优先。不授全局 people.create、账号或住房写能力。
- [ ] 实现规范化、版本条件、加密与占位：身份与邮箱跨本流程／people／login identifiers 检查；同一人复用不误报、同电话不合并。注册、认领、邮箱绑定／修改、管理员资料修改也遵守新占位规则。保持三种邮箱摘要域各自含义；统一从规范化邮箱计算 registration credential 锁，不直接跨域比较摘要。
- [ ] 固定锁序为 credential advisory locks → 请求行 → 排序 people（含 actor／监护人）→ membership keys → 排序 account → 队伍目录／队籍 → activation session → challenge；保持现有 team write_support 的 person→account→directory 顺序。现有资料更改入口先取快照计算旧新键，再加锁重读，快照变化返回可重试409，不在持有人行锁后反取 credential 锁。所有有关写入口使用同序，禁止审核中再次调用相反顺序的公开服务。
- [ ] 实现 GET `/personnel-intake/context`、POST/GET `/personnel-intake/requests`、GET `/{id}`、POST `/{id}/resubmit`、POST `/{id}/withdraw`（统一 `/api/v1` 前缀）；分页默认20／最大100，列表及 count 先过滤授权。所有成功／错误 no-store/private；越权详情私有404，冲突只说“资料存在冲突，请联系管理员核实”。审计不含原始负载。
- [ ] 同组重跑，并跑 `tests/db/test_registration_claim_concurrency.py tests/db/test_registration_contact_change_concurrency.py tests/db/test_account_identifier_concurrency.py`；独立连接同时争抢邮箱只允许一个合法持有者，错误响应无范围外资料，无死锁。
- [ ] scoped 提交 `feat: add scoped personnel intake and credential reservations`。

## Task 3: 原子审批、独立队员和有效负责人关系

**Files:** 新建 `personnel_intake/{approval_service,guardian_records,responsibilities,activation_state}.py`、`identity/student_numbers.py`；修改 `personnel_intake/{router,schemas}.py`、`identity/service.py`、`registration/finalization_service.py`、`team_management/{write_support,membership_service,query_service,schemas}.py`；新测试 `tests/services/test_personnel_approval.py`、`tests/api/test_personnel_approval.py`、`tests/db/test_personnel_approval_concurrency.py`；更新 `tests/services/test_team_memberships.py`、`tests/api/test_team_management.py`。

**Interfaces:** `PersonnelApprovalService.approve(actor: AuthContext, request_id: UUID, expected_version: int) -> ApprovalResult`、`return_request(actor, request_id, expected_version, reason: str) -> IntakeView`、`direct_create(actor, input: IntakeInput, idempotency_key: UUID) -> ApprovalResult`。`AssistedGuardianWriter.apply_in_transaction(person_id: UUID, guardians: list[AssistedGuardianInput], reference_date: date) -> tuple[UUID, ...]`；`CoachResponsibilityService.replace(actor, person_id: UUID, coach_person_ids: list[UUID], expected_version: int, reason: str) -> None`。

另产出 `correct_profile(actor: AuthContext, person_id: UUID, input: AssistedProfileCorrection) -> ApprovalResult`，input 为本人／监护资料、expected_version、reason、idempotency_key，不能改 kind 或代替调队；`activation_state.invalidate_in_transaction(db: Session, *, person_id: UUID, occurred_at: datetime) -> None` 供纠正资料与任务7调用。

- [ ] 写 `test_approval_without_accounts_creates_one_person_and_no_payments`：`assert player.student_profile is None`、`assert player.account is None`、`assert new_payment_count == 0`；both 只一 person，学生建 draft profile、不在读，无 enrollment/referral 新记录。`test_duplicate_approval_is_atomic` 断言两连接同审批只一份 person／approval／队籍，旧版本拒绝；`test_admin_correction_preserves_previous_approval_and_invalidates_proofs` 断言纠正生成新快照、旧快照不改、旧证明失效、旧账号来源不改。
- [ ] 加 `test_recheck_actor_submitter_and_team_at_approval`：角色／队伍负责人撤销、队伍停用、自己审核拒绝；混合 admin+coach 普通送审仍不能自审，显式 direct-create 可成功且审计类型不同。加第18生日边界、监护人未成年／循环／自关联／重叠及同监护身份证资料冲突测试。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_personnel_approval.py tests/api/test_personnel_approval.py tests/db/test_personnel_approval_concurrency.py -q`，预期审批服务未实现失败。
- [ ] 实现锁内重校验与一事务建档／关联、监护、approval、profile、教练关系和队籍；复用现有身份证规则，学号抽取 `registration/finalization_service.py:_student_number` 的相同生成算法至 `identity/student_numbers.py:generate_student_number(now: datetime) -> str`，两条路径调用，不引入新格式。关联已有人员不覆盖邮箱、队伍、账号状态；冲突需独立管理纠正。通过备注可空、退回 trim 后必填；重放仍先查当前权限。
- [ ] 拆分 `require_member_candidate(db, *, person_id: UUID) -> Person` 与保留 manager 的账号／角色检查；成员候选返回 `account_id: UUID | None`、account_status 新值 `not_activated`。保持一人一当前队伍、转队仅原超管流程和既有幂等。新 guardian writer 支持 player、邮箱可空，但旧 `add_guardian`／GuardianCreate 继续原契约。教练关系维护要求实际 admin/super_admin、people.update 范围及原因，不影响班级或队伍管理任命。
- [ ] 新增 POST `/personnel-intake/requests/{id}/approve|return`、POST `/personnel-intake/direct-create`、PUT `/personnel-intake/people/{id}/coaches`；审批快照绑定实际监护关系与身份版本。重跑本任务和 team memberships／team management／既有 guardians 回归，期望全通过；故意中断监护或队籍写入，断言全事务回滚。
- [ ] 新增 POST `/personnel-intake/people/{id}/corrections`，仅实际 admin/super_admin 且 people.update 对象范围允许；原因必填，锁内比对资料版本、更正或复核既有管理入口已修改资料，生成不可变新批准快照并更新 current approval。不能自动修复别的家庭监护资料；不覆盖已验证联系方式或改变既有登录标识。每次有效纠正调用 activation_state 撤销旧证明，后续资格按任务4重新计算；不要改变 account.assisted_approval_id 原始开户凭据。
- [ ] scoped 提交 `feat: approve assisted profiles and preserve team invariants`。

## Task 4: 新来源资格与账号失效联动

**Files:** 新建 `personnel_intake/eligibility.py`、`auth/account_readiness.py`；修改 `auth/service.py`、`identity/service.py`、`rbac/service.py`；新测试 `tests/services/test_assisted_eligibility.py`、`tests/services/test_account_access_readiness.py`；更新 `tests/api/test_auth_sessions.py`、`tests/services/test_guardians.py`。

**Interfaces:** `AssistedEligibilityPolicy.evaluate_many(person_ids: Sequence[UUID], *, reference_date: date) -> dict[UUID, AssistedEligibility]`；`AssistedEligibility(approved: bool, eligible: bool, kind: PersonKind | None, approval_id: UUID | None, version: int | None, missing: tuple[str,...])`。`AccountAccessReadinessPolicy.evaluate_one(account: UserAccount, *, reference_date: date) -> PersonReadinessProjection` 及 `evaluate_many(accounts: Sequence[UserAccount], *, reference_date: date) -> dict[UUID, PersonReadinessProjection]`。

- [ ] 写 `test_legacy_account_cannot_inherit_assisted_guardian_exception`：同一类完整监护档案，legacy 继续原拒绝，新来源可通过；审批既有停用账号后 `assert account.account_access_origin == 'legacy'` 且仍停用。写 `test_invalid_assisted_guardian_revokes_all_sessions_not_housing` 和跨第18生日测试。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_assisted_eligibility.py tests/services/test_account_access_readiness.py -q`，预期策略缺失失败。
- [ ] 实现来源限定分派：只有开户来源 assisted_activation、原始来源凭据合法及当前批准档案有效才走档案级监护；逐项核对实际关系、监护身份版本、成年、关系日期，不能凭审批 ID 认为永远就绪。legacy 原样调用 GuardianReadinessPolicy；招生与家庭业务不改用本策略。
- [ ] 将 login／authenticate 共用拒绝检查、disable_student_account_if_pending、disable_unready_dependents、enable_account 接入账号策略；资料修改、终止监护、禁用／删除监护账号、撤销 guardian 角色共同覆盖。档案级关系失效撤销新来源会话；单纯监护账号状态变化不错误否定有效的档案级监护。保持 person→account→session 锁序，资格修复不自动启用手工停用账号。
- [ ] 运行本任务测试及 `tests/services/test_readiness_policy.py tests/services/test_guardians.py tests/api/test_auth_sessions.py tests/services/test_registration_guardians.py tests/api/test_registration_guardians.py`；验证既有监护人删除／禁用仍影响 legacy，报名和家庭授权不被放宽。
- [ ] scoped 提交 `feat: isolate assisted account readiness from legacy guardian policy`。

## Task 5: 无账号人员住房全链路与名单兼容

**Files:** 新建 `housing/resident_readiness.py`；修改 `housing/{occupancy_service,schemas,reporting_projection,reporting_types,occupancy_export_service}.py`；Web 修改 `housing/{api,HousingStayDialog,RoomOccupancySlots,HousingRoomDetailPage,HousingReportDialogs,reportingApi}` 对应 ts/tsx 文件；新测试 `tests/services/test_assisted_housing_readiness.py`，更新原 housing 生命周期、候选、并发、投影和报表测试。

**Interfaces:** `HousingResidentReadinessPolicy.evaluate_many(person_ids: Sequence[UUID], *, reference_date: date) -> dict[UUID, HousingResidentReadiness]`；结果含 `housing_ready: bool`、`account_ready: bool`、`profile_ready: bool`、`guardian_ready: bool`、`missing: tuple[str,...]`。消费任务4资格；API 候选新增真实 `housing_ready`，不伪造 account_ready=true。person_type 新增 player／student_player，旧值与历史快照继续可读。

- [ ] 写 `test_approved_minor_without_accounts_can_complete_housing_lifecycle`：未审核候选为空，批准后 `housing_ready is True and account_ready is False`；可预约、确认入住、调房、双阶段退房。加 student/player/both、同性／容量／重叠冲突，both 不重复计数。
- [ ] 写 `test_invalid_profile_keeps_actual_occupancy_and_checkout`：监护失效后新安排／确认／调房受阻，`assert resident_id in visible_stay_person_ids`，已有住客／统计／有权导出仍在，可办实际退房。预定业务日第18生日按现有业务时间函数判断；新来源已审核电话可用于实名资料，但 `phone_verified is False`，旧来源实名校验不改。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_assisted_housing_readiness.py -q` 及 `npm test -- src/housing/HousingStayDialog.test.tsx`，分别预期旧账号条件／前端 residentReady 条件导致失败。
- [ ] 同时修改 `_selectable_resident_predicates`、`lock_selectable_residents`、`_require_resident_ready`、`list_resident_options` 与所有 create/backfill/update/confirm/transfer 调用，SQL 与最终锁内策略一致；候选用 outer join 与来源分支，不只是 UI 放行。checkout 保持基于真实入住的原授权，不额外加就绪门槛。
- [ ] 报表分类支持独立队员和兼任，未成年监护查询扩展队员；新的分类与可选标记采用向后兼容 schema，已封存快照／下载不重写。现有有权人员名单与住客导出加标签，不扩大权限。前端选择使用 housing_ready，并解释“未激活，不影响已审核人员入住”。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_assisted_housing_readiness.py tests/services/test_housing_resident_options.py tests/db/test_housing_stay_lifecycle.py tests/db/test_housing_transfer_checkout.py tests/db/test_housing_concurrency.py tests/db/test_housing_occupancy_projection.py tests/unit/housing/test_reporting_projection.py tests/unit/housing/test_reporting_snapshot.py tests/api/test_housing_reporting.py -q`；Web 跑上述五个 UI/API 的现有测试，预期全部通过。
- [ ] scoped 提交 `feat: allow reviewed accountless residents in housing`。

## Task 6: 所属人员住宿投影与旧全局读取旁路关闭

**Files:** 生产代码编辑面：新建 `personnel_intake/queries.py`；修改 `personnel_intake/{access,schemas,router}.py`、`housing/access.py`、`rbac/seed.py` 及新迁移中的精确旧授权删除。生产代码影响／核验面：`housing/{router,occupancy_router,reporting_router,occupancy_export_service}.py`、`portal/context_service.py`；逐一核验调用点及端点回归，已统一委托 `HousingAccessService` 的消费者保持不变，仅在发现实际旁路时修改，不要求无效编辑或重复守卫。测试编辑面：新建 `tests/api/test_managed_people_housing.py`、`tests/services/test_managed_people_housing.py` 及必要的访问／schema 单测；按确实变化的全局角色语义更新原 housing／permissions／portal-context／seed 测试，同时保留范围、拒绝和跨账号断言。测试核验面：原 invited-housing access／projection 与本人／家庭住房门户测试保持不变并运行回归，只有真实契约变化才修改断言；不得借此改变合法室友展示。

**Interfaces:** `ManagedPeopleQuery.list(actor: AuthContext, filters: ManagedPeopleFilters) -> ManagedPeoplePage`；filters 为 query/kind/team_id/housing_status/cursor/page_size(20,max100)。page 为 items/next_cursor/total；item 仅 person_id/full_name/nickname/kinds/authorized_affiliations/housing。housing 明确复用 `InvitedHousingSummary` 白名单及批量 projection，**不复用邀请人的授权或返回室友的门户 DTO**。

- [ ] 写 `test_owner_scope_applies_to_counts_pages_and_old_housing_routes`：`assert set(item.person_id for item in page.items) <= authorized_person_ids`；负责人A查B 的 person/team/page、房间详情／目录／总数／住客／报表／导出都无未授权信息；旧 coach global allow 和个别残留 allow 不能旁路。`test_mixed_administration_retains_scoped_management` 验证兼任行政只能管原楼层。
- [ ] 写 `test_revocation_removes_new_reads_without_changing_stays`，撤销教练负责／队伍负责／当前队籍后，新读取拒绝或不含该人；所有响应只白名单。参数化 planned/check-in/checkout/long-term/no-stay 状态，断言不读取未授权 ID 的摘要、不按每人 N+1 查库。
- [ ] 运行 `"$PYTHON" -m pytest tests/api/test_managed_people_housing.py tests/services/test_managed_people_housing.py -q`，预期新投影缺失及旧全局授权测试失败。
- [ ] 实现 GET `/personnel-intake/managed-people`，先当前关系过滤再 count／分页／批量住房投影；角色＋能力＋关系共同判定，权限拒绝优先；读取前刷新关系，no-store/private。不可凭 created_by 或历史负责关系取得资格。
- [ ] 移除 seed 中 coach 的默认 housing.read/global；迁移仅删除这一项旧角色授予，不批删手工覆写。`HousingAccessService.can`／room/floor SQL criteria 保持一致：普通教练／队伍管理不可进入全局房态读 API；合法行政／管理员混合角色仍须其实际范围授权。overview/count/catalog/detail/report/download/export及菜单能力同步，保留本人／家庭门户独立权限。
- [ ] 重跑本任务及 `tests/unit/housing/test_access.py tests/api/test_housing.py tests/api/test_housing_occupancy.py tests/api/test_housing_reporting.py tests/api/test_permissions.py tests/services/test_invited_housing_access.py tests/services/test_invited_housing_projection.py tests/services/test_housing_portal.py`；迁移重复 seed 后教练全局 grant 不复活。
- [ ] scoped 提交 `fix: scope staff housing reads to assigned people`。

## Task 7: 仅邮箱、两阶段证明、原子开户的激活 API

**Files:** 新建 `personnel_intake/{activation_schemas,activation_service,activation_router}.py`；修改 `registration/{verification_service,start_limiter,delivery,routing}.py`、`personnel_intake/{locks,approval_service}.py`、`identity/service.py`、`auth/models.py`、`api/router.py`、`audit/service.py`；新测试 `tests/services/test_assisted_activation.py`、`tests/api/test_assisted_activation.py`、`tests/db/test_assisted_activation_concurrency.py`。

**Interfaces:** `AssistedActivationService.start(email: str, request_source: str) -> ActivationIssued`；`verify(session_token: str, code: SecretStr) -> ActivationPreview`；`finish(session_token: str, code: SecretStr, password: SecretStr) -> ActivationCompleted`。`ActivationIssued(public: ActivationStart, session_token: SecretStr)` 不可直接序列化，公开 DTO 仅 accepted/resend_available_at/expires_at，不返回 person/是否存在；路由仅将 public 返回，token 设 HttpOnly Secure SameSite cookie，限定激活 API path，服务端仅 HMAC。preview 只 masked_name/kind；completed 仅 completed=true，不是登录凭证。

- [ ] 写 `test_activation_preserves_person_and_creates_only_verified_email`：同 person/id/guardian/team/stay，账号新来源、仅 student/team_member 必要角色，邮箱 verified、电话无验证标记，无 erp_session cookie。并断言未成年登录与 authenticate 成功、legacy 仍受原约束。
- [ ] 写 `test_preview_does_not_consume_challenge_and_finish_is_atomic`：预览后 `assert challenge.consumed_at is None`、`assert session.state == 'verified'`，final 同事务开户与消费；错误密码政策失败不产生账号，重试成功；相同完成会话重放只返回成功不新建。并发 finish／旧注册／邮箱更正只有一个合法结果，旧版本不可激活，已存在／停用／锁定账号不改状态。
- [ ] 写 `test_shared_otp_limits_and_private_delivery_failure`：固定时钟测10分钟、60秒、5邮箱/24h、20来源/h和5错误，跨注册／找回共用；入口未知邮箱也记预算。错误用途、改收件人、伪 token、过期、关联错误均拒绝。匹配／未知／未审／已开户响应结构一致，SMTP 异常不创建账号，不记录验证码。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_assisted_activation.py tests/api/test_assisted_activation.py tests/db/test_assisted_activation_concurrency.py -q`，预期新服务／路径缺失失败。
- [ ] 实现 start 仅为合法批准、未开户目标调用现有 issue，purpose=`assisted_activation`，sender 收件人来自锁内档案。未知与不合格输入返回同长度随机无权限 session token 和同一公开结构，不建立真实挑战、不发 decoy 邮件；入口预算在查找前处理。给 `RegistrationStartLimiter` 增加 `record_activation(identifier_digest: str, request_source: str) -> None`，调用原共享 record，并对所有格式合法邮箱统一执行60秒申请间隔，不能用短间隔是否429判断有无档案。OTP 与证明不放 URL／日志。
- [ ] 在 `verification_service.py` 抽出 `verify_unconsumed(challenge_id: UUID, code: str, *, expected_target: VerificationTarget) -> VerificationChecked`（challenge_id/expires_at），保留行锁、HMAC、错误计数提交，成功不消费；外层事务失败不能回滚已应生效的猜测次数，测试通过独立会话确认5次封闭。预览绑定并锁 activation session，复核 approval/version/email/purpose 后标 verified；证明不延长原10分钟。finish 再提交内存中的 code 并调用原 consume_with：prepare_target 按共用锁序重查，on_success 同事务新建账号／登录邮箱／必要角色、完成 session。其他用途不能使用此证明，既有 consume_with 语义不改。
- [ ] POST `/auth/profile-activation/start|verify|complete`，匿名写仍受现有同源／Origin 防护和限流。统一受理文案为“请求已受理；如未收到邮件，可能资料尚未获批或邮件发送失败，请稍后重试或联系管理员”，绝不显示“已发送成功”；响应给60秒重试时间。全局邮件服务不可用在档案查找前对所有邮箱一致返回可重试503；个别 SMTP 失败记录私有失败事件、撤销无效证明，保持公开受理结果不可枚举且不宣称投递成功。
- [ ] 管理端更正未开户激活邮箱：受 people.update 范围及实际管理角色控制，生成新的批准资料版本／预留，原子撤销旧 challenges/sessions；不允许审批或档案修改覆盖已开户登录标识。资格失效时既有账号联动任务4；全部成功／错误 private,no-store。
- [ ] 重跑本任务和 `tests/services/test_registration_verification.py tests/db/test_registration_verification_concurrency.py tests/db/test_registration_claim_concurrency.py tests/db/test_registration_contact_change_concurrency.py tests/db/test_account_identifier_concurrency.py tests/api/test_auth_sessions.py`，全部通过且没有未结束事务／并发死锁。
- [ ] scoped 提交 `feat: activate approved profiles with scoped email verification`。

## Task 8: 工作人员表单、审核队列、所属人员和名单显示

**Files:** 新建 `apps/web/src/assistedPeople/{api.ts,keys.ts,useAssistedPeopleContext.ts,AssistedPeopleRoutes.tsx,AssistedPersonForm.tsx,MyAssistedRequestsPage.tsx,AssistedRequestReviewPage.tsx,ManagedPeoplePage.tsx,CoachResponsibilityPanel.tsx,AssistedProfileCorrectionPanel.tsx,assisted-people.css}` 及同名测试；修改 `app/router.tsx`、`layout/AppShell.tsx`、`people/{api,presentation,PeoplePage,PersonDetailPage,GuardianPanel}` 和 `teams/{api,presentation,TeamActionDialogs,TeamManagementPage,MyTeamPanel}` 对应 ts/tsx 文件；API 修改 `identity/{schemas,service}.py`，新建 `tests/api/test_people_assisted_projection.py`。

**Interfaces:** `useAssistedPeopleContext(): UseQueryResult<AssistedContext>`；`AssistedPersonForm({initialValue?: IntakeInput, onSubmit: (input: IntakeInput) => Promise<void>, submitting: boolean})`；`CoachResponsibilityPanel({personId: string})`、`AssistedProfileCorrectionPanel({personId: string})`；其余为无参数路由组件。`api.ts` Zod DTO 严格对应任务1–7，管理候选保持非空 account_id，队员候选允许 null；不扩展全局 portal capability 契约。

- [ ] 写 `test_submit_review_and_role_specific_navigation`：只见有权限子入口，无全局 people.list 的教练也可进入自己的申请；必填本人信息／未成年监护，监护邮箱可空；通过无备注、退回必填原因；普通提交不出现自动通过。`test_revoked_or_switched_account_cannot_render_late_response` 用 `expect(screen.queryByText(privatePersonName)).not.toBeInTheDocument()` 断言清空私有数据、迟到响应不重现。
- [ ] 写 `test_accountless_player_labels_and_housing_actions`：人员／队伍名单显示队员、未激活及正确人数，不显示已删除；both 一人；住房页仅使用任务5真实就绪标记；负责人新名单没有敏感字段、入住／调房／退房／导出按钮。
- [ ] 运行 `npm test -- src/assistedPeople src/people/PersonDetailPage.test.tsx src/teams/TeamActionDialogs.test.tsx src/layout/AppShell.test.tsx`，预期缺组件／旧 DTO 导致失败。
- [ ] 实现人员中心子入口 `/people/assisted-requests`、`/people/assisted-review`、`/people/assisted-create`，独立 `/my-people`“我的学员／队员”；导航按 context 和原子权限，父菜单未选中默认折叠。复用已有样式、日期和掩码，独立严格表单不把旧 PersonForm 必填规则全局改变；管理建档单独明确确认。
- [ ] 实现 account-keyed query、AbortSignal、staleTime/gcTime=0、焦点回归权限检查；切账号／撤权立即清数据，表单按 account ID 隔离，只存组件内存。页面 focus 不 remount 正在编辑的表单，不清空未提交内容；展示明确版本冲突，按允许动作重新提交。教练关系管理员维护，队伍负责人仍原超管入口。
- [ ] 人员 backend 只在授权 projection 增加档案级 kind／activation 状态；调整 `identity/schemas.py`、`identity/service.py` 的名单／数量以去重 person，并增加 `tests/api/test_people_assisted_projection.py`。不让可见新名单升级为完整档案查看；独立队员／新流程资料更正用 AssistedProfileCorrectionPanel，复用任务3 corrections 接口、版本及必填原因，只向有权管理人员展示；旧学生 GuardianPanel 契约保留。
- [ ] 运行 `npm test -- src/assistedPeople src/people src/teams src/housing src/app/router.test.tsx src/layout/AppShell.test.tsx` 及新增 API projection 测试，全部通过；`npm run build` 检查接口类型一致。
- [ ] scoped 提交 `feat: add assisted personnel and owner housing pages`。

## Task 9: 激活页面与 iOS 匿名写兼容

**Files:** 新建 `apps/web/src/auth/{ActivateProfilePage.tsx,ActivateProfilePage.test.tsx,activationApi.ts,activationApi.test.ts}`；修改 `auth/LoginPage.tsx`、`app/router.tsx`、`platform/compatibility.ts`、相关 LoginPage／NativeAuthComposition／兼容测试。

**Interfaces:** `ActivateProfilePage(): JSX.Element`，请求函数 `startProfileActivation(email: string): Promise<ActivationStart>`、`verifyProfileActivation(code: string): Promise<ActivationPreview>`、`completeProfileActivation(code: string, password: string): Promise<ActivationCompleted>`；token 由 HttpOnly cookie 管理，不由 JS 读取，code/password 只存当前组件内存。

- [ ] 写 `test_email_preview_password_flow_returns_to_login`：初始不显示姓名，验证后仅脱敏提示；可看／隐藏密码；激活成功 `expect(location.pathname).toBe('/login')` 且不建立会话。`test_no_success_claim_on_unconfirmed_delivery` 验证任务7统一文案、429 Retry-After、可重试故障、不接受手机模式。
- [ ] 写 `test_native_anonymous_activation_allowlist_is_exact`：仅三个 POST 激活路径可通过平台兼容，伪子路径／管理接口仍拒绝；cookie 模式、web transport、CSRF/Origin 行为不因新增而放宽。发码／注册切后台焦点回来不重置步骤或输入。
- [ ] 运行 `npm test -- src/auth/ActivateProfilePage.test.tsx src/auth/activationApi.test.ts src/auth/NativeAuthComposition.test.tsx`，预期新页缺失或 native 写被旧 allowlist 拒绝。
- [ ] 实现 `/activate-profile`，登录页“激活已有档案”链接；复用 RegistrationLayout、PasswordInput、现有 mobileViewport，不新增滚动容器。NativeAccessBoundary 不因每个步骤重新挂载；权限仍由服务器，成功清除验证码／密码／预览，失败不显示完整档案。
- [ ] 重跑本任务及 `npm test -- src/auth src/registration src/platform`、`npm run build`、`npm run lint`，预期通过；原自助注册／忘记密码入口不消失、未改变手机申请开放配置。
- [ ] scoped 提交 `feat: add profile activation page across web and iOS compatibility`。

## Task 10: 隔离全链路验收和跨域回归

**Files:** 新建 `apps/api/app/cli/seed_assisted_personnel_e2e.py`、`apps/api/tests/cli/test_seed_assisted_personnel_e2e.py`、`scripts/run-assisted-personnel-e2e.sh`、`apps/api/tests/cli/test_assisted_personnel_e2e_launcher.py`、`apps/web/tests/assisted-personnel.spec.ts`、`docs/features/assisted-personnel.md`；使用现有注册／队伍／房态 E2E，不改其业务断言来迎合新流程。

**Interfaces:** `seed_assisted_personnel_e2e.main() -> None` 仅已验证一次性 DB；launcher 输入 TEST_DATABASE_URL/E2E_API_PORT/E2E_WEB_PORT/E2E_PYTHON_BIN，检查端口、测试 DB 和自有 PID，创建0700临时目录、0600测试邮件记录，finally仅清本次产物。运行 desktop-1440、mobile-360；发送器必须测试专用。

- [ ] 写 `test_launcher_refuses_unowned_database_ports_and_real_smtp`，断言不触碰 NAS／未知容器／占用端口／生产 sender；先运行 `"$PYTHON" -m pytest tests/cli/test_assisted_personnel_e2e_launcher.py -q` 看到缺脚本失败，再实现 launcher 和虚拟种子。种子含成年／未成年学生、独立队员、兼任、旧停用账号、两个不同负责人及有范围的行政。
- [ ] 写浏览器 `reviewed accountless player can stay then activate original profile`，串联提交→退回修改→他人批准→行政入住→负责人只读→邮箱验证→本人登录→恢复／退出，`expect(after.person_id).toBe(before.person_id)`，精确比对队籍／住宿不变；`revoked manager cannot revisit owner or old housing pages` 覆盖旧全局 grant、迟到响应和直接 URL。
- [ ] 运行根目录 `bash scripts/run-assisted-personnel-e2e.sh`，先记录真实业务失败，补仅本功能遗漏后重跑两 viewport。流程中旧报名／付款／分成计数不增长；并分别执行 `bash scripts/run-team-management-e2e.sh`、`bash scripts/run-self-service-registration-e2e.sh`、`bash scripts/run-housing-occupancy-e2e.sh`、`bash scripts/run-housing-reporting-e2e.sh`，各自使用本次 DB 和空闲端口，串行重置。
- [ ] 运行 API 全量 `"$PYTHON" -m pytest --tb=short`（设置本次 TEST_DATABASE_URL），`ruff check app tests migrations`、`mypy app`；Web 全量 `npm test -- --maxWorkers=4`、`npm run build`、`npm run lint`。归档脱敏统计；任何失败逐个定位，不以“本轮无关”默默跳过。迁移／真实 DB 测试不能全部 SKIP。
- [ ] iOS 先跑现有匿名兼容／AuthProvider 和原生构建门禁；新页面至少模拟器验证空表单软键盘、焦点切换、步骤保持，不使用正式账号。沿用已有模拟器环境前核对所有权／信任状态，不自动安装证书或动手机；若无可用环境，明确原生验收待办，不把 desktop/mobile-360 标成 iOS通过。真机只有新授权和测试环境就绪后另验收。
- [ ] 补功能文档中的状态、权限、激活指引、错误处理和无短信说明；scoped 提交 `test: cover assisted onboarding housing activation end to end`，记录规格13条验收分别对应测试与实测结果。

## Task 11: 发布打包安全、备份恢复和 NAS 验证

**Files:** 修改经过核实的实际执行入口 `scripts/deploy-nas.sh` 及 `scripts/test_deploy_nas_packaging.py`、`scripts/test_deploy_nas.py`；新建 `docs/operations/assisted-personnel-release.md`。启动仓库脚本与实施工作树脚本若不同，只对本任务必需过滤改动同步并分别测试，不覆盖既有差异。

**Interfaces:** 保持 deploy-nas.sh CLI、凭据和卷行为；snapshot 与 outbound 两组过滤均排除 `outputs/`、`*.log`、`*.xcresult/`、本次测试凭据／邮件箱及现有 runtime/secrets 目录，保留业务源码和迁移。不得把日志作为 Web 静态文件发布。

- [ ] 写 `test_both_release_filters_exclude_acceptance_artifacts`：临时文件树包含 token样本、测试邮箱、日志、xcresult、正常源和0066迁移；实际 rsync 两过滤组后 `assert artifacts_absent and source_present`。运行 `"$PYTHON" scripts/test_deploy_nas_packaging.py`，预期当前过滤缺项失败。
- [ ] 实现过滤并重跑 packaging／deploy 单测；使用 `NAS_DEPLOY_TEST_LAUNCHER` 分别指向实施脚本和启动仓库真正入口，测试实际待执行的脚本，不只测另一个副本。确认 release source 只有已验收基线和本功能，运行 `git diff --check`、单 head 检查、源码指纹／发布清单审查，移除测试种子中的真实数据风险。
- [ ] 在隔离数据库验证加密备份恢复后的新表、约束、账号来源和原数据；新业务数据出现后不降级删表，优先前向修复。记录旧版本兼容评估与恢复窗口；恢复备份会丢失窗口后写入，未经用户批准不在 NAS 执行破坏性恢复。
- [ ] 请求整体验证／代码审查，修复发现并重跑对应检查；进入 finishing-a-development-branch 技能，但不因技能选项绕过用户指定 NAS 脚本。scoped 提交 `chore: harden assisted personnel release and recovery gates`，在完整验收前保持 hook deployment 有意暂停。
- [ ] 从 `/Users/skymoonzyj/Documents/new project3` 执行 `scripts/deploy-nas.sh --source "$VERIFIED_SOURCE"`，其中 VERIFIED_SOURCE 必须是上一步记录的已验收隔离源绝对路径，不是默认旧同步目录。失败立即发布阻塞，不直拷或临时 Docker 更新，不更改域名／18080 HTTPS。
- [ ] 发布后只读验证 `.active-release` 源指纹、实际 Alembic head、容器健康／scheduler、静态资源版本和公开健康端点；通过既有安全管理员验收方式核对菜单，绝不临时创建真实住房／账号／付款。记录备份标识、恢复证据、测试数量、NAS结果及仍未覆盖的 iOS 实机情况后交付。

## 主代理计划自审与执行交接

- 规格覆盖：提交／状态／冲突→任务1–3；开户来源与监护边界→任务1／4／7；无账号队员、住房与导出事实→任务3／5；旧新权限入口→任务2／6／8；邮箱激活与原生兼容→任务7／9；验收／迁移／隔离发布→任务10／11。
- Review Focus 五项分别有具名测试；来源不可变、guardian player 分支、真实住房事实、旧全局读入口、原生匿名写 allowlist 不作为可选优化。
- 接口审查：统一 AuthContext、PersonKind、IntakeStatus；`housing_ready` 与 `account_ready` 不混用；activation session 持有 challenge FK，验证码预览不消费，最终仍使用 consume_with；不将手机号标成已验证。
- 数据库测试串行；分工可以并行，但迁移／路由／共享权限文件由主代理协调。每项先RED后GREEN，再进行任务级审查；共用接口修改必须回查下游任务。
- 当前规格及计划均已确认，实现完成且源码已审核；测试证据和剩余最终验收见功能／发布文档。NAS 尚未部署；原生验收不由 Web 结果替代。执行沿用已确认的子任务协作方式，不再次询问已明确的业务规则。
