import pytest

from app.evaluations.adapter_registry import (
    AdapterDefinition,
    AdapterNotSupported,
    require_adapter,
    supported_adapters,
)

EXPECTED_ADAPTERS = {
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
EXPECTED_TEST_ADAPTER = {
    ("synthetic", "1.0.0"): AdapterDefinition(
        adapter_id="synthetic",
        adapter_version="1.0.0",
        scoring_rule_version="1",
        result_schema_version="1",
    )
}


def test_registry_freezes_first_release_adapter_versions() -> None:
    assert supported_adapters() == EXPECTED_ADAPTERS
    for key, expected in EXPECTED_ADAPTERS.items():
        assert require_adapter(*key) == expected


def test_test_only_processing_adapter_requires_an_explicit_opt_in() -> None:
    assert supported_adapters() == EXPECTED_ADAPTERS
    assert supported_adapters(include_test_only=True) == {
        **EXPECTED_ADAPTERS,
        **EXPECTED_TEST_ADAPTER,
    }


@pytest.mark.parametrize(
    ("adapter_id", "adapter_version"),
    [
        ("lol", "latest"),
        ("valorant", "2.0.0"),
        ("peace_elite", "0.9.0"),
        ("synthetic", "1.0.0"),
    ],
)
def test_registry_rejects_unknown_or_test_only_adapters(
    adapter_id: str,
    adapter_version: str,
) -> None:
    with pytest.raises(AdapterNotSupported):
        require_adapter(adapter_id, adapter_version)
