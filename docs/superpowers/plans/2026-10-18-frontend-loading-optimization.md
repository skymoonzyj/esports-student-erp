# ERP 前端加载优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 减少登录和常用业务页面首次加载成本，同时保证发布升级、权限、草稿、房态筛选及天赋测试计分不回退。

**Architecture:** 先建立生产构建的隔离性能基线，再以镜像内历史静态资源和安全加载失败提示保护升级，随后按路由、游戏和大型面板延迟加载。认证及查询客户端保持单例；源码指纹、历史资源输入指纹、最终镜像身份分别验证。复用现有 NAS 发布和加密恢复流程，不引入持久资源卷。

**Tech Stack:** 现有 React 19、TypeScript、Vite 8、React Router、React Query、Vitest、Playwright；Node 24、Python 3.12、Docker Compose、Nginx、现有 Caddy。使用锁文件中的版本，不为本轮升级依赖。

**Spec:** `docs/superpowers/specs/2026-10-18-frontend-loading-optimization-design.md`，用户已确认。

**状态：** 待用户审阅本实施计划。以下复选框均为待执行任务，不代表测试、上线或性能目标已完成。延续按实际依赖分配子任务、分阶段独立复核的协作方式。

## Global Constraints

- 顺序固定为：基线测量、发布更新保护、业务路由拆分、模块内部优化。
- 登录冷启动所需 JavaScript 总压缩传输量以相同测试环境下减少至少 40% 为首轮目标。未达到须如实报告，不宣称目标完成。
- 登录、房态、人员中心的可操作时间中位数不比基线恶化超过 10%；超过时复测并定位。
- 桌面 1440×900、手机 390×844；下载 10 Mbps、上传 2 Mbps、RTT 150 ms、CPU 4 倍减速；每组至少 7 次有效运行，另保留未限速对照。
- 统计窗口从导航开始至页面真实可操作后 2 秒，包含全部脚本、公共分包、modulepreload、自动预取和期间的版本检查请求。
- 当前 HTML 与公开的最小版本标识文件使用 `Cache-Control: no-cache`；哈希静态资源保持 7 天 immutable 缓存；缺失资源返回真实 404。
- 保留当前、刚被替代的上一版，以及退出活跃服务不超过 7 天的历史资源集；候选全部资源去重后未压缩字节上限 512 MiB，即 536870912 字节。
- 超限、资源校验失败或空间不足必须在初始化媒体、迁移、切换服务之前停止；不删除仍需保留的版本来凑容量。
- 不自动刷新、注销或持久化敏感草稿；不改变角色检查、CSRF、会话代次、撤权清理、幂等键、日期、随机及评分规则。
- 不新增框架、CDN、Service Worker、离线功能、全模块预取或原生权限；不把未发布 iOS 源码混入本次 Web 发布。
- 只使用一次性数据库、合成账号及私有邮件发送器；测试库写入串行；不得对 NAS 创建测试业务数据或恢复覆盖业务库。
- 产品更新验收后通过实际启动仓库的 `scripts/deploy-nas.sh --source '<验收源目录>'` 发布，失败阻断交付；保持 HTTPS 18080、密钥及业务卷。

## Review Focus

1. 长期开着的旧标签页首次进入未访问模块，或下载中途断网：仍能使用保留资源，失败只提示，不能丢失草稿（任务 3、6、11）。
2. 直接打开资产／注册深链，或先访问采购再访问人员：公共按钮及表单样式不依赖先前页面加载顺序（任务 7、11）。
3. 加载中的账号退出、切换或被撤权，随后旧模块／API 才完成：不得恢复旧账号页面及数据（任务 6、8、10、11）。
4. 前驱长期未发布、激活回执缺失／未来时间、同名资源不同内容，或构建后 tag 被替换：不提前清理、不接受错误镜像、不误记发布成功（任务 3、4、5）。
5. 慢网加载游戏、恢复隐藏页面或重复点击重试／提交：加载不进入计时，不能重复提交成绩／采购／建档，也不能因组件重建清空正在编辑的值（任务 9、10、11）。

---

## 执行环境及命令约定

文档源 `S=/Users/skymoonzyj/.codex/worktrees/assisted-personnel-housing/new project3`，代码基线为已发布的 `95da4227ae831eeb4edfd75d3fc31bcdaa8882ae`，规格提交为 `5604ff3`。启动仓库 `L=/Users/skymoonzyj/Documents/new project3`。实施前只读核对这些身份和 NAS 活跃版本；不从旧 `.nas-release-sync` 或未发布原生工作树整树复制。

执行任务 1 前按 using-git-worktrees 检查现有工作树及归属，选择或创建本轮隔离根 `W`；不重置任何脏目录。本文所有相对路径均相对 `W`，除非明确写 `L`。`WEB="$W/apps/web"`，`PYTHON="$L/.venv/bin/python"`；Python 路径带空格，必须引用。

文中的新文件／命令是本计划要实现的接口，并非声称现在存在。每个代码任务先写断言、确认因目标行为缺失而失败，再实现并跑绿；连接失败／依赖缺失不算有效 RED。每个任务结束只暂存该任务文件／块，检查 staged diff 后用 `ERP_NAS_AUTO_DEPLOY=0 git commit -m '<任务提交说明>'` 提交，表明集成期间有意暂停发布；任务 12 再统一发布。不得 `git add .`、推送或借提交钩子提前发布未验收内容。

