# Direct Cuisine Entry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let every eligible food-preference editor add up to three directly typed cuisine names while preserving catalog shortcuts, history, statistics, and exports.

**Architecture:** Direct entries keep the protected system “其他” option ID and place the actual value in `custom_name`. PostgreSQL uses a partial unique index for standard choices, while service validation resolves catalog semantics before allowing repeated “其他” IDs. The shared React panel owns one cuisine input, so onboarding, profiles, guardian/admin editing, and survey drafts change together.

**Tech Stack:** PostgreSQL 16, Alembic, SQLAlchemy 2, Pydantic 2, FastAPI, React 19, TypeScript 6, TanStack Query, Vitest, Testing Library, pytest.

---

### Task 1: Database constraint and ORM contract

**Files:**
- Create: `apps/api/migrations/versions/0022_multiple_custom_cuisines.py`
- Modify: `apps/api/app/food_preferences/models.py:124-167`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_food_preference_models.py:165-188`

- [ ] **Step 1: Write the failing model test**

Add this test while retaining the existing test that two standard rows are rejected:

```python
def test_multiple_custom_cuisines_may_share_system_other_option(db: Session) -> None:
    person_id, account_id = _insert_person_and_account(db)
    _add_current_preference(db, person_id=person_id, account_id=account_id)
    db.add_all(
        [
            PersonFoodPreferenceCuisine(
                person_id=person_id,
                position=position,
                cuisine_option_id=FOOD_OTHER_CUISINE_ID,
                cuisine_name_snapshot="其他",
                custom_name=name,
                custom_normalized_name=name,
            )
            for position, name in enumerate(("川菜", "粤菜"), start=1)
        ]
    )
    db.flush()
    rows = tuple(
        db.scalars(
            select(PersonFoodPreferenceCuisine)
            .where(PersonFoodPreferenceCuisine.person_id == person_id)
            .order_by(PersonFoodPreferenceCuisine.position)
        )
    )
    assert [row.custom_name for row in rows] == ["川菜", "粤菜"]
```

- [ ] **Step 2: Write the failing migration contract**

Set `CURRENT_HEAD = "0022_multiple_custom_cuisines"`. Add a test that checks `pg_get_indexdef('uq_person_food_preference_cuisines_standard_option')` contains `custom_name IS NULL`. Insert two rows for one person with the system “其他” ID and distinct custom names, then assert downgrade to `0021_registration_review_candidate_binding` raises:

```python
with pytest.raises(
    RuntimeError,
    match="cannot downgrade multiple custom cuisines with repeated options",
):
    command.downgrade(config, "0021_registration_review_candidate_binding")
```

Clean inserted preference/account rows in `finally` and restore `head`.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
PYTHONPATH=apps/api TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -q -p no:cacheprovider \
  apps/api/tests/db/test_food_preference_models.py::test_multiple_custom_cuisines_may_share_system_other_option \
  apps/api/tests/db/test_migrations.py::test_0022_uses_partial_standard_cuisine_uniqueness_and_protects_downgrade
```

Expected: the model test fails on `uq_person_food_preference_cuisines_option`; the migration test fails because revision/index `0022` is absent.

- [ ] **Step 4: Implement revision 0022**

Create the migration with this contract:

```python
"""Allow multiple distinct custom cuisines.

Revision ID: 0022_multiple_custom_cuisines
Revises: 0021_registration_review_candidate_binding
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_multiple_custom_cuisines"
down_revision: str | Sequence[str] | None = (
    "0021_registration_review_candidate_binding"
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "person_food_preference_cuisines"
OLD_CONSTRAINT = "uq_person_food_preference_cuisines_option"
NEW_INDEX = "uq_person_food_preference_cuisines_standard_option"


def _lock_rows() -> None:
    op.get_bind().execute(
        sa.text(f"LOCK TABLE {TABLE} IN SHARE ROW EXCLUSIVE MODE")
    )


def _preflight_downgrade() -> None:
    _lock_rows()
    repeated = op.get_bind().scalar(
        sa.text(
            f"SELECT EXISTS (SELECT 1 FROM {TABLE} "
            "GROUP BY person_id, cuisine_option_id HAVING count(*) > 1)"
        )
    )
    if repeated:
        raise RuntimeError(
            "cannot downgrade multiple custom cuisines with repeated options"
        )


def upgrade() -> None:
    _lock_rows()
    op.drop_constraint(OLD_CONSTRAINT, TABLE, type_="unique")
    op.create_index(
        NEW_INDEX,
        TABLE,
        ["person_id", "cuisine_option_id"],
        unique=True,
        postgresql_where=sa.text("custom_name IS NULL"),
    )


def downgrade() -> None:
    _preflight_downgrade()
    op.drop_index(NEW_INDEX, table_name=TABLE)
    op.create_unique_constraint(
        OLD_CONSTRAINT,
        TABLE,
        ["person_id", "cuisine_option_id"],
    )
```

