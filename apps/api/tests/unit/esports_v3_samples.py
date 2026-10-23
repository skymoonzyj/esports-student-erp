"""Complete v3 raw samples kept separate from frozen v1/v2 helpers."""

from copy import deepcopy

from app.esports_assessments.catalog import dodge_choice_schedule
from app.esports_assessments.config_schemas import TaskConfig
from tests.unit.esports_samples import sample_raw


def sample_raw_v3(
    config: TaskConfig,
    *,
    perfect: bool,
    seed: int = 123,
) -> dict[str, object]:
    if config.catalog_version != f"{config.suite_id}-v3":
        raise ValueError("v3 sample requires an exact v3 catalog")
    if config.params.kind == "move_fire_v3":
        return {
            "kind": "move_fire_v3",
            "rounds": [
                {
                    "hit": perfect,
                    "reached": perfect,
                    "hit_latency_ms": 500 if perfect else None,
                    "hit_speed_m_s": 2 if perfect else None,
                    "pre_hit_moving_ms": 100 if perfect else None,
                }
                for _ in range(20)
            ],
            "shots": 20 if perfect else 0,
        }
    if config.params.kind == "dodge_choice":
        return {
            "kind": "dodge_choice",
            "rounds": [
                {
                    **event,
                    "hit": not perfect,
                    "safe_entry_latency_ms": 100 if perfect else None,
                }
                for event in dodge_choice_schedule(seed, config.params.warning_ms)
            ],
        }

    raw = deepcopy(sample_raw(config.suite_id, config.test_id, perfect=perfect, seed=seed))
    if config.test_id == "switching" and perfect:
        for index in range(1, len(raw["trials"]), 2):
            raw["trials"][index]["latency_ms"] = 200
    if config.params.kind == "map" and not perfect:
        for event in raw["events"]:
            event["latency_ms"] = config.params.event_window_ms
    return raw
