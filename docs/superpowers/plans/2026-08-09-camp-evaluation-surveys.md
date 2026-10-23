# Camp Evaluation Surveys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development task-by-task, superpowers:test-driven-development before production code, and superpowers:verification-before-completion before each handoff.

**Goal:** 交付结营问卷：学员与每名有效监护人各自评价一次、窗口内最多修改一次；动态冻结实际服务人员，向本人提供匿名反馈，向获权管理员提供默认匿名及受审计实名打印投影。

**Architecture:** 营期结束后由 ERP 基于报名、教练分配和届次工作人员历史幂等创建不可变问卷周期与工作人员角色快照。每位回答者拥有独立 submission，完整版本和逐工作人员评分均追加保存。读取端按回答者、工作人员、管理员、实名打印四种投影重新授权；打印使用专用可打印 Web 页面，服务端决定是否包含身份并记录审计。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic 2、Pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright、CSS print media

---

## Frozen scope, model and routes

- 依赖 `2026-08-09-teaching-reports.md` 完成，迁移头从 `0015_food_preferences` 开始。
- 学员本人一份；在提交时仍有效且拥有独立有效账号的每个监护人各自一份。关系未解除则学员成年后仍可提交和查看。
- 开放时刻为报名实际 `completed_at`（自动结营时与计划结营一致，人工结营时使用实际确认时刻）；截止为该时刻在上海时区加一个自然月，日期不存在时取目标月最后一天，并保留当地时分秒。
- 第一次提交后、窗口内只允许一次修改；数据库永久保存 version 1 与 version 2。
- 工作人员快照取与报名实际班级历史区间 `[enrollment.confirmed_at, enrollment.completed_at)` 重叠的主教练、助教、生活老师及配置角色；同一工作人员只评价一次，快照内保留其全部角色类别和服务时间段。转班前后的实际服务人员都必须纳入。
- 首份问卷提交前，管理员修正服务历史可幂等重建当前快照；已有提交后不得改写快照，必须填写审计原因创建新快照版本。新增人员形成每个既有 submission 的补充评价任务且不消耗问卷的一次修改；错误纳入人员只从后续汇总排除，原快照和回答永久保留。
- 每个 snapshot 的 1–10 整数分必填；原因/建议选填。总体课程训练、住宿生活、服务管理 1–10 必填；各原因及总体建议选填。
- 提交立即发布，无审核。工作人员只匿名读取与本人+角色 snapshot 对应内容；超管默认可实名读取，其他人需有限期、有限范围 `camp_surveys.identity.read`。
- `camp_surveys.print` 控制打印；默认匿名打印。`identity=real_name` 同时要求两项敏感权限并写安全审计。

```text
GET    /api/v1/evaluations/surveys/context
GET    /api/v1/evaluations/surveys?student_id=&enrollment_id=&page=&page_size=
GET    /api/v1/evaluations/surveys/{cycle_id}
POST   /api/v1/evaluations/surveys/{cycle_id}/submissions
POST   /api/v1/evaluations/surveys/{cycle_id}/submissions/{submission_id}/revisions
GET    /api/v1/evaluations/surveys/my-feedback?cohort_id=&role_id=&page=&page_size=
GET    /api/v1/evaluations/admin/surveys?cohort_id=&project_id=&staff_id=&page=&page_size=
GET    /api/v1/evaluations/admin/surveys/print-projection?cohort_id=&staff_id=&role_key=&identity=hidden|real_name
```

所有 POST 使用唯一幂等键。缺失、未开放、已关闭、关系失效、服务快照无关、错误 scope、deny override 均遵守私有 404；账号无任何营期评价能力才返回 403。任何拒绝请求零业务、幂等和审计写入，实名打印的拒绝也不得留下成功审计。

## Tasks

### Task 1: Freeze calendar-month windows and strict score contracts

**Files:**