不直接运行会重置共享数据库的 `scripts/check.sh`。需要数据库的命令必须取得本轮独占测试窗口，核实唯一 Compose 项目、容器 ID、卷及端口；退出和清理完成后才进入下一套。现有业务 API 不变，无需新增迁移；若实现发现必须改变业务协议，应停下报告范围变化。

## 文件和任务边界

| 单元 | 文件范围 | 职责 |
| --- | --- | --- |
| 性能与生产浏览器验收 | `scripts/run-frontend-loading-acceptance.py`、`apps/web/scripts/{loading-metrics,measure-loading}.mjs`、`apps/web/tests/frontend-*.spec.ts` | 归属明确的隔离服务、冷暖缓存及跨版本证据 |
| 当前构建与资源合并 | `apps/web/scripts/{prepare-web-assets.mjs,web-assets.schema.json}`、`apps/web/retained-empty/manifest.json`、Dockerfile／Vite／Nginx | 当前构建身份、资源清单、独立历史输入、干净最终镜像 |
| 历史选择与部署适配 | `scripts/{web_assets.py,nas-deploy-web-assets.py}`、两份 `deploy-nas.sh`、`nas-deploy-guard.py` | 字节／路径校验、保留策略、不可变镜像与激活回执 |
| 恢复 | `scripts/erp-backup.py`、`apps/api/tests/cli/test_web_assets_backup_docker.py` | 加密归档内 Web 镜像真实恢复和静态响应验证 |
| 加载边界 | `apps/web/src/app/loading/` | 模块失败分类、版本查询、用户主动重试／更新 |
| 公共依赖与路由 | `styles/`、`foodPreferences/paths.ts`、`app/router.tsx`、`layout/AppShell.tsx` | 明确公共样式及轻量常量，稳定权限守卫与路由懒加载 |
| 内部模块 | `talentTests/`、`assistedPeople/`、`people/`、`assets/`、`procurement/` | 游戏及按打开条件加载的面板，不改业务逻辑 |

依赖：1 → 2 → 3 → 4；5 依赖 3／4，6 依赖 2；5／6 完成后 7 → 8；9、10 在 8 后可并行；11 汇总，12 发布。前期可并行只读调查或编写不冲突的测试，但不得跳过基线及更新保护门禁。路由由单一任务持有；部署脚本由单一任务持有；不用任务数凑满 8 个并发。

## Task 1: 生产构建测量与隔离基线

**Files:** 新建 `scripts/run-frontend-loading-acceptance.py`、`scripts/test_frontend_loading_acceptance.py`、`apps/web/scripts/loading-metrics.mjs`、`apps/web/scripts/loading-metrics.test.mjs`、`apps/web/scripts/measure-loading.mjs`、`apps/web/playwright.loading.config.ts`；复用 `compose.yaml`、`compose.e2e.yaml` 及既有 seed，不改共享 runner 默认行为。

**Interfaces:** Python CLI `--baseline-source PATH --candidate-source PATH --phase baseline|compare|release-window --evidence-dir PATH`，只接受本地核验源码；产生脱敏 `baseline.json`、`candidate.json`、`comparison.json`。`sumInitialScriptBytes(resources: ResourceMetric[]): number`、`summarizeRuns(runs: RunMetric[]): ScenarioSummary`、`compareSummaries(baseline, candidate): Comparison` 在 `loading-metrics.mjs`；ResourceMetric 为 `{kind,encodedBodySize,cacheHit}`，RunMetric 明确包含 scenario、viewport、cacheMode、networkProfile、ok、jsEncodedBytes、cssEncodedBytes、requestCount、readyMs、fcpMs、lcpMs、scriptMs、longTaskCount、固定错误分类。ScenarioSummary含有效／失败数及每项指标median／max；Comparison含jsTargetMet、readyRegressionPassed、实际百分比及样本数。不可用指标为 null，不填 0。

- [ ] 写 `loading-metrics.test.mjs`：`assert.equal(sumInitialScriptBytes(entryAndSharedChunks), 300)`；使用 7 个样本验证中位数、最差值和失败数；`assert.equal(compareSummaries(base, reducedBy40Percent).jsTargetMet, true)`，39.9% 为 false；10% 可操作时间边界通过，超过标回归；缓存命中为零传输但仍计请求。
- [ ] 写 launcher 拒绝测试：非回环／继承生产环境、既有不属本次容器、错基线 SHA、dotenv、未知测试选择器均失败且未创建服务；清理只按核验 ID；信号中断后不留下拥有的子进程。运行 `node --test "$WEB/scripts/loading-metrics.test.mjs"` 和 `"$PYTHON" "$W/scripts/test_frontend_loading_acceptance.py"`，确认 RED。
- [ ] 实现上述统计和 CLI：用现有 Compose 的生产 Web target、独立项目及覆盖文件启动本轮 API／DB／Web／代理；只启这四个服务，关闭外部邮件，回环动态端口。基线用核验提交的受控源码快照，不 checkout 当前工作树；冷暖两组用相同 gzip 代理与浏览器。复用 seed_e2e、seed_assisted_personnel_e2e 合成数据，真实 API 登录；凭据仅 mode0600 临时文件，不进入报告／截图／trace／HAR。
- [ ] 实现浏览器采集：同源 Resource Timing 的 encodedBodySize 与缓存标记计响应体，不把 CDP 含头部字节冒充响应体；收集全部资源至 ready+2000ms。CDP 限速用 1250000／250000 字节每秒、150ms、CPU rate4；冷启动禁缓存新 context，暖启动保留同一 context。登录以输入和提交可用且授权恢复结束、房态以筛选和房卡查询完成、人员以列表查询及搜索可用为 ready。SPA 内切页单独计可操作时间，FCP／LCP 不伪造。
- [ ] 跑两套纯测试至PASS，然后以`--phase baseline`跑既定三页面、两视口、冷／暖和模块首次／再次矩阵，每组7次有效样本。无效运行和失败保留原因，不剔除慢样本；核对压缩生效、基线代码未被测量工具修改，保存source/image/browser/config指纹及脱敏基线。
- [ ] 按提交约定提交本任务文件：`test: establish isolated frontend loading baseline`。

