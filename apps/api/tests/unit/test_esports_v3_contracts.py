"""Frozen v3 catalog/raw contracts and byte-stable legacy submissions."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from importlib import import_module
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.esports_assessments import catalog
from app.esports_assessments.catalog import get_task, validate_raw, validate_schedule
from app.esports_assessments.config_schemas import TaskConfig
from app.esports_assessments.lifecycle import operation_digest
from tests.esports_helpers import result_request

SUITES = ("pc-fps", "mobile-fps", "pc-moba", "mobile-moba")
TARGET = UUID("22222222-2222-4222-8222-222222222222")
KEY = UUID("11111111-1111-4111-8111-111111111111")


def test_v3_moving_result_requires_new_kind():
    config = get_task("mobile-fps", "move-fire", catalog_version="mobile-fps-v3")
    assert config.params.kind == "move_fire_v3"
    assert config.scoring_version == config.report_version == "mobile-fps-v2"
    with pytest.raises(ValueError):
        validate_raw(
            config,
            {
                "kind": "move_fire",
                "rounds": [{"hit": True, "reached": True} for _ in range(20)],
                "shots": 20,
            },
        )


@pytest.mark.parametrize("suite_id", SUITES)
@pytest.mark.parametrize("difficulty", [1, 2, 3, 4, 5])
def test_all_v3_configs_have_exact_registered_identity_and_parameters(suite_id, difficulty):
    rules = import_module("app.esports_assessments.rule_versions")
    warning_ms = {
        "pc-moba": (1200, 1100, 1000, 950, 900),
        "mobile-moba": (1250, 1200, 1100, 1050, 1000),
    }
    distance = (0.7, 0.85, 1, 1.1, 1.2)[difficulty - 1]
    size = (1.5, 1.25, 1, 0.85, 0.7)[difficulty - 1]
    for test_id in catalog.MEMBERS[suite_id]:
        config = get_task(
            suite_id,
            test_id,
            difficulty,
            catalog_version=f"{suite_id}-v3",
        )
        identity = rules.registered_rule(suite_id, test_id, f"{suite_id}-v3")
        assert config.catalog_version == config.engine_version == f"{suite_id}-v3"
        assert (config.params.kind, config.scoring_version, config.report_version) == (
            identity.raw_kind,
            identity.scoring_version,
            identity.report_version,
        )
        if test_id == "move-fire":
            assert config.params.model_dump(mode="json") == {
                "horizontal_fov_deg": 90.0,
                "pitch_limit_deg": 70.0,
                "fire_interval_ms": 200.0,
                "kind": "move_fire_v3",
                "opportunity_count": 20,
                "window_ms": 3000.0,
                "target_diameter_deg": 6 * size,
                "checkpoint_distance_m": 1.2 * distance,
                "checkpoint_tolerance_m": 0.35,
                "player_speed_m_s": 2.0,
                "target_offset_min_deg": 12 * distance,
                "target_offset_max_deg": 20 * distance,
                "min_moving_ms": 100.0,
                "min_moving_speed_m_s": 1.0,
            }
        elif test_id == "dodge":
            assert config.params.model_dump(mode="json") == {
                "logical_width": 1000.0,
                "logical_height": 562.5,
                "player_radius": 12.0,
                "player_speed": 180.0,
                "attack_range": 180.0,
                "kind": "dodge_choice",
                "opportunity_count": 30,
                "window_ms": 2000.0,
                "warning_ms": float(warning_ms[suite_id][difficulty - 1]),
                "safe_radius": 32.0,
                "candidate_offset": 20.0,
                "candidate_distance": 100.0,
                "warning_start_min_ms": 100.0,
                "warning_start_max_ms": 600.0,
                "burst_ms": 150.0,
            }
        else:
            legacy = get_task(
                suite_id,
                test_id,
                difficulty,
                catalog_version=f"{suite_id}-v2",
            )
            assert config.params == legacy.params


def test_registered_rule_is_frozen_and_unknown_versions_never_fall_back():
    rules = import_module("app.esports_assessments.rule_versions")
    identity = rules.registered_rule("pc-fps", "switching", "pc-fps-v3")
    assert identity == rules.RuleIdentity("aim", "pc-fps-v2", "pc-fps-v2")
    with pytest.raises(FrozenInstanceError):
        identity.raw_kind = "tracking"
    for args in (
        ("pc-fps", "alien", "pc-fps-v3"),
        ("pc-fps", "switching", "pc-fps-v4"),
        ("pc-fps", "switching", "mobile-fps-v3"),
    ):
        with pytest.raises(ValueError):
            rules.registered_rule(*args)


@pytest.mark.parametrize(
    "suite_id,test_id,legacy_kind,v3_kind",
    [
        ("mobile-fps", "move-fire", "move_fire", "move_fire_v3"),
        ("pc-moba", "dodge", "dodge", "dodge_choice"),
        ("mobile-moba", "dodge", "dodge", "dodge_choice"),
    ],
)
def test_new_and_legacy_raw_kinds_cannot_cross_versions(suite_id, test_id, legacy_kind, v3_kind):
    samples = import_module("tests.unit.esports_v3_samples")
    legacy = get_task(suite_id, test_id, catalog_version=f"{suite_id}-v2")
    current = get_task(suite_id, test_id, catalog_version=f"{suite_id}-v3")
    legacy_raw = result_request(suite_id, test_id, seed=123, key=KEY).raw.model_dump(mode="json")
    current_raw = samples.sample_raw_v3(current, perfect=True)
    assert legacy_raw["kind"] == legacy_kind
    assert current_raw["kind"] == v3_kind
    with pytest.raises(ValueError):
        validate_raw(current, legacy_raw)
    with pytest.raises(ValueError):
        validate_raw(legacy, current_raw)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["rounds"][0].pop("hit_latency_ms"),
        lambda r: r["rounds"][0].pop("hit_speed_m_s"),
        lambda r: r["rounds"][0].pop("pre_hit_moving_ms"),
        lambda r: r["rounds"][0].update(extra=1),
        lambda r: r["rounds"][0].update(hit_latency_ms=float("nan")),
        lambda r: r["rounds"][0].update(hit_speed_m_s=True),
        lambda r: r["rounds"][0].update(pre_hit_moving_ms=99),
        lambda r: r["rounds"][0].update(pre_hit_moving_ms=501, hit_latency_ms=500),
        lambda r: r["rounds"][0].update(hit_latency_ms=3000),
        lambda r: r["rounds"][0].update(hit_speed_m_s=0.999),
        lambda r: r["rounds"][0].update(hit_speed_m_s=2.001),
        lambda r: r["rounds"][0].update(hit=False),
    ],
)
def test_move_fire_v3_requires_complete_consistent_finite_moving_hits(mutation):
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task("mobile-fps", "move-fire", catalog_version="mobile-fps-v3")
    raw = samples.sample_raw_v3(config, perfect=True)
    mutation(raw)
    with pytest.raises((ValueError, ValidationError)):
        validate_raw(config, raw)


def test_move_fire_v3_requires_null_details_for_a_miss_and_enough_shots_for_hits():
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task("mobile-fps", "move-fire", catalog_version="mobile-fps-v3")
    miss = samples.sample_raw_v3(config, perfect=False)
    miss["rounds"][0]["hit_latency_ms"] = 100
    with pytest.raises(ValueError):
        validate_raw(config, miss)
    hit = samples.sample_raw_v3(config, perfect=True)
    hit["shots"] = 19
    with pytest.raises(ValueError):
        validate_raw(config, hit)


@pytest.mark.parametrize(
    "first,second",
    [
        ({"hit": False, "latency_ms": 1500}, {"hit": True, "latency_ms": 200}),
        ({"hit": True, "latency_ms": 100}, {"hit": True, "latency_ms": 199}),
    ],
)
def test_v3_switching_rejects_impossible_second_target_results(first, second):
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task("pc-fps", "switching", catalog_version="pc-fps-v3")
    raw = samples.sample_raw_v3(config, perfect=True)
    raw["trials"][0] = first
    raw["trials"][1] = second
    with pytest.raises(ValueError):
        validate_raw(config, raw)


SCHEDULE_GOLDENS = {
    0: (
        (1, 103, 1103.0, 1253.0),
        (1, 2356, 3356.0, 3506.0),
        (3, 4212, 5212.0, 5362.0),
        (3, 6363, 7363.0, 7513.0),
        (2, 8456, 9456.0, 9606.0),
        (2, 10594, 11594.0, 11744.0),
        (3, 12533, 13533.0, 13683.0),
        (2, 14409, 15409.0, 15559.0),
        (2, 16109, 17109.0, 17259.0),
        (0, 18250, 19250.0, 19400.0),
        (2, 20357, 21357.0, 21507.0),
        (3, 22102, 23102.0, 23252.0),
        (1, 24461, 25461.0, 25611.0),
        (3, 26440, 27440.0, 27590.0),
        (1, 28238, 29238.0, 29388.0),
        (0, 30198, 31198.0, 31348.0),
        (3, 32154, 33154.0, 33304.0),
        (1, 34369, 35369.0, 35519.0),
        (3, 36141, 37141.0, 37291.0),
        (3, 38135, 39135.0, 39285.0),
        (3, 40416, 41416.0, 41566.0),
        (1, 42394, 43394.0, 43544.0),
        (0, 44583, 45583.0, 45733.0),
        (2, 46479, 47479.0, 47629.0),
        (2, 48497, 49497.0, 49647.0),
        (2, 50480, 51480.0, 51630.0),
        (3, 52541, 53541.0, 53691.0),
        (3, 54154, 55154.0, 55304.0),
        (0, 56362, 57362.0, 57512.0),
        (3, 58405, 59405.0, 59555.0),
    ),
    123: (
        (2, 418, 1418.0, 1568.0),
        (0, 2154, 3154.0, 3304.0),
        (2, 4196, 5196.0, 5346.0),
        (2, 6187, 7187.0, 7337.0),
        (3, 8417, 9417.0, 9567.0),
        (1, 10442, 11442.0, 11592.0),
        (1, 12461, 13461.0, 13611.0),
        (2, 14510, 15510.0, 15660.0),
        (1, 16229, 17229.0, 17379.0),
        (0, 18384, 19384.0, 19534.0),
        (0, 20100, 21100.0, 21250.0),
        (3, 22440, 23440.0, 23590.0),
        (1, 24237, 25237.0, 25387.0),
        (0, 26144, 27144.0, 27294.0),
        (3, 28382, 29382.0, 29532.0),
        (2, 30541, 31541.0, 31691.0),
        (0, 32588, 33588.0, 33738.0),
        (3, 34425, 35425.0, 35575.0),
        (1, 36307, 37307.0, 37457.0),
        (3, 38229, 39229.0, 39379.0),
        (3, 40344, 41344.0, 41494.0),
        (2, 42391, 43391.0, 43541.0),
        (2, 44444, 45444.0, 45594.0),
        (3, 46516, 47516.0, 47666.0),
        (3, 48109, 49109.0, 49259.0),
        (1, 50352, 51352.0, 51502.0),
        (3, 52522, 53522.0, 53672.0),
        (1, 54467, 55467.0, 55617.0),
        (1, 56521, 57521.0, 57671.0),
        (3, 58129, 59129.0, 59279.0),
    ),
    4294967295: (
        (3, 577, 1577.0, 1727.0),
        (2, 2283, 3283.0, 3433.0),
        (0, 4141, 5141.0, 5291.0),
        (3, 6244, 7244.0, 7394.0),
        (3, 8431, 9431.0, 9581.0),
        (0, 10309, 11309.0, 11459.0),
        (0, 12496, 13496.0, 13646.0),
        (1, 14507, 15507.0, 15657.0),
        (2, 16527, 17527.0, 17677.0),
        (0, 18195, 19195.0, 19345.0),
        (2, 20313, 21313.0, 21463.0),
        (1, 22107, 23107.0, 23257.0),
        (1, 24445, 25445.0, 25595.0),
        (0, 26250, 27250.0, 27400.0),
        (2, 28208, 29208.0, 29358.0),
        (2, 30403, 31403.0, 31553.0),
        (2, 32458, 33458.0, 33608.0),
        (1, 34509, 35509.0, 35659.0),
        (0, 36410, 37410.0, 37560.0),
        (0, 38302, 39302.0, 39452.0),
        (2, 40255, 41255.0, 41405.0),
        (3, 42165, 43165.0, 43315.0),
        (0, 44232, 45232.0, 45382.0),
        (3, 46112, 47112.0, 47262.0),
        (3, 48140, 49140.0, 49290.0),
        (2, 50529, 51529.0, 51679.0),
        (3, 52343, 53343.0, 53493.0),
        (2, 54338, 55338.0, 55488.0),
        (1, 56545, 57545.0, 57695.0),
        (2, 58333, 59333.0, 59483.0),
    ),
}


@pytest.mark.parametrize("seed", [0, 123, 4294967295])
def test_dodge_choice_schedule_has_literal_cross_language_golden_vectors(seed):
    events = catalog.dodge_choice_schedule(seed, 1000)
    assert (
        tuple(
            (
                event["safe_option_id"],
                event["warning_at_ms"],
                event["activation_at_ms"],
                event["resolved_at_ms"],
            )
            for event in events
        )
        == SCHEDULE_GOLDENS[seed]
    )


@pytest.mark.parametrize(
    "field,replacement",
    [
        ("safe_option_id", 3),
        ("warning_at_ms", 419),
        ("activation_at_ms", 1419),
        ("resolved_at_ms", 1569),
    ],
)
def test_dodge_choice_schedule_rejects_each_client_event_mismatch(field, replacement):
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task("pc-moba", "dodge", catalog_version="pc-moba-v3")
    raw = samples.sample_raw_v3(config, perfect=True, seed=123)
    raw["rounds"][0][field] = replacement
    parsed = validate_raw(config, raw)
    with pytest.raises(ValueError):
        validate_schedule(config, parsed, 123)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda r: r["rounds"][0].pop("safe_entry_latency_ms"),
        lambda r: r["rounds"][0].update(extra=1),
        lambda r: r["rounds"][0].update(safe_option_id=True),
        lambda r: r["rounds"][0].update(activation_at_ms=float("nan")),
        lambda r: r["rounds"][0].update(safe_entry_latency_ms=None),
        lambda r: r["rounds"][0].update(safe_entry_latency_ms=1001),
    ],
)
def test_dodge_choice_rejects_missing_extra_nonfinite_and_inconsistent_fields(mutation):
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task("pc-moba", "dodge", catalog_version="pc-moba-v3")
    raw = samples.sample_raw_v3(config, perfect=True, seed=123)
    mutation(raw)
    with pytest.raises((ValueError, ValidationError)):
        validate_raw(config, raw)


@pytest.mark.parametrize(
    "suite_id,test_id,array_name",
    [
        ("mobile-fps", "move-fire", "rounds"),
        ("pc-moba", "dodge", "rounds"),
    ],
)
@pytest.mark.parametrize("delta", [-1, 1])
def test_new_raw_requires_exact_round_count(suite_id, test_id, array_name, delta):
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task(suite_id, test_id, catalog_version=f"{suite_id}-v3")
    raw = samples.sample_raw_v3(config, perfect=True)
    raw[array_name] = (
        raw[array_name][:-1] if delta < 0 else [*raw[array_name], copy.deepcopy(raw[array_name][0])]
    )
    with pytest.raises(ValueError):
        validate_raw(config, raw)


def test_mobile_dodge_allows_final_resolution_exactly_at_sixty_seconds():
    samples = import_module("tests.unit.esports_v3_samples")
    config = get_task("mobile-moba", "dodge", 1, catalog_version="mobile-moba-v3")
    raw = samples.sample_raw_v3(config, perfect=True, seed=62)
    assert raw["rounds"][-1]["resolved_at_ms"] == 60000
    parsed = validate_raw(config, raw)
    validate_schedule(config, parsed, 62)


def test_explicit_v3_result_helper_uses_new_raw_without_changing_legacy_calls():
    legacy = result_request("mobile-fps", "move-fire", seed=123, key=KEY)
    current = result_request(
        "mobile-fps",
        "move-fire",
        seed=123,
        key=KEY,
        catalog_version="mobile-fps-v3",
    )
    assert legacy.raw.kind == "move_fire"
    assert current.raw.kind == "move_fire_v3"


@pytest.mark.parametrize(
    "catalog_version",
    ["", "unknown", "mobile-fps-v4", "pc-fps-v3"],
)
def test_result_helper_rejects_unknown_future_and_cross_suite_versions(catalog_version):
    with pytest.raises(ValueError):
        result_request(
            "mobile-fps",
            "move-fire",
            seed=123,
            key=KEY,
            catalog_version=catalog_version,
        )


@pytest.mark.parametrize("catalog_version", ["mobile-fps-v1", "mobile-fps-v2"])
def test_explicit_legacy_catalog_requests_remain_byte_identical(catalog_version):
    omitted = result_request("mobile-fps", "move-fire", seed=123, key=KEY)
    explicit = result_request(
        "mobile-fps",
        "move-fire",
        seed=123,
        key=KEY,
        catalog_version=catalog_version,
    )
    assert explicit.model_dump_json() == omitted.model_dump_json()
    assert operation_digest("formal", "submit_result", TARGET, explicit) == operation_digest(
        "formal", "submit_result", TARGET, omitted
    )


LEGACY_SUBMISSIONS = {
    ("pc-fps", "switching"): (
        (
            '{"operation_key":"11111111-1111-4111-8111-111111111111","raw":{"kind":"aim",'
            '"trials":[{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0},'
            '{"hit":true,"latency_ms":100.0},{"hit":true,"latency_ms":100.0}],'
            '"shots":40,"wrong_target_shots":0},"quality":{"duration_ms":60000.0,'
            '"max_frame_gap_ms":16.0,"long_frame_gap_total_ms":0.0,"input_kind":"mouse",'
            '"hidden":false,"interrupted":false}}'
        ),
        "dbecd36cd349c968e35703d29674441866c89b51967cd98dfea39da39e5c1696",
    ),
    ("mobile-fps", "move-fire"): (
        (
            '{"operation_key":"11111111-1111-4111-8111-111111111111","raw":{"kind":"move_fire",'
            '"rounds":[{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true},'
            '{"hit":true,"reached":true},{"hit":true,"reached":true}],"shots":20},'
            '"quality":{"duration_ms":60000.0,"max_frame_gap_ms":16.0,'
            '"long_frame_gap_total_ms":0.0,"input_kind":"touch","hidden":false,'
            '"interrupted":false}}'
        ),
        "e114d5701fc9805b3c025e7e6970ff8c37d81e8ebd833b3b2b8d0f47e9230efb",
    ),
    ("pc-moba", "dodge"): (
        (
            '{"operation_key":"11111111-1111-4111-8111-111111111111","raw":{"kind":"dodge",'
            '"threats":[{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0},'
            '{"hit":false,"response_ms":100.0},{"hit":false,"response_ms":100.0}]},'
            '"quality":{"duration_ms":60000.0,"max_frame_gap_ms":16.0,'
            '"long_frame_gap_total_ms":0.0,"input_kind":"mouse","hidden":false,'
            '"interrupted":false}}'
        ),
        "f579ede6f8f7dca5c97cd2bb2581195afbd4cf904d025a9da9f6535ae7ea13bd",
    ),
}


@pytest.mark.parametrize("suite_id,test_id", LEGACY_SUBMISSIONS)
def test_legacy_submission_json_and_operation_digest_remain_byte_stable(suite_id, test_id):
    expected_json, expected_digest = LEGACY_SUBMISSIONS[(suite_id, test_id)]
    request = result_request(suite_id, test_id, seed=123, key=KEY)
    assert request.model_dump_json() == expected_json
    assert operation_digest("formal", "submit_result", TARGET, request) == expected_digest


def test_registered_v3_params_cannot_be_mutated_under_the_same_identity():
    for suite_id, test_id, field in (
        ("mobile-fps", "move-fire", "min_moving_ms"),
        ("pc-moba", "dodge", "safe_radius"),
    ):
        payload = get_task(suite_id, test_id, catalog_version=f"{suite_id}-v3").model_dump(
            mode="json"
        )
        payload["params"][field] += 1
        with pytest.raises(ValidationError):
            TaskConfig.model_validate(payload)


def test_v3_samples_validate_for_every_suite_task_and_difficulty():
    samples = import_module("tests.unit.esports_v3_samples")
    for suite_id in SUITES:
        for test_id in catalog.MEMBERS[suite_id]:
            for difficulty in range(1, 6):
                config = get_task(
                    suite_id,
                    test_id,
                    difficulty,
                    catalog_version=f"{suite_id}-v3",
                )
                for perfect in (False, True):
                    raw = validate_raw(
                        config,
                        copy.deepcopy(samples.sample_raw_v3(config, perfect=perfect, seed=123)),
                    )
                    validate_schedule(config, raw, 123)
