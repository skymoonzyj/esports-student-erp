# ERP 云备份扫码授权实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让超管使用自有开发者应用，在 ERP 内扫码绑定百度或 115，并在新账号验证通过后安全切换备份目标。

**Architecture:** 新增短期授权状态机和独立主机运行器，经私有标准输入桥接调用 ERP，不向 API 容器开放 Docker socket。官方授权所得令牌交付 ERP 管理的独立 OpenList 候选挂载，复用现有 WebDAV 上传与回读流程；确认后才切换配置引用。连接器恢复资料纳入现有加密备份，保留手动配置和旧备份兼容性。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy、PostgreSQL、Alembic、现有 EnvelopeCipher、React 19、TypeScript、Vitest、Playwright、OpenList 4.2.6、Linux Docker 与 cron。

**Spec:** [已确认规格](../specs/2026-10-06-cloud-backup-qr-authorization-design.md)，2026-10-06 确认。用户随后确认按本计划分工开发并要求开始；未勾选项目仍表示待完成工作，实际进度记录在本计划专用 SDD ledger。

## Global Constraints

- 沿用实际 `super_admin` 角色与全局 `operations.monitor.read` 双重校验。
- 使用用户自有百度和 115 开发者应用，不借用第三方应用标识，不经公共令牌中转服务。
- 等待扫码阶段的有效期取厂商有效期与 ERP 十分钟上限的较小值；授权成功后另有十分钟配置期限，不因轮询续期。
- 每个提供商同时仅允许一个未结束的 ERP 授权会话。会话绑定发起账号和实际 ERP 登录会话 UUID。
- 扫码成功、连接测试通过、已有可恢复云备份是不同状态，不得合并显示。
- ERP 不与连接器并发刷新同一令牌；交付候选挂载后由连接器单独刷新。失效授权停止该目标新上传，不影响其他目标和本地备份。
- 夸克保留现有实验性手动入口，本期不开放扫码。保留现有 WebDAV 手动配置、本地完整备份和云端历史文件。
- 不改每日北京时间 03:00 本地备份或两分钟云复制扫描逻辑，不改变加密归档格式版本和完成标记规则；新增可选的受保护恢复内容。
- 不依赖 fnOS，不公开连接器管理端口；网页不得配置任意 URL、命令或主机文件路径。不在聊天、参数、日志、审计或浏览器持久存储中放机密。
- 当前源为 `/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3`，分支 `codex/sound-preview-nas`，核对时 HEAD 为 `ae67a5fe464943024d77767cbc4930d320dd1138`。保留全部已部署未提交改动；沿用前项云备份计划的 no commits、no reset、no cleanup 边界，不自动提交。
- 数据库测试只允许一个进程，必须使用可重建的本地测试库；真实 NAS 不运行会清空 schema 的测试。
- 产品代码完成且本地检查通过后必须运行主仓库 `scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/team-roles-divisions/new project3'`。失败阻止宣布上线，不旁路部署。

## Review Focus

- 扫码后退出登录或超管权限被撤销：异步结果不能继续创建可用授权或切换配置，前端不能重新展示迟到二维码。对应任务 2、4、7。
- 旧复制任务数据库租约已过期，但主机仍在上传：切换不能只看任务表，必须获得主机复制锁证明实际传输已结束。对应任务 6。
- 连接器创建成功后运行器宕机，且令牌已刷新：恢复流程应识别已创建挂载，不再次创建或写回旧令牌。对应任务 5、6。
- 网盘目录名含中文、空格、百分号、编码后的路径穿越，或服务返回重定向：正常名字可用，越界和非预期跳转拒绝。对应任务 3、5、7。
- 备份恰逢挂载切换或令牌刷新，随后迁移到新主机：数据库引用和连接器快照必须一致，旧格式备份仍可恢复。对应任务 8、9。

## 当前代码与分工

现有 `CloudBackupService` 只管理 `test/copy` 任务，按提供商加锁并校验目标 revision；没有授权应用、扫码会话、候选挂载、配置历史或切换冻结状态。`cloud_bridge.py` 是 64 KiB 有界的私有 stdin/stdout RPC，可沿用其安全边界，但新增授权桥接必须与原复制协议分开。

文件职责按以下任务归属。任务 1 先确定共享类型和迁移；随后 API、官方适配、连接器适配、UI 可依接口并行。任务 4 的桥接与任务 6 的切换服务由同一后端负责人整合；任务 8 的备份及安装脚本由运维负责人独占。共享文件 `operations/router.py`、`cloud_service.py`、迁移清单和部署入口由主代理串行合入，避免多人改写。

