# 自助注册凭据撤销与监护邀请恢复安全加固设计

- **日期：** 2026-08-16
- **状态：** 本地实现、完整门禁与独立复审已完成
- **范围：** 自助注册最终质量/安全复审的两个 Important
- **上游规格：** `2026-08-14-self-service-registration-design.md`

## 1. 背景与目标

自助注册的完整本地门禁已经覆盖常规密码找回、注册会话撤销、监护邀请 handoff、接受后刷新恢复和四视口主流程，但最终质量/安全复审发现两个未被既有门禁覆盖的边界：

1. 密码重置只撤销事务当时可见的注册会话，没有使旧注册验证码失效，也没有与验证码签发、注册会话签发使用同一组锁。并发事务可以在密码重置提交后留下新的活动注册会话。
2. 新监护邀请的 handoff 或 context 失败时，页面会读取浏览器中旧 flow cookie 的 acceptance receipt。旧邀请已经接受时，页面可能把新邀请误报为“监护关系已建立”，同时新 token 已从地址栏清除。

本单元目标是把密码重置定义为完整的注册凭据撤销边界，并让新邀请失败恢复只能关联本次 handoff。完成后仍只声明本地自助注册安全加固通过，不扩展支付、真实短信/邮件、第三方实名、Docker/NAS 或生产能力。

## 2. 已确认规则

### 2.1 密码重置终止未完成注册

- 有效账号完成密码重置时，系统终止与该账号任一已验证登录手机号或邮箱相匹配的全部未完成注册申请。
- 终止后的申请进入现有 `expired` 状态，并调用统一的申请秘密清理函数；临时密码摘要、实名密文和临时联系方式密文不得保留。
- 对应的所有活动注册会话立即撤销。
- 对应的所有未消费、未过期、用途为 `registration` 的验证码立即失效；保留挑战行用于限流和最小历史，不把失效误记成成功消费。
- 已经 `finalized`、`rejected` 或 `expired` 的申请不改写业务终态。
- 密码重置验证码本身仍在同一事务成功消费；任何撤销、密码更新或审计失败都回滚整个事务。
- 重置后的用户若要继续注册，必须重新发起一份新申请。已有账号仍按现有规则引导登录或找回密码。

### 2.2 撤销范围

- 撤销范围来自账号当前全部 `AccountLoginIdentifier`，不只限于本次找回所用标识。
- 每个登录标识必须与人员表中对应的加密手机号或邮箱重新计算并核对；缺失、损坏或摘要不一致时，密码重置不得产生部分成功。
- 未验证邮箱、辅助手机号和不属于该账号的注册申请不进入撤销范围。
- 不存在账号的 decoy 密码重置继续只消费 decoy challenge，不修改人员、账号、申请、会话或审计。

### 2.3 新监护邀请失败恢复

- 页面发现 URL fragment token 后立即从地址栏移除，继续禁止 token 进入 DOM、localStorage、sessionStorage、错误文本或日志。
- 移除后的 token 只保留在当前 React 页面实例的内存中，直到 handoff 成功；刷新页面会丢失该内存值，这是主动避免持久化邀请凭据的安全边界。
- 新 token 的 handoff 请求失败时，页面不得调用 acceptance receipt，因为当前 Cookie 仍可能属于旧邀请。
- handoff 失败时显示可重试操作；重试使用内存中的同一 token。成功后立即清除内存 token，再读取本次 flow 的 context。
- 本次 handoff 已成功但 context 响应丢失时，可以读取 acceptance receipt；此时 flow cookie 已由本次 handoff 替换，回执与本次邀请同源。
- 普通刷新没有新 fragment 时，继续允许读取旧 flow cookie 的 acceptance receipt，以保留“接受已提交但响应丢失”的恢复能力。
- 页面卸载或刷新后若既没有 fragment，也没有有效 context/receipt，只显示通用邀请不可用提示，不提供可能泄露旧邀请状态的细节。

## 3. 事务与锁顺序

### 3.1 统一的标识锁

