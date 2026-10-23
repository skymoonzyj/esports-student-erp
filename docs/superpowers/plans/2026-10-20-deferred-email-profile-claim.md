# 建档暂缺邮箱与审核认领 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 已审核学员／队员无邮箱也可入住；本人经姓名证件匹配、邮箱验证、管理员核对和最终激活，使用原人员档案而不重复建人。

**Architecture:** 保留现有人员、不可变审批快照、住房资格和唯一开户事务。在 `personnel_intake/claims` 中增加短期验证会话、认领申请、审核操作记录及绑定证据；扩展共享凭据预留，使新路径与普通注册、邮箱更改使用相同锁和冲突规则。公开认领不返回数据库实名，审核绑定与最终开户分开。

**Tech Stack:** FastAPI、SQLAlchemy 2、PostgreSQL、Alembic；React、TypeScript、React Query、Zod、Vitest、Playwright；现有加密、OTP、邮件发送器、Capacitor 兼容和 NAS 发布脚本，不增加外部认证依赖。

**Spec:** `docs/superpowers/specs/2026-10-20-deferred-email-profile-claim-design.md`（2026-10-20 用户已确认）。

执行状态：用户已确认计划，任务 1–8 于 2026-10-21 完成开发、独立审查、全量回归和 NAS 发布。实际证据见 `docs/superpowers/verification/2026-10-20-deferred-email-profile-claim.md`。下列清单保留原计划；实际任务完成记录以本计划的 SDD ledger 为准。为保留原有未提交内容，执行时采用临时索引审查快照，未实际 stage、commit、merge 或 push。真实人员名单导入仍是独立事项。

## Global Constraints

- 本期可认领对象限定为经既有管理建档／代建审核批准、当前没有预留邮箱且没有账号的学员、队员及兼任人员。
- 已有预留邮箱者沿用原激活流程；工作人员、监护人、访客和未批准草稿不进入本分支。已有账号包括锁定、停用、删除状态，不可重新开户。
- 邮箱可空不放宽姓名、有效身份证、手机号、性别／生日、必要成年监护资料、真实教练／队伍归属和建档审核。
- 邮箱验证只证明邮箱控制权；管理员核对后批准也不等于开户。最终使用新的验证码和本人设置的密码激活，返回登录页，不自动登录。
- 无账号人员仍为“未激活”；账号实际创建成功才为“已激活”。保留原 `person_id`、昵称、备注、住宿、队籍、负责人及财务历史。
- 审核需要实际管理员／超管身份、全局 `accounts.manage`、目标 `personnel_intake.review` 范围，显式拒绝优先；事务内重检，不允许自批。
- 验证码 6 位、10 分钟有效、60 秒重发间隔、5 次错误封闭挑战；沿用跨用途每邮箱 5 次／24 小时、来源 20 次／小时的发送总额，以及入口标识 5 次／小时、来源 10 次／分钟和 20 次／小时。
- 验证后待审核有效期为 7 天。审核核验当时已成功消费的不可变邮件验证证据，不要求旧验证码仍有效；最终开户另取短期验证码。
- 密码沿用 8–16 位、ASCII 大写／小写／数字同时存在、允许符号的现有规则，不改哈希参数。
- 中国业务日期只显示年月日，安全审计保留时分，验证码倒计时保留秒级交互。
- 公共接口不泄露是否存在档案、原姓名、房间、监护或账号状态；敏感输入不进入 URL、查询键、浏览器持久存储、普通日志或遥测。
- 管理端查询不持久缓存，失权后新请求立即拒绝；未知目标只给具有全局审核范围的管理员查看申请人提交内容，不推测其他档案。
- 只使用隔离数据库和虚拟邮件发送器测试，不给名单中的真人发验证码、不写 NAS 测试人员或住宿。Web 检查不宣称 iOS 真机验收。
- 追加迁移，不编辑已上线迁移；本功能不包括真实名单导入、待核对房间历史补录、短信、联网身份认证或自动授予监护权限。
- 本期名册继续排除 1 幢；具体导入去重、房型、调房日期、监护缺失和核房限制仍按规格第 9 节另行执行。

## Review Focus

1. 攻击者用正确证件和自己的邮箱先申请：不得独占身份阻止本人，不能仅凭邮箱验证码开户；独立申请并存、管理员只批准一份（任务 3、4、8）。
2. 审核发生在验证码过期后、申请第 7 天边界或午夜：有效历史验证仍可审核，超过待审有效期不能批准，已批准者需新验证码开户（任务 3、4、5）。
3. 已批准但未激活时更正资料、撤销邮箱或另一入口注册：旧证明不能复活，不能用“当前有邮箱”绕过人工认领，原住房仍保留（任务 4、5、8）。
4. 切到邮箱 App 再回来、切换后台账号、迟到响应：本人表单不丢失，管理端失权资料立即隐藏，旧会话不能覆盖新会话（任务 6、7）。
5. 旧快照非空邮箱、新快照空邮箱、同电话不同人和跨身份同名：历史审核仍可验证，不复制人员、不把 legacy 账号或监护规则放宽（任务 1、2、5、8）。

---

## 工作目录与检查约定

