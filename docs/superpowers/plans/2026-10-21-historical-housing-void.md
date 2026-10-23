# 历史住宿作废 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为超级管理员提供逐条、可审计的已退房历史作废功能，保留人员、账号、其他入住及调房链；安全处理本次名单的两条旧住宿冲突后补录四人入住。

**Architecture:** 保持原住宿状态和时间，添加独立作废事实与持久幂等操作记录；通过一个专用写服务原子完成权限复核、作废和审计。有效住宿读取共用排除谓词，已生成报送继续读取原加密快照；前端消费服务端能力，不推断权限。

**Tech Stack:** 现有 FastAPI、SQLAlchemy、PostgreSQL、Alembic、EnvelopeCipher、React、TypeScript、React Query、Vitest、Playwright；不增加运行依赖。

**Spec:** `docs/superpowers/specs/2026-10-21-historical-housing-void-design.md`，用户已确认。

状态（2026-10-22 收尾）：任务 1–5 已完成实现、独立审查及适用验收：真实恢复 64 项、浏览器 48 项、完整 Web 2,870 项通过；候选 `1dae523e21642cfffd6f9d5a772faf058f59c149` 完整后端为 8,590 通过／7 显式跳过／34 警告。任务 6 已经标准脚本成功发布 NAS 0070，实际 release 为 `20261022T012843Z-5d47a5fccb7a-3fe7c5403573`；两条历史经正常 Chrome 后台双确认作废、四人入住补齐，每次独立只读核验通过。新增 47 人全部源事实检查通过，仍未激活、待分队；原有 20 人源事实已核验且跨操作指纹保持，最终 67/67，既定排除项不变。主代理已核实指定临时明文副本删除、原始工作簿未改，内存核验材料已释放，本轮独占测试库已清理；满房详情标题的既有显示问题继续披露。详细证据见同名 verification、任务账本及主代理收尾报告 `task-6-production-report.md`。下方复选框保留原计划，实际执行结果以账本为准；本轮未对真实 index/HEAD 执行 stage/commit。

## Global Constraints

- 初版仅超级管理员可作废历史住宿；普通住宿查看和管理权限不扩大，授予普通房态权限不能绕过实际超级管理员身份。
- 仅已退房且尚未作废的记录可操作；保留原状态、实际日期、人员关系及调房链，不变更当前其他房间的入住。
- 原因限定为 2–500 个字符，不允许填写完整证件或手机号；要求二次明确确认，不默认勾选确认。
- 同一请求键与相同内容在重新核验权限后返回原结果；同键不同内容拒绝。新键再次操作已作废记录时提示“已作废，无需重复操作”，不新增成功审计。
- 初版不提供物理清除、批量作废、自动纠错或撤销作废；作废不是取消预约，也不是退房。
- 一般有效住宿、统计、新住房导出与新实名报送排除作废记录；超级管理员显式查看留存项和原因，其他角色不获得额外敏感信息。
- 已生成的实名报送批次、加密快照及已下载文件不自动重写或删除。报送批次作废仍为独立操作。
- 原因保存在受限业务记录中；普通日志和通用审计不复制原因原文、完整证件、联系方式或报表数据。
- 原有普通回填入住的历史冲突校验缺口不在此计划内隐含重构；真实导入继续独立逐条历史区间预检。
- 仅处理重新核实的 4-201、4-212 两条目标旧住宿；不影响原有 20 人。1 幢排除、昵称备注、证件更正及待激活规则不变。
- 无入住日期使用实际写入当天的中国业务日期；无离开日期为长期入住。不能改源表真实日期或扩大容量掩盖历史冲突。
- 适用检查通过后执行 `scripts/deploy-nas.sh`；预检、备份、迁移或发布失败即停止，不得直拷或临时 Docker 部署绕过。

## Review Focus

