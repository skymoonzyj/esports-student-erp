"""Add the self-service enrollment catalog and selected-program snapshots."""

from collections.abc import Sequence
from uuid import UUID

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0045_enrollment_program_catalog"
down_revision: str | Sequence[str] | None = "0044_travel_report_conversions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMISSION_ID = "20000000-0000-4000-8000-000000000075"

WEEKEND_AUDIENCE = (
    "孩子正常学习但动力不足，把绝大部分业余时间花在游戏上，容易出现不当用语或情绪爆发，"
    "缺乏其他爱好及解压方式，家庭教育遇到瓶颈，亲子关系有变差趋势。"
)
WEEKEND_HIGHLIGHTS = [
    "由懂电竞的家庭教育老师梳理由游戏引发的家庭问题并提供解决建议",
    "帮助孩子建立正确的游戏观和健康的游戏相处方式",
    "采用专业、科学的电竞训练体系",
    "通过沟通、情绪管理和责任意识训练引导孩子建立正确价值观",
]
WEEKEND_SCHEDULE = [
    {
        "time": "13:00-14:00 或 18:00-19:00",
        "item": "家庭教育沟通",
        "note": "复盘上一周期表现并制定下一周期教育内容；首次参加需建立家庭档案。全天班家长任选一个时段沟通。",
    },
    {
        "time": "13:00-17:00 或 18:00-22:00",
        "item": "学员电竞训练",
        "note": "职业选手同款训练，覆盖基本功、游戏理解、团队协作、抗压、沟通、情绪管理与责任意识。",
    },
]

EXPERIENCE_AUDIENCE = (
    "孩子重度沉迷游戏，想放弃学业通过职业选手、主播、陪玩或代练赚钱，认为没有天赋也能"
    "单靠努力实现梦想；家长希望孩子短期体验职业电竞高强度训练，认清兴趣与职业之间的差距。"
)
SEVEN_DAY_HIGHLIGHTS = [
    "每天约 12 小时职业化训练体验",
    "包含机械基本功训练和高强度针对性训练",
    "模拟职业电竞训练压力，帮助学员认识爱好与职业的差异",
    "训练期间手机仅保留训练游戏、微信和 QQ",
]
SEVEN_DAY_SCHEDULE = [
    {"time": "11:00-11:30", "item": "起床", "note": "洗漱"},
    {"time": "11:30-12:30", "item": "午餐", "note": "原课程表备注为自行解决"},
    {
        "time": "12:30-13:00",
        "item": "职业病预防拉伸",
        "note": "针对颈椎、腰椎和腱鞘的恢复训练",
    },
    {"time": "13:00-17:30", "item": "职业选手生活体验", "note": "电竞职业选手同款训练"},
    {"time": "17:30-18:30", "item": "晚餐", "note": "原课程表备注为自行解决"},
    {"time": "18:30-19:00", "item": "亲子时间", "note": "与父母家人沟通"},
    {"time": "19:00-01:00", "item": "职业选手生活体验", "note": "电竞职业选手同款训练"},
    {"time": "01:00", "item": "回宿舍休息", "note": "洗漱、整理房间和衣物"},
]

LONG_TERM_AUDIENCE = (
    "已经休学在家休息，希望通过系统、科学训练全方位提升并接近职业电竞要求的学员；"
    "同时希望更清晰认识电竞行业，并通过与家长阶段性分开重塑亲子关系。"
)
LONG_TERM_HIGHLIGHTS = [
    "标准职业化训练体系，覆盖游戏水平、职业理解和训练赛体验",
    "集体生活、沟通教练、复盘总结与逆境抗压训练",
    "职业病预防、身体素质和有氧能力提升",
    "梳理家庭教育与亲子关系问题，并在训练期间给出改进方案",
]
LONG_TERM_SCHEDULE = [
    {"time": "08:00", "item": "起床", "note": "洗漱并下楼签到集合"},
    {"time": "08:00-08:30", "item": "职业病预防拉伸", "note": "颈椎、腰椎和腱鞘恢复训练"},
    {"time": "08:30-09:00", "item": "早餐", "note": "自理"},
    {"time": "09:00-11:30", "item": "职业选手生活体验", "note": "行业通识、大咖分享与基本功训练"},
    {"time": "11:30-12:30", "item": "午餐", "note": "自理"},
    {"time": "12:30-13:00", "item": "午休", "note": "午休"},
    {"time": "13:00-14:00", "item": "学习时间", "note": "假期作业或针对天赋测试弱项训练"},
    {"time": "14:00-17:00", "item": "职业选手生活体验", "note": "电竞职业选手同款训练"},
    {"time": "17:00-17:30", "item": "体能训练", "note": "提升有氧、心肺和身体素质"},
    {"time": "17:30-18:30", "item": "晚餐", "note": "自理"},
    {
        "time": "18:30-19:00",
        "item": "亲子或休息时间",
        "note": "与家长沟通；关系紧张时可每 2 至 3 天沟通一次",
    },
    {"time": "19:00-22:00", "item": "职业选手生活体验", "note": "电竞职业选手同款训练"},
    {"time": "22:00-22:30", "item": "回宿舍洗漱", "note": "整理个人卫生和床铺"},
    {"time": "23:00", "item": "收手机就寝", "note": "电子产品统一交给教练"},
]