实施源 `P=/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`，当前文档 HEAD `e2069bf`，源迁移头 `0067_person_profile_notes`。该 linked worktree 有已发布但未提交的业务改动，包含 `0067`；只基于 HEAD 新建工作树会丢失这些内容。实施前按 using-git-worktrees 技能核对附件与工作树，优先复用 P，记录已有 dirty 清单并保留全部内容。若改用新工作树，先完成受控源码一致性验证，不能只 checkout HEAD。

完整文件路径相对 P；表格／任务中的 API 模块短路径（如 `personnel_intake/`、`claims/`）分别相对 `apps/api/app/`、`apps/api/app/personnel_intake/`，`tests/`、`migrations/` 相对 `apps/api/`；Web 的 `src/` 相对 `apps/web/`。`{types,api,keys}.ts` 为类型／接口文件，React 组件均为 `.tsx`，下文 `.ts(x)` 仅为这一约定的简写。API 命令 cwd=`apps/api`，Web 命令 cwd=`apps/web`，脚本 cwd=P：

```sh
PYTHON='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'
RUFF='/Users/skymoonzyj/Documents/new project3/.venv/bin/ruff'
MYPY='/Users/skymoonzyj/Documents/new project3/.venv/bin/mypy'
```

开始时验证这些工具存在；为本次运行分配唯一、本机回环、已确认归属的临时 PostgreSQL 项目和空闲端口，数据库名以 `_test` 结尾。设置 `TEST_DATABASE_URL` 后运行 `"$PYTHON" -m app.cli.e2e_database_safety`，预期 exit 0。pytest 会清空该库，数据库套件必须串行；不复用不明归属数据库，不直接运行会重置固定项目的 `scripts/check.sh`。缺库导致 SKIP 不算通过。

每任务执行 RED→最小实现→GREEN→审查。只 stage 本任务新文件／差异块，检查 staged diff；开发期间用 `ERP_NAS_AUTO_DEPLOY=0 git commit -m '<本任务消息>'` 明确暂停自动发布，最终任务统一发布。禁止 `git add .` 或提交先前未提交代码。不更改全局自动发布设置。

## 文件分工和公共契约

| 单元 | 文件 | 职责 |
| --- | --- | --- |
| 存储及共享输入 | `apps/api/app/personnel_intake/{models,schemas,codec}.py`，新 `claims/{__init__,models,schemas,errors}.py` | 可空邮箱、持久激活要求、验证／认领／绑定记录、独立响应 DTO |
| 建档与共享占位 | `personnel_intake/{service,approval_service,locks,eligibility,guardian_records}.py` | 空邮箱兼容、可信后继审批、跨入口邮箱唯一性与到期释放 |
| 公开认领 | 新 `claims/{public_service,public_router,dependencies}.py` | 姓名证件匹配、独立 cookie、OTP、私有申请状态 |
| 审核与绑定 | 新 `claims/{access,queries,review_service,binding}.py`、`claims/admin_router.py` | 范围过滤、人工确认、原子批准／撤销、最终激活证据验证 |
| 原激活集成 | `personnel_intake/{activation_service,activation_state}.py`，`auth/service.py`、`identity/service.py`、`registration/finalization_service.py` | 旧路径兼容、证据失效和唯一开户竞争 |
| Web 本人入口 | 新 `apps/web/src/profileClaims/{types,api,keys,ClaimProfilePage}.ts(x)` | 无密码认领、验证码、状态、重新验证查询；不展示数据库姓名 |
| Web 审核入口 | 新 `profileClaims/{adminApi,ClaimReviewPage}.ts(x)`、`registration/RegistrationProcessingPage.tsx` | 独立队列、资料核对、批准／拒绝／撤销、分入口权限 |
| 迁移和验收 | 新 `0068_deferred_email_claims.py` 及具名测试 | 可恢复升级、并发、安全、浏览器、备份和 NAS 发布 |

`0068` 在实施前重新确认无新迁移占用；若占用，顺延编号并一次性更新下文引用、manifest 和测试，不改旧迁移。

后端共用类型统一在 `claims/schemas.py`，不借用原激活的 `masked_name` 预览：

- `ClaimStartInput(full_name: SecretStr, identity_number: SecretStr, email: SecretStr)`；`ClaimVerifyInput(code: SecretStr)`。姓名仅去首尾空白、不得猜测改名；身份证、邮箱复用既有规范化与格式检查。
- `ClaimStart(accepted: Literal[True], resend_available_at: datetime, expires_at: datetime)`；内部 `ClaimIssued(public: ClaimStart, session_token: SecretStr)` 不可作响应。
- `ClaimPublicStatus(status: Literal["waiting_review","approved","rejected","expired","closed"], reason: str | None)`。不返回 person、匹配结果、姓名、邮箱、内部原因或申请 ID；已撤销／失效／完成公开归为 `closed`，引导联系管理员或正常登录。
- `ClaimAdminContext(can_enter: bool)`；`ClaimListItem(id: UUID, version: int, status: ClaimStatus, masked_name: str, masked_email: str, verified_at: datetime, expires_at: datetime, allowed_actions: list[str])`；`ClaimPage(items: list[ClaimListItem], total: int, pending_count: int, page: int, page_size: int)`，两个计数均先应用权限范围，pending_count 排除到期申请。
- `ClaimDetail` 扩展列表项，包含经范围授权的 `person_id: UUID | None`、双方脱敏资料、内部不可批准原因、同目标待审数量和 `can_approve: bool`；不默认返回完整身份证。申请人完整邮箱仅在有权审核详情显示。
- `ClaimDecisionInput(expected_version: int, idempotency_key: UUID, identity_confirmed: bool, note: str | None)`；`ClaimReasonInput(expected_version: int, idempotency_key: UUID, reason: str)`；备注上限 500 字，拒绝／撤销原因 1–500 字。拒绝理由提示只写可向申请人公开的内容。
- `ClaimStatus = Literal["pending","rejected","expired","invalidated","approved","revoked","completed"]`。所有 DTO 禁止额外字段；敏感输入采用 SecretStr，存储用现有信封加密和分域摘要。

