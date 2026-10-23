# 电竞教育 ERP

当前 NAS 版本已包含结算防重复、自动加密备份与隔离恢复验证，以及持久运维历史和站内告警。使用方式与边界见 [运行可靠性说明](docs/operations/reliability.md)。下文的本地验收章节保留了各阶段的历史记录，不代表当前 NAS 的最新功能清单；视频/GPU 分析和真实二维码支付仍待接入。

这是电竞教育 ERP 的本地验收版本。系统已经具备响应式 Web、人员与实名档案、成年状态判定、监护就绪与关系历史、手机号密码登录、账号生命周期、拒绝优先权限、敏感信息加密查看和安全审计；管理员可以维护电竞项目、职业队伍、营期、普通班/菁英班和教练分配，并完成付款导入、手工付款、待分配、正式报名、转班、退款和退营退费。住宿生命周期后端 API、管理员动态房态 UI、学员/监护人个人住宿门户以及实名入住报送页面均已完成本地实现；入住生命周期、个人住宿门户和实名报送的非 Docker 四视口门禁均已完成。教练、监护人和学员可以从同一工作台进入各自获权的只读门户。

系统评价截图→任务→Worker→审核→发布链路已提供本地隔离 E2E，但这只是验收流程，不代表生产部署认证。教学日报、周报、审核发布与家庭已发布查看已本地实现；教学日报、周报、营期问卷和组合工作台的 Stage 2 本地联合验收已通过。Stage 2 本地联合验收通过不代表 Docker/NAS/GPU/录像/生产数据迁移通过。ERP 核心服务的一次性本地 Compose 验收也已通过，但评分 Web/Worker 的生产容器、NAS 部署、公安在线 API、生产密钥轮换、生产备份恢复和生产环境仍未验证。当前阶段仍不包含学员站内选项目和跳转支付、第三方支付平台适配器或录像分析；录像与 GPU 分析继续保持延期。

## 默认入口与安全边界

- 统一 Web 入口：<http://127.0.0.1:8080>
- “天赋评测 2”独立菜单与模块根路径：`/esports-assessments`；玩法、权限、恢复和设备边界见[天赋评测 2 功能说明](docs/features/esports-assessments.md)。当前四个套系全部“尚未开放”，该入口和文档不代表测试通过、NAS 发布或正式运营开放
- 开发 PostgreSQL：仅本机 `127.0.0.1:5432`
- API、Vite 和生产静态 Web 不单独发布宿主机端口
- E2E 使用独立 Compose 项目、`erp_e2e` 数据库、独立卷及 `127.0.0.1:18080`
- 身份证与手机号在数据库中加密；列表、普通详情和审计默认只显示脱敏值

生产环境必须由 NAS 的 HTTPS 反向代理对外提供访问。生产配置强制安全 Cookie，直接用明文 HTTP 访问会导致登录 Cookie 无法正常工作。

## 准备环境

需要：

- Python 3.12
- Node.js 24 与 npm
- PostgreSQL 客户端（备份恢复需要 `pg_dump`、`pg_restore`、`psql`、`createdb`）
- Docker Engine 与 Docker Compose

安装本地依赖：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e 'apps/api[dev]'
npm --prefix apps/web install
cd apps/web && npx playwright install chromium && cd ../..
```

## 本地 Docker 开发

复制配置：

```bash
cp .env.example .env
```

生成三个不同的 32 字节 URL-safe Base64 值，分别填入 `.env` 的：

- `FIELD_ENCRYPTION_MASTER_KEY`
- `FIELD_LOOKUP_KEY`
- `SESSION_TOKEN_PEPPER`

每次执行下面命令会生成一个值：

```bash
.venv/bin/python -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())'
```

启动数据库，执行迁移，再启动全部服务：

```bash
docker compose --env-file .env up -d db
docker compose --env-file .env run --rm api alembic upgrade head
docker compose --env-file .env up -d --build
```

如果环境提供的是独立 `docker-compose` 命令，将示例中的 `docker compose` 替换为 `docker-compose`。

创建首位管理员（密码会在终端中安全询问两次）：

```bash
docker compose --env-file .env exec api python -m app.cli.create_admin \
  --name '平台管理员' \
  --phone '13800138000' \
  --email 'admin@example.com' \
  --identity-number '11010519491231002X' \
  --sex female \
  --date-of-birth '1949-12-31'
