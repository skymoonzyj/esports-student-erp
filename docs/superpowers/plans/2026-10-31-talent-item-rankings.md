# 天赋测评独立单项排名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 天赋测评 1 每项保存后立即展示个人最佳、本次对照排名、匿名前三及差距，不等待整套完成。

**Architecture:** 新增结果级排名接口，使用现有单项结果和冻结规则；SQL 按人选单项最佳，再计算名次与前三。新响应与前端面板可供实体灯复用，但旧的整轮排名接口和组件保持原义。

**Tech Stack:** 现有 Python 3.12、FastAPI、SQLAlchemy、PostgreSQL、React、TypeScript、Zod、Vitest、Playwright；不新增排行榜服务或缓存产品。

**Spec:** [已确认规格](../specs/2026-10-31-reactionx-erp-integration-design.md)，重点“独立单项排名与前三成绩”。总编排见 [ERP 接入计划](2026-10-31-reactionx-erp-integration.md)。

## Global Constraints

- 每人每项历史最佳入榜、本次成绩单独对照；同一人只占一个位置；匿名前三最多三行。
- 同分按 `1、1、3` 并列；行顺序按最佳记录较早保存时间及内部 ID 稳定排序，不将其用作打破并列的条件。
- 天赋测评 1 仍按服务端该项 0–100 分排序；不修改评分锚点、旧综合榜或天赋测评 2。
- 结果读取保留 `TalentAccess.require_session()` 的未完成轮次限制，不因新增排名扩大监护人或教练权限。
- 实际 ERP 源为 `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`；已有未提交改动必须保留。执行前核验该路径仍为本任务集成源；不得在旧主目录另做一套。
- 当前源是 detached linked worktree；每任务保存核对清单，不自动 stage/commit/push，也不借提交钩子触发半成品部署。后续若改为独立干净分支，再逐任务定向提交。
- 数据库测试必须使用隔离 `TEST_DATABASE_URL`，同库测试进程串行；skip 不算通过。没有 `scripts/run_owned_tests.py`，不得引用不存在的运行器。

## Review Focus

1. 同一个人已有高分而本次低分：不被自己的旧成绩挤低对照名次，也不覆盖最佳，A2 验证。
2. 未完成整轮或其他项目质量异常：本项完整有效可入榜，但跨人读取部分轮次仍受原权限限制，A2 验证。
3. 名次并列超过三人：只返回三行，名次不被强行改成 1/2/3，并提示截断处同分人数，A1/A2 验证。
4. 新成绩保存成功但排名请求失败，或切账号后旧响应返回：不伪造名次、不串账号、不阻塞下一项，A3 验证。
5. 没有激活账号的合法档案与停用账号：无账号不等于无资格；账号停用、人员删除按规则排除，A2/A4 验证。

---

## 文件及责任边界

- 新建 `apps/api/app/assessment_rankings/{__init__,schemas}.py`：跨网页/实体灯的匿名单项排名响应，不依赖具体业务模型。
- 新建 `apps/api/app/talent_tests/item_ranking.py`、`item_ranking_keys.py`：本项可比条件、资格、SQL 最佳/名次/前三查询。
- 修改 `apps/api/app/talent_tests/router.py`：新增结果级 GET；`ranking.py` 的整轮计算不改。
- 新建 `apps/web/src/assessmentRankings/{itemContracts.ts,ItemRankingPanel.tsx,ItemRankingPanel.test.tsx}`；修改同目录 `rankings.css`。
- 修改 `apps/web/src/talentTests/{api.ts,GameHost.tsx,TalentReportPage.tsx}`，给已保存单项及历史单项接入同一面板；不替换旧综合榜。
- 数据库索引若需要，由 A4 独占新增迁移；总计划 B2 在 A4 之后追加迁移，禁止两路同时猜测下一个 revision。

## 测试命令约定

在 ERP 根目录设置 `PY=/Volumes/MacDev/Projects/new\ project3/.venv/bin/python`，先核验它可执行；下列后端命令从 `apps/api` 运行。`TEST_DATABASE_URL` 由隔离测试环境提供，运行前用 `"$PY" -m app.cli.e2e_database_safety` 核验，不读取或复制 NAS 凭据。前端命令从 ERP 根目录执行。

### Task 1: A1 匿名单项排名响应契约

**Files:** 新建公共 schemas、`tests/unit/test_item_ranking_contracts.py`；新建前端 `itemContracts.ts`、`itemContracts.test.ts`。