依赖为任务 1→2→3→4→5；任务 6 在 2、3 接口稳定后可并行，任务 7 在 4 接口稳定后可并行；任务 8 汇总。API 共用文件和数据库验证由主代理串行协调，前端两路分别负责本人页与审核页，router／AppShell 由同一集成人修改。

## Task 1 追加可恢复存储和激活要求

**Files:** 新建 `apps/api/app/personnel_intake/claims/{__init__,models,schemas,errors}.py`、`apps/api/migrations/versions/0068_deferred_email_claims.py`、`apps/api/tests/db/test_profile_claim_migration.py`；修改 `personnel_intake/models.py`、`registration/models.py`、`migrations/env.py`、`scripts/db-schema-manifest.sh`、`tests/db/test_migrations.py`、`tests/cli/test_operations_docs.py`、`tests/cli/test_local_container_acceptance_launcher.py`（后三类 API 短路径相对 `apps/api`）。

**Interfaces:** 产出 `ProfileClaimSession`、`ProfileClaim`、`ProfileClaimDecision`、`ProfileClaimBinding` ORM 和上文 DTO；`AssistedPersonProfile.activation_requirement: Literal["reserved_email","reviewed_claim"]`；`PersonnelCredentialReservation.profile_claim_id: UUID | None`。

- [ ] 写迁移测试 `test_upgrade_preserves_people_accounts_stays_and_old_snapshots`：`assert after_business == before_business`；旧有效预留邮箱 profile 为 `reserved_email`，账户来源未变；新 NULL revision email 可存，旧快照字节不改。`test_claim_constraints_and_owner_xor`：重复 challenge、同人第二个活动 binding、双 owner、无 owner、claim 的 identity 预留均触发 IntegrityError。`test_downgrade_refuses_claim_or_null_email_data`：空新增数据可降级／再升级，已有新业务事实拒绝破坏性降级。
- [ ] 运行 `"$PYTHON" -m pytest tests/db/test_profile_claim_migration.py -q`，预期因缺新模型／约束失败，而非环境连接失败。
- [ ] 实现 session：随机 token 摘要、独立 challenge FK 唯一、加密申请输入及摘要、邮箱／身份摘要、requested／verified 状态、创建／到期／验证时间、可空 claim FK。实现 claim：不可变提交内容及版本、验证时 challenge/session/email 绑定和消费时间、可空内部 target/目标 approval、私有匹配原因、状态版本、7 天截止、审核人与公开原因；状态更新不允许改提交内容或验证证据。
- [ ] 实现 binding：同人唯一活动绑定，关联 claim、person、规范化邮箱摘要、绑定时后继审批及撤销时间；claim 保存可空 `completed_account_id` 同人 FK。实现不可变 decision：claim、审核人、动作、key、请求指纹、操作前后版本、结果审批／binding ID，唯一 `(actor_user_id, idempotency_key)`；批准及后来撤销各有独立记录，可重放但不重写旧决定。为 pending expiry、email、identity、target、状态分页增加索引。profile 的 `reviewed_claim` 不得降回 `reserved_email`；旧 profile 回填 reserved 路径仍须使用原快照真实性检查，异常旧事实拒绝激活，不伪造证明。
- [ ] 将 revision `email_digest` 改 nullable；reservation `request_id` 改 nullable但保留历史 FK/值，新增 claim owner FK 和 XOR：旧 request owner 或新 claim owner（二者仅一项，claim 只能 email且 `person_id IS NULL`）。保留活动 `(kind,digest)` 唯一索引。新增 challenge purpose `profile_claim`，审批 operation_kind 允许 `claim_bind`、`claim_revoke`；不可变审批触发器不移除。
- [ ] 重跑上述测试与 `tests/db/test_migrations.py`、三项 manifest/head CLI 测试，全通过且无数据库跳过；检查 schema manifest 包含新表、索引、约束、触发器与加密列。提交 `feat: add reviewed profile claim storage`。

## Task 2 邮箱可空与共享占位兼容

**Files:** 修改 `apps/api/app/personnel_intake/{schemas,codec,service,approval_service,locks,eligibility,guardian_records}.py`、`apps/api/app/housing/resident_readiness.py`（仅确有必要时）；新建 `apps/api/app/personnel_intake/claims/reservations.py`；测试 `tests/services/{test_personnel_intake,test_personnel_approval,test_assisted_eligibility,test_assisted_housing_readiness,test_account_access_readiness}.py` 和 `tests/db/test_personnel_intake_conflicts.py`。