```

停止开发环境：

```bash
docker compose --env-file .env down
```

## 不使用 Docker 的本地调试

准备一个名称以 `_test` 结尾的 PostgreSQL 数据库，把 `.env` 中的数据库主机改为 `127.0.0.1`。在第一个终端运行 API：

```bash
set -a
source .env
set +a
export DATABASE_URL='postgresql+psycopg://本机用户@127.0.0.1:5432/erp_test'
.venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000
```

在第二个终端运行 Web：

```bash
npm --prefix apps/web run dev
```

Vite 会把 `/api` 同源转发到 `127.0.0.1:8000`。

在第三个终端使用与 API 相同的 `.env` 和 `DATABASE_URL` 运行营期状态调度器；它会按配置间隔执行到期的自动开营和自动结营：

```bash
.venv/bin/python -m app.cli.run_teaching_scheduler
```

本地排查时可只执行一轮：

```bash
.venv/bin/python -m app.cli.run_teaching_scheduler --once
```

## 自助注册本地验收（已完成）

自助注册模块现已具备手机号/邮箱注册、实名资料、既有档案安全认领、已有账号登录或找回密码、未成年监护人邀请与独立确认、成年可选监护关系、工作人员申请审核、第二登录标识和已验证手机号业务门禁。注册会话与正式 ERP 会话使用不同 Cookie、pepper、CSRF 和依赖，待监护学员不能进入正式 ERP。

确定性本地验收入口是：

```bash
E2E_PYTHON_BIN="$PWD/.venv/bin/python" \
  bash scripts/run-self-service-registration-e2e.sh
```

该 runner 只绑定回环地址，要求隔离 PostgreSQL 测试库，并通过受 sentinel 保护的 `0600` 临时 mailbox 提供本地验证码。它串行运行 `desktop-1440`、`desktop-1024`、`tablet-768` 和 `mobile-360` 下的主旅程与隐私旅程。完整运行方法、密钥要求、30 天申请保留、7 天监护邀请、Cookie 隔离和清理边界见 [自助注册本地运行与验收](docs/operations/self-service-registration-local.md)。

自助注册本地实现与完整门禁已完成：全量 API、PostgreSQL、迁移、并发、Web、Ruff、mypy、lint 和生产构建均已通过；主旅程与隐私旅程在 1440、1024、768 和 360 四个视口共 8 组组合全部通过。项目选择、订单、支付跳转、真实短信/邮件、第三方实名、Docker/NAS 和生产部署仍未开放或验证，不得把本地页面与 E2E 结果用于真实运营。

## 住宿生命周期与个人住宿门户

房间目录与静态房态基础继续采用“楼栋 → 楼层 → 房间”三层目录。管理员可以逐间或批量核对这些房间，
并把房态权限授予指定楼栋或楼层范围。管理页面提供静态汇总、楼栋/楼层/房型/核对状态组合筛选和响应式
房间卡片。房间源自一次初始导入批次，导入批次仅由初始种子写入。系统保留导入批次元数据用于来源追溯，
楼栋和房间都没有导入批次外键。没有导入批次 API 或管理界面，管理员不能创建、编辑或删除导入批次，
批次不是可维护的目录层级。初始目录包含 4 幢楼、11 个楼层、188 个房间条目，其中
171 间住宿房需要逐间或批量核对。

住宿生命周期后端 API 已在本地实现，PostgreSQL 是住宿业务主记录；学员/监护人个人住宿门户已在本地实现，
可在独立 `/me/housing` 页面查看获权学员的当前住宿、当前室友姓名和未来预约，未来室友在实际确认入住前
保持隐藏。原始 `seed_e2e` 仍不创建住宿记录，个人门户的 3 条当前住宿和 8 条未来住宿只由显式 opt-in
场景 seeder 写入隔离测试库，不能视为真实房态。

管理员入住、调房、退房对话框和动态房态管理 UI 已在本地实现。新增启动器覆盖管理员核对、预约、入住、
异性冲突、问题房、原子调房、二次确认退房、本人/监护人室友投影和楼层范围撤权。
入住生命周期本地实现与非 Docker 门禁已完成。住宿生命周期四视口浏览器旅程已通过：已在安全检查通过的本地隔离 PostgreSQL 上，
按 `desktop-1440`、`desktop-1024`、`tablet-768`、`mobile-360` 四个视口分别执行生命周期与撤权两个 spec，
共 8 条浏览器旅程全部成功；该门禁只启动本地 API/Web 进程，不调用 Docker。

实名入住报送已在本地实现。拥有敏感权限的账号可以从 `/housing/reports` 按实际入住时间和获权范围生成
PostgreSQL 加密不可变快照，下载包含“入住名单”和“未成年监护人”的 Excel 两个工作表或等价的 CSV ZIP，
并完成历史重下载、人工报送、作废和更正链。服务端强制执行拒绝优先的楼栋/楼层范围授权，权限撤销后历史
列表、详情、下载和状态动作立即拒绝；页面摘要与安全审计不展示完整手机号、身份证或快照内容。

实名报送四视口浏览器旅程已通过：已在安全检查通过的本地隔离 PostgreSQL 上，按 `desktop-1440`、
`desktop-1024`、`tablet-768`、`mobile-360` 四个视口分别执行生命周期与授权/撤权两个 spec，共 8 条实名报送浏览器旅程全部成功。
实际下载的 XLSX 与 CSV ZIP 均由固定 Python 检查器解析并比较，历史重下载也验证不受后续人员资料修改影响。

住宿模块的 Docker/NAS、公安在线 API、生产密钥轮换、生产备份恢复和生产环境均未验证；录像/GPU 也继续延期。
阶段 3 仍未完成，当前结果也不可用于生产住宿运营或公安在线自动报送。

四套四视口浏览器门禁都只启动本地进程，不调用 Docker：

```bash
scripts/run-housing-room-status-e2e.sh
scripts/run-housing-occupancy-e2e.sh
scripts/run-housing-personal-portal-e2e.sh
scripts/run-housing-reporting-e2e.sh
```

静态房态启动器默认使用 API `8001` 和 Web `5176`，完整生命周期启动器默认使用 API `8002` 和 Web `5177`，
个人住宿启动器默认使用 API `8003` 和 Web `5178`，实名报送启动器默认使用 API `8004` 和 Web `5179`。
生命周期启动器按四个视口和两个 spec 的每个组合重新运行 `python -m app.cli.seed_e2e`；实名报送启动器还会在
每个组合中随后运行 `python -m app.cli.seed_housing_reporting_e2e`，不会把浏览器状态带入下一条旅程。端口冲突时可选用 `E2E_API_PORT` 和
`E2E_WEB_PORT` 覆盖，例如：

```bash
E2E_API_PORT=18001 E2E_WEB_PORT=15176 scripts/run-housing-room-status-e2e.sh
```

只有在 `TEST_DATABASE_URL` 指向通过安全检查的隔离测试库，并同时提供
`FIELD_ENCRYPTION_MASTER_KEY`、`FIELD_LOOKUP_KEY`、`SESSION_TOKEN_PEPPER` 和
`EVALUATION_TOOL_SERVICE_TOKEN` 后，才可运行完整生命周期入口：

```bash
E2E_PYTHON_BIN=.venv/bin/python scripts/run-housing-occupancy-e2e.sh
E2E_PYTHON_BIN=.venv/bin/python scripts/run-housing-reporting-e2e.sh
```

## 评价中心页面整合

评价中心基础与系统评价处理单元均已提供本地实现和非 Docker 门禁。本状态覆盖工作人员服务历史、游戏 ID、辅助手机号、对象范围权限、版本化分析配置、私有媒体、任务处理和响应式页面；Docker/NAS 等生产整合仍按原计划延期。

本地开发现已接入独立仓库 `game-score-tool` 的系统评价页面。获权账号可以从 ERP 的评价中心进入系统评价，
既可在 ERP 内同源嵌入，也可在 `http://localhost:8010` 单独打开；两种入口均通过 ERP 单次票据建立会话，
评分工具会在每个受保护请求上重新验证 ERP 会话和 `evaluation_center.access` 权限。