## Task 2: 当前构建身份与缓存契约

**Files:** 新建 `apps/web/scripts/{build-id.mjs,prepare-web-assets.mjs,prepare-web-assets.test.mjs,web-assets.schema.json}`；修改 `apps/web/src/vite-env.d.ts`、`apps/web/vite.config.ts`、`apps/web/Dockerfile`、`apps/web/nginx.conf`、`.gitignore`、`apps/web/.dockerignore`、`scripts/source-tree-digest.py` 的构建产物排除；新建 `apps/api/tests/cli/test_web_asset_delivery.py`，扩展 `test_source_tree_digest.py`。

**Interfaces:** `resolveBuildId({root,mode,explicitId}): string` 为Vite与生成器共用入口；`writeCurrentAssets({distDir,metadataDir,buildId,releaseId}): Promise<AssetManifest>`。构建内 `__ERP_BUILD_ID__: string` 与 `/build-info.json` 的 `{schema_version:1, build_id:string}` 一致。NAS 用 release ID 作 build ID；本地从src、public（存在时）、index.html、package及锁文件、vite／tsconfig配置的排序路径和字节生成 `local-<sha256>`，排除dist、metadata及历史输入，开发模式为development。私有schema：`schema_version:1, build_id, release_id, predecessor_image_id:string|null, input_sha256:string|null, total_bytes:int, documents:AssetFile[], sets:AssetSet[]`；AssetSet为 `{release_id,build_id,superseded_by_release_id:string|null,files:AssetFile[]}`，AssetFile为 `{path,size,sha256}`。sets的path仅为`assets/`下规范路径；documents只允许当前index.html和build-info.json，历史集不含HTML；total_bytes包含documents与去重assets。其他公开根文件须显式扩展白名单，不能静默漏算。最终imageID不写入自身manifest。

- [ ] 写 `test_build_id_matches_document`、`test_private_inventory_not_served`、`test_missing_asset_is_404`、`test_html_and_build_info_revalidate`：`assert public_info == {"schema_version":1,"build_id":compiled_id}`；HTML／版本 no-cache，现有 JS 仍 immutable，404 无长期缓存，私有清单不在 docroot；Node 测试还断言相同文件清单顺序变化不影响摘要。
- [ ] 运行 `node --test "$WEB/scripts/prepare-web-assets.test.mjs"` 及 `"$PYTHON" -m pytest -q --confcutdir="$W/apps/api/tests/cli" "$W/apps/api/tests/cli/test_web_asset_delivery.py"`，确认 RED。
- [ ] 实现生成器和Vite build ID define；Vite产出build-info并在closeBundle调用writeCurrentAssets，公开构建仍在dist，私有元数据在`.build-metadata`。最终镜像仍从现有Nginx基础镜像构建，私有manifest COPY到`/etc/erp-web/assets-manifest.json`，dist COPY到`/usr/share/nginx/html`；metadata不得进入公开根或源码指纹／打包。排除source map、源码、环境和日志；Nginx对HTML／版本精确匹配，所有`/assets/`缺失返回404，不落SPA fallback。
- [ ] 跑Node／Python测试及`npm --prefix "$WEB" run build`；在任务1隔离环境实际检查HTTP头和响应而非只匹配配置文本，全部PASS。
- [ ] 按提交约定提交本任务文件：`feat: add versioned web assets and cache contract`。

## Task 3: 有界历史资源选择与构建合并

**Files:** 新建 `scripts/web_assets.py`、`scripts/test_web_assets.py`、`apps/web/retained-empty/manifest.json`；扩展任务2生成器／测试／schema、`apps/web/Dockerfile`、`compose.yaml` 的 Web build.additional_contexts；禁止新增运行资源卷。

**Interfaces:** Python `select_retained_sets(manifest: dict, receipts: dict[str, dict], *, previous_release: str, candidate_release: str, now: datetime) -> list[dict]`；`extract_asset_archive(stream: BinaryIO, destination: Path, *, expected_files: list[dict], max_bytes: int = 536870912) -> dict`；`verify_asset_tree(root: Path, manifest: dict) -> dict`。私有激活回执结构 `{schema_version:1, release_id, source_tree_sha256, web_image_id, web_assets_manifest_sha256, activated_verified_utc}`。历史输入为独立RetainedInput `{schema_version:1,candidate_release_id:string|null,predecessor_image_id:string|null,sets:AssetSet[]}`，默认空fixture两ID均null、sets为空；在schema的独立definition中验证，不能当最终AssetManifest。Node `mergeRetainedAssets({currentManifest,retainedDir,outputDir,metadataDir,maxBytes}): Promise<AssetManifest>`，maxBytes固定536870912。同一JSON schema及跨语言fixture为契约。

