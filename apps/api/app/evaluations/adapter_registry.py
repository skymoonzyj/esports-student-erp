from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AdapterDefinition:
    adapter_id: str
    adapter_version: str
    scoring_rule_version: str
    result_schema_version: str


class AdapterNotSupported(ValueError):
    pass


_SUPPORTED_ADAPTERS = {
    ("lol", "1.0.0"): AdapterDefinition(
        adapter_id="lol",
        adapter_version="1.0.0",
        scoring_rule_version="1",
        result_schema_version="1",
    ),
    ("valorant", "1.0.0"): AdapterDefinition(
        adapter_id="valorant",
        adapter_version="1.0.0",
        scoring_rule_version="1",
        result_schema_version="1",
    ),
    ("peace_elite", "1.0.0"): AdapterDefinition(
        adapter_id="peace_elite",
        adapter_version="1.0.0",
        scoring_rule_version="1",
        result_schema_version="1",
    ),
}
_TEST_ONLY_ADAPTERS = {
    ("synthetic", "1.0.0"): AdapterDefinition(
        adapter_id="synthetic",
        adapter_version="1.0.0",
        scoring_rule_version="1",
        result_schema_version="1",
    )
}


def supported_adapters(
    *,
    include_test_only: bool = False,
) -> dict[tuple[str, str], AdapterDefinition]:
    adapters = dict(_SUPPORTED_ADAPTERS)
    if include_test_only:
        adapters.update(_TEST_ONLY_ADAPTERS)
    return adapters


def require_adapter(adapter_id: str, adapter_version: str) -> AdapterDefinition:
    try:
        return _SUPPORTED_ADAPTERS[(adapter_id, adapter_version)]
    except KeyError as exc:
        raise AdapterNotSupported(
            f"unsupported adapter: {adapter_id}/{adapter_version}"
        ) from exc