先为 ERP 与评分工具生成只在本机使用的 `EVALUATION_TOOL_SERVICE_TOKEN` 和
`SCORE_TOOL_SESSION_SECRET`，两者都至少 32 个字符，且不要提交真实值。保持本地 PostgreSQL 已迁移后，分别在
三个终端运行：

```bash
# Terminal 1: ERP API
APP_ENV=development DATABASE_URL="$DATABASE_URL" \
EVALUATION_TOOL_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
ERP_WEB_BASE_URL=http://127.0.0.1:5173 \
EVALUATION_TOOL_STANDALONE_BASE_URL=http://localhost:8010 \
.venv/bin/uvicorn app.main:app --app-dir apps/api --host 127.0.0.1 --port 8000

# Terminal 2: ERP Web
ERP_API_PROXY_TARGET=http://127.0.0.1:8000 \
EVALUATION_TOOL_PROXY_TARGET=http://127.0.0.1:8010 \
npm --prefix apps/web run dev -- --host 127.0.0.1 --port 5173

# Terminal 3: score tool
cd /Users/skymoonzyj/Documents/game-score-tool
ERP_API_BASE_URL=http://127.0.0.1:8000/api/v1 \
ERP_WEB_BASE_URL=http://127.0.0.1:5173 \
ERP_SERVICE_TOKEN="$EVALUATION_TOOL_SERVICE_TOKEN" \
SCORE_TOOL_SESSION_SECRET="$SCORE_TOOL_SESSION_SECRET" \
SCORE_TOOL_ENV=development \
ERP_EMBEDDED_PATH=/evaluation-tool \
ERP_ALLOWED_PARENT_ORIGIN=http://127.0.0.1:5173 \
SCORE_TOOL_COOKIE_SECURE=false \
.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8010
```

评价中心导航、系统评价独立/内嵌页面和 ERP 登录桥接继续保留；游戏 ID、辅助手机号、工作人员服务历史和手动分析配置已可用。游戏 ID 原文和辅助手机号继续加密保存，解绑、停用、人员更换和配置换版只新增历史，不做硬删除。

私有媒体存储、校验和读取原语已经建立；系统评价处理的本地 E2E 会覆盖截图上传、任务创建、OCR/评分、审核、发布和人工整改。旧 game-score-tool 的 `data/`、`uploads/`、`runtime/` 和 SQLite 未读取、未迁移、未修改或删除，ERP PostgreSQL 仍是新业务数据的唯一主记录。教学日报、周报、营期问卷和组合工作台的 Stage 2 本地联合验收已通过。

