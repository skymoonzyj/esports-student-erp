# 云备份免填应用信息导入实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 超管无需申请或填写 AppKey/AppID，首次在外部授权页取得百度或 115 授权结果，导入 ERP 验证并绑定专用备份目录。

**Architecture:** 在现有授权状态机上增加显式 `openlist_builtin_import` 分支，保留 `own_app` 与手动 WebDAV。API 保存加密材料、权限及幂等状态，主机授权运行器负责固定协议的身份验证和一次性刷新，隔离连接器候选在显式确认前不得替换旧目标。刷新交接后只由连接器维护令牌。

**Tech Stack:** React、TypeScript、FastAPI、SQLAlchemy、PostgreSQL、Alembic、Python 主机运行器、OpenList 4.2.6、Vitest、pytest、Playwright。

**Spec:** [免填应用信息补充规格](../specs/2026-10-07-cloud-backup-builtin-import-design.md)，继承 [原扫码授权规格](../specs/2026-10-06-cloud-backup-qr-authorization-design.md)；冲突时补充规格优先。

## Global Constraints

- 本计划和百度有效期兼容修订已于 2026-10-09 经用户确认，实施中；完成验证前不宣称产品已开发或上线。
- 默认新绑定使用 `openlist_builtin_import`；省略模式的旧 API 请求仍为 `own_app`，所有历史记录保持原模式。
- 只开放百度、115；不增加夸克扫码、抓取 Cookie、嵌入登录页、自动接收第三方回调或任意 HTTP 代理。
- 外部授权页固定为 `https://api.oplist.org/`，新标签页使用 `noopener noreferrer`，不携带 ERP 账号、Cookie 或令牌。
- 两项令牌各最多 4096 字符，去首尾空白后拒绝内部空白、控制字符及明显误贴的 URL/HTML/JSON/Cookie；不解析或访问输入地址。请求体最多 16 KiB。
- 会话创建限流保持每账号、每提供商每分钟 3 次和每小时 10 次，两个模式合并计数；导入提交另限每分钟 5 次；429 必须有 `Retry-After`。
- 导入会话 10 分钟；验证成功后独立 10 分钟配置期；刷新或重试不延长期限。每个提供商最多一个未结束会话。
- 实际 `super_admin` 角色与全局 `operations.monitor.read` 双重校验；绑定发起账号和 ERP 登录会话，保留 CSRF、防缓存、异步执行前重新验权。
- 浏览器向 ERP 只通过同源 POST body 提交秘密，API 与主机仅用私有 stdin RPC；厂商边界按任务 3 固定协议传输。使用信封加密，不进审计、错误、日志、持久浏览器存储或共享查询缓存。
- 百度内置刷新不报告有效期时显示“到期时间未知”；115 必须提供有效期限。自有应用原 `TokenGrant` 契约不放宽。
- 发送刷新后发生不确定结果，禁止重发旧令牌；新令牌持久化后只继续身份核对或候选交接，不重新刷新。
- 显式确认应用前不修改旧目标；不删除云端历史文件或 NAS 数据卷；不改 HTTPS 18080、备份时间或云复制间隔，不依赖飞牛。
- 本工作树有大量已有修改；只改本任务列出的范围，不还原、不全量暂存、不自动提交。最终发布必须使用主仓库 `scripts/deploy-nas.sh --source`，任何预检、备份、迁移、恢复或健康失败均阻断交付。

## Review Focus

- 粘贴了其他账号或正在使用的刷新令牌：刷新前拒绝已知重复；身份不一致绝不替换旧目标，并明确新授权可能使厂商旧授权失效。
- POST 已被接收但响应丢失：按原 UUID 查询安全状态，不新增任务；未知状态不自动重发令牌。
- 刷新请求已发出后运行器被杀、租约到期、数据库不可写：恢复只读持久检查点，不重发旧令牌、不将未确认结果显示为成功。
- 切出外部授权页、后台刷新卡片、退出后相同账号重新登录：保留当前页面输入仅限原有效登录会话；退出、会话变化及迟到响应不能恢复秘密。
- 还原旧备份或新旧模式混合记录：来源沿原模式恢复，未知记录版本拒绝猜测，百度未知有效期不能伪造成到期时间。

### 2026-10-10 最终审查修订：完整恢复与同库重启分离

