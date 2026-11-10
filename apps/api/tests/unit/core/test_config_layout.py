from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core import config


@pytest.mark.parametrize(
    "origin",
    [
        "http://example.test:18080",
        "https://user:secret@example.test",
        "https://example.test/",
        "https://example.test/path",
        "https://example.test?origin=evil",
        "https://example.test#fragment",
        "https://EXAMPLE.test",
        "https://example.test:99999",
        "https://example.test\\@evil.test",
        "https://example.test\n",
    ],
)
def test_reactionx_trusted_origin_rejects_ambiguous_or_non_https_settings(test_settings, origin):
    values = test_settings.model_dump()
    values["reactionx_public_origin"] = origin
    with pytest.raises(ValidationError):
        config.Settings(**values)


def test_reactionx_trusted_origin_is_explicit_config_not_web_localhost(test_settings):
    assert test_settings.reactionx_public_origin == "https://skymoonzyj.l.cd:18080"
    values = test_settings.model_dump()
    values["reactionx_public_origin"] = "https://new-site.example.test:18080"
    assert (
        config.Settings(**values).reactionx_public_origin == "https://new-site.example.test:18080"
    )


def test_runtime_root_supports_the_container_application_layout() -> None:
    assert hasattr(config, "_runtime_root_from_config_path")
    assert config._runtime_root_from_config_path(Path("/app/app/core/config.py")) == Path("/app")


def test_runtime_root_preserves_the_repository_layout_default() -> None:
    assert hasattr(config, "_runtime_root_from_config_path")
    assert config._runtime_root_from_config_path(
        Path("/workspace/apps/api/app/core/config.py")
    ) == Path("/workspace")