- [ ] 写 `test_retention_uses_supersession_not_build_time`、`test_unknown_receipt_keeps_set`：刚替代的长期旧版必须保留；恰好7天保留，超过且非前驱剔除；缺失、不匹配、未来激活时间保守保留。测试同路径同hash去重／不同hash拒绝、536870912通过／多1字节拒绝、重复条目、超大清单、路径穿越、绝对路径、软／硬链接和设备文件全部拒绝。
- [ ] Node 增加 `merge_keeps_dependency_closure`、`merge_rejects_conflict_and_overflow`，断言旧入口JS、共享JS、CSS都保留、index.html只用新版；Python `"$PYTHON" "$W/scripts/test_web_assets.py"` 与 Node任务2测试先 RED。
- [ ] 实现选择及流式受限提取，拒绝未知文件／schema；清单长度和条目数分别限制 16 MiB／100000，路径限1024 ASCII字符，错误只有稳定类别。无需以删除候选原有文件应付非法输入。并行文件写入不可逃出已核验空目录；写前和读中累计限制，完成后逐项hash核验。首版导入由任务4显式 bootstrap，普通路径不自动降级。
- [ ] 构建用 Compose 命名上下文 `erp_retained_assets`，默认指向仓库的空集fixture；NAS覆写为独立受限scratch目录。builder用 `COPY --from=erp_retained_assets` 读取，Node合并后才COPY到干净Nginx最终层。所有生产构建入口显式提供此上下文；更新既有 Dockerfile 合约测试。不支持命名上下文时拒绝，不忽略警告；不用 `FROM` 旧生产镜像或先COPY历史再RUN删除的分层做法。
- [ ] 跑两语言fixture／边界测试和实际两个版本的隔离镜像构建；验证最终镜像私有清单与文件、过期资源未进入最终层、没有派生scratch混入源摘要，全部PASS。
- [ ] 按提交约定提交本任务文件：`feat: retain bounded immutable web asset sets`。

## Task 4: 实际 NAS 发布入口与激活身份门禁

**Files:** 新建 `scripts/nas-deploy-web-assets.py`、`scripts/web-assets-contract.json`；修改 `scripts/deploy-nas.sh`、`scripts/nas-deploy-guard.py`、`scripts/test_deploy_nas.py`、`scripts/test_deploy_nas_packaging.py`、`compose.prod.yaml`的Web构建参数，以及 `L/scripts/deploy-nas.sh` 的同等必要功能块，保留 L 独有 iOS 排除规则。

**Interfaces:** helper命令 `prepare --app-dir PATH --candidate-release ID --output-dir PATH`、`verify-image --image-id sha256:... --input-manifest PATH`、`verify-running --app-dir PATH --expected-image-id sha256:... --expected-manifest-sha256 HEX`；stdout只返回验证结果JSON，不读／输出`.env`。prepare返回前驱imageID、input清单hash、scratch及bootstrap标记；verify返回总字节、文件数、清单hash。源码`web-assets-contract.json`要求协议1，最终镜像label `cn.jingxue.erp.web-assets-contract=1`。`.release-metadata`新增`web_assets_contract/web_image_id/web_assets_manifest_sha256/web_assets_input_sha256/web_predecessor_image_id/source_tree_sha256/activated_verified_utc`，保留旧字段；转换为任务3回执结构后读取，不能覆盖原key=value格式。

- [ ] 扩展真实heredoc的mock测试：`test_assets_failure_precedes_media_and_activation` 断言 `media_init/migrate/recreate == []`、active未变；`test_old_launcher_rejects_required_contract`、`test_runtime_image_swap_rejected`、`test_failed_candidate_never_retained`、`test_bootstrap_once_only`。bootstrap仅限活跃版本未声明协议且未有过成功协议1发布；声称协议1而缺清单必须失败。
- [ ] 对 W 和 L 显式设置 `NAS_DEPLOY_TEST_LAUNCHER`，分别运行 `"$PYTHON" "$W/scripts/test_deploy_nas.py"`、`"$PYTHON" "$W/scripts/test_deploy_nas_packaging.py"`，先 RED；测试fixture显式复制新共享模块／schema，不从宿主源码偷读。
- [ ] 在部署锁、源码验真和旧authority检查后绑定 `.active-release`、complete/healthy状态、Compose项目／service／配置归属、旧release元数据、运行容器 `.Image` 与镜像ID。只从该ID建立owned临时容器提取公开资源及私有清单；不依赖可变tag、旧源码目录或任意宿主路径。首次严格导入完整构建资源集并记录来源。
- [ ] 在首次需要新构建变量的`compose config`及`compose build`之前生成独立上下文和受限override；prod compose要求`ERP_WEB_ASSETS_PROTOCOL=1`、`ERP_WEB_ASSET_INPUT_SHA256`和`ERP_WEB_BUILD_ID`，缺少则构建失败，因此旧launcher不能用空fixture绕过。历史输入`candidate_release_id`必须与NAS buildID一致；开发／隔离空fixture不受NAS条件影响。构建后捕获精确候选imageID并校验合并清单／容量／空间，全部在媒体初始化之前。记录derived输入hash，不改源快照；运行后验证`.Image`等于候选。公开TLS、资源和运行检查全成功后原子写激活回执，才记录healthy；失败继续既有保护和前向修复分支。
- [ ] 双入口四组测试、`bash -n`、helper单测全部PASS；打包测试证明`.build-metadata`、scratch、凭据、iOS产物不入源包，资源回执写失败不能报告healthy。
- [ ] 按约定提交W功能，再仅暂存L必需块并提交：`feat: gate NAS releases on retained web asset identity`。此阶段不执行NAS发布。

## Task 5: 备份中的 Web 资源恢复证明