每个实现任务都执行 RED → GREEN → 独立审查；RED 必须来自预期新增行为尚未实现，依赖缺失等环境故障不算。不得把模拟授权作为厂商验收。任务末只记录改动与验证证据，不提交 Git。

## 共享契约

以下均为新增契约，不宣称现有代码已具备这些接口。

- `AuthProvider = Literal["baidu", "115"]`；原 `Provider` 仍含 `quark`，两者不得混用。
- `AuthState = creating | awaiting_confirmation | authorized | configuring | test_pending | ready | applying | applied | expired | cancelled | failed`。只有 `applied/expired/cancelled/failed` 是终态。
- `AuthActor(account_id: UUID, login_session_id: UUID)`；`AuthFence(session_id: UUID, session_revision: int, app_revision: int, target_revision: int, lease_id: UUID)`；授权、目录、测试和切换的异步完成同时校验 fence 与重新加载的权限。到期/撤权后的安全清理是独立系统任务，仅依据 journal 精确 ownership 和有效清理租约，不因原操作者失去权限而留下凭据。
- `CloudAuthSessionView`：`id, provider, state, revision, expires_at, account_label?, selected_directory?, error_code?, next_poll_after_seconds`。仅专用二维码端点返回 `QrPresentation(kind: "image" | "payload", content: str, official_open_url?: str, expires_at)`；其中 content 只能是有界 PNG data URI 或白名单校验后的官方授权内容，不能包含长期凭据。
- `CloudAppView`：`provider, client_id, secret_present, revision, configured, prerequisites[]`；`CloudAppUpdate`：`client_id, client_secret?: SecretStr, clear_secret: bool, expected_revision`。空密钥保留，清除与输入非空密钥不可同时提交；115 PKCE 不要求 AppSecret。
- 运行器独占类型 `DeviceGrant(secret_payload: dict, qr: QrPresentation, expires_at, interval_seconds)`、`TokenGrant(access_token: str, refresh_token: str, expires_at)`、`PollResult(status: pending | authorized | denied | expired | throttled, retry_after_seconds?, tokens?: TokenGrant)`。不得导出到 HTTP 响应。
- 连接器契约 `CandidateRef(operation_id: UUID, storage_id: str, webdav_user_id: str, mount_path: str, account_fingerprint: str)` 与 `CandidateAccess(ref: CandidateRef, username: str, password: str)`；后者只允许私有桥接。账号原始标识及凭据加密保存，HTTP 只显示脱敏标签。
- 探测结果 `ProbeEvidence(operation_id: UUID, candidate_id: UUID, config_revision: int, directory: str, sha256: str, verified_at)` 必须绑定该候选和目录；不写 `last_backup_id/last_success_at`。
- 私有授权 RPC 最大请求和响应均为 64 KiB、字段禁止额外键、日志禁止原始错误。授权动作仅 `heartbeat/claim/renew/complete`；另有仅供备份的 `begin_snapshot/end_snapshot`，只接收登记的 connector UUID 和快照操作 UUID。`complete` 使用判别联合，禁止任意 URL、文件路径、SQL 或连接器管理命令。

## Task 1 持久状态机与迁移

**Files:** 新建 `apps/api/app/operations/cloud_auth_models.py`、`cloud_auth_schemas.py`、`cloud_auth_state.py`、`apps/api/migrations/versions/0063_cloud_authorization.py`；修改 `cloud_models.py`、`apps/api/migrations/env.py`、`scripts/db-schema-manifest.sh` 及既有 migration/head 测试。新建 `apps/api/tests/unit/operations/test_cloud_auth_state.py`。

**Interfaces:** 产出共享契约；`transition(current: AuthState, event: str, *, now: datetime, expires_at: datetime) -> AuthState`；`aad(provider: AuthProvider, record_id: UUID, field: str) -> str`。调用现有 `EnvelopeCipher.encrypt/decrypt(..., field_name=aad(...))`。

- [ ] 编写 RED 测试，固定时间验证 599 秒有效、600 秒拒绝、扫码授权后的配置期限重新计时、终态不能恢复、跨记录密文不能解密。例如：

  ```python
  def test_exact_deadline_rejects_apply():
      with pytest.raises(AuthExpired):
          transition("ready", "apply", now=deadline, expires_at=deadline)
  ```

