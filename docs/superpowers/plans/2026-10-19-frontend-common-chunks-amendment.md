# ERP 常用公共代码合包补充 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 合并明确的纯图标与日期工具小块，减少常用页面额外请求，同时维持原加载安全边界和真实性能验收。

**Architecture:** 在现有 Vite 构建配置中精确声明 21 叶图标组和 2 模块日期组，不改业务导入、运行时加载器或准备顺序。离线产物检查与隔离生产回归分别验证模块边界和真实表现；通过后回接原计划剩余的恢复、最终审查及 NAS 发布工作。

**Tech Stack:** 现有锁定的 Vite 8／Rolldown、React、TypeScript、Node test、Vitest、Playwright 和既有隔离验收 runner；不升级依赖。

**Spec:** `docs/superpowers/specs/2026-10-19-frontend-common-chunks-amendment-design.md`，用户已于 2026-10-19 确认；未覆盖事项继承原加载优化规格。

**状态：** 本计划待用户审阅，未实施。沿用子任务开发、任务独立复核，串行运行使用数据库的验收。原计划已完成项不重做，旧失败证据与纠错记录不清零。

## Global Constraints

- 首轮只接受上述两个分组。若实际构建发现模块副作用、循环、CSS 归属变化或无法减少原有请求，保留证据并重新评估，不临时扩大白名单或通过修改业务导入关系凑出结果。
- 图标组完整白名单：`download, filter, pencil, trash-2, activity, calendar-days, chevron-down, message-square-text, plane-takeoff, wallet-cards, mail, shield-check, sliders-horizontal, user-plus, user-round, x, clipboard-pen-line, gamepad-2, file-spreadsheet, landmark, refresh-cw`。
- 日期工具包 `common-date` 仅含 `src/shared/date.ts` 与 `src/teaching/datetime.ts`；两个组均 `includeDependenciesRecursively: false`，不移动 helper/runtime。
- 不修改 `router.tsx`、`lazyPages.ts`、`lazyModule.tsx`、权限或业务接口、数据库、日期工具实现、CSS 源文件、原生 iOS 工程、服务端缓存头或代理配置。
- 准备代码不挂载页面，不提前发起人员、房态或其他私有查询。换账号、撤权、离开页面后的迟到完成不能恢复旧界面或数据。
- 登录冷启动 JavaScript 至少减少 40% 的目标保持不变；原 40 组 × 7 次、10% 回退门槛、原始慢样本和失败记录不改。版本保护成本仅按已获用户批准且证据核实的范围另注，不机械减去 150ms。
- 所有含数据的验证继续使用隔离环境和虚拟账号；不得向 NAS 写入测试入住、财务记录或验证码。最终验收通过后只能使用实际启动仓库的 `scripts/deploy-nas.sh --source '<验收工作树>'` 发布。

## Review Focus

1. 同名模块、Windows 分隔符、query/map 后缀或第三方路径误命中：必须仍为精确白名单，不把 helper、业务或匿名依赖吸入公共包（任务 1）。
2. 两个日期文件被错误地认为“任意两个模块”：验证真实源身份、唯一性和同一工程归属；缺失、拆散、重复或额外模块都失败（任务 1）。
3. 日期检查报告失败但 CLI 仍 exit0：真实 CLI 负控必须仅让 dates 失败，其他结果通过，进程仍非零退出（任务 1）。
4. 新公共块下载失败：中文模块错误、没有自动注销／刷新；日期块失败不能卸载仍成功的外壳或触发房态私有查询（任务 1、2）。
5. 文件数下降但前置工作变多、角色边界变化或慢网更慢：原权限／样式／状态回归和完整 280 样本统计保留，额外下载不能移出计费窗口（任务 2）。

## 工作区、证据及文件职责

`W=/Users/skymoonzyj/.codex/worktrees/assisted-personnel-housing/new project3`；`L=/Users/skymoonzyj/Documents/new project3`；`PYTHON="$L/.venv/bin/python"`。执行时只读核对工作树归属、HEAD 及 dirty 状态，不新建或重置工作树。当前文档前产品树为 `e051b05`，与 `0e99e94` 相同；实际 BASE 在执行前重新记录。

本补充证据放在其自身 SDD 工作区 `S=$W/.superpowers/sdd/2026-10-19-frontend-common-chunks-amendment`，不写入旧计划的运行目录。旧基线为 `O=$W/.superpowers/sdd/2026-10-18-frontend-loading-optimization` 下 `task-1-evidence/accepted-baseline/baseline.json`，SHA256 必须为 `04ed4078a8d3ccbd9b3d06aeb2dc13f4f2ac2fa371b47b6e0c697baf1db07518`。

