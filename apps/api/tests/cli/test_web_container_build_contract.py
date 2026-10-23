from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_web_builder_can_create_output_as_unprivileged_node_user() -> None:
    dockerfile = (REPOSITORY_ROOT / "apps/web/Dockerfile").read_text(
        encoding="utf-8"
    )
    source_stage = dockerfile.split("FROM source AS development", maxsplit=1)[0]

    source_lines = [line.strip() for line in source_stage.splitlines()]
    ownership = "RUN chown -R node:node /app"
    user_switch = "USER node"
    assert ownership in source_lines
    assert source_lines.index(ownership) < source_lines.index(user_switch)


def test_retained_context_is_merged_into_each_current_production_compose_config() -> None:
    """Break caught: an override silently drops the named context before build."""
    compose = shutil.which("docker-compose") or "/opt/homebrew/bin/docker-compose"
    if not Path(compose).is_file():
        pytest.skip("Compose client unavailable")
    combinations = [[], ["compose.prod.yaml"], ["compose.e2e.yaml"],
                    ["compose.prod.yaml", "compose.nas-staging.yaml", "compose.nas-lan-staging.yaml"]]
    for extra in combinations:
        args = [compose, "--env-file", "/dev/null", "-f", str(REPOSITORY_ROOT / "compose.yaml")]
        for filename in extra:
            args.extend(["-f", str(REPOSITORY_ROOT / filename)])
        result = subprocess.run([*args, "config", "--format", "json"],
                                env={**os.environ, "COMPOSE_DISABLE_ENV_FILE": "1",
                                     'ERP_WEB_ASSETS_PROTOCOL': '1', 'ERP_WEB_ASSET_INPUT_SHA256': 'a' * 64,
                                     'ERP_WEB_BUILD_ID': 'candidate'},
                                text=True, capture_output=True, check=False)
        assert result.returncode == 0, 'Compose contract configuration failed'
        context = json.loads(result.stdout)["services"]["web"]["build"]["additional_contexts"]
        assert Path(context["erp_retained_assets"]).resolve() == REPOSITORY_ROOT / "apps/web/retained-empty"


def test_production_compose_rejects_missing_web_admission_variables() -> None:
    compose = shutil.which('docker-compose') or '/opt/homebrew/bin/docker-compose'
    if not Path(compose).is_file():
        pytest.skip('Compose client unavailable')
    values = {'ERP_WEB_ASSETS_PROTOCOL': '1', 'ERP_WEB_ASSET_INPUT_SHA256': 'a' * 64,
              'ERP_WEB_BUILD_ID': 'candidate'}
    for missing in values:
        environment = {key: value for key, value in os.environ.items() if key not in values}
        environment.update({key: value for key, value in values.items() if key != missing})
        result = subprocess.run([compose, '--env-file', '/dev/null', '-f', str(REPOSITORY_ROOT / 'compose.yaml'),
                                 '-f', str(REPOSITORY_ROOT / 'compose.prod.yaml'), 'config', '--quiet'],
                                env=environment, text=True, capture_output=True, check=False)
        assert result.returncode != 0, f'missing {missing} was accepted'