1. 已打开弹窗或已成功请求后失去超管身份：首次写与幂等重放均拒绝，缓存/迟到响应不泄露原因（任务 2、4、5）。
2. 旧历史是调房链的来源或去向：保留完整链和当前入住；隐藏旧项不能通过内连接把目标入住一起隐藏（任务 3、5）。
3. 生成报送与作废并发：旧快照原样保留，新的稳定快照排除已提交的作废事实，不能出现半更新的数量/内容（任务 3、5）。
4. 双击、超时、两名超管不同键争抢：一次作废、一次成功审计；失败回滚，恢复数据库后重放也不重复（任务 2、4、5）。
5. 被作废的是邀请摘要最新历史，或唯一保留的房间历史：回退到下一条有效记录，但仍阻止删除房间/人员，不能扩张他人历史查看权（任务 3）。

## 工作源、依赖和验证约定

`P=/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`。所有路径相对 P；下文 `app/`、`tests/`、`migrations/` 相对 `P/apps/api`，`src/` 相对 `P/apps/web`。API 命令在 `P/apps/api` 执行，npm 在 `P/apps/web`，脚本在 P。

当前待补建档候选仍冻结。**先结束其验收、发布并记录实际基线，再为本功能建立独立差异账本**；未结束前只编辑本规格/计划，不修改公共代码、启动重测试或重用其数据库。复用完整 linked worktree，不从旧 HEAD 丢弃尚未提交的已发布业务代码。开始时复核迁移头；本计划预期前置为 `0069_pending_profile_intake`，若已变化，先调整顺序并审查，禁止形成两个迁移头。

工具：`PYTHON=/Users/skymoonzyj/Documents/new project3/.venv/bin/python`，Ruff/mypy 使用同目录工具。`TEST_DATABASE_URL` 只指向本轮独占、回环地址、名称以 `_test` 结尾的临时库；先运行 `"$PYTHON" -m app.cli.e2e_database_safety`，必须 exit 0。会重置 schema 的测试统一串行。独立审查可并行，不停止用户其他项目，不同时启动多组重测试。

每任务记录 RED→GREEN 与独立审查。此源有大量既有未提交修改，沿用当前临时索引快照/差异账本，不污染真实 index、不提交混合历史。需要真实提交时另行核对独立可暂存范围，避免 post-commit 提前部署；本轮最终发布仍必须显式执行部署脚本。

任务依赖：1 → 2/3 → 4 → 5 → 6。任务 2 与 3 可按已冻结契约分流；共享 `schemas.py`、`occupancy_router.py` 和迁移清单由主代理单一写入。测试执行不能因分流而共用数据库并发。

## 文件与契约图

- 新建 `app/housing/history_void_{models,schemas,access,service,router}.py`：分别负责操作凭据、输入输出、授权围栏、原子写、HTTP 接入。只在已有大文件增加集成点。
- 新建 `app/housing/history_validity.py`：纯有效性 SQL 谓词；`HousingStay` 作废列仍放在现有 `models.py`，与住宿模型共同维护。
- 现有 `occupancy_router.py`、`occupancy_service.py`、`occupancy_export_service.py`、`reporting_projection.py`、`invited_projection.py`：按各自读取职责消费谓词，不整体重构。
- 新建 `src/housing/HousingHistoryVoidDialog.tsx`：原因和二次确认。既有房间详情、生命周期协调器、API 与查询键负责集成。
- 新增专用迁移/服务/API/并发/读取矩阵/浏览器/恢复测试；现有住房、报送、个人摘要、权限测试作为回归，不复用真实名单或 NAS 账号作测试种子。

## Task 1 — 可恢复的作废事实与接口契约

**Files:** 新建 `migrations/versions/0070_housing_history_void.py`、`app/housing/history_void_models.py`、`app/housing/history_void_schemas.py`、`app/housing/history_validity.py`、`tests/db/test_housing_history_void_migration.py`、`tests/unit/test_housing_history_void_input.py`；修改 `app/housing/models.py`、`migrations/env.py`、`scripts/db-schema-manifest.sh`、`tests/db/test_migrations.py`、`tests/cli/test_operations_docs.py`、`tests/cli/test_local_container_acceptance_launcher.py`。

