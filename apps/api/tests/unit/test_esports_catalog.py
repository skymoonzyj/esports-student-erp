import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.esports_assessments.catalog import get_suite, get_task, next_u32
from app.esports_assessments.config_schemas import SuiteConfig, TaskConfig

MEMBERS = {
    "pc-fps": ("precision", "tracking", "switching", "recognition"),
    "mobile-fps": ("precision", "tracking", "switching", "move-fire"),
    "pc-moba": ("last-hit", "orb-walk", "dodge", "map-awareness"),
    "mobile-moba": ("dodge", "skill-aim", "move-attack", "map-awareness"),
}


@pytest.mark.parametrize("suite, members", MEMBERS.items())
def test_frozen_members_are_the_only_accepted_tasks(suite, members):
    config = get_suite(suite, catalog_version=f"{suite}-v3")
    assert config.test_ids == members
    assert tuple(config.tasks) == members
    for test in members:
        assert get_task(suite, test, catalog_version=f"{suite}-v3").difficulty == 3
    for alien in set(sum(MEMBERS.values(), ())) - set(members):
        with pytest.raises(ValueError):
            get_task(suite, alien, catalog_version=f"{suite}-v3")


@pytest.mark.parametrize(
    "difficulty,size,speed,window,distance",
    [
        (1, 1.5, 0.6, 1.4, 0.7),
        (2, 1.25, 0.8, 1.2, 0.85),
        (3, 1, 1, 1, 1),
        (4, 0.85, 1.15, 0.85, 1.1),
        (5, 0.7, 1.3, 0.7, 1.2),
    ],
)
def test_practice_parameters_apply_relevant_columns_only(difficulty, size, speed, window, distance):
    aim = get_task("pc-fps", "precision", difficulty, catalog_version="pc-fps-v3").params
    assert aim.target_diameter_deg == pytest.approx(3 * size)
    assert aim.angle_distances_deg == pytest.approx((10 * distance, 20 * distance, 30 * distance))
    track = get_task("mobile-fps", "tracking", difficulty).params
    assert track.speed_deg_s == pytest.approx(20 * speed)
    skill = get_task("mobile-moba", "skill-aim", difficulty).params
    assert skill.target_radius == pytest.approx(22 * size)
    assert skill.target_speed == pytest.approx(80 * speed)
    assert skill.projectile_speed == 600
    mini = get_task("pc-moba", "map-awareness", difficulty, catalog_version="pc-moba-v3").params
    assert mini.event_window_ms == pytest.approx(1000 * window)
    assert mini.target_radius == 24
    dodge = get_task("pc-moba", "dodge", difficulty, catalog_version="pc-moba-v2").params
    assert dodge.warning_ms == pytest.approx(400 * window)
    assert dodge.projectile_speed == pytest.approx(400 * speed)
    assert dodge.warning_ms + 240 / dodge.projectile_speed * 1000 <= 2000
    assert (
        get_task("pc-fps", "precision", catalog_version="pc-fps-v3").params.target_diameter_deg == 3
    )


@pytest.mark.parametrize("value", [0, 6, True, 3.0, "3"])
def test_difficulty_is_a_strict_registered_integer(value):
    with pytest.raises(ValueError):
        get_task("pc-fps", "precision", value, catalog_version="pc-fps-v3")


def test_catalog_is_deeply_immutable_and_returns_independent_snapshots():
    one = get_suite("pc-fps", catalog_version="pc-fps-v3")
    two = get_suite("pc-fps", catalog_version="pc-fps-v3")
    assert one is not two
    with pytest.raises(TypeError):
        one.tasks["precision"] = one.tasks["tracking"]
    with pytest.raises(ValidationError):
        one.tasks["precision"].params.target_diameter_deg = 42


@pytest.mark.parametrize(
    "field,value", [("catalog_version", "unknown"), ("duration_ms", True), ("extra", 1)]
)
def test_unknown_or_malformed_config_cannot_be_used(field, value):
    payload = get_task("pc-fps", "precision", catalog_version="pc-fps-v3").model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        TaskConfig.model_validate(payload)


def test_frontend_catalog_snapshot_matches_every_backend_field():
    path = (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-v1.json"
    )
    snapshot = json.loads(path.read_text())
    assert snapshot == {
        suite: get_suite(suite, catalog_version=f"{suite}-v1").model_dump(mode="json")
        for suite in MEMBERS
    }


def test_current_frontend_catalog_matches_every_backend_field():
    path = (
        Path(__file__).resolve().parents[3]
        / "web/src/esportsAssessments/testSupport/catalog-v3.json"
    )
    assert json.loads(path.read_text()) == {
        suite: get_suite(suite, catalog_version=f"{suite}-v3").model_dump(mode="json")
        for suite in MEMBERS
    }


@pytest.mark.parametrize(
    "seed,expected",
    [
        (
            0,
            [
                1085196063,
                2447379481,
                2618286376,
                1701901981,
                265159372,
                1030440423,
                4012273292,
                2080899351,
                4184948546,
                230363598,
            ],
        ),
        (
            123,
            [
                31682556,
                4018661298,
                2101636938,
                3842487452,
                1628673942,
                1875546790,
                2388115785,
                3114636470,
                1834579151,
                1003884751,
            ],
        ),
    ],
)
def test_rng_golden_sequence(seed, expected):
    values = []
    for _ in range(10):
        seed = next_u32(seed)
        values.append(seed)
    assert values == expected


@pytest.mark.parametrize(
    "field,value", [("opportunity_count", 39), ("target_diameter_deg", 99), ("fire_interval_ms", 1)]
)
def test_named_version_cannot_silently_change_registered_parameters(field, value):
    payload = get_task("pc-fps", "precision", catalog_version="pc-fps-v3").model_dump(mode="json")
    payload["params"][field] = value
    with pytest.raises(ValidationError):
        TaskConfig.model_validate(payload)


@pytest.mark.parametrize("suite", MEMBERS)
@pytest.mark.parametrize("difficulty", [1, 2, 3, 4, 5])
def test_all_eighty_registered_configs_are_feasible(suite, difficulty):
    for test in MEMBERS[suite]:
        config = get_task(suite, test, difficulty, catalog_version=f"{suite}-v3")
        assert TaskConfig.model_validate_json(config.model_dump_json()) == config


@pytest.mark.parametrize("suite", MEMBERS)
def test_suite_json_roundtrip_ignores_task_mapping_key_order(suite):
    original = get_suite(suite, catalog_version=f"{suite}-v3")
    payload = original.model_dump(mode="json")
    payload["tasks"] = dict(reversed(tuple(payload["tasks"].items())))
    restored = SuiteConfig.model_validate_json(json.dumps(payload, sort_keys=True))
    assert restored == original
    assert restored.test_ids == MEMBERS[suite]
    assert tuple(restored.tasks) == restored.test_ids
