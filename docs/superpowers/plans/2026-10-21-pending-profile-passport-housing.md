# 待补资料建档 护照支持和待划分队伍 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 管理员先为真实人员建档和安排住宿，缺失资料在本人激活前补齐，队伍由管理员另行划分；兼容护照和其他工作人员，保留现有人员、账号及住宿历史。

**Architecture:** 两部分共用人员与审批数据，采用一个集成计划。增加显式待补模式及版本化审批事实，分开住宿、补录、激活和登录资格；护照使用类型化身份键；队伍划分使用独立状态和现有真实关系，不创建虚拟队伍。沿用邮箱验证、人工审核和唯一开户事务，不全局降低普通注册要求。

**Tech Stack:** 现有 FastAPI、SQLAlchemy、PostgreSQL、Alembic、React、TypeScript、React Query、Vitest、Playwright；沿用加密、验证邮件和发布组件，不增加外部认证服务。

**Spec:** `docs/superpowers/specs/2026-10-21-pending-profile-passport-housing-design.md`，用户于 2026-10-21 确认；`2026-10-20-deferred-email-profile-claim-design.md` 是已实现的认领基线，其冲突要求仅在明确的待补模式中由新规格覆盖。

状态：用户于 2026-10-21 确认实施计划，开始按任务执行。执行进度见本计划独立 SDD ledger；任务通过、NAS 发布和真实人员导入分别核验，不以计划批准代表完成。

## Global Constraints

- 姓名和真实证件必须提供。中国身份证继续检查格式、出生日期、性别一致性及校验位，保留加密存储、去重和敏感字段查看权限。
- 联系方式、监护资料、负责教练或队伍允许暂缺。缺失项由服务端计算并展示，不能用假邮箱、假手机号、默认监护人或虚构归属代填。
- 建档不创建 `UserAccount`、密码、邮箱验证凭据或 `UserRole`。既有人员不因本次默认分类而降级、改角色或重新建人。
- 工作人员和类别不明的新记录使用 `StaffProfile.other`；表格原岗位或项目描述保留为内部备注，不能据此分配教练、队伍或账号角色。
- 性别属于实际安排住宿的必要信息。不能猜填性别或取消房间性别限制；房间核对、容量、性别、时间重叠和目录版本检查不变。
- 队伍或教练尚未划分不阻挡建档、入住、本人补资料或账号激活；激活后的功能仍按真实权限开放。
- 未成年人激活前必须有完整、有效的成年监护资料；监护资料登记不等于监护账号授权、家庭查看权或已经取得监护同意。
- 不直接放宽目前共用的 `AssistedEligibility.eligible`。历史审批、已有账号和普通严格建档继续验证原有证据。
- 旧记录保留原中国身份证类型、摘要和严格规则，不批量改变既有账号权限或把历史资料标记为已验证。
- 无入住日期使用实际写入当天的中国业务日期；无退宿日期为长期入住；已有实际入住和调房历史不覆盖。
- 1 幢继续排除；李子恒保留 4-301；原已完成的 20 人记录保留；剩余 47 人分别核实建档和实际入住结果。
- 不给真人发送测试验证码，不在 NAS 创建测试账务或测试入住。公开响应、日志、URL、浏览器存储不暴露完整证件或补录负载。
- 适用检查通过后执行工作源中的 `scripts/deploy-nas.sh`；预检、备份、迁移或发布失败即停止，不以直拷或临时 Docker 操作绕过。

## Review Focus

1. 未知签发国家与已知国家的同号护照并发建档：不得重复建人或凭同名合并，补国家仍保留原人及住宿（任务 1、7）。
2. 管理员已填邮箱但监护资料未补齐：必须能进入补录，不能卡死，也不能通过普通注册或管理员开户绕过审核（任务 3、4、7）。
3. 审核、补资料、撤销和最终激活交错：旧证明不得复活，失败回滚后昵称、备注、住宿和既有权限不变（任务 2–4、7）。
4. 只有人员查看权而没有队伍查看权：筛选、计数、缓存和迟到响应都不能泄露归属或把隐藏队伍误认为待划分（任务 5、6）。
5. 护照生日或性别为空：列表、详情、备注编辑和导出仍可用；未知年龄不视为成年，缺性别不可入住（任务 1、2、6、7）。

## 工作目录和验证约定

实施源 `P=/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`。本计划所有相对路径均以 P 为根；下文 API 短路径 `app/`、`tests/`、`migrations/` 以 `P/apps/api` 为根，Web `src/` 以 `P/apps/web` 为根。花括号表示所列各个文件，不表示目录整体所有权。

