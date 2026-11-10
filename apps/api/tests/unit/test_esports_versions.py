"""Versioned scene/input rules must not invalidate frozen rules or mix comparisons."""

import itertools
import json
from pathlib import Path

import pytest

from app.audit.service import AuditMetadataRejected, AuditService
from app.esports_assessments.catalog import get_suite, get_task, validate_raw, validate_schedule
from app.esports_assessments.config_schemas import SuiteConfig, TaskConfig
from app.esports_assessments.formal_service import _frozen_comparison
from app.esports_assessments.reporting import build_report, comparison_key
from app.esports_assessments.schemas import AssessmentReportView, PersonDisplaySnapshot
from app.esports_assessments.scoring import practice_result, score_result
from tests.unit.esports_samples import sample_raw
from tests.unit.test_esports_reporting import (
    COMPLETED_AT,
    PERSON_ID,
    REPORT_ID,
    SESSION_ID,
    environment,
    report_results,
    settings,
)
from tests.unit.test_esports_scoring import quality

SUITES = ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba")
VERSIONS = ("catalog_version", "engine_version", "scoring_version", "report_version")
LEGACY = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-v1.json"
    ).read_text()
)
V2 = json.loads(
    (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-v2.json"
    ).read_text()
)

V3_SCORE_REPORT_V2 = {
    "pc-fps": frozenset({"switching"}),
    "mobile-fps": frozenset({"switching", "move-fire"}),
    "pc-moba": frozenset({"dodge"}),
    "mobile-moba": frozenset({"dodge"}),
}


@pytest.mark.parametrize("suite_id", SUITES)
def test_default_v3_catalogs_use_exact_registered_versions_and_new_comparison_identity(suite_id):
    suite = get_suite(suite_id, catalog_version=f"{suite_id}-v3")
    assert suite.catalog_version == f"{suite_id}-v3"
    previous = SuiteConfig.model_validate(V2[suite_id])
    for test_id in suite.test_ids:
        current, old = suite.tasks[test_id], previous.tasks[test_id]
        score_report_version = 2 if test_id in V3_SCORE_REPORT_V2[suite_id] else 1
        assert tuple(getattr(current, field) for field in VERSIONS) == (
            f"{suite_id}-v3",
            f"{suite_id}-v3",
            f"{suite_id}-v{score_report_version}",
            f"{suite_id}-v{score_report_version}",
        )
        if test_id not in V3_SCORE_REPORT_V2[suite_id]:
            assert current.params == old.params
        assert comparison_key(current, settings(suite_id), environment(suite_id)) != comparison_key(
            old, settings(suite_id), environment(suite_id)
        )


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("levels", list(itertools.product((1, 2), repeat=4)))
def test_only_exact_registered_version_combinations_are_accepted(suite_id, levels):
    test_id = LEGACY[suite_id]["test_ids"][0]
    payload = dict(LEGACY[suite_id]["tasks"][test_id])
    payload.update({field: f"{suite_id}-v{level}" for field, level in zip(VERSIONS, levels)})
    if levels in ((1, 1, 1, 1), (2, 2, 1, 1)):
        TaskConfig.model_validate(payload)
    else:
        with pytest.raises(ValueError):
            TaskConfig.model_validate(payload)


@pytest.mark.parametrize(
    "suite_id,test_id,result_revision",
    [
        ("pc-fps", "switching", 2),
        ("pc-fps", "precision", 1),
        ("mobile-fps", "switching", 2),
        ("mobile-fps", "move-fire", 2),
        ("mobile-fps", "precision", 1),
        ("pc-moba", "dodge", 2),
        ("pc-moba", "last-hit", 1),
        ("mobile-moba", "dodge", 2),
        ("mobile-moba", "skill-aim", 1),
    ],
)
@pytest.mark.parametrize(
    "levels", [levels for levels in itertools.product((1, 2, 3), repeat=4) if 3 in levels]
)
def test_v3_changed_and_unchanged_tasks_reject_mixed_versions(
    suite_id, test_id, result_revision, levels
):
    payload = get_task(suite_id, test_id, catalog_version=f"{suite_id}-v3").model_dump(mode="json")
    payload.update({field: f"{suite_id}-v{level}" for field, level in zip(VERSIONS, levels)})
    if levels == (3, 3, result_revision, result_revision):
        assert TaskConfig.model_validate(payload).test_id == test_id
    else:
        with pytest.raises(ValueError):
            TaskConfig.model_validate(payload)


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("difficulty", [1, 2, 3, 4, 5])
def test_both_registered_versions_validate_and_score_frozen_statistics(suite_id, difficulty):
    for test_id in LEGACY[suite_id]["test_ids"]:
        for revision in (1, 2):
            config = get_task(
                suite_id, test_id, difficulty, catalog_version=f"{suite_id}-v{revision}"
            )
            for perfect, expected in ((False, 0), (True, 100)):
                sample = sample_raw(suite_id, test_id, perfect=perfect)
                if test_id == "map-awareness" and not perfect:
                    # A missed map event records its difficulty-scaled deadline.
                    deadline_ms = {1: 1400, 2: 1200, 3: 1000, 4: 850, 5: 700}[difficulty]
                    for event in sample["events"]:
                        event["latency_ms"] = deadline_ms
                raw = validate_raw(config, sample)
                validate_schedule(config, raw, 123)
                assert practice_result(config, raw, quality(suite_id)).score is None
                if difficulty == 3:
                    assert score_result(config, raw, quality(suite_id)).score == expected