- Create: `apps/api/app/evaluations/survey_window.py`
- Create: `apps/api/app/evaluations/survey_schemas.py`
- Create: `apps/api/tests/unit/evaluations/test_survey_window.py`
- Create: `apps/api/tests/unit/evaluations/test_survey_schemas.py`

- [ ] RED 覆盖 1 月 31 日、闰年 2 月 29 日、夏令时无关的上海时区、naive datetime、开放前、截止瞬间和截止后。
- [ ] 严格 schema RED 覆盖整数 1/10、有小数/布尔/0/11 拒绝、每个工作人员恰有一个评分、重复/未知 snapshot 拒绝、必填总体三项、选填文本 trim 后空转 null、正文长度上限。
- [ ] 最小实现 `survey_window_for_end` 和 Pydantic input；运行聚焦 pytest、Ruff、mypy GREEN。

### Task 2: Add migration 0016, immutable snapshots and permission seeds

**Files:**

- Create: `apps/api/app/evaluations/survey_models.py`
- Create: `apps/api/migrations/versions/0016_camp_evaluation_surveys.py`
- Create: `apps/api/tests/db/test_camp_survey_models.py`
- Modify: `apps/api/app/evaluations/models.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] RED 冻结 `camp_survey_cycles`、`camp_survey_snapshot_versions`、`camp_survey_staff_snapshots`、`camp_survey_submissions`、`camp_survey_versions`、`camp_survey_staff_ratings`、`camp_survey_supplements`、`camp_survey_supplement_ratings` 八表，使用评价中心预留权限 ID 034 `camp_surveys.identity.read`、035 `camp_surveys.print`；仅 admin 默认全局授权，两项均 sensitive。
- [ ] 周期按 enrollment 唯一并保存开放/截止及当前快照版本；snapshot version 按 `(cycle_id, version)` 唯一并保存变更原因；staff snapshot 按 `(snapshot_version_id, staff_person_id)` 唯一并保存名称、角色/类别/服务区间 JSON 及是否纳入汇总；submission 按 `(cycle_id, respondent_person_id)` 唯一并保存 self/guardian 与关系快照；version 按 `(submission_id, version)` 唯一且仅 1/2；rating 按 `(version_id, staff_snapshot_id)` 唯一；supplement 按 `(submission_id, snapshot_version_id)` 唯一并以追加评分补齐新人员。
- [ ] 全部历史表 `RESTRICT`，版本/snapshot/rating 禁止更新删除。降级在任意业务行或定制权限存在时先拒绝，空库往返一致。
- [ ] PostgreSQL RED/GREEN：

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/db/test_camp_survey_models.py apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py apps/api/tests/api/test_audit.py -q
```

### Task 3: Build deterministic service-history snapshots

**Files:**

- Create: `apps/api/app/evaluations/survey_snapshot_service.py`
- Create: `apps/api/tests/services/test_camp_survey_snapshots.py`
- Create: `apps/api/tests/db/test_camp_survey_snapshot_concurrency.py`

- [ ] RED 构造 class 主教练、项目助教、生活老师、自定义角色、提前离任、开营后加入、无重叠、重复服务段、停用角色和工作人员删除/账号停用场景。
- [ ] RED 构造报名转班事件，证明快照从 `confirmed/transferred/completed` 历史重建实际班级区间，而不是只读取报名最终 `class_id`；同一工作人员多角色/多段服务只生成一个评分对象并完整保存服务历史。
- [ ] 快照必须按历史重叠而非当前 assignment 判断；同人员同角色合并边界，同人员不同角色保留；排序稳定为角色类别、角色名、人员名、UUID。
- [ ] 使用 `cohort -> enrollment -> coach assignments -> cohort staff assignments -> cycle` 固定锁序；并发首次打开只产生一个 cycle 和同一组快照，冲突后 session 可用。
- [ ] 首份提交前服务历史修正可在同一 cycle 重建未使用快照；首份提交后旧快照不可变，管理员必须以审计原因创建下一快照版本。新人员只生成补充项，错误人员在新版本标记不参与汇总，任何旧行不得更新或删除。

