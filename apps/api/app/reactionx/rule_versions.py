"""Frozen task definitions and deterministic schedule; no device or database access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .schemas import (
    CommandEnvelope,
    EndKind,
    ManualActivatePayload,
    Mode,
    RunManifest,
    StartPayload,
    StimulusRole,
    canonical_digest,
)


@dataclass(frozen=True)
class RuleDefinition:
    version: str
    mode: Mode
    task_count: int | None
    duration_ms: int | None
    allowed_ends: tuple[EndKind, ...]
    metric_keys: tuple[str, ...]
    verification_status: Literal["unverified"] = "unverified"


_DEFINITIONS: tuple[
    tuple[Mode, int | None, int | None, tuple[EndKind, ...], tuple[str, ...]], ...
] = (
    ("manual", None, None, (), ()),
    ("standard", 30, None, ("task_completed",), ("miss_count", "mean_hit_ms")),
    ("sequence", 5, None, ("task_completed",), ("miss_count", "mean_hit_ms")),
    ("simultaneous", 5, None, ("task_completed",), ("miss_count", "mean_group_ms")),
    ("base", 10, None, ("task_completed",), ("miss_count", "mean_cycle_ms")),
    ("auditory", 30, None, ("task_completed",), ("miss_count", "mean_hit_ms")),
    ("true_false", 30, None, ("task_completed",), ("correct_count", "mean_hit_ms")),
    ("counter", None, 30_000, ("time_completed",), ("hit_count",)),
    ("whack", None, 60_000, ("all_lit", "time_completed"), ("survival_ms", "hit_count")),
)
RULES: dict[Mode, RuleDefinition] = {
    mode: RuleDefinition("reactionx-v1", mode, count, duration, ends, metrics)
    for mode, count, duration, ends, metrics in _DEFINITIONS
}


def validate_manifest(manifest: RunManifest) -> RuleDefinition:
    if canonical_digest(manifest, exclude={"manifest_digest"}) != manifest.manifest_digest:
        raise ValueError("manifest digest mismatch")
    config, devices = manifest.config, manifest.devices
    keys = [d.device_key for d in devices]
    if len(set(keys)) != len(keys):
        raise ValueError("duplicate device key")
    if [d.order for d in devices] != list(range(len(devices))):
        raise ValueError("devices must be in frozen contiguous order")
    if config.lamp_count != len(devices):
        raise ValueError("lamp count mismatch")
    positions = manifest.layout.positions
    if len({p.device_key for p in positions}) != len(positions):
        raise ValueError("duplicate layout key")
    if not {p.device_key for p in positions} <= set(keys):
        raise ValueError("unknown layout device")
    colors = {config.color, config.target_color, config.distractor_color}
    if any(d.protocol_version == 3 for d in devices) and (
        config.sensor >= 4 or config.flash >= 2 or 8 in colors
    ):
        raise ValueError("configuration is unsupported by protocol v3")
    rule = RULES[manifest.mode]
    if manifest.mode == "manual":
        return rule
    if manifest.layout.posture == "unregistered" or {p.device_key for p in positions} != set(keys):
        raise ValueError("formal run requires complete registered layout")
    if config.stimulus_interval_ms < 10:
        raise ValueError("formal interval must be positive")
    if config.task_count != rule.task_count or config.duration_ms != rule.duration_ms:
        raise ValueError("formal task count or duration differs from frozen rule")
    if manifest.mode in ("sequence", "simultaneous", "base", "whack") and len(devices) < 2:
        raise ValueError("mode needs at least two lamps")
    if manifest.mode == "counter":
        if config.restart_interval_ms is None or config.restart_interval_ms < 10:
            raise ValueError("counter requires a positive restart interval")
    elif config.restart_interval_ms is not None:
        raise ValueError("restart interval belongs only to counter")
    if manifest.mode == "base":
        if config.base_device_key not in keys:
            raise ValueError("base needs a registered base lamp")
    elif config.base_device_key is not None:
        raise ValueError("unexpected base lamp")
    if manifest.mode == "true_false":
        if (
            config.target_color is None
            or config.distractor_color is None
            or config.target_color == config.distractor_color
            or config.target_color == 0
            or config.distractor_color == 0
        ):
            raise ValueError("true/false needs disjoint visible target and distractor colors")
    elif config.target_color is not None or config.distractor_color is not None:
        raise ValueError("unexpected judgment colors")
    if manifest.mode == "auditory":
        if config.color != 0 or not config.on_sound or config.flash != 0:
            raise ValueError("auditory requires sound and no visual stimulus")
    elif config.color == 0:
        raise ValueError("visual mode requires a visible color")
    return rule


def validate_command_for_manifest(command: CommandEnvelope, manifest: RunManifest) -> None:
    """Use the server's stored manifest, never a browser-supplied replacement."""
    validate_manifest(manifest)
    if (
        command.run_id,
        command.actor_account_id,
        command.subject_person_id,
        command.connector_id,
    ) != (
        manifest.run_id,
        manifest.actor_account_id,
        manifest.subject_person_id,
        manifest.connector_id,
    ):
        raise ValueError("command ownership differs from frozen manifest")
    if (
        isinstance(command.payload, StartPayload)
        and command.payload.manifest_digest != manifest.manifest_digest
    ):
        raise ValueError("start manifest digest mismatch")
    if isinstance(command.payload, ManualActivatePayload):
        if manifest.mode != "manual":
            raise ValueError("manual activation is practice-only")
        if command.payload.device_key not in {d.device_key for d in manifest.devices}:
            raise ValueError("manual activation device not in manifest")
        if command.payload.color == 8 and any(
            d.device_key == command.payload.device_key and d.protocol_version == 3
            for d in manifest.devices
        ):
            raise ValueError("manual color unsupported by device")