Replace the ORM `UniqueConstraint` on `(person_id, cuisine_option_id)` with the matching partial `Index`.

- [ ] **Step 5: Verify GREEN and commit**

Run the focused nodes, the full food model file, and the existing food migration round trip. Then commit only:

```bash
git add apps/api/migrations/versions/0022_multiple_custom_cuisines.py \
  apps/api/app/food_preferences/models.py \
  apps/api/tests/db/test_migrations.py \
  apps/api/tests/db/test_food_preference_models.py
git commit -m "feat: allow multiple custom cuisines"
```

### Task 2: Request and service validation

**Files:**
- Modify: `apps/api/app/food_preferences/schemas.py:74-98`
- Modify: `apps/api/app/food_preferences/preference_service.py:123-186,264-310`
- Modify: `apps/api/tests/unit/food_preferences/test_schemas.py`
- Modify: `apps/api/tests/services/test_food_preferences.py`

- [ ] **Step 1: Write failing schema and service tests**

Replace the schema test that rejects duplicate IDs with:

```python
def test_food_preference_write_request_defers_repeated_option_to_catalog() -> None:
    other_id = uuid4()
    payload = FoodPreferenceWriteRequest(
        expected_version=0,
        cuisines=[
            {"cuisine_option_id": other_id, "custom_name": "川菜"},
            {"cuisine_option_id": other_id, "custom_name": "粤菜"},
        ],
        dishes=[],
    )
    assert [item.custom_name for item in payload.cuisines] == ["川菜", "粤菜"]
```

Add a service test that saves three selections using `scenario.options["other"].id`, expects ordered custom names `川菜/粤菜/东北菜`, and checks the same three entries in `PersonFoodPreferenceVersion.cuisines`. Add rejection tests for two identical standard IDs and for a custom name matching a selected standard option name; both must leave `_counts(...) == (0, 0, 0, 0)`.

- [ ] **Step 2: Run focused tests and verify RED**

```bash
PYTHONPATH=apps/api TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -q -p no:cacheprovider \
  apps/api/tests/unit/food_preferences/test_schemas.py::test_food_preference_write_request_defers_repeated_option_to_catalog \
  apps/api/tests/services/test_food_preferences.py::test_three_distinct_custom_cuisines_share_other_option_and_keep_history
```

Expected: current Schema and Service reject the repeated option ID.

- [ ] **Step 3: Move option uniqueness into catalog-aware resolution**

Remove the generic cuisine-ID duplicate checks from the Pydantic validator and `_normalize_command`. In `_resolve_cuisines`, resolve each display name and apply:

```python
standard_option_ids: set[UUID] = set()
display_keys: set[str] = set()

if option.is_other:
    if selection.custom_name is None:
        raise self._validation_error("other cuisine requires a custom name")
    display_name = selection.custom_name
else:
    if option.id in standard_option_ids:
        raise self._validation_error("standard cuisine options must be unique")
    standard_option_ids.add(option.id)
    if selection.custom_name is not None:
        raise self._validation_error("custom name is only allowed for other cuisine")
    display_name = (
        existing.cuisine_name_snapshot
        if not option.is_active and existing is not None
        else option.name
    )

display_key = normalize_food_key(display_name)
if display_key in display_keys:
    raise self._validation_error("cuisine names must be unique")
display_keys.add(display_key)
```

