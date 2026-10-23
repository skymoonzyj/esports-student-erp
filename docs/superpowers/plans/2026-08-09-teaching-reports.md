# Teaching Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Every production change follows superpowers:test-driven-development, and every completion claim follows superpowers:verification-before-completion.

**Goal:** 交付绑定正式报名的教练日报/周报：按中国时间生成可填写周期，支持多人独立意见、提交审核、驳回整改、一次次受审核的发布修订，以及学员/有效监护人只读已发布内容。

**Architecture:** PostgreSQL 保存评语资源、不可变正文版本和追加式审核记录；纯函数负责 `Asia/Shanghai` 日/周周期及截止时间。API 先用共享评价授权选择器隐藏不可见报名，再由服务层在同一事务内锁定报名和评语行、验证状态与时钟、写版本和审计。React 用账户与授权 revision 隔离查询缓存，作者、审核人和家庭端使用不同投影。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic 2、Pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright

---

## Frozen scope and routes

- 工作树：在实际执行时使用当前隔离 worktree；本文不绑定固定路径。
- 分支：`codex/teaching-feedback-surveys`
- 不修改 score-tool、Docker、NAS、GPU、录像、付款和公安接口。
- 每条评语绑定一个 `enrollment_id`；不同作者拥有各自资源，互不覆盖。
- 正文必填，`dimension_values` 仅保留空 JSON 对象扩展位，不显示未确认评分维度。
- 状态严格为 `draft`、`pending_review`、`rejected`、`correction_draft`、`published`、`revision_pending_review`。
- 作者不得审核自己；家庭端永远只读取 `published_version_id`，待审/驳回修订不得覆盖已发布投影。
- 日报按上海自然日，不生成未来日期；首次提交最晚到结营当地日期后第 3 个自然日结束。
- 周报从开营当地日期每 7 日切分；最后不足 7 日仍生成。非末周最晚在下一周期结束时提交，末周最晚在结营后第 7 个自然日结束提交。
- 周报周期开始后可保存草稿，但必须等该周期当地结束日完全结束后才能首次提交；日报从对应当地日开始即可保存和提交。
- 驳回后整改窗口为 3 个上海自然日；管理员或获 `student_reports.review` 权限者可填写理由重新开放。

```text
GET    /api/v1/evaluations/reports/context
GET    /api/v1/evaluations/reports/slots?enrollment_id=
GET    /api/v1/evaluations/reports?enrollment_id=&kind=&status=&page=&page_size=
GET    /api/v1/evaluations/reports/{report_id}
PUT    /api/v1/evaluations/reports/{report_id}/draft
POST   /api/v1/evaluations/reports/{report_id}/submit
POST   /api/v1/evaluations/reports/{report_id}/correction-drafts
POST   /api/v1/evaluations/reports/{report_id}/revisions
GET    /api/v1/evaluations/reports/review-queue?cohort_id=&project_id=&class_id=&page=&page_size=
POST   /api/v1/evaluations/reports/{report_id}/reviews
POST   /api/v1/evaluations/reports/{report_id}/reopen
GET    /api/v1/evaluations/students/{student_id}/published-reports?enrollment_id=&page=&page_size=
```

所有写路由要求唯一 `Idempotency-Key`，使用服务器生成审计 request id；私有对象的缺失、删除、停用、无关系、错误范围与 deny override 返回相同 404，完全没有相应全局能力才返回 403，失败路径零业务/幂等/审计写入。

## Dependency map

| Task | Boundary | Depends on |
| --- | --- | --- |
| 1 | 周期、截止时间、状态机纯函数 | 冻结规格 |
| 2 | 模型、迁移、权限与安全降级 | Task 1 |
| 3 | 作者选择器、草稿与列表投影 | Task 2 |
| 4 | 提交、审核、整改、修订与并发 | Task 3 |
| 5 | API、隐私、审计与上下文能力 | Task 4 |
| 6 | 作者/审核/家庭响应式页面 | Task 5 |
| 7 | 四视口 E2E 与单元总门禁 | Tasks 1–6 |

共享评价模型、路由、schema、权限文件只能串行修改；只读探索和独立审查可并行。

### Task 1: Freeze report periods and transitions

**Files:**

- Create: `apps/api/app/evaluations/report_periods.py`
- Create: `apps/api/app/evaluations/report_state.py`
- Create: `apps/api/tests/unit/evaluations/test_report_periods.py`
- Create: `apps/api/tests/unit/evaluations/test_report_state.py`

- [ ] 先写参数化失败测试，覆盖上海跨 UTC 日期、闰日、未来日报、21 日三周、8/20 日不足周、日报当地日开始提交、周报周期中仅草稿/结束后提交、非末周截止、末周结营后 7 日、日报结营后 3 日、驳回后 3 日和 naive datetime 拒绝。
- [ ] 运行 RED：

```bash
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/unit/evaluations/test_report_periods.py \
  apps/api/tests/unit/evaluations/test_report_state.py -q
```

