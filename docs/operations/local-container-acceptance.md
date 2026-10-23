# 本地 disposable Compose 验收

本文定义 ERP 当前容器拓扑的本地一次性验收入口和证据边界。它验证镜像构建、E2E Compose 合并配置、
容器健康状态、空卷迁移、反向代理/API 健康端点以及数据库 schema manifest；不代表 NAS 或生产环境已
经部署验收。

## 官方入口

先启动 Docker Desktop（或兼容 Docker daemon），然后在仓库根目录运行：

```bash
bash scripts/run-local-container-acceptance.sh
```

runner 优先使用 `docker compose`；也支持 PATH 中的 `docker-compose`，旧版 Apple Silicon Homebrew
环境最终可回退到 `/opt/homebrew/bin/docker-compose`。Compose 客户端必须是 Docker Compose v2 或更高版本，
以支持 E2E overlay 使用的标签和 `--wait`。三种方式都必须同时具备可用的 Docker client 和 Docker daemon。
daemon 不可用时 runner 以状态码 3 结束，并且不会执行 `config`、`up`、`exec` 或清理其他 Compose
项目。

本工作树已留存一次符合该标准的本地容器通过记录；具体生成时间、Git SHA 和
`source_tree_sha256` 以摘要文件为准。摘要退出码为 0、`status=passed`、
`cleanup_completed=true`，八个检查项全部为 `passed`。该记录只证明一次性本地 Compose 拓扑通过，
不代表 NAS 或生产环境已经部署。

## 隔离拓扑和执行顺序

每次执行都会生成唯一的 `esports-erp-container-acceptance-*` Compose project，仅合并：

- `compose.yaml`；
- `compose.e2e.yaml`。

本验收不使用 compose.prod.yaml。runner 在 `up` 之前先执行 `config --quiet`，随后执行
`up --build --wait`。HTTP 与 PostgreSQL 发布端口均从 `127.0.0.1` 的空闲端口自动选择；显式提供非
`127.0.0.1` 的 `ERP_BIND_ADDRESS` 会在访问 Docker 前被拒绝。

Compose 使用 runner 创建的空 `--env-file`，并设置 `COMPOSE_DISABLE_ENV_FILE=1`，因此不会自动加载
仓库 `.env`。所有 Compose 子命令均从 `env -i` 开始，只保留 Docker 连接信息、PATH、HOME、临时目录、
回环地址和本次 E2E 端口；宿主环境中的 `DATABASE_URL`、生产密钥、token、`COMPOSE_FILE`、
`COMPOSE_ENV_FILES` 和项目名不会传播给 Compose 或容器。

容器启动后按以下顺序验收：

1. 在 API 容器内以内部 `erp_e2e` URL 执行 `app.cli.e2e_database_safety`；
2. 仅在安全门禁通过后执行 `alembic upgrade head`；
3. 确认 manifest 声明的迁移文件存在；
4. 以连接和总请求超时请求反向代理 `/healthz`；
5. 通过同一代理请求 API `/api/v1/health/live`；
6. 在一次性 PostgreSQL 容器内核对 Alembic head、必要表、索引、约束、触发器、序列、关键列、角色和
   权限。

所有外部命令输出先写入 `0700` 临时目录；成功输出不回显，失败输出必须经过安全脱敏器后才进入终端，
数据库 URL、token、pepper、password 和 key 均不会原样输出。

该 runner 不运行浏览器、Playwright、Vitest、API pytest 或业务 seed。浏览器旅程和完整代码门禁仍由
各自的本地非容器 runner 执行，不能从本摘要推断通过。

## 临时文件、摘要和清理

runner 使用权限为 `0700` 的系统临时目录。默认机器可读摘要写入：

```text
runtime/container-acceptance/local-container-acceptance-summary.json
```

可通过 `LOCAL_CONTAINER_ACCEPTANCE_SUMMARY_PATH` 指定其他路径；runner 不会修改已经存在的父目录权限。
摘要权限固定为 `0600`，只记录状态、退出码、清理状态、随机 Compose project 名、两份 Compose 文件名、
Git SHA、`source_tree_sha256`、每个检查项的 `pending`、`passed` 或 `failed` 逐项状态和 UTC 时间；
`source_tree_sha256` 覆盖 HEAD、当前已修改内容和未忽略的新文件，只保存摘要；不会记录
数据库 URL、用户名、密码、cookie、token、密钥、手机号、邮箱或身份证。

正常完成、失败或中断均由 `trap cleanup EXIT` 收尾，并针对本次唯一 project 执行：

```text
down --volumes --remove-orphans
```

因此容器、网络和命名卷均为一次性资源，不复用开发数据库或其他任务的 Compose project。摘要只有在
清理完成后才可能写入 `status=passed`；只有 `status=passed`、`cleanup_completed=true` 且全部逐项状态
均为 `passed` 时，才能作为“本地容器验收通过”的证据。

## 安全与证据边界

- 只绑定 `127.0.0.1`，不对局域网或公网开放端口。
- 不使用 compose.prod.yaml，不读取生产 overlay、生产 secrets 或 NAS 卷。
- 不运行浏览器，也不处理截图、录像、GPU 作业或真实业务数据。
- NAS 部署、外部 TLS、生产密钥轮换、真实备份恢复和生产数据迁移仍延期。
- 不证明生产域名、监控、NAS 权限或灾难恢复可用。
- Docker daemon 不可用时，只能运行 launcher 的静态 pytest；不得把静态 GREEN 描述成容器已启动或
  容器验收通过。
