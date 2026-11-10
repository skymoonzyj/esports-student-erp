# ReactionX Windows 连接器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 复用现有九模式 BLE 引擎，实现可由 ERP 配对和调度、逐事件持久化、可本地停止且可靠补传的 Windows 连接器。

**Architecture:** 旧 Tk 默认程序保留；连接器独立入口复用可注入设备运行器。BLE、网络与单写入存储分别调度，命令和事件先持久化，最终成绩只在匹配服务端 receipt 后确认保存。所有网络主动出站 HTTPS，无公开或回环 TCP 控制端口。

**Tech Stack:** 保留当前 Python 3.14 x64、bleak 3.0.2、unittest、PyInstaller；系统集成优先标准库 ctypes/winreg，HTTP 使用标准库 http.client + ssl。不为了接入更换训练引擎语言。

**Spec:** [已确认规格](../specs/2026-10-31-reactionx-erp-integration-design.md)；协议、限额、八模式规则和共同验收以 [主计划](2026-10-31-reactionx-erp-integration.md) 的 B1 为准。

## Global Constraints

- 参考仓库 `https://github.com/skymoonzyj/reactionx-windows`，读取基线 `e9b593119760067b98c60bf67b6b428a94a1f7d2`。`/tmp/erp-reactionx-reference.s6dFVZ/source` 只是只读参考，不在临时 clone 中开展交付开发。
- 执行时按 using-git-worktrees 建立该仓库持久隔离工作区，核验来源与基线差异后再改；不自动推送或更改私有可见性。以下路径均相对该 Windows 仓库。
- 旧 `%LOCALAPPDATA%/ReactionX-Windows/reactionx.sqlite3`、成员与历史不删除、不自动导入 ERP；新连接器使用独立 `%LOCALAPPDATA%/ReactionX-Connector` 数据目录。
- 手动控制只存训练记录，不参与公共排名；其他八模式只在固定规则完整结束且验证状态允许时参榜。
- 不静默安装、不默认开机自启、不强制更新；更新必须等待当前训练结束/停止且保护待同步队列。
- 设备协议分辨率为 10 ms，不宣称实际精度或无误差；不能可靠归属回执时必须标无效、需复测，不能补出漂亮成绩。
- 凭据/队列按当前 Windows 用户保护；不在 URI、明文日志、截图或测试文件放真实 token/个人资料。DPAPI 不是对同用户恶意软件的防护。

## Review Focus

1. 原 Tk 与连接器同时启动、设备编号重复：Windows 互斥与 opaque device key 防止抢灯/误归属，C1/C5 验证。
2. 迟到通知进入下一次激活、重连后的旧通知：无法消歧时不计成绩，C1/C6 验证。
3. 磁盘满、SQLite 忙或中途断电：先落盘再确认，失败停止刺激且不丢弃待 ACK 包，C3/C4 验证。
4. 网络请求卡住但用户点本地停止：BLE/停止不受网络线程阻塞，C4 验证。
5. Windows 用户/站点更换、安装路径含空格和中文：凭据不跨用户解密、旧包不改归属、URI 不变成任意执行，C2/C5 验证。

---

## 文件结构与接口责任

- `src/device_runtime.py`：从 main 抽出 Backend，注入 transport/engine，不产生窗口或日志初始化副作用。
- `src/activation_tracker.py`：设备连接代次、运行、激活标识与通知资格。
- `src/connector_protocol.py`：消费 B1 v1 fixture、严格解析、canonical digest，无设备副作用。
- `src/connector_windows.py`：DPAPI、进程互斥、同用户命名管道与托盘封装，可注入 fake 供非 Windows 测试。
- `src/connector_store.py`：独立 SQLite 单写入线程、命令去重、事件、终包与 receipt。
- `src/connector_api.py`：固定站点 HTTPS；`src/connector_service.py`：运行状态和恢复；`src/connector_entry.py`：启动/URI/UI 生命周期。
- `src/connector_installer.py`、`build-connector.ps1`、`uninstall-connector.ps1`：用户确认安装、独立构建与卸载；旧 setup/build 默认行为不被连接器替换。
- `tests/fakes.py` 与 `tests/test_connector_*.py`：fake 设备/时钟/存储/ERP；`contracts/v1` 固定复制 B1 契约并记录 digest，不能自己改字段。

