# Food Preferences, Survey Handoff and Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付独立的人员饮食偏好主记录、注册后可跳过的本人餐饮问卷、权限受控的本人/家庭/后台编辑、统计与 XLSX/CSV ZIP 导出，并把尚未实施的营期问卷计划顺延到迁移 `0016`、冻结后续同步接口。

**Architecture:** 新建 `app.food_preferences` 边界，PostgreSQL 用规范化当前关联行加不可变版本快照保存偏好，并在 `user_accounts` 保存一次性本人完成时间；服务层以 `Person` 行作为偏好并发根，自助 `/me` 路径提供受 deny 约束的固定 self 基线，代办路径继续复用 `self/family/person/global` RBAC。统计和导出共享同一投影构建器；React 以账号和授权 revision 隔离缓存，复用一个 `FoodPreferencesPanel` 到注册引导、人员档案、本人资料和家庭学习页。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、Pydantic 2、openpyxl、Pytest、React 19、TypeScript 6、TanStack Query、Zod、Vitest、Playwright

---

## Frozen scope and authority

- 设计依据：`docs/superpowers/specs/2026-08-11-food-preferences-survey-export-design.md`。
- 本计划先交付独立饮食偏好模块；营期问卷尚未实现，本轮只修改其计划和阶段 2 计划，不创建任何 `camp_survey_*` 生产表或 API。
- 迁移头固定为 `0015_food_preferences`；未来营期问卷固定顺延为 `0016_camp_evaluation_surveys`。
- 每人当前偏好允许 0–3 个菜系、0–3 个菜品；注册后首次本人确认和未来营期问卷均要求 1–3 个菜系和 1–3 个菜品。
- 权限 seed 以已确认矩阵为准：admin 四项 global；student 的 read/update 为 self；guardian 的 read/update 为 family；其他角色无读取他人或导出默认权限。所有活动普通个人账号可使用固定 `/me` self 基线，且账号级 self deny 仍优先。
- 注册引导适用于具有 student/staff/visitor 任一人员类型的普通账号；仅监护人身份与服务账号豁免。监护人多身份账号只要同时是学员、员工或访客仍参与。
- 引导允许跳过且不阻断 ERP；跳过零写入，首页持续提醒。本人成功确认时原子保存偏好并写 `food_preferences_onboarding_completed_at`；管理员或监护人代填不写该字段，完成后清空偏好也不重置。
- `food_preferences.read` 使用 UUID `20000000-0000-4000-8000-000000000047`；`update` 为 `20000000-0000-4000-8000-000000000048`；`catalog.manage` 为 `20000000-0000-4000-8000-000000000049`；`export` 为 `20000000-0000-4000-8000-000000000050`。
- Docker、NAS、真实付款适配、公安 API、录像和 GPU 继续延期；不得读取或修改 score-tool 历史截图/评分数据。

## File responsibility map

### Backend

- `app/food_preferences/normalization.py`：唯一文本规范化入口。
- `app/food_preferences/models.py`：字典、当前根、当前关联行和不可变版本 ORM。
- `app/food_preferences/schemas.py`：严格闭合的 API/服务 DTO。
- `app/food_preferences/access.py`：操作级能力、人员对象授权和授权 revision。
- `app/food_preferences/catalog_service.py`：菜系字典管理和菜品建议。
- `app/food_preferences/preference_service.py`：当前偏好、版本、并发和 caller-owned transaction 接口。
- `app/food_preferences/reporting.py`：人员范围选择、统计和可导出一致性投影。
- `app/food_preferences/renderer.py`：无状态 XLSX/CSV ZIP 渲染与公式安全。
- `app/food_preferences/export_service.py`：重复读快照、文件摘要和流式成功审计。
- `app/food_preferences/routing.py`：覆盖成功与错误响应的私有缓存头。
- `app/food_preferences/router.py`：全部饮食偏好 HTTP 路由和私有错误映射。
- `app/auth/models.py`：账号级餐饮问卷完成时间；不承载偏好正文。
- `app/portal/context_service.py`：计算纯监护人豁免、多身份参与和首页待办投影。

### Web

- `foodPreferences/api.ts`：Zod 白名单、JSON API 与附件下载。
- `foodPreferences/keys.ts`：账号 + 授权 revision 查询键。
- `foodPreferences/FoodPreferencesPanel.tsx`：四入口复用编辑器和 409 恢复。
- `foodPreferences/MyFoodProfilePage.tsx`：本人资料入口。
- `foodPreferences/FoodOnboardingPage.tsx`：注册后可跳过的本人餐饮问卷。
- `foodPreferences/FoodPreferenceStatisticsPage.tsx`：统计、范围和导出。
- `foodPreferences/FoodCuisineSettingsPage.tsx`：菜系新增、排序和停用。
- `foodPreferences/food-preferences.css`：360/768/1024/1440 响应式规则。

## Dependency map and safe parallelism

| Task | Boundary | Depends on | May run in parallel with |
| --- | --- | --- | --- |
| 1 | 规范化与严格 schema | 设计 | 无 |
| 2 | 模型、迁移、RBAC、审计 | Task 1 | 无；共享迁移/RBAC 文件必须独占 |
| 3 | 对象授权与菜系字典 | Task 2 | Task 7 renderer unit work |
| 4 | 当前偏好与版本并发 | Task 3 | Task 7 renderer unit work |
| 5 | 人员 API 与建议 | Task 4 | Task 7 renderer unit work |
| 6 | 统计投影 | Task 4 | Task 8 Web API scaffold |
| 7 | 渲染与导出审计 | Tasks 2, 6 | 无共享文件时可与 Task 8 开始部分并行 |
| 8 | Web API、缓存与复用面板 | Tasks 3–5 | Task 6/7 backend |
| 9 | 注册引导与三种资料入口 | Task 8 | Task 10 管理页面（路由文件最后串行合并） |
| 10 | 后台统计与字典页面 | Tasks 7–8 | Task 9（避开 router/AppShell 同时编辑） |
| 11 | 四视口 E2E | Tasks 1–10 | 无 |
| 12 | 问卷/总验收计划交接 | Task 11 | 无 |

并发上限仍可设为 8，但本单元实际安全峰值为 3–4 条工作流；迁移、`rbac/seed.py`、`audit/service.py`、`app/router.tsx` 和 `AppShell.tsx` 必须单写者串行合并。

### Task 1: Freeze normalization and strict request contracts

**Files:**

- Create: `apps/api/app/food_preferences/__init__.py`
- Create: `apps/api/app/food_preferences/normalization.py`
- Create: `apps/api/app/food_preferences/schemas.py`
- Create: `apps/api/tests/unit/food_preferences/test_normalization.py`
- Create: `apps/api/tests/unit/food_preferences/test_schemas.py`

- [ ] **Step 1: Add the normalization test file**

```python
@pytest.mark.parametrize(
    ("raw", "display", "key"),
    [
        ("  宫保  鸡丁  ", "宫保 鸡丁", "宫保 鸡丁"),
        ("ＳｉＣｈｕａｎ", "SiChuan", "sichuan"),
        ("粤\u3000菜", "粤 菜", "粤 菜"),
    ],
)
def test_normalize_food_text(raw: str, display: str, key: str) -> None:
    assert normalize_food_display(raw) == display
    assert normalize_food_key(raw) == key


@pytest.mark.parametrize("raw", ["", "   ", "x" * 41])
def test_normalize_food_text_rejects_invalid_length(raw: str) -> None:
    with pytest.raises(FoodTextValidationError):
        normalize_food_display(raw)
```

- [ ] **Step 2: Run only normalization tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m pytest -p no:cacheprovider \
  apps/api/tests/unit/food_preferences/test_normalization.py -q
```

Expected: collection fails because `app.food_preferences.normalization` does not exist.

- [ ] **Step 3: Implement only `normalization.py`**

```python
_WHITESPACE = re.compile(r"\s+")

def normalize_food_display(value: str, *, max_length: int = 40) -> str:
    display = _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())
    if not 1 <= len(display) <= max_length:
        raise FoodTextValidationError("food text must contain 1 to 40 characters")
    return display

def normalize_food_key(value: str, *, max_length: int = 40) -> str:
    return normalize_food_display(value, max_length=max_length).casefold()
