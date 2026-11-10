# ReactionX 执行未知归档 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对已发出开始但本地未接受即崩溃的运行，提供不生成成绩的受控归档闭环。

**Architecture:** 原连接器用受保护的本地事实提交不可变异常声明；ERP 核对原运行和真实 STOP/OFF 后，允许有权限超管填写原因、两次确认并生成独立归档凭据。连接器验证、持久化该凭据为独立墓碑后才释放该条未处理配额，旧证据不删除。成绩包及 B1 契约保持不变。

**Tech Stack:** 现有 FastAPI/SQLAlchemy/PostgreSQL、React/TypeScript、Python SQLite/DPAPI 连接器；无新服务或依赖。

**Spec:** ../specs/2026-11-04-reactionx-uncertain-delivery-design.md（已确认）。

## Global Constraints

- 仅有效超级管理员，且仍有该人员/运行的处理权限，能看到并操作入口。
- 必须由原连接器对本轮原设备完成实际关灯，并让服务器记录确认；网页勾选或收到 HTTP 请求成功，均不能替代设备确认。
- 不生成成绩、不排名，保留原记录和审计；不能替代为“未执行”“取消成功”“成绩已保存”或补造空成绩。
- 原连接器收到并持久化服务器的匹配归档凭据后，才将该条从“未处理”配额中移出。
- 归档后不自动续跑、不自动开始新测试。矛盾的新证据保留并拒绝自动覆盖，另行人工核对。
- 两个仓库已有脏改动保留，不 stage/commit/push；ERP 仅用原 deploy-nas.sh、SSH202 发布，不能绕过备份迁移。
- Windows/实体灯验收仍未进行；所有八种正式模式继续关闭，模拟测试不等于正式开放。

## Review Focus

1. 同时到达的成绩提交和归档确认只能一种成功，不能既入榜又归档：D1 行锁并发回归。
2. 超管等待锁时会话自然到期、权限被撤销或原始证据改变：D1 最后写入前复核同一会话。
3. 丢失归档响应或电脑重启：D2 重取同一凭据、不可变墓碑及配额测试，不能重复归档或丢数据。
4. 本地有开始/刺激/结果，或伪造原始归属/关灯证据：D1/D2 拒绝，不能借归档绕过结果补传。
5. 登录切换或后台失权后仍显示旧页面：D3 清除私有缓存并停止操作；原始包不改归属。

## 共同契约（实施者技术选择）

- 不改 B1 ResultEnvelope/ResultReceipt、规则及 schema/fixture 摘要；独立命名，不复用上传许可。
- `UncertaintyDeclaration` 严格字段：`version:1,kind:'start_delivery_unknown',run_id,actor_account_id,subject_person_id,connector_id,manifest_digest,stop_operation_id,local_evidence_digest`。所有 ID 为规范 UUID，两个 digest 为64位小写hex，禁止额外字段。`declaration_digest` 为现有 canonical JSON SHA256。
- 本地 evidence_digest 由保护过的原 manifest、恢复证据(started=false)、原 STOP 及实际 OFF 事实规范化计算；不包含凭据，不声称硬件加密证明。声明固定后不变。
- `UncertaintyReceipt` 严格字段：`version:1,archive_id,run_id,connector_id,manifest_digest,declaration_digest,stop_operation_id,status:'execution_unknown',closed_at`。首份匹配凭据固定，后续不允许换 archive_id 或改任何字段。
- 独立运行状态 `uncertain_closed`，不在 ACTIVE_STATES，不存在 ResultReceipt/成绩/排名。原 Run 表增加 nullable JSONB `uncertainty_record`，包含不可变声明、声明摘要、时间、受控确认请求和最终归档凭据。沿用尚未发布的0080建表迁移，不增加新表或改旧生产行。
- 浏览器处理同时要求当前实际super_admin、reactionx.manage、对被测者的reactionx.conduct，保留显式拒绝；锁身份→connector→run，与 B3/B4 一致，等待锁后再次验证当前同一会话。
- 处理原因10..500字符，沿用审计用途校验。第一步发起请求产生UUID、绑定当前issuer/session/声明摘要、有效5分钟；第二步使用该request_id和literal true确认，不允许第一步直接归档。
- 连接器只用原未撤销凭据、原固定origin及原run提交/读取声明和凭据；原操作人失权/退出不赋予新控制权。声明仅是原始异常证据，不自动归档；最终归档仍由当前有效超管确认。

### Task 1: D1 后台声明、核验和非成绩归档

**Files:** 新建 `apps/api/app/reactionx/uncertainty.py`、`uncertainty_router.py`、`tests/api/test_reactionx_uncertainty.py`；修改 `models.py`、`results.py`、`recovery.py`、`app/api/router.py`、`migrations/versions/0080_reactionx_foundation.py`及`tests/db/test_migrations.py`。审计沿用已有operation_id/session_id等安全元数据，不为此放宽全局审计过滤。正式编辑前主控捕获精确路径快照。

**Interfaces:**
- `UncertaintyService.declare(connector, run_id, declaration)->UncertaintyStatus`，`status(connector, run_id, declaration_digest)->UncertaintyStatus`。
- `list_pending(actor,offset=0,limit=20)`；`request_archive(actor,run_id,declaration_digest,reason,confirmation)->ArchiveRequest`；`confirm_archive(actor,run_id,request_id,confirmation)->UncertaintyReceipt`。
- machine POST/GET `/api/v1/reactionx-client/runs/{run_id}/uncertainty`（GET要求declaration_digest），响应 `{state:'pending_review'|'archived',declaration_digest,receipt:null|UncertaintyReceipt}`；不存在404、不同声明409。
- browser GET `/api/v1/reactionx/uncertainties?offset=&limit=` 返回 `{items,total,offset,limit}`；行只含run_id、经权限过滤的subject_name、mode、created_at、declaration_digest、state，不下发旧凭据/设备地址/其他结果。
- browser POST `/api/v1/reactionx/runs/{run_id}/uncertainty/request` body `{declaration_digest,reason,confirmation:true}` 返回 `{request_id,run_id,declaration_digest,expires_at}`；POST同前缀`/confirm` body `{request_id,confirmation:true}` 返回Receipt。