**Interfaces:** `StayHistoryVoidRequest(expected_version: int >= 1, reason: SecretStr, confirmed: Literal[True], idempotency_key: UUID)`，extra=forbid；confirmed 原始值必须为 JSON true，不能以 1 或字符串代替；trim 后验证原因。`StayHistoryVoidResult(operation_id: UUID, stay_id: UUID, room_id: UUID, person_id: UUID, version: int, voided_at: AwareDatetime)`。`effective_stay_clause(stay: type[HousingStay] | AliasedClass[HousingStay] = HousingStay) -> ColumnElement[bool]`，仅表达 `voided_at IS NULL`，不替调用方改变状态/范围条件。

- [ ] 写 `test_void_migration_preserves_existing_rows_and_constraints`：`assert legacy_rows_after == legacy_rows_before`；部分标记、非 checked_out 标记及未知操作人插入均 `raises(IntegrityError)`。写 `test_void_input_contract`：1/501 字符、完整手机/证件、false/missing/1/字符串 confirmed、额外字段均拒绝；2/500 字符合规原因接受，`assert raw_reason not in repr(request)`；API 422 与日志不回显校验失败的 reason 原文。
- [ ] 运行 `"$PYTHON" -m pytest tests/db/test_housing_history_void_migration.py tests/unit/test_housing_history_void_input.py -q`，确认因缺失本功能而 RED，不把缺测试库当 RED。
- [ ] `HousingStay` 增加 nullable `voided_at`、`voided_by_user_id`（账号 RESTRICT FK）、`void_reason_ciphertext`。约束全空或全有，非空只允许 checked_out；已存在作废事实不可被改写/清空。保持状态枚举、日期和调房 FK 不变。新增 `HousingStayVoidOperation`：UUID id、actor_account_id、idempotency_key、stay_id、request_fingerprint、result_version、created_at；actor/key 唯一、stay_id 唯一、FK RESTRICT，操作凭据不可修改/删除。
- [ ] 实现上述输入输出和谓词；迁移继承 0069，现存记录新列全空，不做回填作废。降级先检查作废记录或操作凭据，存在即拒绝，在检查前不得删除任何结构。清单增加准确列、约束、触发器、操作表和新 revision；不猜表数量，按完整 schema 核验。
- [ ] GREEN：重跑本任务命令及 `tests/db/test_migrations.py`；断言空事实可升级/降级往返、存在事实不能有损降级、原调房外键与报送快照防改触发器保留。独立审查通过后保存任务快照。

## Task 2 — 超管授权、原子写与持久幂等

**Files:** 新建 `app/housing/history_void_access.py`、`app/housing/history_void_service.py`、`app/housing/history_void_router.py`、`tests/services/test_housing_history_void.py`、`tests/api/test_housing_history_void.py`、`tests/db/test_housing_history_void_concurrency.py`；修改 `app/api/router.py`、`app/housing/errors.py`、`app/housing/api_support.py`、`app/rbac/seed.py`、`app/audit/service.py`。

**Interfaces:** `require_history_void_authority(db: Session, actor: AuthContext, room: HousingRoom) -> None`；`can_void_history(db: Session, actor: AuthContext, room: HousingRoom, stay: HousingStay) -> bool`。`HousingHistoryVoidService(db: Session, cipher: EnvelopeCipher, lookup: LookupDigest, *, occurred_at: datetime, request_id: str).void(actor: AuthContext, stay_id: UUID, command: StayHistoryVoidRequest) -> StayHistoryVoidResult`。`POST /api/v1/housing/stays/{stay_id}/void-history`，成功/同内容重放均 200；冲突 409，输入错误 422，未授权依现有私有错误规范 403/404。