| 文件 | 责任 |
| --- | --- |
| `apps/web/vite.config.ts` | 两个精确构建组，不增加其他构建策略 |
| `apps/web/scripts/check-loading-graph.mjs` 及 `.test.mjs` | 白名单、真实产物、匿名闭包、循环和运行时检查；CLI 失败传播 |
| `apps/web/src/vite-env.d.ts` | 新增日期 selector 的构建侧类型声明 |
| `apps/web/tests/frontend-module-recovery.spec.ts` | 在原 6 例之外增加两个公共块故障回归 |
| `apps/web/scripts/run-module-recovery.mjs` | 仅把对应固定结果数 6 更新到 8，保留全部通过／失败规则 |

源码及文档通过 `apply_patch` 修改，只暂存本任务路径，提交用 `ERP_NAS_AUTO_DEPLOY=0`。不改正式测速 collector、比较器或源快照规则，不新增 runner/CLI 开关；不向公开资产 manifest 增加离线检查字段。

## Task 1: 两组精确合包及可执行防回退检查

**Files:** 修改上表 6 类文件；不新增产品文件。报告写本补充 SDD 的 `task-1-report.md`。

**Interfaces:** 保留 `isAuthenticatedIconModule(id)`、`inspectAuthenticatedIcons({graph, manifest, initialChunks, files})` 和 `checkInitialGraph({manifestPath, bundleGraphPath})` 现有入参／返回字段。新增 `isCommonDateModule(id: string): boolean`，及独立的 `inspectCommonDates({graph, manifest, initialChunks, files}) -> {ok: boolean, file: string|null, moduleCount: number, violations: string[]}`。日期检查不能嵌入原 icon-only inspector，避免旧 fixture 被迫拥有日期组。CLI 输出由 `{initial, styles, icons}` 增加 `dates`，退出条件必须合取四个 `ok`。

- [ ] **写真实 RED：** 扩展现有图标 fixture 到上述 21 叶，新增日期 fixture；测试名分别为 `finite icon selector captures exactly twenty-one leaves`、`common date selector accepts only the two exact project modules`、`common date output rejects split missing duplicate extra initial CSS dependency and ghost modules`。`dateFixture()` 在单个非初始文件内放入 `/project/src/shared/date.ts` 和 `/project/src/teaching/datetime.ts`，配套真实 manifest/file entries；`splitDateFixture()` 仅将两源分到两个文件。代表断言如下；fixture helper 复用现有 `chunk()` 风格，不复制现有全图遍历：

```js
assert.equal(graphTools.isAuthenticatedIconModule(iconId('refresh-cw')), true)
assert.equal(graphTools.inspectAuthenticatedIcons(iconFixture()).leafCount, 21)
assert.equal(graphTools.isCommonDateModule('/project/src/shared/date.ts'), true)
assert.equal(graphTools.isCommonDateModule('/project/src/teaching/datetime.ts'), true)
assert.equal(graphTools.isCommonDateModule('/project/src/shared/date.ts?raw'), false)
assert.equal(graphTools.isCommonDateModule('/project/node_modules/pkg/src/shared/date.ts'), false)
assert.equal(graphTools.inspectCommonDates(dateFixture()).moduleCount, 2)
assert.equal(graphTools.inspectCommonDates(splitDateFixture()).ok, false)
```