SUMMER_STANDARD_HIGHLIGHTS = [
    "标准职业化训练体系，系统提升游戏操作水平",
    "训练赛实战、赛后复盘和教练一对一指导",
    "科学作息、职业病预防、身体素质和有氧训练",
    "逆境抗压、团队协作、集体生活和职业选手素养培养",
    "帮助家长剖析家庭教育及亲子关系问题，并根据训练观察给出改进方案",
]

PROGRAM_ROWS = [
    {
        "id": UUID("45000000-0000-4000-8000-000000000001"),
        "code": "weekend-half-day",
        "name": "周末半天班",
        "category": "weekend",
        "duration_label": "周六或周日半天",
        "price_cents": 80_000,
        "price_note": "800 元/半天",
        "inclusion_note": "不含餐食与住宿",
        "summary": "家庭教育沟通与职业化电竞训练相结合的周末半天课程。",
        "target_audience": WEEKEND_AUDIENCE,
        "highlights": WEEKEND_HIGHLIGHTS,
        "schedule": WEEKEND_SCHEDULE,
        "eligibility_note": None,
        "is_active": True,
        "sort_order": 10,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000002"),
        "code": "weekend-full-day",
        "name": "周末全天班",
        "category": "weekend",
        "duration_label": "周六或周日全天",
        "price_cents": 150_000,
        "price_note": "1,500 元/全天",
        "inclusion_note": "不含餐食与住宿",
        "summary": "覆盖下午与晚间训练的周末全天课程，家长任选一个家庭教育沟通时段。",
        "target_audience": WEEKEND_AUDIENCE,
        "highlights": WEEKEND_HIGHLIGHTS,
        "schedule": WEEKEND_SCHEDULE,
        "eligibility_note": None,
        "is_active": True,
        "sort_order": 20,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000003"),
        "code": "seven-day-experience",
        "name": "七日体验营",
        "category": "experience",
        "duration_label": "7 天",
        "price_cents": 699_900,
        "price_note": "6,999 元/期",
        "inclusion_note": "文档标注包吃包住；每日课程表又将午餐、晚餐标为自行解决，报名时需由工作人员确认餐食安排",
        "summary": "通过连续七天高强度职业电竞训练，让学员直观认识兴趣与职业训练的差距。",
        "target_audience": EXPERIENCE_AUDIENCE,
        "highlights": SEVEN_DAY_HIGHLIGHTS,
        "schedule": SEVEN_DAY_SCHEDULE,
        "eligibility_note": None,
        "is_active": True,
        "sort_order": 30,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000004"),
        "code": "monthly-experience",
        "name": "月度体验营",
        "category": "experience",
        "duration_label": "1 个月",
        "price_cents": 1_288_800,
        "price_note": "12,888 元/月",
        "inclusion_note": "含住宿，不含餐食",
        "summary": "采用标准职业化训练体系，系统提升竞技、体能、抗压、集体生活与职业认知。",
        "target_audience": LONG_TERM_AUDIENCE,
        "highlights": LONG_TERM_HIGHLIGHTS,
        "schedule": LONG_TERM_SCHEDULE,
        "eligibility_note": None,
        "is_active": True,
        "sort_order": 40,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000005"),
        "code": "quarterly-experience",
        "name": "季度体验营",
        "category": "experience",
        "duration_label": "3 个月",
        "price_cents": 3_288_800,
        "price_note": "32,888 元/季度",
        "inclusion_note": "含住宿，不含餐食",
        "summary": "以季度为周期执行标准职业化训练与生活体系，持续观察并改善学习、训练和亲子关系。",
        "target_audience": LONG_TERM_AUDIENCE,
        "highlights": LONG_TERM_HIGHLIGHTS,
        "schedule": LONG_TERM_SCHEDULE,
        "eligibility_note": None,
        "is_active": True,
        "sort_order": 50,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000006"),
        "code": "family-education-monthly",
        "name": "家庭教育及亲子关系修复课程",
        "category": "family",
        "duration_label": "1 个月",
        "price_cents": 300_000,
        "price_note": "3,000 元/月",
        "inclusion_note": "每周主动沟通 3 次，家长与孩子的问题沟通不限次数",
        "summary": "系统梳理成长轨迹与亲子互动方式，围绕家庭教育、青春期和内驱力提供持续指导。",
        "target_audience": "已报名任一课程或参加过天赋测试，明确感受到家庭教育及亲子关系问题，希望缩短试错成本，并通过电竞游戏帮助孩子建立正确价值观的家庭。",
        "highlights": [
            "梳理孩子从小到大的成长轨迹和心路历程",
            "了解各成长阶段家长的陪伴及教育方式",
            "结合孩子现状提出家庭教育和亲子关系改进意见",
            "帮助孩子平衡度过青春期迷茫阶段，明确人生目标并激发内驱力",
        ],
        "schedule": [
            {"time": "每周 3 次", "item": "主动沟通", "note": "老师主动与家长及孩子沟通"},
            {"time": "服务期内", "item": "问题答疑", "note": "家长及孩子的问题沟通不限次数"},
        ],
        "eligibility_note": "需已报名任一课程或参加过天赋测试，并认可课程的电竞教育理念。",
        "is_active": True,
        "sort_order": 60,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000007"),
        "code": "summer-standard-21-day",
        "name": "普通暑期班",
        "category": "summer",
        "duration_label": "21 天",
        "price_cents": 1_188_800,
        "price_note": "11,888 元/期",
        "inclusion_note": "含餐食与住宿",
        "summary": "面向希望体验职业选手训练模式的学员，完整覆盖竞技、健康、心理素养和家庭教育模块。",
        "target_audience": "对电竞感兴趣、希望体验职业选手训练模式的学员，无特殊门槛。",
        "highlights": SUMMER_STANDARD_HIGHLIGHTS,
        "schedule": [],
        "eligibility_note": "无特殊门槛",
        "is_active": True,
        "sort_order": 70,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000008"),
        "code": "summer-career-sprint-21-day",
        "name": "职业班冲刺班",
        "category": "summer",
        "duration_label": "21 天",
        "price_cents": 1_388_800,
        "price_note": "13,888 元/期",
        "inclusion_note": "含餐食与住宿",
        "summary": "为具备青训潜力的学员模拟真实青训及职业战队训练计划。",
        "target_audience": "具备冲击青训潜力的学员。",
        "highlights": [
            "模拟真实青训及职业战队训练计划",
            "围绕职业路径开展高强度、针对性训练",
            "由教练结合天赋测试和评测结果确认入学资格",
        ],
        "schedule": [],
        "eligibility_note": "需通过天赋测试和教练评测方可入学",
        "is_active": True,
        "sort_order": 80,
    },
    {
        "id": UUID("45000000-0000-4000-8000-000000000009"),
        "code": "summer-advanced-21-day",
        "name": "暑期进阶班",
        "category": "summer",
        "duration_label": "21 天",
        "price_cents": 1_488_800,
        "price_note": "14,888 元/期",
        "inclusion_note": "含餐食与住宿",
        "summary": "在普通暑期班全部课程基础上，增加心理支持、成长轨迹梳理和家庭教育深度改进。",
        "target_audience": "希望体验职业训练，同时有亲子关系改善需求或希望获得深度心理支持的学员，无特殊门槛。",
        "highlights": [
            *SUMMER_STANDARD_HIGHLIGHTS,
            "俱乐部有经验心理老师驻场咨询，并提供上师大心理专家咨询服务",
            "系统梳理成长历程、心路变化和各阶段家庭陪伴方式",
            "结合现状深度改进家庭教育和亲子关系",
            "帮助学员度过青春期迷茫阶段，明确目标并激发内在驱动力",
        ],
        "schedule": [],
        "eligibility_note": "无特殊门槛",
        "is_active": True,
        "sort_order": 90,
    },
]