管理员可用只读媒体清单核对数据库记录、缺失文件、哈希不一致和未登记孤儿文件。命令入口是 `python -m app.cli.evaluation_media_manifest`；正常执行只输出计数和不透明 ID，绝不删除文件。需要落盘时，`--json-output` 必须使用配置的隔离目录内绝对路径，文件权限固定为 `0600`：

```bash
PYTHONPATH=apps/api .venv/bin/python -m app.cli.evaluation_media_manifest \
  --json-output "$PWD/runtime/evaluation-quarantine/manifests/latest.json"
```

默认运行不修改数据库。只有本地 development/test 环境可显式增加 `--record-status`，把缺失或哈希异常写回状态；该选项仍不会移动或删除任何媒体。

生产 Docker/NAS 整合仍延期到全部 ERP 项目完成后

本单元的三服务浏览器门禁由普通本地进程运行，不调用 Docker：

```bash
SCORE_REPO=/Users/skymoonzyj/Documents/game-score-tool \
  scripts/run-evaluation-page-e2e.sh
```

启动器默认使用 API `8000`、Web `5175` 和评分工具 `8010`。若这些端口已由本机开发服务占用，可显式设置
`E2E_API_PORT`、`E2E_WEB_PORT` 和 `E2E_SCORE_PORT` 使用空闲端口；启动器不会终止不属于本次运行的进程。

## 本地评价存储初始化

评价上传与清理需要 `EVALUATION_MEDIA_STORE_ID`，且媒体、隔离目录均须具有匹配的身份标记。API、调度器和初始化 CLI 的相对目录都以仓库根目录解析，与启动工作目录无关。普通启动不会自动初始化身份；缺少或不匹配时，清理停止并保留记录。

仅在确认本地 `EVALUATION_MEDIA_ROOT`、`EVALUATION_MEDIA_QUARANTINE_ROOT` 指向正确目录、并已加载其余应用环境变量后，首次初始化可运行：

```bash
export EVALUATION_MEDIA_STORE_ID="$(python -c 'from uuid import uuid4; print(uuid4())')"
PYTHONPATH=apps/api python -m app.evaluations.media_store --initialize-store-identity
PYTHONPATH=apps/api python -m app.evaluations.media_store --verify-store-identity
```

请将生成的 UUID 保存到本机应用环境，后续 API 与调度器使用同一个值，不要每次启动重新生成。已有身份的存储必须沿用原 UUID；不匹配时应检查目录配置，不能覆盖标记。上述命令在仓库根目录执行，仅用于已确认的本地存储；NAS 身份初始化由发布脚本在核对实际挂载后完成。

## 系统评价处理本地 E2E

Task 8 的总入口是 `scripts/run-system-evaluation-e2e.sh`。它不调用 Docker/Compose，也不启动或创建 PostgreSQL；运行拓扑是四个本地应用进程加一个外部 PostgreSQL：ERP API、ERP Web、score-tool Web、score-tool Worker，以及 `TEST_DATABASE_URL` 指向的隔离数据库。启动器会跟踪四个子进程、在退出时清理它们和本次运行的临时目录，并在浏览器启动前拒绝不安全数据库、占用端口、缺失密钥、契约不一致或服务未就绪的情况。

### 前置条件与环境变量

先在本机准备 Python 3.12、Node.js/npm、可运行的 PostgreSQL、ERP API 依赖、`apps/web/node_modules` 和 Playwright Chromium；也可以设置 `PLAYWRIGHT_EXECUTABLE_PATH` 指向本机 Chrome。数据库必须先存在且可由当前用户迁移，数据库名严格为 `erp_e2e` 或以 `_test` 结尾；播种会清理并重建隔离测试数据，不得指向开发或生产库。

在 ERP 工作树根目录设置以下必需变量。`TEST_DATABASE_URL` 只接受 PostgreSQL URL；`FIELD_*` 三项必须是各自独立、可解码为 32 字节的 URL-safe Base64；三个评价服务 token 均须至少 32 个非空白字符且彼此不同：

```bash
export TEST_DATABASE_URL='postgresql+psycopg://postgres@127.0.0.1:55439/housing_reporting_test'
export E2E_PYTHON_BIN='/Users/skymoonzyj/Documents/new project3/.venv/bin/python'
export FIELD_ENCRYPTION_MASTER_KEY="$("$E2E_PYTHON_BIN" -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
export FIELD_LOOKUP_KEY="$("$E2E_PYTHON_BIN" -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
export SESSION_TOKEN_PEPPER="$("$E2E_PYTHON_BIN" -c 'import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())')"
export EVALUATION_TOOL_SERVICE_TOKEN="$("$E2E_PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
export EVALUATION_TOOL_WEB_SERVICE_TOKEN="$("$E2E_PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
export EVALUATION_TOOL_WORKER_SERVICE_TOKEN="$("$E2E_PYTHON_BIN" -c 'import secrets; print(secrets.token_urlsafe(32))')"
```

