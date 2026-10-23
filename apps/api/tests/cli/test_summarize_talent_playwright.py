from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli.summarize_talent_playwright import main

SPEC = "talent-dynamic-vision.spec.ts"
PROJECT = "desktop-1440"


def _report(
    *,
    status: str = "expected",
    results: list[dict[str, object]] | None = None,
    spec: str = SPEC,
    project: str = PROJECT,
    stats: dict[str, int] | None = None,
) -> dict[str, object]:
    return {
        "stats": stats
        or {
            "expected": 1 if status == "expected" else 0,
            "skipped": 1 if status == "skipped" else 0,
            "unexpected": 1 if status == "unexpected" else 0,
            "flaky": 1 if status == "flaky" else 0,
        },
        "suites": [
            {
                "file": f"tests/{spec}",
                "specs": [
                    {
                        "file": f"tests/{spec}",
                        "tests": [
                            {
                                "projectName": project,
                                "status": status,
                                "results": results
                                or [{"status": "passed", "retry": 0}],
                            }
                        ],
                    }
                ],
            }
        ],
    }


def _run(
    tmp_path: Path,
    report: object,
    *,
    spec: str = SPEC,
    project: str = PROJECT,
) -> tuple[int, dict[str, object]]:
    report_path = tmp_path / "raw-report.json"
    output_path = tmp_path / "safe-summary.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    status = main(
        [
            "--report",
            str(report_path),
            "--output",
            str(output_path),
            "--spec",
            spec,
            "--project",
            project,
        ]
    )

    return status, json.loads(output_path.read_text(encoding="utf-8"))


def test_exact_required_test_passes_with_true_structured_counts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    status, summary = _run(
        tmp_path,
        _report(results=[{"status": "failed", "retry": 0}, {"status": "passed", "retry": 1}]),
    )

    assert status == 0
    assert summary == {
        "spec": SPEC,
        "project": PROJECT,
        "passed": 1,
        "skipped": 0,
        "failed": 0,
        "flaky": 0,
        "retries": 1,
        "reasons": [],
    }
    assert capsys.readouterr().out.strip() == (
        "talent_playwright_summary "
        f"spec={SPEC} project={PROJECT} passed=1 skipped=0 failed=0 flaky=0 retries=1"
    )


@pytest.mark.parametrize(
    ("report", "reason", "expected_counts"),
    [
        (_report(status="skipped"), "required_test_skipped", (0, 1, 0)),
        (_report(status="unexpected"), "required_test_failed", (0, 0, 1)),
        (
            _report(status="flaky", stats={"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 1}),
            "required_test_flaky",
            (0, 0, 0),
        ),
    ],
)
def test_non_clean_outcomes_fail_with_fixed_safe_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    report: object,
    reason: str,
    expected_counts: tuple[int, int, int],
) -> None:
    status, summary = _run(tmp_path, report)

    assert status == 1
    assert (summary["passed"], summary["skipped"], summary["failed"]) == expected_counts
    assert summary["reasons"] == [reason]
    emitted = capsys.readouterr()
    assert reason in emitted.err
    assert "answer" not in emitted.err.casefold()
    assert "stimulus" not in emitted.err.casefold()


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        ({"private": {"stimulus": "981204"}}, "malformed_report"),
        (_report(spec="talent-tests.spec.ts"), "required_spec_missing"),
        (_report(project="mobile-360"), "required_project_missing"),
        (
            {
                **_report(),
                "stats": {"expected": 2, "skipped": 0, "unexpected": 0, "flaky": 0},
            },
            "required_test_count",
        ),
    ],
)
def test_invalid_reports_fail_without_echoing_raw_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    report: object,
    reason: str,
) -> None:
    status, summary = _run(tmp_path, report)

    assert status == 2
    assert summary["reasons"] == [reason]
    emitted = capsys.readouterr()
    combined = emitted.out + emitted.err
    assert "981204" not in combined
    assert "stimulus" not in combined.casefold()


def test_missing_report_fails_without_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output_path = tmp_path / "safe-summary.json"

    status = main(
        [
            "--report",
            str(tmp_path / "missing.json"),
            "--output",
            str(output_path),
            "--spec",
            SPEC,
            "--project",
            PROJECT,
        ]
    )

    assert status == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["reasons"] == [
        "report_unavailable"
    ]
    emitted = capsys.readouterr()
    assert "Traceback" not in emitted.err

