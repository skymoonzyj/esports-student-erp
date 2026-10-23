# ERP 自动备份与恢复

本功能由 `scripts/deploy-nas.sh` 安装到 NAS。部署成功后先执行一次加密全量备份和隔离恢复演练，随后每天中国时间 03:00 自动执行同样的流程。原有迁移前数据库备份继续保留；新流程覆盖数据库之外的恢复材料。

## 备份内容与保护

- PostgreSQL custom-format 数据库备份。通过 `pg_export_snapshot()` 固定同一个只读事务快照，记录每张业务表的行数、表结构、索引、约束、序列定义和 Alembic 版本；恢复比较不受备份期间新增业务数据影响。
- 序列位置不属于 PostgreSQL 的事务快照。恢复演练从实际 `database.dump` 的 `SEQUENCE SET` 条目读取 `last_value` 和 `is_called`，逐一核对隔离库中的序列，不能用导出前后的线上序列采样代替。备份期间正常取号不会因这个比较而误报；证据缺失、重复、无效或恢复位置回退会阻止验证成功。校验不会调用线上 `nextval` 或 `setval`。
- 评价原始文件、预览文件与隔离区目录。数据库快照引用的可用原文件逐个核对长度及 SHA-256，预览逐个检查存在；归档内所有文件另有完整 SHA-256 清单。原文件在复制期间被移除或损坏时，本次备份失败，不会误报为成功。
- 当时生效的运行配置、实际挂载的数据库及应用密钥、原 `.env`/`secrets`、Compose 配置、TLS 证书及私钥、发布版本标记和源代码快照。
- API、调度器、PostgreSQL、前端、代理、Caddy 的实际 Docker 镜像，按镜像 ID 去重保存。恢复材料不依赖 NAS 上旧镜像仍存在，也不依赖当时的软件包索引仍能下载。跨架构迁移需要另外构建匹配架构镜像。

归档采用 OpenSSL AES-256-CBC + PBKDF2（200000 次）加密，并使用独立派生的 HMAC-SHA256 认证密文；解密前同时校验 SHA-256 与 HMAC。备份主密钥为独立随机文件，不是 ERP 字段加密密钥，且不能存进备份目录。恢复用的业务密钥只存在于加密归档内部。日志和运维状态均不输出密钥、个人资料或数据库连接串。

NAS 默认路径：

| 内容 | 路径 |
| --- | --- |
| 加密备份 | `/home/skymoonzyj/esports-erp/backups/full`，权限 0700 |
| 独立备份主密钥 | `/home/skymoonzyj/.config/esports-erp/backup.key`，权限 0600 |
| 脱敏运行状态 | `/home/skymoonzyj/esports-erp/operations/backup-status.json` |
| 定时设置 | `/home/skymoonzyj/esports-erp/backup-tools/config.json` |
| 定时执行日志 | `/home/skymoonzyj/esports-erp/backup-tools/backup.log` |

每个成功备份目录包含 `archive.tar.gz.enc` 和 `complete.json`。只有加密归档恢复演练成功后，该目录才原子发布到正式备份列表。归档和目录同步落盘后才标记成功。临时明文使用私有目录，正常成功或异常退出均清理；如果机器突然掉电/进程被强杀，应由管理员检查并清理遗留 `.work-*`，不要把它们当成成功备份。

保留最近 14 个有备份的日期、8 个周和 6 个月的代表备份，合并去重，并至少保留最新两份有效备份。只在新备份和恢复验证成功后清理旧备份。损坏或无法认证的目录不会占用保留名额，也不会被自动删除。备份和部署互斥，重叠定时任务不会启动第二个备份。

## 运维状态与第二份副本

运维监控读取脱敏 JSON：当前运行/成功/失败状态、本次开始及结束时间、上次成功、上次恢复验证、备份大小、固定错误码、文件/表验证数量和第二份副本状态。失败保留上次成功时间，不会把旧备份伪装成本次成功。

