# 动态视力与八项天赋测试验证

日期：2026-09-21（中国时间）
状态：已完成并发布到NAS；源码/镜像、服务健康、迁移、调度心跳及加密恢复全部核验通过。
规格：`docs/superpowers/specs/2026-09-20-talent-dynamic-vision-design.md`
计划：`docs/superpowers/plans/2026-09-21-talent-dynamic-vision.md`

## 范围

新增动态视力（瞬时数字识别）：任选一级，只测五题，正确数×10×难度系数；新轮次八项完成后生成报告。旧七项轮次和历史不重算。无外站运行时依赖，不保存题目或答案，不增加角色权限。

## 实施前已核实的基线

- 当前发布源 `.nas-release-sync`，分支 `codex/audit-repairs-20260914`，HEAD `d6e1accd0692dca155b12e1197d0fc154f2e832c`；大量原有未提交修改保留，新增任务有逐文件基线档案。
- 本轮开始前服务端契约/计分纯单测160项通过，退出码0。
- 隔离 PostgreSQL 健康、仅 localhost:55433；e2e_database_safety 退出码0。数据库与浏览器测试采用单运行者。
- NAS部署脚本 dry-run 退出码0：本地0058，线上0057。未更改线上源码或容器；发布前仍需针对0059重新验证。
- 先前七项版本完整前端1390项通过、核心浏览器70通过/3视口条件跳过、七项桌面与真实触屏各1通过属于旧基线，不是本次八项结果。

## Task 5 浏览器验收结果（隔离本地环境）

已完成测试、harness 静态检查和最终浏览器验收：

- 原桌面和真实触屏整轮用例扩为八项；在原七项完成时明确断言 `7/8`、无报告，动态视力保存后才允许唯一报告。
- 新增独立动态视力用例，计划在 1440、1024、768、360、390 触屏五个项目逐一重建隔离 fixture 后运行；覆盖六位数字无横向溢出、可聚焦数字输入、手机高度变化不误中断、真实宽度变化中断、后台中断、第四题前零写入、第五题响应丢失后原提交键重放和服务端唯一结果。
- 混合版本 fixture 为同一合成学员提供一个确定性的 v1 进行中轮次；直接访问动态视力只显示“不包含该测试项目”，放弃后才开始 v2 八项轮次。
- 三个 talent spec 均显式关闭 trace、video 和失败截图；仅允许保存说明/进度/不含答案的报告截图。浏览器诊断只保存固定事件类别计数；harness 不再持久化或回显 talent Playwright 原始日志。
- 隔离库后置检查将验证动态结果 `raw_result` 与 `normalized_result.raw` 仅含四个允许键、设备快照独立存放、审计元数据不含题目/答案/原始成绩载荷，并只输出模式和计数。

未触发数据库或浏览器进程的静态证据：

```text
npm --prefix apps/web run test:e2e -- --list talent-dynamic-vision.spec.ts talent-tests.spec.ts talent-touch.spec.ts
Total: 15 tests in 3 files
exit=0

apps/web/node_modules/.bin/eslint apps/web/tests/talent-tests.spec.ts apps/web/tests/talent-touch.spec.ts apps/web/tests/talent-dynamic-vision.spec.ts
exit=0

bash -n scripts/run-core-e2e.sh
exit=0

PYTHONPATH="$PWD/apps/api" "$PY" -m pytest -q apps/api/tests/cli/test_check_e2e_orchestration.py::test_core_launcher_is_serial_and_reseeds_every_viewport_spec_pair apps/api/tests/cli/test_check_e2e_orchestration.py::test_complete_gate_owns_every_playwright_spec_exactly_once
2 passed, exit=0

"$RUFF" check apps/api/app/cli/seed_talent_e2e.py apps/api/tests/cli/test_seed_talent_e2e.py apps/api/tests/cli/test_check_e2e_orchestration.py
All checks passed, exit=0

PYTHONPATH="$PWD/apps/api" "$MYPY" apps/api/app/cli/seed_talent_e2e.py apps/api/tests/cli/test_seed_talent_e2e.py
Success: no issues found in 2 source files, exit=0
```

后续获批的单文件隔离库证据：