P 是已有 linked worktree，包含大量已发布但未提交的修改，当前源码迁移头为 `0068_deferred_email_claims`。实施前按 using-git-worktrees 核对附件和 dirty 清单，复用这个完整工作源；不能只从 HEAD 新建而遗漏现有修改。主代理记录本轮差异，禁止 `git add .`、reset 或替换既有文件。

API 命令 cwd=`P/apps/api`，Web 命令 cwd=`P/apps/web`，脚本 cwd=P。工具变量：

```sh
PYTHON='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'
RUFF='/Users/skymoonzyj/Documents/new project3/.venv/bin/ruff'
MYPY='/Users/skymoonzyj/Documents/new project3/.venv/bin/mypy'
```

开始时确认工具和本轮独占的本机回环临时 PostgreSQL；使用以 `_test` 结尾的空库，设置 `TEST_DATABASE_URL` 后运行 `"$PYTHON" -m app.cli.e2e_database_safety`，必须 exit 0。数据库测试会重置数据，主代理统一串行执行，不让不同任务并发使用同一库。缺库导致 SKIP 不算通过。浏览器使用现有隔离 launcher 和虚拟身份，不复用 NAS 会话。

每任务保留 RED→GREEN 证据并独立审查。提交仅限可独立暂存的本轮差异，命令为 `ERP_NAS_AUTO_DEPLOY=0 git commit -m '<任务消息>'`，明确暂停中间发布；无法隔离历史修改时保存审查 diff、不提交混合文件，记录原因。最终任务必须运行正式发布脚本，不能把提交或单个容器健康视为发布完成。

## 公共接口和文件所有权

- 主代理维护迁移、模型、公共 DTO、路由及发布整合；这些共享文件只由一个写入者维护。后端实现、前端、测试与安全审查可分流，但代码依赖按任务顺序推进。
- 任务 1 产出证件/数据契约；任务 2 产出待补建档与分用途资格；任务 3→4 完成本人补录和激活；任务 5 可在任务 1 后独立推进；任务 6 按已冻结接口并行开发；任务 7→8 串行验收和发布导入。
- `DocumentType = Literal["cn_id", "passport"]`；`IntakeMode = Literal["strict", "pending"]`；`PersonKind` 增加 `staff`。普通 strict 输入不因此允许空手机号、监护资料或任意工作人员岗位。
- `TeamAllocationStatus = Literal["untracked", "pending", "assigned", "not_required"]`。旧人员默认 untracked，明确需要稍后分队的新档案为 pending。状态不是角色或临时队伍。
- `PendingIntakeInput` 含真实姓名、证件类型/号码、可空护照签发国家、可空 phone/email、可空护照 sex/date_of_birth、已有 guardian DTO、kind、可空 coach/team、显式 pending/not_required 选择。证件/联系方式使用 SecretStr，extra=forbid；CN 性别和生日由有效证件核对，不允许 CN 空事实。
- 新待补档案的 `activation_requirement` 一律为 `reviewed_claim`，包括已填写未验证预留邮箱者；旧 strict reserved_email 不变。pending 来源永久保留，资料补齐后不能转换为 reserved_email。pending 已预留邮箱只接受同邮箱补录，换邮箱须现有授权更正流程，不能公共覆盖。
- 版本 1 历史加密负载、摘要与审批事实保持字节兼容；新 pending 使用显式版本 2。缺少版本字段按原严格版本处理，未知版本拒绝，不因解码兼容得到 pending 豁免。

## Task 1 类型化证件和可恢复存储

**Files:** 新建 `apps/api/app/identity/document_identity.py`、`migrations/versions/0069_pending_profile_intake.py`、`tests/db/test_pending_profile_migration.py`、`tests/db/test_document_identity_concurrency.py`；修改 `app/identity/{models,schemas,service,readiness}.py`、`app/personnel_intake/{models,schemas,locks}.py`、`app/personnel_intake/claims/models.py`、`migrations/env.py`、`scripts/db-schema-manifest.sh`、`tests/db/test_migrations.py`、`tests/cli/test_operations_docs.py`、`tests/cli/test_local_container_acceptance_launcher.py`。

**Interfaces:** 新建 frozen `NormalizedDocumentIdentity(document_type: DocumentType, number: str, issuing_country: str | None, sex: str | None, date_of_birth: date | None)`、`DocumentIdentityKeys(identity_digest: str, passport_number_digest: str | None, issuing_country: str | None)`；`normalize_document_identity(document_type: DocumentType, number: str, *, issuing_country: str | None, sex: str | None, date_of_birth: date | None, allow_pending: bool) -> NormalizedDocumentIdentity`；`document_identity_keys(document: NormalizedDocumentIdentity, lookup: LookupDigest) -> DocumentIdentityKeys`；`require_document_available(db: Session, keys: DocumentIdentityKeys, *, exclude_person_id: UUID | None = None) -> None`；`mask_document_identity(document_type: DocumentType, number: str) -> str`。