默认没有自动配置异地目标。可通过安装程序的 `--secondary-dir` 设置已经挂载、预先创建的独立磁盘/远程文件系统目录；目标必须和主备份目录处于不同文件系统，避免挂载丢失时悄悄写回主盘。第二份只复制已完成且认证通过的加密归档，也应用相同保留规则。复制失败会报告失败，已完成的主备份仍保留。

部署重装定时任务时保留已有第二份目标；只有显式 `--clear-secondary` 才清除配置。若某次备份由其他设备手动取走，这并不等于已经建立每天自动异地备份。

必须另外安全保管备份主密钥。仅复制加密归档而遗失主密钥无法恢复；将主密钥与归档放在同一位置则削弱备份保密性。RAID 和同机另一目录不能替代独立备份。

## 手动检查和恢复演练

以下命令只运行备份工具，不是绕过部署脚本发布代码。使用 NAS 已有 Docker 权限的部署账号：

```sh
read -r erp_release < /home/skymoonzyj/esports-erp/.active-release
python3 "$erp_release/scripts/erp-backup.py" verify \
  --app-dir /home/skymoonzyj/esports-erp \
  --project esports-erp-staging \
  --backup-dir /home/skymoonzyj/esports-erp/backups/full \
  --key-file /home/skymoonzyj/.config/esports-erp/backup.key \
  --status-file /home/skymoonzyj/esports-erp/operations/backup-status.json
```

默认验证最新完整备份。可以加 `--backup-id backup-YYYYMMDDTHHMMSSZ-xxxxxxxx` 指定实际存在的历史备份，以便恢复到误删或错误修改之前；该参数不接受任意路径。

演练会先验证并解密归档，再启动临时 PostgreSQL 容器。容器使用 `--network none`，不发布端口、不挂载生产卷，验证程序只加入该临时容器的隔离网络空间。随后还原数据库、比较快照行数及结构，用恢复的密钥解密真实人员和邮箱配置样本，并验证媒体清单。只清理本次带随机所有权标记的演练容器及它的临时卷。没有任何分支可以把数据库直接覆盖到线上。

PostgreSQL 还原时可能改写约束或索引中的等价表达式。工具会在隔离数据库的临时空表中重新解析相关快照定义后再比较，避免把括号和类型转换的等价改写误报为损坏；缺失对象、规则、谓词、类型转换或操作符的实际变化仍会阻止验证成功。

序列还会在不消耗编号的情况下计算下一值。存在对应列时，订单号 `BM+日期+流水号` 和自动学号 `REG-+年份+流水号` 的下一流水号必须高于已恢复记录中的最大流水号，且不允许耗尽、倒序或循环序列；人工学号不参与此规则。此检查仅验证备份内可确定的编号安全性，不代表线上序列状态的实时审计。

归档格式保持 v1，不增加必需清单字段；历史备份同样从自身的 dump 提取序列证据，并根据其中的 `standard_conforming_strings=on/off` 设置正确还原序列名称中的字面反斜杠。历史库没有序列或尚无上述编号列时可以通过相应兼容分支。含序列但缺失 `SEQUENCE SET` 的旧备份不会再被标为验证成功，也不会自动改写序列或原归档。运维错误码分别为 `restore_sequence_evidence_invalid`（证据问题）、`restore_sequence_state_mismatch`（恢复位置/调用状态不符）、`restore_sequence_next_identifier_unsafe`（下一编号不安全），监控界面显示对应中文说明。

## 整机故障后的恢复步骤

1. 准备一台兼容架构的干净 Linux 主机，安装 Docker、Python 3.11+、OpenSSL；取得加密备份目录和单独保管的备份主密钥。
2. 将以下完整最小恢复工具包带到新主机，保留列表中所有相对目录结构。`cloud_auth.recovery`、`web_assets.py` 及其共享 schema 是备份入口的必需依赖，即使恢复旧归档也不能省略；不能依靠尚未解出的 source.tar.gz 或原仓库环境提供它们。备份源代码包含这些文件；日常应把工具与恢复文档一起保留在独立的代码副本中：

   <!-- ERP_RECOVERY_KIT_BEGIN -->
   - `scripts/erp-backup.py`
   - `scripts/erp-backup-install.py`
   - `scripts/erp_backup_container.py`
   - `scripts/cloud_auth/__init__.py`
   - `scripts/cloud_auth/recovery.py`
   - `scripts/web_assets.py`
   - `apps/web/scripts/web-assets.schema.json`
   <!-- ERP_RECOVERY_KIT_END -->

   在不依赖原仓库环境的目录运行 `python3 -I scripts/erp-backup.py extract --help`，确认入口能启动；此检查不访问 Docker、数据库或云服务，也不等同于完成恢复。
