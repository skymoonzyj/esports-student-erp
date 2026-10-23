# Admissions Enrollment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付阶段 1B 的第二个独立模块，使管理员能够安全导入或手工登记付款、处理待分配、确认和调整正式报名，并完成退款与退营退费。

**Architecture:** 新建独立的 `app.admissions` 业务包，以 PostgreSQL 约束、稳定锁顺序和事务服务共同保护付款、退款、待分配、容量与报名历史；外部付款使用独立机器凭证、HMAC 签名、防重放和双幂等，不复用人员会话。React 在现有“招生教学”工作区增加付款、待分配和正式报名页面，桌面使用紧凑列表与详情面板，手机使用卡片、筛选菜单和全屏操作表单。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy 2、Alembic、PostgreSQL 16、cryptography、pytest、React 19、TypeScript、TanStack Query、Vitest、Playwright、lucide-react、Docker Compose

---

## Scope guardrails

- 本计划只实现已批准规格的“招生报名”模块：机器付款接口、手工付款、退款、冲突、待分配、正式报名、转班、取消、退营退费及管理员界面。
- 本计划不实现教练下一期名单、家庭教学页面、付款人自助查看和客服名单界面；这些属于阶段 1B 的第三个模块。
- 本计划不创建每日/每周评价、截图、OCR、游戏 ID、住宿、访客、实名导出、自助注册、支付跳转、录像或 GPU 分析。
- 阶段 1B 只接受人民币。所有金额进入领域层前转换为整数分；不得使用二进制浮点数保存或计算金额。
- 付款、退款和报名状态分别保存。已付款不等于已报名；普通退款不自动取消报名。
- 待分配单不保存临时班级选择。确认报名请求一次提交目标班级、最终约定学费及必要的调整原因，事务成功后才保存快照。
- `X-Service-Account` 的值是凭证公开 ID，而不是人员账号或数据库 UUID。轮换会生成新的公开 ID 和密钥；密钥只在创建响应中显示一次。
- 所有浏览器写操作继续使用现有 Cookie 会话和 CSRF。`POST /api/v1/payment-imports` 只接受机器 HMAC，不接受浏览器会话。
- 所有列表稳定分页。手机号筛选只使用 `LookupDigest` 精确匹配，禁止解密全表扫描。
- 所有关键写操作必须同时具备数据库约束、行锁/稳定锁顺序、权限、稳定错误码、领域事件和安全审计。
- 新页面必须通过 360、768、1024 和 1440 四档验收，核心流程不得依赖页面级横向滚动。

## Technical decisions frozen by this plan

1. HMAC 规范串为 `METHOD + "\n" + PATH + "\n" + UNIX_TIMESTAMP + "\n" + NONCE + "\n" + SHA256(raw_body)`，签名使用小写十六进制 HMAC-SHA256。
2. 时间戳使用十进制 Unix 秒，允许与服务器 UTC 时间相差最多 300 秒。
3. 有效签名通过后立即单独提交 nonce；即使后续业务校验失败，同一 nonce 也不能重放。nonce 摘要保留 24 小时。
4. 每个凭证固定限流为每分钟 120 次；限流计数独立提交，不因后续业务事务回滚而消失。
5. API 付款导入总是创建一张待分配单。付款人本人是学员时自动匹配该学员并进入 `ready`；付款人是监护人、未知账号或不能唯一决定时保持 `pending_match`。
6. 管理员直接选择学员登记手工付款时创建新的 `ready` 待分配单；追加分期付款时必须明确选择现有待分配单，避免猜测归集对象。
7. 取消报名时清空待分配单的学费和当前报名引用并回到 `ready`，原付款、退款、报名和事件历史全部保留。
8. 转班只允许同营期，且不修改已确认的约定学费快照；跨营期必须取消后重新确认报名。
9. 退营确认请求携带预览快照和退款拆分。服务端在锁内重新计算并逐字段比较，不使用客户端金额。
10. 所有跨模块事务共用锁顺序：营期 UUID -> 班级 UUID（排序）-> 报名 UUID（排序）-> 待分配单 UUID -> 人员 UUID（排序）-> 付款 UUID（排序）。只使用后半段资源的操作可以从对应位置开始，但不得反向获取前面的锁。
11. 默认 `ends_at` 可能是结束日次日 `00:00`。退费结束自然日使用上海时间；本地时间恰为 `00:00` 时先减一微秒再取日期，其他时间直接取本地日期。
12. 取消或退营后保持已确认的 `student_person_id` 并把待分配单设为 `ready`，即使全额退款后净付款为零；再次确认仍必须返回 `admission_payment_required`，直到追加新的净付款。
13. HMAC 对原始请求字节签名；双幂等的 `payload_digest` 对 Pydantic 校验后的业务对象做排序键、无多余空白的 canonical JSON，因此仅 JSON 键顺序或空白变化不构成冲突。
14. 相同付款业务内容的幂等重试必须使用新的时间戳和 nonce。重复同一请求头 nonce 总是先返回 `payment_replay_rejected`，不会进入业务幂等判断。
15. 机器导入的安全审计使用 `actor_user_id=None`，以 `service_account_id`、`credential_id`、导入事件和付款 UUID 标识来源；不得把公开凭证 ID、nonce、签名或密钥写入审计元数据。
16. `ambiguous_payer` 仅用于迁移数据造成多个账号候选的防御性路径。监护人绑定多个学员不是付款人冲突，只创建 `pending_match` 待分配单。
17. 每个机器账号固定一个 `source_system`；导入体的 `source_system` 必须与凭证所属账号一致，否则返回 `payment_source_not_allowed`，防止一个凭证冒充其他来源。

The implementation must keep these names and shapes consistent across services, routers and Web types:

```python
RefundPolicy = Literal["prorated_remaining_days", "full_remaining_paid"]
AdmissionCaseStatus = Literal["pending_match", "ready", "enrolled", "closed"]
EnrollmentStatus = Literal["active", "cancelled", "completed"]

class ConfirmEnrollment(BaseModel):
    class_id: UUID
    agreed_tuition_cents: int | None = Field(default=None, ge=0)
    tuition_override_reason: str | None = Field(default=None, max_length=500)
    capacity_override_reason: str | None = Field(default=None, max_length=500)

class TransferEnrollment(BaseModel):
    target_class_id: UUID
    reason: str = Field(min_length=1, max_length=500)
    capacity_override_reason: str | None = Field(default=None, max_length=500)

class CancelEnrollment(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

class WithdrawalPreviewRequest(BaseModel):
    withdrawal_date: date
    policy: RefundPolicy

class RefundAllocationSchema(BaseModel):
    payment_id: UUID
    amount_cents: int = Field(gt=0)

class WithdrawalConfirmRequest(BaseModel):
    withdrawal_date: date
    policy: RefundPolicy
    total_days: int = Field(gt=0)
    remaining_days: int = Field(ge=0)
    agreed_tuition_cents: int = Field(ge=0)
    available_paid_cents: int = Field(ge=0)
    refund_cents: int = Field(gt=0)
    allocations: list[RefundAllocationSchema] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)
```

`ConfirmEnrollment.agreed_tuition_cents=None` means “copy the target class default.” A non-default value requires `tuition_override_reason`. `capacity_override_reason` is ignored unless the class is full and the actor has `enrollments.capacity_override`; a supplied reason must never grant permission by itself.

## File map

### Create

