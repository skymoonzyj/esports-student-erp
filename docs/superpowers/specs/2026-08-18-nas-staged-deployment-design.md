# NAS 空库试运行部署设计

**日期：** 2026-08-18  
**状态：** 已完成 NAS 空库 HTTP 域名 staging（2026-08-18）；HTTPS/生产切换待后续
**目标：** 将当前自助注册工作树快照以空数据库方式部署到 NAS，保留现有 NAS 服务不变。

## 1. 范围与明确边界

本次只部署 ERP 主服务：PostgreSQL、API、营期 scheduler、Web 和 ERP proxy。

包含：

- 当前工作树中已经实现的人员、权限、招生、房态、评价页面和自助注册代码；
- 一个全新的 PostgreSQL 数据卷，不导入本机数据库；
- 2 TB 盘上的数据库、媒体预留区和备份预留区；
- NAS 自带 HTTPS 反向代理接入准备；
- Compose 配置校验、生产镜像构建、迁移、健康检查和最小登录验收。

本次不包含：

- 微信/支付宝或其他真实支付适配器；
- score-tool Web/Worker 的生产容器、GPU、录像分析；
- 公安在线 API；
- 本地历史数据迁移；
- NAS 现有容器、80/443 管理入口或现有 Docker 卷的修改；
- 将本地 E2E 结果宣称为生产验收。

## 2. 源码快照

上传当前工作树快照（包含已确认的未提交改动），但排除：

- `.git/`
- `node_modules/`、`.venv/` 和其他依赖缓存
- `runtime/`、测试产物和本地日志
- 本地 `.env`、`secrets/`、数据库文件和媒体文件

上传前后计算不透明源码摘要；摘要不写入业务日志。远端先上传到按摘要命名的临时发布目录，校验通过后才切换为当前发布目录，避免半成品覆盖。

## 3. NAS 拓扑与存储

NAS 已确认：x86_64、Docker 28.5.2、Compose v2.40.3，2 TB 磁盘挂载为 `/vol1`，Docker 数据根目录为 `/vol1/docker`。

代码和 Compose 文件放在：

`/home/skymoonzyj/esports-erp`

数据库、评价媒体和隔离区使用独立 Docker 命名卷，由 Docker 放在 `/vol1/docker` 下。命名卷不会复用任何既有项目卷。媒体卷会预留目录，但本次不启动 score-tool Worker。

Compose 项目使用独立名称 `esports-erp-staging`。基础覆盖默认让 proxy 绑定 NAS 本机 `127.0.0.1:18080`，不占用现有 8080、5173、5432，也不直接暴露 API 或数据库端口。由于 NAS SSH 禁止 TCP 转发，本次另加载 `compose.nas-lan-staging.yaml`，把 staging proxy 绑定到 NAS 地址 `192.168.31.151:18080`。现有公网映射已使 `http://skymoonzyj.l.cd:18080` 到达 ERP；无端口的 80/443 仍由 fnOS 管理入口占用。

## 4. HTTPS 与受信代理

生产配置保持安全 Cookie。NAS 自带反向代理应把用户提供的 ERP 域名转发到：

`http://127.0.0.1:18080`

反向代理必须提供有效证书并保留 `Host`、`X-Real-IP`、`X-Forwarded-For` 和 `X-Forwarded-Proto`。API 的 `TRUSTED_PROXY_CIDRS` 只写实际反向代理来源的最小 CIDR，禁止使用全网段。

截至部署验收时，`skymoonzyj.l.cd` 已有 DNS A 记录并可解析。ERP staging 基址已设置为 `http://skymoonzyj.l.cd:18080`，登录页和 live 健康接口均从该地址返回 200。当前没有有效 HTTPS 证书，此 HTTP 域名仅用于空库 staging，不能录入真实身份证、手机号、付款或其他敏感数据，也不能作为生产登录验收。

## 5. Secret 与配置

所有 secret 先在 NAS 上以 `umask 077` 创建，不经过源码、Git、命令行参数或普通日志。由于本次 Compose 使用本地 file secret 且 API/scheduler 以 UID 10001 运行，staging release 将 secret 文件设为 `0644`，但其父级项目目录保持 `0700`；正式生产应改用支持 UID/GID/mode 的 secret 管理方式后再启用公网入口。除生产覆盖中已有的五项外，当前代码还需要补齐：

- 注册验证 pepper；
- 注册会话 pepper；
- 评价服务 token；
- 评价 Web token；
- 评价 Worker token。

部署覆盖配置将把这些值通过 `*_FILE` 注入 API/scheduler。评价 Worker 本次不启动，相关 token 仅用于保持配置契约完整，后续容器化时重新核对。

## 6. 执行顺序

1. 只读预检查端口、架构、磁盘和 Docker 状态。
2. 上传源码到临时发布目录并校验摘要。
3. 创建 secrets 和独立命名卷。
4. 渲染 `docker compose config`；任何 secret、路径或端口错误立即停止。
5. 在本机构建并校验 `linux/amd64` 生产镜像，再上传到 NAS x86_64（NAS 侧不再 pull Docker Hub）。
6. 只启动数据库并等待健康检查。
7. 执行 `alembic upgrade head`。
8. 启动 API、scheduler、Web 和 proxy。
9. 执行 Compose 健康检查、schema manifest、Web 首页和 HTTP 域名 staging 验收；HTTPS 登录验收留到证书配置后。
10. 业务初始化完成后再创建首位管理员；不自动写入实名资料。

## 7. 回滚与数据安全

- 任一步骤失败时只停止本次 `esports-erp` 项目，不停止其他 Compose 项目。
- 保留源码发布目录和数据库卷，便于诊断；不得执行 `down -v`。
- 空库阶段不做本地数据恢复；后续迁移必须另行备份、校验和确认。
- 2 TB 盘不是独立备份，首位管理员和基础配置完成后必须另行生成数据库备份。
- 本次 SSH 密码曾在对话中传递，部署完成后应更换并改用 SSH key。

## 8. 验收与停止条件

本次 staging 验收已记录：源码摘要 `32b264cb0825d90f7f777cf3ebdc098646c429e238a9b38e42962abbc56a8f69`、Compose 项目名 `esports-erp-staging`、迁移版本 `0021_registration_review_candidate_binding`、五个容器健康状态、域名 `http://skymoonzyj.l.cd:18080` 的 `/login` 与 live 响应。HTTPS 登录验收和首位管理员创建仍未执行。

以下情况视为未完成，不得宣称 NAS 部署完成：

- DNS/证书未就绪导致无法完成生产安全 Cookie 登录；
- 任一 secret 明文出现在日志或源码；
- 数据库迁移失败或卷落在非 `/vol1/docker` 的未知位置；
- 现有 NAS 容器受到停止、重启或端口冲突影响；
- 将未容器化的 score-tool/GPU/录像功能误标为可用。