3. 用 `extract` 导出到**尚不存在的新目录**。该步骤不连接任何数据库、不覆盖已有目录：

   ```sh
   python3 scripts/erp-backup.py extract \
     --backup-dir /srv/erp-backups \
     --key-file /secure/erp-backup.key \
     --recovery-dir /srv/erp-recovery-20260911
   ```

4. 私有恢复目录中的 `recovery/images.tar` 可用 `docker image load --input` 导入；`manifest.json` 记录每个服务的镜像 ID。也可从 `recovery/source.tar.gz` 在新目录重建，`verify --api-image` 支持明确指定重建后的 API 镜像，`--postgres-image` 只允许与备份相同的 PostgreSQL 主版本。
5. 在独立恢复工作目录执行 `verify`，使用已复制的备份和主密钥；该命令不要求原生产容器仍在运行。缺失原 API/PostgreSQL 镜像时会从归档加载，再进行隔离数据库恢复演练。
6. 实际业务恢复需要新建 ERP Compose 项目、空 PostgreSQL 卷和媒体目录，依据恢复的配置调整新主机路径。保留字段加密/查询密钥，恢复数据库、原始文件及预览文件，核对人员、订单、财务和登录，再安排域名切换。TLS 私钥及数据库凭据由管理员按恢复计划保管或轮换；不要把恢复目录公开共享。

本工具完成可重复的数据库和文件恢复演练、恢复材料导出，不会自动切换域名或覆盖生产数据库。只有在独立环境完成业务验收后，才进行正式流量切换。

## 可选受管连接器的一致性恢复

归档保持 `format_version=1` 和既有 `archive.tar.gz.enc` / `complete.json` 二件套。没有连接器子树的历史备份仍按原流程验证；受管实例存在时不能因快照失败就退回“无连接器备份成功”。恢复主密钥必须位于备份目录和连接器目录之外，从不纳入恢复 payload。

备份锁顺序为 `.cloud-backup.lock` 独占 → `.backup.lock` 独占 → `.deploy.lock` 共享 → `.cloud-connector.lock` 独占。取得锁后，先将本次 operation UUID 和原运行状态写入受保护的 `snapshot-state.json`，再以私有 RPC 建立 90 秒维护 gate；返回的 exact fence 验证并持久化后才停止专用连接器。丢失 begin 回复时以同一 UUID 重试，绝不创建新操作覆盖未知 gate。停止期间导出 ERP 数据库事务快照，并完整复制连接器 SQLite/WAL/config；禁止热拷贝。暂停预算 30 秒，数据库元数据读取有独立截止时间，复制为重启和运行状态检查预留预算。重启后才继续同一数据库快照的 `pg_dump` 和镜像归档。

失败会恢复原运行状态并清除 gate；重启或确认失败时保留 journal/gate，备份失败。进程被强杀后，后续持同一锁顺序的主机恢复逻辑先检查专用连接器所有权和真实运行状态，再清除持久 fence。丢失 end 回复或清除成功后未能删除 journal 时，可重放已完成的清除；若出现另一个活跃 fence 则拒绝清除。仅维护租约过期不表示服务恢复。不要手动删 journal 来绕过检查。

新可选子树 `recovery/cloud-connector/` 的每个文件纳入现有 SHA-256 inventory、加密和磁盘峰值预检；连接器镜像也纳入 `recovery/images.tar`。清单不含明文 secret。ERP 导出的候选用户名/密码仅放在受保护的恢复文件中，且从数据库 metadata 移除；恢复验证再次使用原字段绑定解密数据库记录，核对挂载/用户 ID。