**Interfaces:** `AssistedPersonFields.email: SecretStr | None = None`、`IntakePayloadView.email: str | None`；`CredentialReservationPolicy.require_available(*, identity: str, email: str | None, person_id: UUID | None, request_id: UUID | None, profile_claim_id: UUID | None = None, registration_application_id: UUID | None = None) -> None`；`require_email_available(*, email: str, person_id: UUID | None = None, request_id: UUID | None = None, profile_claim_id: UUID | None = None, registration_application_id: UUID | None = None) -> None`。新 `expire_claim_reservations_in_transaction(db: Session, *, email_digest: str, now: datetime) -> None`，调用前必须持有该 email 共享锁。

- [ ] 写 `test_approved_intake_without_email_is_housing_ready_not_activation_ready`，参数化 student/player/both：`assert person.email_ciphertext is None`、`assert email_reservations == []`、`assert housing_ready`、`assert no_account`；缺手机号、非法证件、缺必要监护、错误归属仍拒绝。加 `test_null_and_legacy_snapshot_authentication`，空／非空快照均真实验证，篡改仍失败。
- [ ] 运行上述五个 services 文件与 conflict 测试，记录新增断言 RED。
- [ ] 实现空白→None、非空格式校验、可空摘要／请求修订和详情；仅缺邮箱不建邮箱占位。初次批准时由服务器按有无邮箱写 activation_requirement，不接受客户端来源字段。住房仍调用原 approved-assisted 资格，legacy 分支不改；完整快照校验照常运行。
- [ ] 规范可空邮箱更正：reserved 路径若授权清空邮箱且仍无账号，持久要求单向改为 reviewed_claim；任何 reviewed_claim 的邮箱绑定／清除只允许任务 4 的显式审核转换，普通更正不能填邮箱绕过认领。保留原资料更正的其他字段、原因、版本及监护边界。
- [ ] 实现共享占位扩展：claim 自身豁免仅比较 `profile_claim_id`，不得用同 person 绕过；所有 nullable owner 的排除使用 `IS DISTINCT FROM` 或显式NULL分支，不能用 `request_id != ...` 漏掉claim行，也不能反过来漏掉旧request行。邮箱到期释放在共享锁内实际更新 ended_at 后再检查，不能只 SELECT 忽略过期记录。普通注册申请占用定义为 email kind、`expires_at > now` 且 state 属于 draft/contact_verified/identity_completed/pending_guardian/ready_to_finalize/conflict，排除 `registration_application_id` 本身；finalized依赖Person/login占用，rejected/expired不占用。普通注册、登录标识变更与建档都使用这一规则，不把仅challenge行当永久占位。
- [ ] 加 `test_expired_claim_email_reservation_is_released_under_lock`、`test_person_exemption_cannot_steal_claim_email`、`test_nullable_owner_exemptions_preserve_other_owner_conflicts`、`test_active_registration_email_conflicts_with_claim`；重跑本任务及 `tests/db/test_registration_claim_concurrency.py tests/db/test_registration_contact_change_concurrency.py tests/db/test_account_identifier_concurrency.py`，预期只有一个合法邮箱拥有者，无死锁。提交 `feat: allow deferred email in approved personnel intake`。

## Task 3 本人认领与邮箱验证

**Files:** 新建 `apps/api/app/personnel_intake/claims/{public_service,public_router,dependencies}.py`；修改 `apps/api/app/{api/router,registration/delivery,registration/start_limiter,registration/verification_service,core/config,audit/service}.py`；新测试 `tests/services/test_profile_claims.py`、`tests/api/test_profile_claims.py`、`tests/unit/test_profile_claim_security.py`。

**Interfaces:** `ProfileClaimService.start(input: ClaimStartInput, request_source: str) -> ClaimIssued`；`verify(session_token: str, code: SecretStr) -> ClaimPublicStatus`；`status(session_token: str) -> ClaimPublicStatus`。独立路径 `/api/v1/auth/profile-claims/{start,verify,status}` 均 POST JSON；cookie `erp_profile_claim`、path `/api/v1/auth/profile-claims`、Secure/HttpOnly/SameSite=Lax、最长 600 秒，拒绝重复 cookie。status 无业务输入；短期 cookie 过期后重新输入同一资料并验证邮箱才能查同一申请，不建立长期匿名档案访问。