## 验证命令约定

当前仓库使用 unittest，不是 pytest。Windows 从仓库运行 `& .\tools\venv\Scripts\python.exe -m unittest discover -s tests -p 'test_connector_*.py' -v`；旧回归运行 `& .\tools\venv\Scripts\python.exe -m unittest discover -s tests -v`。只在依赖已具备的隔离环境运行；Mac 可以跑纯逻辑 fake 测试，但不能将 Windows API 测试的 skip 记作通过。`tests/gui_smoke.py` 与 `tests/gui_true_false.py` 要单独验收。

### Task 1: C1 可中断设备运行器和回执隔离

**Files:** 新建 `device_runtime.py`、`activation_tracker.py`、`tests/test_connector_runtime.py`；修改 `main.py`、`training.py`、`transport.py`，复用现有 `tests/test_core.py`、`test_true_false.py`、`test_scan.py`。

**Interfaces:** `DeviceRuntime(emit, transport_factory, engine_factory)`，提供 `scan/connect/identify/start(manifest)/manual(device_key)/stop(reason)/disconnect`；`ActivationTracker.accept(connection_epoch,run_id,device_key,event)->AcceptedEvent|AmbiguousEvent`。旧 `Engine(transport,emit)` 入口保留，正式 manifest 走受控任务调度，自由桌面配置不被暗中改成正式计分配置。

- [ ] 写失败测试：identify 的延迟 finally 不能熄灭新训练；休息/倒计时立即响应 disconnect/stop；同灯号不同设备不合并；旧运行/旧连接事件丢弃；不带可靠试次标识且落在歧义区的事件标 invalid。同步固定完整组、基地完整目标/回基地循环、真假 15/15 角色记录必须可从事件重算。
- [ ] 跑 `test_connector_runtime.py` 先失败，再最小抽取 Backend；用明确代次、激活状态、复位/排空流程和测试事件调度实现互斥及中断，不能把主机收到时间当硬件试次 ID 的证明。
- [ ] 完成逐事件 emit 与稳定 device key，补上 stim/group/cycle/end 元信息；在灯激活前记录意图、设备写完成后记录状态。写失败/断线保留原因；停止失败不能吞掉。重启恢复后先将旧运行标中断，重新连接设备后完成复位/熄灯确认才可准备新运行，不能只因本地锁空闲就认为灯状态安全。
- [ ] 跑新测试及全部旧 unittest、两项 GUI smoke。真实设备无法证明的隔离条件交 C6，模式保持 unverified；不得凭 fake 用例解除限制。

### Task 2: C2 协议解析与系统保护

**Files:** 新建 `connector_protocol.py`、`connector_windows.py`、`tests/test_connector_protocol.py`、`tests/test_connector_security.py`、`contracts/v1`。

**Interfaces:** 消费 B1 模型；`parse_command(raw: bytes)->CommandEnvelope`、`canonical_digest(value)->str`、`CredentialStore.save/load/delete(origin,actor_id,connector_id)`、`UserProtector.protect/unprotect(bytes)->bytes`、`HardwareLease.acquire()->bool/release()`。DPAPI 保护当前用户，不设置 LOCAL_MACHINE；失败不回退明文。

