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
2. 将备份工具三个 Python 文件带到新主机。备份源代码也包含这些文件；日常应把工具与恢复文档一起保留在独立的代码副本中。
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

## 验证命令

常规回归覆盖加密/错密钥、密文篡改、路径穿越、故障后保留旧成功状态、保留策略、互斥锁、定时任务幂等和容器隔离。独立 Docker 演练测试不访问项目共享测试数据库：

```sh
ERP_BACKUP_DOCKER_TEST=1 python -m pytest \
  apps/api/tests/cli/test_erp_backup.py \
  apps/api/tests/cli/test_erp_backup_docker.py
```

可用 `ERP_BACKUP_TEST_API_IMAGE` 指定本地已有 API 测试镜像。生产部署还必须实际执行一次全量加密备份和恢复验证，并检查运维状态及新备份目录。