- [ ] **覆盖精确负例：** 保留 `eye/eye-off`、helper、runtime、源图标、query/map 后缀拒绝；将原被拒绝但现已批准的四图标移入正例。日期包含 Windows 路径正例，以及错目录、重复同一源、两个不同工程根、额外 API、manifest/最终文件缺失等负例。两组分别覆盖 CSS 附件／动态依赖和进入匿名初始闭包的拒绝，原静态环和重复 React/Auth/Query 身份检查不得放宽。
- [ ] **运行旧实现 RED：** 在 W 执行 `node --test apps/web/scripts/check-loading-graph.test.mjs`，保留因新 21 叶／日期行为未实现而失败的输出；依赖缺失或命令错误不算 RED。
- [ ] **实现最小分组与检查：** 图标组仍叫 `authenticated-icons`；日期组叫 `common-date`。新增 selector 同时用于 Vite 与检查器，`vite-env.d.ts` 补同签名声明。日期 inspector 校验精确两源、单一最终文件、非初始闭包、无 CSS、动态导入或外部静态依赖；图标 inspector 同样拒绝 CSS 和动态依赖，保留现有 bootstrap helper 静态依赖合同。`checkInitialGraph()` 将日期 selector 纳入禁止集合。全图完整性检查复用现有实现，不重构业务。CLI 从同一 manifest/graph/files 构建两组结果并纳入 exit status。
- [ ] **添加 CLI 失败传播负控：** 同一 Node 测试文件增加 opt-in 测试 `CLI exits nonzero when only dates inspection fails`，仅由测试环境变量 `ERP_LOADING_GRAPH_CLI_INTEGRATION=1` 启用。复制最新构建 metadata 和对应 dist/assets 到受限临时目录，完整正控要求四项 `ok=true`／exit0；只移除日期组一个 moduleId 后要求 `initial/styles/icons.ok=true`、`dates.ok=false`／exit1。始终清理本测试临时目录，不修改真实构建输出。普通单元运行显式报告该 opt-in 跳过，构建后的启用运行必须执行通过。
- [ ] **加两个生产故障例并保持原 6 例不变：** `shared authenticated icon failure never mounts shell or reads private pages` 在登录 API 后、首次 goto 前拦截 `**/assets/authenticated-icons-*.js`，确认实际请求被阻断、中文模块错误可见而不是“无权限”、AppShell/People/Housing 未挂载、人员／房态私有查询为 0、logout 为 0、仅一次初始 document。`common date failure keeps successful shell and another authorized route usable` 首次 goto `/housing` 前拦截 `**/assets/common-date-*.js`，确认真实请求失败、房态不挂载／不读 catalog/overview、外壳可用；转到 `/people` 后真实页面可用且外壳 DOM 身份未变。查询计数沿用现有明确的人员列表／房态业务端点，不把合法认证或 capability context 算作违规；切到人员页后的合法查询不计入房态失败阶段。两例均不宣称版本更新、无自动刷新或退出，保留真实权限及版本探测行为。沿用 `fault-evidence`，仅输出有限计数和布尔结果，不记录账号、响应体或任意 URL。runner 固定结果数只由 6 改为 8，不减少原断言、重试次数或失败条件。
- [ ] **先跑相关 GREEN：** `node --test apps/web/scripts/check-loading-graph.test.mjs`；WEB 下 `npm test -- src/app src/housing src/people src/layout/AppShell.test.tsx src/shared/date.test.ts src/teaching/datetime.test.ts`。检查新增测试文件的 TypeScript（见下方命令）；记录实际结果，不以测试收集替代运行。
- [ ] **冻结后跑一次完整本地检查：** WEB 下依次 `npm test`、`npm run build`、`npm run lint`；W 下执行实际 checker CLI 和启用的 CLI integration 测试。build 仅负责 `tsc -b && vite build`，不等于 checker 已执行。保留原 renderer >500KB 警告，不修改警告阈值隐藏它。

```sh
# WEB 中；PATH 加入 /Users/skymoonzyj/.hermes/node/bin
npx tsc --noEmit --strict --skipLibCheck --target ES2022 --module ESNext --moduleResolution Bundler --types node tests/frontend-module-recovery.spec.ts
# W 中，完成生产构建后
node apps/web/scripts/check-loading-graph.mjs apps/web/.build-metadata/vite-manifest.json apps/web/.build-metadata/bundle-graph.json
ERP_LOADING_GRAPH_CLI_INTEGRATION=1 node --test apps/web/scripts/check-loading-graph.test.mjs
```

- [ ] **读取实际产物与提交：** 两组必须恰为 21／2 个指定模块、各为一个真实文件，匿名初始模块集合不扩大；按 `router.tsx` 现有公开路由逐一列出对应入口，实际静态闭包不得触达两组，不能把所有 `isDynamicEntry` 误当公开入口。按实际 manifest 静态边记录 AppShell、Housing、People 的单独及联合闭包、文件数和字节，与现有联合 16 个额外 JS 对照。预计 11 不是免测硬编码的成功证据；文件数未减少或边界变化则停止后续运行。完成 diff 自查、精确路径暂存、暂停发布提交，独立任务审查通过后再进入任务 2。

## Task 2: 冻结候选的真实回归、性能裁决及原计划交接

**Files:** 不修改产品／测速源；仅本补充 SDD 下 `task-2-report.md`、`evidence-index.md` 和全新 `evidence/*`。确需修正任务 1 新代码时退回明确失败项，不在运行中编辑。