**Files:** 修改 `scripts/erp-backup.py`、`scripts/erp-backup-install.py` 的helper安装清单（如现有安装未覆盖新helper）；新建 `apps/api/tests/cli/test_web_assets_backup.py`、`apps/api/tests/cli/test_web_assets_backup_docker.py`；更新 `test_erp_backup.py`、`test_erp_backup_sequences.py`。

**Interfaces:** 复用任务3校验器；备份manifest新增可选 `web_assets`，含协议、清单hash、build/release ID及runtime_images.web精确ID。恢复结果新增 `web_assets_verification: {status:'verified'|'legacy_unavailable', current_files:int|null, retained_files:int|null, manifest_sha256:string|null}`。协议1缺项／错hash失败；旧归档保留兼容但不能给verified。

- [ ] 写 `test_missing_web_image_requires_archive_load_even_when_api_exists`、`test_new_contract_cannot_fall_back_to_legacy`、`test_legacy_backup_not_claimed_verified`，断言完整imageID缺失时加载受验证归档、绝不pull替代；schema1损坏报错；老归档状态legacy_unavailable。纯pytest先 RED。
- [ ] 实现backup inventory及verify_archive加载条件扩展：包含web，恢复该精确镜像和回执；复用清单校验，不能把数据库检查成功替代本项。helper安装／归档带上必要模块，避免线上restore路径ImportError；归档加密与认证机制不变。
- [ ] 新Docker测试借鉴现有 `test_assisted_personnel_backup_docker.py` 的ID／所有权／pull=never规则。opt-in为 `ERP_BACKUP_DOCKER_TEST=1`，另强制 `ERP_BACKUP_TEST_WEB_IMAGE`、API／POSTGRES均本地完整sha256。验证加密归档save/load、当前与旧JS/CSS hash、HTML只有当前、未公开清单；HTTP probe只在owned网络命名空间，不绑定公网。仅移除本轮精确owned ID，失败保留受限证据并报清理状态。
- [ ] 先跑无Docker `test_web_assets_backup.py` 与现有backup／sequences至PASS；再在独占窗口运行新opt-in，必须实际执行且0skip。使用 `env -u DATABASE_URL -u TEST_DATABASE_URL -u DATABASE_URL_FILE PYTHONPATH="$W/apps/api" "$PYTHON" -m pytest -o addopts='' -q -p no:cacheprovider --confcutdir="$W/apps/api/tests/cli" <具名测试文件>`；Docker运行在该命令另显式设置上述opt-in及image变量。
- [ ] 按提交约定提交本任务文件：`test: verify encrypted restoration of retained web assets`。

## Task 6: 安全的模块加载与失败恢复

**Files:** 新建 `apps/web/src/app/loading/{lazyModule.tsx,ModuleBoundary.tsx,buildInfo.ts,moduleLoadEvents.ts,loading.css}` 及同目录测试；修改 `apps/web/src/main.tsx` 挂一次监听。当前路由大规模拆分留到任务8。

**Interfaces:** `createLazyModule<P extends object>(id:string, loader:()=>Promise<{default:ComponentType<P>}>): {Component:ComponentType<P>, retry:()=>void}`，在模块顶层创建；retry只替换失败子模块的lazy类型，成功实例稳定。`ModuleBoundary({moduleId, retry, children}: {moduleId:string; retry:()=>void; children:ReactNode})`；`readBuildInfo(signal:AbortSignal): Promise<{kind:'same'|'changed'|'unknown'}>`，仅同源 `/build-info.json`，超时5秒，schema及buildID校验。`installModuleLoadEvents():()=>void` 返回卸载函数，事件订阅无路由同步请求。

- [ ] 写测试 `retry_does_not_remount_provider_or_parent_draft`、`version_probe_failure_does_not_logout`、`refresh_requires_confirmation`、`late_import_after_account_change_cannot_restore_old_view`；断言父输入值未变、QueryClient／AuthProvider同一实例、普通导航fetch版本次数0、自动reload次数0、取消刷新不reload。普通业务throw不误判“版本过期”。
- [ ] `npm --prefix "$WEB" test -- src/app/loading` 先 RED；实现只包裹动态import错误的typed标记，结合Vite preload事件通知，不吞所有全局错误。失败提示“页面模块加载失败，请检查网络后重试”；确认版本改变才显示“系统已更新”；刷新前提示“刷新会丢失尚未保存的内容，是否继续？”并由用户确认。
- [ ] 在允许重试的失败分支创建新的lazy promise尝试；不加任意query复制依赖。浏览器继续缓存失败时保持提示并允许主动刷新，不循环reload。局部panel错误不越过父页面，授权guard继续在外层，业务异常走原有错误处理；检查abort及迟到版本响应不污染新页面。
- [ ] 上述测试和 `src/auth/AuthProvider.test.tsx`、`src/api/client.test.ts` 跑绿；隔离浏览器断网／旧版本／业务403三种故障各自断言不同提示，权限不放宽。
- [ ] 按提交约定提交本任务文件：`feat: add safe lazy module recovery boundaries`。

## Task 7: 明确公共样式和轻量依赖

**Files:** 新建 `apps/web/src/styles/shared-controls.css`、`apps/web/src/foodPreferences/paths.ts`、`apps/web/tests/frontend-style-order.spec.ts`；修改 `main.tsx`、`auth/LoginPage.css`、`people/people.css`、`settings/settings.css`、`layout/AppShell.css`、`foodPreferences/FoodOnboardingPage.tsx`、`layout/AppShell.tsx`、`app/router.tsx` 中常量导入；检查 `assets/assets.css`、`procurement/procurement.css` 的局部覆盖。