原检查点续作要求只适用于数据库未回滚的正常进程重启。完整备份恢复/新主机交接必须在主机 journal 中先持久化 `restore_handoff`，通过私有恢复动作在同一锁顺序/事务中校验原 exact gate 和来源，关闭所有未完成内置导入并释放 gate，要求重新授权；保留已应用目标、旧 own_app/手动配置和受保护候选清理日志。无 gate 的丢失应答重试只读确认，不能失效新授权；新/foreign gate 或来源校验失败不释放。普通备份仍使用原释放动作，不失效正常同库任务。

最终修复验证增加真实较早数据库状态 → 原实例较晚外部刷新/交付 → 回滚较早状态 → 认证恢复交接的回归，覆盖 not_started/sent/refreshed/verified，断言外部刷新和旧令牌交付次数不增加；补验证丢失 ACK、持久 journal 中断重试、新授权保护、已应用目标/清理日志及来源失败保留 gate。两项独立 Docker 恢复 gate 在最终源码上串行重跑。

## 工作目录与任务依赖

使用现有隔离工作树 `/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3`。执行前重新确认 `AGENTS.md`、工作树状态、迁移 head 和 NAS 实际发布源。当前 head 为 `0063_cloud_authorization`；本计划预留 `0064_cloud_auth_builtin_import`，若届时冲突，先调整编号及 manifest，不能覆盖其他迁移。

任务 1 固定契约后，任务 2、3 可以独立推进；任务 4 待任务 2、3 完成后修改共享 bridge/apply 文件；任务 5 使用任务 2 的 HTTP 契约，可与任务 4 并行；任务 6 在全部合并后运行。用户此前要求按实际需要并行，推荐三路分工、按依赖启动、逐任务独立审查，不为凑数量拆分共享文件。

下文列出的后端测试路径，在工作树 `apps/api` 用 `PYTHONPATH=. '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest` 后追加完整路径执行；成功判据是退出码 0 且没有失败用例。有数据库的测试必须指定经 `app.cli.e2e_database_safety` 验证的本地一次性 `TEST_DATABASE_URL`；一个数据库同时只跑一个测试进程。前端命令在 `apps/web` 执行。所有测试只用合成凭据。

## Task 1 数据模型和跨组件契约

**Files:** 修改 `apps/api/app/operations/cloud_auth_{models,schemas,requests,state}.py`、`apps/api/app/operations/cloud_schemas.py`、`scripts/db-schema-manifest.sh`；创建 `apps/api/migrations/versions/0064_cloud_auth_builtin_import.py`、`apps/api/app/operations/cloud_auth_source.py`；测试新增 `apps/api/tests/unit/operations/test_cloud_auth_import_contracts.py`，扩展 `apps/api/tests/db/test_migrations.py` 和现有 schema manifest CLI 测试。

**Interfaces:**
- `AuthMode = Literal['own_app', 'openlist_builtin_import']`。
- `AuthSource(mode, auth_config_id: str, auth_config_revision: int, app_revision: int | None)`；自有配置 ID 固定 `own-app:{provider}`、来源修订等于 app_revision；内置 ID 固定 `openlist-builtin-import-v1:{provider}`、修订 1。这是授权来源修订，不是候选目录的 `config_revision`。
- `CloudSessionCreate(operation_id: UUID, mode: AuthMode='own_app')`；`CloudImportCommand(CloudOperationCommand)` 增加 `access_token: SecretStr`、`refresh_token: SecretStr`。
- `CloudImportOperationView(operation_id: UUID, status: Literal['pending','succeeded','failed','cancelled'], session: CloudAuthSessionView, error_code: str | None)`；公开返回无令牌。
- 私有 `ImportedTokenGrant(access_token, refresh_token, expires_at: datetime | None, expiry_source: Literal['provider','unreported'], refreshed_at: datetime)`，秘密禁止 repr；未知有效期只允许百度内置模式，原 `TokenGrant` 不变。会话另存身份核对完成的 `verified_at`，不得把刷新时间冒充身份验证时间。
- `CloudAuthSessionView` 新增 `mode` 和可空 `verified_at/token_expires_at/expiry_source`；目标公开视图新增可空 `authorization_mode`，仅由目标关联候选取得，手动 WebDAV 为 null，不能根据 UI 默认值推断。
- `require_auth_source_current(db: Session, row: CloudAuthSession) -> None` 集中检查冻结来源；`AuthFence` 同步携带来源，旧协议按明确版本读取。