```

- [ ] **Step 4: Re-run normalization tests and verify GREEN**

Run the Step 2 command. Expected: all normalization tests PASS.

- [ ] **Step 5: Add failing strict-schema tests**

Freeze `extra="forbid"`, strict integer `expected_version >= 0`, ordered maximum-three arrays, duplicate UUID/name rejection, blank-to-null custom name, `purpose` length 4–200, `population` enum and `format` enum `xlsx|csv_zip`. The key assertion is:

```python
payload = FoodPreferenceWriteRequest.model_validate(
    {
        "expected_version": 0,
        "cuisines": [{"cuisine_option_id": str(cuisine_id), "custom_name": None}],
        "dishes": [" 宫保  鸡丁 "],
    }
)
assert payload.dishes == ("宫保 鸡丁",)
with pytest.raises(ValidationError):
    FoodPreferenceWriteRequest.model_validate({**payload.model_dump(), "unexpected": True})
```

- [ ] **Step 6: Run only schema tests and verify RED**

Run:

```bash
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m pytest -p no:cacheprovider \
  apps/api/tests/unit/food_preferences/test_schemas.py -q
```

Expected: collection fails because `app.food_preferences.schemas` does not exist.

- [ ] **Step 7: Implement the strict schema surface**

`schemas.py` defines closed `CuisineSelectionInput`, `FoodPreferenceWriteRequest`, `CuisineCreateRequest`, `CuisinePatchRequest`, `FoodPreferenceExportRequest` and response DTOs. Database-dependent rules—active option and “其他” custom-name pairing—remain service rules, not fake Pydantic lookups.

- [ ] **Step 8: Run unit and static quality gates**

```bash
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m pytest -p no:cacheprovider apps/api/tests/unit/food_preferences -q
'/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m ruff check \
  apps/api/app/food_preferences/normalization.py \
  apps/api/app/food_preferences/schemas.py apps/api/tests/unit/food_preferences
```

Expected: tests PASS and Ruff prints no errors.

- [ ] **Step 9: Commit the contract unit**

```bash
git add apps/api/app/food_preferences/__init__.py \
  apps/api/app/food_preferences/normalization.py \
  apps/api/app/food_preferences/schemas.py \
  apps/api/tests/unit/food_preferences/test_normalization.py \
  apps/api/tests/unit/food_preferences/test_schemas.py
git commit -m "test: freeze food preference contracts"
```

### Task 2: Add migration 0015, immutable history, permissions and audit allowlists

**Files:**

- Create: `apps/api/app/food_preferences/models.py`
- Create: `apps/api/migrations/versions/0015_food_preferences.py`
- Create: `apps/api/tests/db/test_food_preference_models.py`
- Modify: `apps/api/app/auth/models.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `apps/api/tests/api/test_permissions.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Add table and permission expectations to migration tests**

Add expected tables:

```python
FOOD_PREFERENCE_TABLES = {
    "food_cuisine_options",
    "person_food_preferences",
    "person_food_preference_cuisines",
    "person_food_preference_dishes",
    "person_food_preference_versions",
}
FOOD_PERMISSION_IDS = {
    "food_preferences.read": UUID("20000000-0000-4000-8000-000000000047"),
    "food_preferences.update": UUID("20000000-0000-4000-8000-000000000048"),
    "food_preferences.catalog.manage": UUID("20000000-0000-4000-8000-000000000049"),
    "food_preferences.export": UUID("20000000-0000-4000-8000-000000000050"),
}
```

Add only table/head/permission/grant expectations to `test_migrations.py` and `test_rbac_seed.py` first.
Also assert `user_accounts.food_preferences_onboarding_completed_at` exists as nullable `TIMESTAMPTZ` and is absent again after a clean downgrade to `0014_teaching_reports`.

- [ ] **Step 2: Run migration/seed tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_rbac_seed.py -q
```

Expected: failures name missing revision, tables and permissions.

- [ ] **Step 3: Add failing database constraint tests**

Create `test_food_preference_models.py` for position 1–3, per-person option/dish uniqueness, normalized cuisine uniqueness, exactly one protected “其他”, `Person` deletion restriction, source enum, version >= 1, JSON arrays length <= 3, update/delete rejection for version rows, and round-trip of the nullable aware account completion time. Add a downgrade refusal case with a non-null completion time so removing `0015` never silently discards completed onboarding state.

- [ ] **Step 4: Run the new model test and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/db/test_food_preference_models.py -q
```

Expected: collection fails because `app.food_preferences.models` is missing.

- [ ] **Step 5: Create the five ORM models**

Add the exact five ORM tables and named constraints from the schema below; import them from Alembic `env.py` so metadata inspection sees them. Add this single account field without placing preference text on the account:

```python
food_preferences_onboarding_completed_at: Mapped[datetime | None] = mapped_column(
    DateTime(timezone=True)
)
```

- [ ] **Step 6: Create migration tables and indexes**

The migration seeds `FOOD_OTHER_CUISINE_ID = 60000000-0000-4000-8000-000000000001` with name/normalized name `其他`, `is_active=true`, `is_other=true`. It creates:

```text
food_cuisine_options(id, name, normalized_name, sort_order, is_active, is_other,
                     created_by_user_id, updated_by_user_id, created_at, updated_at)
person_food_preferences(person_id, version, source, updated_by_user_id, updated_at)
person_food_preference_cuisines(person_id, position, cuisine_option_id,
                                cuisine_name_snapshot, custom_name, custom_normalized_name)
person_food_preference_dishes(person_id, position, display_name, normalized_name)
person_food_preference_versions(id, person_id, version, cuisines, dishes, source,
                                updated_by_user_id, created_at)
```

Use named checks and indexes only. Seed the stable Other row after table creation.
Migration `0015` also adds nullable `user_accounts.food_preferences_onboarding_completed_at TIMESTAMPTZ`; it has no default, so existing accounts remain incomplete until the eligibility projection decides whether they participate.

- [ ] **Step 7: Add the database protection triggers**

One trigger rejects disabling/deleting the `is_other` row and validates custom-name pairing against the referenced option. A second trigger rejects UPDATE/DELETE on version rows.

- [ ] **Step 8: Add guarded downgrade and schema manifest entries**

Downgrade first refuses when non-system cuisine/current/history rows, any non-null account onboarding completion, or non-seed role/override references exist; an empty/default-only database safely removes grants, permissions, Other, triggers, tables and the account column. Set schema manifest head/table lists to `0015_food_preferences`.

- [ ] **Step 9: Re-run model and migration tests**

Run the Step 2 and Step 4 commands. Expected: model/migration structure passes while seed/audit assertions still fail.

- [ ] **Step 10: Add exact role grants to the seed**

The effective seed matrix must be exactly:

```python
("admin", "food_preferences.read", "global"),
("admin", "food_preferences.update", "global"),
("admin", "food_preferences.catalog.manage", "global"),
("admin", "food_preferences.export", "global"),
("student", "food_preferences.read", "self"),
("student", "food_preferences.update", "self"),
("guardian", "food_preferences.read", "family"),
("guardian", "food_preferences.update", "family"),
```

In `rbac/seed.py`, the existing admin comprehension supplies the first four rows automatically; add only the four student/guardian tuples. Migration `0015` inserts all eight effective grants explicitly because it does not execute the application seed helper.

- [ ] **Step 11: Add failing override-scope API tests**

In `test_permissions.py`, prove read/update reject `assigned_class|cohort|project|building|floor`, and catalog/export reject every non-global scope with 422 and zero override/audit writes.

- [ ] **Step 12: Implement food-permission scope validation**

在 `RbacService` 冻结 override scope：`food_preferences.read/update` 只接受 `self|family|person|global`，`catalog.manage/export` 只接受 `global`；错误 scope 在写 override 前返回 422 且零权限/审计写入。

- [ ] **Step 13: Add failing audit allowlist tests**

Assert safe count/range/hash/source metadata is accepted and any cuisine/dish/name text key or malformed SHA is rejected.

- [ ] **Step 14: Implement minimal audit allowlists**

Add `person_id`, `old_version`, `new_version`, `cuisine_count`, `dish_count`, `population`, `include_inactive`, `person_count`, `filled_count`, `file_sha256` and `source`. Text values for cuisine/dish names remain forbidden；全局 `format` 白名单保留房态已有的 `csv` 并接受 `xlsx|csv|csv_zip`，饮食偏好事件自身只允许 `xlsx|csv_zip`。

- [ ] **Step 15: Run the complete migration/RBAC/audit gate**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/db/test_food_preference_models.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py -q
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m ruff check apps/api/app/food_preferences/models.py \
  apps/api/migrations/versions/0015_food_preferences.py
git diff --check
```