**Interfaces:** `foodOnboardingPath` 继续原值及既有导出兼容，公共调用者改从paths导入；基础类 `.secondaryButton/.dangerButton/.formError/.formField/.formGrid/.fieldError/.pageEyebrow` 的跨域通用规则明确进入共享层。页面特有覆盖保留原特异性及相对顺序，不机械合并同名规则。

- [ ] 写浏览器样式顺序测试：直接深链资产扫码／采购／人员／注册，以及采购→人员→资产与反向顺序，断言按钮最小尺寸、输入边框／错误提示、焦点可见、无横向溢出一致。用受控只加载该页面CSS的fixture使隐式依赖先暴露，不改变业务授权。
- [ ] 任务1runner用当前隔离账号执行具名测试，确认RED来自缺失公共规则；同时写轻量依赖图断言 `foodPreferences/paths.ts` 不import页面／panel。
- [ ] 迁移确实跨域的规则并删除重复基础定义，保留局部覆盖；main静态引入shared-controls，领域CSS仍由领域组件引入。不得把全部people.css移到global来规避拆分，也不做全量CSS重写。
- [ ] 跑样式两视口多顺序测试和 `npm --prefix "$WEB" test -- src/layout/AppShell.test.tsx src/foodPreferences src/people src/assets src/procurement`，全部PASS；截图仅合成数据，报告保留脱敏尺寸差异。
- [ ] 按提交约定提交本任务文件：`refactor: make shared style and path dependencies explicit`。

## Task 8: 稳定外壳下的业务路由延迟加载

**Files:** 修改 `apps/web/src/app/router.tsx`、`layout/AppShell.tsx` 的入口接缝；新建 `app/lazyPages.ts`、`app/routeLoading.test.tsx`、`apps/web/scripts/check-loading-graph.mjs` 及其 `.test.mjs`；扩展 `vite.config.ts` 输出非公开构建manifest；更新现有路由测试的异步等待。

**Interfaces:** 保留 `appRoutes:RouteObject[]`、`AppRouter()`、`AnyPermissionRoute({permissions,children})` 及所有URL／guard；lazyPages只声明顶层动态导入并适配既有named exports。图检查 `checkInitialGraph({manifestPath, bundleGraphPath}): GraphCheck`，bundleGraph为Vite构建插件离线输出的chunk→源码moduleID依赖表，放非公开metadata，不发布路径到公网。

**Task11 限定修订：** 经固定生产诊断与 root 裁定，仅房态总览、人员中心选择既有 `createLazyModule` 的 `controlled` 模式；默认 `suspense` 和 `{Component,retry}` 接口保持不变。复用 factory 的 import 错误标记、监听和 retry 所有权，不增加预取或跨账号页面缓存。effect 在真实授权挂载后启动共享单次加载，普通 pending UI 不抛 Promise；成功保存稳定组件类型，使用当前 props/context。测试覆盖非挂起提交、StrictMode、多订阅、CSS 顺序、显式 retry／sticky CSS、业务渲染错误、真实房态 guard 的 pending 撤权与迟到代码，以及原筛选／access_revision 断言。先完整本地检查和限定复核，再单次相同 14 项诊断；若实际 reveal 等待未改善停止扩展，280 门禁和 no-cache 取舍另行处理。

**Task11 两页资源顺序后续裁定：** `lazyPages.routeModule` 仅匹配 HousingOverviewPage／PeoplePage 同时启动现有 CSS gate 和 page import，立即为 page promise 安装成功／失败 outcome handler；先 await CSS，再读 page outcome，确保 CSS 失败优先且无未观察 JS 拒绝，成功后才挂载。其余消费者不变。先真实 RED（CSS pending 时两页 import 应已启动），再双完成／失败顺序、CSS sticky／JS 显式重试、StrictMode／稳定状态／卸载及真实 router+filter guard 的 CSS-pending 撤权／注销／换账号测试；既有 browser 单例改为真实 held CSS 下页面请求完成但 DOM／query 不出现，释放后断言实际 overlap。完整 Web／build／lint／graph 和 scoped review 后再授权唯一原 14case／38phase 运行；不自动采样 280、不调整原 ready 或阈值，不修 deferred minors。

- [ ] 写 `routeLoading.test.tsx`：登录不调用AppShell或任何业务loader；注册／重置／激活仅加载请求页；后台切页AppShell挂载次数1；HousingFilterStateProvider仍在capability guard上、按account key隔离。图测试断言登录静态闭包排除人员／房态／采购／评价／两套天赋测试的页面与引擎实现，允许明确列出的轻量类型／常量，不能仅检查chunk文件名。
- [ ] 跑具名新测试与 `npm --prefix "$WEB" test -- src/app/router.test.tsx src/app/housingFocus.test.tsx src/app/invitationsAccess.test.tsx`，确认RED；实现顶层createLazyModule和局部Suspense。登录外层保留必要认证，已认证壳和每个业务域入口动态载入；不把领域页面通过barrel又静态导回。
- [ ] 逐一保持原有守卫语义：住房撤权清缓存，portal重新验证时隐藏／卸载策略不改，evaluation保留草稿策略不改，深链及浏览器前后退不变。路由mock改等待loader就绪，不放宽业务断言或fake timer时间。
- [ ] 跑 `npm --prefix "$WEB" test -- src/app src/auth/AuthProvider.test.tsx src/auth/LoginPage.test.tsx src/layout/AppShell.test.tsx src/housing`、构建、图检查；生产浏览器验证常见角色入口和切页，无重复React／query／auth实例，无全业务预取，全部PASS。
- [ ] 按提交约定提交本任务文件：`perf: lazy load ERP business routes`。