- [ ] 写红测：省略模式仍 own_app；错误模式拒绝；4096 字符可接收、4097 拒绝；内部空白/控制字符/明显误贴内容拒绝，异常及 repr 不含输入；百度 `unreported/null` 可建新私有类型、115 拒绝；原 TokenGrant 仍拒绝 null。
- [ ] 运行 `tests/unit/operations/test_cloud_auth_import_contracts.py`，确认因新契约不存在而失败。
- [ ] 增加迁移：session、operation、candidate 保存来源字段；session 新增 nullable `application_provider` FK，旧行回填原 provider，原 provider 到应用表的无条件 FK 解除。own_app 强制应用引用和非空正 `app_revision`；内置强制应用引用与 app_revision 为 NULL、固定来源修订大于零。所有 CHECK 显式处理 NULL，不能仅用比较表达式。
- [ ] 扩展 session 状态 `awaiting_import`、`verifying_import`，operation action `create_import`、`verify_import`，共享 live-provider 唯一索引包含新状态。operation 新增 `refresh_phase`（not_started/sent/refreshed/verified/unknown，默认 not_started）、`refresh_checkpoint_ciphertext`（可空 Text）、`import_token_digests_ciphertext`（可空 Text，保存原始及已轮换令牌的域隔离 HMAC）。新 AAD 绑定模式；原密文用原格式读取，禁止回填时错误解密。
- [ ] 写并运行迁移测试：0063 真实旧记录升级后字段、密文及模式保留；无应用内置会话可创建；非法 NULL 组合失败；已有内置记录时降级拒绝且零写入，纯旧记录可降级。运行 `tests/db/test_migrations.py` 及 `tests/cli/test_operations_docs.py`、`tests/cli/test_local_container_acceptance_launcher.py`，全部通过。
- [ ] 保存精确任务 diff 和红绿测试证据，独立审查契约后交给任务 2–5；不全量提交当前工作树。

## Task 2 API 导入流程和隐私边界

**Files:** 创建 `apps/api/app/operations/cloud_auth_import.py`；修改 `cloud_auth_service.py`、`cloud_auth_router.py`、`cloud_auth_errors.py`、`cloud_auth_security.py`、`cloud_auth_apply.py`、`cloud_service.py`、必要的 `app/audit/service.py` 白名单；新增 `apps/api/tests/api/test_cloud_authorization_import.py`，扩展现有 authorization/concurrency/apply 测试。

**Interfaces:**
- `create_session(provider, actor, operation_key, mode='own_app') -> CloudAuthSessionView` 兼容现有调用；内置创建为 `awaiting_import`，不调用厂商，不要求自有应用。
- `submit_import(session_id: UUID, actor: AuthActor, input: CloudImportCommand) -> CloudImportOperationView`；`get_import_operation(session_id, actor, operation_key) -> CloudImportOperationView`。
- HTTP 使用现有 prefix：`POST /sessions/{id}/import` 和 `GET /sessions/{id}/operations/{operation_id}`；提交返回 202 安全操作视图，已完成重放返回原视图，不新增任务。查询越权或不存在均私有 404。
- 通过 `LookupDigest(field_lookup_key).digest_domain('cloud-auth-import-v1', canonical_payload)` 计算带密钥指纹；payload 包含提供商、会话、模式、冻结配置修订、规范化令牌。原 `_fingerprint` 不接触秘密。另用提供商隔离域的 HMAC 标记已消费的导入令牌，重复使用已知受管授权时在刷新前拒绝。

