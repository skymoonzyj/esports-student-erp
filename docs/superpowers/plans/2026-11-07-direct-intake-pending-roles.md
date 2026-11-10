# Direct Intake Pending Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 行政直接完成普通人员建档，超权限角色独立待超管批准，审批可跨账号激活执行。

**Architecture:** 新建签名的角色意向及独立审批记录，保留现有 ActivationRolePlan。真实人员队伍岗位通过明确签名关联暂不兑现账号权限，审批后原子追加计划、岗位证明及需要的账号角色。

**Tech Stack:** SQLAlchemy/PostgreSQL/Alembic、FastAPI、React/TypeScript/Zod、pytest/Vitest。

**Spec:** docs/superpowers/specs/2026-11-07-direct-intake-pending-roles-design.md

## Global Constraints

- 不硬编码指定行政人员账号或更改财务/分成拒绝；显式拒绝优先。
- 不开放管理员/超管身份，不放宽旧已批准计划失效语义，不绕过激活资料要求。
- 既有外部管理工作树包含未提交改动，不提交/重置/清理；实际文件快照为审查基线。
- 仅一个数据库测试进程；不得使用线上业务数据测试。
- 检查通过后必须 scripts/deploy-nas.sh --source "$PWD"，NAS_SSH_PORT=202。

## Review Focus

- 普通角色被拒绝授权时：档案保留、角色待批，不能连带放行。
- 待批账号先激活：只有基础角色，没有队伍读取权限。
- 批准与激活/岗位结束竞争：共享人员锁，重新发现并拒绝过期快照。
- 已批准操作重放：不恢复后来被撤销角色，不复活已结束岗位。
- 历史合法计划失效：仍拒绝激活，不能回落到待批路径。

### Task 1: 行政直接建档及待授权意向

**Files:** personnel_intake/{access,approval_service,editor_context,identity_service,role_grants,schemas,router,role_intents,role_intent_models}.py; team_management/{person_staff_service,person_staff_query}.py; rbac/seed.py; migrations/versions/0088_intake_role_intents.py; tests/api/test_intake_role_intents.py.

**Interfaces:** ActivationRoleOption.requestable: bool; RoleIntentService.record_in_transaction records fixed keys and signed pending position evidence; GET /personnel-intake/people/{person_id}/role-intent returns nullable intent; GET /personnel-intake/role-intents returns bounded pending queue for actual superadmin.

- [x] Write real API failures: administration creates coach/life_teacher without granting roles; finance/admin/super rejection; denied intake blocked.
- [x] Run tests RED, then add additive migration/models, narrowly scoped authority and pending intent recording.
- [x] Test immediate legacy authorized path, selections/team affiliation, rollback and replay; run affected tests GREEN.

### Task 2: 基础激活及超管批准

**Files:** personnel_intake/{role_intents,role_grants,staff_activation}.py; team_management/person_staff_query.py; tests/services/test_intake_role_intent_activation.py; tests/db/test_intake_role_intent_locks.py.

**Interfaces:** POST /personnel-intake/people/{person_id}/role-intent/approve with expected_version:int, idempotency_key:UUID. RoleIntentView fields: id,person_id,version,role_keys,role_names,status(pending|approved),account_activated,can_approve. List rows additionally full_name; paginated page/page_size/total/items. No role_keys accepted on approve.

- [x] Test base-only activation plus absence of team access before approval; existing invalid approved plan remains blocked.
- [x] Add signed pending-position recognition and atomic approval, before and after activation; retain append-only approval evidence.
- [x] Test superadmin/explicit deny, stale versions, lost-response replay, concurrent activation/approval and late failure rollback.

### Task 3: 建档选择与可发现的审批界面

**Files:** apps/web/src/assistedPeople/{api,types,IntakeIdentitySection,AssistedDirectCreatePage,ActivationRolesPanel,AssistedRequestReviewPage} plus new PendingRoleIntentPanel; corresponding tests.

- [x] Test ordinary requestable identities selectable while forbidden choices stay disabled.
- [x] Add pending explanation, independent read-only pending summary and explicit superadmin approval button after activation too.
- [x] Add superadmin-only pending-role section to existing review page, paginated rows linking to profile; validate failure/retry/stale response behavior.
- [x] Run frontend tests/lint/build.

### Task 4: 完整验证与 NAS 发布

- [x] Update schema manifest/head tests; test additive upgrade, downgrade refusal with retained data if applicable.
- [x] Run affected real DB regressions, full default backend (report skips), full frontend, Ruff/mypy/lint/build.
- [x] Independent whole-change security/concurrency review against actual-file baseline; fix actionable findings and repeat affected checks.
- [x] Standard NAS deploy and verify active release, head, health and assets. Record actual results and limitations.

Plan self-review: all approved scope maps to tasks; finance and legacy role-plan authority remain unchanged. User has explicitly approved the described design and implementation; proceed inline without repeating a confirmation gate.