Keep the existing custom-name and dish normalization checks. Build `_ResolvedCuisine.name` from the inactive snapshot or active option exactly as before.

- [ ] **Step 4: Run suites and commit**

```bash
PYTHONPATH=apps/api TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -q -p no:cacheprovider \
  apps/api/tests/unit/food_preferences/test_schemas.py \
  apps/api/tests/services/test_food_preferences.py
```

Expected: all selected tests pass with no PostgreSQL skips. Commit the four Task 2 files with `feat: validate multiple custom cuisine names`.

### Task 3: Shared React direct-entry control

**Files:**
- Modify: `apps/web/src/foodPreferences/FoodPreferencesPanel.tsx`
- Modify: `apps/web/src/foodPreferences/FoodPreferencesPanel.test.tsx`
- Modify: `apps/web/src/foodPreferences/food-preferences.css`

- [ ] **Step 1: Write failing UI tests**

Add `otherOnlyCatalog()` and a test that edits an empty profile, directly types `川菜`, clicks “添加菜系”, types `粤菜{Enter}` and `东北菜{Enter}`, then asserts:

```tsx
expect(screen.getByText("菜系 3/3")).toBeVisible()
expect(screen.getByRole("button", { name: "移除菜系 川菜" })).toBeVisible()
expect(screen.queryByText("其他：川菜")).not.toBeInTheDocument()
expect(screen.getByLabelText("菜系名称")).toBeDisabled()
```

Save and assert the API payload contains three entries with the same `cuisineIds.other` and three distinct `custom_name` values. Add duplicate tests for NFKC/casefold variants and for a typed name matching a selected standard option.

- [ ] **Step 2: Run focused UI tests and verify RED**

```bash
npm --prefix apps/web test -- src/foodPreferences/FoodPreferencesPanel.test.tsx \
  -t "directly adds three cuisine names|rejects duplicate directly entered cuisines|rejects a direct cuisine matching a standard option"
```

Expected: “菜系名称” is absent.

- [ ] **Step 3: Implement cuisine input behavior**

Add `cuisineInput`/`onCuisineInput` to `PreferenceEditorProps`, derive `otherOption = sortedOptions.find(item => item.is_other)`, and add:

```tsx
const selectedCuisineKey = (item: EditableCuisine): string =>
  normalizedText(item.isOther ? item.custom_name ?? "" : item.name)

const addCustomCuisine = (candidate: string) => {
  if (disabled || fullCuisines || !otherOption) return
  const displayName = cleanDisplayText(candidate)
  if (!displayName) return
  if (
    value.cuisines.some(
      (item) => selectedCuisineKey(item) === normalizedText(displayName),
    )
  ) {
    onSelectionError("不能添加重复菜系。")
    return
  }
  onSelectionError(null)
  onChange({
    ...value,
    cuisines: [
      ...value.cuisines,
      {
        cuisine_option_id: otherOption.id,
        custom_name: displayName,
        name: otherOption.name,
        isOther: true,
      },
    ],
  })
  onCuisineInput("")
}
```

Make `cuisineLabel` return the custom text alone. Filter `is_other` out of shortcut buttons. Remove the old per-row “其他菜系名称” fields and add an always-visible “菜系名称” input plus “添加菜系” button, with Enter handling and the shared 3-item limit.

- [ ] **Step 4: Reset transient input in every security/state boundary**

Add parent `cuisineInput` state. Clear it alongside `dishInput` in successful mutation, `clearTransient`, `startEdit`, and cancel edit. Pass it into `PreferenceEditor`. Do not persist un-added input in query cache, conflict drafts, or controlled survey drafts.

- [ ] **Step 5: Update responsive styling**

Replace the dish-specific layout class with `foodPreferencesEditor__textEntry` and use it for both entry rows. Preserve a one-column mobile layout and switch to `minmax(0, 1fr) auto` at 768 px; retain 44 px inputs/buttons.

- [ ] **Step 6: Verify and commit**

```bash
npm --prefix apps/web test -- src/foodPreferences/FoodPreferencesPanel.test.tsx
npm --prefix apps/web run build
```