Expected: all listed PostgreSQL/API tests PASS and Ruff/diff checks are clean.

- [ ] **Step 16: Commit persistence and security seeds**

```bash
git add apps/api/app/food_preferences/models.py \
  apps/api/app/auth/models.py apps/api/migrations/versions/0015_food_preferences.py \
  apps/api/migrations/env.py \
  apps/api/app/rbac/seed.py apps/api/app/rbac/service.py \
  apps/api/app/audit/service.py apps/api/tests/db/test_food_preference_models.py \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py \
  apps/api/tests/api/test_permissions.py apps/api/tests/api/test_audit.py \
  scripts/db-schema-manifest.sh
git commit -m "feat: add food preference persistence"
```

### Task 3: Build deny-first access revision and cuisine catalog services

**Files:**

- Create: `apps/api/app/food_preferences/errors.py`
- Create: `apps/api/app/food_preferences/access.py`
- Create: `apps/api/app/food_preferences/catalog_service.py`
- Create: `apps/api/tests/services/test_food_preference_access.py`
- Create: `apps/api/tests/services/test_food_cuisine_catalog.py`

- [ ] **Step 1: Add access happy-path tests**

Use admin, student, guardian with two students, ended guardian, coach, disabled account and account overrides. Freeze:

```python
assert access.can_target("food_preferences.read", own_student_id)
assert not access.can_target("food_preferences.read", unrelated_student_id)
with pytest.raises(FoodPreferenceNotFound):
    access.require_target("food_preferences.read", missing_id)
coach_access.require_any_capability("food_preferences.read")
assert coach_access.require_self("food_preferences.read").id == coach_person_id
with pytest.raises(FoodPreferenceNotFound):
    coach_access.require_target("food_preferences.read", unrelated_student_id)
```

`require_self()` is a fixed standard-account capability, not authority over a supplied UUID. Add guardian-only and roleless visitor-account cases proving self succeeds, an unrelated target still fails, and an explicit `deny/self` makes the self call return operation-level 403 with zero writes.

- [ ] **Step 2: Add private-equivalence and zero-write tests**

Missing/deleted target, ended/expired relationship, wrong person scope and deny override must have the same private-not-found exception after generic delegated capability succeeds. All rejected cases assert zero preference/version/account-completion/audit writes. Disabled or non-standard/service credentials never receive the self baseline.

- [ ] **Step 3: Run access tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preference_access.py -q
```

Expected: imports fail for `errors.py` and `access.py`.

- [ ] **Step 4: Implement operation-level capability checks**

Create the closed permission/not-found exception types. Implement `require_any_capability(permission)` from active role/override rules with deny precedence independently of a concrete person target. Implement the self baseline by adding one synthetic allow-self rule only for the authenticated ordinary account and then applying real account overrides through the same deny-first policy:

```python
baseline = PolicyRule(permission=permission, effect="allow", scope_kind="self")
decision = context.policy.decide(
    permission,
    {f"self:{account.person_id}", f"person:{account.person_id}"},
    [*context.role_rules, baseline],
    context.overrides,
)
```

The method returns only `account.person_id`; it never accepts a target ID and therefore cannot broaden coach/staff access to another person.

- [ ] **Step 5: Implement person target authorization**

Reuse `RbacService.person_resource_tokens()` for `require_target()` and `can_target()`. Target selection requires a non-deleted Person and maps every missing/wrong-scope/ended/deny state to the same private exception without writing.

- [ ] **Step 6: Re-run access tests and verify GREEN**

Run the Step 3 command. Expected: all access matrix tests PASS.

- [ ] **Step 7: Add authorization-revision tests**

Cover next-request override revocation, self deny, relationship start/end changing revision, stable result for irrelevant rows, and no raw preference text or onboarding timestamp in the revision input.

- [ ] **Step 8: Implement the authorization revision**

The SHA-256 revision canonicalizes only active relevant RBAC rules, actor UUID and active family relationship IDs/ranges. It never includes names, cuisine/dish values, phone, identity, audit purpose or session material.

- [ ] **Step 9: Run access tests after revision implementation**

Run the Step 3 command. Expected: access and revision tests PASS.

- [ ] **Step 10: Add catalog list/mutation tests**

Cover active-only default, inactive list only with catalog global, NFKC duplicate, protected Other, stable ordering, rename snapshot preservation, create, reorder, disable and reactivate.

- [ ] **Step 11: Run catalog tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_cuisine_catalog.py -q
```

Expected: import fails for `catalog_service.py`.

- [ ] **Step 12: Implement list/create/patch catalog commands**

`CuisineCatalogService.list(include_inactive)`, `create`, and `patch` run in `write_transaction`, normalize once, lock the target option, reject disabling Other, and audit only option UUID/status/order.

- [ ] **Step 13: Add dish-suggestion tests**

Cover blank query, limit 1–20, NFKC/case-equivalent names, current-person count ordering, stable tie order and absence of person/count/source fields.

- [ ] **Step 14: Implement dish suggestions**

`dish_suggestions(query, limit)` returns distinct current display names grouped by normalized key, ordered by current-person count DESC, normalized name, and display name; blank query returns `()`.

- [ ] **Step 15: Run service and static gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preference_access.py \
  apps/api/tests/services/test_food_cuisine_catalog.py -q
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m ruff check apps/api/app/food_preferences/errors.py \
  apps/api/app/food_preferences/access.py \
  apps/api/app/food_preferences/catalog_service.py \
  apps/api/tests/services/test_food_preference_access.py \
  apps/api/tests/services/test_food_cuisine_catalog.py
```

Expected: tests PASS and Ruff prints no errors.

- [ ] **Step 16: Commit access and catalog services**

```bash
git add apps/api/app/food_preferences/errors.py \
  apps/api/app/food_preferences/access.py \
  apps/api/app/food_preferences/catalog_service.py \
  apps/api/tests/services/test_food_preference_access.py \
  apps/api/tests/services/test_food_cuisine_catalog.py
git commit -m "feat: add food preference access and catalog"
```

### Task 4: Implement atomic current preferences, immutable versions and concurrency

**Files:**

- Create: `apps/api/app/food_preferences/preference_service.py`
- Create: `apps/api/tests/services/test_food_preferences.py`
- Create: `apps/api/tests/db/test_food_preference_concurrency.py`

- [ ] **Step 1: Add first-save and projection tests**

Cover expected version 0 on no row, first non-empty save version 1, ordered current projection and complete version snapshot. Add account-level cases for eligible student/staff/visitor, pure guardian exemption and guardian+staff participation. An eligible incomplete self save with either list empty must fail with zero writes; a 1+1 save sets an aware completion time in the same transaction.

- [ ] **Step 2: Run service tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preferences.py -q
```

Expected: import fails for `preference_service.py`.

- [ ] **Step 3: Implement read projection and command DTOs**

Add immutable `ReplaceFoodPreferenceCommand` and `FoodPreferenceProjection`; missing current root returns version 0, empty tuples and null updated time.

Add `FoodPreferenceSelfProjection(profile, onboarding_required, onboarding_completed_at)` and a deterministic clock dependency. Eligibility is an `EXISTS` over `StudentProfile`, `StaffProfile` or `VisitorProfile`; guardian relationships/roles do not exempt a person who has one of those profiles.

- [ ] **Step 4: Implement the transaction wrappers**

Expose both wrappers, with no duplicated write logic:

```python
def replace(self, command: ReplaceFoodPreferenceCommand) -> FoodPreferenceProjection:
    with write_transaction(self.db):
        return self.replace_locked(command)

def replace_locked(
    self,
    command: ReplaceFoodPreferenceCommand,
) -> FoodPreferenceProjection:
    """Caller owns the transaction; lock Person then current root; never commit."""
```

Add the only completion-writing entry point:

```python
def replace_self(
    self,
    *,
    account_id: UUID,
    command: ReplaceFoodPreferenceCommand,
) -> FoodPreferenceSelfProjection:
    """Lock account, then Person/current root; complete onboarding only for self."""
```

`replace_self()` ignores any caller-supplied target and derives `person_id`/actor/source from the locked active `UserAccount`. Generic `replace()` and caller-owned `replace_locked()` never set the completion field.

