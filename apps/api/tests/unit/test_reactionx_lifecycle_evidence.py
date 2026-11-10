"""Completion proof consumes original diagnostics without making them scores."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.reactionx.metrics import InvalidEvents, _validate_events, calculate_metrics
from app.reactionx.schemas import ResultEnvelope, RunManifest, canonical_digest


def original_with_diagnostic(mode="whack", position="middle"):
    fixtures = json.loads(
        (Path(__file__).parents[4] / "shared/reactionx/v1/fixtures.json").read_text()
    )
    case = deepcopy(next(case for case in fixtures["cases"] if case["name"] == mode))
    events = case["result"]["events"]
    index = 0 if position == "first" else len(events) if position == "last" else 1
    diagnostic = {
        **events[max(0, index - 1)],
        "kind": "invalid",
        "device_ticks_10ms": None,
        "termination_reason": None,
        "quality_code": "duplicate_notification",
    }
    events.insert(index, diagnostic)
    for seq, event in enumerate(events, 1):
        event["seq"] = seq
    return RunManifest.model_validate(case["manifest"]), case["result"], index


@pytest.mark.parametrize("mode", ["standard", "whack"])
@pytest.mark.parametrize("position", ["first", "middle", "last"])
def test_original_diagnostics_never_supply_score_or_change_complete_package(mode, position):
    manifest, raw, _ = original_with_diagnostic(mode, position)
    result = ResultEnvelope.model_validate(raw)
    digest = canonical_digest(result)
    _validate_events(manifest, result.events, result.end, allow_diagnostics=True)
    with pytest.raises(InvalidEvents):
        _validate_events(manifest, result.events, result.end)
    metrics = calculate_metrics(manifest, result.events, result.end)
    assert not metrics.eligible and metrics.reason == "quality_warning"
    assert metrics.values == {} and metrics.dimensions == []
    assert canonical_digest(result) == digest and result.model_dump(mode="json") == raw


@pytest.mark.parametrize(
    "mutation", ["invalid_only", "replace_required", "device", "time", "sequence"]
)
def test_diagnostics_cannot_forge_missing_or_inconsistent_natural_evidence(mutation):
    manifest, raw, index = original_with_diagnostic()
    diagnostic = raw["events"][index]
    if mutation == "invalid_only":
        raw["events"] = [{**diagnostic, "seq": 1}]
    elif mutation == "replace_required":
        raw["events"][0] = {
            **raw["events"][0],
            "kind": "invalid",
            "quality_code": "activation_write_failed",
        }
    elif mutation == "device":
        diagnostic["device_key"] = "foreign-lamp"
    elif mutation == "time":
        diagnostic["host_elapsed_ms"] = raw["end"]["active_elapsed_ms"] + 1
    elif mutation == "sequence":
        diagnostic["seq"] = 99
    result = ResultEnvelope.model_validate(raw)
    with pytest.raises(InvalidEvents):
        _validate_events(manifest, result.events, result.end, allow_diagnostics=True)