- [ ] 写 `test_cn_digest_and_old_data_unchanged`、`test_only_pending_passport_allows_unknown_facts`：`assert new_cn_digest == lookup.digest(old_normalized_number)`；`assert after_people_accounts_stays == before_people_accounts_stays`；普通人员插入 NULL sex/DOB 失败，明确 pending passport 可存；unknown age 不等于 adult。
- [ ] 运行 `"$PYTHON" -m pytest tests/db/test_pending_profile_migration.py tests/unit/identity/test_normalization.py -q`，确认 RED 来自缺新契约，而不是数据库连接错误。
- [ ] 实现类型化规范化与加密：护照原号保存，匹配值仅去首尾空白并大写，不套中国身份证校验；签发国家使用校验后的 ISO 两字母代码。CN 摘要原样保留；护照精确摘要用独立 domain `passport-identity` 编码 country/number，未知 country 用显式 null；另存 domain `passport-number` 的裸号 HMAC 及非唯一索引。所有新建、改证件、补国家先取同裸号锁；未知 country 与任何同号候选冲突转人工，已知且不同 country 可区分，不自动合并。
- [ ] 追加迁移，旧 `Person` 默认 cn_id/strict/untracked；增加证件国家、护照裸号摘要、明确待补来源、allocation/version。sex/DOB 仅 pending passport 可以为空，其他情况用 CHECK 保持完整；账号插入和人员事实变更增加事务结束约束，禁止无必要身份事实的人同时持有账号。扩展 staff intake CHECK、新审批 operation `claim_complete`、claim binding 的 `email_binding_origin`（introduced/preserved，旧 binding 为 introduced）。版本 2 材料沿用现有加密列，不覆写旧快照。安全降级只允许没有新增业务事实，拒绝丢弃护照/待补事实的 downgrade。
- [ ] 将 `_new_person` 和 `update_person` 的证件处理接入新接口，保证护照仅改昵称备注也不触发 CN 校验；普通 CN 创建函数签名兼容。新增 `age_status_for(birth_date: date | None, reference_date: date) -> AgeStatus`（adult/minor/unknown），保留 `is_minor(date, date)` 严格签名，调用处显式处理 None。
- [ ] GREEN：重跑迁移/规范化测试及 `tests/db/test_document_identity_concurrency.py`，断言未知↔已知 country 竞争只有一个合法结果、补 country 冲突回滚、原 Person ID 不变；manifest/head 测试全部通过。审查后提交 `feat: support pending passport identity storage`。实施前如 0069 已被其他工作占用，顺延新迁移及所有引用，不修改已上线迁移。

## Task 2 管理员待补建档和住宿资格

**Files:** 新建 `app/personnel_intake/readiness.py`、`app/personnel_intake/approved_facts.py`、`tests/api/test_pending_personnel.py`；修改 `app/personnel_intake/{codec,guardian_records,eligibility,approval_service,service,access,router,editor_context}.py`、`app/housing/{resident_readiness,occupancy_service}.py`；测试 `tests/services/{test_personnel_approval,test_assisted_eligibility,test_assisted_housing_readiness}.py`。

**Interfaces:** 消费任务 1 DTO 和证件键；`normalize_pending_payload(input: PendingIntakeInput) -> PendingIntakePayloadView`；`PersonnelApprovalService.direct_create_pending(actor: AuthContext, input: PendingIntakeInput, idempotency_key: UUID) -> ApprovalResult`；`PendingProfileCorrection` 使用待补事实字段加 `expected_version: int, reason: str, idempotency_key: UUID`，禁止 kind/roles/coach/team/assignment；`correct_pending_profile(actor: AuthContext, person_id: UUID, input: PendingProfileCorrection) -> ApprovalResult`。`AssistedReadinessPolicy.evaluate_many(person_ids: Sequence[UUID], *, reference_date: date, original_approvals: Mapping[UUID, UUID | None] | None = None) -> dict[UUID, AssistedReadiness]`。结果包含 `approval_valid/housing_ready/completion_allowed/activation_profile_ready: bool`，`approval_id: UUID | None, version: int | None, kind: PersonKind | None`，`profile_missing/housing_missing/activation_missing: tuple[str, ...]`。