## Task 9: 天赋测试1按游戏加载

**Files:** 修改 `apps/web/src/talentTests/GameHost.tsx`、`TalentTestsPage.tsx`；新建 `talentTests/lazyGames.tsx`、`talentTests/GameHost.loading.test.tsx`；更新既有三个Host测试，游戏引擎／评分契约不改。

**Interfaces:** 保留 `GameHostProps={accountId,personId,sessionId,testId,onSessionCompleted?}` 及 `GameProps<K>={onComplete,onInterrupted}`；八个named export不变。lazyGames按TestId映射，DynamicVision额外保留 `rule?:DynamicVisionRule`、`onAttemptStarted?:()=>void`；模块就绪前仅占位，不出现可启动游戏控件，CSS和布局就绪后由原组件处理用户开始手势。

- [ ] 参数化八项 `host_loads_only_selected_game`，`expect(otherLoaders).not.toHaveBeenCalled()`；历史／报告不import游戏。写 `loading_is_not_attempt_time`：人为延迟import5000ms，尚未开始不调用onAttemptStarted/onComplete，准备后反应测试只记录原5次有效响应；隐藏时中断／重试保持原gameKey和终结去重规则。
- [ ] 运行`npm --prefix "$WEB" test -- src/talentTests/GameHost.loading.test.tsx`，确认RED。
- [ ] 仅把静态import及渲染接缝改成顶层lazy模块。保持dynamic-vision七位难度、音频用户手势、测量区域／随机／引擎版本、pending重试及会话完成跳转；不修改engine或评分来消除失败。
- [ ] `npm --prefix "$WEB" test -- src/talentTests` 全部PASS，特别fake timers必须先等模块挂载再推进；跑现有 `talent-tests.spec.ts`、`talent-touch.spec.ts`、`talent-dynamic-vision.spec.ts` 的隔离生产回归及按游戏资源请求断言。
- [ ] 按提交约定提交本任务文件：`perf: load talent games only when selected`。

## Task 10: 建档表单和大型弹窗按需加载

**Files:** 修改 `assistedPeople/AssistedPeopleRoutes.tsx`、`people/PersonDetailPage.tsx`、`assets/AssetsPage.tsx`、`procurement/ProcurementPage.tsx`；新建 `procurement/ProcurementDialogs.tsx`、`procurement/procurement-dialogs.css` 及上述模块的loading测试。Web短路径均位于 `apps/web/src/`。

**Interfaces:** AssistedPeopleRoutes继续 `{view:'mine'|'review'|'create'|'managed'}`；mine/review共享申请工作区，create/managed分开。AssistedPersonFormProps及initialValue只在mount应用的行为不变；PersonDetail纠正面板仅打开时载入。AssetQrDialog保留原props。采购四个现有内部组件 PurchaseFormDialog／InvoiceDialog／SettlementDialog／ReimbursementDialog移出并保留原props，父页面继续持有选择项和mutation，不新增独立缓存。

- [ ] 写 `details_dont_load_correction_before_open`、`asset_list_does_not_load_qr_before_open`、`procurement_list_does_not_load_dialogs`；打开后提交／双击／失败重试断言原mutation次数和同一operation key；局部加载失败父筛选和草稿不变。更换initialValue不自动覆盖已编辑值，账号切换则清除。
- [ ] `npm --prefix "$WEB" test -- src/assistedPeople src/people src/assets src/procurement` 中新用例先RED；实现只在open为true时渲染异步面板，必要纯类型／常量独立，不把qrcode或完整表单反向导入列表。
- [ ] 表单状态按原mount条件存活，不能用每次render新lazy或随query状态变化的key；采购dialog样式只迁与dialog有关规则，共享基础仍在任务7层；权限／版本／幂等key继续由原逻辑负责。
- [ ] 跑四域全单测、构建图和隔离生产的建档审核／纠正／采购／资产二维码路径；无新增业务请求或重复提交、导出格式不变，全部PASS。
- [ ] 按提交约定提交本任务文件：`perf: defer optional forms and heavy dialogs`。

## Task 11: 跨版本、性能和安全回归收口

用户后续批准的有限修订（不重开 Task1–10）：仅 Housing/People 在已认证 layout、当前权限及既有 capability 通过后准备原代码/CSS 闭包，实际导航前零业务挂载／私有查询；复用 controlled 工厂而非第二套预取缓存。覆盖匿名／强制改密／权限与 capability 拒绝、后台失败、登出／换号／撤权晚到完成和跨域 CSS（按 root 裁定仅三组 :where owner 限域）。沿原14case/38phase验收并补 setup/first 与整文档 bytes/counts/ScriptDuration，禁止将提前工作移出统计后宣称总成本消失。原280协议、基线与旧数值失败保留；本地 focused/fullWeb/build/lint/graph 后先独立复核，再单独授权实际 owned production 运行。两个 warm-login revalidation 用户接受注记与其余门禁分开。

**Files:** 扩展任务1runner／指标，新增 `apps/web/tests/frontend-loading.spec.ts`、`frontend-release-window.spec.ts`、`frontend-state-safety.spec.ts`；扩展 `scripts/test_frontend_loading_acceptance.py`；新建 `docs/operations/frontend-loading.md`。