隔离演练复制到新的私有目录，校验 SQLite 结构、完整性及完整候选引用，并以 `--network none`、无发布端口、禁止 Docker 日志的临时 OpenList 验证管理员和可用候选凭据。仅在容器自身 localhost 登录，不刷新网盘令牌、不触达网盘。文件共享层 UID 映射通过只读、断网的 stat 检查处理，不放宽 0700。演练只删除带本次随机所有权标记的容器及临时恢复目录，不删除生产卷或云端历史文件。

此流程不自动把旧主机的“成功”状态当作新主机联网可用。恢复目录中的路径为相对恢复路径；正式迁移需显式创建/登记新的专用实例、重建正确路径和权限，再执行新主机联网验收。

### 授权来源与刷新状态恢复

受管授权保存 `own_app`（自有应用）或 `openlist_builtin_import`（免填应用信息导入）的实际来源、冻结来源修订和协议版本。候选目录修订是另一项数据，不能用来推断授权来源。旧版已识别记录按原自有应用模式读取；未知日志版本拒绝恢复，不根据缺少 AppKey 猜测。恢复密钥仍单独保管，授权资料仅保存在完整加密备份内。

2026-10-10 恢复边界修订：数据库快照不能回滚厂商在备份后消耗/轮换令牌的事实。**完整备份还原或新主机交接**必须先关闭所有未完成的内置导入，再释放原 exact gate；即使快照显示尚未发送、已有新令牌或已核对身份，也要求重新授权。已交付但尚未应用的候选只保留受保护的归属/清理日志用于核对，不重新交付旧令牌。已应用的活动候选、旧目标、自有应用、手动配置和云端历史文件保持不变。

以下矩阵仅适用于**未回滚数据库的同库进程重启**，不能用于完整备份恢复：

| 同库持久状态 | 重启处理 |
| --- | --- |
| 尚未发送刷新 | 重新校验会话期限、操作者权限、来源与租约；仍有效才可继续 |
| 已记录发送，结果未持久化 | 标记“续期结果未确认”，禁止重发原刷新令牌，需重新授权 |
| 新令牌已持久化 | 只核对新令牌身份，不发起第二次刷新 |
| 身份核对完成 | 仅继续候选交接及其幂等核对 |
| 已交付候选或已应用 | 由连接器继续管理，不写回旧令牌，不自动启用复制 |

同库续作仍受原会话与配置期限约束；完整备份恢复不续期、不恢复未完成内置导入的续作资格。百度内置授权的有效期可能为未知；必须保留这一事实，不能改写成长期有效或虚构时间。授权验证时间和刷新时间也不能互相替代。恢复时保留其他账号、其他挂载和远端历史文件，维护锁只按原 exact fence 解除。

迁移 `0064_cloud_auth_builtin_import` 保留旧数据及旧密文，增加显式来源与加密协议标记。若数据库已有内置导入或新协议记录，降级预检会拒绝操作；不能手工删字段或修改版本号绕过。需要回退时按已验证备份和隔离恢复计划处理，不在生产库试验降级。

### 新主机受管连接器维护 gate 交接

受管备份的 PostgreSQL 快照是在维护 gate 活跃期间导出的，因此恢复库仍保留该 gate；源主机随后成功清除自身 gate 不会修改备份。归档提供 `cloud_connector.snapshot_fence` 和 `was_running`，但不携带旧主机的临时 journal。不要以租约过期、手动删字段或导入任意 JSON 来解除维护。

此操作必须由管理员明确执行，顺序为：