- [ ] **Step 5: Implement validation and no-op behavior**

Add first empty save as no-op, ordered 0–3 replacement, clear-to-empty new version, exact no-op reuse, Other pairing, inactive unchanged preservation, inactive new selection rejection, duplicates and fourth-item rejection. In `replace_self`, an eligible account with null completion requires 1–3 cuisines and 1–3 dishes; once completed, or for a pure guardian, the normal 0–3 rule applies. Clearing later never changes the stored completion time.

- [ ] **Step 6: Implement version/current replacement and audit**

Inside `replace_locked`, lock a non-deleted Person, compare version, resolve dictionary rows in stable UUID order, append the complete new snapshot, replace current child rows, update root, and append safe audit in the same transaction. `replace_self` first locks `UserAccount`, delegates to the same locked preference code, then sets completion time only when eligible/incomplete and appends `food_preferences.onboarding.complete`; even a content no-op may complete onboarding without creating a duplicate preference version. Audit failure rolls back preference, history and completion together.

- [ ] **Step 7: Run lifecycle tests and verify GREEN**

Run the Step 2 command. Expected: service lifecycle, validation, history, no-op and audit tests PASS.

- [ ] **Step 8: Add simultaneous first-save concurrency test**

Two expected-version-0 requests use two PostgreSQL sessions and a barrier; exactly one succeeds and only one version-1 snapshot exists.

- [ ] **Step 9: Add stale-replace and lock-order concurrency tests**

Two version-1 replacements yield one version 2 and one typed `FoodPreferenceVersionChanged`; generic→guardian and guardian→generic lock paths use the same `Person -> preference` order. Add generic-admin→self-onboarding and self-onboarding→generic-admin cases: self may lock its account before `Person`, while every competing preference path converges on `Person -> preference`, so neither direction deadlocks. After conflict, both sessions can execute `select(1)` and the failed session can read the winning version; a losing onboarding request leaves completion null.

- [ ] **Step 10: Run concurrency tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/db/test_food_preference_concurrency.py -q
```

- [ ] **Step 11: Add the Person-root lock and stale-version mapping**

Lock `Person` before current root in every path. Catch uniqueness races only at the transaction boundary, roll back to a usable session and map the loser to `FoodPreferenceVersionChanged` without retrying a stale write. This is the only interface the future survey service may call.

- [ ] **Step 12: Run service/concurrency/static gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preferences.py \
  apps/api/tests/db/test_food_preference_concurrency.py -q
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m ruff check apps/api/app/food_preferences/preference_service.py \
  apps/api/tests/services/test_food_preferences.py \
  apps/api/tests/db/test_food_preference_concurrency.py
```

Expected: lifecycle and concurrency tests PASS; Ruff prints no errors.

- [ ] **Step 13: Commit versioned preference service**

```bash
git add apps/api/app/food_preferences/preference_service.py \
  apps/api/tests/services/test_food_preferences.py \
  apps/api/tests/db/test_food_preference_concurrency.py
git commit -m "feat: version person food preferences"
```

### Task 5: Expose private profile, catalog and suggestion APIs

**Files:**

- Create: `apps/api/app/food_preferences/router.py`
- Create: `apps/api/app/food_preferences/routing.py`
- Create: `apps/api/tests/api/test_food_preferences.py`
- Modify: `apps/api/app/food_preferences/schemas.py`
- Modify: `apps/api/tests/unit/food_preferences/test_schemas.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/main.py`

- [ ] **Step 1: Add profile GET privacy tests**

Test standard account, admin/self/family/person reads, account-level 403, equivalent object 404 and no PII fields.

Expected profile response shape:

```json
{
  "person_id": "uuid",
  "version": 2,
  "cuisines": [{"cuisine_option_id": "uuid", "name": "川菜", "custom_name": null}],
  "dishes": ["宫保鸡丁"],
  "updated_at": "2026-08-11T10:00:00+08:00",
  "allowed_actions": ["update"],
  "access_revision": "64-hex"
}
```

Add `/api/v1/me/food-preferences` cases for student, coach/staff, visitor-account and pure guardian. The self response is closed and wraps the same profile:

```json
{
  "profile": {"person_id": "uuid", "version": 0, "cuisines": [], "dishes": [], "updated_at": null, "allowed_actions": ["update"], "access_revision": "64-hex"},
  "onboarding_required": true,
  "onboarding_completed_at": null
}
```

Pure guardian returns `onboarding_required=false`; a guardian+staff account returns true. A self deny returns 403 and no profile body.

- [ ] **Step 2: Add profile PUT and 409 tests**

Test CSRF, strict extra-field rejection, typed 409 `food_preference_version_changed`, closed 422 fields and failed-path zero audit/business/account-completion writes. For incomplete eligible `/me` PUT, 0+0, 1+0 and 0+1 return 422; 1+1 sets completion. Skip has no API route. Admin/family PUT on `/people/{id}` never sets the target account completion, and later `/me` clear preserves it.

- [ ] **Step 3: Add catalog and suggestion route tests**

Cover `include_inactive`, catalog global management, suggestion query/limit validation and response field closure.

- [ ] **Step 4: Add all-status private-header tests**

Assert `Cache-Control: no-store, private`, `Pragma: no-cache` and `X-Content-Type-Options: nosniff` on 200/201/403/404/409/422/500.

- [ ] **Step 5: Run API tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/api/test_food_preferences.py -q
```

Expected: route requests return 404 because the router is not registered.

- [ ] **Step 6: Implement private response middleware**

`PrivateFoodPreferenceResponseMiddleware` covers `/api/v1/food-preferences`, exact `/api/v1/me/food-preferences`, and exact `/api/v1/people/{uuid}/food-preferences` shapes, sets the existing private-response request state, and adds the three frozen headers to success and error responses. Register it in `main.py`.

- [ ] **Step 7: Register the food-preference router**

Create `APIRouter(tags=["food-preferences"])`, dependencies that instantiate access/catalog/preference services, and include it once from `api/router.py`.

- [ ] **Step 8: Implement catalog and suggestion handlers**

Map closed schemas to `CuisineCatalogService`; require standard account and reauthorize in the service for every call.

- [ ] **Step 9: Implement profile GET/PUT handlers**

Use `require_standard_account`; never trust `AuthUser.permissions` as authorization. `GET cuisines` returns a closed catalog envelope containing `items`, `allowed_actions`, and `access_revision`; this envelope is the Web authorization context, so no undocumented context route is added. Profile GET/PUT expose no actor, relationship, phone, identity, account or history fields；无当前偏好时 `updated_at` 为 null。

Implement `GET/PUT /me/food-preferences` without a person path parameter. Both derive target from `AuthContext`, call `FoodPreferenceAccess.require_self`, and return `FoodPreferenceSelfResponse`. PUT calls only `replace_self`; generic `/people/{person_id}` continues to call generic replacement and therefore cannot mark onboarding complete.

- [ ] **Step 10: Run API/static gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/api/test_food_preferences.py -q
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m ruff check apps/api/app/food_preferences/router.py \
  apps/api/app/food_preferences/routing.py apps/api/tests/api/test_food_preferences.py
```

Expected: API tests PASS and Ruff prints no errors.

- [ ] **Step 11: Commit the private APIs**

```bash
git add apps/api/app/food_preferences/router.py apps/api/app/food_preferences/schemas.py \
  apps/api/app/food_preferences/routing.py apps/api/app/api/router.py \
  apps/api/app/main.py apps/api/tests/api/test_food_preferences.py \
  apps/api/tests/unit/food_preferences/test_schemas.py
git commit -m "feat: expose food preference APIs"
```

### Task 6: Build one consistent statistics projection

**Files:**

- Create: `apps/api/app/food_preferences/reporting.py`
- Create: `apps/api/app/food_preferences/dependencies.py`
- Create: `apps/api/tests/services/test_food_preference_reporting.py`
- Modify: `apps/api/app/food_preferences/router.py`
- Modify: `apps/api/tests/api/test_food_preferences.py`

- [ ] **Step 1: Add population and person-row tests**

