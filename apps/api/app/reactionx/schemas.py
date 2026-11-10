"""Frozen v1 wire contracts. All wire numbers are bounded integers, never floats.

Canonical JSON uses UTF-8, UTF-16 key order (the JavaScript order), compact
separators and no insignificant whitespace. Digest excludes only the manifest's
own ``manifest_digest`` field, not any other field.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Annotated, Literal, Self, get_args, get_origin

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

MAX_DURATION_MS = 900_000
MAX_EVENTS = 10_000
MAX_BATCH_EVENTS = 100
MAX_BATCH_BYTES = 256 * 1024
MAX_RESULT_BYTES = 8 * 1024 * 1024
SAFE_INTEGER = 9_007_199_254_740_991
# Provisional internal scheduler validity budget, NOT device/BLE precision.
SCHEDULER_LATENESS_MS = 250

OpaqueKey = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,128}$")]
UUIDText = Annotated[
    str, Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def _utc_stamp(value: str) -> str:
    datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")
    return value


UTCStamp = Annotated[
    str,
    Field(pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z$"),
    AfterValidator(_utc_stamp),
]
ElapsedMs = Annotated[int, Field(ge=0, le=MAX_DURATION_MS)]
PositiveIndex = Annotated[int, Field(ge=1, le=MAX_EVENTS)]
Color = Annotated[int, Field(ge=0, le=8)]
Mode = Literal[
    "manual",
    "standard",
    "sequence",
    "simultaneous",
    "base",
    "auditory",
    "true_false",
    "counter",
    "whack",
]
EndKind = Literal[
    "task_completed",
    "time_completed",
    "all_lit",
    "user_stopped",
    "lease_expired",
    "disconnected",
    "process_interrupted",
    "device_error",
    "storage_error",
]
EventKind = Literal[
    "activated",
    "hit",
    "timeout",
    "ignored",
    "group_started",
    "group_completed",
    "cycle_started",
    "cycle_completed",
    "invalid",
]
StimulusRole = Literal["target", "distractor", "base"]
QualityCode = Literal[
    "activation_ambiguous",
    "activation_write_failed",
    "unknown_activation",
    "duplicate_notification",
    "device_time_invalid",
    "event_limit_exceeded",
    "stale_connection",
    "stale_run",
    "scheduler_late",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    @model_validator(mode="before")
    @classmethod
    def strict_numeric_literals(cls, value: object) -> object:
        if isinstance(value, dict):
            for name, info in cls.model_fields.items():
                if (
                    name in value
                    and get_origin(info.annotation) is Literal
                    and any(type(option) is int for option in get_args(info.annotation))
                    and type(value[name]) is not int
                ):
                    raise ValueError("numeric literals require integers, not booleans")
        return value


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int and abs(value) <= SAFE_INTEGER:
        return value
    if isinstance(value, list):
        return [_json_value(v) for v in value]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return {
            k: _json_value(value[k]) for k in sorted(value, key=lambda key: key.encode("utf-16-be"))
        }
    raise ValueError("canonical wire JSON requires safe integer numbers and string keys")


def canonical_json(value: object, *, exclude: set[str] | None = None) -> bytes:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if exclude and isinstance(value, dict):
        value = {k: v for k, v in value.items() if k not in exclude}
    try:
        return json.dumps(
            _json_value(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (UnicodeError, TypeError) as exc:
        raise ValueError("invalid canonical JSON") from exc


def canonical_digest(value: object, *, exclude: set[str] | None = None) -> str:
    return hashlib.sha256(canonical_json(value, exclude=exclude)).hexdigest()


class DeviceDefinition(StrictModel):
    device_key: OpaqueKey
    display_number: Annotated[int, Field(ge=1, le=64)]
    protocol_version: Literal[3, 4]
    order: Annotated[int, Field(ge=0, le=63)]


class LayoutPosition(StrictModel):
    device_key: OpaqueKey
    x_cm: Annotated[int, Field(ge=-100_000, le=100_000)]
    y_cm: Annotated[int, Field(ge=-100_000, le=100_000)]
    z_cm: Annotated[int, Field(ge=-100_000, le=100_000)]


class RunLayout(StrictModel):
    posture: Literal["standing", "seated", "custom", "unregistered"]
    positions: Annotated[list[LayoutPosition], Field(max_length=64)]


class RunConfig(StrictModel):
    lamp_count: Annotated[int, Field(ge=1, le=64)]
    sensor: Annotated[int, Field(ge=0, le=5)]
    flash: Annotated[int, Field(ge=0, le=5)]
    on_sound: bool
    off_sound: bool
    color: Color
    stimulus_interval_ms: Annotated[int, Field(ge=0, le=60_000)]
    timeout_ms: Annotated[int, Field(ge=10, le=655_350, multiple_of=10)]
    restart_interval_ms: Annotated[int, Field(ge=0, le=60_000)] | None
    duration_ms: Annotated[int, Field(ge=1, le=MAX_DURATION_MS)] | None
    task_count: Annotated[int, Field(ge=1, le=1000)] | None
    seed: Annotated[int, Field(ge=0, le=4_294_967_295)]
    schedule_algorithm: Literal["balanced_cycle_v1"]
    base_device_key: OpaqueKey | None
    target_color: Color | None
    distractor_color: Color | None


class RunManifest(StrictModel):
    protocol_version: Literal[1]
    run_id: OpaqueKey
    actor_account_id: UUIDText
    subject_person_id: UUIDText
    connector_id: OpaqueKey
    rule_version: Literal["reactionx-v1"]
    mode: Mode
    config: RunConfig
    devices: Annotated[list[DeviceDefinition], Field(min_length=1, max_length=64)]
    layout: RunLayout
    manifest_digest: Digest


class RunEvent(StrictModel):
    seq: PositiveIndex
    activation_id: OpaqueKey | None
    device_key: OpaqueKey | None
    kind: EventKind
    host_elapsed_ms: ElapsedMs
    device_ticks_10ms: Annotated[int, Field(ge=0, le=65_535)] | None
    round: PositiveIndex | None
    group: PositiveIndex | None
    cycle: PositiveIndex | None
    stimulus_role: StimulusRole | None
    color: Color | None
    termination_reason: Literal["host_timeout", "hardware_timeout"] | None
    quality_code: QualityCode | None

    @model_validator(mode="after")
    def event_shape(self) -> Self:
        if self.kind == "invalid":
            if (
                self.quality_code is None
                or self.device_ticks_10ms is not None
                or self.termination_reason is not None
            ):
                raise ValueError("invalid evidence needs a quality code, never a scored resolution")
            return self
        if self.quality_code is not None:
            raise ValueError("quality codes require invalid evidence kind")
        boundary = self.kind.startswith(("group_", "cycle_"))
        if boundary:
            if any(
                v is not None
                for v in (
                    self.activation_id,
                    self.device_key,
                    self.stimulus_role,
                    self.color,
                    self.device_ticks_10ms,
                    self.termination_reason,
                    self.round,
                )
            ):
                raise ValueError("boundary events cannot carry stimulus fields")
            if self.kind.startswith("group_"):
                if self.group is None or self.cycle is not None:
                    raise ValueError("group boundary needs only a group number")
            elif self.cycle is None or self.group is not None:
                raise ValueError("cycle boundary needs only a cycle number")
        else:
            if any(
                v is None
                for v in (self.activation_id, self.device_key, self.stimulus_role, self.color)
            ):
                raise ValueError("stimulus event requires activation/device/role/color")
            if self.kind == "hit":
                if self.device_ticks_10ms is None or self.termination_reason is not None:
                    raise ValueError("hit requires device time and no timeout reason")
            elif self.device_ticks_10ms is not None:
                raise ValueError("only hits have device time")
            if (self.kind in ("timeout", "ignored")) != (self.termination_reason is not None):
                raise ValueError("timeout/ignore needs explicit host or hardware timeout")
        return self


class RunEnd(StrictModel):
    kind: EndKind
    active_elapsed_ms: ElapsedMs


class ResultEnvelope(StrictModel):
    protocol_version: Literal[1]
    engine_version: Literal["reactionx-engine-v1"]
    run_id: OpaqueKey
    manifest_digest: Digest
    events: Annotated[list[RunEvent], Field(max_length=MAX_EVENTS)]
    end: RunEnd

    @model_validator(mode="after")
    def byte_bound(self) -> Self:
        if len(canonical_json(self)) > MAX_RESULT_BYTES:
            raise ValueError("result exceeds 8 MiB")
        return self


class ResultReceipt(StrictModel):
    run_id: OpaqueKey
    result_id: OpaqueKey
    digest: Digest
    saved_at: UTCStamp


class ScanPayload(StrictModel):
    duration_ms: Annotated[int, Field(ge=1, le=30_000)]


class IdentifyPayload(StrictModel):
    device_key: OpaqueKey
    color: Color
    duration_ms: Annotated[int, Field(ge=1, le=3000)]


class PreparePayload(StrictModel):
    manifest: RunManifest


class StartPayload(StrictModel):
    manifest_digest: Digest


class StopPayload(StrictModel):
    reason: Literal["user_stopped", "lease_expired", "disconnected"]


class DeviceKeysPayload(StrictModel):
    device_keys: Annotated[list[OpaqueKey], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def unique_keys(self) -> Self:
        if len(set(self.device_keys)) != len(self.device_keys):
            raise ValueError("duplicate connection device keys")
        return self


class ManualActivatePayload(StrictModel):
    device_key: OpaqueKey
    color: Color


class CommandEnvelope(StrictModel):
    protocol_version: Literal[1]
    operation_id: OpaqueKey
    run_id: OpaqueKey | None
    actor_account_id: UUIDText
    subject_person_id: UUIDText | None
    connector_id: OpaqueKey
    sequence: Annotated[int, Field(ge=1, le=2_147_483_647)]
    expires_at: UTCStamp
    lease_remaining_ms: Annotated[int, Field(ge=0, le=15_000)]
    kind: Literal[
        "scan", "identify", "connect", "disconnect", "prepare", "start", "stop", "manual_activate"
    ]
    payload: (
        ScanPayload
        | IdentifyPayload
        | DeviceKeysPayload
        | PreparePayload
        | StartPayload
        | StopPayload
        | ManualActivatePayload
    )

    @model_validator(mode="after")
    def kind_payload_and_binding(self) -> Self:
        expected = {
            "scan": ScanPayload,
            "identify": IdentifyPayload,
            "connect": DeviceKeysPayload,
            "disconnect": DeviceKeysPayload,
            "prepare": PreparePayload,
            "start": StartPayload,
            "stop": StopPayload,
            "manual_activate": ManualActivatePayload,
        }[self.kind]
        if not isinstance(self.payload, expected):
            raise ValueError("payload does not match command kind")  # noqa: TRY004
        bound = self.kind in ("prepare", "start", "stop", "manual_activate")
        if bound != (self.run_id is not None and self.subject_person_id is not None):
            raise ValueError("command kind requires exact run/subject binding")
        if not bound and (self.run_id is not None or self.subject_person_id is not None):
            raise ValueError("unbound command cannot carry partial binding")
        if isinstance(self.payload, PreparePayload):
            from .rule_versions import validate_manifest

            manifest = self.payload.manifest
            validate_manifest(manifest)
            if (self.run_id, self.actor_account_id, self.subject_person_id, self.connector_id) != (
                manifest.run_id,
                manifest.actor_account_id,
                manifest.subject_person_id,
                manifest.connector_id,
            ):
                raise ValueError("prepare manifest ownership mismatch")
        return self


def validate_event_batch(events: list[RunEvent]) -> None:
    if (
        len(events) > MAX_BATCH_EVENTS
        or len(canonical_json([e.model_dump() for e in events])) > MAX_BATCH_BYTES
    ):
        raise ValueError("event batch exceeds 100 events or 256 KiB")


def protocol_schema() -> dict[str, object]:
    """Generate the checked-in JSON Schema without dependency on application startup."""
    from pydantic.json_schema import models_json_schema

    models = (CommandEnvelope, RunManifest, RunEvent, RunEnd, ResultEnvelope, ResultReceipt)
    _, bundle = models_json_schema([(m, "validation") for m in models])
    bundle["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    bundle["title"] = "ReactionX v1 frozen wire contracts"
    bundle["x-reactionx-scheduler-lateness-ms"] = SCHEDULER_LATENESS_MS
    return bundle
