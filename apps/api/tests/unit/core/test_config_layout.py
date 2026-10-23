from __future__ import annotations

from pathlib import Path

from app.core import config


def test_runtime_root_supports_the_container_application_layout() -> None:
    assert hasattr(config, "_runtime_root_from_config_path")
    assert config._runtime_root_from_config_path(
        Path("/app/app/core/config.py")
    ) == Path("/app")


def test_runtime_root_preserves_the_repository_layout_default() -> None:
    assert hasattr(config, "_runtime_root_from_config_path")
    assert config._runtime_root_from_config_path(
        Path("/workspace/apps/api/app/core/config.py")
    ) == Path("/workspace")