```text
PYTHONPATH="$PWD/apps/api" "$PY" -m pytest -q apps/api/tests/cli/test_seed_talent_e2e.py
......                                                                   [100%]
6 passed, exit=0
```

该命令覆盖生产环境拒绝、fixture 幂等与真实关系、确定性 v1 旧轮次、动态结果/设备/审计隐私形状。首次环境装载把 shell 引号误作值的一部分，运行时密钥校验在测试收集前拒绝；纠正为只解释 `scripts/check.sh` 公共本地段中七个白名单 `export` 后通过。首次输出已保留，不计作产品 RED。

审查修复后的最终 CLI 组合运行：

```text
tests/cli/test_seed_talent_e2e.py
tests/cli/test_summarize_talent_playwright.py
tests/cli/test_check_e2e_orchestration.py
23 passed, 0 failed, 0 errors, 0 skipped, exit=0
```

完整后端结果按唯一测试身份与该组合 XML 去重核对：共 4818 个身份，4816 通过、2 个显式 opt-in 跳过，没有遗漏或额外身份。最终完整前端为 145 个文件、1468 项通过；lint、build、Ruff、mypy 均退出0。

最终浏览器/数据库矩阵：

| 范围 | 项目/视口 | 通过 | 跳过 | 失败 | 状态 |
| --- | --- | ---: | ---: | ---: | --- |
| 原七项+新增第八项整轮 | desktop-1440 | 1 | 0 | 0 | 通过；16结果、2报告、6审计 |
| 原七项+新增第八项整轮 | touch-mobile-390 | 1 | 0 | 0 | 通过；8结果、1报告、2审计 |
| 独立动态视力 | desktop-1440 | 1 | 0 | 0 | 通过；1结果、0报告、2审计 |
| 独立动态视力 | desktop-1024 | 1 | 0 | 0 | 通过；1结果、0报告、2审计 |
| 独立动态视力 | tablet-768 | 1 | 0 | 0 | 通过；1结果、0报告、2审计 |
| 独立动态视力 | mobile-360 | 1 | 0 | 0 | 通过；1结果、0报告、2审计 |
| 独立动态视力 | touch-mobile-390 | 1 | 0 | 0 | 通过；1结果、0报告、2审计 |

最终 talent harness 仅使用 `scripts/check.sh` 的公开本地测试配置段，7个结构化摘要全部为 `passed=1 skipped=0 failed=0 flaky=0 retries=0`，退出0。五个独立项目都到达隐私安全的 `saved_verified` 终态，覆盖几何/后台中断、难度锁定、第五题响应丢失、相同提交键恢复、待保存与已保存安全摘要以及存储隐私。

随后串行执行未改动的 core harness：24个 spec/项目组合，73通过、3个原有视口条件跳过、0失败，退出0。最终确认 localhost 8014、5188 可绑定，且无遗留本地 API、Vite 或 Playwright 进程；数据库/浏览器租约已交还。

## 本轮发布门槛

- [x] 严格契约、全部30组分数及边界
- [x] 0059迁移保留数据、拒绝不兼容降级
- [x] v1七项/v2八项完成与旧键重放
- [x] 报告、同条件比较及隐私权限回归
- [x] 五题交互、重复操作、恢复与中断
- [x] 五视口、真实鼠标/触屏与既有业务浏览器验收
- [x] 最终完整后端/前端测试、静态检查和构建
- [x] 独立代码审核及必要修复复核
- [x] 指定脚本NAS部署与线上入口核实
- [x] 迁移头、源码/镜像、健康、调度心跳、加密备份隔离恢复

未勾选项仍由对应实现/发布证据确认。任何本地测试通过均不等同于功能上线；生产环境不创建模拟测评记录。

## 控制代理最终核验