Fixtures include active/draft/inactive/deleted students, active/deleted staff/visitor, a student who is also a guardian, empty preferences, inactive cuisine snapshots, custom Other and case/width-equivalent dishes. Assert each person appears once, person types use stable `student, guardian, staff, visitor` order, default/current and include-history ranges are exact, and no history version is double counted. A non-deleted student uses its profile status, a non-student is `active`, and any soft-deleted person is `deleted`; “已填写” means at least one current cuisine or dish exists。

- [ ] **Step 2: Run reporting tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preference_reporting.py -q
```

Expected: import fails for `reporting.py`.

- [ ] **Step 3: Implement population selection and stable person rows**

Build student/all-people predicates, current/history flags, ordered multi-role projection, status mapping and name/UUID ordering. Do not calculate ranks yet.

- [ ] **Step 4: Re-run person-row tests**

Run the Step 2 command. Expected: population/person tests PASS while rank tests do not yet exist.

- [ ] **Step 5: Add aggregate-key and percentage tests**

Standard cuisine groups by option UUID; Other groups by `custom_normalized_name` and displays `其他：<custom_name>`. Dishes group by normalized name and choose the lexicographically smallest current display name. All ranks sort count DESC, normalized label, stable UUID/key. Ratios use total target people as denominator and return zero when total is zero.

- [ ] **Step 6: Implement the reusable snapshot builder**

```python
@dataclass(frozen=True, slots=True)
class FoodPreferenceReportSnapshot:
    population: Literal["students", "all_people"]
    include_inactive: bool
    total_people: int
    filled_people: int
    people: Sequence[FoodPreferencePersonRow]
    cuisines: Sequence[FoodPreferenceRankRow]
    dishes: Sequence[FoodPreferenceRankRow]
```

`build_snapshot()` reads only current roots/children and returns tuples. `get_food_reporting_db` creates a dedicated `REPEATABLE READ` session, so every population, role, current preference and aggregate query belongs to one database snapshot. `statistics()` slices `people` after aggregates are frozen; export reuses the unsliced snapshot instead of reimplementing SQL.

- [ ] **Step 7: Add statistics route/pagination tests**

Extend `test_food_preferences.py` with global-only authorization, `population`, `include_inactive`, `page >= 1`, `page_size 1–100`, aggregates independent of page and private headers.

- [ ] **Step 8: Implement the statistics handler**

Add GET `/food-preferences/statistics` on the dedicated reporting dependency; reauthorize `food_preferences.export` global before creating the snapshot and return a closed response.

- [ ] **Step 9: Run reporting/API/static gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preference_reporting.py \
  apps/api/tests/api/test_food_preferences.py -q
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m ruff check apps/api/app/food_preferences/reporting.py \
  apps/api/app/food_preferences/dependencies.py \
  apps/api/tests/services/test_food_preference_reporting.py
```

Expected: reporting and API tests PASS; Ruff prints no errors.

- [ ] **Step 10: Commit statistics projection**

```bash
git add apps/api/app/food_preferences/reporting.py \
  apps/api/app/food_preferences/dependencies.py \
  apps/api/app/food_preferences/router.py \
  apps/api/tests/services/test_food_preference_reporting.py \
  apps/api/tests/api/test_food_preferences.py
git commit -m "feat: add food preference statistics"
```

### Task 7: Render XLSX/CSV ZIP and audit only completed streams

**Files:**

- Create: `apps/api/app/food_preferences/renderer.py`
- Create: `apps/api/app/food_preferences/export_service.py`
- Modify: `apps/api/app/food_preferences/dependencies.py`
- Create: `apps/api/tests/unit/food_preferences/test_renderer.py`
- Create: `apps/api/tests/services/test_food_preference_exports.py`
- Modify: `apps/api/app/food_preferences/router.py`
- Modify: `apps/api/tests/api/test_food_preferences.py`

- [ ] **Step 1: Add formula-safe table tests**

Assert all columns and formula-safe text for every `= + - @ tab CR` prefix before testing file containers.

- [ ] **Step 2: Run renderer tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m pytest -p no:cacheprovider \
  apps/api/tests/unit/food_preferences/test_renderer.py -q
```

Expected: import fails for `renderer.py`.

- [ ] **Step 3: Implement safe table construction**

Reuse the housing renderer pattern but do not import housing business types. Implement constants, table row functions and `safe_spreadsheet_text()` that prefixes dangerous content with `'`.

- [ ] **Step 4: Add XLSX container tests**

Open output with openpyxl and assert exactly `人员偏好明细`、`菜系统计`、`菜品统计`, fixed metadata, headers, freeze panes, filters and safe cell data types.

- [ ] **Step 5: Implement XLSX rendering**

Use one in-memory `BytesIO`, deterministic sheet order and bounded column widths.

- [ ] **Step 6: Add CSV ZIP container tests**

Open with `ZipFile`; assert exactly three same-named UTF-8 BOM CSV members, stable timestamps/permissions and row parity with XLSX.

- [ ] **Step 7: Implement CSV ZIP rendering**

Use fixed `ZipInfo` metadata and no filesystem path. Re-run the Step 2 command and expect all renderer tests PASS.

- [ ] **Step 8: Add export authorization/render/audit failures**

Test global permission only, purpose 4–200, population/range parity with statistics, `Content-Disposition`, MIME, SHA-256, no filesystem writes, render failure, audit preflight failure, permission revoked immediately before query, and cancellation before the final stream completion. Only a fully consumed successful stream may commit one audit event; cancellation rolls back it.

- [ ] **Step 9: Run export service tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_food_preference_exports.py -q
```

Expected: import fails for `export_service.py` and `dependencies.py`.

- [ ] **Step 10: Add the dedicated repeatable-read dependency**

Extend the existing `get_food_reporting_db` teardown so an uncommitted/cancelled streaming response always rolls back while the statistics path still closes its completed read transaction.

- [ ] **Step 11: Implement export preparation**

Reauthorize, build the snapshot, render, hash, create safe ASCII filenames such as `food-preferences-students-20260811T102030Z.xlsx`, and flush one audit with `AuditEvent.purpose` plus safe range/count/hash metadata before returning prepared bytes.

- [ ] **Step 12: Implement stream commit/cancellation behavior**

```python
async def stream_export(prepared: PreparedFoodPreferenceExport) -> AsyncIterator[bytes]:
    try:
        yield prepared.content
        prepared.db.commit()
    except BaseException:
        prepared.db.rollback()
        raise
```

Audit uses `AuditEvent.purpose` for the business purpose and safe metadata for range/count/hash. It is flushed before the first byte so audit failure yields no file; commit occurs only after Starlette resumes the generator after sending the final chunk. Cancellation rolls back. Document in the service that network receipt and database commit cannot be made mathematically atomic, but this ordering is the tested failure boundary.

- [ ] **Step 13: Add and implement POST export API tests**

Extend API tests for strict body, CSRF, attachment headers, format bytes, revoke-before-query, renderer failure and fully-consumed audit. Implement POST `/food-preferences/exports` with `StreamingResponse`.

- [ ] **Step 14: Run renderer/export/API/static gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/unit/food_preferences/test_renderer.py \
  apps/api/tests/services/test_food_preference_exports.py \
  apps/api/tests/api/test_food_preferences.py -q
PYTHONPATH="$PWD/apps/api" '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' \
  -m ruff check apps/api/app/food_preferences/renderer.py \
  apps/api/app/food_preferences/export_service.py \
  apps/api/app/food_preferences/dependencies.py
```

Expected: all renderer/export/API tests PASS and Ruff prints no errors.

- [ ] **Step 15: Commit export reporting**

```bash
git add apps/api/app/food_preferences/renderer.py \
  apps/api/app/food_preferences/export_service.py \
  apps/api/app/food_preferences/dependencies.py \
  apps/api/app/food_preferences/router.py \
  apps/api/tests/unit/food_preferences/test_renderer.py \
  apps/api/tests/services/test_food_preference_exports.py \
  apps/api/tests/api/test_food_preferences.py
git commit -m "feat: export food preference reports"
```

### Task 8: Add Web contracts, authorization keys and reusable editor

**Files:**

- Create: `apps/web/src/foodPreferences/api.ts`
- Create: `apps/web/src/foodPreferences/api.test.ts`
- Create: `apps/web/src/foodPreferences/keys.ts`
- Create: `apps/web/src/foodPreferences/keys.test.ts`
- Create: `apps/web/src/foodPreferences/FoodPreferencesPanel.tsx`
- Create: `apps/web/src/foodPreferences/FoodPreferencesPanel.test.tsx`
- Create: `apps/web/src/foodPreferences/food-preferences.css`
- Modify: `apps/web/src/api/client.ts`
- Modify: `apps/web/src/api/client.test.ts`