### Task 4: Implement respondent authorization, submit and one revision

**Files:**

- Create: `apps/api/app/evaluations/survey_access.py`
- Create: `apps/api/app/evaluations/survey_service.py`
- Create: `apps/api/tests/services/test_camp_survey_access.py`
- Create: `apps/api/tests/services/test_camp_surveys.py`
- Create: `apps/api/tests/db/test_camp_survey_concurrency.py`
- Modify: `apps/api/app/evaluations/errors.py`
- Modify: `apps/api/app/evaluations/dependencies.py`

- [ ] RED 验证学员 self、每名有效监护人独立提交；一个监护人绑定多个学员各自可提交；其他家人/工作人员不能代填；关系在提交前解除立即撤权；关系保持则成年不撤权。
- [ ] 首次提交要求窗口内、当前快照所有项目完整；提交立即生成 version 1 和 published_at。一次修订生成完整 version 2；第三次、截止后或并发双修订返回稳定冲突。快照纠错后新增工作人员由独立 supplement 补填，不增加主问卷版本、不占用修改次数；同一补充项只提交一次。
- [ ] 输入中的 respondent、student、工作人员名称一律由服务器资源覆盖；幂等 payload digest 不保存正文；审计不记录分数、原因、建议或评价者身份。
- [ ] PostgreSQL 并发覆盖双提交、双修订、关系撤销竞态、快照创建竞态和锁顺序。

### Task 5: Add anonymous staff/admin/print query projections and API

**Files:**

- Create: `apps/api/app/evaluations/survey_query_service.py`
- Create: `apps/api/app/evaluations/survey_router.py`
- Create: `apps/api/tests/services/test_camp_survey_queries.py`
- Create: `apps/api/tests/api/test_camp_surveys.py`
- Modify: `apps/api/app/teaching/cohort_service.py`
- Modify: `apps/api/tests/services/test_cohorts.py`
- Modify: `apps/api/tests/services/test_teaching_scheduler.py`
- Modify: `apps/api/app/evaluations/router.py`
- Modify: `apps/api/app/evaluations/schemas.py`
- Modify: `apps/api/app/evaluations/context_service.py`
- Modify: `apps/api/app/main.py`
- Modify: `apps/api/tests/api/test_evaluation_foundation.py`

- [ ] 工作人员投影 RED：只含本人对应 snapshot 的分数/原因/建议、项目/届次/角色与匿名提交序号；教学角色可见课程训练评价及给教学团队建议，生活服务角色可见住宿生活评价，运营角色可见服务管理评价。不得含 respondent UUID、账号、人员名、关系 ID、电话、证件、精确提交时间、其他工作人员评分或角色无关总体字段。
- [ ] 管理投影 RED：普通管理读取默认匿名；实名必须 `identity.read` 对该 enrollment/cohort/project 有效且下一请求重新判权。错误 scope 和 deny 与不存在同为 404。
- [ ] 打印投影按工作人员、届次和角色聚合平均值、匿名明细、原因、本人建议、教学团队建议及服务时间。hidden 永远不带身份；real_name 同时需要 print+identity，成功审计仅记录 staff/cohort UUID、identity mode、版本和 item_count；正文不得进入审计。
- [ ] `surveys_available` 为真实布尔值；有可填周期、本人匿名反馈或管理查询能力时启用。
- [ ] 人工和自动结营共用的 cohort 转换事务必须完成报名 `completed_at` 和初始问卷快照；快照失败时营期、报名、事件和审计全部回滚，不能留下“已结营但无问卷”的半状态。
- [ ] API 严格分页、CSRF、幂等、私有缓存头和失败零写入全部 GREEN。

### Task 6: Build survey, feedback and print Web pages

**Files:**