预期因两个模块不存在失败。

- [ ] 最小实现不可变 `ReportPeriod`、`daily_periods`、`weekly_periods`、`submission_opens_at`、`submission_deadline`、`correction_deadline`，以及只允许冻结边的 `require_report_transition`。
- [ ] 重跑同一命令 GREEN；Ruff 与 mypy 只检查新增模块。

### Task 2: Add persistence, migration 0014, RBAC and downgrade guards

**Files:**

- Create: `apps/api/app/evaluations/report_models.py`
- Create: `apps/api/migrations/versions/0014_teaching_reports.py`
- Create: `apps/api/tests/db/test_teaching_report_models.py`
- Modify: `apps/api/app/evaluations/models.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/evaluations/staff_service.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `apps/api/tests/services/test_evaluation_staff.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] RED 测试冻结五张表 `student_reports`、`student_report_drafts`、`student_report_versions`、`student_report_reviews`、`student_report_window_grants`，使用评价中心预留稳定 ID 032 `student_reports.write` 与 033 `student_reports.review`，仅 admin 默认全局授权；两权限均可作为届次工作人员细粒度权限。不得给 coach 角色直接 seed `student_reports.write`，否则助教也会继承；报告选择器只把周期重叠的 primary 主教练视为默认作者，助教仍需显式有限范围授权。
- [ ] 模型约束：评语唯一键为 `(enrollment_id, report_kind, period_start_date, author_person_id)`；日报周期首尾相同，周报 1–7 日；版本 `(report_id, version)` 唯一且正文去空白非空；审核只能 `approved/rejected`；全部业务外键 `RESTRICT`；索引覆盖作者待办、审核队列、家庭发布时间线。
- [ ] `StudentReport` 保存当前/已发布版本指针、当前窗口授权指针、状态、乐观锁、提交/整改/修订期限，以及创建时的作者角色、班级、项目、营期快照；`StudentReportDraft` 保存当前可变正文、扩展 JSON、draft kind/revision；`StudentReportVersion` 保存完整提交正文、作者与前版本；`StudentReportReview` 追加保存决定、原因、审核人和时间；`StudentReportWindowGrant` 追加保存整改/修订重开理由、授权人及三日窗口。数据库触发器禁止版本、审核和窗口授权更新删除。
- [ ] 迁移 downgrade 在任一业务行、定制权限/授权存在时先拒绝；空库 upgrade→downgrade→upgrade 保持 schema 一致。
- [ ] PostgreSQL RED/GREEN：

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_teaching_report_models.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py \
  apps/api/tests/services/test_evaluation_staff.py apps/api/tests/api/test_audit.py -q