- [ ] **Step 1: Add raw-response client tests**

Prove a POST raw response receives CSRF/cookies, normalizes Problem Details, invokes invalid-session handling and does not parse a successful body as JSON.

- [ ] **Step 2: Run shared-client tests and verify RED**

```bash
npm --prefix apps/web test -- --run src/api/client.test.ts
```

Expected: tests fail because `apiFetchResponse()` is not exported.

- [ ] **Step 3: Implement `apiFetchResponse()` and delegate JSON requests**

Move header/session/error handling into the raw function; keep `apiFetch()` behavior unchanged by parsing the returned response.

- [ ] **Step 4: Re-run shared-client tests and verify GREEN**

Run the Step 2 command. Expected: existing and new client tests PASS.

- [ ] **Step 5: Add strict food API schema tests**

All catalog/profile/self-profile/statistics JSON schemas use `.strict()`. Tests reject unknown fields, malformed UUID/version/revision, nonclosed action enums and wrong export MIME/filename. Freeze `onboarding_required` as strict boolean and `onboarding_completed_at` as nullable aware ISO timestamp.

- [ ] **Step 6: Implement typed API functions and attachment parsing**

Add catalog/profile/update/self-profile/self-update/suggestion/statistics/export calls. Self functions call only `/api/v1/me/food-preferences` and accept no person ID. Export uses `apiFetchResponse()`, permits only safe ASCII attachment names and returns `{blob, filename}` without storing the purpose.

- [ ] **Step 7: Add query-key and purge tests**

Freeze account/revision/person keys and prove a revision change removes old current-account revision roots without touching another account.

- [ ] **Step 8: Implement query keys and authorization-context hook**

```ts
export const foodPreferenceKeys = {
  accountRoot: (accountId: string) => ["food-preferences", accountId] as const,
  catalog: (accountId: string) => ["food-preferences", accountId, "catalog"] as const,
  revisionRoot: (accountId: string, revision: string) =>
    ["food-preferences", accountId, "revision", revision] as const,
  person: (accountId: string, revision: string, personId: string) =>
    ["food-preferences", accountId, "revision", revision, "person", personId] as const,
}
```

Catalog refetch owns authorization revision. When it changes, cancel and remove every previous revision root for the current account, close active editors/dialogs, then enable new queries. Login/logout already calls `queryClient.clear()`.

- [ ] **Step 9: Add read-only and basic editor tests**

Cover read-only/action-driven edit, loading/reauthorizing states and no role-specific authority.

- [ ] **Step 10: Implement read projection and edit-state shell**

Use a discriminated prop contract with no role-specific flags:

```ts
type FoodPreferencesPanelProps =
  | {
      mode: "profile"
      personId: string
      onAccessRevoked?: () => void
      onCommitted?: (value: FoodPreferenceProfile) => void
    }
  | {
      mode: "self"
      intent: "profile" | "onboarding"
      onSkipped?: () => void
      onCommitted?: (value: FoodPreferenceSelfResponse) => void
    }
  | {
      mode: "draft"
      personId: string
      required: true
      value: FoodPreferenceDraft
      onChange: (value: FoodPreferenceDraft) => void
    }
```

Profile mode uses server `allowed_actions` as the only edit authority. Self mode calls only `/me`; onboarding intent requires 1+1 locally, shows “稍后填写”, and never marks completion on skip. Draft mode only renders controlled fields/catalog/suggestions and never calls profile PUT; the future survey parent owns its atomic submit. Use `staleTime: 0`, `gcTime: 0`, hide stale content while reauthorizing, and call `mutation.reset()` after success/error UI has copied only the safe status.

- [ ] **Step 11: Add selection and accessibility interaction tests**

Cover 0/3–3/3 counters, Other custom input, suggestions only after nonblank query, exact duplicate, fourth-item prevention, keyboard removal, 44px controls and no local/session storage.

Add a controlled `mode="draft"` case proving 0+0 is invalid when required, 1+1 is valid, changes call `onChange`, and no profile PUT/save button exists.
Add `mode="self" intent="onboarding"` cases proving skip calls only `onSkipped`, 0+0/1+0/0+1 cannot submit, 1+1 calls self PUT, prefilled admin data still requires本人确认, and completion/clear behavior follows the server envelope. No skip/completion flag is written to local/session storage.

- [ ] **Step 12: Implement selection, suggestions and save**

Keep draft only in component state. After save, invalidate only catalog/current-person/statistics keys and reset mutation variables.

- [ ] **Step 13: Add typed 409 recovery tests**

Preserve local draft, fetch newest version without painting it over the draft, display “资料已被更新，请确认后重试”, and submit the refreshed version only after explicit confirmation.

- [ ] **Step 14: Implement 409 refresh-and-confirm flow**

Copy only the refreshed numeric version into conflict state; do not silently merge or overwrite. Cancel/route/revision changes clear the conflict and draft.

- [ ] **Step 15: Run Web unit/static/build gates**

```bash
npm --prefix apps/web test -- --run \
  src/api/client.test.ts src/foodPreferences/api.test.ts \
  src/foodPreferences/keys.test.ts \
  src/foodPreferences/FoodPreferencesPanel.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: all listed Vitest tests, lint and TypeScript/Vite build PASS.

- [ ] **Step 16: Commit Web contracts and editor**

```bash
git add apps/web/src/api/client.ts apps/web/src/api/client.test.ts \
  apps/web/src/foodPreferences/api.ts apps/web/src/foodPreferences/api.test.ts \
  apps/web/src/foodPreferences/keys.ts apps/web/src/foodPreferences/keys.test.ts \
  apps/web/src/foodPreferences/FoodPreferencesPanel.tsx \
  apps/web/src/foodPreferences/FoodPreferencesPanel.test.tsx \
  apps/web/src/foodPreferences/food-preferences.css
git commit -m "feat: add food preference editor"
```

### Task 9: Add skippable onboarding and embed admin, self and family entries

**Files:**

- Create: `apps/web/src/foodPreferences/MyFoodProfilePage.tsx`
- Create: `apps/web/src/foodPreferences/MyFoodProfilePage.test.tsx`
- Create: `apps/web/src/foodPreferences/FoodOnboardingPage.tsx`
- Create: `apps/web/src/foodPreferences/FoodOnboardingPage.test.tsx`
- Modify: `apps/api/app/portal/schemas.py`
- Modify: `apps/api/app/portal/context_service.py`
- Modify: `apps/api/tests/services/test_portal_context.py`
- Modify: `apps/api/tests/api/test_portal.py`
- Modify: `apps/web/src/portal/api.ts`
- Modify: `apps/web/src/portal/api.test.ts`
- Modify: `apps/web/src/people/PersonDetailPage.tsx`
- Modify: `apps/web/src/people/PersonDetailPage.test.tsx`
- Modify: `apps/web/src/portal/LearningPage.tsx`
- Modify: `apps/web/src/portal/LearningPage.test.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`

- [ ] **Step 1: Add backend portal-capability tests**

`PortalContextResponse` gains strict booleans `food_profile_available` and `food_preferences_onboarding_required`.

Freeze this matrix:

```text
active student/staff/visitor + completion NULL + no self deny => available=true, required=true
pure guardian + completion NULL                              => available=true, required=false
guardian + staff/student/visitor + completion NULL           => available=true, required=true
eligible account + completion set                            => available=true, required=false
explicit read/update self deny                               => available/required reflect the denied operation
disabled/non-standard service account                        => no ordinary portal projection
```

Admin/staff prefilled preferences do not change `required`; only the account completion timestamp does.

- [ ] **Step 2: Run portal tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py -q
```

Expected: response/schema assertions fail because the two capabilities are absent.

- [ ] **Step 3: Implement backend portal capability**

Evaluate the actor's exact self target with `FoodPreferenceAccess.require_self`; do not infer from permission-name presence. Compute eligibility with `EXISTS(StudentProfile|StaffProfile|VisitorProfile)` and read the account completion field in the same context query. Pure guardian means none of those profiles; a guardian relationship or role never cancels another profile. A self deny makes the unavailable operation and onboarding reminder disappear rather than showing a link that will always 403.