- [ ] 写 `test_pending_minor_can_reside_but_cannot_activate`：`assert readiness.housing_ready`、`assert not readiness.activation_profile_ready`；`assert new_accounts == new_login_identifiers == new_roles == 0`。写 `test_pending_creation_requires_global_admin_boundary`：有单队 scope、教练或仅人员查看权者不能直接批准无归属档案，旧代建申请仍需管理员审核。
- [ ] 运行 `"$PYTHON" -m pytest tests/api/test_pending_personnel.py tests/services/test_assisted_housing_readiness.py -q` 确认 RED。
- [ ] 增加独立 `/personnel-intake/direct-create-pending` 管理入口，使用现有实际管理员身份与全局直接建档能力，锁内重检；普通 submit/direct-create 继续严格。已提供事实仍校验，未知类别由管理员明确选 other staff；不把现有人员改类型。新请求按幂等键和类型化证件去重；未验证外国联系电话仅保存联系事实，不能生成手机号登录标识。归属留空不创造临时关系。
- [ ] 增加 `/personnel-intake/people/{person_id}/pending-corrections`，沿用管理员+目标 people.update 边界，只更正明确待补且未激活档案，补护照性别/国家时也生成可信后继审批、检查证件冲突并失效旧证明。普通已激活资料编辑不取得此豁免；保持昵称备注与住房不变。写 `test_pending_passport_correction_enables_housing_without_new_person`，断言同 person_id、无账号、缺性别前拒绝/真实补充后允许入住。
- [ ] 将认证审批事实加载提取到 `approved_facts.py`，同时供原严格 eligibility 和新 readiness 使用；版本 2 用认证的 null 表示缺项。新 readiness 对 pending 的住房仅放宽联系方式/监护/归属，仍要求有效批准、本人身份、可靠性别；激活完整度要求本人联系方式/生日/性别/护照国家及必要成年监护资料，但不要求教练或队伍。原 strict 路径不变，批量投影不能变成逐人查询。
- [ ] 入住候选和实际入住写事务都消费同一 housing_ready，保留核房、容量、日期冲突、混住限制和目录版本；明确 staff 类型，不落入“非学员=队员”分支。已入住者后续资料待补不自动退房。缺监护时不创建伪 guardian relationship；已有真实关系不覆盖。
- [ ] GREEN：运行本任务全部测试和 `tests/api/test_housing_occupancy.py`、`tests/api/test_person_housing.py`。断言缺性别拒绝入住、旧严格缺监护仍拒绝、同 key 重试不重复审批或入住、写入失败无残留；审查后提交 `feat: separate pending intake and housing readiness`。

## Task 3 本人补资料和管理员核对

**Files:** 修改 `app/personnel_intake/claims/{schemas,proof,public_service,queries,review_service,binding}.py`、`app/personnel_intake/approval_service.py`；新增 `app/personnel_intake/claims/completion.py`；测试 `tests/services/{test_profile_claims,test_profile_claim_review,test_profile_claim_binding}.py`、`tests/api/{test_profile_claims,test_profile_claim_review}.py`、`tests/unit/test_profile_claim_security.py`。

**Interfaces:** `ClaimCompletionFields(phone: SecretStr | None, sex: Literal["male","female"] | None, date_of_birth: date | None, issuing_country: str | None, guardians: list[AssistedGuardianInput])`，extra=forbid；`ClaimStartInput` 增加 document_type（默认 cn_id）、issuing_country、可选 completion。材料随 start 进入加密版本 2 proof，verify 后形成不可变申请；`PersonnelApprovalService.apply_claim_completion_in_transaction(actor: AuthContext, *, person_id: UUID, completion: ClaimCompletionFields, expected_approval_id: UUID, claim_id: UUID, operation_key: UUID) -> AssistedApproval`，不自行提交或重开锁序。