- [ ] 执行 `"$PY" -m pytest tests/unit/operations/test_cloud_auth_state.py -q`，确认因新增契约缺失失败。
- [ ] 增加六张表：`cloud_auth_applications`、`cloud_auth_sessions`、`cloud_auth_candidates`、`cloud_auth_operations`、`cloud_backup_target_history`、`cloud_connector_state`。应用保存 client_id、密钥密文、revision；会话保存 owner 两个 UUID、截止时间、应用/目标版本、短期密文；候选保存 ERP ownership UUID、挂载引用、探测证据；操作保存幂等键、动作、租约 UUID、lease_expires_at、重试和清理状态；历史保存加密旧配置及原复制证据；connector state 保存实例 UUID、维护操作 UUID 和维护租约截止，不保存管理员凭据。目标表加 `authorization_status`、`managed_candidate_id` 和 `switch_operation_id`；不重置任何既有目标。
- [ ] 为每提供商一个非终态会话、操作幂等键和领取到期时间加约束/索引；操作键复用但参数不同返回冲突。同一次重放先匹配 owner 和参数，再返回原结果，不重复扣限流额度。加模型 metadata 注册；确认当前唯一 head 仍为 `0062_cloud_backups` 后连接 `0063_cloud_authorization`，若已前进则停止冲突合入并重新编号。
- [ ] 在 `tests/db/test_migrations.py` 增加升级保留旧目标和任务、空新表降级、约束一致性测试；更新 `tests/cli/test_operations_docs.py`、`test_local_container_acceptance_launcher.py` 的 head/表集合。执行新 unit 与上述 db/cli 测试，预期全过；仅在一次性库测试降级，绝不在线降级。

## Task 2 超管 API 与会话边界

**Files:** 新建 `apps/api/app/operations/cloud_auth_service.py`、`cloud_auth_router.py`、`cloud_auth_security.py`、`cloud_auth_errors.py`，修改 `operations/router.py`、`audit/service.py` 的安全错误白名单。新建 `tests/api/test_cloud_authorization.py`、`test_cloud_authorization_concurrency.py`。

**Interfaces:** `CloudAuthorizationService(db: Session, *, cipher: EnvelopeCipher)`；方法 `save_application(provider: AuthProvider, actor: AuthActor, input: CloudAppUpdate) -> CloudAppView`、`create_session(provider: AuthProvider, actor: AuthActor, operation_key: UUID) -> CloudAuthSessionView`、`get_session(session_id: UUID, actor: AuthActor) -> CloudAuthSessionView`、`cancel_session(session_id: UUID, actor: AuthActor, expected_revision: int) -> CloudAuthSessionView`；`require_auth_actor(db, actor: AuthActor) -> None` 每次重新加载账户、Person、AuthSession 及实际角色权限。

- [ ] 编写失败测试：普通管理员和仅获追加权限者均 403；跨登录会话访问 404；无 CSRF 拒绝；同操作者同 key 重放返回同 ID；并发创建只有一个成功；密钥写入后所有响应、审计和错误中均不存在原值。
- [ ] 执行 `"$PY" -m pytest tests/api/test_cloud_authorization.py tests/api/test_cloud_authorization_concurrency.py -q` 确认 RED，使用独占的一次性 PostgreSQL。
- [ ] 在 `/api/v1/operations/cloud-backups/{provider}/authorization` 下提供 GET/PUT `/application`、POST `/sessions`、POST `/unbind`；会话子路径 `/sessions/{id}` 下提供 GET 本身、GET `/qr`、GET/POST `/directories`、POST `/cancel`、`/select-directory`、`/test`、`/apply`。POST directories 登记 parent 和 operation UUID 后返回 202；GET directories 按 operation UUID 读取该 owner 的结果，返回 `{operation_id,status,entries,error_code}`，pending 时 202、ready 时 200。目录、测试、应用和解绑仅登记任务，不在 HTTP 中等待厂商或连接器。变更请求均带 expected revision；创建/目录/选择/测试/应用/解绑另带 operation UUID。响应不暴露令牌。
- [ ] 创建限流采用数据库计数：每操作者每提供商 3 次/分钟、10 次/小时；返回 429 与 `Retry-After`。只允许百度/115。保留现有标准账户、双重权限和 no-store；二维码单独读取且不出现在列表。安全错误统一中文；缺连接器、自有应用或运行器时明确返回前置条件，不生成假二维码。
- [ ] 加撤权/注销发生在排队后、回调前的测试；保留历史审计但取消后续外部动作。目录和二维码仅 owner session 可读取；明确拒绝强制改密、已删除或停用账户。运行新增 API、现有 `tests/api/test_cloud_backups.py` 与 `test_auth_sessions.py`，预期通过且旧手动操作不变。

