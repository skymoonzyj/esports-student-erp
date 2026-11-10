import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.esports_assessments.catalog import get_suite, get_task, validate_raw
from app.esports_assessments.config_schemas import TaskConfig
from app.esports_assessments.lifecycle import operation_digest
from app.esports_assessments.schemas import QualityInput, SessionCreate
from app.esports_assessments.scoring import score_result


@pytest.mark.parametrize(
    ("mode", "damage", "interval", "windup", "distance", "speed"),
    [
        ("melee", 70, 1100, 300, 55, 0),
        ("ranged", 54, 1250, 450, 210, 500),
    ],
)
def test_frozen_lane_config_preserves_selected_character(
    mode, damage, interval, windup, distance, speed
):
    config = get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4", attack_mode=mode)
    assert config.duration_ms == 300000
    assert config.params.model_dump() == {
        "kind": "lane_last_hit",
        "attack_mode": mode,
        "logical_width": 1000,
        "logical_height": 562.5,
        "player_radius": 12,
        "player_speed": 250,
        "attack_range": distance,
        "wave_interval_ms": 30000,
        "wave_count": 10,
        "melee_per_wave": 3,
        "ranged_per_wave": 2,
        "attack_damage": damage,
        "attack_interval_ms": interval,
        "attack_windup_ms": windup,
        "projectile_speed": speed,
    }
    assert get_suite("pc-moba", catalog_version="pc-moba-v4", attack_mode=mode).test_ids == (
        "last-hit",
    )
    assert validate_raw(
        config, {"kind": "lane_last_hit", "deaths": [], "attacks": 0, "cancelled_attacks": 0}
    )
    for field, value in [
        ("duration_ms", 60000),
        ("params", {**config.params.model_dump(), "attack_damage": 999}),
    ]:
        with pytest.raises(ValidationError):
            TaskConfig.model_validate({**config.model_dump(), field: value})


def test_historical_moba_and_fps_duration_remain_one_minute():
    for version in ("v1", "v2", "v3"):
        assert len(get_suite("pc-moba", catalog_version=f"pc-moba-{version}").test_ids) == 4
        assert (
            get_task("pc-moba", "last-hit", catalog_version=f"pc-moba-{version}").duration_ms
            == 60000
        )
    assert get_task("pc-fps", "gridshot").duration_ms == 60000


@pytest.mark.parametrize(
    "deaths,attacks",
    [
        ([{"unit_id": 5, "at_ms": 29999, "by_player": True}], 1),
        ([{"unit_id": 0, "at_ms": 300000, "by_player": True}], 1),
        ([{"unit_id": 0, "at_ms": 1000, "by_player": 1}], 1),
        ([{"unit_id": 0, "at_ms": 1000, "by_player": True}] * 2, 2),
        (
            [
                {"unit_id": 1, "at_ms": 1000, "by_player": True},
                {"unit_id": 0, "at_ms": 1000, "by_player": True},
            ],
            2,
        ),
        ([{"unit_id": 0, "at_ms": 1000, "by_player": True}], 0),
        ([], 274),
    ],
)
def test_lane_rejects_impossible_death_or_attack_statistics(deaths, attacks):
    config = get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4")
    with pytest.raises(ValueError):
        validate_raw(
            config,
            {"kind": "lane_last_hit", "deaths": deaths, "attacks": attacks, "cancelled_attacks": 0},
        )


@pytest.mark.parametrize("count,score", [(0, 0), (1, 2), (25, 50), (50, 100)])
def test_lane_score_uses_all_fifty_opportunities(count, score):
    config = get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4")
    raw = validate_raw(
        config,
        {
            "kind": "lane_last_hit",
            "deaths": [
                {"unit_id": i, "at_ms": i // 5 * 30000 + 1000 + i % 5, "by_player": i < count}
                for i in range(50)
            ],
            "attacks": 50,
            "cancelled_attacks": 2,
        },
    )
    result = score_result(
        config,
        raw,
        QualityInput(
            duration_ms=300000,
            max_frame_gap_ms=16,
            long_frame_gap_total_ms=0,
            input_kind="mouse",
            hidden=False,
            interrupted=False,
        ),
    )
    assert result.score == score
    assert result.metrics["last_hits"] == count
    assert result.metrics["missed"] == 50 - count
    assert result.metrics["unresolved"] == 0
    assert result.metrics["last_hits_per_minute"] == count / 5
    assert sum(result.metrics[f"minute_{i}_last_hits"] for i in range(1, 6)) == count


def test_optional_attack_mode_does_not_change_historical_operation_digest():
    values = {"operation_key": "00000000-0000-0000-0000-000000000001", "suite_id": "pc-moba"}
    assert operation_digest(
        "formal", "create_session", None, SessionCreate(**values)
    ) == operation_digest("formal", "create_session", None, values)


def test_shared_lane_fixture_matches_server_catalog_exactly():
    path = (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-moba-v4.json"
    )
    assert json.loads(path.read_text()) == get_suite(
        "pc-moba", catalog_version="pc-moba-v4"
    ).model_dump(mode="json")


def test_lane_raw_cancellation_boundary_and_unresolved_not_missed():
    config = get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4")
    raw = {"kind": "lane_last_hit", "deaths": [], "attacks": 0, "cancelled_attacks": 15000}
    parsed = validate_raw(config, raw)
    assert parsed.cancelled_attacks == 15000
    with pytest.raises(ValueError):
        validate_raw(config, {**raw, "cancelled_attacks": 15001})
    from app.esports_assessments.scoring import summarize_metrics

    metrics = summarize_metrics(config, parsed)
    assert (metrics["last_hit_rate"], metrics["missed"], metrics["unresolved"]) == (0, 0, 50)


def test_single_member_does_not_relax_historical_suite_members():
    from app.esports_assessments.config_schemas import SuiteConfig

    old = get_suite("pc-moba", catalog_version="pc-moba-v3").model_dump(mode="json")
    with pytest.raises(ValueError):
        SuiteConfig.model_validate(
            {**old, "test_ids": ["last-hit"], "tasks": {"last-hit": old["tasks"]["last-hit"]}}
        )


def test_lane_minutes_are_half_open_and_final_alive_units_are_unresolved():
    from app.esports_assessments.scoring import summarize_metrics

    config = get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4")
    raw = validate_raw(
        config,
        {
            "kind": "lane_last_hit",
            "deaths": [
                {"unit_id": 0, "at_ms": 59999, "by_player": True},
                {"unit_id": 1, "at_ms": 60000, "by_player": True},
                {"unit_id": 2, "at_ms": 299999, "by_player": True},
            ],
            "attacks": 3,
            "cancelled_attacks": 0,
        },
    )
    metrics = summarize_metrics(config, raw)
    assert [metrics[f"minute_{i}_last_hits"] for i in range(1, 6)] == [1, 1, 0, 0, 1]
    assert (
        metrics["missed"],
        metrics["unresolved"],
        metrics["last_hit_rate"],
        metrics["opportunity_completion"],
    ) == (0, 47, 1, 0.06)


@pytest.mark.parametrize(
    "suite,test,version",
    [("pc-fps", "gridshot", "pc-fps-v5"), ("pc-moba", "last-hit", "pc-moba-v3")],
)
def test_character_selection_does_not_apply_to_historical_or_other_suites(suite, test, version):
    with pytest.raises(ValueError, match="不支持"):
        get_task(suite, test, catalog_version=version, attack_mode="melee")
