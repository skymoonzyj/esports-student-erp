# 自助注册本地运行与验收

本文只描述自助注册模块的本地开发和隔离验收方式。完整本地门禁与四视口 8 组 E2E 已通过；真实短信/邮件服务商、第三方实名、项目选择、订单和支付均不在本单元内，Docker/NAS 仍未验证。

## 已实现范围

- 学员、监护人、工作人员申请人和临时访客可用手机号或邮箱发起注册。
- 联系方式验证后使用独立注册会话完善实名资料；正式账号开通后重新登录 ERP。
- 可安全认领资料完全一致且尚无账号的人员；已有账号返回登录或找回密码路径；跨人员命中进入获权审核队列。
- 未成年学员在至少一名成年监护人使用独立账号确认关系前保持待完善状态；成年学员也可选绑监护人。
- 工作人员申请批准前只有基础账号，获权管理员审核后才创建工作人员资料并授予固定最低角色。
- 邮箱唯一账号可进入基础 ERP，并可在个人资料中补充已验证手机号；报名、付款和入住写入仍执行已验证手机号门禁。
- 密码找回不披露账号是否存在；有效重置会撤销旧正式会话和注册会话。

项目选择与支付仍未开放，真实支付跳转、支付回调和第三方支付适配器继续属于后续单元。

## 数据库与保留周期

本模块由 `0017_self_service_registration` 建立注册申请、验证码、注册会话、登录标识、监护邀请、工作人员申请和确认记录；`0018_registration_review_metadata` 补充冲突审核决策与原因；`0019_guardian_invitation_tokens` 把对外监护邀请令牌与后台管理 UUID 分离，数据库只保存带域隔离的 keyed digest；`0020_registration_review_pending_guardian` 允许已审核通过的未成年冲突申请安全回到待监护确认状态，避免后台审核绕过监护门禁；`0021_registration_review_candidate_binding` 把等待监护确认的申请固定到本次已审核候选人员，防止等待期间重新匹配到未经审核的人员。

- 活跃注册申请按最后一次有效活动续期，最长保留窗口为 30 天；过期清理会清除临时密码、实名和联系方式密文等申请秘密。
- 监护人邀请有效期为 7 天；重发会撤销旧邀请并创建新邀请。
- 验证码 10 分钟过期、最多尝试 5 次、同一标识 60 秒内不能重发，并同时受标识与来源限流。
- 正式业务人员、账号、监护关系和审核结果是长期业务记录，不随注册临时数据清理。

需要手工执行一轮有界清理时，在仓库根目录使用与 API 相同的安全环境变量运行：

```bash
PYTHONPATH="$PWD/apps/api" .venv/bin/python \
  -m app.cli.cleanup_registration_applications --limit 500
```

命令只处理到期的注册临时数据与邀请，每轮最多 500 条；不要把它指向非隔离数据库做开发试跑。

迁移升级会在修改 schema 前校验旧手机号、邮箱、重复摘要和加密密钥；不满足条件时原子拒绝。升级到 `0019_guardian_invitation_tokens` 会撤销旧版待处理 UUID 邀请链接并清除其临时联系方式，避免继续接受可猜测的管理标识。`0020_registration_review_pending_guardian` 扩展审核状态约束；`0021_registration_review_candidate_binding` 增加带外键和状态约束的审核候选绑定。若升级时已存在无法安全推断候选的“审核通过且待监护确认”申请，或降级时仍存在活动候选绑定，迁移会先锁定申请表并在修改 schema 前原子拒绝。其他受保护降级在存在新式邮箱账号、注册业务行、新监护人资料、角色或权限覆盖时拒绝执行；一旦存在监护邀请历史，也不能降级回不安全的邀请格式。

## 会话隔离

- 注册会话 Cookie 为 `erp_registration_session`，路径限定为 `/api/v1/registration`，使用独立注册会话 pepper 和独立 CSRF。
- 正式会话 Cookie 为 `erp_session`，使用正式会话 pepper 和正式 CSRF。
- 注册会话不能访问正式 ERP API，正式会话也不能读取其他人的注册申请。
- 账号完成开通或密码重置后，相关旧会话立即撤销；浏览器必须重新登录取得新正式会话。

## 本地测试发送器

官方 runner 使用受限的本地测试发送器，不调用外部短信或邮件网络。它仅在以下条件同时满足时启用：

1. `APP_ENV=test`；
2. API 显式声明绑定回环地址 `127.0.0.1`；
3. `REGISTRATION_TEST_MAILBOX_DIR` 指向 runner 新建的私有临时目录；
4. 目录内存在 runner 创建的 `.registration-test-mailbox` sentinel。

