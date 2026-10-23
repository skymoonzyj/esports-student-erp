from __future__ import annotations

import importlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
API_ROOT = REPOSITORY_ROOT / "apps/api"
SEED_WRAPPER = REPOSITORY_ROOT / "scripts/seed-stage2-acceptance-e2e.py"
SEED_MODULE = API_ROOT / "app/cli/seed_stage2_acceptance_e2e.py"


def test_stage2_combined_seed_is_executable_and_composes_all_domains() -> None:
    assert SEED_WRAPPER.is_file()
    assert SEED_MODULE.is_file()
    assert stat.S_IMODE(SEED_WRAPPER.stat().st_mode) == 0o755

    syntax = subprocess.run(
        [sys.executable, "-m", "py_compile", str(SEED_WRAPPER), str(SEED_MODULE)],
        cwd=REPOSITORY_ROOT,
        env=os.environ,
        capture_output=True,
        text=True,
        check=False,
    )
    assert syntax.returncode == 0, syntax.stderr

    wrapper = SEED_WRAPPER.read_text(encoding="utf-8")
    assert 'import_module("app.cli.seed_stage2_acceptance_e2e").main' in wrapper

    source = SEED_MODULE.read_text(encoding="utf-8")
    seed_calls = (
        "seed_system_evaluation_e2e(",
        "seed_teaching_reports_e2e(",
        "seed_camp_surveys_e2e(",
    )
    assert all(call in source for call in seed_calls)
    assert source.index(seed_calls[0]) < source.index(seed_calls[1]) < source.index(seed_calls[2])


def test_stage2_combined_seed_public_payload_excludes_secrets_and_media_paths() -> None:
    module = importlib.import_module("app.cli.seed_stage2_acceptance_e2e")
    payload = module._public_payload(
        system_result=SimpleNamespace(
            automatic_enrollment_id=UUID("10000000-0000-4000-8000-000000000001"),
            manual_enrollment_id=UUID("10000000-0000-4000-8000-000000000002"),
            automatic_png_path=Path("/private/media/automatic.png"),
            manual_png_path=Path("/private/quarantine/manual.png"),
        ),
        report_result=SimpleNamespace(
            cohort_ids=(UUID("20000000-0000-4000-8000-000000000001"),),
            enrollment_ids=(UUID("20000000-0000-4000-8000-000000000002"),),
        ),
        survey_result=SimpleNamespace(
            cycle_id=UUID("30000000-0000-4000-8000-000000000001"),
            enrollment_id=UUID("30000000-0000-4000-8000-000000000002"),
        ),
    )
    encoded = json.dumps(payload, sort_keys=True)

    assert payload["schema_version"] == 1
    assert payload["modules"] == [
        "system_evaluation",
        "teaching_reports",
        "camp_surveys",
    ]
    assert "/private/" not in encoded
    for forbidden in ("password", "token", "phone", "identity", "credential", "path"):
        assert forbidden not in encoded.lower()


def test_stage2_combined_seed_reuses_one_second_guardian_relationship() -> None:
    combined_source = SEED_MODULE.read_text(encoding="utf-8")
    survey_source = (
        API_ROOT / "app/cli/seed_camp_surveys_e2e.py"
    ).read_text(encoding="utf-8")

    assert "TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID" in combined_source
    assert re.search(
        r"second_guardian_relationship_id\s*=\s*\(?\s*"
        r"TEACHING_REPORT_SECOND_GUARDIAN_RELATIONSHIP_ID",
        combined_source,
    )
    assert (
        "second_guardian_relationship_id: UUID = "
        "CAMP_SURVEY_SECOND_GUARDIAN_RELATIONSHIP_ID"
    ) in survey_source
