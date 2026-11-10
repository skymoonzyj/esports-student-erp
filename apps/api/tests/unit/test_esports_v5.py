"""Fixed-board v5 registration preserves frozen v4 rules and score semantics."""

import json
from pathlib import Path

import pytest

from app.audit.service import AuditService
from app.esports_assessments.catalog import get_suite, get_task, validate_raw
from app.esports_assessments.config_schemas import (
    TaskConfig,
    members_for_catalog,
    registered_catalog_version,
)
from app.esports_assessments.rule_versions import registered_rule
from app.esports_assessments.scoring import score_result
from tests.unit.test_esports_scoring import quality

TRIAD = ("gridshot", "sixshot", "spheretrack90")


def test_v5_catalog_registered_and_v4_unchanged():
    assert registered_catalog_version("pc-fps", None) == "pc-fps-v5"
    for revision in range(1, 6):
        assert registered_catalog_version("pc-fps", f"pc-fps-v{revision}") == f"pc-fps-v{revision}"
    assert members_for_catalog("pc-fps", "pc-fps-v5") == TRIAD
    fixtures = Path(__file__).resolve().parents[3] / "web/src/esportsAssessments/testSupport"
    for revision in (4, 5):
        expected = json.loads((fixtures / f"catalog-v{revision}.json").read_text())
        assert expected == {
            suite: get_suite(
                suite, catalog_version=f"pc-fps-v{revision}" if suite == "pc-fps" else f"{suite}-v3"
            ).model_dump(mode="json")
            for suite in ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba")
        }


@pytest.mark.parametrize("difficulty,size", [(1, 1.5), (2, 1.25), (3, 1), (4, 0.85), (5, 0.7)])
def test_v5_parameters_are_exact_and_tracking_is_unchanged(difficulty, size):
    for test, count, diameter, rows, spacing, pitch, reference in (
        ("gridshot", 3, 6, 3, 9, 9, 300),
        ("sixshot", 6, 1.5, 5, 6, 12, 150),
    ):
        current = get_task("pc-fps", test, difficulty)
        assert current.duration_ms == 60000
        assert current.params.model_dump(mode="json") == {
            "kind": "multi_target",
            "horizontal_fov_deg": 90,
            "pitch_limit_deg": pitch,
            "fire_interval_ms": 50,
            "target_count": count,
            "target_diameter_deg": diameter * size,
            "grid_columns": 5,
            "grid_rows": rows,
            "grid_spacing_deg": spacing,
            "reference_hits": reference,
        }
        old = get_task("pc-fps", test, difficulty, catalog_version="pc-fps-v4")
        assert old.params.pitch_limit_deg == 70
        assert old.params.grid_spacing_deg == (10 if test == "gridshot" else 6)
        payload = current.model_dump(mode="json")
        payload["params"]["grid_spacing_deg"] += 1
        with pytest.raises(ValueError, match="parameters differ"):
            TaskConfig.model_validate(payload)
    assert (
        get_task("pc-fps", "spheretrack90", difficulty).params
        == get_task("pc-fps", "spheretrack90", difficulty, catalog_version="pc-fps-v4").params
    )


@pytest.mark.parametrize(
    "test,kind",
    [("gridshot", "multi_target"), ("sixshot", "multi_target"), ("spheretrack90", "spheretrack90")],
)
def test_v5_rule_identities(test, kind):
    config = get_task("pc-fps", test)
    assert config.engine_version == "pc-fps-v5"
    assert config.scoring_version == config.report_version == "pc-fps-v4"
    identity = registered_rule("pc-fps", test, "pc-fps-v5")
    assert identity.raw_kind == config.params.kind == kind
    assert identity.scoring_version == identity.report_version == "pc-fps-v4"


@pytest.mark.parametrize(
    "test,raw",
    [
        (
            "gridshot",
            {
                "kind": "multi_target",
                "shots": [{"at_ms": i * 50, "hit": i < 150} for i in range(300)],
            },
        ),
        (
            "sixshot",
            {
                "kind": "multi_target",
                "shots": [{"at_ms": i * 50, "hit": i < 150} for i in range(300)],
            },
        ),
        (
            "spheretrack90",
            {
                "kind": "spheretrack90",
                "coverage_ms": 45000,
                "loss_count": 2,
                "reacquisition_ms": [100, 200],
            },
        ),
    ],
)
def test_v5_and_v4_known_raw_results_have_identical_scores(test, raw):
    scores = []
    for version in ("pc-fps-v4", "pc-fps-v5"):
        config = get_task("pc-fps", test, catalog_version=version)
        scores.append(score_result(config, validate_raw(config, raw), quality("pc-fps")))
    assert scores[0] == scores[1]
    assert scores[1].score == {"gridshot": 25, "sixshot": 50, "spheretrack90": 75}[test]


def test_v5_audit_only_registers_catalog_not_a_new_scoring_version():
    metadata = {"catalog_version": "pc-fps-v5", "scoring_version": "pc-fps-v4"}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata
    assert AuditService.public_metadata({"scoring_version": "pc-fps-v5"}) == {}
