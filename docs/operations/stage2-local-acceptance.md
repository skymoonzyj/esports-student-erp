# Stage 2 本地联合验收

本文只定义评价中心 Stage 2 的本地非容器联合验收入口和证据边界。只有官方 runner 当次退出码为 0，且生成的摘要中 `status=passed`、`cleanup_completed=true`，才能表述为“Stage 2 本地联合验收通过”。

本工作树保留最近一次符合该标准的通过摘要；具体生成时间、Git SHA 和 `source_tree_sha256` 以摘要文件为准。摘要退出码为 0、`status=passed` 且 `cleanup_completed=true`，覆盖本文列出的五个 spec 和四个固定视口，只证明本地非容器 Stage 2 通过。

## 官方入口

```bash
TEST_DATABASE_URL='postgresql+psycopg://本机用户@127.0.0.1:5432/erp_stage2_test' \
FIELD_ENCRYPTION_MASTER_KEY='独立的 32 字节 URL-safe Base64' \
FIELD_LOOKUP_KEY='另一个 32 字节 URL-safe Base64' \
SESSION_TOKEN_PEPPER='第三个 32 字节 URL-safe Base64' \
EVALUATION_TOOL_SERVICE_TOKEN='独立 v1 测试服务密钥' \
EVALUATION_TOOL_WEB_SERVICE_TOKEN='独立 Web 测试服务密钥' \
EVALUATION_TOOL_WORKER_SERVICE_TOKEN='独立 Worker 测试服务密钥' \
E2E_PYTHON_BIN='/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
SCORE_REPO='/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing' \
bash scripts/run-stage2-local-acceptance.sh
```

三个评价服务密钥必须彼此不同。数据库必须位于本机，数据库名必须为 `erp_e2e` 或以 `_test` 结尾；runner 在迁移和 seed 前调用 `app.cli.e2e_database_safety`，危险地址会直接拒绝。

组合夹具入口是 `scripts/seed-stage2-acceptance-e2e.py`。它要求先存在 canonical E2E 基础夹具，然后在同一隔离数据库中组合系统评价、教学日报/周报和营期问卷资源。输出只包含固定 UUID、模块名和 schema 版本，不输出密码、服务 token、手机号、身份证、邮箱或媒体路径。

## 本地拓扑与执行顺序

runner 只绑定回环地址，启动以下临时进程：

- ERP API；
- ERP Vite Web；
- game-score-tool Web；
- score-tool Worker。

媒体、隔离区、Worker 临时文件、Playwright 结果和服务日志全部位于权限为 `0700` 的系统临时目录。每个视口按以下顺序串行运行：

1. 系统评价处理与隐私旅程；
2. 教学日报/周报、审核发布、版本修订、家庭查看与即时撤权旅程；
3. 营期评价独立提交、一次修改、匿名反馈、管理员打印与即时撤权旅程；
4. 三个域共同存在时的工作台、家庭投影、隐私响应和布局烟测。

固定视口是：

- `desktop-1440`；
- `desktop-1024`；
- `tablet-768`；
- `mobile-360`。

runner 每个域重新建立 canonical E2E 数据，避免上一个浏览器旅程的写入污染下一个域；跨模块烟测则使用 `scripts/seed-stage2-acceptance-e2e.py` 一次组合三类初始资源。全部页面使用真实 API、真实授权和语义定位器，不注入页面假数据。

## 输出与清理

默认机器可读摘要写入：

```text
runtime/stage2-acceptance/stage2-local-acceptance-summary.json
```

也可以通过 `STAGE2_ACCEPTANCE_SUMMARY_PATH` 指定其他路径。摘要文件权限为 `0600`，只记录 schema 版本、状态、退出码、Git SHA、`source_tree_sha256`、UTC 生成时间、视口、spec 名称和清理状态。`source_tree_sha256` 覆盖 HEAD、当前已修改内容和未忽略的新文件，只保存摘要，不保存文件正文或路径清单。

正常完成、失败或中断都会触发清理：停止 API/Web/score-tool/Worker，删除本次系统临时目录，并更新摘要。失败时只向终端输出经过脱敏的末尾日志；Authorization、Cookie、CSRF、票据、手机号、身份证、邮箱、数据库 URL 和绝对路径不得进入验收摘要。

## 安全边界

- 只允许隔离测试数据库和临时媒体目录。
- 不读取、迁移、修改或删除 score-tool 旧 `data/`、`uploads/`、`runtime/` 及 SQLite。
- 不连接 NAS，不复制真实截图，不处理录像。
- 不使用生产密钥、生产数据库、生产域名或真实账号。
- 本 runner 不构建镜像，也不证明容器、备份恢复、TLS、反向代理或生产监控可用。

Docker/NAS/GPU/录像/生产数据迁移均未执行。它们必须使用各自独立的验收和上线记录，不能由本地 Stage 2 摘要推断完成。