- [ ] 写参数化 `test_public_claim_matching_is_not_an_identity_oracle`：有效目标、不匹配、姓名错误、已有账号、范围外身份均 `assert start.status_code == 200`、`assert start_keys == {"accepted","resend_available_at","expires_at"}`、`assert len(captured_messages) == 1`；有效码后统一 `waiting_review`，正文无 person/name/internal_reason。模拟同一邮箱条件，避免测试被共享频控误挡。
- [ ] 写 `test_verified_claim_is_immutable_and_duplicate_returns_existing`：`assert count_claims == 1`、`assert claim.original_verified_at == old_time`、重复不延长 7 天、不改首次证据；不同邮箱同身份保留多个申请且无 identity reservation。写 `test_fresh_email_proof_can_reopen_own_approved_status`：第2天重验相同资料返回approved、不创建新申请或重绑邮箱。运行本任务三个文件，确认 RED。
- [ ] 实现匹配仅在服务端：规范化 ID 摘要+姓名、有效 approved-assisted、当前无邮箱且无任意状态 account；邮箱冲突和匹配失败记录为管理员私有不可批准原因。所有格式合法目标走相同发码与公开状态路径；不能复制旧激活“未知目标不实际发码”的 decoy 行为。没有正确 OTP 不进入可批准队列，不改 Person。
- [ ] 复用发送总量／入口 limiter和来源解析，扩展独立 `profile_claim` purpose 及邮件文案；issue/verify/consume 绑定 session、版本、email、purpose，验证失败计数独立提交。`consume_with` 成功回调先按规范化姓名/证件/email的整组请求指纹查同一已验证申请：有效pending复用且不续期，approved返回原待激活状态，已完成返回closed；过期/拒绝/撤销需重新申请。仅新申请执行目标匹配和插入，不因已批准后的Person有邮箱而丢失本人查询入口。不可安全预留者创建同结构但不可批准的申请。数据库目标、邮箱占用不体现在初次发码／状态差异中；SMTP故障使用统一真实发送失败提示。
- [ ] 增加工程默认上限（Settings）：同邮箱最多 3 个、同身份输入摘要最多 5 个、全局最多 1000 个有效 pending，跨进程以固定顺序配额锁原子检查；匹配失败也计数，身份不存在也按输入摘要计数。达到上限统一429+Retry-After，不静默新建；相同有效申请复用不占新配额。过期状态／预留在访问路径惰性释放，存量终态保留审计。初始短期会话受入口预算约束；新增 `prune_expired_session_inputs(db: Session, *, now: datetime, limit: int = 200) -> int`，每次start前按expiry/id取到期行、SKIP LOCKED清空session输入副本，保留申请中的不可变验证事实、摘要与事件；绝不清活跃session。
- [ ] 复用 `require_activation_request` 的严格 Origin／JSON／Fetch-Metadata 和无 Origin 原生约束，明确覆盖status；所有响应 no-store/private，不将 token 输出给 JS。状态仅由本人当前已验证会话访问，跨会话／跨用途／猜测码／旧挑战／重复 header 都拒绝。审计只存事件、内部对象 ID、动作结果，不存身份证、邮件码、密码和完整负载。
- [ ] 运行本任务测试及 `test_registration_verification.py`、`test_activation_proxy_budget.py`、原 activation security/delivery 测试；覆盖59/60秒、10分钟、5次错误、7天、跨用途总量、SMTP失败、重放、过期cookie和并发配额。提交 `feat: add private email verified profile claims`。

## Task 4 管理员核对、原子绑定与撤销

**Files:** 新建 `apps/api/app/personnel_intake/claims/{access,queries,review_service,binding,admin_router}.py`；修改 `personnel_intake/{approval_service,activation_state}.py`、`api/router.py`、`audit/service.py`；新测试 `tests/api/test_profile_claim_review.py`、`tests/services/test_profile_claim_review.py`、`tests/db/test_profile_claim_review_concurrency.py`。

**Interfaces:** `ClaimReviewAccess.require_review(actor: AuthContext, person_id: UUID | None) -> None`；`ProfileClaimReviewService.approve(actor, claim_id: UUID, input: ClaimDecisionInput) -> ClaimDetail`、`reject(actor,claim_id,input: ClaimReasonInput) -> ClaimDetail`、`revoke(actor,claim_id,input: ClaimReasonInput) -> ClaimDetail`；查询 `context(actor) -> ClaimAdminContext`、`list_claims(actor, *, status: ClaimStatus | None, page: int, page_size: int) -> ClaimPage`、`detail(actor,claim_id) -> ClaimDetail`。

增加内部 `PersonnelApprovalService.apply_claim_email_in_transaction(actor, *, person_id: UUID, email: str | None, expected_approval_id: UUID, claim_id: UUID, operation_key: UUID, operation_kind: Literal["claim_bind","claim_revoke"]) -> AssistedApproval`，只由已加完整锁的审核服务调用，不开放独立HTTP写入口。

- [ ] 写 `test_review_requires_admin_account_capability_and_target_scope`：行政／教练／经理、仅有其一能力、显式deny、失权后新读／写均拒绝，列表与total先过滤；无匹配目标仅全局 intake 审核范围可查看。`test_approve_requires_explicit_identity_confirmation`：未勾选422、本人自批403、通过可空备注、拒绝／撤销空原因422。
- [ ] 写 `test_approve_after_otp_expiry_before_review_deadline`：验证后第2天通过，第7天到期不通过；成功后 `assert same_person_id`、`assert no_account`、`assert successor.supersedes_id == old.id`、`assert old_snapshot_unchanged`、`assert other_claims_invalidated`、`assert active_bindings == 1`。运行本任务三套，确认 RED。
- [ ] 实现 `/api/v1/personnel-intake/profile-claims`：GET `/context`、GET 列表（默认20／最大100）、GET `/{id}`、POST `/{id}/{approve,reject,revoke}`；静态路径先于动态 UUID。查询在 SQL 前置范围过滤，分页/总数使用同一有效状态表达式（pending且已到期显示expired），GET不为更新状态而另开写事务；详情私有404、非公开 no-store/private。复用现有实际角色/deny/范围解析，不用 registration.review 代替。姓名／身份证脱敏，原档案明文证件沿用 `person.identity.reveal` 和用途审计；不另造“审核可全看”特权。
- [ ] 实现统一锁序：先发现候选锁集，再共享 credential（旧新email/identity，含相关配额锁）→ membership/intake request→完整监护图→排序 Person/accounts→team directory→claim／session→challenge（仅验证消费才锁）。发现阶段不作写，锁后重读指纹及权限；审批、撤销和最终激活遵守现有共同前缀，不先持claim行再反取credential。批准失效其他邮箱申请之前先发现并锁定所有竞争邮箱，锁后若竞争集合变化则重试，不在已持业务行锁后补拿邮箱锁。冲突或锁集漂移409提示重试。
- [ ] 批准事务检查未到期、历史有效消费证据、当前同目标同姓名证件与审批版本、无账号无邮箱、身份/监护有效且邮箱可用；创建后继 intake revision 和不可变 approval，写 Person邮箱，插入绑定并使本申请批准。将本人该邮箱预留转交现有 request/person 体系，结束竞争申请预留并作废其他 pending、旧激活session；原申请证据和历史保持不变。不可批准的私有匹配原因不能被管理员强行略过。
- [ ] 拒绝／到期释放预留并保留事件。撤销仅在无账号、当前邮箱和活动binding均仍属于本次批准时可执行：创建空邮箱后继审批，结束本绑定和其邮箱预留，作废旧会话；不影响住房或另一轮邮箱。重复相同操作key／指纹返回同结果，不重复审批／审计；复用key不同输入409。拒绝理由限制长度并提示禁止第三人资料，公开页只展示本人的处理意见。
- [ ] 在独立连接测试两邮箱并发批准、批准对更正／撤销／开户竞争、失权发生在路由预检查后、任何写步骤抛错回滚；`assert persons == 1`、`assert active_bindings <= 1`、`assert no_partial_email_write`，并验证失败不改住房。GREEN后提交 `feat: review and revoke profile claim bindings atomically`。