**Interfaces:** 消费任务 1 的冻结 HEAD、构建四项检查及实际分组表。沿用 `scripts/run-frontend-loading-acceptance.py --phase named --check <name>` 与 `--phase compare`；其输出 schema、真实 ready、金融基线校验、归档不变性和精确 owner 清理合同不变。原 `route-loading` 仍 14 例／38 个被动阶段，`module-recovery` 为原 6 加本轮 2 例，`state-safety` 保持原矩阵。

- [ ] **运行前检查：** 工作树冻结，前一个 owner 已退出且其容器／卷／网络为 0，保护基线与运行／停止容器引用的镜像，检查磁盘空间。一次只有一个 DB/browser owner；不全局 prune、不操作 NAS 数据或证书，不与其他重构／构建并行。每个 evidence-dir 必须不存在；失败保留日志和结果，不覆盖重用。
- [ ] **串行执行三个既有 named suites：** 顺序为 `module-recovery`、`route-loading`、`state-safety`。每一个都要求实际测试非跳过、全部通过、金融／归档后检查到达通过，以及精确 owner 清理完成，才进入下一项。新公共块失败例在实际生产构建运行；桌面／手机、受限／学员角色、公开入口及草稿／筛选沿用原对应回归。

```sh
# cwd W；S 为本补充 SDD，PYTHON 为 L/.venv/bin/python
# 每次只替换一个明确的 check 与新的 evidence-dir，禁止并行
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 /usr/bin/time -p "$PYTHON" scripts/run-frontend-loading-acceptance.py --baseline-source "$W" --candidate-source "$W" --phase named --check module-recovery --evidence-dir "$S/evidence/module-recovery-1"
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 /usr/bin/time -p "$PYTHON" scripts/run-frontend-loading-acceptance.py --baseline-source "$W" --candidate-source "$W" --phase named --check route-loading --evidence-dir "$S/evidence/route-loading-1"
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 /usr/bin/time -p "$PYTHON" scripts/run-frontend-loading-acceptance.py --baseline-source "$W" --candidate-source "$W" --phase named --check state-safety --evidence-dir "$S/evidence/state-safety-1"
```

- [ ] **复制并校验原基线：** 创建新的 `evidence/compare-1`，只复制原 baseline.json，使用 `cmp` 和 SHA256 校验上述固定值；不重新生成一个更容易通过的基线。
- [ ] **执行一次原完整比较：** 使用同一冻结源码执行下列命令，保留每组全部样本、median／worst、错误、JS/CSS 字节、ready 变化与所有源码／镜像／配置指纹。验证原 collector/config/profile/统计窗口与基线未变化；不得将修改组名后无法识别的观测静默当成空数据。

```sh
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 /usr/bin/time -p "$PYTHON" scripts/run-frontend-loading-acceptance.py --baseline-source "$W" --candidate-source "$W" --phase compare --evidence-dir "$S/evidence/compare-1"
```

- [ ] **裁决而不是择样重跑：** 280 个有效样本／40 组 × 7、0 收集错误、JS 目标通过才可讨论 ready；所有未经明确接受的回退均须解决。已接受版本校验成本仍与原数值 FAIL 分栏，不能把混合延迟算成它。若失败，保留证据并停止发布：报告新增回退与旧未解决项，不开启另一轮无依据的调参或篡改旧诊断工具的冻结源码白名单。
- [ ] **独立证据复核并交回原计划：** 复核原始数组／中位数、40 组比较、测试退出码、金融／归档完成及清理来源；任务报告不能替代全部独立凭证。补充计划只完成合包验收，不标记原全计划完成。通过后记录最终源码与产物身份，接回原计划任务 12 的 strict backup/recovery、最终整分支审查和 NAS 发布；原 SDD 中的 `task-12-exact-image-order-handoff.md` 与 `task-12-strict-gate-dispatch-brief.md` 保留为该任务的已记录约束，不在本补充中提前实施或消除。

## 完成与交付边界

本次计划审阅不启动上面的测试或部署。用户确认后沿用子任务开发／独立复核；有负载的运行按依赖顺序进行，不为增加并发而并发操作数据库或污染测速。

只有原计划全部适用门禁完成，才从 L 执行 `scripts/deploy-nas.sh --source "$W"`；记录真实 release ID、运行镜像及 HTTPS 18080 验证。脚本预检、备份、恢复或发布失败均明确报告为阻断，不绕过脚本、不回滚数据库、不声称 NAS 已同步。