- [ ] 写红测：无 AppKey 可开始导入；旧 own_app 仍拒绝缺配置；并发双模式只有一个 live session；重复 UUID 原样返回、不同令牌 409；5 次/分钟后第 6 次 429 + 正 Retry-After；两个模式创建限流合并。
- [ ] 运行新增 API 文件及 `tests/api/test_cloud_authorization_concurrency.py`，记录预期失败。
- [ ] 在 `PrivateAuthorizationRoute` 读取 JSON 前限制实际请求字节（含无 Content-Length、分块请求）最多 16384；此路由校验失败只输出固定字段白名单和中文错误，不回显 extra 字段名、loc 输入或厂商异常。保持 no-store 和 CSRF。
- [ ] 实现 `CloudImportService` 专属流程并由现有服务委托；在事务内加锁、验权、查重、限流、加密入队，禁止在事务中做厂商网络 I/O。get 查询不延长会话；成功授权只设置一次新的 10 分钟配置期限。
- [ ] 将连接器安装、注册、维护状态和运行器在线检查从自有应用检查中分离；内置模式仅跳过应用要求，不能跳过运行条件。新增 `test_import_without_app_still_requires_connector_and_runner`，断言离线时返回明确错误且零验证任务。
- [ ] 替换 service `_probe_attempt/_queue/_directory_result`、apply `_versions` 的无条件应用检查为任务 1 的来源检查。解绑会话从原候选推导来源；不得因页面新默认改变旧目标。
- [ ] 增加安全测试：非超管、只有追加权限、跨登录会话、撤权、CSRF、到期、取消、超大 body、恶意 extra 字段、错误日志和审计不含合成令牌；获取原操作不返回密文。运行 API authorization 全组和 `tests/services/test_cloud_auth_apply.py` 全绿。
- [ ] 独立审查本任务 diff 与证明，发布冻结 HTTP 契约供前端使用。

## Task 3 主机验证和不可重放的刷新检查点

**Files:** 创建 `scripts/cloud_auth/builtin_import.py`、`apps/api/app/operations/cloud_auth_import_worker.py`；修改 `provider_types.py`、`http_client.py`、`baidu.py`、`drive115.py`、`scripts/erp-cloud-authorize.py`、`cloud_auth_worker.py`、`cloud_auth_bridge.py`；新增 `apps/api/tests/cli/test_cloud_auth_builtin_import.py`，扩展 worker/bridge/runner 测试。

**Interfaces:**
- `get_import_identity(provider: AuthProvider, access_token: str) -> AccountIdentity` 使用现有固定官方身份接口，不构造假的有效期。
- `refresh_import(provider, refresh_token: str, *, now: datetime) -> ImportedTokenGrant` 只允许固定内置配置；115：`POST https://passportapi.115.com/open/refreshToken`；百度：`GET https://api.oplist.org/baiduyun/renewapi`，固定 `server_use=true`、`driver_txt=baiduyun_go`、`refresh_ui`。不得允许用户传 URL、跟随重定向或回退其他服务。
- 独立私有 `RefreshCheckpointRequest(action='refresh_checkpoint', fence, operation_id, phase, tokens?)`，不依赖 candidate 已存在。阶段只能 `not_started → sent → refreshed → verified`，或 `sent → unknown`；新版请求和结果带来源，旧格式只能通过已知版本路径读取。
- `verify_import` 操作顺序：身份读取 → 已知重复检查 → 持久化 sent → 一次刷新 → 持久化新令牌及刷新时间 → 新令牌身份一致核对 → verified → prepare。记录 `unknown` 必须禁止原凭据重放。

- [ ] 写红测：刷新前后账号一致；缺访问令牌/身份不一致/异常有效期失败；百度无 `expires_in` 明确 unknown，115 缺有效期失败；网络和错误正文永不回显；不允许任意 host/重定向/私有 DNS。
- [ ] 运行新增 CLI 文件、`tests/unit/operations/test_cloud_auth_bridge.py`，确认失败后实现固定协议适配，仍使用 64 KiB 响应上限和 TLS 校验。
- [ ] 为 claim、renew、complete、checkpoint 加来源及版本栅栏；持久刷新检查点与候选创建检查点分开。领取过期 verify_import lease 时：sent 无持久结果则终止为 `cloud_refresh_outcome_unknown`，refreshed 则只继续身份核对，verified 则只继续交接。
- [ ] 注入崩溃测试：发送前、发送后、响应后落库失败、落库后身份核对前、lease 丢失、完成回复丢失；重启后的外部 refresh 调用总数不超过 1。未知和终态清除不再需要的旧令牌，不能清除仍需核对的候选恢复资料。
- [ ] 固定关键回归名称与断言：`test_sent_refresh_is_not_replayed_after_lease_expiry` 断言 refresh 调用 1 次、状态 unknown；`test_persisted_refresh_resumes_identity_without_second_refresh` 断言只读新令牌核对身份；`test_baidu_unreported_expiry_never_changes_configuration_deadline` 断言 expires_at 为空但配置期严格 600 秒。
- [ ] 运行 `tests/cli/test_cloud_auth_builtin_import.py`、`tests/cli/test_cloud_auth_providers.py`、`tests/cli/test_erp_cloud_authorization.py`、`tests/services/test_cloud_auth_worker.py`、`tests/unit/operations/test_cloud_auth_bridge.py` 全绿，独立审查发送前持久化和单一刷新责任。