- [ ] 写失败测试：B1 fixture digest 一致；非法 URI、双重编码、未知查询参数、NaN、超大请求、错误协议版本、重复灯/序号拒绝。fake 系统保护失败则不写 token；其他用户/其他站点不能解开当前身份数据。
- [ ] 跑新 unittest 先失败，实现严格白名单与 Windows API 包装。按 [Microsoft DPAPI 文档](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata) 管理缓冲区与错误，禁用交互式 DPAPI prompt，不依赖将被废弃的 prompt 流程。
- [ ] 密钥是本地安全随机 32 字节，server 登记摘要；本地 DPAPI blob 与敏感队列仅限当前用户目录 ACL。日志仅输出安全错误码和必要 run ID，不输出输入 URI/challenge/secret 或原始异常网络 body。
- [ ] 纯解析/保护 fake 测试全部通过；Windows 实机另跑真实 DPAPI 回环、跨用户拒绝、互斥竞争，结果单独记录，不用平台 skip 冒充。

### Task 3: C3 崩溃安全事件与待同步队列

**Files:** 新建 `connector_store.py`、`tests/test_connector_store.py`。

**Interfaces:** `ConnectorStore.accept_command(envelope)->CommandState`、`append_event(run_id,seq,event)`、`finish_run(envelope)->OutboxItem`、`pending_outbox(limit)->list[OutboxItem]`、`ack_outbox(run_id,receipt)`、`recover_interrupted()->list[RunId]`。一个存储线程拥有 SQLite 连接；`finish_run` 在一个事务内写终态与不可变 outbox。

- [ ] 写失败测试：接受 command 后、亮灯后、最后事件后、finish 后、server commit 后丢 ACK 各阶段强制中断；重启不自动 start，事件按序保留，同包不重复、不同包拒绝。磁盘满/保护失败/事务 rollback 不返回成功；中断记录不能拼接成完整记录。
- [ ] 跑 `test_connector_store.py` 先失败，实现命令/事件唯一约束、WAL 与明确同步策略、DPAPI blob、不可变 digest 和 receipt 校验；每次刺激前的状态持久化失败即通知停止，不继续训练。
- [ ] 限制队列总量 1 GiB 或 1,000 个未确认 run（先到者）；ACK 后事件保留 7 天再清理，未 ACK 达 30 天转隔离待核验，禁止自动删除/覆盖，阻止新增测试并提示处理。重新授权只能处理相同 origin/actor/run；新账号不接管。卸载默认保留该数据目录。
- [ ] 跑 store 测试包含独立进程 kill/reopen、重复 ACK、错误 result/run/digest、序号冲突及配额边界，全部通过后核对旧 `reactionx.sqlite3` 未被访问或改写。

### Task 4: C4 出站控制循环与可靠补传

**Files:** 新建 `connector_api.py`、`connector_service.py`、`tests/test_connector_api.py`、`tests/test_connector_service.py`。

**Interfaces:** `ConnectorApi.poll(cursor)->PollView`、`ack(operation_id,ack)`、`submit(envelope)->ResultReceipt`、`receipt(run_id)->ResultReceipt|None`、`heartbeat(status)`；`ConnectorService(runtime,store,api,credentials,clock).run()/stop_local(reason)`。消耗 B1/B3/B4 契约，不调用 ERP 人员通用接口。

