"""Server-owned lifecycle validation and lexicographic metrics.

The exported JSON numbers are presentation values. Ranking compares bounded
integer numerator/denominator pairs by cross multiplication, never rounded means.
Logical eligibility does not certify a connector, device or scientific norm.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated, Literal

from pydantic import Field

from .rule_versions import comparison_key, planned_stimulus, validate_manifest
from .schemas import (
    MAX_EVENTS,
    SCHEDULER_LATENESS_MS,
    ResultEnvelope,
    RunEnd,
    RunEvent,
    RunManifest,
    StrictModel,
)


class ExactMetric(StrictModel):
    key: str
    direction: Literal["asc", "desc"]
    unit: Literal["ms", "count"]
    numerator: Annotated[int, Field(ge=0, le=9_007_199_254_740_991)]
    denominator: Annotated[int, Field(ge=1, le=MAX_EVENTS)]


class MetricResult(StrictModel):
    eligible: bool
    reason: str | None
    values: dict[str, int | float | None]
    comparison_key: str
    dimensions: list[ExactMetric]


class InvalidEvents(ValueError):
    pass


def _require(condition: bool) -> None:
    if not condition:
        raise InvalidEvents("event lifecycle does not match frozen manifest")


@dataclass
class _Validated:
    hits: list[int] = field(default_factory=list)
    misses: int = 0
    false_hits: int = 0
    correct_ignores: int = 0
    host_timeouts: int = 0
    hardware_timeouts: int = 0
    group_times: list[int] = field(default_factory=list)
    cycle_times: list[int] = field(default_factory=list)
    active: dict[str, RunEvent] = field(default_factory=dict)
    activations: int = 0


def _identity(event: RunEvent) -> tuple[object, ...]:
    return (
        event.activation_id,
        event.device_key,
        event.round,
        event.group,
        event.cycle,
        event.stimulus_role,
        event.color,
    )


def _validate_events(
    manifest: RunManifest, events: list[RunEvent], end: RunEnd, *, allow_diagnostics: bool = False
) -> _Validated:
    _require(len(events) <= MAX_EVENTS)
    stats = _Validated()
    mode, config = manifest.mode, manifest.config
    keys = {device.device_key for device in manifest.devices}
    used_activations: set[str] = set()
    previous_time = 0
    last_resolution: int | None = None
    boundary_start: RunEvent | None = None
    boundary_activations = 0
    boundary_count = 0
    all_lit_seen = False
    device_resolutions: dict[str, int] = {}
    last_lifecycle_index = (
        max((index for index, event in enumerate(events) if event.kind != "invalid"), default=-1)
        if allow_diagnostics
        else len(events) - 1
    )
    for index, event in enumerate(events):
        _require(event.seq == index + 1)
        _require(previous_time <= event.host_elapsed_ms <= end.active_elapsed_ms)
        previous_time = event.host_elapsed_ms
        if allow_diagnostics and event.kind == "invalid":
            # Completion-only proof consumes the ORIGINAL bounded stream. A
            # diagnostic never supplies an activation/resolution or a score.
            _require(event.device_key is None or event.device_key in keys)
            continue
        _require(not all_lit_seen)
        if event.kind in ("group_started", "cycle_started"):
            group = event.kind == "group_started"
            _require(mode == ("simultaneous" if group else "base"))
            _require(boundary_start is None and not stats.active)
            _require((event.group if group else event.cycle) == boundary_count + 1)
            if last_resolution is not None:
                _require(event.host_elapsed_ms - last_resolution >= config.stimulus_interval_ms)
            boundary_start, boundary_activations = event, 0
            continue
        if event.kind in ("group_completed", "cycle_completed"):
            group = event.kind == "group_completed"
            _require(mode == ("simultaneous" if group else "base"))
            _require(boundary_start is not None and not stats.active)
            assert boundary_start is not None
            _require((event.group, event.cycle) == (boundary_start.group, boundary_start.cycle))
            _require(boundary_activations == (len(keys) if group else 2))
            duration = event.host_elapsed_ms - boundary_start.host_elapsed_ms
            (stats.group_times if group else stats.cycle_times).append(duration)
            boundary_count += 1
            boundary_start = None
            last_resolution = event.host_elapsed_ms
            continue
        _require(event.device_key in keys and event.activation_id is not None)
        assert event.activation_id is not None
        if event.kind == "activated":
            _require(event.activation_id not in used_activations)
            _require(not any(a.device_key == event.device_key for a in stats.active.values()))
            if mode == "counter":
                _require(event.stimulus_role == "target" and event.color == config.color)
                _require(event.round == stats.activations + 1)
                if stats.activations < len(keys):
                    _require(event.device_key == manifest.devices[stats.activations].device_key)
                    _require(event.host_elapsed_ms <= SCHEDULER_LATENESS_MS)
                else:
                    resolved_at = device_resolutions.get(event.device_key or "")
                    _require(resolved_at is not None)
                    assert resolved_at is not None and config.restart_interval_ms is not None
                    _require(
                        config.restart_interval_ms
                        <= event.host_elapsed_ms - resolved_at
                        <= config.restart_interval_ms + SCHEDULER_LATENESS_MS
                    )
            elif mode != "manual":
                planned = planned_stimulus(
                    manifest,
                    stats.activations,
                    active_device_keys={
                        event.device_key
                        for event in stats.active.values()
                        if event.device_key is not None
                    },
                )
                _require(
                    (
                        event.device_key,
                        event.stimulus_role,
                        event.color,
                        event.round,
                        event.group,
                        event.cycle,
                    )
                    == (
                        planned.device_key,
                        planned.role,
                        planned.color,
                        planned.round,
                        planned.group,
                        planned.cycle,
                    )
                )
            if mode in ("simultaneous", "base"):
                _require(boundary_start is not None)
                assert boundary_start is not None
                _require((event.group, event.cycle) == (boundary_start.group, boundary_start.cycle))
                boundary_activations += 1
            elif mode != "manual":
                _require(event.group is None and event.cycle is None)
            if mode not in ("manual", "simultaneous", "whack", "counter"):
                _require(not stats.active)
                if last_resolution is not None:
                    interval = (
                        config.restart_interval_ms
                        if mode == "counter"
                        else config.stimulus_interval_ms
                    )
                    assert interval is not None
                    _require(event.host_elapsed_ms - last_resolution >= interval)
            if mode == "whack":
                # Absolute slots never drift by chaining late ACK timestamps.
                due = stats.activations * config.stimulus_interval_ms
                _require(due <= event.host_elapsed_ms <= due + SCHEDULER_LATENESS_MS)
            used_activations.add(event.activation_id)
            stats.active[event.activation_id] = event
            stats.activations += 1
            if mode == "whack" and len(stats.active) == len(keys):
                _require(end.kind == "all_lit" and index == last_lifecycle_index)
                _require(end.active_elapsed_ms == event.host_elapsed_ms)
                all_lit_seen = True
            continue
        activated = stats.active.get(event.activation_id)
        _require(activated is not None)
        assert activated is not None
        _require(_identity(event) == _identity(activated))
        if mode == "simultaneous":
            _require(boundary_activations == len(keys))
        if mode == "counter":
            _require(stats.activations >= len(keys))
        elapsed = event.host_elapsed_ms - activated.host_elapsed_ms
        if event.kind == "hit":
            assert event.device_ticks_10ms is not None
            reaction_ms = event.device_ticks_10ms * 10
            # Device ticks are 10 ms resolution, not a claim of host/BLE accuracy.
            _require(reaction_ms <= config.timeout_ms and reaction_ms <= elapsed + 10)
            _require(elapsed <= config.timeout_ms)
            if event.stimulus_role == "distractor":
                stats.false_hits += 1
            else:
                stats.hits.append(reaction_ms)
        else:
            _require(event.kind in ("timeout", "ignored"))
            _require(elapsed >= config.timeout_ms)
            if mode in ("counter", "whack"):
                _require(elapsed <= config.timeout_ms + SCHEDULER_LATENESS_MS)
            if event.termination_reason == "host_timeout":
                stats.host_timeouts += 1
            else:
                stats.hardware_timeouts += 1
            _require((event.kind == "ignored") == (event.stimulus_role == "distractor"))
            if event.kind == "ignored":
                stats.correct_ignores += 1
            else:
                stats.misses += 1
        del stats.active[event.activation_id]
        assert event.device_key is not None
        device_resolutions[event.device_key] = event.host_elapsed_ms
        last_resolution = event.host_elapsed_ms
    if mode not in ("manual", "counter", "whack"):
        count = config.task_count
        assert count is not None
        expected = count * (
            len(keys) if mode in ("sequence", "simultaneous") else 2 if mode == "base" else 1
        )
        _require(stats.activations == expected and not stats.active and boundary_start is None)
        if mode in ("simultaneous", "base"):
            _require(boundary_count == count)
    if mode in ("counter", "whack"):
        # At the exact end boundary the timer may cancel a pending operation;
        # anything already overdue by >250ms proves a stalled/missing scheduler.
        for active in stats.active.values():
            _require(
                active.host_elapsed_ms + config.timeout_ms + SCHEDULER_LATENESS_MS
                >= end.active_elapsed_ms
            )
    if mode == "counter":
        _require(stats.activations >= len(keys) and end.active_elapsed_ms == 30_000)
        active_keys = {active.device_key for active in stats.active.values()}
        assert config.restart_interval_ms is not None
        for key in keys - active_keys:
            resolved_at = device_resolutions.get(key)
            _require(resolved_at is not None)
            assert resolved_at is not None
            _require(
                resolved_at + config.restart_interval_ms + SCHEDULER_LATENESS_MS
                >= end.active_elapsed_ms
            )
    if mode == "whack":
        _require(stats.activations > 0 and end.active_elapsed_ms <= 60_000)
        _require(
            stats.activations * config.stimulus_interval_ms + SCHEDULER_LATENESS_MS
            >= end.active_elapsed_ms
        )
        if end.kind == "all_lit":
            _require(all_lit_seen)
        elif end.kind == "time_completed":
            _require(end.active_elapsed_ms == 60_000 and not all_lit_seen)
    return stats


def calculate_metrics(manifest: RunManifest, events: list[RunEvent], end: RunEnd) -> MetricResult:
    """Never accepts a client rankable/score flag; invalid telemetry is ineligible."""
    try:
        rule = validate_manifest(manifest)
    except ValueError:
        return MetricResult(
            eligible=False, reason="invalid_manifest", values={}, comparison_key="", dimensions=[]
        )
    key = comparison_key(manifest)
    if any(event.kind == "invalid" or event.quality_code is not None for event in events):
        return MetricResult(
            eligible=False, reason="quality_warning", values={}, comparison_key=key, dimensions=[]
        )
    if manifest.mode != "manual" and end.kind not in rule.allowed_ends:
        return MetricResult(
            eligible=False, reason="incomplete_end", values={}, comparison_key=key, dimensions=[]
        )
    try:
        stats = _validate_events(manifest, events, end)
    except InvalidEvents:
        return MetricResult(
            eligible=False, reason="invalid_events", values={}, comparison_key=key, dimensions=[]
        )
    values: dict[str, int | float | None] = {
        "hit_count": len(stats.hits),
        "miss_count": stats.misses,
        "false_hit_count": stats.false_hits,
        "correct_ignore_count": stats.correct_ignores,
        "correct_count": len(stats.hits) + stats.correct_ignores,
        "host_timeout_count": stats.host_timeouts,
        "hardware_timeout_count": stats.hardware_timeouts,
        "active_elapsed_ms": end.active_elapsed_ms,
        "mean_hit_ms": sum(stats.hits) / len(stats.hits) if stats.hits else None,
    }
    if manifest.mode == "manual":
        return MetricResult(
            eligible=False, reason="practice_only", values=values, comparison_key=key, dimensions=[]
        )
    if manifest.mode not in ("counter", "whack") and not stats.hits:
        return MetricResult(
            eligible=False, reason="no_valid_hits", values=values, comparison_key=key, dimensions=[]
        )
    totals: dict[str, tuple[int, int]] = {
        "miss_count": (stats.misses, 1),
        "hit_count": (len(stats.hits), 1),
        "correct_count": (len(stats.hits) + stats.correct_ignores, 1),
        "mean_hit_ms": (sum(stats.hits), len(stats.hits)),
        "mean_group_ms": (sum(stats.group_times), len(stats.group_times)),
        "mean_cycle_ms": (sum(stats.cycle_times), len(stats.cycle_times)),
        "survival_ms": (end.active_elapsed_ms, 1),
    }
    dimensions = []
    for metric in rule.metric_keys:
        numerator, denominator = totals[metric]
        assert denominator > 0
        values[metric] = numerator / denominator if denominator > 1 else numerator
        dimensions.append(
            ExactMetric(
                key=metric,
                direction="desc"
                if metric in ("hit_count", "correct_count", "survival_ms")
                else "asc",
                unit="ms" if metric.endswith("_ms") else "count",
                numerator=numerator,
                denominator=denominator,
            )
        )
    return MetricResult(
        eligible=True, reason=None, values=values, comparison_key=key, dimensions=dimensions
    )


def validate_result_envelope(manifest: RunManifest, result: ResultEnvelope) -> MetricResult:
    if result.run_id != manifest.run_id or result.manifest_digest != manifest.manifest_digest:
        raise ValueError("result does not bind to the frozen manifest")
    return calculate_metrics(manifest, result.events, result.end)


def compare_metric_results(left: MetricResult, right: MetricResult) -> int:
    """-1 means left ranks better; 0 is an exact tie. No weighted score."""
    if not left.eligible or not right.eligible or left.comparison_key != right.comparison_key:
        raise ValueError("only eligible results on the same comparison key may be compared")
    if len(left.dimensions) != len(right.dimensions):
        raise ValueError("incompatible metric dimensions")
    for first, second in zip(left.dimensions, right.dimensions, strict=True):
        if (first.key, first.direction, first.unit) != (second.key, second.direction, second.unit):
            raise ValueError("incompatible metric dimensions")
        lhs = first.numerator * second.denominator
        rhs = second.numerator * first.denominator
        if lhs != rhs:
            direction = 1 if first.direction == "asc" else -1
            return direction * (-1 if lhs < rhs else 1)
    return 0
