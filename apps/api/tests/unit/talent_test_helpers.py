from typing import Any


def valid_payload(test_id: str) -> dict[str, Any]:
    raws: dict[str, dict[str, Any]] = {
        "reactiontime": {"samples_ms": [200] * 5, "too_soon_count": 0},
        "aim": {
            "intervals_ms": [300] * 30,
            "miss_count": 0,
            "total_elapsed_ms": 9000,
        },
        "sequence": {
            "reached_level": 1,
            "completed_level": 0,
            "failed_step": 1,
            "end_reason": "wrong_tile",
        },
        "number-memory": {
            "correct_digits": 0,
            "failed_digits": 1,
            "display_duration_ms": 1800,
        },
        "verbal-memory": {
            "correct_count": 0,
            "error_count": 3,
            "total_count": 3,
            "word_bank_version": "zh-192-v1",
        },
        "chimp": {
            "reached_count": 4,
            "completed_count": 0,
            "strikes": 3,
            "end_reason": "strikes",
        },
        "memory": {
            "reached_level": 1,
            "completed_level": 0,
            "accepted_misses": 9,
            "lives_used": 3,
            "end_reason": "lives",
        },
        "dynamic-vision": {
            "selected_level": 1,
            "outcomes": ["incorrect"] * 5,
            "measured_display_ms": [100] * 5,
            "font_size_px": 56,
        },
    }
    durations: dict[str, float] = {
        "reactiontime": 1000,
        "aim": 9000,
        "sequence": 1,
        "number-memory": 1800,
        "verbal-memory": 1,
        "chimp": 1,
        "memory": 1,
        "dynamic-vision": 500,
    }
    geometry = {
        "aim": {"width": 1280, "height": 720, "target_diameter": 50},
        "dynamic-vision": {"width": 800, "height": 450, "target_diameter": None},
    }.get(test_id)
    engine_version = "dynamic-vision-v1" if test_id == "dynamic-vision" else f"{test_id}-clone-v1"
    return {
        "test_id": test_id,
        "raw": raws[test_id],
        "device": {
            "input_kind": "mouse",
            "viewport_width": 1280,
            "viewport_height": 720,
            "geometry": geometry,
        },
        "duration_ms": durations[test_id],
        "background_interrupted": False,
        "submission_id": "00000000-0000-4000-8000-000000000001",
        "engine_version": engine_version,
        "locale": "zh-CN",
    }
