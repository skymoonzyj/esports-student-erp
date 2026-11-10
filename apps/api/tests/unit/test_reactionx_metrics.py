"""Expected values are hand checked; no implementation helper builds expectations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

FIXTURES_PATH = Path(__file__).resolve().parents[4] / "shared/reactionx/v1/fixtures.json"


def case(name: str) -> dict:
    return next(c for c in json.loads(FIXTURES_PATH.read_text())["cases"] if c["name"] == name)


def measure(value: dict):
    from app.reactionx.metrics import calculate_metrics
    from app.reactionx.schemas import RunEnd, RunEvent, RunManifest

    return calculate_metrics(
        RunManifest.model_validate(value["manifest"]),
        [RunEvent.model_validate(e) for e in value["result"]["events"]],
        RunEnd.model_validate(value["result"]["end"]),
    )


@pytest.mark.parametrize(
    "name",
    [
        "manual",
        "standard",
        "sequence",
        "simultaneous",
        "base",
        "auditory",
        "true_false",
        "counter",
        "whack",
    ],
)
def test_fixture_metrics_have_independent_literal_expectations(name: str) -> None:
    value = case(name)
    result = measure(value)
    assert result.eligible == value["expected"]["eligible"]
    assert result.reason == value["expected"]["reason"]
    for key, expected in value["expected"]["values"].items():
        assert result.values[key] == expected


@pytest.mark.parametrize("name", ["simultaneous", "base"])
def test_half_group_or_cycle_cannot_finish(name: str) -> None:
    value = case(name)
    value["result"]["events"] = value["result"]["events"][:-3]
    assert not measure(value).eligible


@pytest.mark.parametrize("name", ["standard", "sequence", "simultaneous", "base", "auditory"])
def test_empty_hits_do_not_form_reaction_rank(name: str) -> None:
    value = case(name)
    for event in value["result"]["events"]:
        if event["kind"] == "hit":
            event["kind"] = "timeout"
            event["termination_reason"] = "host_timeout"
            event["device_ticks_10ms"] = None
    assert not measure(value).eligible


def test_true_false_requires_balanced_roles_and_manifest_color_binding() -> None:
    value = case("true_false")
    for event in value["result"]["events"]:
        if event["stimulus_role"] == "distractor":
            event["stimulus_role"] = "target"
            event["color"] = 2
    assert not measure(value).eligible


def test_user_stop_cannot_masquerade_as_natural_whack_failure() -> None:
    value = case("whack")
    value["result"]["end"]["kind"] = "user_stopped"
    assert measure(value).reason == "incomplete_end"
    value["result"]["end"]["kind"] = "all_lit"
    value["result"]["events"] = value["result"]["events"][:-1]
    assert not measure(value).eligible


def test_counter_twelve_beats_eleven_at_full_thirty_seconds() -> None:
    from app.reactionx.metrics import compare_metric_results

    twelve = complete_counter_window(6, 6)
    eleven = complete_counter_window(6, 5)
    assert measure(twelve).values["hit_count"] == 12
    assert measure(eleven).values["hit_count"] == 11
    assert compare_metric_results(measure(twelve), measure(eleven)) == -1
    eleven["result"]["end"]["active_elapsed_ms"] = 29_999
    assert not measure(eleven).eligible


def complete_counter_window(a_hits: int, b_hits: int) -> dict:
    """Independent test schedule: each lamp hits200ms, then times out3000ms."""
    value = case("counter")
    template = value["result"]["events"][0]
    scheduled = []
    for device, hit_quota in (("lamp-a", a_hits), ("lamp-b", b_hits)):
        onset = cycle = 0
        while onset < 30_000:
            cycle += 1
            activation = {
                **template,
                "activation_id": f"{device}-{cycle}",
                "device_key": device,
                "host_elapsed_ms": onset,
            }
            scheduled.append(activation)
            hit = cycle <= hit_quota
            resolution_time = onset + (200 if hit else 3000)
            if resolution_time >= 30_000:
                break
            scheduled.append(
                {
                    **activation,
                    "kind": "hit" if hit else "timeout",
                    "host_elapsed_ms": resolution_time,
                    "device_ticks_10ms": 20 if hit else None,
                    "termination_reason": None if hit else "host_timeout",
                }
            )
            onset = resolution_time + 80
    events = sorted(scheduled, key=lambda event: (event["host_elapsed_ms"], event["device_key"]))
    rounds: dict[str, int] = {}
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
        if event["kind"] == "activated":
            rounds[event["activation_id"]] = len(rounds) + 1
        event["round"] = rounds[event["activation_id"]]
    value["result"]["events"] = events
    return value


def test_nine_correct_judgments_beat_eight_even_if_eight_are_faster() -> None:
    from app.reactionx.metrics import compare_metric_results

    nine, eight = case("true_false"), case("true_false")
    # One target hit plus 8/7 correct ignores; remaining targets miss.
    for value, correct, ticks in ((nine, 8, 80), (eight, 7, 1)):
        seen = targets = 0
        for event in value["result"]["events"]:
            if event["kind"] == "hit" and event["stimulus_role"] == "target":
                targets += 1
                activation_time = (event["round"] - 1) * 3500
                if targets == 1:
                    event.update(
                        device_ticks_10ms=ticks, host_elapsed_ms=activation_time + ticks * 10
                    )
                else:
                    event.update(
                        kind="timeout",
                        termination_reason="host_timeout",
                        device_ticks_10ms=None,
                        host_elapsed_ms=activation_time + 3000,
                    )
            elif event["kind"] == "ignored":
                seen += 1
                if seen > correct:
                    event.update(kind="hit", termination_reason=None, device_ticks_10ms=ticks)
    assert measure(nine).values["correct_count"] == 9
    assert measure(eight).values["correct_count"] == 8
    assert compare_metric_results(measure(nine), measure(eight)) == -1


@pytest.mark.parametrize(
    "mutation",
    [
        "seq",
        "time",
        "device",
        "activation",
        "role",
        "color",
        "tick",
        "duplicate",
        "gap",
        "interval",
    ],
)
def test_events_must_match_manifest_and_real_activation_lifecycle(mutation: str) -> None:
    value = case("standard")
    events = value["result"]["events"]
    if mutation == "seq":
        events[0]["seq"] = 2
    elif mutation == "time":
        events[1]["host_elapsed_ms"] = 0
    elif mutation == "device":
        events[1]["device_key"] = "unknown"
    elif mutation == "activation":
        events[1]["activation_id"] = "missing"
    elif mutation == "role":
        events[1]["stimulus_role"] = "base"
    elif mutation == "color":
        events[1]["color"] = 8
    elif mutation == "tick":
        events[1]["device_ticks_10ms"] = 200
    elif mutation == "duplicate":
        events.insert(2, copy.deepcopy(events[1]))
    elif mutation == "gap":
        events.pop(1)
    else:
        events[2]["host_elapsed_ms"] = events[1]["host_elapsed_ms"]
    assert not measure(value).eligible


def test_comparison_key_excludes_seed_and_identity_but_freezes_configuration() -> None:
    from app.reactionx.schemas import canonical_digest

    original = case("standard")
    changed = copy.deepcopy(original)
    changed["manifest"]["run_id"] = "another-run"
    changed["manifest"]["config"]["seed"] = 2  # same device rotation for two lights
    changed["manifest"]["manifest_digest"] = canonical_digest(
        changed["manifest"], exclude={"manifest_digest"}
    )
    assert measure(original).comparison_key == measure(changed).comparison_key
    changed["manifest"]["config"]["sensor"] = 1
    changed["manifest"]["manifest_digest"] = canonical_digest(
        changed["manifest"], exclude={"manifest_digest"}
    )
    assert measure(original).comparison_key != measure(changed).comparison_key


@pytest.mark.parametrize(
    "quality",
    [
        "activation_ambiguous",
        "activation_write_failed",
        "unknown_activation",
        "duplicate_notification",
        "device_time_invalid",
        "event_limit_exceeded",
        "stale_connection",
        "stale_run",
        "scheduler_late",
    ],
)
def test_ambiguous_or_failed_activation_is_evidence_never_a_rankable_hit(quality: str) -> None:
    value = case("standard")
    event = value["result"]["events"][0]
    event.update(kind="invalid", quality_code=quality)
    assert measure(value).reason == "quality_warning"


def test_bound_result_rejects_different_run_or_manifest() -> None:
    from app.reactionx.metrics import validate_result_envelope
    from app.reactionx.schemas import ResultEnvelope, RunManifest

    value = case("standard")
    manifest = RunManifest.model_validate(value["manifest"])
    assert validate_result_envelope(
        manifest, ResultEnvelope.model_validate(value["result"])
    ).eligible
    for patch in ({"run_id": "other"}, {"manifest_digest": "0" * 64}):
        with pytest.raises(ValueError):
            validate_result_envelope(
                manifest, ResultEnvelope.model_validate({**value["result"], **patch})
            )


def test_error_count_has_priority_over_faster_mean_and_exact_ties_are_kept() -> None:
    from app.reactionx.metrics import compare_metric_results

    perfect = case("standard")
    missed = copy.deepcopy(perfect)
    missed["result"]["events"][1].update(
        kind="timeout",
        device_ticks_10ms=None,
        termination_reason="host_timeout",
        host_elapsed_ms=3000,
    )
    for event in missed["result"]["events"][2:]:
        event["host_elapsed_ms"] += 2800
        if event["kind"] == "hit":
            event["device_ticks_10ms"] = 1
    missed["result"]["end"]["active_elapsed_ms"] += 2800
    assert measure(missed).eligible
    assert measure(missed).values["miss_count"] == 1
    assert compare_metric_results(measure(perfect), measure(missed)) == -1
    assert compare_metric_results(measure(perfect), measure(copy.deepcopy(perfect))) == 0


def test_mean_precision_survives_equal_rounded_display_values() -> None:
    from app.reactionx.metrics import compare_metric_results

    first, second = case("standard"), case("standard")
    first["result"]["events"][1]["device_ticks_10ms"] = 19
    # 5990/30 = 199.666..., versus 6000/30=200: both round to 200ms.
    assert round(measure(first).values["mean_hit_ms"]) == 200
    assert measure(first).dimensions[1].numerator == 5990
    assert measure(first).dimensions[1].denominator == 30
    assert compare_metric_results(measure(first), measure(second)) == -1


def test_counter_all_lamps_arm_and_each_rearm_uses_its_own_interval() -> None:
    value = case("counter")
    assert value["result"]["events"][0]["kind"] == "activated"
    assert value["result"]["events"][1]["kind"] == "activated"
    assert measure(value).eligible
    # First rearm must respect the resolution on that same device.
    value["result"]["events"][4]["host_elapsed_ms"] = 279
    assert not measure(value).eligible


@pytest.mark.parametrize(
    "mutation", ["only_initial_lamp", "stalled_after_hits", "missing_rearm", "overdue_timeout"]
)
def test_counter_requires_complete_activity_obligations_through_thirty_seconds(
    mutation: str,
) -> None:
    value = complete_counter_window(6, 6)
    events = value["result"]["events"]
    if mutation == "only_initial_lamp":
        value["result"]["events"] = events[:1]
    elif mutation == "stalled_after_hits":
        value["result"]["events"] = events[:24]
    elif mutation == "missing_rearm":
        value["result"]["events"] = events[:4]
    else:
        value["result"]["events"] = events[:2]
    assert not measure(value).eligible


@pytest.mark.parametrize("mutation", ["one_stimulus", "delayed_failure"])
def test_whack_cannot_gain_survival_by_omitting_or_delaying_scheduled_stimuli(
    mutation: str,
) -> None:
    value = case("whack")
    if mutation == "one_stimulus":
        value["result"]["events"] = value["result"]["events"][:1]
        value["result"]["end"] = {"kind": "time_completed", "active_elapsed_ms": 60_000}
    else:
        value["result"]["events"][1]["host_elapsed_ms"] = 59_999
        value["result"]["end"]["active_elapsed_ms"] = 59_999
    assert not measure(value).eligible


def complete_whack_window() -> dict:
    """Hand-defined120 absolute500ms slots, each cleared200ms later."""
    value = case("whack")
    template = value["result"]["events"][0]
    events = []
    for slot in range(120):
        activation = {
            **template,
            "seq": len(events) + 1,
            "activation_id": f"slot-{slot}",
            "host_elapsed_ms": slot * 500,
            "round": slot + 1,
        }
        events.extend(
            [
                activation,
                {
                    **activation,
                    "seq": len(events) + 2,
                    "kind": "hit",
                    "host_elapsed_ms": slot * 500 + 200,
                    "device_ticks_10ms": 20,
                },
            ]
        )
    value["result"]["events"] = events
    value["result"]["end"] = {"kind": "time_completed", "active_elapsed_ms": 60_000}
    return value


def test_whack_absolute_slots_cover_complete_sixty_second_window() -> None:
    value = complete_whack_window()
    result = measure(value)
    assert result.eligible
    assert result.values["survival_ms"] == 60_000
    assert result.values["hit_count"] == 120


@pytest.mark.parametrize("lateness,eligible", [(250, True), (251, False)])
def test_whack_slot_jitter_is_bounded_without_chained_drift(lateness: int, eligible: bool) -> None:
    value = case("whack")
    value["result"]["events"][1]["host_elapsed_ms"] = 500 + lateness
    value["result"]["end"]["active_elapsed_ms"] = 500 + lateness
    assert measure(value).eligible is eligible


@pytest.mark.parametrize("lateness,eligible", [(250, True), (251, False)])
def test_counter_rearm_lateness_has_exact_bounded_window(lateness: int, eligible: bool) -> None:
    value = complete_counter_window(0, 0)
    # Shift the final pending active ACKs, whose next timeouts are beyond end.
    for event in value["result"]["events"][-2:]:
        event["host_elapsed_ms"] += lateness
    assert measure(value).eligible is eligible


@pytest.mark.parametrize("lateness,eligible", [(250, True), (251, False)])
def test_counter_initial_ack_set_has_bounded_lateness(lateness: int, eligible: bool) -> None:
    value = complete_counter_window(6, 6)
    for event in value["result"]["events"]:
        event["host_elapsed_ms"] += lateness
    assert measure(value).eligible is eligible


@pytest.mark.parametrize("lateness,eligible", [(250, True), (251, False)])
def test_counter_timeout_resolution_has_bounded_lateness(lateness: int, eligible: bool) -> None:
    value = complete_counter_window(0, 0)
    for event in value["result"]["events"][2:]:
        event["host_elapsed_ms"] += lateness
    assert measure(value).eligible is eligible


def test_whack_jitter_does_not_accumulate_or_require_shifted_future_slots() -> None:
    value = complete_whack_window()
    value["result"]["events"][0]["host_elapsed_ms"] = 250
    value["result"]["events"][1]["host_elapsed_ms"] = 450
    assert measure(value).eligible  # Next onset is still absolute500, not ACK250+500.
    for index, event in enumerate(value["result"]["events"]):
        slot = index // 2
        event["host_elapsed_ms"] = slot * 500 + slot * 100 + (200 if index % 2 else 0)
    assert not measure(value).eligible


def test_shared_positive_timed_fixtures_match_literal_metrics() -> None:
    data = json.loads(FIXTURES_PATH.read_text())
    for value in data["timed_cases"]:
        result = measure(value)
        assert result.eligible
        for key, expected in value["expected"]["values"].items():
            assert result.values[key] == expected