Expected: component tests and production build pass. Commit the three Task 3 files with `feat: add direct cuisine entry`.

### Task 4: Reporting and export regression coverage

**Files:**
- Modify: `apps/api/tests/services/test_food_preference_reporting.py`
- Modify: `apps/api/tests/services/test_food_preference_exports.py`

- [ ] **Step 1: Add multi-custom regression data**

Create one person with two current cuisine rows sharing `FOOD_OTHER_CUISINE_ID` and custom names `川菜` and `粤菜`. In reporting, assert both labels count independently. In XLSX and CSV ZIP assertions, require `川菜、粤菜` and reject a collapsed “其他” label.

Use the reporting fixture's existing helper so history and current rows stay aligned:

```python
_add_current_preference(
    scenario,
    person_key="without_account",
    cuisines=(
        (scenario.other_option_id, "其他", "川菜", "川菜"),
        (scenario.other_option_id, "其他", "粤菜", "粤菜"),
    ),
    dishes=(("宫保鸡丁", "宫保鸡丁"),),
)
snapshot = scenario.service().build_snapshot(
    population="all_people",
    include_inactive=False,
)
counts = {row.label: row.count for row in snapshot.cuisines}
assert counts["川菜"] == 1
assert counts["粤菜"] == 1
```

For the export fixture, add the same two `PersonFoodPreferenceCuisine` rows to its student before rendering, then assert both decoded CSV and worksheet cuisine cells equal `川菜、粤菜`.

- [ ] **Step 2: Run tests**

```bash
PYTHONPATH=apps/api TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -q -p no:cacheprovider \
  apps/api/tests/services/test_food_preference_reporting.py \
  apps/api/tests/services/test_food_preference_exports.py
```

Expected: both suites pass using the existing custom-name-first projection. Limit any required production change to per-row label selection; do not group custom rows by option ID.

- [ ] **Step 3: Commit regression tests**

Commit only the two Task 4 test files with `test: cover custom cuisine reporting`.

### Task 5: Cross-layer verification and deployment readiness

**Files:**
- Verify all Task 1–4 paths; modify no unrelated files.

- [ ] **Step 1: Run API quality checks**

```bash
PYTHONPATH=apps/api ../../.venv/bin/python -m ruff check \
  apps/api/app/food_preferences \
  apps/api/migrations/versions/0022_multiple_custom_cuisines.py \
  apps/api/tests/unit/food_preferences/test_schemas.py \
  apps/api/tests/services/test_food_preferences.py \
  apps/api/tests/services/test_food_preference_reporting.py \
  apps/api/tests/services/test_food_preference_exports.py \
  apps/api/tests/db/test_food_preference_models.py
```

- [ ] **Step 2: Run all focused API tests with PostgreSQL**

```bash
PYTHONPATH=apps/api TEST_DATABASE_URL="$TEST_DATABASE_URL" \
  ../../.venv/bin/python -m pytest -q -p no:cacheprovider \
  apps/api/tests/unit/food_preferences \
  apps/api/tests/services/test_food_preferences.py \
  apps/api/tests/services/test_food_preference_reporting.py \
  apps/api/tests/services/test_food_preference_exports.py \
  apps/api/tests/api/test_food_preferences.py \
  apps/api/tests/db/test_food_preference_models.py \
  apps/api/tests/db/test_migrations.py
```

Expected: all selected tests pass and none skip for missing PostgreSQL.

- [ ] **Step 3: Run Web tests, lint, and build**

```bash
npm --prefix apps/web test -- src/foodPreferences
npm --prefix apps/web run lint -- src/foodPreferences
npm --prefix apps/web run build
```

- [ ] **Step 4: Verify repository hygiene**

```bash
git diff --check
git status --short
git log --oneline -8
```

Expected: no whitespace errors; unrelated pre-existing changes remain untouched; only direct-cuisine commits are new.

- [ ] **Step 5: Prepare deployment handoff**

Record migration head `0022_multiple_custom_cuisines`, source digest, focused test results, and changed paths. A later NAS rollout must back up PostgreSQL, run `alembic upgrade head`, reuse `esports-erp-staging_postgres_data`, and never run `docker compose down -v`.