- [ ] 写失败测试：真实配对/原run/已下发start/真实STOP_ACK建立声明；缺OFF、没有发出start、已有running ACK/结果/events、错误manifest/stop/凭据拒绝。相同声明幂等，不同声明冲突，不能伪装saved。
- [ ] 加权限/过期/二次确认测试，含等待锁后会话到期及另一会话不能消费；每次都重新检查候选事实。另加两个独立连接并发：归档与结果不能同时成功。
- [ ] 最小实现状态JSONB和两组独立路由，沿用当前严格用户/机器认证与请求体限制；仅原connector可声明/取回归档凭据，不开通其他业务路由。
- [ ] 最终归档检查真实原STOP完成及全设备OFF、原start确已deliver但无running ACK、无receipt/events，写状态和不可变凭据+审计同事务。结果提交和上传许可在同样锁内拒绝uncertain_closed，拒绝理由为需人工核对。
- [ ] 测试重复确认只返回原凭据，late结果不改写；迁移upgrade/downgrade和模型结构一致。按独占DB调度跑新API+关联B3/B4回归，Ruff/Mypy，报告冻结给D2/D3。

### Task 2: D2 本地受保护声明与归档凭据闭环

**Files:** R新建`src/connector_uncertainty.py`、`tests/test_connector_uncertainty.py`；最小修改`connector_store.py`、`connector_api.py`、`connector_service.py`及对应测试。C5迁移校验由其原owner在契约冻结后适配。

**Interfaces:** Store `prepare_uncertainty_declaration(run_id,stop_operation_id,server_ack)->dict`，`pending_uncertainties(limit=20)->list[dict]`，`ack_uncertainty(run_id,receipt)->None`；Api `declare_uncertainty(run_id,declaration)` / `uncertainty_status(run_id,declaration_digest)`；Service在真实OFF及原STOP_ACK为awaiting_sync后进入这一流程，不改B1终包。

- [ ] 先测真实C3重启场景：recovered_unstarted且受保护started=false、无accepted_start/events/intents/outbox/receipt、原STOP已接受、全原设备OFF；缺任意事实拒绝声明。
- [ ] 声明及最终墓碑以独立protected SQLite表/用途名保存，原scope不变；第一份声明与凭据固定。真实server_ack必须原operation的completed/awaiting_sync，不能改成cancelled。
- [ ] API只允许固定原站点TLS、规范路径/摘要及严格状态DTO。断网/失权保留，使用既有有界网络调度，停止不受请求阻塞；不添加控制命令、不重放start。
- [ ] 服务器pending只显示“待超管核对”，归档凭据真正落盘后才显示“执行状态未知，需复测”；全局/本scope配额仅排除经完整保护验证的归档墓碑。不能把明文状态或receipt字符串当证据。
- [ ] 丢响应/重启/重复凭据/不同凭据/保护失败/满盘/原scope错误/新结果矛盾各有实际持久化测试；原records和events/commands不清除，另一个run不受影响。
- [ ] 原C6未知窗口测试改为完整真实接口两次确认→重取凭据→落盘→配额释放，并保留未批准时仍pending的反例。其原owner负责ERP两测试文件，独占DB安排。
- [ ] 源码冻结后跑wholeR显式B1依赖，新增status对C5只允许经同样墓碑验证的零未处理计数，独立复核。

### Task 3: D3 超管异常处理界面与最终核验

**Files:** 新建`apps/web/src/reactionx/UncertaintyPanel.tsx`及测试；最小修改`api.ts`、`contracts.ts`、`ReactionxPage.tsx`及相关测试；不改其他角色菜单。

**Interfaces:** 消费D1严格DTO，使用既有QueryClient、CSRF、私有错误清理、A/B主题和移动布局。仅页面有manage+conduct时请求候选列表，服务端仍为最终授权边界。

- [ ] 测试只对有权限超管显示“执行未知待核对”，显示run/人员/原始事实，不显示分数或排行；缺设备确认服务器拒绝时显示中文原因，不提供绕过按钮。
- [ ] 填原因→第一次请求→独立二次确认对话框→提交，提交中禁重复；退出/换账号/403清私有缓存，过期/证据变化要求重来，不能沿用旧request_id。
- [ ] 成功只显示“执行状态未知，需复测”，不说“成绩已保存”；后台已归档与连接器是否收到凭据分开显示，不假定本地队列同步完成。
- [ ] 跑focused Vitest/type/lint/build及隔离实际浏览器流程，不造真实人员数据。补验收文档，列出代码/模拟/NAS/原生与实体设备不同状态。
- [ ] 主控执行最终相关后端/迁移、完整前端、wholeR、实际跨服务和旧天赋回归；独立全任务复核后，按`NAS_SSH_PORT=202 scripts/deploy-nas.sh --source "$PWD"`发布并只读核验。失败是阻断，不绕过脚本。

## 自检与执行

用户已确认设计及继续实施；沿用当前子任务实施/独立复核方式。所有共同DTO在D1实现前按此冻结，若实际代码需要改变，先主控协调消费者，不能分别猜测。D1/D2未完成前D3可做代码准备，但真实接口验收串行。新功能开发使先前全套测试仅为基线，最终重新验证适用部分；未完成时不宣称发布完成。