**Interfaces:** 产生 `ItemRankingView` 与 TypeScript 同名类型。字段固定为 `status: ranked|not_eligible`、`reason`、`group_label`、`calculated_at`、`participant_count`、`metrics`、`current`、`best`、`top_three`。`metrics` 包含 `key,label,unit,better(higher|lower),decimals`；standing 为 `rank,values`；前三行额外含 `is_self,tie_count,gaps`，gap 为 `key,amount,state(ahead|behind|equal)`。`values` 为指标名到有限 JSON number 的映射，Decimal 的输出序列化显式处理，不能一端 number、一端字符串；内部比较使用原始整数/精确比值，不依赖格式化数字。不得包含其他人的任何 ID。`not_eligible` 需明确 reason，排名值均为空。

- [ ] 写失败测试，锁定有限数字、前三长度、匿名字段白名单与名次范围；示例断言：
  ```python
  assert view.participant_count == 4
  assert [row.rank for row in view.top_three] == [1, 2, 2]
  assert not ({"person_id", "account_id", "report_id"} & view.top_three[0].model_dump().keys())
  ```
  拒绝 NaN、Infinity、非法 metric key、负 gap、缺参榜人数的 ranked、带排名的 not_eligible。并列 `tie_count` 是该成绩同分总人数，不限前三内人数。
- [ ] 运行 `"$PY" -m pytest tests/unit/test_item_ranking_contracts.py -q` 和 `npm --prefix apps/web test -- src/assessmentRankings/itemContracts.test.ts`，确认因新契约缺失而失败。
- [ ] 实现严格 Pydantic/Zod 契约与中文不可参榜原因映射：`quality_warning`、`incomplete_result`、`incompatible_input`、`missing_comparison_metadata`、`subject_inactive`、`practice_only`、`mode_unverified`。数值只接受对应版本声明的指标；现有 `RankingView` 不改。
- [ ] 重跑两条命令，预期 0 failed；核对差异仅覆盖本任务文件，记录完成情况，不自动提交。

### Task 2: A2 数据库单项最佳与对照排名

**Files:** 新建 `item_ranking.py`、`item_ranking_keys.py`、`tests/api/test_talent_item_ranking.py`；修改 `router.py`；复用 `models.py`、`access.py`。

**Interfaces:** 消费 A1 `ItemRankingView`；产生 `TalentItemRankingService(db, access).for_result(result_id: UUID) -> ItemRankingView`，以及 `GET /api/v1/talent-tests/results/{result_id}/ranking`。`build_item_group(session: TalentTestSession, result: TalentTestResult) -> ItemGroupKey | None` 只提取该项版本、规则、引擎、语言与输入类型，不使用其他项目完成状态或整轮组合键。

- [ ] 从现有 `tests/api/test_talent_ranking.py` 的 `seed_round()` 构造局部结果 fixtures，写失败测试：A 最佳 92、本次 89；B=95、C=92、D=70，断言 A 最佳名次 2、本次对照名次 3、人数 4、前三分数 `[95,92,92]`、本次落后 `[6,3,3]`。再覆盖只有 1 人、三行外同分、连续重复提交、单项有效但整轮 abandoned 或未完成、另一项质量警告、同名不同 person、一个 person 多账号操作、旧版本缺字段、鼠标与触屏分组。
- [ ] 运行 `"$PY" -m pytest tests/api/test_talent_item_ranking.py -q`，确认失败来自新能力缺失，不是测试环境跳过。
- [ ] 实现权限先行、SQL 去重与一条一致快照查询：每人先按本项 score DESC、created_at ASC、id ASC 取一条，再按严格更优人数计算竞争名次；本次名次计数排除本人。候选包含有效历史单项（即使未做完其他项），但不包含该单项无效/作废记录。人员未删除，且没有账号或至少一个账号 active；不能 inner join 要求人人有账号。新接口先 `require_session()`，所有响应 private/no-store，沿用匿名 404，不泄漏资源是否属于别人。
- [ ] 运行新 API 测试及 `tests/api/test_talent_ranking.py tests/services/test_talent_access.py tests/services/test_talent_atomicity.py`，0 failed 且数据库用例不 skip；验证旧整轮契约未改变。

### Task 3: A3 每项保存页和历史页展示

**Files:** 新建 `ItemRankingPanel.tsx` 与测试；修改 `talentTests/api.ts`、`GameHost.tsx`、`TalentReportPage.tsx`、相应测试和 `rankings.css`。

