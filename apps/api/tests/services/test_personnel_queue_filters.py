"""Queue filters must narrow authorized SQL, not one fetched page or global names."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import event

from app.identity.models import Person
from app.personnel_intake.errors import PersonnelIntakeError
from app.personnel_intake.models import PersonnelIntakeRequest
from app.rbac.models import RolePermission, UserPermissionOverride, UserRole
from app.rbac.seed import PERMISSIONS, ROLES
from tests.api.conftest import platform_users as _platform_users
from tests.services.test_personnel_intake import actor, intake, service
from tests.services.test_team_access import teams


@pytest.fixture
def platform_users(db):
    return _platform_users.__wrapped__(db)


def queue_row(
    db,
    users,
    *,
    submitter="coach",
    kind="student",
    status="pending",
    team=None,
    person_id=None,
    coaches=None,
):
    row = PersonnelIntakeRequest(
        submitted_by_user_id=users["accounts"][submitter].id,
        kind=kind,
        state=status,
        version=1,
        person_id=person_id,
        target_team_id=team,
        target_coach_person_ids=(
            coaches
            if coaches is not None
            else [users["people"]["coach"].id]
            if kind != "player"
            else []
        ),
        created_at=datetime(2026, 10, 18, tzinfo=UTC),
    )
    db.add(row)
    db.flush()
    return row


def override(db, users, who, permission, effect, scope_kind="global", scope_id=None):
    db.add(
        UserPermissionOverride(
            user_id=users["accounts"][who].id,
            permission_id=PERMISSIONS["personnel_intake." + permission].id,
            effect=effect,
            scope_kind=scope_kind,
            scope_id=scope_id,
        )
    )
    db.flush()


def scoped_admin(db, users, team_id):
    """A real admin with only one review team and explicitly no submit authority."""
    db.add(UserRole(user_id=users["accounts"]["coach"].id, role_id=ROLES["admin"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    override(db, users, "coach", "submit", "deny")
    override(db, users, "coach", "review", "allow", "assigned_team", team_id)


def test_mine_review_and_default_union_keep_own_actions(db, platform_users):
    own = queue_row(db, platform_users, submitter="admin")
    other = queue_row(db, platform_users)
    s, admin = service(db), actor(platform_users, "admin")
    mine = s.list_requests(admin, view="mine")
    assert [item.id for item in mine.items] == [own.id]
    assert mine.items[0].allowed_actions == ["withdraw"]
    review = s.list_requests(admin, view="review")
    assert {item.id for item in review.items} == {own.id, other.id}
    assert next(item for item in review.items if item.id == own.id).allowed_actions == ["withdraw"]
    assert next(item for item in review.items if item.id == other.id).allowed_actions == [
        "approve",
        "return",
    ]
    assert {item.id for item in s.list_requests(admin).items} == {own.id, other.id}


def test_coach_mine_never_expands_current_visibility_or_grants_review(db, platform_users):
    own = queue_row(db, platform_users)
    queue_row(db, platform_users, submitter="admin")
    s, coach = service(db), actor(platform_users)
    assert [item.id for item in s.list_requests(coach, view="mine").items] == [own.id]
    override(db, platform_users, "coach", "review", "allow")
    with pytest.raises(PersonnelIntakeError) as denied:
        s.list_requests(coach, view="review")
    assert denied.value.status == 403
    db.execute(UserRole.__table__.delete().where(UserRole.user_id == coach.account.id))
    db.flush()
    assert s.list_requests(coach, view="mine").total == 0


def test_review_only_admin_cannot_borrow_submit_union_or_other_team_names(db, platform_users):
    first, second = teams(db, actor(platform_users, "admin").account.id)
    visible = queue_row(db, platform_users, submitter="admin", kind="player", team=first.id)
    queue_row(db, platform_users, submitter="guardian", kind="player", team=second.id)
    queue_row(db, platform_users, submitter="guardian")
    scoped_admin(db, platform_users, first.id)
    s, reviewer = service(db), actor(platform_users)
    page = s.list_requests(reviewer, view="review")
    assert (page.total, [item.id for item in page.items]) == (1, [visible.id])
    assert page.items[0].submitted_by.model_dump() == {
        "id": platform_users["accounts"]["admin"].id,
        "label": "平台管理员",
    }
    assert s.list_requests(reviewer, view="mine").total == 0
    assert s.list_requests(reviewer, view="review", submitter_query="学员家长").total == 0
    override(db, platform_users, "coach", "review", "deny", "assigned_team", first.id)
    with pytest.raises(PersonnelIntakeError) as denied:
        s.list_requests(reviewer, view="review")
    assert denied.value.status == 403


def test_review_view_excludes_submit_only_rows_and_role_without_review(db, platform_users):
    first, second = teams(db, actor(platform_users, "admin").account.id)
    db.add(UserRole(user_id=platform_users["accounts"]["coach"].id, role_id=ROLES["admin"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    queue_row(db, platform_users, submitter="admin", kind="player", team=second.id)
    reviewer = actor(platform_users)
    assert service(db).list_requests(reviewer).total == 0
    with pytest.raises(PersonnelIntakeError) as denied:
        service(db).list_requests(reviewer, view="review")
    assert denied.value.status == 403
    override(db, platform_users, "coach", "review", "allow", "assigned_team", first.id)
    assert service(db).list_requests(reviewer, view="review").total == 0
    assert service(db).list_requests(reviewer, view="mine").total == 0
    assert service(db).list_requests(reviewer).total == 0


def test_review_only_admin_sees_own_in_scope_but_no_review_or_submit_actions(db, platform_users):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    own = queue_row(db, platform_users, kind="player", team=first.id)
    scoped_admin(db, platform_users, first.id)
    reviewer = actor(platform_users)
    page = service(db).list_requests(reviewer, view="review")
    assert (page.total, [item.id for item in page.items]) == (1, [own.id])
    assert page.items[0].allowed_actions == []


@pytest.mark.parametrize("status", ["pending", "returned", "approved", "withdrawn"])
@pytest.mark.parametrize("kind", ["student", "player", "both"])
def test_all_filters_precede_count_and_stable_pagination(db, platform_users, status, kind):
    first, _ = teams(db, actor(platform_users, "admin").account.id)
    matches = [
        queue_row(
            db,
            platform_users,
            kind=kind,
            status=status,
            team=first.id if kind != "student" else None,
        )
        for _ in range(5)
    ]
    queue_row(db, platform_users, submitter="admin", kind=kind, status=status)
    queue_row(db, platform_users, kind="player" if kind == "student" else "student", status=status)
    queue_row(
        db, platform_users, kind=kind, status="returned" if status != "returned" else "pending"
    )
    s, reviewer = service(db), actor(platform_users, "admin")
    pages = [
        s.list_requests(
            reviewer,
            view="review",
            status=status,
            kind=kind,
            submitter_query="  平台教练  ",
            page=p,
            page_size=2,
        )
        for p in (1, 2, 3)
    ]
    assert [page.total for page in pages] == [5, 5, 5]
    assert [len(page.items) for page in pages] == [2, 2, 1]
    assert [item.id for page in pages for item in page.items] == sorted(row.id for row in matches)


@pytest.mark.parametrize("needle", ["%", "_", "\\", "cOaCh"])
def test_submitter_search_is_literal_bound_case_insensitive_contains(db, platform_users, needle):
    db.get(Person, platform_users["people"]["coach"].id).full_name = "Literal%_\\Coach Name"
    wanted = queue_row(db, platform_users)
    queue_row(db, platform_users, submitter="admin")
    found = service(db).list_requests(
        actor(platform_users, "admin"), view="review", submitter_query=needle
    )
    assert (found.total, [item.id for item in found.items]) == (1, [wanted.id])


def test_mine_and_review_preserve_explicit_person_denies(db, platform_users):
    target = platform_users["people"]["own_student"].id
    queue_row(db, platform_users, submitter="admin", person_id=target)
    wanted = queue_row(db, platform_users, submitter="admin")
    for permission in ("submit", "review"):
        override(db, platform_users, "admin", permission, "deny", "person", target)
    for view in ("mine", "review", None):
        page = service(db).list_requests(actor(platform_users, "admin"), view=view)
        assert (page.total, [item.id for item in page.items]) == (1, [wanted.id])


def test_submitter_dto_on_mutations_detail_and_inactive_historical_submitter(db, platform_users):
    from app.personnel_intake.approval_service import PersonnelApprovalService

    s, coach = service(db), actor(platform_users)
    submitted = s.submit(coach, intake(platform_users))
    expected = {"id": coach.account.id, "label": "平台教练"}
    assert submitted.submitted_by.model_dump() == expected
    returned = PersonnelApprovalService(db, cipher=s.cipher, lookup=s.lookup).return_request(
        actor(platform_users, "admin"), submitted.id, 1, "补充资料"
    )
    assert returned.submitted_by.model_dump() == expected
    row = db.get(PersonnelIntakeRequest, submitted.id)
    assert (
        s.resubmit(coach, row.id, 1, intake(platform_users)).submitted_by.model_dump() == expected
    )
    assert s.withdraw(coach, row.id, 2).submitted_by.model_dump() == expected
    coach.account.status = "disabled"
    db.get(Person, coach.person.id).full_name = "历史提交者当前姓名"
    db.flush()
    reviewer = actor(platform_users, "admin")
    assert s.detail(reviewer, row.id).submitted_by.label == "历史提交者当前姓名"
    assert (
        s.list_requests(reviewer, view="review").items[0].submitted_by.label == "历史提交者当前姓名"
    )


def test_blank_search_is_ignored_outside_review_and_trimmed_limit_is_supported(db, platform_users):
    wanted = queue_row(db, platform_users, submitter="admin")
    admin = actor(platform_users, "admin")
    for view in ("mine", None):
        page = service(db).list_requests(admin, view=view, submitter_query="   ")
        assert (page.total, [item.id for item in page.items]) == (1, [wanted.id])
    db.get(Person, platform_users["people"]["admin"].id).full_name = "名" * 80
    db.flush()
    page = service(db).list_requests(admin, view="review", submitter_query="  " + "名" * 80 + "  ")
    assert (page.total, [item.id for item in page.items]) == (1, [wanted.id])


def test_person_scoped_reviewer_can_enter_empty_queue_without_global_grant(db, platform_users):
    target = platform_users["people"]["own_student"].id
    db.add(UserRole(user_id=platform_users["accounts"]["coach"].id, role_id=ROLES["admin"].id))
    db.execute(
        RolePermission.__table__.delete().where(
            RolePermission.role_id == ROLES["admin"].id,
            RolePermission.permission_id == PERMISSIONS["personnel_intake.review"].id,
        )
    )
    override(db, platform_users, "coach", "submit", "deny")
    override(db, platform_users, "coach", "review", "allow", "person", target)
    reviewer = actor(platform_users)
    assert service(db).list_requests(reviewer, view="review").total == 0
    wanted = queue_row(db, platform_users, person_id=target)
    queue_row(db, platform_users)
    page = service(db).list_requests(reviewer, view="review")
    assert (page.total, [item.id for item in page.items]) == (1, [wanted.id])


def test_queue_has_bounded_queries_and_projects_only_submitter_name(
    db, platform_users, monkeypatch
):
    for _ in range(45):
        queue_row(db, platform_users)
    s, reviewer = service(db), actor(platform_users, "admin")
    db.commit()

    def no_decrypt(*args, **kwargs):
        pytest.fail("queue must not decrypt contact or payload fields")

    monkeypatch.setattr(s.cipher, "decrypt", no_decrypt)
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        page = s.list_requests(
            reviewer, view="review", page=2, page_size=20, submitter_query="教练"
        )
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert (page.total, len(page.items)) == (45, 20)
    assert {item.submitted_by.label for item in page.items} == {"平台教练"}
    assert len(statements) <= 10
    assert not any(
        "people.identity_ciphertext" in q or "people.phone_ciphertext" in q for q in statements
    )


@pytest.mark.parametrize(
    "query",
    [
        {"view": "hidden"},
        {"status": "hidden"},
        {"kind": "hidden"},
        {"submitter_query": "secret@example.com"},
        {"view": "mine", "submitter_query": "secret@example.com"},
        {"view": "review", "submitter_query": "名" * 81},
        {"page": 0},
        {"page_size": 101},
    ],
)
def test_service_rejects_invalid_queue_input_privately(db, platform_users, query):
    with pytest.raises(PersonnelIntakeError) as invalid:
        service(db).list_requests(actor(platform_users, "admin"), **query)
    assert invalid.value.status == 422
    assert "secret@example.com" not in invalid.value.detail