- `apps/api/migrations/versions/0005_admissions_enrollment.py` - 招生、付款、退款、幂等、nonce、报名与退营表及权限种子。
- `apps/api/app/admissions/__init__.py` - 招生包标记。
- `apps/api/app/admissions/models.py` - 机器凭证、付款、冲突、待分配、报名、事件和退营 ORM 模型。
- `apps/api/app/admissions/schemas.py` - 输入、分页、脱敏响应、预览快照和机器接口契约。
- `apps/api/app/admissions/errors.py` - 稳定招生业务错误。
- `apps/api/app/admissions/money.py` - 元/分转换、付款汇总、按期退费和退款拆分纯函数。
- `apps/api/app/admissions/signing.py` - HMAC 规范串、常量时间校验、时间窗和摘要函数。
- `apps/api/app/admissions/credential_service.py` - 机器账号、凭证轮换、限流和 nonce 服务。
- `apps/api/app/admissions/payment_service.py` - 导入、冲突、手工付款、退款和付款查询。
- `apps/api/app/admissions/case_service.py` - 待分配匹配、关闭、重新开放和确认报名。
- `apps/api/app/admissions/enrollment_service.py` - 转班、取消、报名事件和营期结营联动。
- `apps/api/app/admissions/withdrawal_service.py` - 退营退款预览、快照复核和原子确认。
- `apps/api/app/admissions/access.py` - 管理员/客服读取范围和金额可见性值对象。
- `apps/api/app/admissions/dependencies.py` - 从应用设置构造加密、摘要和机器鉴权依赖。
- `apps/api/app/admissions/payment_router.py` - 机器导入、凭证、付款、退款和冲突 API。
- `apps/api/app/admissions/case_router.py` - 待分配匹配、关闭、重开和确认 API。
- `apps/api/app/admissions/enrollment_router.py` - 报名列表、转班、取消和退营 API。
- `apps/api/app/admissions/router.py` - 招生路由聚合和私有缓存头。
- `apps/api/tests/unit/admissions/test_money.py` - 金额解析、汇总、退费和拆分矩阵。
- `apps/api/tests/unit/admissions/test_signing.py` - 规范串、签名、时间窗和常量格式测试。
- `apps/api/tests/services/test_admissions_credentials.py` - 凭证轮换、限流、nonce 和停用行为。
- `apps/api/tests/services/test_payment_imports.py` - 双幂等、手机号匹配、冲突和加密负载。
- `apps/api/tests/services/test_payments.py` - 手工付款、分期归集、退款上限和金额汇总。
- `apps/api/tests/services/test_admission_cases.py` - 匹配、监护就绪、容量和确认事务。
- `apps/api/tests/services/test_enrollments.py` - 转班、取消、结营和历史事件。
- `apps/api/tests/services/test_withdrawals.py` - 预览失效、分期退款和原子退营。
- `apps/api/tests/api/test_admissions.py` - 浏览器招生 API、权限、脱敏、错误和审计契约。
- `apps/api/tests/api/test_payment_imports.py` - 机器签名、过期、重放、幂等和冲突契约。
- `apps/web/src/teaching/TeachingSectionNav.tsx` - 招生教学二级导航。
- `apps/web/src/admissions/api.ts` - 招生 API 类型、请求和分页参数。
- `apps/web/src/admissions/money.ts` - 整数分的中文金额展示。
- `apps/web/src/admissions/PaymentsPage.tsx` - 付款、退款、冲突和接口凭证页面。
- `apps/web/src/admissions/AdmissionCasesPage.tsx` - 待分配队列、学员匹配和确认报名页面。
- `apps/web/src/admissions/EnrollmentsPage.tsx` - 正式报名列表和筛选。
- `apps/web/src/admissions/EnrollmentDetailPage.tsx` - 转班、取消和退营退费详情页。
- `apps/web/src/admissions/CohortStudentsPanel.tsx` - 营期详情中的学员页签。
- `apps/web/src/admissions/admissions.css` - 桌面、平板和手机招生布局。
- `apps/web/src/admissions/api.test.ts` - 请求路径和金额契约测试。
- `apps/web/src/admissions/money.test.ts` - 金额展示测试。
- `apps/web/src/admissions/PaymentsPage.test.tsx` - 付款、退款、冲突和凭证页面测试。
- `apps/web/src/admissions/AdmissionCasesPage.test.tsx` - 匹配和确认流程测试。
- `apps/web/src/admissions/EnrollmentsPage.test.tsx` - 报名列表和容量状态测试。
- `apps/web/src/admissions/EnrollmentDetailPage.test.tsx` - 转班、取消和退营二次确认测试。
- `apps/web/tests/admissions-enrollment.spec.ts` - 四视口管理员招生完整旅程。

### Modify

- `apps/api/migrations/env.py` - 注册招生模型元数据。
- `apps/api/app/api/router.py` - 注册招生聚合路由。
- `apps/api/app/core/config.py` - 增加付款时间窗、nonce 保留和机器限流配置。
- `apps/api/app/rbac/seed.py` - 增加招生权限及管理员/客服默认授权。
- `apps/api/app/rbac/service.py` - 增加报名对象的 `global|assigned_class|family|self` 资源 token，为下一模块复用。
- `apps/api/app/audit/service.py` - 只放行招生 UUID、状态、金额状态和退款政策等非敏感元数据。
- `apps/api/app/teaching/cohort_service.py` - 营期完成时调用报名完成联动；停用班级前检查有效报名。
- `apps/api/app/teaching/errors.py` - 增加 `class_has_active_enrollments`。
- `apps/api/tests/db/test_migrations.py`、`test_rbac_seed.py` - `0005` 约束、升级和种子测试。
- `apps/api/tests/services/test_cohorts.py`、`test_teaching_scheduler.py` - 结营和班级停用联动测试。
- `apps/api/tests/api/conftest.py`、`apps/api/app/cli/seed_e2e.py` - 招生 E2E 管理员、学生、监护关系和班级数据。
- `apps/web/src/app/router.tsx`、`router.test.tsx` - 付款、待分配和报名路由守卫。
- `apps/web/src/layout/AppShell.tsx`、`AppShell.test.tsx` - 招生教学区域可达性，不增加第五个手机底部主项。
- `apps/web/src/teaching/TeachingCatalogPage.tsx`、`CohortsPage.tsx`、`CohortDetailPage.tsx` - 共用二级导航和学员页签。
- `apps/web/src/teaching/teaching.css` - 二级导航和页签衔接样式。
- `apps/web/tests/helpers.ts` - 招生种子账号登录与无横向溢出辅助断言。
- `scripts/db-schema-manifest.sh`、`scripts/check.sh`、`scripts/restore-db.sh` - `0005` 表清单和完整验收链。
- `README.md` - 当前可用招生能力、机器接口示例和仍延期边界。

## Test environment

优先使用隔离 PostgreSQL 测试库：

```bash
export TEST_DATABASE_URL='postgresql+psycopg://erp@127.0.0.1:55433/teaching_test'
```

浏览器 E2E 使用独立 `erp_e2e` 数据库和现有四视口配置。Docker daemon 不可用时可以执行本地 PostgreSQL、Web 和静态检查，但不得声称 Compose 容器链已通过。

### Task 1: Add the admissions schema and RBAC foundation

**Files:**

- Create: `apps/api/migrations/versions/0005_admissions_enrollment.py`
- Create: `apps/api/app/admissions/__init__.py`
- Create: `apps/api/app/admissions/models.py`
- Create: `apps/api/app/admissions/access.py`
- Modify: `apps/api/migrations/env.py`
- Modify: `apps/api/app/rbac/seed.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/tests/db/test_migrations.py`
- Modify: `apps/api/tests/db/test_rbac_seed.py`
- Modify: `scripts/db-schema-manifest.sh`

- [ ] **Step 1: Write failing migration and permission tests**

Add the expected admissions tables and assert check, unique, partial unique, composite foreign-key and exclusion behavior:

```python
ADMISSIONS_TABLES = {
    "service_accounts",
    "service_account_credentials",
    "service_account_request_limits",
    "service_account_nonces",
    "payments",
    "payment_import_events",
    "payment_conflicts",
    "payment_refunds",
    "admission_cases",
    "admission_case_payments",
    "enrollments",
    "enrollment_events",
    "enrollment_withdrawals",
}

def test_admissions_schema_protects_money_idempotency_and_active_enrollment(
    postgres_engine: Engine,
) -> None:
    inspector = inspect(postgres_engine)
    assert ADMISSIONS_TABLES <= set(inspector.get_table_names())
    payment_checks = {row["name"] for row in inspector.get_check_constraints("payments")}
    refund_checks = {
        row["name"] for row in inspector.get_check_constraints("payment_refunds")
    }
    assert "ck_payments_positive_cny" in payment_checks
    assert "ck_payment_refunds_positive" in refund_checks
    with postgres_engine.connect() as connection:
        indexes = set(connection.scalars(text(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
        )))
    assert "uq_payment_import_source_order" in indexes
    assert "uq_payment_import_idempotency" in indexes
    assert "uq_enrollments_active_student_cohort" in indexes
```

Add seed assertions for:

```python
ADMISSIONS_PERMISSIONS = {
    "admission_cases.read",
    "admission_cases.manage",
    "payments.read",
    "payments.manage",
    "refunds.manage",
    "enrollments.read",
    "enrollments.manage",
    "enrollments.capacity_override",
    "payment_imports.manage",
}
```

- [ ] **Step 2: Run the migration tests and verify they fail**

Run:

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py apps/api/tests/db/test_rbac_seed.py -q
```

Expected: FAIL because revision `0005_admissions_enrollment`, admissions tables and permissions do not exist.

- [ ] **Step 3: Implement ORM models, migration and permission seeds**

Use UUID primary keys, integer cents, timezone-aware timestamps and these database-level invariants:

```python
class Enrollment(TimestampMixin, Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'cancelled', 'completed')",
            name="ck_enrollments_status",
        ),
        CheckConstraint(
            "agreed_tuition_cents >= 0",
            name="ck_enrollments_tuition",
        ),
        ForeignKeyConstraint(
            ["class_id", "cohort_id"],
            ["teaching_classes.id", "teaching_classes.cohort_id"],
            name="fk_enrollments_class_cohort",
            ondelete="RESTRICT",
        ),
    )
```

Create `uq_enrollments_active_student_cohort` as a PostgreSQL partial unique index with `WHERE status = 'active'`. Make `admission_case_payments.payment_id` unique, enforce `CNY`, positive payments/refunds, one withdrawal per enrollment, credential/public-ID uniqueness, and `(credential_id, nonce_digest)` uniqueness. Use `use_alter=True` for the import-event/conflict circular references so Alembic can create and drop them deterministically.

Protect the admission-case/enrollment cycle with `UNIQUE(enrollments.id, enrollments.admission_case_id)` and a deferred composite foreign key from `(admission_cases.current_enrollment_id, admission_cases.id)` to `(enrollments.id, enrollments.admission_case_id)`. This guarantees that `current_enrollment_id` cannot point to another case's enrollment.

Grant all nine permissions to administrators. Grant only `admission_cases.read` and `enrollments.read` to customer service; amount fields remain protected by `payments.read`. Do not grant coach, guardian or student permissions in this module.

- [ ] **Step 4: Run migration, model and seed tests**

Run the Step 2 command again.

Expected: PASS, including empty-database upgrade and `0004_teaching_configuration -> 0005_admissions_enrollment` upgrade.

- [ ] **Step 5: Commit the schema foundation**

```bash
git add apps/api/migrations apps/api/app/admissions apps/api/app/rbac \
  apps/api/tests/db scripts/db-schema-manifest.sh
git commit -m "feat: add admissions enrollment schema"
```

### Task 2: Implement integer money and withdrawal calculations

**Files:**

- Create: `apps/api/app/admissions/money.py`
- Create: `apps/api/tests/unit/admissions/test_money.py`

- [ ] **Step 1: Write the failing money matrix**

```python
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.01", 1), ("12800.00", 1_280_000), ("36800.05", 3_680_005)],
)
def test_parse_cny_uses_integer_cents(raw: str, expected: int) -> None:
    assert parse_cny(raw) == expected

def test_prorated_refund_uses_remaining_natural_days_and_half_up() -> None:
    preview = calculate_withdrawal(
        starts_on=date(2026, 8, 1),
        ends_on=date(2026, 8, 10),
        withdrawal_on=date(2026, 8, 6),
        agreed_tuition_cents=10_001,
        available_paid_cents=20_000,
        policy="prorated_remaining_days",
    )
    assert preview.total_days == 10
    assert preview.remaining_days == 4
    assert preview.refund_cents == 4_000

def test_refund_split_uses_most_recent_payment_first() -> None:
    assert split_refund(
        12_000,
        [
            RefundablePayment(UUID(int=1), datetime(2026, 1, 1, tzinfo=UTC), 10_000),
            RefundablePayment(UUID(int=2), datetime(2026, 2, 1, tzinfo=UTC), 5_000),
        ],
    ) == (
        RefundAllocation(UUID(int=2), 5_000),
        RefundAllocation(UUID(int=1), 7_000),
    )

def test_default_midnight_end_maps_to_previous_participation_date() -> None:
    assert cohort_participation_dates(
        datetime(2026, 8, 1, 0, 0, tzinfo=BUSINESS_TIME_ZONE),
        datetime(2026, 8, 8, 0, 0, tzinfo=BUSINESS_TIME_ZONE),
    ) == (date(2026, 8, 1), date(2026, 8, 7))
```

Also cover malformed decimals, more than two decimal places, non-CNY/negative values, overpayment, zero remaining days, pre-start withdrawal, full remaining paid and refund caps.

- [ ] **Step 2: Run tests and verify the module is missing**

```bash
.venv/bin/python -m pytest apps/api/tests/unit/admissions/test_money.py -q
```

Expected: FAIL with `ModuleNotFoundError: app.admissions.money` or missing functions.

- [ ] **Step 3: Implement the pure value objects and functions**

```python
@dataclass(frozen=True)
class RefundablePayment:
    payment_id: UUID
    paid_at: datetime
    refundable_cents: int

@dataclass(frozen=True)
class RefundAllocation:
    payment_id: UUID
    amount_cents: int

@dataclass(frozen=True)
class WithdrawalCalculation:
    total_days: int
    remaining_days: int
    agreed_tuition_cents: int
    available_paid_cents: int
    refund_cents: int

def parse_cny(value: str) -> int:
    decimal = Decimal(value)
    if not decimal.is_finite() or decimal <= 0 or decimal.as_tuple().exponent < -2:
        raise InvalidMoney("CNY amount must be positive with at most two decimals")
    return int((decimal * 100).to_integral_exact())