@pytest.mark.parametrize("suite_id", SUITES)
def test_old_report_build_and_json_read_remain_frozen_after_current_catalog_changes(suite_id):
    legacy = SuiteConfig.model_validate(LEGACY[suite_id])
    results = [
        r.model_copy(update={"config": legacy.tasks[r.test_id]}) for r in report_results(suite_id)
    ]
    report = build_report(
        SESSION_ID,
        PersonDisplaySnapshot(person_id=PERSON_ID, display_name="合成历史学员"),
        legacy,
        results,
        COMPLETED_AT,
        report_id=REPORT_ID,
    )
    assert report.suite.model_dump(mode="json") == LEGACY[suite_id]
    assert AssessmentReportView.model_validate_json(report.model_dump_json()) == report
    current_result = report_results(suite_id)[0]
    comparison = _frozen_comparison(current_result, (REPORT_ID, report.results[0]))
    assert comparison.score_delta is None
    assert comparison.non_comparable_reasons == ("config_changed",)


@pytest.mark.parametrize("suite_id", SUITES)
def test_suite_version_cannot_hide_mixed_task_versions(suite_id):
    legacy = LEGACY[suite_id]
    mixed = json.loads(json.dumps(legacy))
    test_id = legacy["test_ids"][0]
    mixed["tasks"][test_id]["catalog_version"] = f"{suite_id}-v2"
    mixed["tasks"][test_id]["engine_version"] = f"{suite_id}-v2"
    with pytest.raises(ValueError):
        SuiteConfig.model_validate(mixed)


@pytest.mark.parametrize("suite_id", SUITES)
def test_explicit_v1_catalogs_remain_identical_to_the_original_snapshots(suite_id):
    assert (
        get_suite(suite_id, catalog_version=f"{suite_id}-v1").model_dump(mode="json")
        == LEGACY[suite_id]
    )


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("version_kind", ["empty", "unknown", "future", "different_suite"])
def test_unknown_catalogs_cannot_fall_back_to_latest_rules(suite_id, version_kind):
    version = {
        "empty": "",
        "unknown": "unknown",
        "future": f"{suite_id}-v6",
        "different_suite": f"{'mobile-moba' if suite_id != 'mobile-moba' else 'pc-fps'}-v3",
    }[version_kind]
    with pytest.raises(ValueError):
        get_suite(suite_id, catalog_version=version)


@pytest.mark.parametrize("suite_id", SUITES)
def test_audit_accepts_exact_registered_v3_catalog_and_v2_score_versions(suite_id):
    metadata = {"catalog_version": f"{suite_id}-v3", "scoring_version": f"{suite_id}-v2"}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata


@pytest.mark.parametrize(
    "metadata",
    [
        {"catalog_version": "pc-fps-v6"},
        {"catalog_version": "mobile-fps-v4"},
        {"catalog_version": "pc-moba-v6"},
        {"catalog_version": "mobile-moba-v4"},
        {"scoring_version": "pc-fps-v3"},
        {"scoring_version": "mobile-fps-v3"},
        {"scoring_version": "pc-moba-v3"},
        {"scoring_version": "mobile-moba-v3"},
    ],
)
def test_audit_still_rejects_unregistered_future_catalog_and_score_versions(metadata):
    with pytest.raises(AuditMetadataRejected):
        AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == {}
