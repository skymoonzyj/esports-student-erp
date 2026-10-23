import subprocess
from pathlib import Path

from tests.cli.test_local_container_acceptance_launcher import _fake_toolchain, _run_launcher


def test_intake_scope_metadata_is_exposed_to_schema_preflight():
    manifest = Path(__file__).resolve().parents[4] / "scripts/db-schema-manifest.sh"
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"\nprintf "%s\\n" "${REQUIRED_SCOPE_COLUMNS[@]}"',
            "manifest-test",
            str(manifest),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        "personnel_intake_requests.target_coach_person_ids",
        "personnel_intake_requests.target_team_id",
        "guardian_relationships.requires_verification",
    ]


def test_missing_scope_column_blocks_disposable_acceptance(tmp_path):
    tools = _fake_toolchain(tmp_path)
    (tmp_path / "missing-scope-column").write_text("missing", encoding="utf-8")
    result = _run_launcher(tools.env)
    assert result.returncode == 5
    assert "scope-column: personnel_intake_requests.target_coach_person_ids" in result.stderr


def test_missing_guardian_access_column_blocks_disposable_acceptance(tmp_path):
    tools = _fake_toolchain(tmp_path)
    (tmp_path / "missing-guardian-access-column").write_text("missing", encoding="utf-8")
    result = _run_launcher(tools.env)
    assert result.returncode == 5
    assert "scope-column: guardian_relationships.requires_verification" in result.stderr
