"""Task A regressions for live respondent authorization and supplement state."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.evaluations.context_service import EvaluationContextService
from app.evaluations.survey_models import (
    CampSurveyCycle,
    CampSurveySnapshotVersion,
    CampSurveyStaffSnapshot,
)
from app.rbac.models import UserPermissionOverride
from app.rbac.seed import PERMISSIONS
from tests.api.test_camp_surveys import (
    CampApiScenario,
    _append_snapshot_v2,
    _counts,
    _csrf_headers,
)

pytest_plugins = ("tests.api.test_camp_surveys",)


def _append_snapshot_v3(scenario: CampApiScenario) -> CampSurveyStaffSnapshot:
    db = scenario.db
    cycle = db.get(CampSurveyCycle, scenario.cycle.id)
    assert cycle is not None
    previous = db.scalar(
        select(CampSurveySnapshotVersion).where(
            CampSurveySnapshotVersion.cycle_id == cycle.id,
            CampSurveySnapshotVersion.version == 2,
        )
    )
    assert previous is not None
    old_rows = tuple(
        db.scalars(
            select(CampSurveyStaffSnapshot).where(
                CampSurveyStaffSnapshot.snapshot_version_id == previous.id
            )
        )
    )
    cycle.current_snapshot_version = 3
    snapshot = CampSurveySnapshotVersion(
        cycle_id=cycle.id,
        version=3,
        change_reason="第三版快照新增工作人员",
        created_by_user_id=scenario.accounts["admin"].id,
    )
    db.add(snapshot)
    db.flush()
    db.add_all(
        CampSurveyStaffSnapshot(
            snapshot_version_id=snapshot.id,
            staff_person_id=row.staff_person_id,
            staff_name_snapshot=row.staff_name_snapshot,
            role_key_snapshot=row.role_key_snapshot,
            role_name_snapshot=row.role_name_snapshot,
            role_category=row.role_category,
            roles_snapshot=row.roles_snapshot,
            service_intervals=row.service_intervals,
            include_in_summary=row.include_in_summary,
        )
        for row in old_rows
    )
    added = CampSurveyStaffSnapshot(
        snapshot_version_id=snapshot.id,
        staff_person_id=scenario.people["customer_service"].id,
        staff_name_snapshot="第三版新增工作人员",
        role_key_snapshot="operations",
        role_name_snapshot="运营老师",
        role_category="operations",
        roles_snapshot=[
            {
                "role_key": "operations",
                "role_name": "运营老师",
                "role_category": "operations",
            }
        ],
        service_intervals=[],
        include_in_summary=True,
    )
    db.add(added)
    db.commit()
    return added


@pytest.mark.parametrize("scope_kind", ["global", "cohort"])
def test_respondent_routes_recheck_live_center_resource_before_any_write(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
    scope_kind: str,
) -> None:
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    seeded = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, f"task-a-center-seed-{scope_kind}"),
        json=scenario.submission_payload(key=f"task-a-center-seed-{scope_kind}"),
    )
    assert seeded.status_code == 201, seeded.text
    submission_id = seeded.json()["submission_id"]
    added = _append_snapshot_v2(scenario)
    scenario.db.add(
        UserPermissionOverride(
            user_id=scenario.accounts["self_student"].id,
            permission_id=PERMISSIONS["evaluation_center.access"].id,
            effect="deny",
            scope_kind=scope_kind,
            scope_id=scenario.cycle.cohort_id if scope_kind == "cohort" else None,
        )
    )
    scenario.db.commit()

    before = _counts(scenario.db)
    context = db_client.get(
        "/api/v1/evaluations/surveys/context",
        headers={"X-Request-ID": f"task-a-center-context-{scope_kind}"},
    )
    detail = db_client.get(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}",
        headers={"X-Request-ID": f"task-a-center-detail-{scope_kind}"},
    )
    post = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, f"task-a-center-post-{scope_kind}"),
        json=scenario.submission_payload(key=f"task-a-center-post-{scope_kind}"),
    )
    revision = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/{submission_id}/revisions",
        headers=_csrf_headers(csrf, f"task-a-center-revision-{scope_kind}"),
        json=scenario.submission_payload(
            key=f"task-a-center-revision-{scope_kind}",
            snapshot_version=1,
        ),
    )
    supplement = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/{submission_id}/supplement",
        headers=_csrf_headers(csrf, f"task-a-center-supplement-{scope_kind}"),
        json=scenario.supplement_payload(
            key=f"task-a-center-supplement-{scope_kind}",
            staff_id=added.id,
        ),
    )

    # Collection endpoints hide denied rows inside an otherwise valid empty
    # page. Resource and mutation endpoints retain private-404 equivalence.
    assert context.status_code == 200, context.text
    assert context.json() == []
    for response in (detail, post, revision, supplement):
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "evaluation_resource_not_found"
    assert _counts(scenario.db) == before


def test_supplement_history_deduplicates_staff_by_stable_person_id(
    db_client: TestClient,
    camp_api_scenario: CampApiScenario,
) -> None:
    scenario = camp_api_scenario
    csrf = scenario.login(db_client, "self_student")
    main = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions",
        headers=_csrf_headers(csrf, "task-a-dedup-main"),
        json=scenario.submission_payload(key="task-a-dedup-main"),
    )
    assert main.status_code == 201, main.text
    revision = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{main.json()['submission_id']}/revisions",
        headers=_csrf_headers(csrf, "task-a-dedup-revision"),
        json=scenario.submission_payload(key="task-a-dedup-revision"),
    )
    assert revision.status_code == 200, revision.text
    added_v2 = _append_snapshot_v2(scenario)
    supplement = db_client.post(
        f"/api/v1/evaluations/surveys/{scenario.cycle.id}/submissions/"
        f"{main.json()['submission_id']}/supplement",
        headers=_csrf_headers(csrf, "task-a-dedup-supplement-v2"),
        json=scenario.supplement_payload(
            key="task-a-dedup-supplement-v2",
            staff_id=added_v2.id,
        ),
    )
    assert supplement.status_code == 201, supplement.text
    added_v3 = _append_snapshot_v3(scenario)

    context = db_client.get(
        "/api/v1/evaluations/surveys/context",
        headers={"X-Request-ID": "task-a-dedup-context-v3"},
    )
    assert context.status_code == 200, context.text
    item = next(row for row in context.json() if row["cycle_id"] == str(scenario.cycle.id))
    assert [row["id"] for row in item["supplement_staff"]] == [str(added_v3.id)]

    assert (
        EvaluationContextService(scenario.db).surveys_available(
            account_id=scenario.accounts["self_student"].id,
            occurred_at=scenario.cycle.opens_at + timedelta(hours=1),
        )
        is True
    )