## Task 3 官方设备授权适配

**Files:** 新建 `scripts/cloud_auth/provider_types.py`、`http_client.py`、`baidu.py`、`drive115.py`、`__init__.py`；新建 `apps/api/tests/cli/test_cloud_auth_providers.py`。模块只负责官方协议，不访问 ERP 数据库或创建挂载。

**Interfaces:** 两个适配器均实现 `start(client_id: str, client_secret: str | None, now: datetime) -> DeviceGrant` 和 `poll(grant: DeviceGrant, client_id: str, client_secret: str | None, now: datetime) -> PollResult`。HTTP 客户端沿用标准库，固定白名单域名/路径，禁用 ambient proxy 和重定向，TLS 必须验证，单次响应不超过 64 KiB。

- [ ] 写 mocked HTTP RED：百度等待确认/慢轮询/拒绝/过期，115 URL-safe PKCE challenge、设备码交换与拒绝，非法二维码内容、私网 URL、302、过大响应、超时和供应商响应包含令牌时不泄漏。`test_poll_pending_has_no_tokens` 断言 `status == "pending" and tokens is None`；`test_115_challenge_is_urlsafe` 使用包含 `+ /` 的 Base64 对照样本。
- [ ] 运行 `"$PY" -m pytest tests/cli/test_cloud_auth_providers.py -q`，确认 RED。
- [ ] 依据规格链接的官方接口实现流程；115 自有 AppID 的 PKCE 不强行索要 AppSecret。连接超时 5 秒、普通读取 15 秒、115 长轮询最多 35 秒；限制接口、响应字段和二维码编码长度。二维码内容由安全代理/本地编码产生，拒绝任意 HTML/SVG。厂商 interval 与 ERP 最小 2 秒取较大值，限流遵守 Retry-After；不实现第二套长期刷新器。
- [ ] 将厂商终态授权错误映射为重新授权，将临时网络失败映射为有界退避，均不回传原文。绑定官方协议资料的来源及核对日期，记录仍需自有应用验证的协议差异，不借用公开 AppID 做测试。
- [ ] 运行 provider 测试，并在工作树根目录运行 `ruff check scripts/cloud_auth apps/api/tests/cli/test_cloud_auth_providers.py`，预期全部通过；没有自有应用时仅可记录“协议模拟通过”。

## Task 4 私有桥接与短轮询运行器

**Files:** 新建 `apps/api/app/operations/cloud_auth_bridge.py`、`cloud_auth_worker.py`，新建 `scripts/erp-cloud-authorize.py`；新建 `tests/unit/operations/test_cloud_auth_bridge.py`、`tests/cli/test_erp_cloud_authorization.py`。不得改变原复制 bridge 的动作语义。

**Interfaces:** `claim_auth_operation(db, *, now) -> AuthWorkItem | None`、`renew_auth_operation(db, fence, *, now) -> bool`、`complete_auth_operation(db, fence, result, *, now) -> bool`。`AuthWorkItem` 包含 action、AuthFence、该动作所需的最小解密材料；action 为 create_qr、poll、prepare、list_directories、select_directory、probe、apply、cleanup、unbind。`AuthWorkResult` 为同 action 的严格判别联合，由任务 1 schemas 定义。

- [ ] 写 RED：超 64 KiB/额外字段在打开数据库前拒绝；过期租约重新领取生成不同 lease_id；旧 fence 完成失败且不改变新状态；关闭浏览器不会产生第二条厂商轮询；注销后完成拒绝并安排清理。
- [ ] 执行新 bridge unit 与 runner CLI 测试确认 RED。
- [ ] 实现 90 秒租约、最多每 30 秒续约，数据库提交后才做外部请求；每一轮先恢复未决 journal 再领取下一动作。进程使用固定 Docker argv 与 stdin，不在 argv 放 secrets。前向业务动作在完成时验证状态/期限/权限/版本；成功领到 TokenGrant 后加密登记候选准备动作。失联、取消、过期和撤权产生独立系统清理任务，仍验证清理租约与精确 ownership，不依赖已失效的用户会话，也不清理真实云端历史。
- [ ] 主机 `run(config, *, deadline_seconds: int = 55)` 使用非阻塞独占 `.cloud-auth-runner.lock`，每秒检查到期任务，官方请求间隔由记录控制；到运行截止不领取新任务，正在执行的有界请求允许收尾。每分钟启动一次的专用 wrapper 提供连续短轮询，与原两分钟复制任务分开，不写无意义心跳日志。持有部署共享锁只覆盖一次有界操作，不跨整个驻留周期。
- [ ] 为正常轮询、运行器重启、迟到 response、租约替换、429 退避及权限撤销运行测试；只保存安全 worker 心跳 `cloud_authorization`，离线超过 2 分钟在前置条件中提示，不假装正在等待手机确认。