```

### Task 3: Author selectors, slots, drafts and privacy-bounded queries

**Files:**

- Create: `apps/api/app/evaluations/report_schemas.py`
- Create: `apps/api/app/evaluations/report_access.py`
- Create: `apps/api/app/evaluations/report_query_service.py`
- Create: `apps/api/app/evaluations/report_service.py`
- Create: `apps/api/tests/services/test_teaching_report_access.py`
- Create: `apps/api/tests/services/test_teaching_report_queries.py`
- Create: `apps/api/tests/services/test_teaching_reports.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/evaluations/dependencies.py`

- [ ] 先测作者候选：当前/历史有效主教练默认、助教/生活老师/其他角色仅在实际服务范围且显式获写权限；错误报名/班级/项目、停用账号、已删除人员、过期服务、deny override 均不可枚举。
- [ ] 先测 slot：只返回营期内截至当前当地日的日报和全部已开始周报；同作者已存在资源带回当前状态；未来日报不存在。
- [ ] 先测草稿：选择器先于事务和幂等；同一作者并发创建由唯一约束稳定合并，正文限制 1–5000 字，维度 JSON 仅接受对象；他人不能覆盖。
- [ ] 查询投影分别冻结 author/reviewer/family：家庭不接收作者账号、草稿、驳回理由、待审版本或内部权限来源；审核列表可见作者身份但不得泄露手机号/证件。
- [ ] RED 后实现共享 SQL 条件和 caller-owned transaction，锁顺序固定 `cohort -> enrollment -> report`，冲突后 session 仍可用。

### Task 4: Submission, independent review, correction and revision

**Files:**

- Modify: `apps/api/app/evaluations/report_service.py`
- Create: `apps/api/tests/db/test_teaching_report_concurrency.py`
- Modify: `apps/api/tests/services/test_teaching_reports.py`

- [ ] 失败测试覆盖首次提交窗口、周报周期中禁止提交、重复幂等、过期拒绝、作者自审、审核者范围、批准发布、驳回三日整改、超期拒绝、有理由重开、正常窗口内作者修订、窗口结束后只有管理员可记录原因发起修订或给原作者开放三日、修订待审期间旧版仍公开、修订批准切换指针、版本号连续。
- [ ] PostgreSQL 并发测试覆盖双提交、审核/撤权竞态、审核/修订竞态、两个审核人竞态、锁顺序与冲突后 session 可继续查询。
- [ ] 每个状态变化同事务写追加审计；审计元数据仅 UUID、kind、period、version、decision、状态和计数，不含正文、驳回理由、重开理由或建议。
- [ ] 实现最小状态机，禁止服务层外直接更新状态；全部时钟由显式 `occurred_at` 注入测试。

### Task 5: Expose APIs and live context capabilities

**Files:**

- Create: `apps/api/app/evaluations/report_router.py`
- Create: `apps/api/tests/api/test_teaching_reports.py`
- Modify: `apps/api/app/evaluations/router.py`
- Modify: `apps/api/app/evaluations/schemas.py`
- Modify: `apps/api/app/evaluations/context_service.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/api/test_evaluation_foundation.py`

- [ ] API RED 覆盖全部冻结路由、严格请求、分页上限、幂等、私有 404 等价、账号级 403、CSRF、撤权后下一请求、家庭关系终止后下一请求与失败零写入。
- [ ] `reports_available` 改为真实布尔能力；评价中心存在可写 slot、可审核队列或家庭已发布记录时为 true，不以缓存权限替代服务端重新授权。
- [ ] 统一复用 `mutation_api_support` 的安全请求解析和服务端审计 ID；新增错误映射不得返回内部状态或对象存在性。
- [ ] 运行 API 文件及 OpenAPI 快照/路由唯一性测试 GREEN。

### Task 6: Build responsive author, review and family experiences

**Files:**

- Create: `apps/web/src/evaluations/reportApi.ts`
- Create: `apps/web/src/evaluations/reportKeys.ts`
- Create: `apps/web/src/evaluations/TeachingReportsPage.tsx`
- Create: `apps/web/src/evaluations/TeachingReportsPage.test.tsx`
- Create: `apps/web/src/evaluations/TeachingReportReviewPage.tsx`
- Create: `apps/web/src/evaluations/TeachingReportReviewPage.test.tsx`
- Create: `apps/web/src/evaluations/PublishedReportsPanel.tsx`
- Create: `apps/web/src/evaluations/PublishedReportsPanel.test.tsx`
- Create: `apps/web/src/evaluations/teaching-reports.css`
- Modify: `apps/web/src/evaluations/api.ts`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.tsx`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.test.tsx`
- Modify: `apps/web/src/portal/LearningPage.tsx`
- Modify: `apps/web/src/portal/LearningPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] API 数据使用 Zod 白名单解析；正文、驳回/重开原因不进入 query key、local/session storage 或可检查的长期 mutation variables，写入使用一次性 opaque payload，mutation `gcTime: 0`。资源响应的 `allowed_actions` 是按钮可用性的唯一依据。
- [ ] 组件 RED：作者按营期/日报/周报筛选，保存草稿、提交、整改、修订和版本时间线；审核人队列、批准/驳回且不能自审；家庭多学员仅已发布卡片。
- [ ] 路由为 `/evaluations/reports`、`/evaluations/reports/review`；受保护路由失权后取消请求、清空当前账户 report root 并返回工作台。
- [ ] 360px 使用单列卡片和底部安全区，所有 dialog 复用 `EvaluationDialogShell`，键盘焦点、Escape、滚动锁、44px 触控目标和错误恢复均测试。
- [ ] 运行 Vitest、ESLint、严格 TypeScript 与 production build。

### Task 7: Teaching-report E2E and review checkpoints

**Files:**

- Create: `scripts/seed-teaching-reports-e2e.py`
- Create: `apps/api/tests/cli/test_seed_teaching_reports_e2e.py`
- Create: `apps/web/tests/teaching-reports.spec.ts`
- Modify: `apps/web/playwright.config.ts`

- [ ] Seed 仅允许测试数据库，创建 21 日、10 日和跨 UTC 日界营期，主教练、授权助教、未授权助教、审核人、学员与两个监护人；输出仅 UUID 和测试凭据引用。
- [ ] 四视口旅程覆盖日报、三份周报、补录、审核拒绝/整改/批准、发布修订、家庭双账号与立即撤权。
- [ ] 每个实施任务先交规格审查，再交代码质量审查；修复后复审。
- [ ] 最终聚焦门禁：

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/unit/evaluations/test_report_periods.py \
  apps/api/tests/unit/evaluations/test_report_state.py \
  apps/api/tests/db/test_teaching_report_models.py \
  apps/api/tests/db/test_teaching_report_concurrency.py \
  apps/api/tests/services/test_teaching_report_access.py \
  apps/api/tests/services/test_teaching_report_queries.py \
  apps/api/tests/services/test_teaching_reports.py \
  apps/api/tests/api/test_teaching_reports.py \
  apps/api/tests/cli/test_seed_teaching_reports_e2e.py -q
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
npx --prefix apps/web playwright test apps/web/tests/teaching-reports.spec.ts
```

预期：全部通过，四视口无失败；本单元完成后仍不声明阶段 2 总验收或 Docker 就绪。