def calculate_withdrawal(
    *,
    starts_on: date,
    ends_on: date,
    withdrawal_on: date,
    agreed_tuition_cents: int,
    available_paid_cents: int,
    policy: RefundPolicy,
) -> WithdrawalCalculation:
    total_days = (ends_on - starts_on).days + 1
    remaining_days = min(max((ends_on - withdrawal_on).days, 0), total_days)
    theoretical = (
        Decimal(agreed_tuition_cents) * remaining_days / total_days
    ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    refund = available_paid_cents if policy == "full_remaining_paid" else min(
        int(theoretical), available_paid_cents
    )
    return WithdrawalCalculation(
        total_days=total_days,
        remaining_days=remaining_days,
        agreed_tuition_cents=agreed_tuition_cents,
        available_paid_cents=available_paid_cents,
        refund_cents=refund,
    )
```

`split_refund` must sort by `(paid_at DESC, id DESC)`, never allocate more than each payment's refundable balance, and raise if the requested total cannot be allocated exactly.

- [ ] **Step 4: Run the money tests**

Run the Step 2 command.

Expected: PASS.

- [ ] **Step 5: Commit the money domain**

```bash
git add apps/api/app/admissions/money.py apps/api/tests/unit/admissions
git commit -m "feat: calculate admissions money and refunds"
```

### Task 3: Add machine credentials, HMAC verification, replay protection and throttling

**Files:**

- Create: `apps/api/app/admissions/signing.py`
- Create: `apps/api/app/admissions/credential_service.py`
- Create: `apps/api/app/admissions/dependencies.py`
- Create: `apps/api/app/admissions/errors.py`
- Create: `apps/api/tests/unit/admissions/test_signing.py`
- Create: `apps/api/tests/services/test_admissions_credentials.py`
- Modify: `apps/api/app/core/config.py`

- [ ] **Step 1: Write failing signing and credential lifecycle tests**

```python
def test_canonical_request_is_byte_stable() -> None:
    assert canonical_request(
        method="POST",
        path="/api/v1/payment-imports",
        timestamp="1785196800",
        nonce="n-123",
        body=b'{"amount":"100.00"}',
    ) == (
        b"POST\n/api/v1/payment-imports\n1785196800\nn-123\n"
        + hashlib.sha256(b'{"amount":"100.00"}').hexdigest().encode()
    )

def test_rotated_credential_secret_is_returned_once_and_stored_encrypted(
    db: Session, cipher: EnvelopeCipher
) -> None:
    created = CredentialService(db, cipher=cipher).create_credential(
        service_account_id=service_account.id,
        actor_user_id=admin.id,
    )
    assert created.secret
    row = db.get(ServiceAccountCredential, created.id)
    assert created.secret not in row.secret_ciphertext
```

Cover invalid format, wrong signature, expired/future timestamp, disabled account/credential, 121st request in one minute, valid nonce first use, and same nonce rejection after a later business failure.

- [ ] **Step 2: Run signing and credential tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/unit/admissions/test_signing.py \
  apps/api/tests/services/test_admissions_credentials.py -q
```

Expected: FAIL because the signing and credential services do not exist.

- [ ] **Step 3: Implement signing, encrypted credentials and request admission**

Define stable errors `payment_signature_invalid`, `payment_request_expired`, `payment_replay_rejected`, `payment_rate_limited`, `service_account_disabled` and `service_credential_disabled`.

```python
def verify_signature(*, secret: bytes, provided: str, canonical: bytes) -> bool:
    if not re.fullmatch(r"[0-9a-f]{64}", provided):
        return False
    expected = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)

@dataclass(frozen=True)
class VerifiedMachineRequest:
    service_account_id: UUID
    credential_id: UUID
    raw_body: bytes
```

`CredentialService.verify_request` must:

1. Resolve the active credential by public ID.
2. Commit a one-minute throttle counter before signature verification.
3. Validate timestamp and signature over exact raw bytes.
4. Insert and commit the nonce digest with a 24-hour expiry.
5. Return only internal IDs and raw bytes, never the secret.

Prune expired nonces in bounded batches. Add settings with exact defaults: `payment_signature_tolerance_seconds=300`, `payment_nonce_ttl_hours=24`, `payment_import_requests_per_minute=120`.

- [ ] **Step 4: Run signing, service, Ruff and mypy checks**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/unit/admissions/test_signing.py \
  apps/api/tests/services/test_admissions_credentials.py -q
.venv/bin/ruff check apps/api/app/admissions apps/api/tests/unit/admissions \
  apps/api/tests/services/test_admissions_credentials.py
.venv/bin/mypy apps/api/app
```

Expected: all commands PASS.

- [ ] **Step 5: Commit machine authentication**

```bash
git add apps/api/app/admissions apps/api/app/core/config.py \
  apps/api/tests/unit/admissions apps/api/tests/services/test_admissions_credentials.py
git commit -m "feat: secure payment import credentials"
```

### Task 4: Implement payment imports, payer matching and conflict preservation

**Files:**

- Create: `apps/api/app/admissions/schemas.py`
- Create: `apps/api/app/admissions/payment_service.py`
- Create: `apps/api/tests/services/test_payment_imports.py`
- Create: `apps/api/tests/api/test_payment_imports.py`
- Modify: `apps/api/app/admissions/dependencies.py`
- Modify: `apps/api/app/admissions/errors.py`

- [ ] **Step 1: Write failing double-idempotency and matching tests**

```python
def test_identical_import_replay_returns_original_payment(db: Session) -> None:
    first = service.import_payment(input, machine=machine, raw_body=body)
    second = service.import_payment(input, machine=machine, raw_body=body)
    assert first.created is True
    assert second.created is False
    assert second.payment.id == first.payment.id
    assert db.scalar(select(func.count()).select_from(Payment)) == 1

def test_same_order_with_different_payload_creates_conflict_not_payment(db: Session) -> None:
    service.import_payment(original, machine=machine, raw_body=original_body)
    with pytest.raises(PaymentImportConflict):
        service.import_payment(changed, machine=machine, raw_body=changed_body)
    assert db.scalar(select(func.count()).select_from(Payment)) == 1
    conflict = db.scalar(select(PaymentConflict))
    assert conflict.status == "open"
    assert changed.payer_phone not in conflict.attempted_raw_payload_ciphertext
```

Cover unique student payer -> `ready`, guardian payer -> `pending_match`, unknown payer -> `pending_match`, no incomplete person/account creation, two independent idempotency keys, response phone redaction, CNY-only and payload encryption.

- [ ] **Step 2: Run import tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_payment_imports.py \
  apps/api/tests/api/test_payment_imports.py -q
```

Expected: FAIL because import schemas, service and endpoint are missing.

- [ ] **Step 3: Implement schemas and the import transaction**

```python
class PaymentImportRequest(BaseModel):
    source_system: str = Field(min_length=1, max_length=80)
    external_order_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str = Field(min_length=1, max_length=120)
    payer_phone: str
    amount: str
    currency: Literal["CNY"]
    paid_at: AwareDatetime
    raw_payload: dict[str, JsonValue]

class PaymentImportResponse(BaseModel):
    import_event_id: UUID
    payment_id: UUID
    admission_case_id: UUID
    processing_status: Literal["processed", "pending_match"]
    created: bool
```

Normalize and digest the phone before opening the business transaction. Inside `write_transaction`, rely on unique constraints and catch `IntegrityError`; after a collision lock and compare the existing payload digest. Exact duplicates return the existing result, while changed payloads append an encrypted conflict without changing the original event or payment. Store the conflict ID, leave `write_transaction` normally so the row commits, and only then raise `PaymentImportConflict`; never raise the HTTP-mapped exception while the conflict transaction is still open.

Never include `payer_phone`, `raw_payload`, ciphertext, credential secrets or signatures in responses, logs, errors or audit metadata.

- [ ] **Step 4: Run service and API tests**

Run the Step 2 command.

Expected: PASS with first import `201`, business-idempotent retry using a fresh nonce `200`, payload conflict `409 payment_import_conflict`, bad signature `401 payment_signature_invalid`, expired request `401 payment_request_expired`, reused nonce `409 payment_replay_rejected`, and throttle overflow `429 payment_rate_limited`.

- [ ] **Step 5: Commit payment imports**

```bash
git add apps/api/app/admissions apps/api/tests/services/test_payment_imports.py \
  apps/api/tests/api/test_payment_imports.py
git commit -m "feat: import payments with double idempotency"
```

### Task 5: Add manual payments, installment aggregation and bounded refunds

**Files:**

- Create: `apps/api/tests/services/test_payments.py`
- Modify: `apps/api/app/admissions/schemas.py`
- Modify: `apps/api/app/admissions/payment_service.py`
- Modify: `apps/api/app/admissions/errors.py`

- [ ] **Step 1: Write failing manual-payment and refund tests**

```python
def test_manual_payment_for_student_creates_ready_case(db: Session) -> None:
    result = service.create_manual_payment(
        ManualPaymentCreate(
            student_person_id=student.id,
            admission_case_id=None,
            amount="5000.00",
            paid_at=NOW,
            note="线下转账",
        ),
        actor_user_id=admin.id,
        request_id="manual-1",
    )
    assert result.case.status == "ready"
    assert result.payment.amount_cents == 500_000

def test_refund_cannot_exceed_original_payment(
    db: Session,
    payment: Payment,
    admin: UserAccount,
) -> None:
    service.create_refund(
        payment.id,
        RefundCreate(amount="75.00", refunded_at=NOW, reason="首次退款"),
        actor_user_id=admin.id,
        request_id="refund-1",
    )
    with pytest.raises(RefundExceedsPaidAmount):
        service.create_refund(
            payment.id,
            RefundCreate(amount="25.01", refunded_at=NOW, reason="超额退款"),
            actor_user_id=admin.id,
            request_id="refund-2",
        )
```

Cover exactly-one-of student/case selection, installment attachment to an enrolled case, no automatic enrollment cancellation, partial/full status, available refund, archived-cohort write blocking, stable pagination and totals `paid/refunded/net/due/overpaid`.

- [ ] **Step 2: Run payment tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_payments.py -q
```

Expected: FAIL on missing manual payment/refund methods.

- [ ] **Step 3: Implement manual payment, summaries and locked refunds**

```python
@dataclass(frozen=True)
class PaymentSummary:
    paid_cents: int
    refunded_cents: int
    net_paid_cents: int
    agreed_tuition_cents: int | None
    due_cents: int
    overpaid_cents: int
    status: Literal["unpaid", "partial", "paid", "overpaid", "refunded"]
```

Refund creation must select the payment `FOR UPDATE`, recompute cumulative refunds in the same transaction, reject totals above the original amount with `refund_exceeds_paid_amount`, and append security audit metadata containing IDs and refund type only. Amount values must not enter audit metadata.

- [ ] **Step 4: Run payment and regression tests**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_payments.py \
  apps/api/tests/services/test_payment_imports.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit payment and refund services**

```bash
git add apps/api/app/admissions apps/api/tests/services/test_payments.py
git commit -m "feat: manage admissions payments and refunds"
```

### Task 6: Match admission cases and confirm enrollment atomically

**Files:**

- Create: `apps/api/app/admissions/case_service.py`
- Create: `apps/api/tests/services/test_admission_cases.py`
- Modify: `apps/api/app/admissions/schemas.py`
- Modify: `apps/api/app/admissions/errors.py`

- [ ] **Step 1: Write failing matching, readiness and capacity tests**

```python
def test_pending_guardian_payment_can_match_student_but_minor_blocks_confirmation(
    db: Session,
) -> None:
    matched = service.match_student(case.id, student.id, actor_user_id=admin.id)
    assert matched.status == "ready"
    with pytest.raises(MinorGuardianRequired):
        service.confirm_enrollment(
            case.id,
            ConfirmEnrollment(class_id=class_.id),
            actor_user_id=admin.id,
            request_id="confirm-minor",
        )

def test_full_class_requires_independent_override_permission_and_reason(
    db: Session,
    ready_case: AdmissionCase,
    full_class: TeachingClass,
    admin: UserAccount,
) -> None:
    with pytest.raises(ClassCapacityReached):
        service.confirm_enrollment(
            ready_case.id,
            ConfirmEnrollment(
                class_id=full_class.id,
                capacity_override_reason=None,
            ),
            actor_user_id=admin.id,
            request_id="capacity-denied",
            capacity_override_allowed=False,
        )
    with pytest.raises(CapacityOverrideReasonRequired):
        service.confirm_enrollment(
            ready_case.id,
            ConfirmEnrollment(
                class_id=full_class.id,
                capacity_override_reason=None,
            ),
            actor_user_id=admin.id,
            request_id="capacity-reason-missing",
            capacity_override_allowed=True,
        )
    enrollment = service.confirm_enrollment(
        ready_case.id,
        ConfirmEnrollment(
            class_id=full_class.id,
            capacity_override_reason="校长审批",
        ),
        actor_user_id=admin.id,
        request_id="capacity-approved",
        capacity_override_allowed=True,
    )
    assert enrollment.capacity_override_reason == "校长审批"
```

Cover non-student target, deleted person, zero net payment, non-`open` cohort, inactive class, class/cohort mismatch, duplicate active enrollment, default tuition snapshot, changed tuition with mandatory reason, and concurrent final seat claims.

- [ ] **Step 2: Run admission case tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_admission_cases.py -q
```

Expected: FAIL because `AdmissionCaseService` does not exist.

- [ ] **Step 3: Implement stable lock order and readiness reuse**

Read identifiers without a lock, then acquire the global order: cohort, target class, existing enrollments ordered by UUID, admission case, then the student through `GuardianReadinessPolicy.lock_students`. Re-read and revalidate every row after locking. Compute the business reference date with `business_date_at(cohort.starts_at)` and call:

```python
projection = GuardianReadinessPolicy(db).evaluate_one(
    student_person_id,
    business_date_at(cohort.starts_at),
)
if projection.age_status == "minor" and projection.guardian_status != "ready":
    raise MinorGuardianRequired("student needs a valid guardian at cohort start")
```

The confirmation transaction must set case tuition fields, create the active enrollment, set `current_enrollment_id`, move the case to `enrolled`, append `confirmed` and optional `capacity_overridden` events, and append security audit before commit.

Map service failures to these exact stable codes: `admission_payment_required`, `minor_guardian_required`, `duplicate_cohort_enrollment`, `class_capacity_reached`, `capacity_override_reason_required`, `tuition_override_reason_required`, `cohort_not_open`, `class_inactive`, and `class_cohort_mismatch`. Permission failures remain `permission_denied` and must not be replaced by the presence of an override reason.

- [ ] **Step 4: Run admission, concurrency and readiness regressions**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_admission_cases.py \
  apps/api/tests/services/test_guardians.py \
  apps/api/tests/services/test_readiness_policy.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit admission confirmation**

```bash
git add apps/api/app/admissions apps/api/tests/services/test_admission_cases.py
git commit -m "feat: confirm admissions enrollment atomically"
```

### Task 7: Implement transfer, cancellation and cohort-completion lifecycle

**Files:**

- Create: `apps/api/app/admissions/enrollment_service.py`
- Create: `apps/api/tests/services/test_enrollments.py`
- Modify: `apps/api/app/teaching/cohort_service.py`
- Modify: `apps/api/app/teaching/errors.py`
- Modify: `apps/api/tests/services/test_cohorts.py`
- Modify: `apps/api/tests/services/test_teaching_scheduler.py`

- [ ] **Step 1: Write failing lifecycle tests**

```python
def test_transfer_locks_classes_in_uuid_order_and_preserves_tuition(db: Session) -> None:
    before = enrollment.agreed_tuition_cents
    moved = service.transfer(
        enrollment.id,
        TransferEnrollment(target_class_id=target.id, reason="调整训练方向"),
        actor_user_id=admin.id,
        request_id="transfer-1",
    )
    assert moved.class_id == target.id
    assert moved.agreed_tuition_cents == before
    assert latest_event.type == "transferred"

def test_cancel_releases_capacity_and_reopens_case_without_refund(db: Session) -> None:
    cancelled = service.cancel(
        enrollment.id,
        CancelEnrollment(reason="学员改期"),
        actor_user_id=admin.id,
        request_id="cancel-1",
    )
    assert cancelled.status == "cancelled"
    assert case.status == "ready"
    assert case.current_enrollment_id is None
    assert case.agreed_tuition_cents is None
    assert db.scalar(select(func.count()).select_from(PaymentRefund)) == 0
```

Cover cross-cohort transfer rejection, inactive/full target, capacity override, archived read-only, original/new class event snapshots, active enrollment blocking class deactivation, and manual/scheduler cohort completion changing each active enrollment exactly once.

- [ ] **Step 2: Run lifecycle tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_enrollments.py \
  apps/api/tests/services/test_cohorts.py \
  apps/api/tests/services/test_teaching_scheduler.py -q
```

Expected: FAIL on missing enrollment lifecycle and current teaching behavior.

- [ ] **Step 3: Implement transfer/cancel and explicit teaching integration**

Expose one narrow cross-module function:

```python
def complete_active_enrollments_for_cohort(
    db: Session,
    *,
    cohort_id: UUID,
    occurred_at: datetime,
    actor_user_id: UUID | None,
    request_id: str,
) -> int:
    enrollment_ids = tuple(db.scalars(
        select(Enrollment.id)
        .where(Enrollment.cohort_id == cohort_id, Enrollment.status == "active")
        .order_by(Enrollment.id)
        .with_for_update()
    ))
    for enrollment_id in enrollment_ids:
        enrollment = db.get(Enrollment, enrollment_id)
        if enrollment is None or enrollment.status != "active":
            continue
        enrollment.status = "completed"
        enrollment.completed_at = occurred_at
        db.add(EnrollmentEvent(
            enrollment_id=enrollment.id,
            event_type="completed",
            old_status="active",
            new_status="completed",
            actor_user_id=actor_user_id,
            request_id=request_id,
            occurred_at=occurred_at,
        ))
    db.flush()
    return len(enrollment_ids)
```

Call it only after a legal transition to `completed`, inside the same transaction as the cohort event. The teaching transition already owns the cohort lock; the lifecycle function must next lock all cohort classes by UUID and then active enrollments by UUID. In class updates, acquire the cohort lock before the class lock, query active enrollments before changing `is_active` to false and raise `ClassHasActiveEnrollments` with stable code `class_has_active_enrollments`.

- [ ] **Step 4: Run lifecycle, teaching and scheduler tests**

Run the Step 2 command.

Expected: PASS and repeated scheduler execution produces no duplicate completion events.

- [ ] **Step 5: Commit enrollment lifecycle**

```bash
git add apps/api/app/admissions apps/api/app/teaching \
  apps/api/tests/services/test_enrollments.py \
  apps/api/tests/services/test_cohorts.py \
  apps/api/tests/services/test_teaching_scheduler.py
git commit -m "feat: manage enrollment lifecycle"
```

### Task 8: Add withdrawal refund preview and atomic confirmation

**Files:**

- Create: `apps/api/app/admissions/withdrawal_service.py`
- Create: `apps/api/tests/services/test_withdrawals.py`
- Modify: `apps/api/app/admissions/schemas.py`
- Modify: `apps/api/app/admissions/errors.py`

- [ ] **Step 1: Write failing preview and stale-confirmation tests**

```python
def test_withdrawal_preview_returns_snapshot_and_recent_first_split(db: Session) -> None:
    preview = service.preview(
        enrollment.id,
        WithdrawalPreviewRequest(
            withdrawal_date=date(2026, 8, 6),
            policy="prorated_remaining_days",
        ),
    )
    assert preview.total_days == 10
    assert preview.remaining_days == 4
    assert [item.payment_id for item in preview.allocations] == [newer.id, older.id]

def test_changed_payment_after_preview_rejects_confirmation(db: Session) -> None:
    preview = service.preview(
        enrollment.id,
        WithdrawalPreviewRequest(
            withdrawal_date=date(2026, 8, 6),
            policy="prorated_remaining_days",
        ),
    )
    payment_service.create_refund(
        newer.id,
        RefundCreate(amount="1.00", refunded_at=NOW, reason="预览后退款"),
        actor_user_id=admin.id,
        request_id="refund-after-preview",
    )
    with pytest.raises(WithdrawalRefundChanged):
        service.confirm(
            enrollment.id,
            WithdrawalConfirmRequest(
                withdrawal_date=preview.withdrawal_date,
                policy=preview.policy,
                total_days=preview.total_days,
                remaining_days=preview.remaining_days,
                agreed_tuition_cents=preview.agreed_tuition_cents,
                available_paid_cents=preview.available_paid_cents,
                refund_cents=preview.refund_cents,
                allocations=preview.allocations,
                reason="申请退营",
            ),
            actor_user_id=admin.id,
            request_id="withdrawal-confirm-stale",
        )
    assert enrollment.status == "active"
```

Cover pre-start full proportional refund, end-day zero, full remaining paid independent of date, nothing left to refund, split ties by UUID, exact snapshot equality, second confirmation rejection and rollback after an injected refund insert failure.

- [ ] **Step 2: Run withdrawal tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_withdrawals.py -q
```

Expected: FAIL because `WithdrawalService` is missing.

- [ ] **Step 3: Implement preview and confirmation with identical calculators**

Both paths must call the same `calculate_withdrawal` and `split_refund` functions. Convert cohort timestamps with a shared `cohort_participation_dates` helper: start date is `starts_at` in Shanghai; an `ends_at` exactly at local `00:00` maps to the preceding date, otherwise it maps to its local date. Confirm locks in the global order: cohort, class, enrollment, admission case, then payments sorted by UUID. Compare policy, withdrawal date, total/remaining days, tuition, available paid, total refund and every allocation before writing.

On success create one `EnrollmentWithdrawal`, one or more `PaymentRefund` rows with the correct withdrawal type, cancel the enrollment, reopen the case, append `withdrawal_refunded`, and audit in the same transaction. Map mismatches to `withdrawal_refund_changed` and zero refundable amounts to `withdrawal_nothing_to_refund`.

- [ ] **Step 4: Run withdrawal and money regressions**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/services/test_withdrawals.py \
  apps/api/tests/unit/admissions/test_money.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit withdrawal refunds**

```bash
git add apps/api/app/admissions apps/api/tests/services/test_withdrawals.py
git commit -m "feat: confirm withdrawal refunds atomically"
```

### Task 9: Expose private admissions APIs, field-level access and audit contracts

**Files:**

- Create: `apps/api/app/admissions/payment_router.py`
- Create: `apps/api/app/admissions/case_router.py`
- Create: `apps/api/app/admissions/enrollment_router.py`
- Create: `apps/api/app/admissions/router.py`
- Create: `apps/api/tests/api/test_admissions.py`
- Modify: `apps/api/app/api/router.py`
- Modify: `apps/api/app/audit/service.py`
- Modify: `apps/api/app/rbac/service.py`
- Modify: `apps/api/tests/api/test_audit.py`
- Modify: `apps/api/tests/api/test_permissions.py`

- [ ] **Step 1: Write failing API, permission and privacy tests**

```python
def test_customer_can_read_case_status_but_never_amounts(
    customer_client: TestClient,
) -> None:
    response = customer_client.get("/api/v1/admission-cases")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["payment_status"] == "partial"
    assert item["paid_cents"] is None
    assert item["agreed_tuition_cents"] is None

def test_admin_with_explicit_payments_deny_cannot_reveal_amounts(admin_client) -> None:
    response = admin_client.get("/api/v1/payments")
    assert response.status_code == 403

def test_all_admissions_responses_are_private_no_store(admin_client) -> None:
    response = admin_client.get("/api/v1/enrollments")
    assert response.headers["cache-control"] == "no-store, private"
```

Cover all routes from the approved spec, pagination, exact phone digest filters, CSRF, object-not-found masking, stable Chinese problem responses, explicit deny priority, service credential secret shown only on creation, conflict resolution note requirement, audit success/failure, and metadata rejecting raw phone/amount/payload/secret values.

- [ ] **Step 2: Run API tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/api/test_admissions.py \
  apps/api/tests/api/test_payment_imports.py \
  apps/api/tests/api/test_audit.py \
  apps/api/tests/api/test_permissions.py -q
```

Expected: FAIL because admissions routes are not registered.

- [ ] **Step 3: Implement route groups and response projection**

Register exact browser routes:

```text
GET/POST /api/v1/service-accounts
POST     /api/v1/service-accounts/{id}/disable
POST     /api/v1/service-accounts/{id}/credentials
POST     /api/v1/service-account-credentials/{id}/disable
POST     /api/v1/payment-imports
GET      /api/v1/payment-conflicts
POST     /api/v1/payment-conflicts/{id}/resolve
GET/POST /api/v1/payments
POST     /api/v1/payments/{id}/refunds
GET      /api/v1/admission-cases
POST     /api/v1/admission-cases/{id}/match-student
POST     /api/v1/admission-cases/{id}/close
POST     /api/v1/admission-cases/{id}/reopen
POST     /api/v1/admission-cases/{id}/confirm-enrollment
GET      /api/v1/enrollments
GET      /api/v1/enrollments/{id}
POST     /api/v1/enrollments/{id}/transfer
POST     /api/v1/enrollments/{id}/cancel
POST     /api/v1/enrollments/{id}/withdrawal-refund-preview
POST     /api/v1/enrollments/{id}/withdrawal-refund-confirm
```

`POST /payment-conflicts/{id}/resolve` accepts `action: "resolved" | "dismissed"` and a required non-empty note. It may attach the correct payer/student relationship, but it must never alter the original amount, timestamps, digests or encrypted payloads.

Use `AdmissionsVisibility(can_read, can_manage, can_read_amounts)` from server-side RBAC and construct response schemas with nullable amount fields. Never fetch full response then strip in the browser.

- [ ] **Step 4: Run API and all backend static checks**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/api/test_admissions.py \
  apps/api/tests/api/test_payment_imports.py \
  apps/api/tests/api/test_audit.py \
  apps/api/tests/api/test_permissions.py -q
.venv/bin/ruff check apps/api/app apps/api/tests
.venv/bin/mypy apps/api/app
```

Expected: all commands PASS.

- [ ] **Step 5: Commit admissions APIs**

```bash
git add apps/api/app/api/router.py apps/api/app/admissions apps/api/app/audit \
  apps/api/app/rbac apps/api/tests/api
git commit -m "feat: expose admissions management APIs"
```

### Task 10: Add the Web admissions contract, routes and stable section navigation

**Files:**

- Create: `apps/web/src/admissions/api.ts`
- Create: `apps/web/src/admissions/money.ts`
- Create: `apps/web/src/admissions/api.test.ts`
- Create: `apps/web/src/admissions/money.test.ts`
- Create: `apps/web/src/teaching/TeachingSectionNav.tsx`
- Modify: `apps/web/src/app/router.tsx`
- Modify: `apps/web/src/app/router.test.tsx`
- Modify: `apps/web/src/layout/AppShell.tsx`
- Modify: `apps/web/src/layout/AppShell.test.tsx`
- Modify: `apps/web/src/teaching/TeachingCatalogPage.tsx`
- Modify: `apps/web/src/teaching/CohortsPage.tsx`
- Modify: `apps/web/src/teaching/CohortDetailPage.tsx`

- [ ] **Step 1: Write failing request, money and route tests**

```typescript
it("formats integer cents without floating point drift", () => {
  expect(formatCny(3680005)).toBe("¥36,800.05")
})

it("posts the exact withdrawal preview contract", async () => {
  await previewWithdrawal("enrollment-1", {
    withdrawal_date: "2026-08-06",
    policy: "prorated_remaining_days",
  })
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/enrollments/enrollment-1/withdrawal-refund-preview",
    expect.objectContaining({ method: "POST" }),
  )
})
```

Add route tests proving administrators can reach `/teaching/payments`, `/teaching/admission-cases`, `/teaching/enrollments` and `/teaching/enrollments/:id`, while absent/explicitly denied permissions redirect to `/`. Verify the mobile bottom navigation remains four stable items and admissions pages are reached through the teaching section navigation.

- [ ] **Step 2: Run Web tests and verify failure**

```bash
npm --prefix apps/web test -- \
  src/admissions/api.test.ts src/admissions/money.test.ts \
  src/app/router.test.tsx src/layout/AppShell.test.tsx
```

Expected: FAIL because admissions contracts and routes do not exist.

- [ ] **Step 3: Implement typed API functions and route guards**

Use integer cents throughout TypeScript types. Define `Page<T>`, payment/case/enrollment status unions, nullable amount projections, preview allocation types and `ProblemDetailsError` handling. Add a shared section navigation with permission-filtered links: 营期、项目与队伍、付款与退款、待分配、正式报名.

Route components must read both global and scoped permissions; management pages require the corresponding global permission, while list pages can render server-projected read-only data.

- [ ] **Step 4: Run Web contract, router, build and lint checks**

```bash
npm --prefix apps/web test -- \
  src/admissions/api.test.ts src/admissions/money.test.ts \
  src/app/router.test.tsx src/layout/AppShell.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: all commands PASS.

- [ ] **Step 5: Commit Web admissions foundation**

```bash
git add apps/web/src/admissions apps/web/src/teaching/TeachingSectionNav.tsx \
  apps/web/src/app apps/web/src/layout apps/web/src/teaching
git commit -m "feat: add admissions web foundation"
```

### Task 11: Build payment, refund, conflict and service-credential management UI

**Files:**

- Create: `apps/web/src/admissions/PaymentsPage.tsx`
- Create: `apps/web/src/admissions/PaymentsPage.test.tsx`
- Create: `apps/web/src/admissions/admissions.css`
- Modify: `apps/web/src/app/router.tsx`

- [ ] **Step 1: Write failing page behavior tests**

```typescript
it("creates a manual payment and never converts yuan with Number arithmetic", async () => {
  renderPage()
  await user.click(screen.getByRole("button", { name: "登记付款" }))
  await user.type(screen.getByLabelText("付款金额（元）"), "36800.05")
  await user.type(screen.getByLabelText("备注"), "线下转账")
  await user.click(screen.getByRole("button", { name: "确认登记" }))
  expect(createManualPaymentMock).toHaveBeenCalledWith(
    expect.objectContaining({ amount: "36800.05" }),
  )
})

it("shows a new credential secret once with a copy action and warning", async () => {
  renderPaymentsPage()
  await user.click(screen.getByRole("tab", { name: "接口凭证" }))
  await user.click(screen.getByRole("button", { name: "新建凭证" }))
  await user.type(screen.getByLabelText("凭证名称"), "付款系统测试凭证")
  await user.click(screen.getByRole("button", { name: "确认创建" }))
  expect(screen.getByText("关闭后无法再次查看此密钥")).toBeVisible()
  expect(screen.getByRole("button", { name: "复制密钥" })).toBeVisible()
})
```

Cover payment filters, amount-hidden read-only mode, remaining refundable amount, refund validation, conflict resolution note, mutation lock, inline errors, disabled credential state and mobile card labels.

- [ ] **Step 2: Run the payment page tests and verify failure**

```bash
npm --prefix apps/web test -- src/admissions/PaymentsPage.test.tsx
```

Expected: FAIL because the page does not exist.

- [ ] **Step 3: Implement a three-tab operational page**

Build tabs `付款与退款`, `导入冲突`, `接口凭证`. Keep filters in an unframed full-width band; use tables at 1024/1440 and labeled cards at 360/768. Use Lucide icons for add, refund, copy, rotate, disable and filter actions with tooltips. Refund opens a focused modal from a specific payment and always displays original, refunded and remaining amounts. A newly created HMAC secret may exist only in the open modal's component state; never put it in TanStack Query cache, browser storage, URL, logs or test snapshots, and clear it when the modal closes.

- [ ] **Step 4: Run page tests, build and lint**

```bash
npm --prefix apps/web test -- src/admissions/PaymentsPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: PASS with no TypeScript or ESLint errors.

- [ ] **Step 5: Commit the payment workspace**

```bash
git add apps/web/src/admissions apps/web/src/app/router.tsx
git commit -m "feat: manage admissions payments in web"
```

### Task 12: Build pending allocation, enrollment and withdrawal UI

**Files:**

- Create: `apps/web/src/admissions/AdmissionCasesPage.tsx`
- Create: `apps/web/src/admissions/AdmissionCasesPage.test.tsx`
- Create: `apps/web/src/admissions/EnrollmentsPage.tsx`
- Create: `apps/web/src/admissions/EnrollmentsPage.test.tsx`
- Create: `apps/web/src/admissions/EnrollmentDetailPage.tsx`
- Create: `apps/web/src/admissions/EnrollmentDetailPage.test.tsx`
- Create: `apps/web/src/admissions/CohortStudentsPanel.tsx`
- Modify: `apps/web/src/admissions/admissions.css`
- Modify: `apps/web/src/teaching/CohortDetailPage.tsx`
- Modify: `apps/web/src/teaching/CohortDetailPage.test.tsx`

- [ ] **Step 1: Write failing allocation and lifecycle page tests**

```typescript
it("matches a guardian payment to one selected student before enrollment", async () => {
  renderCases()
  await user.click(screen.getByRole("button", { name: "匹配学员" }))
  await user.type(screen.getByLabelText("手机号或学员编号"), "S20260001")
  await user.click(screen.getByRole("radio", { name: "张同学" }))
  await user.click(screen.getByRole("button", { name: "确认匹配" }))
  expect(matchStudentMock).toHaveBeenCalledWith("case-1", "student-1")
})

it("requires refund preview and a second confirmation before withdrawal", async () => {
  renderEnrollmentDetail()
  await user.click(screen.getByRole("button", { name: "退营" }))
  await user.click(screen.getByLabelText("按剩余日期退款"))
  await user.click(screen.getByRole("button", { name: "计算退款" }))
  expect(await screen.findByText("退款计算预览")).toBeVisible()
  expect(confirmWithdrawalMock).not.toHaveBeenCalled()
  await user.type(screen.getByLabelText("退营原因"), "家庭安排变化")
  await user.click(screen.getByRole("button", { name: "二次确认退营并退款" }))
  expect(confirmWithdrawalMock).toHaveBeenCalledTimes(1)
})
```

Cover pending/ready/enrolled filters, student result pagination, default tuition, override reason, minor guardian error, full-class override permission, transfer within cohort, no-refund cancellation, both refund policies, stale-preview refresh, amount-hidden mode, archived read-only and cohort student tab cache refresh.

- [ ] **Step 2: Run allocation and enrollment UI tests and verify failure**

```bash
npm --prefix apps/web test -- \
  src/admissions/AdmissionCasesPage.test.tsx \
  src/admissions/EnrollmentsPage.test.tsx \
  src/admissions/EnrollmentDetailPage.test.tsx \
  src/teaching/CohortDetailPage.test.tsx
```

Expected: FAIL because the pages and cohort student panel are missing.

- [ ] **Step 3: Implement the complete administrator journey**

The case page uses a staged drawer/full-screen mobile flow: match student -> select active `open` cohort class -> show default tuition -> optional override reason -> capacity/readiness preview -> confirm. The enrollment detail uses distinct actions for 转班, 取消不退款 and 退营; refund policies never share the one-click cancel path.

The withdrawal preview is read-only and must display total days, remaining days, agreed tuition, available paid, calculated refund and each original-payment allocation before enabling the second confirmation button. A `withdrawal_refund_changed` response closes confirmation, refetches data and requires a new preview.

- [ ] **Step 4: Run all admissions Web tests, build and lint**

```bash
npm --prefix apps/web test -- src/admissions src/teaching/CohortDetailPage.test.tsx
npm --prefix apps/web run build
npm --prefix apps/web run lint
```

Expected: PASS.

- [ ] **Step 5: Commit allocation and enrollment UI**

```bash
git add apps/web/src/admissions apps/web/src/teaching/CohortDetailPage.tsx \
  apps/web/src/teaching/CohortDetailPage.test.tsx
git commit -m "feat: manage allocation and enrollment in web"
```

### Task 13: Seed E2E data and verify four responsive administrator journeys

**Files:**

- Create: `apps/web/tests/admissions-enrollment.spec.ts`
- Modify: `apps/api/app/cli/seed_e2e.py`
- Modify: `apps/api/tests/cli/test_seed_e2e.py`
- Modify: `apps/web/tests/helpers.ts`
- Modify: `apps/web/playwright.config.ts`

- [ ] **Step 1: Write the failing E2E journey and seed assertions**

For each existing project viewport, test this sequence:

```typescript
test("admin records payment, enrolls, transfers and withdraws with preview", async ({ page }) => {
  await loginAsAdmin(page)
  await page.goto("/teaching/payments")
  await createManualPayment(page, { amount: "5000.00", student: seed.studentName })
  await page.goto("/teaching/admission-cases")
  await confirmEnrollment(page, { className: seed.standardClass, tuition: "12800.00" })
  await expect(page.getByText("欠款 ¥7,800.00")).toBeVisible()
  await transferEnrollment(page, seed.eliteClass)
  await previewAndConfirmWithdrawal(page, "prorated_remaining_days")
  await expectNoHorizontalOverflow(page)
})
```

Add API setup or a second journey for exact machine replay, changed-payload conflict and unresolved guardian payer matching. Seed one ready adult student, one pending minor, one guardian linked to multiple students, an open cohort with spare/full classes and a stable service credential used only by E2E.

- [ ] **Step 2: Run seed and E2E tests and verify failure**

```bash
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/cli/test_seed_e2e.py -q
E2E_BASE_URL='http://127.0.0.1:18080' npm --prefix apps/web run test:e2e -- \
  admissions-enrollment.spec.ts
```

Expected: FAIL until the seed and journeys are implemented in the E2E stack.

- [ ] **Step 3: Implement deterministic admissions seeds and journeys**

Use fixed UUIDs under a new documented prefix and upserts so repeated seeding returns the same accounts, classes, service account and credential public ID. The secret may be fixed only when `APP_ENV=test` and the database name is exactly `erp_e2e`; production and development must reject this seed path.

Each viewport must use unique payment order/idempotency values and cleanly assert desktop table versus mobile card behavior without positional selectors.

- [ ] **Step 4: Run all four Playwright projects**

```bash
E2E_BASE_URL='http://127.0.0.1:18080' npm --prefix apps/web run test:e2e -- \
  admissions-enrollment.spec.ts
```

Expected: 4/4 viewport journeys PASS with no horizontal overflow.

- [ ] **Step 5: Commit E2E coverage**

```bash
git add apps/api/app/cli/seed_e2e.py apps/api/tests/cli/test_seed_e2e.py \
  apps/web/tests apps/web/playwright.config.ts
git commit -m "test: verify admissions enrollment journeys"
```

### Task 14: Update operations documentation, rehearse restore and run the module gate

**Files:**

- Modify: `README.md`
- Modify: `scripts/check.sh`
- Modify: `scripts/restore-db.sh`
- Modify: `compose.e2e.yaml`
- Modify: `docs/superpowers/specs/2026-07-27-admissions-teaching-operations-design.md`

- [ ] **Step 1: Add failing manifest and documentation assertions**

Extend script/CLI tests to require Alembic head `0005_admissions_enrollment`, all 33 platform/teaching/admissions tables from the schema manifest, and README sections for machine signing, manual payment, allocation, refunds, rollback and current stage boundaries.

- [ ] **Step 2: Run the focused checks and verify missing documentation/manifest data**

```bash
bash -n scripts/db-schema-manifest.sh scripts/backup-db.sh scripts/restore-db.sh scripts/check.sh
TEST_DATABASE_URL="$TEST_DATABASE_URL" .venv/bin/python -m pytest \
  apps/api/tests/db/test_migrations.py apps/api/tests/cli -q
```

Expected: FAIL until the new head/table manifest and docs are updated.

- [ ] **Step 3: Document exact operator flows and restore expectations**

README must include:

```text
- Create/rotate/disable a machine credential; secret appears once.
- Construct and sign an import request without logging phone, payload or secret.
- Register a manual payment and attach installments to an explicit case.
- Match a payer to a student, confirm a class and interpret due/overpaid status.
- Refund a payment without cancelling enrollment.
- Transfer, cancel without refund, preview prorated/full withdrawal, then confirm twice.
- Back up before migration and roll back application code without downgrading committed money data.
```

Set the design document status to `阶段 1B 第二模块已实现` only after the full gate succeeds. Keep coach/family, evaluation, accommodation and payment-platform adapters listed as unavailable.

- [ ] **Step 4: Run the complete module gate**

```bash
PLAYWRIGHT_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' \
  bash scripts/check.sh
```

Expected:

- all backend tests PASS;
- Ruff and mypy PASS;
- all Web tests, build and ESLint PASS;
- empty and `0004 -> 0005` migrations PASS;
- machine signing, replay, conflict and admissions E2E PASS;
- 360, 768, 1024 and 1440 Playwright projects PASS;
- backup/restore produces the same migration head, explicit table set and row counts;
- development, E2E and production Compose configurations parse without plaintext secrets.

If Docker daemon is unavailable, run every local command separately and record the container/E2E/restore chain as unverified rather than passing.

- [ ] **Step 5: Review the diff and commit operations documentation**

```bash
git diff --check
git status --short
git add README.md scripts compose.e2e.yaml \
  docs/superpowers/specs/2026-07-27-admissions-teaching-operations-design.md
git commit -m "docs: document admissions enrollment operations"
```

## Final acceptance checklist

- [ ] Identical source-order and idempotency replays return the original payment; changed content never overwrites it.
- [ ] HMAC credentials are encrypted, rotate independently, show secrets once, enforce five-minute timestamps, 24-hour nonce uniqueness and per-minute limits.
- [ ] Full phone numbers, raw payloads, signatures, secrets and amount values never enter logs or audit metadata.
- [ ] Guardian/unknown payers remain pending until an administrator selects a student; no incomplete account is auto-created.
- [ ] Manual payment can create a new case or attach to one explicitly selected case; installment totals remain accurate.
- [ ] Partial payment can confirm enrollment and displays a debt; overpayment is separate from paid status.
- [ ] Enrollment confirmation reuses `GuardianReadinessPolicy` at cohort start, prevents duplicates and locks the final seat.
- [ ] Capacity override requires its independent permission and a non-empty reason.
- [ ] Transfer is same-cohort only, atomically moves capacity and preserves tuition/history.
- [ ] Manual refund never silently cancels enrollment and concurrent refunds cannot exceed the original payment.
- [ ] Cancellation reopens allocation without refund; withdrawal refunds always preview and require a second confirmation.
- [ ] Prorated refunds use Shanghai natural days, start remaining days after withdrawal date and cap at available paid.
- [ ] Multi-payment withdrawals refund newest payments first and reject stale previews without partial writes.
- [ ] Cohort completion completes active enrollments exactly once; archived admissions data is read-only.
- [ ] Customer-service API projections omit all amount fields unless `payments.read` is independently granted.
- [ ] Administrator Web flows work at 360, 768, 1024 and 1440 without page-level horizontal overflow.
- [ ] Evaluation, screenshot, game ID, accommodation, self-service payment and coach/family pages remain outside this module.