## Task 5 最终激活与跨入口证据约束

**Files:** 修改 `apps/api/app/personnel_intake/{activation_service,activation_state,approval_service}.py`、`claims/binding.py`、`auth/service.py`、`identity/service.py`、`registration/finalization_service.py`；测试 `tests/services/test_assisted_activation.py`、`tests/api/test_assisted_activation.py`、`tests/services/test_account_access_readiness.py`；新 `tests/db/test_profile_claim_activation_concurrency.py`、`tests/services/test_profile_claim_binding.py`。

**Interfaces:** `require_activation_binding_in_transaction(db: Session, *, person_id: UUID, approval_id: UUID, normalized_email: str, now: datetime) -> ProfileClaimBinding | None`；reserved合法路径返回None，reviewed路径必须返回有效binding或抛现有通用激活错误。`complete_claim_in_transaction(db: Session, *, binding_id: UUID, account_id: UUID, now: datetime) -> None`；现有 `start/verify/finish` 外部签名不变。

- [ ] 写 `test_pending_or_approved_claim_without_fresh_activation_code_cannot_open_account`：初次认领码、注册码、过期激活码均不能开户；批准后仍未激活，新的正确激活码仅建一个account，`assert original_person_id == account.person_id`，email已验证而phone未验证，角色仅student/team_member，不自动登录。
- [ ] 写 `test_claim_requirement_cannot_be_bypassed_by_setting_current_email`、`test_ordinary_registration_cannot_claim_deferred_profile` 和 `test_binding_uses_same_email_trusted_approval_lineage`：无binding即使Person有邮箱也拒绝；普通注册即使验证另一邮箱并填写正确身份证也不能直接claim_existing或另建同证件人员；合法同邮箱资料更正后可信后继链可激活，改邮箱、撤销、非可信审批、账号已有任何状态全部拒绝；旧reserved路径仍通过。运行本任务五套测试确认 RED。
- [ ] 在 `_eligible` 和 finish锁内重检中调用binding policy；证明固定绑定 person、email域摘要、claim批准和绑定时approval，当前approval必须沿现有真实性校验的可信后继链且邮箱相同。原账号开户来源不可变，已有账号不因新审批重新获得例外。
- [ ] 在现有 `consume_with(... on_success=create_account)` 同一事务内标记claim完成，不在提交后另写。密码由现有策略处理且不写claim。把普通资料更正、身份合并、禁用、注册完成和标识变更纳入共同占位/证据失效路径；普通注册命中受保护的assisted档案不得以legacy来源开户或重新建人，使用不泄露匹配细节的指引转已有档案流程。保留同邮箱可信更正的binding，但旧activation会话照旧失效，用户重取码。不允许普通更正悄悄换claim绑定邮箱。
- [ ] 独立连接执行双finish、finalize与claim批准、撤销与finish、普通注册／添加标识争抢邮箱；断言只有一个合法账号、一次验证码消费、角色不重复、失败事务无残留。重跑任务2、4和现有 assisted activation/account/registration concurrency suites，全部通过。提交 `feat: enforce reviewed claim evidence at account activation`。

## Task 6 建档和本人认领页面

**Files:** 修改 `apps/web/src/assistedPeople/{types,api,AssistedPersonForm,MyAssistedRequestsPage,editorApi,AssistedProfileCorrectionPanel}.ts(x)`；新建 `apps/web/src/profileClaims/{types,api,keys,ClaimProfilePage}.ts(x)`；修改 `src/registration/RegistrationStartPage.tsx`、`src/auth/{ActivateProfilePage,AuthProvider}.tsx`、`src/app/router.tsx`。测试同名 `.test.ts(x)` 和既有 activation/router 测试。

