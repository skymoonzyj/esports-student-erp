from uuid import UUID

import pytest
from sqlalchemy import Uuid, create_engine, literal, select

from app.evaluations.access import EvaluationSqlContext, EvaluationSqlResource
from app.rbac.policy import PolicyRule


@pytest.mark.parametrize(
    ("enrollment_id", "expected"),
    [
        (None, True),
        (UUID("10000000-0000-4000-8000-000000000002"), True),
        (UUID("10000000-0000-4000-8000-000000000001"), False),
    ],
)
def test_center_sql_deny_matches_only_its_target(enrollment_id, expected):
    context = EvaluationSqlContext(
        actor_person_id=None,
        role_rules=(PolicyRule(permission="evaluation_center.access", effect="allow", scope_kind="global"),),
        overrides=(PolicyRule(permission="evaluation_center.access", effect="deny", scope_kind="enrollment",
                              scope_id=UUID("10000000-0000-4000-8000-000000000001")),),
        family_student_ids=frozenset(), assigned_class_ids=frozenset(),
        staff_project_scopes=frozenset(), staff_class_scopes=frozenset(), account_active=True,
    )
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            value = connection.scalar(select(context.allowed(EvaluationSqlResource(
                enrollment_id=literal(enrollment_id, type_=Uuid),
            ))))
        assert value is expected
    finally:
        engine.dispose()