上述每次命令替换都会生成新的本机值。不要把真实凭据提交到 Git。启动器会在迁移前验证三 token 是否缺失或复用；ERP Web 进程不会收到任何评价服务 token。

以下变量可按本机路径或端口覆盖，未设置时使用脚本默认值：

- `E2E_PYTHON_BIN`：ERP Python；优先使用工作树内 `.venv/bin/python`，linked worktree 没有独立环境时自动回退到 Git 主仓根目录的 `.venv/bin/python`。
- `E2E_VITE_BIN`：Vite 可执行文件，默认 `apps/web/node_modules/.bin/vite`。
- `SCORE_REPO`：评分工具 Git 工作树根目录，默认 ERP 工作树的同级 `game-score-system-evaluation-processing`。
- `E2E_SCORE_PYTHON_BIN`：评分工具 Python，默认 `/Users/skymoonzyj/Documents/game-score-tool/.venv/bin/python`；在其他机器上应显式改成实际路径。
- `E2E_API_PORT`、`E2E_WEB_PORT`、`E2E_SCORE_PORT`：默认分别为 `8005`、`5180`、`8011`，三者必须都空闲。
- `E2E_SCORE_SESSION_SECRET`：可选的 score-tool 会话 secret；若覆盖，至少 32 字节且仅用于本机回环地址。

启动器会把 `APP_ENV=test` 和 `DATABASE_URL=$TEST_DATABASE_URL` 传给 ERP CLI，先执行迁移，再运行每个视口/规格组合的 `seed_e2e` 与 `seed_system_evaluation_e2e`。不需要手工向 score-tool 旧目录放入截图。

`EVALUATION_MEDIA_MAX_BYTES`、`EVALUATION_MEDIA_MAX_PIXELS` 和 `EVALUATION_MEDIA_PREVIEW_MAX_EDGE` 可沿用 `.env.example` 的默认限制；启动器未显式覆盖时分别使用 20 MiB、50,000,000 像素和 1600 边长上限。启动器会固定本地 `SESSION_COOKIE_SECURE=false`，不要把这些本地值用于生产。

### 契约检查与启动

跨仓库 v2 契约检查要求两个参数都是绝对 Git 工作树根目录，并用 `cmp` 逐字节比较 ERP 与 score-tool 的 `evaluation_integration_v2.json`；总入口会自动执行，也可以单独运行：

```bash
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing'
SCORE_REPO='/Users/skymoonzyj/Documents/new project3/.worktrees/game-score-system-evaluation-processing'
bash scripts/check-evaluation-v2-contract.sh "$PWD" "$SCORE_REPO"
```

准备好上面的变量后运行完整八条浏览器旅程：

```bash
cd '/Users/skymoonzyj/Documents/new project3/.worktrees/system-evaluation-processing'
bash scripts/run-system-evaluation-e2e.sh
```

四个应用进程的凭据边界固定如下：ERP API 读取 ERP 配置中的三项 `EVALUATION_TOOL_*` token；ERP Web 不读取这些 token；score-tool Web 只收到 `ERP_SERVICE_TOKEN=$EVALUATION_TOOL_SERVICE_TOKEN`（v1）和 `ERP_WEB_SERVICE_TOKEN=$EVALUATION_TOOL_WEB_SERVICE_TOKEN`（Web）；score-tool Worker 只收到 `ERP_WORKER_SERVICE_TOKEN=$EVALUATION_TOOL_WORKER_SERVICE_TOKEN`（Worker），不会把任一 token 当作另一身份的后备值。

### 临时媒体与 Worker 就绪

启动器用 `mktemp` 创建一次性根目录（默认位于 `$TMPDIR` 或 `/tmp/system-evaluation-e2e.XXXXXX`），并把 ERP 的 `EVALUATION_MEDIA_ROOT`、`EVALUATION_MEDIA_QUARANTINE_ROOT` 和 Worker 的 `SCORE_WORKER_TEMP_ROOT` 分别指向其中的 `media/`、`quarantine/`、`worker/`。测试种子只写入这些目录下的 `.system-evaluation-e2e` 合成 PNG；正常退出或失败清理会删除整个临时根目录。不要把这些变量改到评分工具旧的 `data/`、`uploads/`、`runtime/`，启动器不会读取、迁移、修改或删除那些目录。

启动器还会为本次临时目录生成独立 UUID，在迁移与 API 启动前显式初始化两个身份标记，并向 ERP CLI/API 传入同一个 `EVALUATION_MEDIA_STORE_ID`。初始化失败会立即退出，不启动 API；无需手工初始化 E2E 的临时目录，也不会沿用本机或 NAS 存储的身份。

Worker 启动后，启动器会以同一 Worker 环境执行 `worker.py --check`；该检查必须成功调用 v2 capabilities，并把标准输出精确写成一行 `score-worker ready`。随后还必须确认长驻 Worker PID 仍存活，才开始浏览器测试；带额外文字的近似 marker、旧日志中的 marker、capabilities 失败或进程提前退出都会使启动器失败。

### 四视口与延期边界