发送器把记录追加到权限为 `0600` 的 JSONL mailbox。每条记录只包含 challenge UUID、目的地的固定 SHA-256 测试别名（截断散列）、purpose 和一次性值，不写完整手机号或邮箱。该别名只用于本地 runner 定位测试消息，不作为密码学不可逆或长期匿名化保证。Playwright 通过独立辅助进程按 challenge 与别名读取；应用没有读取验证码的 DOM、HTTP 或调试接口。runner 退出时会终止本次 API/Web 子进程并删除整个临时目录。

不要在开发服务器上手工设置 `REGISTRATION_TEST_MAILBOX_DIR`，也不要把临时 mailbox、验证码、截图、trace 或服务日志提交到 Git。

## 环境要求

准备名称明确以 `_test` 或 `_e2e` 结尾的独立 PostgreSQL 数据库。不得指向开发共享库或任何生产库。runner 需要：

- `TEST_DATABASE_URL`
- `FIELD_ENCRYPTION_MASTER_KEY`
- `FIELD_LOOKUP_KEY`
- `SESSION_TOKEN_PEPPER`
- `REGISTRATION_VERIFICATION_PEPPER`
- `REGISTRATION_SESSION_TOKEN_PEPPER`
- `EVALUATION_TOOL_SERVICE_TOKEN`
- `EVALUATION_TOOL_WEB_SERVICE_TOKEN`
- `EVALUATION_TOOL_WORKER_SERVICE_TOKEN`

三个会话/注册 pepper 必须是彼此不同的 32 字节 URL-safe Base64 值；三个评价桥接服务令牌也必须彼此不同且不少于 32 个非空白字符。测试值只能用于隔离本地数据库。

如果当前 Playwright 包对应的 Chromium 缓存尚未安装，可把 `PLAYWRIGHT_EXECUTABLE_PATH` 指向本机可信且可执行的 Chrome/Chromium；也可按项目依赖版本安装对应浏览器。不要把个人绝对路径写入脚本或提交记录。

本地 runner 由浏览器直接连接回环 API，不经过反向代理，因此 `TRUSTED_PROXY_CIDRS` 应保持为空并以 socket peer 作为限流来源。后续 Docker/NAS 阶段若由 Nginx 转发，必须把该值设置为实际代理网络的最小 CIDR，并验证不同客户端的 `X-Real-IP` 会产生不同来源摘要；不得信任 `0.0.0.0/0`、`::/0` 或任意公网来源。当前 Compose 尚未接入该生产配置，因此仍属于部署阻断项，不在本地完成结论内。

## 官方验收命令

在仓库根目录导出上述环境变量，然后运行：

```bash
E2E_PYTHON_BIN="$PWD/.venv/bin/python" \
  bash scripts/run-self-service-registration-e2e.sh
```

runner 会依次执行数据库安全检查、迁移、确定性基础 seed、自助注册 seed，并在以下四个 Playwright 项目中串行运行主旅程和隐私旅程：

- `desktop-1440`
- `desktop-1024`
- `tablet-768`
- `mobile-360`

共 8 组组合必须全部通过。主旅程覆盖手机号/邮箱注册、既有档案认领、未成年监护激活、成年可选监护、工作人员审批、找回密码、补手机号、餐饮引导差异和重新登录；隐私旅程覆盖响应扫描、脱敏审核、权限即时撤销后的私有错误和浏览器缓存清理。

runner 成功时不输出服务日志；失败时只显示经过手机号、邮箱、身份证、Cookie、CSRF、验证码、密码、密文、数据库 URL 和绝对路径脱敏后的有限日志尾部。

## 手动启动与停止

日常页面开发可按 README 的“不使用 Docker 的本地调试”启动 API 和 Web，但注册验证码不会自动可用，因为普通开发配置没有外部发送器。需要验证完整注册状态机时使用官方 runner。

无需单独执行停止命令：runner 的退出 trap 会终止它启动的 API/Web 进程并清理 mailbox。若开发者手动启动服务，应在各自终端使用 `Ctrl-C` 停止，并确认回环端口已释放。

## 当前边界

- 项目选择与支付仍未开放。
- 真实短信/邮件、第三方实名和公安在线 API 尚未接入。
- Docker/NAS 仍未验证，生产密钥轮换、备份恢复和 NAS 数据迁移不属于本地通过结论。
- GPU/录像、天赋测评和正式生产监控继续延期。
