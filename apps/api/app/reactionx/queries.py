"""Bounded browser/client DTOs; B1 envelopes remain the wire authority."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

from app.reactionx.rule_versions import RULES
from app.reactionx.schemas import (
    CommandEnvelope,
    DeviceDefinition,
    DeviceKeysPayload,
    IdentifyPayload,
    ManualActivatePayload,
    Mode,
    OpaqueKey,
    RunConfig,
    RunLayout,
    RunManifest,
    ScanPayload,
    StrictModel,
    UUIDText,
)

ACTIVE_STATES = ("prepared", "ready", "starting", "running", "stopping", "unconfirmed")
SUPPORTED_CONNECTOR_VERSION = "0.3.0"
HEARTBEAT_FRESH_SECONDS = 10


class PairRequest(StrictModel):
    browser_tab_id: UUIDText | None = None


class PrepareRequest(StrictModel):
    operation_id: UUIDText
    person_id: UUIDText
    connector_id: UUIDText
    tab_id: UUIDText
    mode: Mode
    config: RunConfig
    devices: Annotated[list[DeviceDefinition], Field(min_length=1, max_length=64)]
    layout: RunLayout


class OperationRequest(StrictModel):
    operation_id: UUIDText
    tab_id: UUIDText


class LeaseRequest(StrictModel):
    tab_id: UUIDText


class ManualRequest(OperationRequest):
    payload: ManualActivatePayload


class DeviceCommandRequest(OperationRequest):
    kind: Literal["scan", "connect", "disconnect", "identify"]
    payload: ScanPayload | DeviceKeysPayload | IdentifyPayload

    @model_validator(mode="after")
    def matching_payload(self) -> "DeviceCommandRequest":
        expected = {
            "scan": ScanPayload,
            "connect": DeviceKeysPayload,
            "disconnect": DeviceKeysPayload,
            "identify": IdentifyPayload,
        }[self.kind]
        if not isinstance(self.payload, expected):
            raise ValueError("command payload mismatch")  # noqa: TRY004
        return self


class InventoryDevice(DeviceDefinition):
    label: str = Field(min_length=1, max_length=80)
    connected: bool


class HeartbeatRequest(StrictModel):
    protocol_version: Annotated[int, Field(ge=1, le=100)]
    connector_version: str = Field(min_length=1, max_length=32, pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")
    instance_id: UUIDText
    devices: Annotated[list[InventoryDevice], Field(max_length=64)]

    @model_validator(mode="after")
    def distinct_devices(self) -> "HeartbeatRequest":
        if len({d.device_key for d in self.devices}) != len(self.devices):
            raise ValueError("duplicate inventory device key")
        return self


class AckRequest(StrictModel):
    status: Literal["accepted", "completed", "failed"]
    run_state: Literal["ready", "running", "stopped_confirmed", "stopped_unconfirmed"] | None
    off_confirmed_device_keys: Annotated[list[OpaqueKey], Field(max_length=64)]

    @model_validator(mode="after")
    def evidence_shape(self) -> "AckRequest":
        if len(set(self.off_confirmed_device_keys)) != len(self.off_confirmed_device_keys):
            raise ValueError("duplicate off evidence")
        if self.run_state == "stopped_confirmed":
            if self.status != "completed" or not self.off_confirmed_device_keys:
                raise ValueError("confirmed stop needs completed OFF evidence")
        elif self.off_confirmed_device_keys:
            raise ValueError("OFF evidence belongs only to terminal confirmation")
        if self.run_state == "ready" and self.status != "completed":
            raise ValueError("ready requires completed preparation")
        if self.run_state == "running" and self.status != "accepted":
            raise ValueError("running is nonterminal execution acknowledgement")
        if self.run_state == "stopped_unconfirmed" and self.status != "failed":
            raise ValueError("unconfirmed stop is failure")
        return self


class RunView(BaseModel):
    run_id: UUID
    connector_id: UUID
    status: str
    manifest: RunManifest
    lease_expires_at: datetime
    lease_remaining_ms: int
    stop_confirmed: bool
    result_saved: bool = False


class LeaseView(BaseModel):
    run_id: UUID
    lease_expires_at: datetime
    lease_remaining_ms: int
    renew_after_ms: int = 5000


class CommandView(BaseModel):
    operation_id: UUID
    run_id: UUID | None
    kind: str
    sequence: int
    status: str
    run_status: str | None


class ConnectorView(BaseModel):
    connector_id: UUID
    name: str
    online: bool
    compatible: bool
    locally_confirmed: bool
    protocol_version: int | None
    connector_version: str | None
    devices: list[InventoryDevice]
    active_run_id: UUID | None
    attention_required: bool
    unsafe_operation_id: UUID | None
    unsafe_device_keys: list[OpaqueKey]


class PollView(BaseModel):
    commands: list[CommandEnvelope]
    cursor: int
    poll_after_ms: int
    run: RunView | None
    lease_remaining_ms: int


class HeartbeatView(BaseModel):
    connector: ConnectorView
    run: RunView | None
    safety_only: bool


def mode_availability() -> list[dict[str, str | bool | None]]:
    return [
        {
            "mode": mode,
            "available": mode == "manual",
            "verification_status": "practice_only"
            if mode == "manual"
            else rule.verification_status,
            "reason": "practice_only" if mode == "manual" else "hardware_unverified",
        }
        for mode, rule in RULES.items()
    ]