def upgrade() -> None:
    op.create_table(
        "enrollment_programs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("category", sa.String(length=24), nullable=False),
        sa.Column("duration_label", sa.String(length=80), nullable=False),
        sa.Column("price_cents", sa.BigInteger(), nullable=False),
        sa.Column("price_note", sa.String(length=200), nullable=False),
        sa.Column("inclusion_note", sa.String(length=300), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("target_audience", sa.Text(), nullable=False),
        sa.Column("highlights", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("schedule", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("eligibility_note", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "category IN ('weekend', 'experience', 'family', 'summer')",
            name="ck_enrollment_programs_category",
        ),
        sa.CheckConstraint("price_cents > 0", name="ck_enrollment_programs_price"),
        sa.CheckConstraint("sort_order >= 0", name="ck_enrollment_programs_sort_order"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_enrollment_programs_code"),
    )
    programs = sa.table(
        "enrollment_programs",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
        sa.column("category", sa.String()),
        sa.column("duration_label", sa.String()),
        sa.column("price_cents", sa.BigInteger()),
        sa.column("price_note", sa.String()),
        sa.column("inclusion_note", sa.String()),
        sa.column("summary", sa.Text()),
        sa.column("target_audience", sa.Text()),
        sa.column("highlights", postgresql.JSONB()),
        sa.column("schedule", postgresql.JSONB()),
        sa.column("eligibility_note", sa.Text()),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.get_bind().execute(programs.insert(), PROGRAM_ROWS)

    op.add_column("admission_cases", sa.Column("application_source", sa.String(length=24)))
    op.add_column(
        "admission_cases",
        sa.Column("selected_project_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column(
        "admission_cases",
        sa.Column("selected_program_id", postgresql.UUID(as_uuid=True)),
    )
    op.add_column("admission_cases", sa.Column("project_name_snapshot", sa.String(length=80)))
    op.add_column("admission_cases", sa.Column("program_name_snapshot", sa.String(length=120)))
    op.add_column("admission_cases", sa.Column("payment_preference", sa.String(length=24)))
    op.create_foreign_key(
        "fk_admission_cases_selected_project",
        "admission_cases",
        "game_projects",
        ["selected_project_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_admission_cases_selected_program",
        "admission_cases",
        "enrollment_programs",
        ["selected_program_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_admission_cases_application_source",
        "admission_cases",
        "application_source IS NULL OR application_source = 'self_service'",
    )
    op.create_check_constraint(
        "ck_admission_cases_payment_preference",
        "admission_cases",
        "payment_preference IS NULL OR payment_preference = 'offline'",
    )
    op.create_check_constraint(
        "ck_admission_cases_self_service_fields",
        "admission_cases",
        "(application_source IS NULL AND selected_project_id IS NULL "
        "AND selected_program_id IS NULL AND project_name_snapshot IS NULL "
        "AND program_name_snapshot IS NULL AND payment_preference IS NULL) OR "
        "(application_source = 'self_service' AND student_person_id IS NOT NULL "
        "AND selected_project_id IS NOT NULL AND selected_program_id IS NOT NULL "
        "AND project_name_snapshot IS NOT NULL AND btrim(project_name_snapshot) <> '' "
        "AND program_name_snapshot IS NOT NULL AND btrim(program_name_snapshot) <> '' "
        "AND payment_preference IS NOT NULL AND agreed_tuition_cents IS NOT NULL)",
    )
    op.create_index(
        "uq_admission_cases_open_self_service_choice",
        "admission_cases",
        ["student_person_id", "selected_project_id", "selected_program_id"],
        unique=True,
        postgresql_where=sa.text(
            "application_source = 'self_service' AND status IN ('pending_match', 'ready')"
        ),
    )

    connection = op.get_bind()
    connection.execute(
        sa.text(
            "INSERT INTO permissions (id, key, name, sensitive) VALUES "
            "(CAST(:id AS uuid), 'enrollment_applications.create', '提交学员报名申请', false) "
            "ON CONFLICT (key) DO UPDATE SET name = EXCLUDED.name, sensitive = false"
        ),
        {"id": PERMISSION_ID},
    )
    for role_key, scope_kind in (("student", "self"), ("guardian", "family")):
        connection.execute(
            sa.text(
                "INSERT INTO role_permissions (role_id, permission_id, scope_kind) "
                "SELECT roles.id, permissions.id, :scope_kind FROM roles, permissions "
                "WHERE roles.key = :role_key "
                "AND permissions.key = 'enrollment_applications.create' "
                "ON CONFLICT DO NOTHING"
            ),
            {"role_key": role_key, "scope_kind": scope_kind},
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "DELETE FROM role_permissions WHERE permission_id IN "
            "(SELECT id FROM permissions WHERE key = 'enrollment_applications.create')"
        )
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE key = 'enrollment_applications.create'")
    )
    op.drop_index(
        "uq_admission_cases_open_self_service_choice",
        table_name="admission_cases",
    )
    op.drop_constraint(
        "ck_admission_cases_self_service_fields",
        "admission_cases",
        type_="check",
    )
    op.drop_constraint(
        "ck_admission_cases_payment_preference",
        "admission_cases",
        type_="check",
    )
    op.drop_constraint(
        "ck_admission_cases_application_source",
        "admission_cases",
        type_="check",
    )
    op.drop_constraint(
        "fk_admission_cases_selected_program",
        "admission_cases",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_admission_cases_selected_project",
        "admission_cases",
        type_="foreignkey",
    )
    op.drop_column("admission_cases", "payment_preference")
    op.drop_column("admission_cases", "program_name_snapshot")
    op.drop_column("admission_cases", "project_name_snapshot")
    op.drop_column("admission_cases", "selected_program_id")
    op.drop_column("admission_cases", "selected_project_id")
    op.drop_column("admission_cases", "application_source")
    op.drop_table("enrollment_programs")
