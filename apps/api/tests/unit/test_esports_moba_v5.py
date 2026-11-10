import json
from pathlib import Path

import pytest

from app.esports_assessments.catalog import get_suite, get_task, validate_raw
from app.esports_assessments.config_schemas import TaskConfig
from app.esports_assessments.scoring import score_result, summarize_metrics
from tests.esports_helpers import quality


def raw_v5(**updates):
    return {
        "kind": "lane_last_hit_towers",
        "deaths": [],
        "attacks": 0,
        "cancelled_attacks": 0,
        "hero_death_times": [],
        "hero_damage_taken": 0,
        **updates,
    }


def test_current_catalog_freezes_seventy_five_opportunities_and_four_towers():
    config = get_task("pc-moba", "last-hit")
    assert config.catalog_version == "pc-moba-v5"
    assert config.scoring_version == config.report_version == "pc-moba-v5"
    assert config.duration_ms == 300000
    p = config.params.model_dump(mode="json")
    assert (p["kind"], p["wave_interval_ms"], p["wave_count"]) == (
        "lane_last_hit_towers",
        20000,
        15,
    )
    assert {
        key: p[key]
        for key in (
            "hero_health",
            "respawn_ms",
            "tower_damage",
            "tower_interval_ms",
            "tower_windup_ms",
            "tower_range",
            "tower_projectile_speed",
        )
    } == {
        "hero_health": 1000,
        "respawn_ms": 5000,
        "tower_damage": 180,
        "tower_interval_ms": 1200,
        "tower_windup_ms": 300,
        "tower_range": 240,
        "tower_projectile_speed": 650,
    }
    assert p["tower_positions"] == [
        {"id": "ally-rear", "team": "ally", "x": 70, "y": 162.5},
        {"id": "ally-front", "team": "ally", "x": 280, "y": 400},
        {"id": "enemy-front", "team": "enemy", "x": 720, "y": 162.5},
        {"id": "enemy-rear", "team": "enemy", "x": 930, "y": 400},
    ]


def test_v4_complete_serialized_fixture_remains_identical():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-moba-v4.json"
    )
    assert get_suite("pc-moba", catalog_version="pc-moba-v4").model_dump(mode="json") == json.loads(
        fixture.read_text()
    )


def test_shared_v5_fixture_and_mutation_rejection_match_frozen_server_rules():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-moba-v5.json"
    )
    assert get_suite("pc-moba").model_dump(mode="json") == json.loads(fixture.read_text())
    config = get_task("pc-moba", "last-hit").model_dump(mode="json")
    for field, value in (("tower_damage", 181), ("wave_interval_ms", 30000), ("hero_health", 999)):
        with pytest.raises(ValueError):
            TaskConfig.model_validate({**config, "params": {**config["params"], field: value}})
    positions = config["params"]["tower_positions"]
    with pytest.raises(ValueError):
        TaskConfig.model_validate(
            {**config, "params": {**config["params"], "tower_positions": positions[::-1]}}
        )


@pytest.mark.parametrize("unit_id,at_ms", [(50, 200000), (5, 20000)])
def test_v4_does_not_accept_v5_ids_or_twenty_second_births(unit_id, at_ms):
    config = get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4")
    with pytest.raises(ValueError):
        validate_raw(
            config,
            {
                "kind": "lane_last_hit",
                "deaths": [{"unit_id": unit_id, "at_ms": at_ms, "by_player": False}],
                "attacks": 0,
                "cancelled_attacks": 0,
            },
        )


def test_last_wave_birth_and_half_open_end_are_enforced():
    config = get_task("pc-moba", "last-hit")
    assert validate_raw(
        config, raw_v5(deaths=[{"unit_id": 74, "at_ms": 280000, "by_player": False}])
    )
    for at_ms in (279999, 300000, 280000.5, True):
        with pytest.raises(ValueError):
            validate_raw(
                config, raw_v5(deaths=[{"unit_id": 74, "at_ms": at_ms, "by_player": False}])
            )


@pytest.mark.parametrize("hits,score", [(0, 0), (2, 3), (50, 67), (75, 100)])
def test_v5_score_and_metrics_use_frozen_seventy_five_count(hits, score):
    config = get_task("pc-moba", "last-hit")
    raw = validate_raw(
        config,
        raw_v5(
            deaths=[
                {"unit_id": i, "at_ms": i // 5 * 20000 + 1000 + i % 5, "by_player": i < hits}
                for i in range(75)
            ],
            attacks=75,
            hero_death_times=[10000, 15000],
            hero_damage_taken=2170,
        ),
    )
    scored = score_result(config, raw, quality("pc-moba", duration_ms=300000))
    assert scored.score == score
    assert scored.metrics["missed"] == 75 - hits
    assert scored.metrics["unresolved"] == 0
    assert scored.metrics["hero_deaths"] == 2
    assert scored.metrics["hero_damage_taken"] == 2170
    assert sum(scored.metrics[f"minute_{i}_last_hits"] for i in range(1, 6)) == hits
    assert summarize_metrics(config, validate_raw(config, raw_v5()))["unresolved"] == 75


@pytest.mark.parametrize(
    "updates",
    [
        {"hero_death_times": [10000], "hero_damage_taken": 999},
        {"hero_death_times": [], "hero_damage_taken": 1000},
        {"hero_death_times": [10000, 14999], "hero_damage_taken": 2000},
        {"hero_death_times": [10000, 10000], "hero_damage_taken": 2000},
        {"hero_death_times": [300000], "hero_damage_taken": 1000},
        {"hero_death_times": [True], "hero_damage_taken": 1000},
        {"hero_damage_taken": 1.5},
        {"hero_damage_taken": True},
        {"hero_damage_taken": 90001},
        {"cancelled_attacks": 15001},
        {"attacks": 274},
        {"deaths": [{"unit_id": 0, "at_ms": 1000, "by_player": True}]},
        {"deaths": [{"unit_id": 0, "at_ms": 1000, "by_player": 1}]},
        {"deaths": [{"unit_id": 0, "at_ms": 1000, "by_player": False}] * 2},
    ],
)
def test_v5_rejects_inconsistent_life_or_attack_statistics(updates):
    with pytest.raises(ValueError):
        validate_raw(get_task("pc-moba", "last-hit"), raw_v5(**updates))


def test_v5_life_statistics_do_not_leak_into_v4():
    with pytest.raises(ValueError):
        validate_raw(
            get_task("pc-moba", "last-hit", catalog_version="pc-moba-v4"),
            {**raw_v5(), "kind": "lane_last_hit"},
        )


def test_v5_audit_identity_is_allowlisted_without_raw_statistics():
    from app.audit.service import AuditService

    metadata = {"catalog_version": "pc-moba-v5", "scoring_version": "pc-moba-v5"}
    AuditService.validate_metadata(metadata)
    assert AuditService.public_metadata(metadata) == metadata
    assert AuditService.public_metadata({"hero_damage_taken": 1000}) == {}