**Interfaces:** 本人API `startProfileClaim(input: ClaimStartInput, signal?: AbortSignal): Promise<ClaimStart>`、`verifyProfileClaim(code: string, signal?: AbortSignal): Promise<ClaimPublicStatus>`、`getProfileClaimStatus(signal?: AbortSignal): Promise<ClaimPublicStatus>`；严格解析任务1DTO，cookie对JS不可见。`clearProfileClaimSession(): void` 仅清理内存generation/草稿/cache并取消旧请求，不假称已删除HttpOnly cookie。

- [ ] 写 `test_null_email_draft_submits_without_weakening_identity_or_guardian_checks`：空值提交null、有邮箱仍校验、旧详情能加载、监护邮箱比较不因null崩溃；受保护的已绑定激活邮箱不能经普通更正UI编辑。`test_claim_form_survives_mail_app_switch_without_persistence`：blur/hidden/focus不清表单、无local/session storage/URL敏感内容；路由离开或账号切换清内存并abort。
- [ ] 写 `test_claim_approval_does_not_show_activated_or_request_password`：pending等待核对、approved显示“待本人激活”与入口；不出现数据库姓名或密码框，成功后跳原 `/activate-profile`，不在URL携带邮箱；旧验证码不会带入激活。运行 `npm test -- src/assistedPeople src/profileClaims src/auth/ActivateProfilePage.test.tsx` 确认新增 RED。
- [ ] 调整输入类型、Zod nullable secret和trim规范化；缺邮箱详情显示“未提供”。表单加入精确文案“邮箱可后续补充；未激活账号不影响已审核人员办理住宿。”；身份更正场景遵守只读邮箱约束并给出管理员撤销入口说明，不用掩码值回填。
- [ ] 实现 `/claim-profile` 公共页：姓名/证件/email→发送码→验证后状态，无密码无原资料预览；收到429遵守Retry-After，SMTP失败允许受限重试。未知目标和真实目标呈现相同待审文案。短会话过期明确要求重新验证查询，不自动重发、不循环后台轮询。注册页增加“已有档案，申请激活”，保留登录页原激活入口和监护邀请专用流程。
- [ ] 公共请求使用独立generation和AbortController；每次start更换会话，旧start/verify/status响应不能污染新状态，失权/登出边界清理。使用现有registration布局和PasswordField的原激活页，不新建滚动容器或修改原生权限检查。敏感状态只存组件内存，不放React Query长缓存。
- [ ] 运行 `npm test -- src/assistedPeople src/profileClaims src/auth src/registration src/app/router.test.tsx src/platform`、`npm run build`、`npm run lint`，预期通过。提交 `feat: add deferred email intake and self claim pages`。

## Task 7 注册处理中的认领审核

**Files:** 新建 `apps/web/src/profileClaims/{adminApi,ClaimReviewPage}.ts(x)`、`apps/web/src/registration/RegistrationProcessingPage.tsx`；修改 `src/app/router.tsx`、`src/layout/AppShell.tsx`，必要时抽出 `RegistrationAdminPage` 的可复用主体但不改变普通注册功能；测试相应 `.test.ts(x)`、`src/layout/AppShell.test.tsx`、`src/registration/RegistrationAdminPage.test.tsx`。

**Interfaces:** `claimAdminApi.context(signal)`、`.list({status,page,page_size},signal)`、`.detail(id,signal)`、`.approve(id,input,signal)`、`.reject(id,input,signal)`、`.revoke(id,input,signal)` 对应任务4；query keys只含scope/id/filter/paging，不含姓名、证件、邮箱。正式后台请求复用 `apiFetch`，不复用匿名claimcookie作为授权。

- [ ] 写 `test_registration_processing_tabs_have_independent_authority`：注册审核人员仅见“注册申请”，仅符合claim审核者可见“档案认领”，都没有则不出现入口；不可因新的外壳绕过原registration.review。`test_claim_review_never_auto_confirms_identity`：默认勾选false、未确认不能批准、通过无备注可提交、拒绝／撤销必填原因。
- [ ] 写 `test_revoked_admin_or_sensitive_permission_hides_stale_detail_and_late_reply`：焦点恢复重新验权，失权立即隐藏，403/404取消并移除private树，旧响应不复活；仍有权限时保留筛选及分页，不把正常后台切换变成全页重置。运行新页面和AppShell测试确认 RED。
- [ ] 实现 `/settings/registrations` 外壳显示独立“注册申请”和“档案认领”子路由；保留既有地址为普通注册入口，有claim权限但无registration权限时转其合法子页。导航只合并显示，不合并权限。claim入口以服务端context为最终准入，后端仍逐目标授权。
- [ ] 实现状态筛选、20条分页/total/待审数、详情及并发version处理；显示原档案缺邮箱、申请资料、验证日期、冲突提示、重复申请数、人工核对说明。明确“批准不会立即创建账号”；拒绝警示勿填写他人隐私。明文身份证需现有用途和揭示权限；没有该权限仍可看脱敏详情但不得绕过敏感入口。
- [ ] 审批按钮来自服务端allowed_actions、can_approve以及本地确认；操作key在同次重试保持，换详情/输入新操作重新生成。绑定且未开户时才显示显式撤销，经原因及确认后调用服务端；成功失效队列、人员详情与context相关缓存，不直接把人员标签改为已激活。
- [ ] 运行 `npm test -- src/profileClaims src/registration src/layout/AppShell.test.tsx src/app/router.test.tsx src/people`、build和lint，通过后提交 `feat: add scoped profile claim review queue`。