- [ ] 写 `test_void_requires_live_superadmin_even_on_replay`：普通 admin/行政/教练/领队/只有显式授权者全部拒绝，撤销超管、账号停用、目标不可见后原键重试也拒绝。写 `test_void_once_or_rolls_back`：相同键结果相等且 `assert allowed_audits == operations == 1`；注入审计或凭据保存失败，`assert state_after == state_before`。各生命周期状态、缺 CSRF、强制改密、过期版本均有拒绝断言。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_housing_history_void.py tests/api/test_housing_history_void.py tests/db/test_housing_history_void_concurrency.py -q` 确认 RED。
- [ ] 增加 `housing.history.void` 并纳入 `SUPER_ADMIN_ONLY_PERMISSIONS`，且写服务始终另查实际 super_admin 角色及有效账号、当前房间可读范围和该权限。不能仅信 `AuthContext` 的缓存角色。GET 能力为只读提示；POST 用当前认证/CSRF依赖并在事务内再次核验。
- [ ] 服务用 `atomic_write_transaction`：先 actor/key 域隔离 advisory lock（域 `housing-history-void`），一次锁定所有相关人员（按 UUID）、账号（按 UUID），再房间、历史 stay，刷新 ORM 状态。遵循既有角色变更与住房 people→rooms→stays 的兼容顺序；不得取房间锁后补取人员锁。操作参与人和目标身份从受限读取取得，锁后重核对，漂移则冲突。当前授权先于任何凭据重放。
- [ ] 指纹用 `lookup.digest_domain("housing-history-void:v1", canonical_json)`，包含目标 stay、expected_version、trim 后 reason、confirmed；凭据不存明文 reason。相同键同指纹回放存储结果，不再验旧版本、不再写成功审计；同键异内容 409 `housing_void_operation_conflict`。通过当前授权的新键遇已作废记录返回 409 `housing_stay_already_voided` 和“已作废，无需重复操作”，不新增任何作废审计或操作凭据，测试断言该重试前后审计总数不变；未授权尝试仍按既有安全规则记录拒绝。
- [ ] 首次成功以 `field_name="housing_stay_void_reason"` 加密原因，标记目标、version + 1，保存操作结果及 `housing.stay.void_history` 成功审计于同一事务。只审计安全 UUID、版本和结果，不复制原因；不要复用会无条件新增成功审计的 `occupancy_router._execute_mutation`。响应 private/no-store，失败日志保持脱敏。
- [ ] GREEN：重跑上述命令，独立连接验证同键竞态/异键双超管/角色撤销/审计失败，断言一次成功、无死锁、原人员账号及其他 stay 字节事实不变。补同键跨目标/版本/原因冲突与授权先于重放测试；独立审查并保存快照。

## Task 3 — 有效历史、调房与报送一致性

**Files:** 修改 `app/housing/{schemas,occupancy_router,occupancy_service,occupancy_export_service,reporting_projection,invited_projection}.py`；新建 `tests/services/test_housing_history_void_projection.py`；扩展 `tests/api/test_housing_occupancy.py`、`tests/db/test_housing_reporting_concurrency.py`、`tests/services/test_invited_housing_projection.py`、`tests/services/test_housing_portal.py`、`tests/api/test_person_housing.py`、`tests/db/test_housing_inventory_service.py`。

**Interfaces:** `StayAllowedActions.void_history: bool = False`；`StayVoidInfo(voided_at: AwareDatetime, voided_by_user_id: UUID, reason: str)` 仅在超管受限响应中解密，`StaySummary.void_info: StayVoidInfo | None = None`。`HousingRoomDetail.voided_history: list[StaySummary]` 默认 factory=list、`can_view_voided_history: bool = False`；既有 room detail GET 增加 `include_voided: bool = False`。`TransferSummary.from_stay_voided/to_stay_voided: bool = False` 仅作既有授权调房行的状态说明，不带原因/新人员资料。

- [ ] 写 `test_void_transfer_source_preserves_current_destination`：`assert current_room_after == current_room_before`、`assert transfer_fk_pair_after == before`、`assert source_id not in effective_history`；默认 history 排除、超管显式查询含 tombstone/原因，普通角色显式查询拒绝且 payload 无原因。写 `test_void_latest_referral_history_falls_back_before_rank` 与 `test_void_history_still_blocks_room_and_person_deletion`，断言下一条有效历史被选中、无有效历史时沿用未安排语义、留存历史继续阻止原删除操作。
- [ ] 运行 `"$PYTHON" -m pytest tests/services/test_housing_history_void_projection.py tests/services/test_invited_housing_projection.py tests/api/test_housing_occupancy.py -q` 确认 RED。
- [ ] 将任务 1 谓词应用到有效 history、调房历史容量与候选、住房敏感导出、新报送候选，以及邀请摘要的 exists/排序源；在计数、候选验证和分页前排除。当前/预约只补不变性回归，不扩大个人/邀请历史 DTO。留存删除保护查询不得加排除谓词；调房关联仍连接原始两端，再添加作废标识，不让过滤器删掉整条链。
- [ ] 房间 GET 默认不解密原因。显式 include_voided 必须重新核验超管、房间范围及 history.void 权限，返回单独 voided_history，所有作废项动作 false。非超管的既有调房说明只增加已作废状态，不包含原因/操作人，也不扩大房间可见范围。
- [ ] 写 `test_report_snapshot_survives_void_and_new_generation_excludes_it`：`assert old_ciphertext_digest_counts == before`、`assert parsed_old_download == before_download`，`assert voided_stay_id not in new_projection`；不以 XLSX 压缩容器的生成时间差误判业务变化。并发测试固定既有 REPEATABLE READ 快照边界：先建立快照的生成可保留旧事实；作废提交后才建立快照的新生成必须排除，内容与计数一致。禁止扫描解密所有旧批次或自动作废批次。
- [ ] GREEN：本任务所有列出测试、`tests/api/test_housing_reporting.py`、`tests/services/test_housing_reporting.py`、`tests/db/test_housing_transfer_checkout.py` 通过；检查未改变报送 schema/下载快照、房间容量或当前个人/邀请住房。独立审查并保存快照。

## Task 4 — 后台逐条确认与缓存隔离

**Files:** 新建 `src/housing/HousingHistoryVoidDialog.tsx`、`src/housing/HousingHistoryVoidDialog.test.tsx`；修改 `src/housing/{api.ts,housingKeys.ts,useHousingRoomLifecycle.tsx,HousingRoomDetailPage.tsx}`、`src/settings/AuditPage.tsx`；扩展 `src/housing/{api.test.ts,HousingRoomDetailPage.test.tsx}`、`src/settings/AuditPage.test.tsx`。

**Interfaces:** Web `StayHistoryVoidRequest = {expected_version: number; reason: string; confirmed: true; idempotency_key: string}`，`StayHistoryVoidResult` 将任务 1 的 UUID/时间映射为 string、version 为 number。`voidHousingStayHistory(stayId: string, payload: StayHistoryVoidRequest): Promise<StayHistoryVoidResult>`；`HousingHistoryVoidDialog({stay: HousingStay; linkedTransfers: readonly HousingTransfer[]; pending: boolean; error: string | null; focusFallbackRef: RefObject<HTMLElement | null>; onSubmit: (payload: StayHistoryVoidRequest) => void; onClose: () => void}): ReactElement`。传入的 linkedTransfers 只来自当前授权 room detail，沿用 HousingDialogShell 的焦点/忙碌语义，不额外获取其他房间人员。

- [ ] 写 `requiresReasonThenExplicitConfirmation`：初始不能提交，首步校验原因后显示目标人员/房间/实际日期/调房关联；第二步复选未勾选时禁用最终按钮。写 `retainsOperationKeyAfterTimeout`：`expect(retry.idempotency_key).toBe(first.idempotency_key)`，双击一次网络调用；在结果不确定后修改原因必须先查询目标再开始新操作。
- [ ] 运行 `npm test -- src/housing/HousingHistoryVoidDialog.test.tsx src/housing/HousingRoomDetailPage.test.tsx src/housing/api.test.ts --maxWorkers=2` 确认 RED。
- [ ] 详情只按服务端 `allowed_actions.void_history` 显示操作；在既有 StayActionKind/标签表及生命周期协调器中增加独立分支，不复用取消预约/确认退房弹窗。二次确认文案明确“只作废此条历史，不删除人员、账号和其他住宿”，提示“已生成报送不会自动改变”；最终按钮“确认作废历史住宿”。原因仅组件内存、不写 URL/localStorage/日志。提供显式“查看已作废历史”，服务端允许才显示，默认关闭。
- [ ] 使用现有 mutation coordinator/account epoch 与 stale-result 防护；同一确认操作失败重试保留 key，关闭/新操作使用新 key。成功或已作废冲突后读回，不乐观伪造结果。`housingKeys.detail(..., includeVoided=false)` 加入变体，失效按 details 前缀同时覆盖两个变体；退出/换账号/失权丢弃弹窗、reason、tombstone缓存与迟到响应，撤权后不能由结果重新打开入口。
- [ ] 成功刷新相关房间详情/overview、人员住房及当前已缓存的 `invitationKeys.housingRoot(accountId)`（来自 `src/invitations/keys.ts`），不额外预取隐藏数据；既有已生成报送列表不改状态。调房行显示相应“来源历史已作废／去向历史已作废”。审计操作中文映射为“作废历史住宿”，不展示原因原文到通用审计。
- [ ] GREEN：上述测试及 `src/settings/AuditPage.test.tsx` 通过。补 `revocationDropsSensitiveLateResponse` 与两种 includeVoided 缓存互不串读断言；运行 `npm run lint`、`npm run build`。独立审查并保存快照。

## Task 5 — 跨流程、浏览器与真实恢复门禁

**Files:** 新建 `apps/web/tests/housing-history-void.spec.ts`、`tests/cli/housing_history_void_restore_probe.py`、`tests/cli/test_housing_history_void_backup_docker.py`；修改 `app/cli/seed_e2e.py`、`scripts/run-housing-occupancy-e2e.sh`；新增 `docs/superpowers/verification/2026-10-21-historical-housing-void.md`。

**Interfaces:** 新恢复 probe `seed_history_void_case() -> dict[str, object]` 和 `verify_history_void_case(expected: dict[str, object]) -> dict[str, bool]`，仅隔离容器内运行，输出断言名/布尔值及安全标识，不输出原因/身份明文。复用已有 `test_assisted_personnel_backup_docker.py` 的 immutable image、归属账本、受限临时目录、精确清理模式；不改动已有 0069 历史证明，不对 NAS 做恢复。

- [ ] 新恢复纯测试 `test_restore_probe_rejects_missing_or_tampered_void_fact`：分别去掉 marker/操作凭据/审计，或改目标 stay、调房端点、旧报送摘要时 `assert verification_failed`。新 Playwright 流程：合法超管原因→确认→刷新仍已作废；原来源/现目标、其他角色无按钮、失权后拒绝、手机输入和确认可见、旧报送下载不变。
- [ ] 先运行 `"$PYTHON" -m pytest tests/cli/test_housing_history_void_backup_docker.py -q` 与只列举新增浏览器用例，确认纯校验 RED；实现 probe 和测试种子后先使纯测试 GREEN。种子全部虚构，无真实名单信息，不缩短 OTP 限制或修改原浏览器超时/重试规则。
- [ ] 在已有 housing occupancy launcher 的原四种视口中加入新 spec，保持每 spec 独立重置。运行 `bash scripts/run-housing-occupancy-e2e.sh` 及 `bash scripts/run-housing-reporting-e2e.sh`，串行真实浏览器验收；失败保留脱敏阶段/错误证据，定位修复后才重跑，不盲目循环或延长时间掩盖问题。
- [ ] 构建并验证本轮 API 内容一致的不可变 image ID，使用现有两项镜像环境变量及 `ERP_BACKUP_DOCKER_TEST=1` 运行 `"$PYTHON" -m pytest tests/cli/test_housing_history_void_backup_docker.py -q`。实际生成加密备份→恢复到全新精确归属隔离容器→核验同一人/账号、作废来源、现住目标、调房 FK、加密原因、一次审计、旧报送原字节及幂等重放；新生成报送继续排除。actual 用例不可 SKIP，保留 backup/image/source/schema 指纹，清理只操作本轮资源。
- [ ] 执行任务 1–4 全部回归以及权限/人员删除保护关联回归、全 Web 测试；运行 `ruff check app tests migrations`、`mypy app`、`npm run lint`、`npm run build`、`git diff --check`。主代理做整体验证，独立审查具体 source 快照。门禁报告区分 PASS/SKIP/未跑、已知独立缺口与本次结果；全部阻断项清零才进入任务 6。

## Task 6 — NAS 发布与两条历史纠错、四人补录

**Files:** 更新本计划和上述 verification；实际导入沿用现有受限名单工作副本、只读核验器及导入账本，不把真人数据加入仓库。若只读核验器仅接受 0069，先为新 head 的适用 schema 加明确校验及本地回归，不能删除版本围栏。

**Interfaces:** 部署仅 `bash scripts/deploy-nas.sh --source "$P" [--dry-run]`；真实作废和入住仅正常登录的超管后台。只读核验可用既有受限脚本，不通过数据库直接修改业务。

- [ ] 门禁齐全后，从 P 运行 `PATH="/Users/skymoonzyj/Documents/new project3/.venv/bin:$PATH" bash scripts/deploy-nas.sh --source "$P" --dry-run`，必须 exit 0；核对实际基线 revision、已备份能力、目标 source 没混入私人数据或其他未验收代码。dry-run 不算发布。
- [ ] 运行同一命令去掉 `--dry-run`；失败即停止。成功后只读核验 `.active-release`、源码/运行 image、唯一迁移头、健康、HTTPS 18080 和新路由/能力；不在 NAS 创造测试历史。迁移含真实作废事实后不允许破坏性 downgrade；回退旧代码也须确认能理解 marker，否则停写并采用前向修复。
- [ ] 正常后台重新读取精确目标：4-201 的 2026-08-29 旧历史、4-212 的 2026-07-13 至 2026-10-21 旧历史，核对 ID/版本/人/房间/时间/调房两端。后者已知 stay ID `9b4f8b25-b227-479c-998f-161998fa4ad3` 仍须核对；前者由实际读取确定，不能凭房号选择任意记录。对与批准对象不一致的条目暂停，不扩大范围。
- [ ] 逐条经双确认提交，原因“按已确认住宿名单纠正重叠旧历史，保留原记录与调房关联”。每次读回 marker/成功审计，核对其他入住、账号和报送快照未改；结果不确定先读回，不重新生成键盲重试。任何一条失败只暂停其对应两人入住。
- [ ] 对源表行 6/7（4-201，2026-08-13 入住）及 16/17（4-212，2026-07-31 入住），先核查档案是否已存在，不重复建人；再次做包含有效历史的区间预检，通过后在后台安排双人间长期入住。保留现有 20 人和其他已成功条目；核验两房有效当前入住各 2 人及其他日期/容量不变。
- [ ] 最终按“已建档／已入住／仅建档待入住／失败或排除”分别汇总并安全读回，不以按钮成功或静态计数替代落库证明。未授权自动纠正的历史问题另列。清理本轮临时证据中的明文工作副本/凭据，只保留原始用户文件、脱敏账本与必需核验材料；报告真实上线结果及剩余项。

## 计划自审与实施交接

- 规格覆盖：权限/输入/原子写/幂等 → 1、2；读取/调房/快照/留存 → 3；双确认/失权与手机 → 4、5；迁移/恢复/审计 → 1、2、5；脚本发布和两条真实纠错/四人补录 → 6。
- Review Focus 五项均对应有名测试和具体断言；不得把备份纯测试、fake launcher 或旧候选结果当真实恢复/浏览器通过。
- 本计划没有授权批量删除、改源表日期、取消任意现住、授予新角色或重构普通回填入住规则。已确认的子任务协作方式保留，按依赖推进并独立审查。
- 用户确认本计划后才开始任务 1；当前冻结的待补建档候选先完成自己的发布门禁。此文档不是上线证明。