@dataclass(frozen=True)
class PlannedStimulus:
    device_key: str
    role: StimulusRole
    color: int
    round: int | None
    group: int | None
    cycle: int | None


def planned_stimulus(
    manifest: RunManifest, index: int, *, active_device_keys: set[str] | None = None
) -> PlannedStimulus:
    """Zero-based activation index; rotation is seed modulo eligible lamp count.

    Sequence/synchronous order never rotates. True/false alternates target and
    distractor (15 each), with seed parity deciding the first role. The algorithm
    is deliberately small and reproducible; it is not scientific randomization.
    """
    config, mode = manifest.config, manifest.mode
    keys = [d.device_key for d in manifest.devices]
    role: StimulusRole = "target"
    color = config.color
    round_number: int | None = index + 1
    group = cycle = None
    if mode == "base":
        targets = [key for key in keys if key != config.base_device_key]
        cycle, round_number = index // 2 + 1, None
        if index % 2:
            role, key = "base", config.base_device_key
            assert key is not None
        else:
            key = targets[(index // 2 + config.seed) % len(targets)]
    elif mode in ("sequence", "simultaneous"):
        key = keys[index % len(keys)]
        if mode == "sequence":
            round_number = index // len(keys) + 1
        else:
            group, round_number = index // len(keys) + 1, None
    else:
        key = keys[(index + config.seed) % len(keys)]
        if mode == "whack":
            # The fixed seed-rotated order chooses its first currently-free lamp.
            # Blind index rotation can point at a lit lamp while another is free.
            rotation = config.seed % len(keys)
            ordered = keys[rotation:] + keys[:rotation]
            free = [
                candidate for candidate in ordered if candidate not in (active_device_keys or set())
            ]
            if not free:
                raise ValueError("whack has no unlit lamp")
            key = free[0]
        if mode == "true_false":
            role = "distractor" if (index + config.seed) % 2 else "target"
            selected = config.distractor_color if role == "distractor" else config.target_color
            assert selected is not None
            color = selected
    return PlannedStimulus(key, role, color, round_number, group, cycle)


def comparison_key(manifest: RunManifest) -> str:
    """Opaque physical identities/seed do not split boards; ordered geometry does."""
    config = manifest.config.model_dump(exclude={"seed", "base_device_key"})
    order = {d.device_key: d.order for d in manifest.devices}
    config["base_device_order"] = order.get(manifest.config.base_device_key or "")
    layout = sorted(
        [
            {"order": order[p.device_key], "x_cm": p.x_cm, "y_cm": p.y_cm, "z_cm": p.z_cm}
            for p in manifest.layout.positions
        ],
        key=lambda p: p["order"],
    )
    return canonical_digest(
        {
            "source": "reactionx",
            "rule_version": manifest.rule_version,
            "mode": manifest.mode,
            "config": config,
            "posture": manifest.layout.posture,
            "layout": layout,
            "devices": [
                {"order": d.order, "protocol_version": d.protocol_version} for d in manifest.devices
            ],
        }
    )
