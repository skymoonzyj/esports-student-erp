from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class TalentPlaywrightSummary:
    spec: str
    project: str
    passed: int
    skipped: int
    failed: int
    flaky: int
    retries: int
    reasons: list[str]


class TalentPlaywrightReportError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _safe_summary(
    spec: str,
    project: str,
    *,
    reason: str,
) -> TalentPlaywrightSummary:
    return TalentPlaywrightSummary(
        spec=spec,
        project=project,
        passed=0,
        skipped=0,
        failed=0,
        flaky=0,
        retries=0,
        reasons=[reason],
    )


def _load_report(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TalentPlaywrightReportError("report_unavailable") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TalentPlaywrightReportError("malformed_report") from exc


def _flatten_tests(report: dict[str, object]) -> list[tuple[str, dict[str, object]]]:
    suites = report.get("suites")
    if not isinstance(suites, list):
        raise TalentPlaywrightReportError("malformed_report")
    found: list[tuple[str, dict[str, object]]] = []
    for suite in suites:
        if not isinstance(suite, dict):
            raise TalentPlaywrightReportError("malformed_report")
        specs = suite.get("specs")
        if not isinstance(specs, list):
            raise TalentPlaywrightReportError("malformed_report")
        for spec_entry in specs:
            if not isinstance(spec_entry, dict):
                raise TalentPlaywrightReportError("malformed_report")
            file_value = spec_entry.get("file", suite.get("file"))
            if not isinstance(file_value, str):
                raise TalentPlaywrightReportError("malformed_report")
            tests = spec_entry.get("tests")
            if not isinstance(tests, list):
                raise TalentPlaywrightReportError("malformed_report")
            for test in tests:
                if not isinstance(test, dict):
                    raise TalentPlaywrightReportError("malformed_report")
                found.append((Path(file_value).name, test))
    return found


def summarize_report(
    report: object,
    *,
    spec: str,
    project: str,
) -> TalentPlaywrightSummary:
    if not isinstance(report, dict):
        raise TalentPlaywrightReportError("malformed_report")
    stats = report.get("stats")
    if not isinstance(stats, dict):
        raise TalentPlaywrightReportError("malformed_report")
    counts = {
        key: _integer(stats.get(key))
        for key in ("expected", "skipped", "unexpected", "flaky")
    }
    if any(value is None for value in counts.values()):
        raise TalentPlaywrightReportError("malformed_report")
    passed = counts["expected"]
    skipped = counts["skipped"]
    failed = counts["unexpected"]
    flaky = counts["flaky"]
    assert passed is not None
    assert skipped is not None
    assert failed is not None
    assert flaky is not None
    if passed + skipped + failed + flaky != 1:
        raise TalentPlaywrightReportError("required_test_count")

    tests = _flatten_tests(report)
    matching_spec = [(file_name, test) for file_name, test in tests if file_name == spec]
    if not matching_spec:
        raise TalentPlaywrightReportError("required_spec_missing")
    matching_project = [
        test for _, test in matching_spec if test.get("projectName") == project
    ]
    if not matching_project:
        raise TalentPlaywrightReportError("required_project_missing")
    if len(tests) != 1 or len(matching_project) != 1:
        raise TalentPlaywrightReportError("required_test_count")

    test = matching_project[0]
    status = test.get("status")
    expected_status = {
        "expected": (1, 0, 0, 0),
        "skipped": (0, 1, 0, 0),
        "unexpected": (0, 0, 1, 0),
        "flaky": (0, 0, 0, 1),
    }
    if not isinstance(status, str) or status not in expected_status:
        raise TalentPlaywrightReportError("malformed_report")
    if expected_status[status] != (passed, skipped, failed, flaky):
        raise TalentPlaywrightReportError("inconsistent_counts")

    results = test.get("results")
    if not isinstance(results, list) or not results:
        raise TalentPlaywrightReportError("malformed_report")
    retry_values: list[int] = []
    for result in results:
        if not isinstance(result, dict):
            raise TalentPlaywrightReportError("malformed_report")
        retry = _integer(result.get("retry"))
        if retry is None:
            raise TalentPlaywrightReportError("malformed_report")
        retry_values.append(retry)

    reasons: list[str] = []
    if skipped:
        reasons.append("required_test_skipped")
    elif failed:
        reasons.append("required_test_failed")
    elif flaky:
        reasons.append("required_test_flaky")
    return TalentPlaywrightSummary(
        spec=spec,
        project=project,
        passed=passed,
        skipped=skipped,
        failed=failed,
        flaky=flaky,
        retries=max(retry_values),
        reasons=reasons,
    )


def _write_summary(path: Path, summary: TalentPlaywrightSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(asdict(summary), ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--spec", required=True)
    parser.add_argument("--project", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        summary = summarize_report(
            _load_report(args.report),
            spec=args.spec,
            project=args.project,
        )
    except TalentPlaywrightReportError as exc:
        summary = _safe_summary(args.spec, args.project, reason=exc.reason)
        _write_summary(args.output, summary)
        print(
            "talent_playwright_invalid "
            f"spec={args.spec} project={args.project} reason={exc.reason}",
            file=sys.stderr,
        )
        return 2

    _write_summary(args.output, summary)
    line = (
        "talent_playwright_summary "
        f"spec={summary.spec} project={summary.project} "
        f"passed={summary.passed} skipped={summary.skipped} "
        f"failed={summary.failed} flaky={summary.flaky} retries={summary.retries}"
    )
    if summary.reasons:
        print(f"{line} reason={summary.reasons[0]}", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
