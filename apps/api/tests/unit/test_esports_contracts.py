import copy
import json

import pytest
from pydantic import ValidationError

from app.esports_assessments.catalog import get_suite, get_task, validate_raw, validate_schedule
from app.esports_assessments.schemas import (
    ControlSettings,
    EnvironmentSnapshot,
    PracticeResult,
    QualityInput,
    SessionCreate,
    SessionView,
    validate_settings,
)
from tests.unit.esports_samples import sample_raw

SUITES = ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba")


def legacy_suite(suite_id: str):
    return get_suite(suite_id, catalog_version=f"{suite_id}-v2")


def legacy_task(suite_id: str, test_id: str, difficulty: int = 3):
    return get_task(
        suite_id,
        test_id,
        difficulty,
        catalog_version=f"{suite_id}-v2",
    )


@pytest.mark.parametrize("invalid", ["different_suite", "unregistered_parameters"])
def test_session_frozen_suite_config_must_be_registered_and_match_session(invalid):
    config = legacy_suite("mobile-fps" if invalid == "different_suite" else "pc-fps").model_dump(
        mode="json"
    )
    if invalid == "unregistered_parameters":
        config["tasks"]["precision"]["params"]["target_diameter_deg"] = 99
    with pytest.raises(ValueError):
        SessionView.model_validate(
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "suite_id": "pc-fps",
                "status": "in_progress",
                "members": ["precision", "tracking", "switching", "recognition"],
                "results": [],
                "report_id": None,
                "suite_config": config,
            }
        )


@pytest.mark.parametrize("suite", SUITES)
@pytest.mark.parametrize("perfect", [True, False])
def test_all_scenes_accept_complete_real_statistics(suite, perfect):
    for test in legacy_suite(suite).test_ids:
        config = legacy_task(suite, test)
        raw = validate_raw(config, sample_raw(suite, test, perfect=perfect))
        validate_schedule(config, raw, 123)


def test_map_preserves_two_complete_channels():
    raw = validate_raw(
        legacy_task("pc-moba", "map-awareness"),
        sample_raw("pc-moba", "map-awareness", perfect=True),
    )
    assert (len(raw.main_hits), len(raw.events)) == (30, 12)


@pytest.mark.parametrize("suite", SUITES)
def test_missing_or_extra_opportunity_is_rejected(suite):
    arrays = {
        "aim": "trials",
        "recognition": "trials",
        "move_fire": "rounds",
        "last_hit": "waves",
        "rhythm": "rounds",
        "dodge": "threats",
        "skill_aim": "trials",
        "map": "events",
    }
    for test in legacy_suite(suite).test_ids:
        raw = sample_raw(suite, test, perfect=True)
        if raw["kind"] == "tracking":
            continue
        field = arrays[raw["kind"]]
        for delta in (-1, 1):
            invalid = copy.deepcopy(raw)
            invalid[field] = (
                invalid[field][:-1] if delta < 0 else invalid[field] + [invalid[field][0]]
            )
            with pytest.raises(ValueError):
                validate_raw(legacy_task(suite, test), invalid)


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -1, "100"])
def test_quality_rejects_nonfinite_coerced_or_negative_numbers(value):
    with pytest.raises(ValidationError):
        QualityInput(
            duration_ms=value,
            max_frame_gap_ms=16,
            long_frame_gap_total_ms=0,
            input_kind="mouse",
            hidden=False,
            interrupted=False,
        )


def test_formal_request_cannot_choose_difficulty_or_members():
    for extra in ({"difficulty": 1}, {"members": ["precision"]}, {"person_id": "other"}):
        with pytest.raises(ValidationError):
            SessionCreate.model_validate(
                {
                    "suite_id": "pc-fps",
                    "operation_key": "11111111-1111-4111-8111-111111111111",
                    **extra,
                }
            )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r.update(shots=True),
        lambda r: r.update(shots=301),
        lambda r: r.update(shots=29),
        lambda r: r.update(wrong_target_shots=1),
        lambda r: r.update(score=100),
        lambda r: r["trials"][0].update(hit=1),
        lambda r: r["trials"][0].update(latency_ms=float("nan")),
        lambda r: r["trials"][0].update(hit=False, latency_ms=100),
    ],
)
def test_aim_rejects_inconsistent_or_untrusted_statistics(mutation):
    raw = sample_raw("pc-fps", "precision", perfect=True)
    mutation(raw)
    with pytest.raises(ValueError):
        validate_raw(legacy_task("pc-fps", "precision"), raw)


def test_recognition_order_is_checked_against_persisted_seed():
    config = legacy_task("pc-fps", "recognition")
    raw = sample_raw("pc-fps", "recognition", perfect=True)
    enemy = next(i for i, t in enumerate(raw["trials"]) if t["target_kind"] == "enemy")
    friend = next(i for i, t in enumerate(raw["trials"]) if t["target_kind"] == "friend")
    raw["trials"][enemy], raw["trials"][friend] = raw["trials"][friend], raw["trials"][enemy]
    valid = validate_raw(config, raw)
    with pytest.raises(ValueError):
        validate_schedule(config, valid, 123)


@pytest.mark.parametrize(
    "suite,test,field,value",
    [
        ("pc-fps", "tracking", "coverage_ms", 60001),
        ("pc-fps", "tracking", "reacquisition_ms", [1]),
        ("pc-moba", "map-awareness", "unprompted_count", 60001),
    ],
)
def test_bounded_aggregates(suite, test, field, value):
    raw = sample_raw(suite, test, perfect=True)
    raw[field] = value
    with pytest.raises(ValueError):
        validate_raw(legacy_task(suite, test), raw)