- Create: `apps/web/src/evaluations/surveyApi.ts`
- Create: `apps/web/src/evaluations/surveyKeys.ts`
- Create: `apps/web/src/evaluations/CampSurveysPage.tsx`
- Create: `apps/web/src/evaluations/CampSurveysPage.test.tsx`
- Create: `apps/web/src/evaluations/MySurveyFeedbackPage.tsx`
- Create: `apps/web/src/evaluations/MySurveyFeedbackPage.test.tsx`
- Create: `apps/web/src/evaluations/CampSurveyAdminPage.tsx`
- Create: `apps/web/src/evaluations/CampSurveyAdminPage.test.tsx`
- Create: `apps/web/src/evaluations/CampSurveyPrintPage.tsx`
- Create: `apps/web/src/evaluations/CampSurveyPrintPage.test.tsx`
- Create: `apps/web/src/evaluations/camp-surveys.css`
- Modify: `apps/web/src/evaluations/api.ts`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.tsx`
- Modify: `apps/web/src/evaluations/EvaluationCenterPage.test.tsx`
- Modify: `apps/web/src/portal/LearningPage.tsx`
- Modify: `apps/web/src/portal/LearningPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`

- [ ] API 响应使用 Zod 白名单；评价原因/建议不进入 query key、local/session storage 或长期 mutation variables，写入使用一次性 opaque payload 且 mutation `gcTime: 0`；所有按钮只服从服务端 `allowed_actions`。
- [ ] `/evaluations/surveys` 支持多学员/多营期、动态工作人员 1–10 原生选择框、完整性提示、首次提交、唯一一次修改及不占修改次数的补充项；不得编辑另一账号提交。
- [ ] `/evaluations/surveys/feedback` 仅显示匿名本人反馈；`/evaluations/surveys/admin` 默认匿名；`/evaluations/surveys/print/:cycleId` 使用服务器投影与 `@media print`，实名切换仅在能力为 true 时出现。
- [ ] 失权立即取消 query、清除当前 account survey root、关闭 dialog 并离开受保护页面；旧账户数据不得在新登录闪现。
- [ ] 1440/1024/768/360、键盘、读屏 label、错误恢复、加载占位、一次修改说明和截止时间均有 Vitest/CSS 测试。

### Task 7: Complete survey E2E and unit handoff

**Files:**

- Create: `scripts/seed-camp-surveys-e2e.py`
- Create: `apps/api/tests/cli/test_seed_camp_surveys_e2e.py`
- Create: `apps/web/tests/camp-evaluation-surveys.spec.ts`

- [ ] 测试 seed 创建一个学员、两个监护人、四类工作人员、历史离任角色、实名获权与无权管理员；仅测试数据库可运行。
- [ ] 四视口 E2E 覆盖双方独立提交、一次修改、第三次拒绝、关系撤销、匿名工作人员反馈、默认匿名打印、实名授权/撤权和打印样式。
- [ ] 每个任务先规格审查再质量审查，所有 Important 以上问题修复并复审。
- [ ] 聚焦门禁：

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest -p no:cacheprovider \
  apps/api/tests/unit/evaluations/test_survey_window.py \
  apps/api/tests/unit/evaluations/test_survey_schemas.py \
  apps/api/tests/db/test_camp_survey_models.py \
  apps/api/tests/db/test_camp_survey_snapshot_concurrency.py \
  apps/api/tests/db/test_camp_survey_concurrency.py \
  apps/api/tests/services/test_camp_survey_snapshots.py \
  apps/api/tests/services/test_camp_survey_access.py \
  apps/api/tests/services/test_camp_surveys.py \
  apps/api/tests/services/test_camp_survey_queries.py \
  apps/api/tests/api/test_camp_surveys.py \
  apps/api/tests/cli/test_seed_camp_surveys_e2e.py -q
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
npx --prefix apps/web playwright test apps/web/tests/camp-evaluation-surveys.spec.ts
```

预期全部通过；本单元不启用 Docker/NAS，也不删除任何历史系统评价媒体。