- [ ] 写 `test_incomplete_profile_can_submit_completion_without_enumeration`：匹配/不匹配/未知 issuer 的公开响应和发码规则一致，`assert public_fields <= {"status", "reason"}`；`test_completion_rejects_authority_fields`：person_id/kind/staff_type/roles/coach/team/assignment 输入一律 422。写 `test_reserved_pending_email_can_complete_but_not_change_email`：同预留邮箱可完成，另一邮箱不可批准；旧 strict reserved 路径不变。
- [ ] 运行 `"$PYTHON" -m pytest tests/api/test_profile_claims.py tests/api/test_profile_claim_review.py tests/services/test_profile_claims.py tests/services/test_profile_claim_review.py tests/services/test_profile_claim_binding.py tests/unit/test_profile_claim_security.py -q` 确认 RED；保留旧 v1 proof 通过用例。
- [ ] start 只加密申请人材料，不查询或返回数据库缺项；verify 成功后内部匹配改用 completion_allowed，不再要求完整激活资格。护照必须由管理员先补齐国家，公共匹配才使用精确国家+号码，不能扫描裸号自动认领。新 pending 可无邮箱或与预留邮箱精确相同；不同邮箱、已有任何状态账号、失效审批仍不可批准。复用现有共享 OTP 限额和证明绑定，不引入换用途绕过。
- [ ] 审核详情分开展示本人提交值与授权可见的旧值；仅合并缺项，已存在姓名/证件/生日/性别/联系方式的矛盾由已有授权更正入口处理，不让公开申请覆盖。缺项未补齐可进入审核但不可批准为可激活结果；事务内再次校验成人监护事实、本人身份、邮箱占位和权限。
- [ ] 在同一生命周期锁和事务内生成后继 revision/approval、写真实补充事实、绑定已验证邮箱、失效旧激活会话与竞争申请。guardian 只登记审核事实，不自动授予家庭查看权。服务端依据审批前可信快照设置 binding origin，客户端不能指定：新填邮箱为 introduced，原预留同邮箱为 preserved。撤销复核当前 binding、邮箱和可信后继链，仅清本次 introduced 邮箱，不能清后来合法更换的邮箱；preserved 保留联系事实但证明失效。重新申请、审核与激活均要求明确 pending 的同邮箱例外和新的有效 reviewed binding，不能凭邮箱非空开户。身份/监护更正与撤销仍保留原住宿。
- [ ] GREEN：重跑本任务测试；对审批每个写步骤注入失败，`assert business_state == before`；不同材料复用 key 为 409，旧 proof 不因后继快照静默复活。审查后提交 `feat: review missing profile facts before activation`。

## Task 4 严格激活 登录及跨入口防绕过

**Files:** 新建 `app/personnel_intake/activation_roles.py`；修改 `app/personnel_intake/{activation_service,activation_state}.py`、`app/auth/{account_readiness,service}.py`、`app/registration/finalization_service.py`、`app/personnel_intake/claims/binding.py`；测试 `tests/services/{test_assisted_activation,test_account_access_readiness,test_accounts,test_registration_finalization}.py`、`tests/api/test_assisted_activation.py`、`tests/db/{test_profile_claim_activation_concurrency,test_personnel_final_lock_order}.py`。

**Interfaces:** `activation_role_keys(kind: PersonKind) -> frozenset[str]`：student→student，player→team_member，both→student+team_member，staff→registered_user，未知类型拒绝。最终激活与登录消费任务 2 readiness、既有账号来源及任务 3 当前 binding，外部 start/verify/finish 签名保持兼容。

- [ ] 写 `test_unassigned_complete_profile_activates_without_team`：`assert account.person_id == old_person_id`；`assert team_memberships == []`；`assert stays == old_stays`。写 `test_staff_activation_has_only_registered_user`：`assert role_keys == {"registered_user"}`，不得出现 team_member/coach/administration/admin。
- [ ] 写 `test_pending_reserved_email_cannot_bypass_completion`：直接调用账号服务和普通注册完成服务均不能提前开户；缺本人事实、未知年龄、缺监护、旧 binding、撤销或锁定/停用/删除已有账号均拒绝。运行 `"$PYTHON" -m pytest tests/services/test_assisted_activation.py tests/services/test_account_access_readiness.py tests/services/test_accounts.py tests/services/test_registration_finalization.py tests/api/test_assisted_activation.py -q` 确认 RED。
- [ ] 激活 finish 锁内重检 activation_profile_ready、当前审批与邮箱证明、独立新验证码和账号唯一性；使用明确角色映射。新待补模式从首次建档即要求 reviewed_claim，不能因邮箱有值转换为 reserved_email；原 strict/legacy 保留原规则。登录继续验证原开户审批来源与可信后继链，但不把后来尚未分队当作拒绝登录理由。
- [ ] 管理员开户和普通注册的服务层同样保护所有 pending 档案，不只保护空邮箱；不得复制同证件人员、解除已有账号限制或自动合并。最终账号、邮箱登录标识、角色、证明完成和审计保持一个原子事务，手机联系资料仍不是短信验证。
- [ ] GREEN：本任务及任务 2、3 原命令通过；运行 `"$PYTHON" -m pytest tests/db/test_profile_claim_activation_concurrency.py tests/db/test_profile_claim_review_concurrency.py tests/db/test_personnel_final_lock_order.py -q`，独立连接测试 approve/correction/revoke/finish 竞争，`assert account_count <= 1`、`assert otp_consumptions <= 1`、`assert no_extra_roles`，锁顺序无逆序。审查后提交 `feat: enforce completed profile activation boundaries`。