## Task 5 受管候选挂载和目录验证

**Files:** 新建 `scripts/cloud_auth/openlist_admin.py`、`candidate.py`，扩展 `cloud_auth_worker.py` 对应动作；新建 `tests/cli/test_cloud_connector_candidates.py` 和 `tests/services/test_cloud_auth_candidates.py`。

**Interfaces:** `OpenListAdmin` 提供 `reconcile(operation_id: UUID) -> CandidateRef | None`、`prepare(operation_id, provider, application, tokens: TokenGrant) -> CandidateAccess`、`list_directories(ref, parent: str) -> list[DirectoryEntry]`、`select_directory(ref, directory: str) -> CandidateAccess`、`cleanup(ref) -> None`。`DirectoryEntry` 只有逻辑 path、显示名、是否可选；`probe_candidate(access, directory, operation_id) -> ProbeEvidence` 复用 `Transport`，不把管理接口加入 WebDAV 类。

- [ ] 编写 RED：先持久 ownership 再创建；服务端创建成功后本地超时可查询找回，不能重复创建；已有挂载令牌被刷新后 reconcile 不写旧令牌；scope 外 GET/PUT/MKCOL 均拒绝；清理仅删除完全匹配 ownership 的挂载/用户与本次探测文件。
- [ ] 运行候选 CLI/service 测试确认 RED；本任务 service 测试同样遵守单 DB 进程。
- [ ] 使用固定 OpenList 4.2.6 管理接口，只针对登记的私有实例与 ERP 命名空间创建百度/115 Open 挂载和独立 WebDAV 用户。先获得可靠账号标识再展示脱敏标签；无法确认身份则失败。目录操作经过一次规范化与编码，拒绝点段、控制字符、编码穿越；测试中文、空格和字面百分号，禁止把目录字符串当 URL。
- [ ] 临时授权挂载在专用 WebDAV 用户就绪前禁止匿名/游客读取。目录确认后按固定目录限制凭据、设置 Native proxy 并进行越界反向测试。若该版本不能满足权限或刷新要求，保持“待验证/不支持”并阻止启用，不能改成管理员传输凭据。
- [ ] 探测使用随机 64 字节，文件名及候选/目录/操作归属先持久记录，PUT→GET→比对→删除；内容不符保留失败状态。删除失败登记清理重试且不覆盖最初业务错误。仅在交付被 journal 确认后擦除 ERP 临时 TokenGrant；未知交付状态先 reconcile，绝不重发旧令牌。
- [ ] 使用隔离 OpenList 验证候选隔离、权限和崩溃恢复，运行新测试及原 `tests/cli/test_erp_cloud_backup.py`。真实厂商上传留到任务 9；本地模拟不能宣布平台已可用。

## Task 6 配置切换与旧任务排空

**Files:** 新建 `apps/api/app/operations/cloud_auth_apply.py`；修改 `cloud_service.py`、`cloud_auth_worker.py`、`scripts/erp-cloud-authorize.py`、`cloud_status.py`；新建 `tests/services/test_cloud_auth_apply.py`，扩展原 cloud API/CLI/status 测试。

**Interfaces:** `request_apply(db, actor: AuthActor, session_id: UUID, expected_revision: int, operation_key: UUID) -> CloudAuthSessionView`、`commit_apply(db, fence: AuthFence, candidate: CandidateRef, evidence: ProbeEvidence, host_drain_receipt: UUID) -> bool`、`request_unbind(db, actor: AuthActor, provider: AuthProvider, expected_target_revision: int, operation_key: UUID) -> CloudAuthSessionView`。解绑创建 owner-bound、直接进入 applying 的操作会话，不生成二维码。drain_receipt 只由持锁主机私有 RPC 提交，绝不接受 HTTP 输入；持锁期间提交才有效。

