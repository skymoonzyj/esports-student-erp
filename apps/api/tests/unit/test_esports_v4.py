"""The triad uses its own frozen contracts without changing historical catalogs."""

import copy
import json
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from app.audit.service import AuditService
from app.esports_assessments.catalog import get_suite, get_task, validate_raw
from app.esports_assessments.config_schemas import SuiteConfig, TaskConfig
from app.esports_assessments.query_schemas import AssessmentContextView, RecoveryItem
from app.esports_assessments.ranking_schemas import AssessmentRankingView
from app.esports_assessments.schemas import AssessmentReportView, SessionView
from app.esports_assessments.scoring import score_result
from tests.unit.test_esports_scoring import quality
from tests.unit.test_esports_v3_reporting import report_for

TRIAD = ("gridshot", "sixshot", "spheretrack90")


def test_frozen_v4_pc_catalog_is_a_triad_and_other_suites_stay_v3():
    current = get_suite("pc-fps", catalog_version="pc-fps-v4")
    assert current.test_ids == TRIAD
    assert current.catalog_version == "pc-fps-v4"
    assert SuiteConfig.model_validate_json(current.model_dump_json()) == current
    for task in current.tasks.values():
        assert task.engine_version == task.scoring_version == task.report_version == "pc-fps-v4"
    for suite_id in ("mobile-fps", "pc-moba", "mobile-moba"):
        assert (
            get_suite(suite_id, catalog_version=f"{suite_id}-v3").catalog_version
            == f"{suite_id}-v3"
        )
    for revision in (1, 2, 3):
        assert get_suite("pc-fps", catalog_version=f"pc-fps-v{revision}").test_ids == (
            "precision",
            "tracking",
            "switching",
            "recognition",
        )


def test_v4_fixture_matches_complete_frozen_backend_payload():
    path = Path(__file__).resolve().parents[3] / (
        "web/src/esportsAssessments/testSupport/catalog-v4.json"
    )
    assert json.loads(path.read_text()) == {
        suite: get_suite(
            suite, catalog_version="pc-fps-v4" if suite == "pc-fps" else f"{suite}-v3"
        ).model_dump(mode="json")
        for suite in ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba")
    }


@pytest.mark.parametrize(
    "difficulty,size,speed",
    [
        (1, 1.5, 0.6),
        (2, 1.25, 0.8),
        (3, 1, 1),
        (4, 0.85, 1.15),
        (5, 0.7, 1.3),
    ],
)
def test_registered_v4_parameters_are_exact_and_cannot_be_mutated(difficulty, size, speed):
    grid = get_task("pc-fps", "gridshot", difficulty, catalog_version="pc-fps-v4")
    six = get_task("pc-fps", "sixshot", difficulty, catalog_version="pc-fps-v4")
    tracking = get_task("pc-fps", "spheretrack90", difficulty, catalog_version="pc-fps-v4")
    for task, count, diameter, rows, spacing, reference in (
        (grid, 3, 6, 3, 10, 300),
        (six, 6, 1.5, 5, 6, 150),
    ):
        assert task.params.model_dump(mode="json") == {
            "kind": "multi_target",
            "horizontal_fov_deg": 90.0,
            "pitch_limit_deg": 70.0,
            "fire_interval_ms": 50.0,
            "target_count": count,
            "target_diameter_deg": diameter * size,
            "grid_columns": 5,
            "grid_rows": rows,
            "grid_spacing_deg": spacing,
            "reference_hits": reference,
        }
    assert tracking.params.model_dump(mode="json") == {
        "kind": "spheretrack90",
        "horizontal_fov_deg": 90.0,
        "pitch_limit_deg": 70.0,
        "fire_interval_ms": 200.0,
        "target_diameter_deg": 3 * size,
        "speed_deg_s": 30 * speed,
        "horizontal_bound_deg": 40.0,
        "vertical_bound_deg": 15.0,
        "direction_interval_ms": 2000.0,
        "turn_ms": 200.0,
    }
    payload = grid.model_dump(mode="json")
    payload["params"]["reference_hits"] = 200
    with pytest.raises(ValueError):
        TaskConfig.model_validate(payload)


@pytest.mark.parametrize(
    "suite,test,version",
    [
        ("pc-fps", "precision", None),
        ("pc-fps", "gridshot", "pc-fps-v3"),
        ("mobile-fps", "gridshot", None),
        ("mobile-fps", "precision", "mobile-fps-v4"),
    ],
)
def test_unregistered_task_catalog_combinations_fail(suite, test, version):
    with pytest.raises(ValueError):
        get_task(suite, test, catalog_version=version)


@pytest.mark.parametrize(
    "shots",
    [
        [{"at_ms": -1, "hit": True}],
        [{"at_ms": 60000, "hit": True}],
        [{"at_ms": float("nan"), "hit": True}],
        [{"at_ms": True, "hit": True}],
        [{"at_ms": 0, "hit": 1}],
        [{"at_ms": 10, "hit": True}, {"at_ms": 10, "hit": False}],
        [{"at_ms": 10, "hit": True}, {"at_ms": 59.999, "hit": False}],
        [{"at_ms": i * 49, "hit": True} for i in range(1201)],
    ],
)
def test_invalid_shots_are_rejected(shots):
    with pytest.raises(ValueError):
        validate_raw(get_task("pc-fps", "gridshot"), {"kind": "multi_target", "shots": shots})


