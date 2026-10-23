from __future__ import annotations

from pathlib import Path

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