注册验证码签发、注册会话签发和正式开通已经使用两个与注册标识摘要有关的 advisory-lock domain：

- `identifier`：验证码签发与正式认领冲突保护；
- `registration-application-identifier`：注册申请验证、合并和会话签发保护。

密码重置必须为账号全部已验证联系方式派生这两个 domain 的锁键，去重后按有符号整数升序一次性获取。不得在持有注册申请、人员或账号行锁后再补取较小的 advisory lock。

### 3.2 正式开通锁序校正

现有正式开通先锁申请行，再取得 claim advisory locks，与注册验证和本次撤销需要的锁序相反。加固时把正式开通改为：

1. 非锁定读取申请的标识和身份证摘要；
2. 取得排序后的 claim advisory locks；
3. `FOR UPDATE` 锁定申请行；
4. 重新核对申请 ID、状态、有效期、标识摘要和身份证摘要；
5. 再执行人员、账号、登录标识和角色写入。

摘要或状态在步骤 1–4 之间变化时返回现有私有不可用结果，不继续使用旧快照。

### 3.3 密码重置锁序

密码重置 challenge 由 `consume_with()` 先锁定。成功回调内按以下顺序处理：

1. 非锁定读取账号、人员联系方式和登录标识，计算候选注册标识摘要；
2. 取得所有 `identifier` 与 `registration-application-identifier` advisory locks；
3. 按 UUID 顺序锁定范围内未终结注册申请；
4. 锁定人员、账号和该账号全部登录标识；
5. 重新解密并计算标识摘要，要求与步骤 1 的集合完全一致；
6. 把锁定的未完成申请置为 `expired` 并清除申请秘密；
7. 使范围内旧 registration challenge 失效，撤销范围内全部注册会话和账号正式会话；
8. 更新密码及锁定计数，追加脱敏审计。

步骤 5 不一致时整个事务回滚，密码重置 challenge 保持未消费，调用方收到现有通用失败，不暴露联系人变化原因。首版不做事务内自动重试，避免隐藏未知并发写入。

### 3.4 并发结果

- 注册会话签发先完成：密码重置等待同一标识锁，随后能看到并撤销迟到会话。
- 密码重置先完成：旧申请已经过期且旧 challenge 已失效，等待中的验证不能签发会话。
- 正式开通先完成：密码重置在锁后重新读取终态，只撤销凭据并更新正式账号，不改写 finalized 申请。
- 密码重置先完成：正式开通重新读取时看到 expired，返回私有不可用且不创建半成品人员或账号。
- 任一冲突或失败后，调用者 Session 无需额外 rollback 即可继续执行只读查询。

### 3.5 审核候选集合的细粒度写入屏障

- 审核驳回不得使用 `people` 表级锁。它只锁当前候选人员行（UUID 升序），再取得当前申请身份证摘要与已验证手机号或邮箱摘要对应的事务 advisory 锁（数值升序），最后以非锁定查询重新计算候选 ID 集合。
- 所有会创建人员或改变 `Person.identity_digest`、`phone_digest`、`email_digest` 的正式写路径必须参与同一协议。已有人员的路径先锁人员行，再锁旧值与新值的摘要键后写入；新建人员没有既有人员行，先锁新摘要键再插入。
- 申请正式开通在已有候选人员行锁之后取得同一摘要键，并在写入人员、账号、角色或审计前重新比较候选 ID 集合。集合漂移进入现有待审核冲突路径，不留下半成品正式数据。
- 摘要键按字段域分隔（身份证、手机号、邮箱），并由摘要再哈希为 PostgreSQL 有符号 64 位 advisory key。碰撞只会额外串行化极少数无关写入，不会错误合并人员或泄露摘要。
- 审核持有摘要键期间，无关人员的读取、登录和不相交人员写入必须继续可用；同一候选集合的写入按先提交者线性化。原始 SQL 或未来直接写 `people` 的生产路径必须先接入该协议。

## 4. 组件边界

### 4.1 后端