1. 用上述认证 `extract`/隔离 `verify` 检查备份；在新项目和空库中还原该备份数据库、媒体及原 ERP 字段密钥，启动新项目 API。保留独立验收环境，不切换生产流量。
2. 从同一份认证归档将 `recovery/cloud-connector/data` 和 `admin-secret` 还原到新 `APP/cloud-connector/`（目录 0700，secret 0600，禁止 symlink）。加载归档记录的不可变镜像，明确准备专用连接器：同一 connector UUID、`erp.cloud.connector=UUID`、`erp.cloud.dedicated=true` 标签、唯一专用数据 bind mount，以及正式使用时仅回环 5244 绑定。容器可改为新 project 的名称，但不能用空的新实例代替已还原数据。
3. 使用完整发布源中的 `erp-cloud-connector-install.py --mode managed --app-dir APP --project PROJECT --connector-id UUID --admin-secret-file APP/cloud-connector/admin-secret` 登记已经准备好的专用实例；不要复制旧 registry 的宿主绝对路径，不要用 create 模式覆盖已还原目录。保留原 connector UUID、镜像 ID、管理员 secret 和固定 origin。
4. 对**原始加密备份二件套**执行以下显式交接命令。`--key-file` 只传密钥文件路径；不传密码或 token。`PROJECT-api-1` 必须是已还原数据库对应的新 API，而非原生产库：

   ```sh
   python3 scripts/erp-backup.py recover-connector \
     --app-dir /srv/erp-restored \
     --project erp-restored \
     --backup-dir /srv/erp-backups \
     --backup-id backup-YYYYMMDDTHHMMSSZ-xxxxxxxx \
     --key-file /secure/erp-backup.key
   ```

5. 再核对新主机业务记录、候选凭据及目录权限、连接测试、最新完整备份回读和隔离恢复，然后决定上线。交接成功不是厂商授权验收，也不自动启用任何云目标。

`recover-connector` 持标准四层主机锁，重新认证原归档 HMAC/密文及解密文件 inventory；不信任单独编辑的提取 manifest。它检查新 registry 与备份的 connector UUID、不可变镜像、origin 和管理员 secret，以及实际容器的 dedicated 标签和唯一 bind。只为该 exact fence 构造受保护 journal，并在任何释放请求前持久化 `restore_handoff` 意图；按原 `was_running` 恢复并确认实际运行状态后调用恢复专用 `restore_snapshot`，不调用普通备份的 `end_snapshot`。API 在同一锁顺序和事务中校验登记连接器、原 fence 和授权来源，关闭未完成内置导入、清除原始令牌/刷新检查点并释放 gate；校验或写入失败保留 gate。若原运行中而新实例停止，它会启动此已经准备好的专用容器；不会创建/还原容器、数据库或外部网络。

已有 foreign journal 不覆盖；另一个活跃 gate 不清除。认证、身份、重启或释放失败均返回失败，释放失败保留本次 journal 供安全重试，不输出敏感材料。恢复 journal 的中断续作仍使用恢复专用动作。若释放已成功但应答丢失，无 gate 重试仅返回只读确认，不再关闭随后新建的授权；无 gate 本身不是已完成失效处理的证明，不能删除恢复意图或用普通释放代替。旧的无连接器归档仍可 extract/verify，但不能调用此受管交接。不要删 journal 或换 UUID 来绕过失败。

## 验证命令

常规回归覆盖加密/错密钥、密文篡改、路径穿越、故障后保留旧成功状态、保留策略、互斥锁、定时任务幂等和容器隔离。独立 Docker 演练测试不访问项目共享测试数据库：

```sh
ERP_BACKUP_DOCKER_TEST=1 python -m pytest \
  apps/api/tests/cli/test_erp_backup.py \
  apps/api/tests/cli/test_erp_backup_docker.py
```

连接器演练另需显式设置 `ERP_CLOUD_CONNECTOR_RECOVERY_TEST=1`，运行 `apps/api/tests/cli/test_cloud_connector_recovery.py`。skip 不算演练完成。Colima 等 Docker VM 只共享部分宿主路径时，应给 pytest `--basetemp` 指定此次新建、Docker 可见的用户目录；不要复用含用户资料的目录。

可用 `ERP_BACKUP_TEST_API_IMAGE` 指定本地已有 API 测试镜像。生产部署还必须实际执行一次全量加密备份和恢复验证，并检查运维状态及新备份目录。