@pytest.mark.parametrize(
    "suite,test,array,mutation",
    [
        ("pc-fps", "switching", "trials", {"hit": True, "latency_ms": 1500}),
        ("pc-fps", "recognition", "trials", {"shot": False, "hit": True}),
        ("pc-moba", "last-hit", "waves", {"offset_ms": 500}),
        ("pc-moba", "last-hit", "waves", {"early": 4, "late": 0}),
        ("pc-moba", "orb-walk", "rounds", {"cancelled_count": 2}),
        ("mobile-moba", "dodge", "threats", {"response_ms": 2000}),
        ("mobile-moba", "skill-aim", "trials", {"released": False}),
        ("mobile-moba", "skill-aim", "trials", {"hit_latency_ms": 99}),
        ("mobile-moba", "map-awareness", "events", {"response": "missed", "latency_ms": 999}),
    ],
)
def test_each_engine_rejects_inconsistent_events(suite, test, array, mutation):
    raw = sample_raw(suite, test, perfect=True)
    raw[array][0].update(mutation)
    with pytest.raises(ValueError):
        validate_raw(legacy_task(suite, test), raw)


def test_success_before_deadline_and_reached_at_round_end_are_distinct():
    aim = sample_raw("pc-fps", "precision", perfect=True)
    aim["trials"][0]["latency_ms"] = 1999.999
    validate_raw(legacy_task("pc-fps", "precision"), aim)
    aim["trials"][0]["latency_ms"] = 2000
    with pytest.raises(ValueError):
        validate_raw(legacy_task("pc-fps", "precision"), aim)
    rhythm = sample_raw("pc-moba", "orb-walk", perfect=False)
    rhythm["rounds"][0]["reached"] = True
    assert validate_raw(legacy_task("pc-moba", "orb-walk"), rhythm).rounds[0].reached


def test_practice_raw_respects_registered_window_not_formal_window():
    raw = sample_raw("pc-moba", "map-awareness", perfect=False)
    with pytest.raises(ValueError):
        validate_raw(legacy_task("pc-moba", "map-awareness", 1), raw)
    for event in raw["events"]:
        event["latency_ms"] = 1400
    validate_raw(legacy_task("pc-moba", "map-awareness", 1), raw)


def test_practice_score_and_grade_are_always_null():
    assert PracticeResult(metrics={}, quality_flags=()).score is None
    for field, value in (("score", 100), ("grade", "exceptional")):
        with pytest.raises(ValidationError):
            PracticeResult.model_validate({"metrics": {}, "quality_flags": [], field: value})


def _touch_settings():
    return {
        "sensitivity": 1,
        "input_kind": "touch",
        "controls": [
            {"id": "move", "kind": "joystick", "x": 0.15, "y": 0.75, "width": 120, "height": 120},
            {"id": "fire", "kind": "button", "x": 0.85, "y": 0.75, "width": 64, "height": 64},
        ],
    }


def _phone_environment():
    return EnvironmentSnapshot(
        input_kind="touch",
        is_phone=True,
        touch_capable=True,
        width_css_px=1000,
        height_css_px=562.5,
        device_pixel_ratio=2,
        pointer_lock_supported=False,
        pointer_lock_active=False,
        raw_mouse_requested=False,
        raw_mouse_confirmed=False,
        webgl2_supported=True,
    )


def test_control_layout_uses_ratio_centers_and_css_pixel_diameters():
    config = legacy_task("mobile-fps", "move-fire")
    settings = ControlSettings.model_validate(_touch_settings())
    validate_settings(config, settings, _phone_environment())
    for x in (0, 0.15):
        invalid = _touch_settings()
        invalid["controls"][1]["x"] = x
        with pytest.raises(ValueError):
            validate_settings(config, ControlSettings.model_validate(invalid), _phone_environment())


def test_unrelated_controls_and_mouse_modes_are_rejected_for_suite():
    with pytest.raises(ValueError):
        validate_settings(
            legacy_task("pc-moba", "dodge"),
            ControlSettings(input_kind="mouse", mouse_mode="raw"),
        )
    with pytest.raises(ValueError):
        validate_settings(
            legacy_task("mobile-moba", "dodge"),
            ControlSettings.model_validate(_touch_settings()),
        )
    with pytest.raises(ValueError):
        validate_settings(legacy_task("pc-fps", "precision"), ControlSettings(input_kind="mouse"))


@pytest.mark.parametrize("value", [0.49, 2.01, 1.03, True, float("inf")])
def test_settings_sensitivity_is_finite_registered_increment(value):
    with pytest.raises(ValidationError):
        ControlSettings(input_kind="mouse", sensitivity=value)


@pytest.mark.parametrize("value", [10**400, -(10**400)], ids=["positive", "negative"])
@pytest.mark.parametrize("field", ["duration_ms", "max_frame_gap_ms", "long_frame_gap_total_ms"])
def test_oversized_json_numbers_are_validation_errors_not_internal_exceptions(value, field):
    payload = {
        "duration_ms": 60000,
        "max_frame_gap_ms": 16,
        "long_frame_gap_total_ms": 0,
        "input_kind": "mouse",
        "hidden": False,
        "interrupted": False,
    }
    payload[field] = value
    with pytest.raises(ValidationError):
        QualityInput.model_validate_json(json.dumps(payload))
    with pytest.raises(ValidationError):
        ControlSettings.model_validate_json(
            json.dumps({"input_kind": "mouse", "sensitivity": value})
        )
