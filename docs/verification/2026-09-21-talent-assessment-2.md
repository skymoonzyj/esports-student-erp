# 天赋评测 2 验证记录

日期：2026-09-22（中国时间）
状态：完整后端、前端和六组浏览器自动化已通过；真机验收尚未完成，四套保持关闭。当前待办为受控集成与 NAS 发布验证。
规格：`docs/superpowers/specs/2026-09-21-talent-assessment-2-design.md`

## 已完成证据

- 四套正式流程与四套练习隔离、HTTP 过期/幂等、私有读取、实时撤权、真实多连接锁竞争及旧迁移兼容组合：当前源码 **86 项通过**，单一 pytest 进程，退出码 0。
- 并发用例通过 `pg_stat_activity.wait_event_type = 'Lock'` 与 `pg_blocking_pids` 实际观察阻塞方和持锁方后才释放事务；不以 sleep 或线程已启动代替数据库阻塞证据。
- 正式与练习计数保持分离：四套正式共 16 条结果、4 份不可变报告；四次练习只产生 4 条 submitted practice，不产生正式成绩或报告。
- 未提交尝试在精确 `submit_until` 过期且不占用提交键；已提交的原 operation key 在截止时间后及套组撤下后仍返回同一不可变结果，不同 key 被拒绝。
- 随机 ID 与越权 session/attempt/report 返回一致私有 404；关联监护人仍不能读取进行中正式/练习明细；真实全局超级管理员可读；GET 不产生业务或审计写入。
- 自读权限实时拒绝后，context 立即取消 take/read 能力，recovery/history/session/detail 均不可见，已提交重放先鉴权后返回 403，已保存结果不变。
- 既有 system-evaluation launcher 默认兄弟工作树测试已改为完全位于临时目录的 ERP/评分 Git 根夹具：精确回归 1 项通过，同 helper 邻近回归 18 项通过；生产 launcher 未放宽。
- Task17 五个测试路径 Ruff lint 通过；四个完整自有测试/helper 文件 Ruff format 通过；scoped `git diff --check` 通过。
- 首次完整后端 JUnit `/tmp/erp-esports-api-20260922-final.xml`：**5209 tests / 1 failure / 2 skipped / 0 errors / 1015.515s**。唯一失败来自 Task17 真实限流 fixture 遗留两条独立提交的限流键，导致后续旧登录限流测试预期 2 行却读到 4 行；生产逻辑未失败。fixture 已保留真实限流并在 `finally` 精确清理本测试新增键，同一进程按“Task17 HTTP 集成 → 原失败用例”回归 **3 项通过**。这是历史 RED；已由下述冻结候选第二次完整重跑关闭。
- 独立静态审查指出重放响应直接相等断言在失败时可能打印完整评分载荷；现改为规范化 JSON 的 SHA-256 摘要相等，保持字典全等语义且失败只显示摘要。
- app/tests 全 Ruff 已退出 0。app mypy 首轮检查 331 个 source，发现 3 个类型错误：本任务拥有的 report results 可变长度 tuple 已改为显式四元组，旧 talent queries 的 2 个 status Literal 错误也已修复；这 3 个历史错误均已关闭。最终 mypy 重跑另发现下述 seed CLI 两处错误。
- 冻结候选 `c49bb979d7b3bd083ee2b581ff992c97d314182d` 的第二次完整后端单进程测试退出 0。JUnit `/tmp/erp-esports-api-20260922-final2.xml` 为 **5227 testcase nodes / 5227 unique identities / 0 duplicates / 0 failures / 0 errors / 0 skipped / 1050.106s**。`ERP_BACKUP_DOCKER_TEST=1`，因此两项 opt-in 加密备份 helper 用例实际执行；该缓存镜像证据不外推为当前应用镜像或 0060 schema 验收。运行后无备份临时容器残留。
- 同一候选的全 Ruff（`apps/api/app apps/api/tests`）退出 0。最终 app mypy 检查 331 个 source 时退出 1，仅剩 `app/cli/seed_esports_assessments_e2e.py:466` 与 `:691` 两个 `redundant-cast` 错误；完整 pytest 全绿不能替代该静态门禁。
- strict gate 修复 `8ec8616` 移除了上述运行时无效 cast，并在 `check.sh` 显式执行 nested config；精确报告与 `strict-gate-review.md` 均通过，完整 strict app mypy 对 **331 个 source** 检查成功。新增 mock 编排专项 **7 项通过**、pure 专项 **8 项通过**；这些专项结果不计入 c49 的 5227 项全量数。c49 的 5227 项完整后端在 `8ec8616` 后没有再次运行，因此不将该历史全量误报为 8ec 后证据。
- 最新前端完整测试为 **198 files / 1901 tests passed**，`npm run lint` 与 `npm run build` 均退出 0；该前端证据与后端 pytest 证据分别记录，不替代尚待完成的真实浏览器验收。
- 已完成一次广域源码独立评审及组合修复后的范围化复审（`final-source-review.md`、`final-source-rereview.md`），冻结候选 `c49bb979d7b3bd083ee2b581ff992c97d314182d` 的 F1–F6 均已关闭。该结论不替代真实浏览器/设备验收或 NAS 发布验证。
- Task18 第三次真实浏览器尝试的首个 PC 用例汇总为 **0 passed / 1 failed / 1 skipped / 0 retries**。后续单用例脱敏诊断确认 `pc_initial_trial` 期望 `trial`、实际 `preflight`，最终鼠标锁定为 false、页面 focus 与独立 WebGL2 探测为 true；最终未锁定不能证明从未获得锁定。`4fc1e46` 的诊断解析专项 **47 项通过**，不计入 c49 全量数。
- 独立空白 loopback 页面在 headless 和 headed Chromium 都拒绝原生鼠标锁定（`WrongDocumentError`）。之后仅对该自有 headed 浏览器 PID 请求真实 macOS 窗口激活，原生 API 成功（promise resolved、实际锁定、focus、secure context、canvas connected 均为 true）。无原生 API mock、无权限放宽、无游戏规则变更；此证据证明环境激活条件有效，ERP 完整流程仍须实测。
- 新增仅限 macOS 电竞桌面验收项目的自有浏览器窗口激活 helper：测试先观察 **6 失败 / 4 通过**，实现后 **10 项通过**，ESLint 与 TypeScript 检查通过。测试进程通过其自有 CDP 连接获取唯一数值型浏览器 PID，不按应用名称激活用户其他窗口，运行中不自动重获焦点。该工具修正尚未经过独立范围化复审。
- 本次仅为本地隔离验收临时打开四个 flag，退出后必须还原关闭；没有修改 NAS 或发布源，不能据此声称已开放。
- 原生焦点／Esc 修正后的实际 ERP 运行已通过进入试练、Esc 中断、重新试练、真实 60 秒 FPS 正式运行、单次服务端保存及输入生效断言，但随后验收制品检查误拒了 `real_active_60s` 数字标签，因此完整用例仍失败；不计为正式 E2E pass。该工具缺陷先复现 **2 失败 / 6 通过**，修正后 **8 项通过**；只允许受限 ASCII 标签和布尔值，未开放原始载荷记录。
- 当前测试工具源的完整前端回归 **200 files / 1922 tests passed**，JUnit `/tmp/erp-esports-web-20260922-native-tools.xml`；全 ESLint、TypeScript、Vite 构建退出 0。运行中仅补充了验收测试表的静态类型标注（无运行时变更）；原有大包警告仍存在。后端三个验收 CLI 专项 **68 项通过**，这些结果不叠加为后端全量数。
- 手机首个响应式用例现已首次通过：390／360 竖屏入口、空练习历史、横屏触控试练。完整手机能力组仍未通过，第二用例在倒计时中断，组级数据库核验尚未执行；不将单用例通过外推为手机套系可开放。
- 已修正两处验收定位问题：手机先打开“更多”导航；练习难度按 combobox 的无障碍名称选择。独立原生浏览器证明 wrapped select 的 exact label 查询未命中，而 exact role/name 查询命中；未修改生产导航或下拉框逻辑。
- 定位到真实计时缺陷：启动／输入使用 `performance.now()`，帧处理却使用 RAF 的此前绘制时间戳，可能在首帧误触时间倒退保护。实际脱敏诊断已区分为 `interruption_clock_invalid=true`，而非真实大于 500ms 卡顿；[浏览器文档](https://developer.mozilla.org/en-US/docs/Web/API/Window/requestAnimationFrame) 说明两者并非同一采样值。新增两项分离当前时钟与 RAF 采样的用例先失败，统一使用当前单调时钟后通过；真实 501ms 延迟仍中断，3 秒倒计时和 60 秒正式窗口不变。时钟／Host／质量／诊断专项 **46 项通过**，lint／TypeScript／构建通过；完整前端重跑和浏览器复验仍在进行，独立范围化复审未完成。
- 统一时钟后的完整前端重跑退出 0：**201 files / 1928 tests passed / 46.01s**，JUnit `/tmp/erp-esports-web-20260922-monotonic.xml`。该数字取代前述较早前端候选数，不与其累加。
- 实际浏览器诊断顺序暂调整为手机能力、手机正式、WebKit、PC、恢复、桌面能力，各组仍独立重建测试数据、保留全部用例与严格核验，没有跳过或重试放宽。手机能力组 **2 passed / 0 failed / 0 skipped / 0 retries**；数据库精确核验通过：正式轮次／尝试／结果／报告／审计均 0，练习 1 条中断记录。两项实际通过证明统一时钟修正有效，但尚不代表完整矩阵或真手机已验收。
- 上述诊断随后在手机正式组的输入响应断言失败（**0 passed / 1 failed / 1 skipped**），四个开关与矩阵顺序均恢复原状。只读隔离库核验确认两个实际 60 秒结果均已保存：FPS 的射击响应满足要求，MOBA 有取消攻击但次数不足。根因是测试手势约每 520ms 发送一次，正常的 1000ms 攻击冷却／2000ms 分轮过滤部分命令；现只把 MOBA 手势释放后的等待改为 2000ms，保留至少 3 次响应断言。纯引擎相邻回归 **24 项通过**；该修正的完整浏览器结果另行记录。
- `root-browser-fixes-review.md` 已完成对当前未提交 Host 时钟、原生自有 PID 输入、诊断脱敏和测试工具的独立范围化复审，结论 **PASS，未发现新增阻断问题**。复审未运行测试，不替代浏览器／真机结果，也不批准临时开关或临时矩阵顺序发布。
- 调整攻击间隔后，手机实际计时用例首次完整通过（两套均真实 60 秒、至少 3 次操作响应、单次保存）；模拟续测用例在 FPS 四项保存、报告已生成后未显示报告标题。相同隔离数据的只读浏览器对照确认：普通时钟 HTTP 成功且报告显示；冻结时钟 HTTP 成功但仍显示加载中；只推进 100ms 通知计时器后报告立即显示。不存在该对照中的 DTO 或权限错误。
- 现将模拟游戏时钟与非游戏 UI 等待分离：结束后才恢复正常计时，下一项暂停基于页面已推进时间；活动阶段不恢复、不跳过帧。新增 8 项测试先 **3 失败 / 5 通过**，实现后全通过；独立审查又发现 Host 卸载与两次定位之间的竞态，新增用例先失败后改为单次原子读取，最终 **9 项通过**，范围化复审 `simulated-clock-rereview.md` 已批准。未改动业务时钟或评分规则。
- 此竞态修正前最近一次完整前端为 **202 files / 1936 tests passed / 48.78s**，JUnit `/tmp/erp-esports-web-20260922-clock-lifecycle.xml`；全 lint、TypeScript、Vite 和浏览器 spec 严格类型检查通过。修正后 9 项与 scoped lint／严格类型检查通过；不把较早的全量数当作新增第 9 项已纳入的全量证据。
- 下一轮真实浏览器诊断再次通过手机能力组和真实 60 秒手机用例；模拟续测已成功显示 FPS 报告，但练习设置中的触摸拖动没有改变滑块。原生输入对照确认滑块初始位于可视区外，先滚动再取坐标后才会响应。测试现使用可见控件位置，并检查触摸起点确实命中画布；未直接注入控件值、游戏输入或成绩。
- 同一次原生对照也确认了产品布局缺陷：手机自由练习画布并非完整可见，中心可以接收输入，上沿却被页面导航遮挡。该缺陷与滑块测试工具问题分别处理，尚未把代码修改当作修复完成证据。
- 新增画布完整入屏与中心／四角命中检查；设置变更后的技能按钮位置来自可见控件。模拟用例总执行预算调整为 300 秒以容纳逐帧调度开销，真实用例、业务 60 秒期限及输入响应断言未改变。此范围的 lint／严格 TypeScript 通过，独立审查 `touch-geometry-tools-review.md` 为 **APPROVED / PASS**；不替代完整浏览器流程。
- 手机布局已改为进入试练前先固定沉浸布局、重测实际场地尺寸，直到返回时才退出；正式／练习、保存中／已保存／中断均保持布局。相同原生浏览器原场景复测退出 0：画布完整可见、中心命中画布、上沿命中画布三项均由先前的 `false / true / false` 变为 `true / true / true`。这证明原遮挡缺陷已在该浏览器场景修复，不代表真手机矩阵已通过；全量前端与独立复审另行记录。
- 布局修复的 TDD 为 **6 失败 / 26 通过 → 32 全通过**，独立 `mobile-layout-review.md` 结论为 **Spec / Quality PASS**。当前合并工作树完整前端退出 0：**202 files / 1943 tests passed / 49.71s**，JUnit `/tmp/erp-esports-web-20260922-mobile-layout.xml`；完整 ESLint、TypeScript、Vite 构建及四个浏览器 spec 的严格类型检查退出 0。已知旧的大包警告与监护人夹具重复 key 警告仍存在，没有隐去。
- 随后的浏览器组中，手机能力 2 项与真实计时手机用例通过，模拟续测在重排布局的预检被拒。独立代码核实与原生 UI 对照确认：测试把摇杆移动到隐藏攻击按钮附近，整套设置仍须校验全部按键，拒绝重叠是正确行为。额外真实拖动攻击按钮后立即进入试练；仅修正测试手势并增加无重叠告警断言，业务校验未修改。该增量 lint／严格类型检查及独立 `touch-geometry-tools-review.md` 补充复审通过，完整矩阵结果待后续记录。
- 合法控件布局后的当前运行已通过手机能力组 **2 项**及手机完整流程组 **2 项**，均无失败／跳过／重试。手机组精确数据库核验为 **2 个正式轮次、8 个尝试、8 条正式结果、2 份报告、2 条练习、4 条审计**。覆盖两套真实 60 秒、八项正式与两项练习、报告、按键重排和保存布局；这仍是浏览器模拟触屏，不是真手机证据。其余组尚在运行。
- 上述运行最终退出 0。`task-18-legal-controls-diagnostic` 六组汇总为 **12 个唯一用例通过 / 0 失败 / 0 跳过 / 0 重试**。顺序为手机能力、手机完整流程、WebKit、桌面完整流程、恢复、桌面能力；每组独立重建隔离数据并完成严格数据库核验。PC 组与手机组各自为 8 条结果、2 份报告、2 条练习；恢复组为 1 条结果、0 份报告、1 条练习；三组能力检查无正式成绩。运行后已恢复标准执行顺序以及前后端全部关闭的开关，三处文件无残留差异。
- 独立发布边界复核确认：允许在套系全部关闭时发布基础代码，但不能称其已开放或真机验收完成。当前 Windows Chrome／Edge、真实 iPhone Safari／Android Chrome，以及正式 Safari 应用矩阵均未验收；自动化 WebKit 不冒充实际 Safari。拟发布范围为独立模块基础代码，**开放套系为零**。
- 恢复关闭开关后的发布候选再次完整前端回归退出 0，JUnit `/tmp/erp-esports-web-20260922-release.xml`：**202 文件、1943 个 testcase 条目、0 失败／错误／跳过**。按 classname/name 合并为 1931 个名称组合，部分参数化测试同名，因此不把 1943 称为唯一名称数。全部后端源和测试 Ruff 再验通过，strict mypy **331 个源文件**通过。自 c49 的后端全量通过后，API 目录仅验收 CLI 有运行时无效 cast 清理和脱敏诊断改动；业务后端未改，CLI 与六组实际运行已有独立回归。

专项数据库命令：

```sh
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" "$PY" -m pytest \
  apps/api/tests/api/test_esports_integration.py \
  apps/api/tests/db/test_esports_concurrency.py \
  apps/api/tests/api/test_esports_privacy.py \
  apps/api/tests/db/test_esports_migration.py \
  apps/api/tests/services/test_esports_queries.py \
  apps/api/tests/services/test_esports_formal.py \
  -q --tb=short -o addopts=''
```

结果：**86 passed in 12.95s**，无失败或跳过。

## 尚未验证

- [x] Task18 六组浏览器自动化与响应式模拟验收，12 个唯一用例全部通过。
- [ ] Task18 真实 Windows／iPhone／Android 与实际 Safari 应用验收；全部套系保持关闭。
- [ ] `scripts/deploy-nas.sh` 发布、NAS 健康、Alembic revision、调度心跳、加密备份及隔离恢复。

专项通过数存在与其他任务测试重叠的可能，不累加为全量用例数。任何未勾选项均不代表已通过；本文件目前不构成上线或发布完成声明。
