# 人员档案动态模块失效修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. 本次是已确认根因的线上缺陷修复，按用户既有直接修复及 NAS 发布授权连续执行。

**Goal:** 修复发布后旧页面加载档案报告脚本 404 导致整页崩溃，保留档案、权限与草稿。

**Architecture:** 标准部署中合并经过校验的近期旧 hashed assets 到候选 Web 镜像；局部报告错误边界及中文路由兜底处理仍可能出现的网络/过期错误。无自动刷新、无数据库修改。

**Tech Stack:** React/React Router、Vite、Python 标准库、Docker/Nginx。

**Spec:** 本文为缺陷约束。现场旧 `/assets/AssessmentReportsPanel-dMBIl6i7.js` HTTP404，当前镜像仅 B46y8Bs1；报告只有 Suspense，无错误边界，发布脚本只部署当前 dist。

## Global Constraints

- 仅当前隔离发布源；保留所有既有脏改动，不提交、推送或重置。
- 不读取或改动真实人员档案；浏览器回归使用合成数据。
- 不自动刷新、不改变权限，不因一个报告模块失败清空人员页草稿。
- 只通过 scripts/deploy-nas.sh 发布；保留原预检、备份、迁移和恢复门禁。
- 旧静态资源最多保留 7 天，合并目录最多 256 MiB、5000 文件；超限/冲突/不可信输入阻断候选发布，不悄悄覆盖或删除仍受保护资源。

## Review Focus

- 旧页面跨一次或多次发布仍能读取相同 hash；真实 HTTP 验证，不能只测源文本。
- 非法归档路径、链接、同名不同内容、不合规历史镜像不能进入候选镜像。
- 历史 manifest 过期有界，不在每次发布无限延长已退役文件寿命。
- 报告 import reject 时人员档案和未提交输入保留；无权限不请求报告模块。
- 中文兜底不泄露错误栈，刷新只有用户明确点击，不自动循环。

### Task 1: 档案局部保护与中文路由错误页

**Files:** 新建 apps/web/src/shared/ModuleErrorBoundary.tsx 及测试、apps/web/src/app/RouteErrorPage.tsx 及测试；修改 apps/web/src/people/PersonDetailPage.tsx、对应测试、apps/web/src/app/router.tsx。

**Interfaces:** ModuleErrorBoundary 接收 children、模块名称；用于报告 Suspense 外层，key 按当前账号和 personId 隔离。路由顶级错误页不依赖已崩溃子组件。

- [ ] 先写 RED：真正 rejected lazy import 不应击穿档案，草稿与档案标题仍可见；无报告权限不调用 loader。路由异常中文显示且不暴露堆栈。
- [ ] 实现最小错误边界；显示报告暂时无法加载和刷新提示。仅用户点击刷新，明确未保存内容风险；不做定时/自动刷新。React.lazy 拒绝缓存不能假装 reset 即重试成功。
- [ ] 路由加入顶级中文兜底，不改路由权限结构。切换人员/账号不会沿用旧错误状态。
- [ ] 运行相关人员、报告、路由测试与 lint；记录 RED/GREEN 和修改文件。根任务负责最终全量前端及真实生产构建浏览器验证。

### Task 2: 标准部署保留旧静态资源

**Files:** scripts/nas-web-assets.py、scripts/test_nas_web_assets.py、scripts/deploy-nas.sh；按需修改 scripts/test_deploy_nas.py、apps/web/nginx.conf。不能修改业务前端或数据库。

**Interfaces:** 部署构建候选 web 后、切换前调用 helper。helper 只操作明确项目的候选镜像和受信历史镜像，创建自己的临时容器导出静态 assets（不启动应用），校验并构建继承候选的镜像。新 index 不被旧版本替换；manifest 在非公开目录。所有临时容器/目录有 finally 清理。

- [ ] 先写 RED：当前发布后旧hash缺失；合并后候选新资源和受保护旧资源同路径可读。覆盖多代保留/过期、碰撞、非法路径/链接、容量/数量界限、失败清理。
- [ ] 实现独立、标准库 helper，固定候选/历史 image ID 防止标签变化。首次引入只接受显式历史镜像白名单，核对本项目最近 7 天且匹配 release metadata 的既存镜像（最多 20 个），不自动发现、不拉取任意镜像、不信任任意路径。后续以已验证 manifest 继承，退役资源 TTL 不延长。
- [ ] 标准 deploy 在切换前完成合并与检查，任何失败保留旧运行服务。保持现有预检/备份/恢复规则；新 HTML 使用 revalidation，hash资源保持 immutable，缺失 JS 仍真实404而非HTML。
- [ ] 运行 helper 全部测试、现有 deploy/packaging/guard 相关回归；真实 Docker 合成两代资源 HTTP 测试由根任务安排。记录精确命令与结果。

### Task 3: 总体验证、审查与 NAS 发布

- [ ] 基于本轮开始文件快照做独立审查，不混入其他既有改动。
- [ ] 全量前端测试、lint/build、脚本测试通过；合成浏览器阻断报告chunk验证档案仍可用；真实静态HTTP跨版本读取验证。
- [ ] scripts/deploy-nas.sh --source 当前工作树；必须 exit0、status=complete、加密备份恢复通过。
- [ ] 只读复核用户给出的旧hash返回200且JS类型、新入口正常、匿名API仍拒绝；保留文档证据。