每个组合都会重新播种隔离夹具，然后运行 `system-evaluation-processing.spec.ts` 和 `system-evaluation-privacy.spec.ts`，四个 Playwright 项目为 `desktop-1440`（1440×900）、`desktop-1024`（1024×768）、`tablet-768`（768×1024）和 `mobile-360`（360×800），共八次浏览器运行。旅程覆盖选目标、上传槽位、Worker 完成、独立审核人发布、学员/监护人历史，以及人工录入和驳回后整改；首版自动结果在审核前保持 `needs_review`。

score-tool 的 `tests/test_adapters.py` 与 `tests/golden/*.json` 是脱敏适配器回归测试；通过 golden tests 只说明固定输入的结果契约未漂移，不会创建适配器认证记录，也不代表生产自动发布已获认证。首版发布仍须由不同账号独立审核；Docker/NAS、生产媒体卷、密钥轮换、备份恢复和录像/GPU 入口继续延期。

## Stage 2 本地联合验收（已通过）

评价中心 Stage 2 的官方非容器入口是：

```bash
bash scripts/run-stage2-local-acceptance.sh
```

最近一次留存摘要记录 `status=passed`、`cleanup_completed=true` 和退出码 0，并覆盖四个固定视口下的系统评价、教学日报/周报、营期问卷及组合工作台。完整前置条件、脱敏摘要和隔离清理边界见 [Stage 2 本地联合验收说明](docs/operations/stage2-local-acceptance.md)。该证据只支持本地非容器结论。

## 本地 disposable Compose 验收

本地 disposable Compose 验收已通过，官方入口是：

```bash
bash scripts/run-local-container-acceptance.sh
```

完整运行与安全边界见 [本地 disposable Compose 验收说明](docs/operations/local-container-acceptance.md)。真实容器通过必须以当次摘要同时记录 `status=passed`、`cleanup_completed=true` 且全部检查项为 `passed` 为证；Docker daemon 未运行时不得声称真实容器验收通过。本工作树已留存符合该标准的当次摘要，覆盖镜像构建、空卷数据库安全检查与迁移、代理/API 健康、迁移链及 schema manifest，并确认本次容器、网络和卷已清理。

NAS 部署、外部 TLS、生产密钥轮换、真实备份恢复和生产数据迁移仍延期。

## 教练与家庭门户

登录后的统一工作台按账号实际能力显示任务卡片。教练可进入“教练下一期学员名单”，默认打开本人获权的最近开营时间组，并可切换更晚营期；名单不提供身份证、手机号、邮箱、学费或付款信息。监护人可在“家庭教学与多学员切换”中查看有效关系内的未成年和成年学员，成年不会自动解除既有关系。拥有启用账号的学员可查看“学员本人教学信息”；未成年账号仍受监护人就绪规则约束。

“本人付款与退款”只按当前付款账号显示记录。绑定同一学员的另一名监护人付款、无付款账号的手工记录、内部备注和导入原文均不可见。关系结束后付款仍属于原付款人，但关联学员显示为“历史付款”。已发布的日报、周报可在已授权的家庭教学页面查看；未发布草稿、驳回理由、待审版本和内部权限来源不会投影到家庭端。

本地 E2E 另增加以下固定测试账号，仅存在于 `erp_e2e`：

- 监护人：`19990000005 / E2E-guardian-password-2026`
- 成年学员：`19990000016 / E2E-student-password-2026`

系统评价截图上传、任务处理、OCR/自动评价、审核和发布已在本地隔离环境开放；支付平台适配、公安在线自动报送和录像/GPU 仍未开放。管理员入住、调房、退房对话框、动态房态管理 UI 与本地实名入住报送已实现，住宿生命周期和实名报送四视口浏览器旅程已通过；Docker/NAS 打包仍在最终阶段，不把容器、生产密钥轮换、生产备份恢复或 NAS 迁移标记为已验证。

## 招生报名与付款运营

招生功能位于“招生教学”的付款与退款、待分配、正式报名和营期学员页面。付款金额、机器凭证、退款、超额报名、转班和退营分别受独立权限控制；客服即使能查看名单，也不会自动获得金额或操作权限。

### 机器付款接口

管理员在“付款与退款 → 接口凭证”中创建接口账号，然后创建、轮换或停用机器凭证。新凭证的 secret 只显示一次，应立即交付到付款系统的秘密存储；轮换时先创建新凭证并切换调用方，验证成功后再停用旧凭证。停用接口账号会停止该账号下全部凭证。

付款系统向 `POST /api/v1/payment-imports` 发送原始 JSON 字节，并提供 `X-Service-Account`、`X-Timestamp`、`X-Nonce` 和 `X-Signature`。签名规范串没有末尾换行：

```text
POST
/api/v1/payment-imports
<Unix 秒时间戳>
<本次唯一 nonce>
<原始请求体的 SHA-256 小写十六进制>
```