- 后端完整运行4798项，后续仅对变更模块补测；按最新唯一用例与4818项收集清单核对，4816通过、2个显式Docker演练opt-in跳过，无遗漏、失败或重复累加。实际NAS加密恢复仍须执行。
- 最终前端145文件/1468项通过，39.84秒；ESLint、TypeScript、生产构建、API Ruff及308源文件mypy通过。既有大包提示仍在（JS1470.21kB、gzip382.96kB），不是构建失败；本轮未扩大为整站拆包重构。
- 原整体复核及所有具体修复的定向复核通过。真实浏览器发现并最终验证了中断提示干扰难度选择的问题；动态视力现在由成功开始回调清除提示，配置难度时不触发父组件刷新。最初单测通过但浏览器失败的版本不算最终证据。
- 最终截图取自browser-final3，已检查桌面及触屏八项报告、规则表、计算式和训练参考说明。全部测试账号/成绩仅在隔离本地库，不对NAS写入模拟结果。
- 0059降级要求先停止新版本写入并保持停止至兼容旧应用启用；数据库锁只保护迁移事务期间。已有v2数据时拒绝降级，不删除记录。

## 实施取舍记录

1. 沿用现有发布目录及未提交成果，不从干净HEAD另建副本；重叠修改风险以文件归属和逐文件快照控制。
2. 冻结接口后并行推进独立模块；接口不匹配会增加返工，因此先通过契约门槛。
3. 不stage/commit或删除开发证据，避免提交钩子提前发布；代价是保留本地档案且没有逐任务提交历史。
4. 完整验证集中在集成版本，数据库测试串行；代价是局部通过不能代表整个系统可发布。
5. 仅按需扩展v2审计白名单；类型及隐私放宽风险以异常输入、负向测试和私有404无写入回归控制。
6. 新浏览器用例登记到唯一执行归属检查；否则可能漏跑，保留严格编排测试。
7. 旧目录刷新冲突使用固定中文错误映射；避免错误提示混淆或泄露，保留no-store和私有错误测试。
8. 冻结产品时先做整体审核，后续只审核明确变更及运行证据；晚期接口变化可能遗漏，因此显式交接，并以最终浏览器/发布门槛把关。

## 发布打包修复

首次指定脚本运行退出1：生产Docker镜像按包显式复制，却漏了app/talent_tests，迁移入口导入即失败。迁移前备份已创建；只读核对数据库仍为0057、原有容器健康，没有执行数据迁移或手工回滚。脚本保留候选目录以便向前修复，未绕过任何门槛。

已补充显式COPY，仍不把E2E种子CLI打入生产镜像。打包回归从应用/迁移入口AST收集必需模块，RED精确发现缺失talent_tests，修正后运维文档完整模块71项通过。定向代码复核通过。以完整模块结果替换原测试身份后，重新收集核对仍为4818唯一用例、4816通过、2个opt-in跳过、无遗漏。Docker打包修正未改变已验收的应用逻辑与前端。

## 最终NAS发布证据

指定主工作区脚本第二次执行退出0：

```text
scripts/deploy-nas.sh --source '/Users/skymoonzyj/Documents/new project3/.nas-release-sync'
release=20260920T180605Z-d6e1accd0692-8435827b2cec
deployment_state=complete
alembic=0059_talent_dynamic_vision
```

- 发布前已生成0057数据库备份；正常应用0058、0059迁移，没有删除业务记录或数据库卷。
- 活动目录以NAS的`.active-release`核实；运行期源码/镜像校验命令退出0。
- API/Web/proxy/PostgreSQL均healthy，Caddy/scheduler运行；teaching_scheduler心跳healthy、连续失败0、120秒内更新。
- 公网`https://skymoonzyj.l.cd:18080/login`及健康接口HTTP200，TLS验证结果0；未登录访问天赋测试上下文HTTP401。
- 公网与本地构建均为`/assets/index-BEF8AnnO.js`，SHA256一致：`9bd2ba60cbb04fbc18eec91697f8a1af9a595a0ad9b3a3084dd9a3723fff125b`。
- 加密备份`backup-20260920T180700Z-89f523ca`完成并经过真正隔离恢复：119张表、16个加密字段样本、1个媒体文件。恢复标志encrypted=true、verified=true；核验时间中国时间2026-09-21 02:09:17。
- 本轮未在生产环境创建模拟学员、成绩或财务记录，未stage/commit/push，原有未提交改动保留。

本节为发布完成后的本地证据补记；NAS不可变源码快照中的本文停留在发布前状态。发布后没有再修改应用、测试或Docker代码，无需因文档补记重新部署。所有任务快照、失败诊断和最终日志保留在本地该计划的`.superpowers/sdd/`档案中。
