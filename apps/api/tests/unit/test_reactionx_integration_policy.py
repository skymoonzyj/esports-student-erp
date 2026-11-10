"""A test environment alone must never open formal hardware modes."""

import importlib
from typing import get_args

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.reactionx.schemas import Mode


def settings(**values):
    return Settings(
        app_env="test",
        database_url="postgresql://localhost/erp_e2e",
        field_encryption_master_key="synthetic",
        field_lookup_key="synthetic",
        session_token_pepper="synthetic",
        evaluation_tool_service_token="synthetic-a",
        evaluation_tool_web_service_token="synthetic-b",
        evaluation_tool_worker_service_token="synthetic-c",
        **values,
    )


def factory():
    return importlib.import_module("app.reactionx.service_factory")


def test_test_environment_does_not_implicitly_enable_formal_modes(monkeypatch):
    monkeypatch.delenv("REACTIONX_SYNTHETIC_FORMAL_ENABLED", raising=False)
    monkeypatch.delenv("REACTIONX_SYNTHETIC_FORMAL_ENABLED_FILE", raising=False)
    value = settings()
    assert value.reactionx_synthetic_formal_enabled is False
    policy = factory().validation_policy(value)
    assert policy("manual")
    assert not any(policy(mode) for mode in get_args(Mode) if mode != "manual")


@pytest.mark.parametrize("environment", ["development", "production"])
def test_non_test_opt_in_is_rejected_at_configuration_time(environment):
    values = settings().model_dump()
    values.update(app_env=environment, reactionx_synthetic_formal_enabled=True)
    with pytest.raises(ValidationError, match="synthetic"):
        Settings(**values)


def test_explicit_test_opt_in_is_narrow_and_does_not_mutate_rule_status():
    from app.reactionx.rule_versions import RULES

    value = settings(reactionx_synthetic_formal_enabled=True)
    policy = factory().validation_policy(value)
    assert all(policy(mode) for mode in get_args(Mode))
    assert not policy("unknown")
    assert all(rule.verification_status == "unverified" for rule in RULES.values())
    value.app_env = "production"
    assert not factory().validation_policy(value)("standard")


def test_factories_supply_policy_to_all_control_and_result_services():
    value = settings(reactionx_synthetic_formal_enabled=True)
    module = factory()
    for constructor in (module.run_service, module.client_service, module.result_service):
        service = constructor(None, value)
        assert service.origin == "https://skymoonzyj.l.cd:18080"
        assert service.validation_policy("standard")
    assert not module.result_service(None, settings()).validation_policy("standard")