- [ ] **Step 4: Update and test the Web portal schema**

Add both strict booleans to `portal/api.ts`, its Zod/fixture tests, and `PortalCapability`.

- [ ] **Step 5: Add onboarding and `/me/profile` route tests**

Test onboarding prefill, student/staff/visitor rendering, pure-guardian exemption, guardian+staff participation, loading/revocation and no generic People management controls. `FoodOnboardingPage` renders `FoodPreferencesPanel mode="self" intent="onboarding"`; “稍后填写” navigates to `/` without a mutation, while success refreshes portal context before navigating. `MyFoodProfilePage` uses `mode="self" intent="profile"`, so pure guardians may maintain their own optional preferences without receiving onboarding.

- [ ] **Step 6: Implement onboarding, `MyFoodProfilePage` and capability routes**

Add `/onboarding/food-preferences` behind `food_preferences_onboarding_required` and `/me/profile` behind `food_profile_available`. Use the shared self panel; neither route accepts or constructs another person's ID. Add “个人资料” navigation without exposing “人员中心”. Public self-registration is still deferred, so this task freezes a `foodOnboardingPath` navigation target for the future registration success handler; current ERP login shows the server-backed homepage待办 rather than inventing a client-only “first login” flag.

- [ ] **Step 7: Add PersonDetail embedding tests**

Test global admin, exact person override, account with unrelated scope, private 404 hide and server `allowed_actions` read-only state.

- [ ] **Step 8: Embed the panel in `PersonDetailPage`**

Pass the union of global/scoped permission names only as a query eligibility hint; the panel/server still decides target authorization.

- [ ] **Step 9: Add family LearningPage and revocation tests**

Test current student panel, two-student switching, relation end while editing, cache removal, next safe target and empty-family fallback.

- [ ] **Step 10: Embed the panel in `LearningPage`**

Mount it below current-learning summary for the currently reauthorized student. Relationship revocation follows existing learning recovery: cancel target query, clear it, close edit state, refresh family students, navigate to the next safe student or `/learning`.

- [ ] **Step 11: Add router/navigation/360px tests**

Add “个人资料” to the portal-capability navigation and a persistent “完善餐饮偏好” AppShell待办 linking to onboarding when `food_preferences_onboarding_required=true`. Skip returns home while leaving the待办 visible; completion invalidates/refetches portal context and removes it. Preserve mobile priority rules and do not expose “人员中心” to self/family accounts. Tests at 360 CSS assumptions require one column, no horizontal overflow and panel controls with accessible names.

- [ ] **Step 12: Run backend/Web/static/build gates**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/services/test_portal_context.py \
  apps/api/tests/api/test_portal.py -q
npm --prefix apps/web test -- --run \
  src/people/PersonDetailPage.test.tsx src/portal/LearningPage.test.tsx \
  src/foodPreferences/MyFoodProfilePage.test.tsx \
  src/foodPreferences/FoodOnboardingPage.test.tsx \
  src/app/router.test.tsx src/layout/AppShell.test.tsx
npm --prefix apps/web run lint && npm --prefix apps/web run build
```

Expected: backend and Web tests PASS; lint/build exit 0.

- [ ] **Step 13: Commit the three profile entries**

```bash
git add apps/api/app/portal/schemas.py apps/api/app/portal/context_service.py \
  apps/api/tests/services/test_portal_context.py apps/api/tests/api/test_portal.py \
  apps/web/src/portal/api.ts apps/web/src/portal/api.test.ts \
  apps/web/src/people/PersonDetailPage.tsx \
  apps/web/src/people/PersonDetailPage.test.tsx \
  apps/web/src/portal/LearningPage.tsx apps/web/src/portal/LearningPage.test.tsx \
  apps/web/src/foodPreferences/MyFoodProfilePage.tsx \
  apps/web/src/foodPreferences/MyFoodProfilePage.test.tsx \
  apps/web/src/foodPreferences/FoodOnboardingPage.tsx \
  apps/web/src/foodPreferences/FoodOnboardingPage.test.tsx \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.test.tsx
git commit -m "feat: add food onboarding and profile entries"
```

### Task 10: Build administration statistics, export and cuisine settings pages

**Files:**

- Create: `apps/web/src/foodPreferences/FoodPreferenceStatisticsPage.tsx`
- Create: `apps/web/src/foodPreferences/FoodPreferenceStatisticsPage.test.tsx`
- Create: `apps/web/src/foodPreferences/FoodCuisineSettingsPage.tsx`
- Create: `apps/web/src/foodPreferences/FoodCuisineSettingsPage.test.tsx`
- Modify: `apps/web/src/foodPreferences/api.ts`
- Modify: `apps/web/src/foodPreferences/api.test.ts`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`

- [ ] **Step 1: Add statistics-page projection tests**

Test total/filled/unfilled/rate, cuisine/dish ranks, empty people, students/all-people toggle, include-history switch, pagination and loading/error/retry.

- [ ] **Step 2: Implement statistics queries and page state**

Keep population/include-history/page in URL-safe state, reset page on range change, and render under-768 labeled cards rather than a horizontally scrolling table.

- [ ] **Step 3: Add export-dialog tests**

Require purpose 4–200 and format choice; save only sanitized server filename, revoke object URLs, and clear purpose on completion/revocation.

- [ ] **Step 4: Implement export dialog and download**

Use the typed API raw response, disable duplicate submits and clear mutation variables after copying only success/error state.

- [ ] **Step 5: Add cuisine settings tests**

Test active/inactive groups, add, rename, numeric sort order, disable/reactivate, normalized duplicate and protected Other controls. Buttons render only from catalog `allowed_actions`; after revocation the dialog closes before navigation.

- [ ] **Step 6: Implement cuisine settings page**

Use create/patch endpoints; invalidate catalog, close dialog after mutation, and preserve server validation field messages without leaking other response data.

- [ ] **Step 7: Add protected route/navigation tests**

Test global permissions, forbidden scoped-only names, revision change, cache purge, dialog close and safe `/` navigation.

- [ ] **Step 8: Implement protected routes and navigation**

Add `/people/food-preferences` behind effective global `food_preferences.export` and `/settings/food-cuisines` behind `food_preferences.catalog.manage`. Route boundaries refetch catalog authorization before rendering, purge old revision roots and navigate to `/` on 403/private loss. Add desktop/mobile nav entries without changing unrelated priorities.

- [ ] **Step 9: Add responsive CSS contract tests**

Freeze 1440/1024 desktop table layout, under-768 labeled cards, 360 single column, no horizontal overflow, accessible names and 44px targets.

- [ ] **Step 10: Implement final administration CSS**

Add only food-preference selectors; do not alter unrelated global layout rules.

- [ ] **Step 11: Run Web unit/static/build gates**

```bash
npm --prefix apps/web test -- --run \
  src/foodPreferences/FoodPreferenceStatisticsPage.test.tsx \
  src/foodPreferences/FoodCuisineSettingsPage.test.tsx \
  src/foodPreferences/api.test.ts src/app/router.test.tsx \
  src/layout/AppShell.test.tsx
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: administration tests, lint and build PASS.

- [ ] **Step 12: Commit administration pages**

```bash
git add apps/web/src/foodPreferences/FoodPreferenceStatisticsPage.tsx \
  apps/web/src/foodPreferences/FoodPreferenceStatisticsPage.test.tsx \
  apps/web/src/foodPreferences/FoodCuisineSettingsPage.tsx \
  apps/web/src/foodPreferences/FoodCuisineSettingsPage.test.tsx \
  apps/web/src/foodPreferences/api.ts apps/web/src/foodPreferences/api.test.ts \
  apps/web/src/app/router.tsx apps/web/src/app/router.test.tsx \
  apps/web/src/layout/AppShell.tsx apps/web/src/layout/AppShell.test.tsx