## Task 8 隔离联调、恢复验证和 NAS 发布

**Files:** 修改 `apps/api/app/cli/seed_assisted_personnel_e2e.py`、`apps/web/tests/assisted-personnel.spec.ts`，新 `apps/web/tests/profile-claims.spec.ts`；修改 `scripts/run-assisted-personnel-e2e.sh`、`apps/api/tests/cli/{test_assisted_personnel_e2e_launcher,test_seed_assisted_personnel_e2e,test_assisted_personnel_backup_docker,assisted_restore_probe}.py`、必要的固定套件/route inventory断言；新增 `docs/superpowers/verification/2026-10-20-deferred-email-profile-claim.md` 记录实际结果。

**Interfaces:** 现有launcher `--suite assisted` 继续固定白名单，纳入新claim spec且只用受控loopback库；恢复探针包含新表/列/不可变证据，使用不可变API/PostgreSQL image ID。不增加生产调试端点，不在测试脚本嵌入真人资料或NAS凭据。

- [ ] 扩展虚拟种子和验收：成年人/未成年student、独立player、both、同名不同证件、共享电话、无匹配、已有锁定/停用/删除账号、两个竞争邮箱、真实权限组合。写端到端断言：缺邮箱审核→入住→claim验证→管理员人工批准→本人新码激活→登录，`assert person_id/stay_id/nickname/notes/team unchanged`，账户从未激活变已激活。先运行固定launcher，确认新行为 RED，再完善种子/安全白名单。
- [ ] 使用当前被本轮独占的库运行：`ASSISTED_E2E_DISPOSABLE_DATABASE='<已确认的测试库名>' E2E_PYTHON_BIN="$PYTHON" bash scripts/run-assisted-personnel-e2e.sh --suite assisted`。已导出TEST_DATABASE_URL和本轮空闲API/Web端口；不把占位串直接执行。再串行运行 `--suite registration`、`team`、`housing-occupancy`、`housing-reporting`。所有exit0；覆盖桌面1440和mobile360的认领路径，键盘/切邮箱/返回不丢草稿。真实 iOS 另列未验收或取得新的实际证据，不能用Chromium touch模拟代替。
- [ ] 串行API全量 `"$PYTHON" -m pytest --tb=short`，然后 `"$RUFF" check app tests migrations`、`"$MYPY" app`；Web全量 `npm test -- --maxWorkers=4`、`npm run build`、`npm run lint`。保留实际通过数、skip原因、exitcode与检查源指纹；失败先定位修复再重跑相关与完整门禁，不吞掉“无关”失败。
- [ ] 升级恢复测试资产，禁止把旧0067硬编码证据当本轮通过：最新镜像封装0068、新DTO及claims代码，restore探针验证pending/approved/revoked/completed、空email快照、邮箱占位、绑定FK和不可变证据。设置 `ERP_BACKUP_DOCKER_TEST=1`、两个 `ERP_BACKUP_TEST_*_IMAGE` 为本轮验证的 `sha256:` IDs，运行 `"$PYTHON" -m pytest tests/cli/test_assisted_personnel_backup_docker.py -q`；具名恢复测试必须实际PASS非SKIP，保存脱敏恢复证据。
- [ ] 运行规格符合性、安全锁序和整合代码review；逐项关闭Critical/Important问题。检查 `git diff --check` 与scope，不遗漏当前P已上线0067或引入无关未验收功能。本任务检查通过后提交 `test: verify reviewed profile claim lifecycle and recovery`；任何检查不通过不发布。
- [ ] 从P执行其发布脚本，显式指定源，先dry-run再正式发布：

  ```sh
  scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3' --dry-run
  scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3'
  ```

  两次预检均必须通过；正式脚本完成加密备份、迁移、构建和健康校验且exit0。失败为发布阻塞，不直拷文件或临时Docker绕过。不把中途容器healthy视为整次发布成功。
- [ ] 只读核对公开HTTPS18080、active-release、实际迁移头、部署状态complete、静态资源与本轮源码一致和备份恢复记录。只做未提交的空表单/入口检查，不向真人发码或修改入住。记录结果、回滚约束（有新业务数据不做破坏性downgrade）、未验收项及后续导入阻塞；清理仅本次拥有的测试容器/临时凭据。产品发布完成不宣称真实名单已导入。

## 规格覆盖与实施交接

- 规格1–3：任务1、2、6；身份/监护和legacy边界同时由任务5、8回归。
- 规格4：任务1、2、3、6；公开不可枚举、额度、邮件失败、会话和重复申请有具名测试。
- 规格5：任务2、4、7；批准、拒绝、撤销、同人竞争和即时失权均覆盖。
- 规格6：任务1、4、5、6；可信绑定、后继链、历史reserved激活和最终独立OTP均覆盖。
- 规格7–8：任务1–5及7；状态、时效、锁、共享预算、审计、隐私和数据保留贯穿对应任务。
- 规格9：任务8；真实名册仍独立执行，不能用功能通过代替缺失资料、真实调房日或房间核查。

用户确认本计划后开始任务1。主代理承担共用锁/迁移/路由整合和数据库串行调度，独立子代理按实际边界实施与复核；不在计划阶段变更线上规则。