- [ ] 写 RED：旧任务标 failed 但外部传输仍持锁时，目标、历史和新任务选择全部保持不变；确认重复提交只增加一次 revision/历史；测试后换目录、应用密钥或目标 revision 导致切换拒绝。
- [ ] 执行 apply service 与 cloud runner 测试确认 RED。
- [ ] 沿用 provider advisory lock 顺序，确认后登记 `switch_operation_id`，所有 save/enqueue/sync 检查它以冻结该目标新工作；claim 只允许冻结前已存在、旧 revision 的任务继续排空，不接受新任务。既有 queued/running 任务先排空；不得将租约超时视为传输已停止。确认必须发生在配置截止前；接受后转换为独立的 applying 截止时间，最多等待 10 分钟。超时撤销冻结、保留旧目标，返回明确“目标任务未结束，请重试”错误，不静默切换。
- [ ] 主机最终切换按 `.cloud-backup.lock` 独占 → `.backup.lock` 共享 → `.deploy.lock` 共享 → `.cloud-connector.lock` 独占顺序取得非阻塞锁；任一失败即释放并退避。取得主机锁、核实候选真实状态后，重新授权校验并在短事务内保存旧配置历史、切换目标引用、增加 revision、清除旧当前证据和冻结。网络请求不能发生在该短事务中；令牌只由候选挂载继续刷新。
- [ ] 新配置默认未启用自动复制，但保留本次绑定到准确目录的连接测试证据，超管可按现有流程开启。原账号的历史备份依然可查但不作为新账号成功证据；不删除原挂载或远程文件。取消或失败只撤销本操作冻结。解绑也通过排空和明确确认，禁用目标并移除对应受管凭据；不冒称已完成厂商撤销。
- [ ] 授权终态失效将目标标为 `reauthorization_required`，停止新上传，保留历史，其他目标不受影响。运行并发 API/service、auth status 与原 cloud tests，覆盖权限在等待排空期间撤销、崩溃前后重复确认、手动配置不被自动接管。

## Task 7 超管扫码页面

**Files:** 新建 `apps/web/src/operations/cloudAuthorizationApi.ts`、`CloudAuthorizationDialog.tsx`、`CloudApplicationSettings.tsx` 与对应 `.test.tsx`；修改 `OperationsCloudBackupPanel.tsx`、`cloudBackupApi.ts`、`operations.css`；新建 `apps/web/tests/cloud-authorization.spec.ts`。

**Interfaces:** TypeScript 类型逐字段对应任务 1/2；所有客户端方法使用现有 `apiFetch`、`AbortSignal` 与 `cache: "no-store"`。`CloudAuthorizationDialog({provider, onClose, onApplied})` 的 QR/会话/草稿仅在组件内；`CloudApplicationSettings({provider, onSaved})` 的密码只写入不读取。

- [ ] 写组件 RED：百度要求必要密钥，115 不显示必填 AppSecret；未安装连接器显示原因；等待/过期/拒绝/429/待测试/待应用分开；扫码完成不出现“已备份”；失去权限或账号切换后迟到响应不能重新打开二维码。
- [ ] 执行 `npm test -- src/operations/CloudAuthorizationDialog.test.tsx src/operations/CloudApplicationSettings.test.tsx` 确认 RED。
- [ ] 实现应用设置 → 创建二维码 → 确认账号 → 选目录 → 写读验证 → 对比旧配置并明确应用；提供取消、重新获取、明确清除密钥和解除绑定确认，解释解绑不会删除云备份及必要的官方撤销步骤。轮询只读 ERP 状态，遵守返回间隔；隐藏页面暂停 UI 轮询，重新聚焦读取当前状态而非重建授权。403/401/终态立即停止重试。组件卸载、身份或权限改变时 abort 全部请求并递增本地 generation，防止迟到写回。
- [ ] 复用已有 `qrcode.react` 展示经服务端校验的内容，或使用受控 PNG data URI；官方手机直达链接仅在返回可用 URL 时显示，明确需要对应网盘 App。不得将 QR、会话材料或应用密钥放 React Query、local/sessionStorage、URL query、错误上报。保留原手动配置和夸克提示。
- [ ] 写 Playwright 测试覆盖 desktop-1440、mobile-360、touch-mobile-390，目录中文和长名不撑破布局，按钮可键盘操作，拒绝/取消/刷新和切换账号不残留二维码。只用虚构授权响应；真实授权验收关闭 trace、截图、video，不保留可用二维码。
- [ ] 运行新组件、既有 `OperationsCloudBackupPanel.test.tsx`、前端 API/认证相关测试及浏览器用例；执行 `npm run lint`、`npm run build`，预期全过。

## Task 8 可移植安装及连接器恢复