git commit -m "feat: manage food preference reporting"
```

### Task 11: Complete deterministic seed and four-viewport E2E

**Files:**

- Create: `apps/api/app/cli/seed_food_preferences_e2e.py`
- Create: `scripts/seed-food-preferences-e2e.py`
- Create: `apps/api/tests/cli/test_seed_food_preferences_e2e.py`
- Create: `scripts/run-food-preferences-e2e.sh`
- Create: `apps/web/tests/food-preferences.spec.ts`
- Create: `apps/web/tests/support/inspect-food-preferences-export.py`
- Create: `apps/web/tests/support/test_inspect_food_preferences_export.py`
- Modify: `apps/web/playwright.config.ts`
- Modify: `apps/api/tests/cli/test_e2e_launchers.py`

- [ ] **Step 1: Add seed safety tests**

Test refusal outside canonical test DBs and refusal when base fixture ownership collides.

- [ ] **Step 2: Add seed determinism/fixture tests**

Require preservation of unrelated rows and fixtures for incomplete student, incomplete staff/coach, pure guardian, guardian+staff multi-identity, completed account, guardian with two students, ended relationship, active/draft/inactive/deleted people, active/inactive/Other cuisines, admin-prefilled-but-unconfirmed preference, empty/current preferences and formula-prefix values. Output only UUIDs and credential references, never preference text or secrets.

- [ ] **Step 3: Run seed tests and verify RED**

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/cli/test_seed_food_preferences_e2e.py -q
```

Expected: import fails for `seed_food_preferences_e2e.py`.

- [ ] **Step 4: Implement deterministic owned-fixture seed**

Use fixed UUIDs, canonical base fixtures and explicit cleanup only for rows owned by this seed. Running twice returns the same result/created timestamps and preserves unrelated data.

- [ ] **Step 5: Add wrapper/runner safety tests**

Test module delegation, executable help, required `TEST_DATABASE_URL`, canonical DB checks, strict-port checks and unconditional temp/process cleanup.

- [ ] **Step 6: Implement seed wrapper and E2E runner shell**

Follow the teaching-report runner: migrate, start local API/Web on reserved ports, reseed per viewport, run only `food-preferences.spec.ts`, surface logs on failure and delete temp files on every exit.

- [ ] **Step 7: Add export inspector tests**

The inspector accepts only a generated path under the runner temp directory, validates three sheets/members, formula cell type/text safety, counts and CSV/XLSX parity, then emits JSON counts/hashes without names or food text.

- [ ] **Step 8: Implement the privacy-bounded export inspector**

Reject symlinks/out-of-root files and unsupported extensions before opening; output only the frozen count/hash JSON.

- [ ] **Step 9: Add onboarding, self and guardian Playwright journeys**

For every viewport, cover:

1. incomplete student opens onboarding, skips with no request, reaches `/`, and still sees the待办;
2. returning to onboarding with admin-prefilled data still requires本人确认;
3. successful 1+1 self submit removes the待办 and stores an aware completion time;
4. later self clear keeps completion and does not restore the待办;
5. pure guardian has no onboarding待办, while guardian+staff does;
6. guardian two-student editing and relationship revocation never set either student's account completion.

- [ ] **Step 10: Add admin/catalog/statistics/export journeys**

Cover person edit, cuisine create/disable, old snapshot display, both population/history switches and XLSX/CSV ZIP inspection.

- [ ] **Step 11: Add 409/privacy/responsive journeys**

For `desktop-1440`, `desktop-1024`, `tablet-768`, `mobile-360`, cover:

1. two contexts trigger 409, preserve draft and explicitly retry;
2. an explicit self-denied coach cannot use `/me`, while an ordinary coach can access only self and never another person's data;
3. no horizontal overflow, keyboard labels and 44px target assertions.

- [ ] **Step 12: Run the official local E2E and focused suite**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" ./scripts/run-food-preferences-e2e.sh
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests/unit/food_preferences \
  apps/api/tests/db/test_food_preference_models.py \
  apps/api/tests/db/test_food_preference_concurrency.py \
  apps/api/tests/services/test_food_preference_access.py \
  apps/api/tests/services/test_food_cuisine_catalog.py \
  apps/api/tests/services/test_food_preferences.py \
  apps/api/tests/services/test_food_preference_reporting.py \
  apps/api/tests/services/test_food_preference_exports.py \
  apps/api/tests/api/test_food_preferences.py \
  apps/api/tests/cli/test_seed_food_preferences_e2e.py -q
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

Expected: all focused API, PostgreSQL, Web and four Playwright projects PASS with no privacy-bearing artifacts left behind.

- [ ] **Step 13: Commit E2E evidence source**

```bash
git add apps/api/app/cli/seed_food_preferences_e2e.py \
  apps/api/tests/cli/test_seed_food_preferences_e2e.py \
  apps/api/tests/cli/test_e2e_launchers.py scripts/seed-food-preferences-e2e.py \
  scripts/run-food-preferences-e2e.sh apps/web/tests/food-preferences.spec.ts \
  apps/web/tests/support/inspect-food-preferences-export.py \
  apps/web/tests/support/test_inspect_food_preferences_export.py \
  apps/web/playwright.config.ts
git commit -m "test: gate food preference journeys"
```

### Task 12: Hand the frozen interface to camp surveys and stage-2 acceptance

**Files:**

- Modify: `docs/superpowers/plans/2026-08-09-camp-evaluation-surveys.md`
- Modify: `docs/superpowers/plans/2026-08-09-stage2-local-acceptance.md`
- Modify: `README.md`

- [ ] **Step 1: Change the unimplemented survey migration dependency**

Change migration to `0016_camp_evaluation_surveys` and dependency head to `0015_food_preferences` in every Task 2 path/command.

- [ ] **Step 2: Add survey persistence/synchronization tasks**

Add `favorite_cuisines`, `favorite_dishes`, `food_preference_version` snapshots. Submission/revision calls `FoodPreferenceService.replace_locked()` inside the survey transaction after survey lock order; 409 rolls back survey, ratings, preference and audit. Staff supplements never resync food preferences.

- [ ] **Step 3: Add survey Web requirements**

The future survey page reuses `FoodPreferencesPanel` in `required=true` draft mode, prefills current version, requires at least 1+1, preserves input on 409 and stores the immutable returned snapshot in each respondent's version. Student and every guardian submission remain independent.

- [ ] **Step 4: Add survey E2E requirements**

Extend the future survey E2E list with student/guardian prefill, sequential versions, typed 409 recovery, immutable respondent snapshots and current-profile synchronization.

- [ ] **Step 5: Update stage-2 migration and journey boundary**

Change the final chain to `0013 -> 0014 -> 0015 -> 0016`; add food preference statistics/export and question-to-profile synchronization to the cross-module journey.

- [ ] **Step 6: Update README completion boundaries**

State local food preferences, `/me` onboarding route and homepage待办 are complete only after this plan's gates pass. Public self-registration/payment remains a future flow and must navigate successful registrations to the frozen `/onboarding/food-preferences` path; camp surveys and Docker/NAS also remain unfinished.

- [ ] **Step 7: Scan for stale migration references**

```bash
if rg -n "0015_camp_evaluation_surveys|0013.*0014.*0015(?!.*0016)" \
  docs/superpowers/plans/2026-08-09-camp-evaluation-surveys.md \
  docs/superpowers/plans/2026-08-09-stage2-local-acceptance.md README.md --pcre2; then
  echo "stale camp-survey migration reference found" >&2
  exit 1
fi
```

Expected: command exits 0 with no stale match.

- [ ] **Step 8: Run repository document/code checks**

```bash
git diff --check
./scripts/check.sh
```

Expected: both commands exit 0.

- [ ] **Step 9: Commit the downstream handoff**

```bash
git add docs/superpowers/plans/2026-08-09-camp-evaluation-surveys.md \
  docs/superpowers/plans/2026-08-09-stage2-local-acceptance.md README.md
git commit -m "docs: hand food preferences to camp surveys"
```

Expected: the `rg` command returns no stale migration references; `scripts/check.sh` and all previously focused gates PASS.

## Final completion gate

Before claiming this plan implemented, run from the worktree with the isolated PostgreSQL URL:

```bash
PYTHONPATH="$PWD/apps/api" TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -p no:cacheprovider apps/api/tests -q
npm --prefix apps/web test -- --run
npm --prefix apps/web run lint
npm --prefix apps/web run build
TEST_DATABASE_URL="$TEST_DATABASE_URL" ./scripts/run-food-preferences-e2e.sh
git diff --check
git status --short
```

Completion means the independent food preference module, `/me` onboarding route, pure-guardian/multi-identity eligibility, skippable homepage待办, profile entries, reporting/export and local four-viewport gate pass. It does **not** mean public self-registration/payment navigation, camp surveys, Docker/NAS, production migration, GPU/video, payment-platform integration or public deployment are complete.