使用凭证 secret 的 UTF-8 字节对上述规范串执行 HMAC-SHA256，结果以小写十六进制放入 `X-Signature`。时间戳允许前后五分钟，nonce 在同一凭证下 24 小时内不得重复。业务重试必须保持订单号、幂等键和请求内容不变，但使用新 nonce 重新签名；同一订单或幂等键的内容变化会进入冲突队列，绝不会覆盖原付款。日志和审计中不得写入完整手机号、原始请求体、签名或 secret。

### 手工付款、分配与退款

1. 管理员在付款页选择“登记付款”，把付款明确关联到学员 ID 或现有待分配单，并填写金额、付款时间和备注。分期付款可以继续关联同一待分配单。
2. 付款成功后进入待分配。唯一学员付款会直接成为“待报名”；监护人关联多名学员或未知付款人保持“待匹配”，必须由管理员选择学员。
3. 管理员选择开放营期和班级后最终确认报名。部分付款允许报名，详情会显示欠款；多付款独立显示，不会被误判为普通已付清。
4. 手工退款只处理指定原付款，退款不自动取消报名。需要释放班级名额时必须另行执行取消报名或退营。

正式报名详情提供三种相互独立的操作：转班仅在同一营期内原子移动名额并保留历史；取消报名不退款，并把付款重新留给待分配；退营先计算预览，再由管理员二次确认。退营可选择“按剩余日期退款”或“退还全部剩余已付款”，多笔付款按最近付款优先拆分；预览后若付款或退款发生变化，系统会拒绝旧预览并要求重新计算。

### 备份、升级与回滚

每次迁移前先执行数据库备份，并在空测试数据库完成恢复演练。若新应用需要回滚，先停止写入流量并回滚应用代码，但不回退已经提交的付款数据，也不对已落库的金额、退款、报名历史执行 Alembic 降级。旧版本无法安全读取新结构时，应保持维护只读状态，修复或前滚后再开放写入。

## 测试与验收

“天赋评测 2”的四套专项、正式／练习隔离、20 秒试练、3 秒倒计时、60 秒运行及真实设备限制见[功能说明](docs/features/esports-assessments.md)。当前四套开放状态均为关闭，尤其两个手机套系尚未完成真实 iPhone Safari 与 Android Chrome 验收；单元测试、模拟触摸、页面存在或构建成功都不能替代最终接口、恢复、浏览器／设备矩阵和发布证据。

完整检查会创建并清理独立的 `esports-erp-e2e` Compose 项目，不会停止开发栈：

```bash
bash scripts/check.sh
```

检查内容包括后端测试、Ruff、mypy、前端测试、构建、ESLint、数据库脚本语法、三套 Compose 配置、迁移、调度器单次执行、安全 E2E 种子、机器签名/防重放/内容冲突、招生完整旅程、四种响应式视口，以及真实备份和空库恢复后的迁移版本、必要表和行数核对。

E2E 固定账号只允许存在于数据库名为 `erp_e2e` 或以 `_test` 结尾的隔离测试库：

- 管理员：`19990000001 / E2E-admin-password-2026`
- 客服：`19990000004 / E2E-customer-password-2026`
- 监护人：`19990000005 / E2E-guardian-password-2026`
- 教练：`19990000003 / E2E-coach-password-2026`
- 成年学员：`19990000016 / E2E-student-password-2026`

固定夹具使用可识别 UUID 分区：`e100…` 为基础人员、`e110…` 为登录账号、`e120…` 为基础监护关系，`e200…` 至 `e231…` 为招生学员、营期班级、报名和机器凭证。每次播种会先清理隔离测试库的运行数据，再以相同 UUID 重建，因此重复执行返回相同账号和业务夹具，同时不会让前一次浏览器旅程污染下一次验收。

测试种子命令同时要求 `APP_ENV=test`，并使用 SQLAlchemy PostgreSQL dialect 生成驱动连接参数；只有有效连接目标的数据库名严格为 `erp_e2e` 或以 `_test` 结尾的隔离库才会被接受。原始 URL 会在 SQLAlchemy 处理前拒绝额外裸 `@`、空或歧义主机、畸形括号 IPv6/端口、空白或控制字符、反斜杠和 fragment；凭据中的 `@` 必须写成百分号编码 `%40`，无 authority 的本地 PostgreSQL socket URL 仍可使用。还会在连接和迁移之前拒绝数据库目标选择器查询参数（包括 `dbname`、`database`、`service` 和 `servicefile`）以及未审查的查询参数；`application_name` 和 `sslmode` 可使用。不要把这些测试凭据用于开发或生产账号。

如果本机已有 Chrome、但 Playwright 的 Chromium 尚未下载，可仅在本机验收时指定：

```bash
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' bash scripts/check.sh
```

## 数据库备份与恢复演练

备份必须明确给出来源 URL 和输出文件。脚本使用 custom 格式、不保存所有者，并生成同名 `.sha256`：

```bash
DATABASE_URL='postgresql://erp:密码@127.0.0.1:5432/erp' \
  bash scripts/backup-db.sh ./backups/phase1b-teaching-verification.dump
```