**Interfaces:** runner三个phase固定，不接受任意外部URL／shell命令；`comparison.json`含 `jsTargetMet`、`readyRegressionPassed`、`sampleCounts`、失败分类和实际百分比。所有主指标来源任务1格式；业务suite复用现有seed、helpers及mock故障设施，不共享测试库并发reset。

- [ ] 写跨升级真实浏览器场景：A已开标签但未访问某模块→发布B→发布C，该A标签能取得旧模块及CSS；保留窗口外真实404时只显示可更新提示；正在填表时无自动刷新，用户取消更新草稿仍在。network abort、版本检查超时、缓存拒绝promise、同构建普通业务异常分别测，不只单测事件handler。
- [ ] 写状态场景：房态焦点回归保留筛选／页码，撤权清除；动态import延迟时换账号／退出，旧结果不回写；建档和验证码草稿不因focus丢失；未保存表单遇局部失败能继续操作。任务1runner验证旧版资源内容真正不同而非复用相同chunk掩盖问题。
- [ ] 扩展launcher测试至覆盖生产构建、禁生产配置／真实邮件、无截图／trace秘密、两次升级资源窗口、异常中断清理；先RED再完善runner，并执行 `node --test "$WEB/scripts/"*.test.mjs` 及 launcher纯测试至PASS。
- [ ] 跑 `npm --prefix "$WEB" test -- --maxWorkers=4`、`npm --prefix "$WEB" run build`、`npm --prefix "$WEB" run lint`；执行图预算和任务1 `--phase compare`，保留全部7次样本。目标不足或时间回归需定位、补测，不调整基线／采样阈值掩盖。
- [ ] 串行执行隔离生产auth／角色／注册激活／房态／人员／游戏的新增和现有browser矩阵，包含明确有权与无权角色；表单界面至少桌面1440及触屏390。天赋2继续跑`esports-pc.spec.ts`、`esports-touch.spec.ts`、`esports-recovery.spec.ts`、`esports-capabilities.spec.ts`对应矩阵；音频开始必须来自真实用户手势且有实际资源就绪断言。另对既有hardened `run-assisted-personnel-e2e.sh`分别传`--suite assisted`、`--suite team`、`--suite registration`、`--suite housing-occupancy`、`--suite housing-reporting`，每次一个进程。所需owned DB变量由本轮环境提供，不能拿development模式结果冒充生产加载验收。
- [ ] 文档记录前后资源图、总压缩字节、可操作中位数／最差值、失败计数、保留恢复结果及确实未覆盖设备，重新检查源码不含原生WIP。
- [ ] 按提交约定提交本任务文件：`test: verify loading gains and cross-release safety`。

## Task 12: 独立复核、恢复检查及 NAS 发布

**Files:** 仅修复复核发现且属于本规格的问题，更新 `docs/operations/frontend-loading.md` 的发布前状态；完成后在 `L/outputs/2026-10-18-frontend-loading-evidence/` 保存脱敏最终报告，不能预填成功。

**Interfaces:** 输入前11项实际退出状态、source digest、源码及derived输入manifest、候选immutable images；输出实际release ID、运行image ID、备份ID、恢复结果、公开资源响应和最终性能对比。

- [ ] 独立审查完整diff与规格覆盖，尤其权限重验、错误边界、实际L脚本和bootstrap。任何修复先加失败回归再改，并重跑受影响检查；不得用文档结论替代原始命令退出状态。
- [ ] 重新核对W/L staged／unstaged范围，重复双launcher部署及packaging测试、新资源/backup单测；任务5严格opt-in加密恢复必须针对最终Web镜像实际通过，不能复用早期候选证据。Python新增模块跑同一venv的Ruff；涉及类型化API helper时跑相应mypy，不因本轮未改业务而全跳脚本回归。
- [ ] 冻结验收源和指标，确认没有未解决阻断；从L执行 `scripts/deploy-nas.sh --source "$W"` 并保存退出状态。只运行正式脚本，不以额外Docker/copy绕过任何失败。真实发布前若活跃源发生变化，重新核对兼容性及派生前驱，不沿用旧imageID。
- [ ] 发布成功后只读检查 `.active-release`／deployment-state、运行Web `.Image`／manifest／build-info、HTTPS18080和当前及保留资源hash、API健康及原有后台任务。确认实际备份及隔离恢复记录；不登录真实人员去创建业务测试数据，不恢复NAS业务库。
- [ ] 交付实际节省比例和时间、已发布版本、剩余问题；任何未满足目标或检查必须明确列出。失败时报告阻塞和仍运行版本，保留回滚／前向修复所需镜像及数据，不尝试降级数据库。仅在全部完成后结束实施。

## 机制依据与计划检查

模块延迟和缓存失败语义依据 [React lazy](https://react.dev/reference/react/lazy)；动态资源更新处理依据 [Vite Load Error Handling](https://vite.dev/guide/build.html#load-error-handling)。历史构建输入隔离使用 [Docker named contexts](https://docs.docker.com/build/concepts/context/#named-contexts) 与 [Compose additional_contexts](https://docs.docker.com/reference/compose-file/build/#additional_contexts)；实现时核实实际Compose及builder支持，不能仅看到配置可解析就假定上下文被使用。

规格映射：测量→1／11，缓存与版本→2／6，资源保留→3／4，恢复→5，路由与共享依赖→7／8，游戏与面板→9／10，状态与安全→6／8／9／10／11，正式发布→12。五项Review Focus均已分配具体断言；所有任务沿用相同schema、CLI、指标口径和安全边界。当前只提交实施计划；用户确认后才执行任务1。
