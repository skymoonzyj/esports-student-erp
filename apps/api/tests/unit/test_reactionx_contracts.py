"""Wire regressions: strict input, canonical bytes, bindings and bounded batches."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

FIXTURES_PATH = Path(__file__).resolve().parents[4] / "shared/reactionx/v1/fixtures.json"


def fixtures() -> dict:
    return json.loads(FIXTURES_PATH.read_text())


def test_protocol_module_exists() -> None:
    assert importlib.util.find_spec("app.reactionx") is not None


def test_nine_modes_parse_and_have_fixed_canonical_digests() -> None:
    from app.reactionx.rule_versions import validate_manifest
    from app.reactionx.schemas import ResultEnvelope, RunManifest, canonical_digest, canonical_json

    data = fixtures()
    assert len(data["cases"]) == 9
    for case in data["cases"] + data["timed_cases"]:
        manifest = RunManifest.model_validate(case["manifest"])
        assert validate_manifest(manifest).mode == case["name"]
        ResultEnvelope.model_validate(case["result"])
        assert canonical_digest(manifest, exclude={"manifest_digest"}) == manifest.manifest_digest
    for vector in data["canonical_vectors"]:
        assert canonical_json(vector["value"]).decode() == vector["canonical_json"]
        assert canonical_digest(vector["value"]) == vector["sha256"]


@pytest.mark.parametrize("value", [True, 1.5, "1", -1, 900_001])
def test_wire_elapsed_is_strict_bounded_integer(value: object) -> None:
    from app.reactionx.schemas import RunEvent

    event = copy.deepcopy(fixtures()["cases"][1]["result"]["events"][0])
    event["host_elapsed_ms"] = value
    with pytest.raises(ValueError):
        RunEvent.model_validate(event)


def test_unknown_fields_duplicate_devices_and_tampered_digest_are_rejected() -> None:
    from app.reactionx.rule_versions import validate_manifest
    from app.reactionx.schemas import RunManifest

    original = fixtures()["cases"][1]["manifest"]
    for mutation in ("unknown", "duplicate", "digest", "layout", "count", "unsupported"):
        value = copy.deepcopy(original)
        if mutation == "unknown":
            value["rankable"] = True
        elif mutation == "duplicate":
            value["devices"][1]["device_key"] = value["devices"][0]["device_key"]
        elif mutation == "digest":
            value["manifest_digest"] = "0" * 64
        elif mutation == "layout":
            value["layout"]["positions"] = []
        elif mutation == "count":
            value["config"]["task_count"] = 29
        else:
            value["devices"][0]["protocol_version"] = 2
        with pytest.raises(ValueError):
            validate_manifest(RunManifest.model_validate(value))


def test_command_kind_limits_payload_and_requires_run_binding() -> None:
    from app.reactionx.schemas import CommandEnvelope

    value = fixtures()["commands"]["scan"]
    CommandEnvelope.model_validate(value)
    for patch in ({"payload": {"bytes": [1, 2]}}, {"kind": "start"}, {"sequence": True}):
        with pytest.raises(ValueError):
            CommandEnvelope.model_validate({**value, **patch})
    for command in fixtures()["commands"].values():
        CommandEnvelope.model_validate(command)


def test_receipt_and_result_reject_unbounded_or_client_scoring_fields() -> None:
    from app.reactionx.schemas import ResultEnvelope, ResultReceipt

    ResultReceipt.model_validate(fixtures()["receipt"])
    result = fixtures()["cases"][1]["result"]
    for patch in (
        {"rankable": True},
        {"events": result["events"] * 200},
        {"end": {"kind": "time_completed", "active_elapsed_ms": 900_001}},
    ):
        with pytest.raises(ValueError):
            ResultEnvelope.model_validate({**result, **patch})


def test_canonical_encoding_rejects_non_integer_numbers_and_non_json_values() -> None:
    from app.reactionx.schemas import canonical_json

    for value in (float("nan"), float("inf"), 1.25, {"a": 1.0}, {1: "bad"}):
        with pytest.raises(ValueError):
            canonical_json(value)


def test_device_identity_not_display_label_distinguishes_lamps() -> None:
    from app.reactionx.rule_versions import validate_manifest
    from app.reactionx.schemas import RunManifest, canonical_digest

    manifest = copy.deepcopy(fixtures()["cases"][1]["manifest"])
    manifest["devices"][1]["display_number"] = 1
    manifest["manifest_digest"] = canonical_digest(manifest, exclude={"manifest_digest"})
    assert validate_manifest(RunManifest.model_validate(manifest)).mode == "standard"


def test_connection_commands_and_manual_command_are_typed_and_mode_bound() -> None:
    from app.reactionx.rule_versions import validate_command_for_manifest
    from app.reactionx.schemas import CommandEnvelope, RunManifest

    data = fixtures()
    for kind in ("connect", "disconnect", "manual_activate"):
        CommandEnvelope.model_validate(data["commands"][kind])
    manual = CommandEnvelope.model_validate(data["commands"]["manual_activate"])
    validate_command_for_manifest(manual, RunManifest.model_validate(data["cases"][0]["manifest"]))
    wrong = manual.model_copy(update={"run_id": "fixture-standard"})
    with pytest.raises(ValueError):
        validate_command_for_manifest(
            wrong, RunManifest.model_validate(data["cases"][1]["manifest"])
        )


def test_event_batch_and_full_result_limits_do_not_truncate() -> None:
    from app.reactionx.schemas import RunEvent, validate_event_batch

    event = RunEvent.model_validate(fixtures()["cases"][1]["result"]["events"][0])
    validate_event_batch([event] * 100)
    with pytest.raises(ValueError):
        validate_event_batch([event] * 101)


def test_checked_in_schema_matches_exported_schema() -> None:
    from app.reactionx.schemas import protocol_schema

    schema_path = FIXTURES_PATH.with_name("schema.json")
    assert json.loads(schema_path.read_text()) == protocol_schema()


@pytest.mark.parametrize(
    "field,value", [("protocol_version", True), ("expires_at", "2026-99-31T25:00:00.000Z")]
)
def test_commands_reject_bool_versions_and_impossible_utc_dates(field: str, value: object) -> None:
    from app.reactionx.schemas import CommandEnvelope

    command = {**fixtures()["commands"]["scan"], field: value}
    with pytest.raises(ValueError):
        CommandEnvelope.model_validate(command)


@pytest.mark.parametrize("extra", ["constructor", "toString", "__proto__"])
def test_protocol_rejects_own_json_prototype_named_fields(extra: str) -> None:
    from app.reactionx.schemas import CommandEnvelope

    command = {**fixtures()["commands"]["scan"], extra: {"unexpected": True}}
    with pytest.raises(ValueError):
        CommandEnvelope.model_validate(command)


@pytest.mark.parametrize("year,valid", [("0000", False), ("0001", True), ("9999", True)])
def test_utc_year_range_is_one_through_9999(year: str, valid: bool) -> None:
    from app.reactionx.schemas import CommandEnvelope

    command = {**fixtures()["commands"]["scan"], "expires_at": f"{year}-01-01T00:00:00.000Z"}
    if valid:
        CommandEnvelope.model_validate(command)
    else:
        with pytest.raises(ValueError):
            CommandEnvelope.model_validate(command)