**Files:** 新建 `scripts/erp-cloud-connector-install.py`、`scripts/cloud_auth/recovery.py`；修改 `scripts/erp-backup-install.py`、`erp-backup.py`、`infra/cloud-backup/compose.yaml`、必要的 `scripts/deploy-nas.sh` 安装衔接；新建 `tests/cli/test_cloud_connector_recovery.py`、`test_cloud_authorization_install.py`，扩展 `test_erp_backup.py`、`test_erp_backup_docker.py` 与 `test_erp_cloud_install.py`；更新 `docs/operations/cloud-backups.md`、`encrypted-backup-recovery.md`。

**Interfaces:** `snapshot_connector(registry: Path, destination: Path) -> ConnectorSnapshotManifest`、`verify_connector_snapshot(payload: Path, manifest: ConnectorSnapshotManifest) -> None`。私有桥接增加 `begin_snapshot(connector_id: UUID, operation_id: UUID) -> SnapshotFence`、`end_snapshot(fence: SnapshotFence) -> bool`，SnapshotFence 含实例/操作 UUID、revision 和维护租约截止。manifest 含 version、镜像不可变标识、受管对象 registry 和文件清单；不含明文 secret。新增备份可选子树 `recovery/cloud-connector/`，保留 `format_version=1` 和现有归档二件套。

- [ ] 写 RED：旧备份无 connector 子树仍通过；新备份引用的挂载能在隔离实例恢复；快照过程中令牌刷新/切换被阻断；symlink/路径越界拒绝；安装失败不破坏旧 connector/cron；恢复主密钥从不进入 payload。
- [ ] 运行新 recovery/install 与原 backup CLI 测试确认 RED。
- [ ] 实现显式管理模式安装：仅管理登记为 ERP 专用的 OpenList 4.2.6，数据目录 0700、secret/registry 0600、wrapper 0700、固定回环 origin；未知既有实例不接管。管理员凭据由受保护文件接入，不从命令参数或网页读取。安装器通过受测部署流程调用，不让按钮安装软件、不静默把原外置连接器升级为受管实例。部署衔接须确认主仓库发布入口和源快照实际执行的是同一已测安装契约；若不一致，先解决该发布阻塞，不能手工 Docker 绕过。
- [ ] 增加独立每分钟 auth wrapper，保留原 daily/two-minute cron 和配置。wrapper 校验 `.active-release`、`.deployment-state`、路径范围，每次有界操作读取当前发布，不让驻留进程永久持部署锁。无管理实例时保持可部署的“待配置”，并明确不具备扫码能力。
- [ ] 备份受管连接器时，遵循任务 6 的锁顺序，但 `.backup.lock` 使用独占；以 90 秒维护租约设置数据库 gate，所有 ERP 挂载/目标写入在锁内重新检查该 gate。候选管理操作也取得 `.cloud-connector.lock`，不得持有它再逆序取前级锁。在受控停止连接器期间获取 ERP 导出的 DB snapshot 和完整 connector 数据/config 快照，不得热拷贝仍在刷新的 SQLite/WAL。暂停窗口上限 30 秒，超时或失败在 finally 恢复原运行状态并释放 gate，备份失败而非降级成无连接器的成功。连接器重启后才继续耗时 pg_dump/镜像归档，但保持同一 DB snapshot。进程宕机后由主机恢复逻辑先检查连接器和锁，再清理过期维护状态；不能只因租约到期就宣称服务恢复。
- [ ] 将恢复文件和所需固定镜像纳入现有 inventory、加密和磁盘峰值预检；安全提取仍只进新目录。隔离恢复使用不对外监听且 `--network none` 的连接器实例，仅验证结构、挂载引用和凭据可恢复，不刷新真实令牌或触达网盘；完成后删除测试资源。实际新主机联网验证为任务 9 的显式验收。
- [ ] 运行原备份认证、序列和 Docker 恢复测试及新 connector 演练；演练必须明确启用，skip 不可算完成。确认连接器停止/重启、维护 gate 清除和所有锁失败路径均有回归。

## Task 9 集成审查和发布验收

**Files:** 更新操作文档并在 `outputs/verification/cloud-authorization/` 保存不含机密的验证记录。主代理负责全部共享文件审查、发布源核实与实际 NAS 发布，其他人不得同时部署。

**Interfaces:** 消费任务 1–8 的完整功能；产出按提供商划分的“自动化通过 / NAS 已部署 / 应用待配置 / 真实授权通过 / 可恢复副本已验证”状态，不能合并为单一“全部完成”。