**Interfaces:** 消费 A1 类型及 A2 路由；产生 `getTalentItemRanking(resultId: string, signal?: AbortSignal): Promise<ItemRankingView>` 和 `ItemRankingPanel({view,loading,error,onRetry})`。请求状态与展示分离，实体灯 B5 可复用面板而不依赖 talent API。

- [ ] 写失败测试：只有服务器确认 saved 才取榜；展示前三、本次/最佳两种名次、分差、同分提示、计算时间；自己进入前三标“本人最佳”。排名错误不隐藏已保存成绩或“下一项”；未保存/待同步不伪造空榜；切换账号时晚响应不回填；历史报告每项按已保存 result ID 取当前榜，原综合排名仍可见。
- [ ] 运行 `npm --prefix apps/web test -- src/assessmentRankings/ItemRankingPanel.test.tsx src/talentTests/GameHost.test.tsx src/talentTests/TalentReportPage.test.tsx`，预期新增断言先失败。
- [ ] 实现面板、当前身份绑定的查询键及 AbortSignal。保存、打开、回前台和手动重试刷新；不每秒轮询。重新查看的排名标题明确“当前数据库排名”；身份变化清空旧数据。沿用 A/B 主题、窄屏堆叠和中文文案，不一次加载其他业务页面。
- [ ] 重跑定向测试，再运行 `npm --prefix apps/web run build` 与 `npm --prefix apps/web run lint`，全部通过后记录截图及移动窄屏检查结果。

### Task 4: A4 历史兼容、查询规模与端到端发布关卡

**Files:** 新建 `tests/services/test_talent_item_ranking_query.py`、`apps/web/tests/talent-item-ranking.spec.ts`；按实测修改 `talent_tests/models.py`、新增当前 head 后的单一索引迁移，并同步 `scripts/db-schema-manifest.sh`、`tests/db/test_migrations.py`；修改 `scripts/run-core-e2e.sh` 和 `tests/cli/test_check_e2e_orchestration.py`。

**Interfaces:** 保持 A2 路由/响应不变；产生已登记到 `talent` E2E 组且只运行一次的新用例。

- [ ] 写失败测试：10,000 条隔离合成历史只返回目标、本组计数及三行，不向应用拉回全表；删除/停用候选后重算、并发保存后同一响应的最佳/前三/人数一致。限制排名 SQL 数量不随历史增长，比较 100 与 10,000 条两种样本；保留 EXPLAIN 输出用于索引选择，不把共享 Mac 的单次耗时作为唯一门槛。
- [ ] 运行 `"$PY" -m pytest tests/services/test_talent_item_ranking_query.py tests/cli/test_check_e2e_orchestration.py -q`；新增 E2E 先验证在既有运行器中缺功能时失败，而不是缺令牌或端口冲突。
- [ ] 据查询计划添加本项与 session/person 过滤需要的索引，不改写旧成绩。若源 head 仍为 `0078_hk_mo_permit_documents`，本计划索引 revision 使用 `0079_talent_item_ranking_indexes`；若已变化则重新顺接，不造双 head。无性能证据需要索引则不制造空迁移。E2E 覆盖两人单项保存、重复测试、前三与差距、退出后保护。
- [ ] 运行 `"$PY" -m pytest tests/api/test_talent_item_ranking.py tests/api/test_talent_ranking.py tests/services/test_talent_item_ranking_query.py tests/db/test_migrations.py tests/cli/test_check_e2e_orchestration.py -q`，再按隔离环境 `E2E_CORE_MODE=talent scripts/run-core-e2e.sh` 和相关前端回归。数据库用例不得跳过。
- [ ] 发布交主计划 B6 统一完成；若只独立执行 A，须确认待发布源不包含未完成的 B/C 改动，审查及适用全量检查后才执行 `scripts/deploy-nas.sh --source "$PWD"`。失败阻断发布，不手工复制或从共享脏工作树抢先发布。线上只读确认页面资源/版本/路由与既有综合榜正常，不制造真实学员测试分数。没有现成有效数据时如实标记真实成绩展示待验收。

## 本计划完成标准

单项保存后即能独立读榜；本次/最佳/前三语义及匿名权限验证通过；旧整轮榜和网页测试未回归；查询规模已验证；NAS 发布和只读核验有实际结果。此计划完成不代表 ReactionX 连接器或真实灯具已完成。