## Task 5 待划分队伍和授权范围

**Files:** 新建 `app/team_management/allocation.py`、`app/team_management/allocation_router.py`、`tests/services/test_team_allocation.py`、`tests/api/test_team_allocation.py`；修改 `app/team_management/{membership_service,assignment_service,projection,schemas}.py`、`app/identity/{service,schemas,router}.py`、`app/api/router.py`、`app/audit/service.py`；测试 `tests/api/{test_people,test_people_assisted_projection}.py`、`tests/services/test_team_memberships.py`。

**Interfaces:** `TeamAllocationProjection(visibility: Literal["allowed","denied"], status: TeamAllocationStatus | None, version: int | None)`；`TeamAllocationInput(status: Literal["pending","not_required"], expected_version: int, idempotency_key: UUID, reason: str)`；`TeamAllocationService.update(actor: AuthContext, person_id: UUID, input: TeamAllocationInput) -> TeamAllocationProjection`；`sync_team_allocation_in_transaction(db: Session, *, person_id: UUID, actor_user_id: UUID, occurred_at: datetime) -> None`。`GET /people` 添加 `team_allocation_status=pending|assigned`；counts 增加授权可见的 `pending_team_allocation/assigned_team_allocation: int | None`。

- [ ] 写 `test_allocation_is_explicit_not_inferred_from_missing_team`：`assert old_person.status == "untracked"`、`assert imported_unassigned.status == "pending"`、`assert invisible_projection.status is None`。写 `test_allocation_counts_and_filter_share_authorization`：隐藏人员不进入可见数量，未获归属查看权者过滤请求拒绝，不能用 total 反推隐藏关系。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_team_allocation.py tests/api/test_team_allocation.py -q` 确认 RED。
- [ ] 实现归属可读 SQL 范围，与真实 team projection 显式拒绝策略一致；在 count/pagination 之前应用同一谓词。无权限数量返回 null，不能返回伪 0。显式 pending/not_required 修改要求实际管理员及现有人员修改能力并在事务内复核目标；空归属不让 scoped 管理者获得全局权限。添加私有、版本化 `PATCH /people/{person_id}/team-allocation`，审计前后状态但不记敏感资料。
- [ ] 接入真实 membership 与 manager assignment 写事务：成功加入后 assigned；最后真实关系结束时 tracked 人回 pending，其他有效关系存在则仍 assigned。旧 untracked 人单纯被读取不变；仅选教练不能假装已入队。禁止有真实关系时硬改 pending/not_required。复用现有加入/离开/转队和任职资格校验，staff.other 不因此获得新角色；原权限范围撤销对下一次读取立即生效。
- [ ] GREEN：运行本任务及 people/team 测试；验证两个并发版本只能一个成功、同 key 不重复审计、失权即拒绝、关系写失败状态也回滚，既有住房完全不变。审查后提交 `feat: track scoped pending team allocation`。

## Task 6 管理建档 个人补录和人员页面

**Files:** 新建 `apps/web/src/assistedPeople/PendingPersonForm.tsx`、`src/people/TeamAllocationPanel.tsx`、`src/profileClaims/ClaimCompletionFields.tsx` 及同名测试；修改 `src/assistedPeople/{AssistedDirectCreatePage,AssistedPersonForm,AssistedProfileCorrectionPanel}.tsx`、`src/assistedPeople/{types,api,editorApi}.ts`、`src/profileClaims/{ClaimProfilePage,ClaimReviewPage}.tsx`、`src/profileClaims/{types,api,adminApi}.ts`、`src/people/{PeoplePage,PersonDetailPage}.tsx`、`src/people/{api,presentation}.ts`、`src/auth/ActivateProfilePage.tsx` 和相应已有测试。

**Interfaces:** `createPendingPerson(input: PendingIntakeInput, idempotencyKey: string): Promise<ApprovalResult>`；`updateTeamAllocation(personId: string, input: TeamAllocationInput): Promise<TeamAllocationProjection>`；表单消费任务 1–5 DTO，不自行推测 readiness、年龄或是否已分队。普通 `AssistedPersonForm` 的严格提交路径保留，待补输入使用独立组件。

- [ ] 写 `PendingPersonForm.test.tsx`：姓名/有效证件必填，其余缺项可保存；选择 passport 后不跑 CN 自动填生日/性别；提交内容没有假邮箱/假监护/角色。写 `TeamAllocationPanel.test.tsx`：三类状态互不替代，未知/无权限不是待分队，保存后不自行调用入队或授角色接口。
- [ ] 运行 `npm test -- src/assistedPeople src/people src/profileClaims src/auth/ActivateProfilePage.test.tsx --maxWorkers=4` 确认 RED。
- [ ] 管理建档加“资料待补建档”，仅服务端允许者可见；显示真实缺项、未激活、待划分、护照未知性别不能入住的原因。员工/未知类别明确选其他工作人员。详情保留昵称/内部备注/房间，年龄空值显示“年龄待补”；行政住房权限和普通资料更正权限不扩大。
- [ ] 人员列表增加独立“队伍归属：全部／待划分队伍／已划分队伍”，接入 URL、分页和 query key；状态详情可由授权管理员标记无需分队，真实分队跳到已有成员/任职不同入口。焦点恢复、标签切换不清空筛选；403/失权清私有缓存和迟到响应，不能重现隐藏数据。
- [ ] 本人认领页面提供自填补资料区域，只呈现申请人输入，不预填数据库姓名/缺项/监护；邮箱与材料一并验证送审，修改材料需重新申请。审核页显示双方差异、资料不完整原因、原邮箱保留规则和人工核对选项；不自动勾选确认、不把审批通过显示为已激活。切换邮箱 App 回来保留内存草稿，敏感信息不持久化；未分队不作为激活错误。
- [ ] GREEN：重跑上述测试，再 `npm run build` 和 `npm run lint`；检查 360px 与桌面布局，原登录/激活/人员详情/住房入口回归。审查后提交 `feat: expose pending intake and allocation workflows`。

## Task 7 兼容回归 隔离联调及备份恢复

**Files:** 修改 `app/identity/{router,readiness}.py`、`app/portal/{context_service,learning_service,coach_service,schemas}.py`、`app/housing/{reporting_projection,reporting_snapshot,reporting_renderer,occupancy_export_service,schemas,reporting_schemas,reporting_types}.py`；测试 `tests/api/test_people.py`、`tests/unit/housing/{test_reporting_projection,test_reporting_snapshot,test_reporting_renderer}.py`。修改 `app/cli/seed_assisted_personnel_e2e.py`、`apps/web/tests/{assisted-personnel,profile-claims,person-housing}.spec.ts`、`scripts/run-assisted-personnel-e2e.sh`、`tests/cli/assisted_restore_probe.py`、`tests/cli/test_assisted_personnel_backup_docker.py`；新建 `docs/superpowers/verification/2026-10-21-pending-profile-passport-housing.md`。

**Interfaces:** 消费前面冻结的 nullable identity DTO/readiness；既有 launcher `--suite assisted`、`registration`、`team`、`housing-occupancy`、`housing-reporting` 不新增生产调试入口。报送缺项返回现有结构化缺项结果，不能生成假完整实名报告；普通授权住房导出可保留该人并显示明确“待补”。

- [ ] 写 `test_unknown_passport_facts_do_not_crash_people_or_exports`：`assert age_status == "unknown"`；`assert missing_fields`；`assert no_fabricated_identity_values`。端到端种子覆盖 pending 成年/未成年/student/player/both/staff/passport、已有预留邮箱和无邮箱、隐藏队伍、未知 issuer。流程断言原 person_id、stay_id、昵称和备注在补录/审核/激活后不变。
- [ ] 运行 `"$PYTHON" -m pytest tests/api/test_people.py tests/unit/housing/test_reporting_projection.py tests/unit/housing/test_reporting_snapshot.py tests/unit/housing/test_reporting_renderer.py -q` 及下列隔离 assisted 浏览器命令确认 RED；只为本轮失败修改 null 消费者，不顺带放宽其他身份规则。搜索 `date_of_birth.isoformat()`、`is_minor()`、固定性别映射和 PersonKind 穷举，逐个补明确缺项分支；旧 CN 报送内容保持不变。
- [ ] 在已验证安全的独占测试库，先将 `ASSISTED_E2E_DISPOSABLE_DATABASE` 设为该库的实际名称，再运行 `E2E_PYTHON_BIN="$PYTHON" bash scripts/run-assisted-personnel-e2e.sh --suite assisted`。分别将 suite 替换为 registration、team、housing-occupancy、housing-reporting，必须串行且全 exit 0；桌面与 360px 视口覆盖补录回后台、审核拒绝、重新申请、分队、撤销后旧访问的读写核对。只用测试邮件发送器；没有新的 iPhone 实证时注明未做真机验收。
- [ ] API 全量 `"$PYTHON" -m pytest --tb=short`，再 `"$RUFF" check app tests migrations`、`"$MYPY" app`；Web 全量 `npm test -- --maxWorkers=4`、`npm run build`、`npm run lint`；保留实际通过数、失败、跳过原因及源指纹。缺数据库的 SKIP 不替代迁移/并发验证。
- [ ] 用本轮不可变 API/DB 镜像 ID 运行 `ERP_BACKUP_DOCKER_TEST=1 "$PYTHON" -m pytest tests/cli/test_assisted_personnel_backup_docker.py -q`，按现有测试要求配置 `ERP_BACKUP_TEST_*_IMAGE`。恢复探针覆盖新证件键、pending 审批、introduced/preserved binding、未分队和已激活人员，以及旧住宿历史。测试必须实际 PASS；不读取真人密文做测试 fixture。
- [ ] 规格符合性和安全审查关闭重要问题，主代理检查共享文件整合及 `git diff --check`。验证报告记录真实结果，不以构建成功代替导入或迁移成功；仅本轮差异可隔离时提交 `test: verify pending profile lifecycle and recovery`。

## Task 8 NAS 发布和剩余名单实际写入

**Files:** 更新本轮验证报告；导入结果追加至 `/Users/skymoonzyj/Documents/new project3/outputs/housing-import-20261021-attempt.json`，保留历史预检和已完成记录。源文件 `/Users/skymoonzyj/Downloads/派出所人员报备.xlsx` 不修改。

**Interfaces:** 产品发布用 P 自带脚本；真实建档/更正/入住仅走正常已认证后台业务入口，不能用生产 SQL 插人、绕过审批或静默激活。导入条目分别记录 profile_created/profile_reused、实际 stay 及阻碍，不能把目标房间算成功。

- [ ] 确认本轮全部门禁通过和部署源与审查源一致，使用 P 的脚本执行 dry-run 后正式发布：`PATH="/Users/skymoonzyj/Documents/new project3/.venv/bin:$PATH" bash scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3' --dry-run`；移除 `--dry-run` 再执行。任何失败暂停发布与导入，不能改用主工作区旧 launcher 或直拷绕过。
- [ ] 发布必须 exit 0，复核 active-release、complete 状态、实际新迁移头、HTTPS18080、备份以及静态/后端源码一致。验证后台显示待补建档/队伍筛选，空表单检查不向真实邮箱发码；上线成功与人员写入成功分别记录。
- [ ] 重新读取工作表 `2026.10.12更新` 和已存去重结果，核对源 SHA256。只处理排除 1 幢/重复行后的剩余人员；王泓博尾号0017、邓明渊尾号4812采用用户更正值，Reginaldo 按原护照。真实号码只在受限处理和业务表单中使用，不加入普通报告或日志。按实际中国日期补空入住日、空退宿为常住、括号名为昵称、备注追加。
- [ ] 每人先核对现有证件/姓名、审批及住房，复用已存在结果；新建无归属标记 pending，不创建账号或岗位角色；原始表明确身份优先，其他工作人员仅用于新工作人员/未知类别。每次提交后重新读取 person_id/审批和房间。核房、性别、容量或冲突不满足的单独暂停，不用假资料填补；房型按原表已确认人数，不随单批成功数缩小。
- [ ] 护照人员缺可靠性别时仅完成建档，向用户收集真实性别后再入住；缺生日、签发国家和监护资料留待真实补录，不推测。记录 47 条逐项结果、既有 20 人保持情况、真正入住数和未完成原因，保留原调房历史；未实际写入项不能计为已导入。
- [ ] 最终复核无重复人员/入住/账号、个人资料与住房一致；关闭仅本轮拥有的隔离服务和临时测试凭据。交付 NAS 入口、实际检查结果和剩余资料项，不以“开发完毕”掩盖未写入的住宿。

## 主代理自审和执行交接

- 目标/建档分类：任务 1、2、6；待划分：任务 5、6；护照及空值：任务 1、7。
- 住宿与激活分离：任务 2、4；本人补录及人工核对：任务 3、4、6；真实名单规则：任务 8。
- 兼容、并发、授权、备份恢复：任务 1–5、7；统一发布及数据读回：任务 8。Review Focus 五项均有归属测试。
- 计划审阅后，保留用户既定的按实际边界并行方式；共享契约和数据库门禁由主代理串行协调，各实现交由独立复核。不将规格批准、计划保存或测试种子视为已完成生产导入。