- [ ] 逐条比对已确认规格与五项 Review Focus，独立审查公共 API、私有桥接、锁顺序、账号切换和恢复路径。已有 issue 必须带回归测试，P0/P1/P2 未解决不发布。
- [ ] 运行下节本地门禁，记录各命令真实结果、失败与有理由的 opt-in skip。检查新增迁移只有一个 head，未更改旧备份/财务/天赋业务；确保待部署快照包含新文件且无 secrets。
- [ ] 从主仓库执行强制 NAS 部署脚本。预检查、迁移备份、构建、健康检查或隔离恢复失败则停止并报告，不宣布发布成功。保留可回滚源/镜像，不删除 PostgreSQL volume，不自动下迁移。
- [ ] 只读确认实际 release、Alembic head、前后端资源一致、主机 auth/backup wrapper 状态和 HTTPS 18080 页面。普通管理员看不到设置，超管在尚未配置时看到准确提示。未经用户填写应用与扫码，不创建厂商授权、不上传真实备份。
- [ ] 用户通过受保护后台填写自有应用并亲自确认扫码后，分别验证百度与 115：账号身份、目录写读、拒绝越界、真实令牌刷新后的再次上传、完整加密包回读认证、隔离恢复和失败告警。专用测试目录只清理本次探测文件；不删任何历史云备份。真实令牌刷新不可通过仅模拟时间称为验证完成。
- [ ] 对已有业务目标，最终配置应用和自动复制由超管明确确认；无自有应用或待审核平台保持“待配置/待验证”，交付时报告这一阻塞。代码可先发布，超管可通过明确标注“待验证”的验收入口完成真实授权和单次备份；未完成验证不得开放自动复制或声称已经解除独立备份告警。

## 本地验证命令与执行约束

以下是实施阶段命令，本次编写计划没有执行这些产品测试。先设置本机 `PY='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'`（带空格调用时使用 `"$PY"`），并确认解释器仍为 Python 3.12。各 API/CLI 测试命令工作目录是源工作树 `apps/api`；pytest 的 `TEST_DATABASE_URL` 只取已配置、经安全检查的本地一次性库，不复制生产 URL。缺少测试库而被跳过的 DB 测试必须补跑。

```sh
"$PY" -m pytest tests/unit/operations/test_cloud_auth_state.py tests/unit/operations/test_cloud_auth_bridge.py tests/cli/test_cloud_auth_providers.py tests/cli/test_erp_cloud_authorization.py tests/cli/test_cloud_connector_candidates.py tests/cli/test_cloud_connector_recovery.py tests/cli/test_cloud_authorization_install.py -q
"$PY" -m pytest tests/api/test_cloud_authorization.py tests/api/test_cloud_authorization_concurrency.py tests/services/test_cloud_auth_candidates.py tests/services/test_cloud_auth_apply.py tests/api/test_cloud_backups.py tests/api/test_auth_sessions.py tests/db/test_migrations.py -q
"$PY" -m pytest tests/cli/test_erp_backup.py tests/cli/test_erp_backup_sequences.py tests/cli/test_erp_cloud_backup.py tests/cli/test_erp_cloud_install.py tests/cli/test_operations_docs.py tests/cli/test_local_container_acceptance_launcher.py -q
ERP_BACKUP_DOCKER_TEST=1 "$PY" -m pytest tests/cli/test_erp_backup_docker.py -q
```

在源工作树执行 `bash scripts/check.sh` 作为最终完整门禁（先停止其他 DB 测试；脚本会负责独立环境），同时检查新增 `scripts/cloud_auth/` 是否纳入 Ruff、类型与 CLI 验证范围，未覆盖部分补充到门禁。前端工作目录 `apps/web`：

```sh
npm test -- --maxWorkers=4
npm run lint
npm run build
npm run test:e2e -- tests/cloud-authorization.spec.ts --project=desktop-1440 --project=mobile-360 --project=touch-mobile-390
```

浏览器用例只连接隔离本地 ERP 与模拟供应商，不把 NAS 当测试数据库。未来真实验收需关闭凭据页面截图、trace 和视频；只记录脱敏时间、结果、版本和校验摘要。

## 自审与执行建议

规格覆盖映射：页面流程→任务 2/7，状态/过期→1/4，权限→2/6/7，私有服务/网络→3/4/5，凭据/切换→1/5/6，迁移与恢复→8，验收与正式开放→9。任务 1 的契约供后续任务统一消费，不在各任务另造字段名。

采用已确认的子任务分工、逐项独立审查：后端、连接器运行器、前端在契约确定后并行，恢复/部署与共享文件合入由主代理协调，数据库测试串行。实现和通过验证的代码发布已获用户授权；实际网盘账号仍由用户填写自有应用并亲自扫码，不自动授权，不提交代码。