- [ ] 写失败测试：过期/replayed 命令、停止覆盖排队启动、网络线程永远挂起仍能本地停止、浏览器 lease 过期、系统时钟回拨、跨 actor/manifest、断网重启不自动续跑。补传只发同一不可变包；读取到匹配 receipt 才本地标 ACK。
- [ ] 跑两项 unittest 先失败，实现活动 500 ms/空闲 2 s 轮询、5 s 网络超时、30 s 退避上限和 15 s 单调租约。独立网络线程使用验证证书的 `http.client.HTTPSConnection`，固定 origin，拒绝重定向/HTTP 降级和任意代理注入；连接可复用，错误后重建。[Python HTTPSConnection 文档](https://docs.python.org/3.14/library/http.client.html#http.client.HTTPSConnection)
- [ ] 通信错误停止新刺激并尝试熄灯；设备 write/close 也有有界等待，不阻塞本地退出。offline/saving/saved/stopped-unconfirmed 对应真实状态。权限撤销留存隔离数据，超管恢复许可仅对原 envelope 生效，不修改历史归属。
- [ ] 跑 API/service 全回归并用隔离假 ERP 验证一次完整链路，断网后只有保存补传，没有新的 start；无任何监听 TCP 端口。

### Task 5: C5 托盘、URI、安装与升级

**Files:** 新建 `connector_entry.py`、`connector_installer.py`、`build-connector.ps1`、`uninstall-connector.ps1`、`tests/test_connector_entry.py`、`tests/test_connector_install_manifest.py`，扩展 windows 包装与 README/使用说明。

**Interfaces:** `parse_activation_uri(uri)->ActivationRequest`、`ConnectorEntry.main(argv)->int`、`TrayController(on_stop,on_pause,on_exit)`、`Installer.install(bundle,target)->InstallReceipt`。连接器单实例用当前用户 ACL 的命名管道转交激活，不开本地网络端口；URI 只能唤起/配对，不自动开始。

- [ ] 写失败测试：含空格/中文路径、恶意引号与命令注入、取消配对、已有运行时重复启动、旧 Tk 抢锁、正在运行时升级拒绝、旧队列在升级/卸载后保留。默认注册仅 HKCU 用户级协议，不设置开机启动或自动更改防火墙/证书。
- [ ] 跑入口/安装 manifest 单测先失败；实现明确确认的安装窗口，将构建包安装到用户级稳定路径，校验 bundle manifest 后再切换入口。失败回滚旧二进制/注册信息但不回滚业务数据；运行中拒绝替换，等待用户停止退出。卸载清注册与程序文件，待同步目录默认保留并显示所在位置。
- [ ] 新 `build-connector.ps1` 先跑 unittest，再独立生成连接器及安装包；旧 `build.ps1` 仍生成旧预览桌面版。生成版本、来源提交、SHA-256、签名状态清单；不把私有仓库 token 打包。没有可信发布者签名只标内部测试包，正式分发门禁保持未通过，不指导关闭系统防护。
- [ ] Windows 11 Chrome/Edge 实测安装、拒绝唤起、已装未运行、配对两端取消/确认、托盘停止、注销、升级、卸载。原桌面模式仍能单独运行，不能和连接器同时占灯。

### Task 6: C6 六灯、八榜和故障验收

**Files:** 新建 `docs/erp-connector-acceptance.md`，补 `tests/test_connector_formal_modes.py`；所有模式验证状态在 ERP B1/B6 配合记录。

**Interfaces:** 输出每模式 `mode,rule_version,connector_version,protocol,device_count,result,limitations` 验收条目；不能用布尔“全部通过”掩盖未测或不支持版本。

- [ ] 写正式 manifest 的 fake 回归：八种结束规则/排序与 B1 fixture一致，manual 保存但 rank reason=practice_only；100 次随机故障序列不跨 run/activation，不重复完成；先运行失败再修足本计划实现缺口。
- [ ] 真实单灯→六灯逐模式执行：固定布局/传感/协议、响应与组/循环计时、重复/迟到通知、相同编号、倒计时/休息断线、熄灯失败、强退进程、磁盘满、网络切换；每项记录事实和不足，不能伪造精度或宣称硬件同步。
- [ ] 与 ERP 隔离环境完整连测：超管为已激活与未激活档案各测一次，确认服务器保存/匿名排名/前三差距；撤权与重复补传用合成账号。经用户确认再进行真实数据试测，不主动给线上人员制造成绩。
- [ ] 仅已验证模式允许超管试测中的有效排名；未验证模式保留明确关闭状态。提交可验安装包路径、digest、实际签名状态与步骤给 B6；正式签名或真实设备缺失时保留阻断项，不推送未获授权的 GitHub Release。

## 交付边界

此计划不把扫描到六灯当成六灯训练通过，不把 PyInstaller 成功当作安装/签名完成，不把本地落库当作 ERP 已保存。完成状态由原生安装、设备训练、服务端 ACK 与受限发布分别证明。