恢复只允许目标名以 `_test` 结尾，目标必须为空；目标不存在时脚本会创建。脚本先校验 SHA-256，再恢复并依据当前 schema manifest 核对迁移版本、清单内每张必要表及关键表行数：

```bash
DATABASE_URL='postgresql://erp:密码@127.0.0.1:5432/erp' \
  bash scripts/restore-db.sh ./backups/phase1b-teaching-verification.dump \
  erp_phase1b_teaching_restore_test
```

脚本拒绝恢复到来源数据库，也不会默认选择或覆盖生产数据库。

## NAS 生产部署

在仓库根目录创建不纳入 Git 的 `secrets/` 目录，并设置仅管理员可读。需要五个文件：

| 文件 | 内容 |
| --- | --- |
| `secrets/postgres_password` | PostgreSQL 强密码 |
| `secrets/database_url` | `postgresql+psycopg://erp:同一强密码@db:5432/erp` |
| `secrets/field_encryption_master_key` | 32 字节 URL-safe Base64 |
| `secrets/field_lookup_key` | 另一个 32 字节 URL-safe Base64 |
| `secrets/session_token_pepper` | 第三个 32 字节 URL-safe Base64 |

以下命令只解析并检查生产覆盖配置，不启动服务、不迁移数据库、不构建镜像：

```bash
docker compose -f compose.yaml -f compose.prod.yaml config --quiet
```

NAS 正式发布必须从主仓库调用受保护的启动器，并显式选择已审核的发布源：

```bash
scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
```

不得以手工复制、直接 Compose 编排或临时 Docker 命令绕过启动器的预检、备份、迁移、健康检查和恢复验证。启动器任一必需检查失败都属于发布阻塞。

生产覆盖配置具有以下约束：

- API 与 Web 镜像以非 root 用户运行
- API、Web 与代理使用只读根文件系统、固定临时目录和 `no-new-privileges`
- 独立 scheduler 服务与 API 使用相同数据库和秘密，按间隔幂等执行营期状态流转
- 只发布反向代理端口；数据库、API 和 Vite 均不发布
- 运行秘密通过 Docker secrets 文件注入，不写进镜像或 Compose 环境明文
- Nginx 设置 CSP、`nosniff`、无引用来源、拒绝嵌入和 25 MB 请求上限
- 数据库、API、Web 和代理均配置健康检查

在 NAS 自带反向代理中，把 HTTPS 域名转发到 ERP 代理端口（默认只绑定 `127.0.0.1:8080`），并保留 `Host`、`X-Real-IP`、`X-Forwarded-For` 和 `X-Forwarded-Proto`。如果反向代理不在同一台主机，再显式调整 `ERP_BIND_ADDRESS`。正式开放前必须完成一次备份和空测试库恢复演练。

## 阶段 1B 明确边界

当前可用：

- 人员、学员、监护人、工作人员和访客档案，以及名单成年/未成年状态；
- 未成年学员待绑定监护人的红色提醒；
- 有效成年监护人的实名资料、独立启用账号和监护人角色校验；
- 监护关系历史、成年学员继续绑定，以及一个监护人关联多个学员；
- 最后一名有效监护人失效后，未成年学员账号自动停用并撤销会话；
- 账号开通/停用/启用/重置，家庭和对象范围权限，实名敏感查看与安全审计；
- 电竞项目和职业队伍的增改、排序、停用与历史保留；
- 营期日期和具体时间、独立自动开营/结营开关、合法手工状态流转和状态历史；
- 普通班、跟随职业队伍训练的菁英班、人数上限和精确到分的默认学费；
- 教练资格筛选、主教练/助教分配、有效期、结束原因和历史记录；
- 教练通过 `assigned_class` 范围只读自己当前分配的营期和班级；客服可全局只读教学配置；
- 通用 HMAC 付款导入、五分钟时间窗、24 小时 nonce 防重放、双幂等和内容冲突保留；
- 管理员手工登记付款、分期归集、待匹配/待报名队列、学员匹配和原子确认报名；
- 普通班与菁英班报名、部分付款欠款、多付款、容量保护及有理由的独立超额权限；
- 同营期转班、取消不退款、指定付款手工退款，以及按剩余日期或全部剩余已付款的退营二次确认；
- 客服名单金额脱敏、报名历史、营期学员页签和四视口招生完整旅程；
- 教练下一期学员名单、家庭教学与多学员切换、学员本人教学信息和本人付款与退款；
- 多身份工作台、对象范围撤权、窗口聚焦重新授权和四视口门户完整旅程；
- 1440、1024、768 和 360 四档响应式界面。

当前不可用：站内项目选择与支付、微信/支付宝等支付平台适配器、公安在线自动报送、录像或 GPU 分析。系统评价截图上传与自动评价只完成本地隔离验收，尚未完成 Docker/NAS 或生产验证。管理员入住、调房、退房对话框、动态房态管理 UI 和本地实名入住报送已实现，住宿生命周期与实名报送四视口浏览器旅程已通过；不要把尚未实现或未完成生产验证的模块用于真实运营。