## Task 4 候选挂载和灾难恢复兼容

**Files:** 修改 `scripts/cloud_auth/candidate.py`、`openlist_admin.py`、`recovery.py`、`cloud_auth_bridge.py`、`cloud_auth_apply.py`；扩展 `apps/api/tests/cli/test_cloud_connector_candidates.py`、`test_cloud_connector_recovery.py`、`test_cloud_authorization_joint_recovery.py`、`tests/services/test_cloud_auth_apply.py`。

**Interfaces:** `CandidateOwnership`、私有 `OwnershipRecord` 和候选交接必须携带 `AuthSource` 与明确日志版本；`OpenListAdmin.prepare(operation_id, provider, application, tokens)` 增加 keyword-only `source`，现有 own_app 调用以显式适配器保留。解析缺来源的旧日志只能在已识别旧结构和旧会话来源一致时补 own_app，不能给任意新输入默认值。

- [ ] 写红测：百度内置候选设置 `use_online_api=true`、固定 `api_url_address`，无自有 client_id/secret；own_app 保持直接刷新；115 使用 Open API 驱动。传错来源、修订或候选 ID 时零外部变更。
- [ ] 运行候选测试确认红，再实现模式分流；只交接任务 3 持久化后的最新令牌，已交付重放仅 reconcile，绝不再次写入旧令牌。禁止从活跃 storage 抽取令牌用于新导入。
- [ ] 扩展恢复格式、解绑及清理：保存原模式、日志版本和冻结配置；冷快照包含加密必要资料，不带恢复主密钥；未知格式恢复关闭而不是猜测。旧目标及远端备份文件保持不变。
- [ ] 覆盖 pending/refreshed/unknown/已交付/已应用多种备份恢复状态，断言还原后不刷新旧链、不释放他人维护锁、不接管非 ERP 挂载；目录探针只删自己的随机文件。
- [ ] 先运行三个 CLI 测试文件；再在隔离 Docker fixture 中运行 `ERP_CLOUD_CONNECTOR_RECOVERY_TEST=1` 的 connector 恢复用例和 `ERP_CLOUD_JOINT_RECOVERY_TEST=1` 的 joint 恢复用例。二者串行、不得指向 NAS；若无法执行，记录明确发布阻断。
- [ ] 独立审查现有 own_app 和手动 WebDAV 不受影响、连接器接管后 ERP 不再刷新。

## Task 5 超管免填配置页面

**Files:** 创建 `apps/web/src/operations/CloudAuthorizationImportForm.tsx` 及 `.test.tsx`；修改 `OperationsCloudBackupPanel.tsx`、`CloudAuthorizationDialog.tsx`、`cloudAuthorizationApi.ts`、`operations.css` 及各自现有测试；扩展 `apps/web/tests/cloud-authorization.spec.ts`。

**Interfaces:**
- Dialog 增加 `mode?: AuthMode`（组件默认 own_app 保持旧调用），Panel 新绑定显式选择内置模式；既有账号显示真实来源，自有应用设置收进高级选项。
- `createCloudAuthorization(provider, operation_id, signal, mode?: AuthMode)`；新 `submitCloudAuthorizationImport(provider, session_id, input, signal): Promise<CloudImportOperationView>`、`getCloudImportOperation(provider, session_id, operation_id, signal)` 与任务 2 完全一致。
- `CloudAuthorizationImportForm` 负责同意说明、固定外链、两项 password 输入和验证动作；父 Dialog 持有 session 及操作 UUID，已验证后共用目录、探针、显式应用流程。导入不走 QR 轮询或缺应用阻断。