- `registration/verification_service.py` 提供域键派生、排序取锁和“失效 registration challenge + 撤销 session”的事务内方法，不自行提交。
- `registration/finalization_service.py` 只调整 claim lock 与申请行锁顺序，并在锁后重新验证快照。
- `auth/service.py` 负责收集账号已验证标识、调用注册凭据撤销、终止申请、更新密码和追加审计。
- 继续复用 `clear_registration_application_secrets()`；不新增 challenge 状态列，不新增 Alembic 迁移。

旧 registration challenge 通过把 `expires_at` 收紧到本次重置时间来失效，`consumed_at` 保持 `NULL`。这样既不伪造成功消费，也保留既有限流统计。

### 4.2 Web

- `GuardianInvitationPage` 把加载结果区分为 `handoff_failed`、`context_failed` 和普通无 fragment 恢复。
- 一个 ref 保存尚未成功 handoff 的 token；另一个 ref 继续串行化 StrictMode effect replay。
- handoff 失败渲染“重试确认邀请”按钮；按钮不显示 token，也不把 token 放入组件状态、查询缓存或浏览器存储。
- acceptance 操作自身响应丢失时仍可读取 receipt，这条路径与首次页面加载的 handoff 失败严格分离。

## 5. 错误与隐私

- 密码重置公开响应继续不区分账号不存在、账号损坏、并发联系人变化或撤销失败。
- 监护邀请 handoff/context/receipt 继续使用现有私有错误，不展示邀请 UUID、token、账号 UUID、完整联系方式或内部原因。
- 安全审计只记录账号、动作、结果和 request ID，不记录验证码、邀请 token、完整手机号/邮箱或申请秘密。
- 失败 handoff 不清除服务端旧 cookie；安全性由“不得读取旧 receipt”保证，避免新增无必要的 cookie 清理接口。

## 6. 测试与验收

### 6.1 后端 RED/GREEN

- 服务测试：密码重置使全部匹配未完成申请变为 expired，清除秘密，撤销 session，并使旧 registration challenge 到期。
- 多标识测试：手机号找回同时终止同账号邮箱对应的未完成申请；未验证邮箱和其他账号不受影响。
- 回滚测试：审计失败、标识集合变化或秘密清理失败时，密码、申请、challenge、session 和 password-reset challenge 全部回滚。
- PostgreSQL 受控并发：
  - 会话签发先持锁、重置后提交，最终活动注册 session 为 0；
  - 重置先持锁、旧 challenge 后验证，验证失败且 session 为 0；
  - 正式开通与重置双向竞态不死锁、不留半成品，两个 Session 均可继续查询。

### 6.2 Web RED/GREEN

- 邀请 A 已接受并保留旧 cookie；打开邀请 B，B handoff 网络失败时不得调用 receipt，不得显示成功。
- B token 已从 URL 清除但保留在内存；点击重试后 handoff 与 context 成功，展示 B 的脱敏信息。
- B handoff 成功、context 响应失败时，已登录账号可以恢复 B 的 acceptance receipt。
- 无 fragment 的普通刷新继续恢复同账号旧 receipt。
- StrictMode 仍只执行一次 handoff；DOM、storage、错误文本和请求缓存不含 token。

### 6.3 完整门禁

- 聚焦后端服务、API 与 PostgreSQL 并发测试；
- `GuardianInvitationPage`、registration API 和路由测试；
- API 全量 pytest，数据库/并发测试不得因缺少 `TEST_DATABASE_URL` 跳过；
- Web 全量 Vitest、Ruff、mypy、ESLint、生产 build；
- 自助注册四视口主流程与隐私流程 8/8；
- `git diff --check`；
- 原规格审查员与质量/安全审查员复审均为 PASS，且无 Critical/Important。

## 7. 完成边界

只有第 6 节全部通过，才能恢复“自助注册本地实现与完整门禁已完成”的结论。此结论不表示主分支已合并，也不表示真实短信/邮件、第三方实名、项目选择、订单支付、Docker/NAS、备份恢复或生产部署已经完成。