@pytest.mark.parametrize(
    "test,hits,shots,expected",
    [
        ("gridshot", 150, 150, 50),
        ("gridshot", 150, 300, 25),
        ("gridshot", 0, 0, 0),
        ("sixshot", 150, 150, 100),
        ("sixshot", 300, 600, 50),
        ("gridshot", 3, 6, 1),
    ],
)
def test_multi_target_scores_use_exact_reference_accuracy_and_half_up(test, hits, shots, expected):
    config = get_task("pc-fps", test)
    raw = validate_raw(
        config,
        {
            "kind": "multi_target",
            "shots": [{"at_ms": i * 50, "hit": i < hits} for i in range(shots)],
        },
    )
    result = score_result(config, raw, quality("pc-fps"))
    assert result.score == expected
    assert result.metrics["hits"] == hits
    assert result.metrics["shots"] == shots
    assert result.metrics["hits_per_second"] == hits / 60
    assert result.metrics["successful_average_interval_ms"] == (50 if hits > 1 else None)


def test_spheretrack_uses_independent_kind_and_strict_existing_loss_constraints():
    config = get_task("pc-fps", "spheretrack90")
    value = {"kind": "spheretrack90", "coverage_ms": 60000, "loss_count": 0, "reacquisition_ms": []}
    assert score_result(config, validate_raw(config, value), quality("pc-fps")).score == 100
    for changes in ({"kind": "tracking"}, {"coverage_ms": 60001}, {"reacquisition_ms": [1]}):
        with pytest.raises(ValueError):
            validate_raw(config, value | changes)


def test_v4_report_and_session_require_exactly_three_members_and_preserve_old_four():
    report = report_for("pc-fps", catalog_version="pc-fps-v4")
    assert len(report.results) == 3
    assert [r.test_id for r in report.results] == list(TRIAD)
    assert "网格射击" in report.recommendations[0]
    assert "六目标射击" in report.recommendations[1]
    assert "90°范围跟枪" in report.recommendations[2]
    assert all("ERP 暂定场景规则" in r for r in report.recommendations)
    assert AssessmentReportView.model_validate_json(report.model_dump_json()) == report
    payload = {
        "id": report.session_id,
        "suite_id": "pc-fps",
        "suite_config": report.suite.model_dump(mode="json"),
        "status": "completed",
        "members": TRIAD,
        "report_id": report.id,
        "results": [
            {"result_id": r.result_id, "test_id": r.test_id, "scored": r.scored}
            for r in report.results
        ],
    }
    assert len(SessionView.model_validate(payload).results) == 3
    for count in (2, 4):
        bad = copy.deepcopy(payload)
        bad["results"] = (payload["results"] + [payload["results"][0]])[:count]
        with pytest.raises(ValueError):
            SessionView.model_validate(bad)
    for revision in (1, 2, 3):
        old = report_for("pc-fps", catalog_version=f"pc-fps-v{revision}")
        assert len(old.results) == 4


def test_recovery_accepts_historical_union_but_formal_context_checks_frozen_members():
    report = report_for("pc-fps", catalog_version="pc-fps-v4")
    for test_id in ("gridshot", "precision"):
        run = RecoveryItem(
            mode="practice",
            attempt_id=uuid4(),
            session_id=None,
            suite_id="pc-fps",
            test_id=test_id,
            issued_at=report.completed_at,
            run_until=report.completed_at + timedelta(seconds=90),
            submit_until=report.completed_at + timedelta(minutes=30),
            state="running",
        )
        AssessmentContextView(
            server_now=report.completed_at,
            can_take=True,
            can_read_self=True,
            can_read_reports=False,
            available_suites=(),
            own_in_progress_sessions=(),
            active_run=run.model_dump(exclude={"state", "issued_at"}),
        )


def test_audit_accepts_v4_registered_catalog_and_score_versions():
    metadata = {"catalog_version": "pc-fps-v4", "scoring_version": "pc-fps-v4"}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


def test_anonymous_ranking_contract_accepts_three_items_and_bounded_signed_gaps():
    report = report_for("pc-fps", catalog_version="pc-fps-v4")
    payload = {
        "report_id": report.id,
        "calculated_at": report.completed_at,
        "status": "ranked",
        "best_report_id": report.id,
        "best_completed_at": report.completed_at,
        "overall_score": 50.0,
        "overall_rank": 2,
        "participant_count": 2,
        "top_scores": [
            {"rank": 1, "score": 60.0, "gap": 10.0},
            {"rank": 2, "score": 50.0, "gap": 0.0},
        ],
        "items": [
            {
                "test_id": t,
                "score": 50,
                "rank": 2,
                "top_scores": [{"rank": 1, "score": 40, "gap": -10}],
            }
            for t in TRIAD
        ],
    }
    value = AssessmentRankingView.model_validate(payload)
    assert len(value.items) == 3
    assert len(value.top_scores) == 2
    assert set(value.top_scores[0].model_dump()) == {"rank", "score", "gap"}
    for mutation in ({"person_id": str(uuid4())}, {"gap": 101}, {"score": float("nan")}):
        bad = copy.deepcopy(payload)
        bad["top_scores"][0].update(mutation)
        with pytest.raises(ValueError):
            AssessmentRankingView.model_validate(bad)


def test_anonymous_top_query_projects_only_scores_and_is_server_bounded():
    from sqlalchemy import column, table
    from sqlalchemy.dialects import postgresql

    from app.esports_assessments.ranking import _anonymous_top_query

    source = table(
        "ranked",
        *(column(k) for k in ("score", "item_rank", "completed_at", "report_id", "person_id")),
    )
    query = _anonymous_top_query(source, source.c.score, source.c.item_rank, 60)
    assert tuple(query.selected_columns.keys()) == ("rank", "anonymous_score", "gap")
    compiled = query.compile(dialect=postgresql.dialect())
    assert 3 in compiled.params.values()
    assert "LIMIT" in str(compiled)