- [ ] 写红测：无应用信息可见并进入导入；页面明确外部授权、第三方处理凭据和百度续期依赖；未确认说明不能打开链接。新标签外链有 noopener/noreferrer 且不含 ERP 数据。
- [ ] 运行 `npm test -- src/operations/CloudAuthorizationImportForm.test.tsx` 确认红后实现组件；按钮使用“打开授权页面”“验证授权”，不伪称自动扫码或自动绑定。
- [ ] 稳定弹窗挂载位置，不能被 `provider-revision` 卡片 key 或 focus/refetch 重建。tab 切换保留输入；确认服务端接收即清空令牌；POST 网络不确定只查询原 UUID，不能自动重发或生成新 UUID。查无结果也提示状态未确认，用户明确重试才允许沿用原 UUID。
- [ ] 关闭、退出、401/403、账号/登录会话变更、取消、失败、到期清理 state/ref；失效请求不能重新写回。复用现有 abort/generation，但不把 AuthProvider 私有 ref 当公开 hook。通过 auth 生命周期卸载授权区，保证退出后同账号重新登录不复用表单。
- [ ] 补测试：恶意服务器错误不显示令牌；手机粘贴；切页返回保留；卡片刷新不丢；网络未知恢复不重复；旧模式仍展示二维码；百度未知有效期不会显示虚构倒计时；验收前自动复制仍停用。运行 `npm test -- src/operations --maxWorkers=4`、`npm run lint`、`npm run build` 全绿。
- [ ] 固定回归：`preserves pasted tokens across focus and target refresh` 断言同一 input 节点和值不变；`queries the original operation after a lost response` 断言 POST 1 次且查询同 UUID；`clears tokens before same-account re-login` 断言退出后原输入不可恢复。扩展 `cloudBackupApi.ts` 的目标来源类型和解析，未知模式拒绝伪显示。
- [ ] 桌面 1440 和手机 390 浏览器模拟流程通过；只用合成令牌，不记录截图、HAR、trace 或日志中的秘密。独立审查后与任务 2–4 联调。

## Task 6 集成验收和 NAS 发布

**Files:** 更新 `docs/operations/cloud-backups.md`、`docs/operations/encrypted-backup-recovery.md`、`scripts/db-schema-manifest.sh` 和受 head 影响的已有 CLI 测试；验收报告放主仓库 `outputs/verification/cloud-builtin-import-20261009/`。不为本任务修改业务无关模块。

- [ ] 核对规格每项与测试对应，运行完整云授权 API/服务/CLI/迁移组、全量前端测试、Ruff、mypy、lint、build、`git diff --check`。所有数据库过程串行或使用独立数据库；设置 `PYTHONPATH=.` 确保共享虚拟环境不导入旧发布源。
- [ ] 使用现有 `scripts/run-core-e2e.sh` 的 `E2E_CORE_MODE=core`（包括 cloud authorization specs）及隔离数据库验证桌面/手机。扩展用例同时验证实际 API 权限、网络恢复、输入清理及旧 own_app；模拟通过不能标记真实网盘可用。
- [ ] 对整合后的代码做独立最终审查；记录测试通过/跳过/失败数量。任何未解决的安全、恢复或版本来源问题都不进入部署。
- [ ] 本地检查全部适用项通过后，在主仓库运行：`scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3'`。不绕过锁、不直接复制、不临时手工重建容器；若发布源已有并行修改，先核对范围，不丢弃他人改动。
- [ ] 确认脚本退出 0、`.deployment-state` 为 complete、实际新迁移 head、API/Web/proxy/数据库健康、公网 TLS 正常、加密备份和隔离恢复成功。只读检查，不在生产造业务数据。
- [ ] 交付超管验收入口后，请用户在外部授权页登录，将两个令牌粘贴 ERP 并确认专用目录；不收集聊天中的密码/令牌，不代过验证码。百度和 115 分别验证身份、上传、完整回读、跨刷新再次上传、加密完整备份回读及恢复。
- [ ] 仅在每个提供商的真实验收完成后开放其自动复制；未完成的一律明确“待账号授权/待真实备份验收”。本地备份持续运行，不因云授权中断。

## 审阅要点

本期仍是“外部授权后首次导入”，不是纯扫码自动回传。新增的唯一协议兼容选择是百度有效期未知时如实记录、以身份和写读恢复验收把关。用户已确认计划及该修订，按上述依赖开展并行实施。
