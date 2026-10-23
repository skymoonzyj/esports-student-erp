from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

REQUIRED_CASE_IDS: dict[tuple[str, str], tuple[str, ...]] = {
    ("esports-pc.spec.ts", "esports-desktop-1440"): (
        "EA-PC-REAL-001",
        "EA-PC-REAL-002",
    ),
    ("esports-touch.spec.ts", "esports-touch-844x390"): (
        "EA-TOUCH-REAL-001",
        "EA-TOUCH-REAL-002",
    ),
    ("esports-recovery.spec.ts", "esports-desktop-1440"): (
        "EA-REC-FORMAL-001",
        "EA-REC-PRACTICE-002",
        "EA-REC-MULTITAB-003",
    ),
    ("esports-capabilities.spec.ts", "esports-desktop-1440"): (
        "EA-CAP-DESKTOP-001",
        "EA-CAP-NONPHONE-002",
    ),
    ("esports-capabilities.spec.ts", "esports-touch-844x390"): (
        "EA-CAP-RESPONSIVE-003",
        "EA-CAP-ROTATE-004",
    ),
    ("esports-capabilities.spec.ts", "esports-webkit-capabilities"): ("EA-CAP-WEBKIT-005",),
}

_CASE_ID = re.compile(r"^(?:\[(EA-[A-Z0-9-]+)\]|(EA-[A-Z0-9-]+))(?=\s|$)")
_STAT_KEYS = ("expected", "skipped", "unexpected", "flaky")
_SAFE_SOURCE_FILES = frozenset(
    {
        "esports-pc.spec.ts",
        "esports-touch.spec.ts",
        "esports-recovery.spec.ts",
        "esports-capabilities.spec.ts",
        "esportsAssessment.ts",
    }
)
_SAFE_STACK_SOURCE = re.compile(
    rf"(?<![A-Za-z0-9_.-])(?P<file>{'|'.join(map(re.escape, sorted(_SAFE_SOURCE_FILES)))})"
    r":(?P<line>\d{1,7}):(?P<column>\d{1,7})(?!\d)"
)

FailureKind = Literal[
    "test_failed",
    "test_timed_out",
    "test_interrupted",
    "test_skipped",
    "unknown_failure",
]
PhaseCheckpoint = Literal[
    "pc_initial_trial",
    "pc_pointer_lock_exit",
    "pc_reentry_trial",
    "pc_trial_finish",
    "pc_formal_countdown",
    "pc_formal_running",
]
HostPhase = Literal[
    "intro",
    "preflight",
    "trial",
    "starting",
    "countdown",
    "running",
    "pending",
    "saved",
    "interrupted",
]
ObservedPhase = HostPhase | Literal["missing", "unknown"]

_PHASE_ANNOTATION_TYPE = "esports.phase.v1"
_MAX_PHASE_DESCRIPTION_BYTES = 512
_PHASE_DESCRIPTION_KEYS = frozenset(
    {
        "checkpoint",
        "expected",
        "observed",
        "pointer_lock",
        "document_focus",
        "webgl2",
    }
)
_PHASE_CHECKPOINTS: frozenset[PhaseCheckpoint] = frozenset(
    {
        "pc_initial_trial",
        "pc_pointer_lock_exit",
        "pc_reentry_trial",
        "pc_trial_finish",
        "pc_formal_countdown",
        "pc_formal_running",
    }
)
_HOST_PHASES: frozenset[HostPhase] = frozenset(
    {
        "intro",
        "preflight",
        "trial",
        "starting",
        "countdown",
        "running",
        "pending",
        "saved",
        "interrupted",
    }
)
_OBSERVED_PHASES: frozenset[ObservedPhase] = frozenset({*_HOST_PHASES, "missing", "unknown"})


@dataclass(frozen=True)
class SafeSource:
    file: str
    line: int
    column: int


@dataclass(frozen=True)
class SafeFailureDiagnostic:
    case_id: str
    kind: FailureKind
    source: SafeSource | None


@dataclass(frozen=True)
class SafePhaseDiagnostic:
    checkpoint: PhaseCheckpoint
    expected: HostPhase
    observed: ObservedPhase
    pointer_lock: bool
    document_focus: bool
    webgl2: bool


@dataclass(frozen=True)
class EsportsPlaywrightSummary:
    spec: str
    project: str
    required_case_ids: list[str]
    passed_case_ids: list[str]
    passed: int
    skipped: int
    failed: int
    flaky: int
    retries: int
    reasons: list[str]
    failure_diagnostics: list[SafeFailureDiagnostic]
    phase_diagnostic: SafePhaseDiagnostic | None = None


@dataclass(frozen=True)
class _ObservedCase:
    file_name: str
    case_id: str
    project: str
    status: str
    results: list[dict[str, object]]


class EsportsPlaywrightReportError(RuntimeError):
    def __init__(
        self,
        reason: str,
        *,
        phase_diagnostic: SafePhaseDiagnostic | None = None,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.phase_diagnostic = phase_diagnostic


class _DuplicateJsonKey(ValueError):
    pass


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    keys = [key for key, _ in pairs]
    if len(keys) != len(set(keys)):
        raise _DuplicateJsonKey
    return dict(pairs)


def _integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _required(spec: str, project: str) -> tuple[str, ...]:
    try:
        return REQUIRED_CASE_IDS[(spec, project)]
    except KeyError as exc:
        raise EsportsPlaywrightReportError("unsupported_spec_project") from exc


def _safe_summary(
    spec: str,
    project: str,
    *,
    reason: str,
    phase_diagnostic: SafePhaseDiagnostic | None = None,
) -> EsportsPlaywrightSummary:
    required = REQUIRED_CASE_IDS.get((spec, project), ())
    return EsportsPlaywrightSummary(
        spec=spec,
        project=project,
        required_case_ids=list(required),
        passed_case_ids=[],
        passed=0,
        skipped=0,
        failed=0,
        flaky=0,
        retries=0,
        reasons=[reason],
        failure_diagnostics=[],
        phase_diagnostic=phase_diagnostic,
    )


def _load_report(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EsportsPlaywrightReportError("report_unavailable") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EsportsPlaywrightReportError("malformed_report") from exc


def _flatten_tests(report: dict[str, object]) -> list[_ObservedCase]:
    suites = report.get("suites")
    if not isinstance(suites, list):
        raise EsportsPlaywrightReportError("malformed_report")
    found: list[_ObservedCase] = []
    for suite in suites:
        if not isinstance(suite, dict):
            raise EsportsPlaywrightReportError("malformed_report")
        specs = suite.get("specs")
        if not isinstance(specs, list):
            raise EsportsPlaywrightReportError("malformed_report")
        for spec_entry in specs:
            if not isinstance(spec_entry, dict):
                raise EsportsPlaywrightReportError("malformed_report")
            file_value = spec_entry.get("file", suite.get("file"))
            title = spec_entry.get("title")
            tests = spec_entry.get("tests")
            if (
                not isinstance(file_value, str)
                or not isinstance(title, str)
                or not isinstance(tests, list)
            ):
                raise EsportsPlaywrightReportError("malformed_report")
            match = _CASE_ID.match(title)
            if match is None:
                raise EsportsPlaywrightReportError("malformed_case_id")
            for test in tests:
                if not isinstance(test, dict):
                    raise EsportsPlaywrightReportError("malformed_report")
                project = test.get("projectName")
                status = test.get("status")
                results = test.get("results")
                if (
                    not isinstance(project, str)
                    or not isinstance(status, str)
                    or not isinstance(results, list)
                    or not all(isinstance(result, dict) for result in results)
                ):
                    raise EsportsPlaywrightReportError("malformed_report")
                found.append(
                    _ObservedCase(
                        file_name=Path(file_value).name,
                        case_id=match.group(1) or match.group(2),
                        project=project,
                        status=status,
                        results=results,
                    )
                )
    return found


def _positive_source_integer(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1 or value > 1_000_000:
        return None
    return value


def _safe_source_from_location(location: object) -> SafeSource | None:
    if not isinstance(location, dict):
        return None
    file_value = location.get("file")
    line = _positive_source_integer(location.get("line"))
    column = _positive_source_integer(location.get("column"))
    if not isinstance(file_value, str) or line is None or column is None:
        return None
    file_name = re.split(r"[/\\\\]", file_value)[-1]
    if file_name not in _SAFE_SOURCE_FILES:
        return None
    return SafeSource(file=file_name, line=line, column=column)


def _safe_source_from_stack(stack: object) -> SafeSource | None:
    if not isinstance(stack, str):
        return None
    match = _SAFE_STACK_SOURCE.search(stack)
    if match is None:
        return None
    line = _positive_source_integer(int(match.group("line")))
    column = _positive_source_integer(int(match.group("column")))
    if line is None or column is None:
        return None
    return SafeSource(file=match.group("file"), line=line, column=column)


def _failure_kind(status: str) -> FailureKind:
    kinds: dict[str, FailureKind] = {
        "failed": "test_failed",
        "timedOut": "test_timed_out",
        "interrupted": "test_interrupted",
        "skipped": "test_skipped",
    }
    return kinds.get(status, "unknown_failure")


def _failure_diagnostic(observed: _ObservedCase) -> SafeFailureDiagnostic | None:
    failed_result = next(
        (result for result in observed.results if result.get("status") != "passed"),
        None,
    )
    if failed_result is None:
        if observed.status == "expected":
            return None
        observed_kinds: dict[str, FailureKind] = {
            "skipped": "test_skipped",
            "unexpected": "test_failed",
            "flaky": "test_failed",
        }
        return SafeFailureDiagnostic(
            case_id=observed.case_id,
            kind=observed_kinds.get(observed.status, "unknown_failure"),
            source=None,
        )

    result_status = failed_result.get("status")
    kind = _failure_kind(result_status) if isinstance(result_status, str) else "unknown_failure"
    error = failed_result.get("error")
    source = None
    if isinstance(error, dict):
        source = _safe_source_from_location(error.get("location"))
        if source is None:
            source = _safe_source_from_stack(error.get("stack"))
    return SafeFailureDiagnostic(
        case_id=observed.case_id,
        kind=kind,
        source=source,
    )


def _phase_diagnostic(cases: Sequence[_ObservedCase]) -> SafePhaseDiagnostic | None:
    annotations: list[dict[str, object]] = []
    for case in cases:
        for result in case.results:
            if result.get("status") == "passed":
                continue
            result_annotations = result.get("annotations")
            if result_annotations is None:
                continue
            if not isinstance(result_annotations, list):
                return None
            for annotation in result_annotations:
                if not isinstance(annotation, dict):
                    continue
                if annotation.get("type") == _PHASE_ANNOTATION_TYPE:
                    annotations.append(annotation)

    if len(annotations) != 1:
        return None
    description = annotations[0].get("description")
    if not isinstance(description, str):
        return None
    try:
        description_bytes = description.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if len(description_bytes) > _MAX_PHASE_DESCRIPTION_BYTES:
        return None
    try:
        payload = json.loads(
            description,
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (json.JSONDecodeError, _DuplicateJsonKey):
        return None
    if not isinstance(payload, dict) or set(payload) != _PHASE_DESCRIPTION_KEYS:
        return None

    checkpoint = payload["checkpoint"]
    expected = payload["expected"]
    observed = payload["observed"]
    pointer_lock = payload["pointer_lock"]
    document_focus = payload["document_focus"]
    webgl2 = payload["webgl2"]
    if not isinstance(checkpoint, str) or checkpoint not in _PHASE_CHECKPOINTS:
        return None
    if not isinstance(expected, str) or expected not in _HOST_PHASES:
        return None
    if not isinstance(observed, str) or observed not in _OBSERVED_PHASES:
        return None
    if not all(isinstance(value, bool) for value in (pointer_lock, document_focus, webgl2)):
        return None
    return SafePhaseDiagnostic(
        checkpoint=checkpoint,
        expected=expected,
        observed=observed,
        pointer_lock=pointer_lock,
        document_focus=document_focus,
        webgl2=webgl2,
    )


def summarize_report(
    report: object,
    *,
    spec: str,
    project: str,
) -> EsportsPlaywrightSummary:
    required = _required(spec, project)
    if not isinstance(report, dict):
        raise EsportsPlaywrightReportError("malformed_report")
    stats = report.get("stats")
    if not isinstance(stats, dict):
        raise EsportsPlaywrightReportError("malformed_report")
    counts = {key: _integer(stats.get(key)) for key in _STAT_KEYS}
    if any(value is None for value in counts.values()):
        raise EsportsPlaywrightReportError("malformed_report")

    tests = _flatten_tests(report)
    spec_tests = [test for test in tests if test.file_name == spec]
    if not spec_tests:
        raise EsportsPlaywrightReportError("required_spec_missing")
    pair_tests = [test for test in spec_tests if test.project == project]
    if not pair_tests:
        raise EsportsPlaywrightReportError("required_project_missing")
    if len(pair_tests) != len(tests):
        raise EsportsPlaywrightReportError("unexpected_report_entry")

    observed_ids = [test.case_id for test in pair_tests]
    if len(observed_ids) != len(set(observed_ids)):
        raise EsportsPlaywrightReportError("duplicate_case")
    if set(observed_ids) - set(required):
        raise EsportsPlaywrightReportError("unexpected_case")

    expected_status_counts = Counter(test.status for test in pair_tests)
    allowed_statuses = set(_STAT_KEYS)
    if set(expected_status_counts) - allowed_statuses:
        raise EsportsPlaywrightReportError("malformed_report")
    if any(counts[key] != expected_status_counts[key] for key in _STAT_KEYS):
        raise EsportsPlaywrightReportError("inconsistent_counts")
    if sum(value for value in counts.values() if value is not None) != len(pair_tests):
        raise EsportsPlaywrightReportError("inconsistent_counts")

    retry_values_by_id: dict[str, list[int]] = {}
    for observed in pair_tests:
        if not observed.results:
            raise EsportsPlaywrightReportError("malformed_report")
        retry_values: list[int] = []
        for result in observed.results:
            retry = _integer(result.get("retry"))
            result_status = result.get("status")
            if retry is None or not isinstance(result_status, str):
                raise EsportsPlaywrightReportError("malformed_report")
            retry_values.append(retry)
        retry_values_by_id[observed.case_id] = retry_values

    phase_diagnostic = _phase_diagnostic(pair_tests)
    if set(required) - set(observed_ids):
        raise EsportsPlaywrightReportError(
            "required_case_missing",
            phase_diagnostic=phase_diagnostic,
        )

    retries = 0
    passed_ids: list[str] = []
    reasons: list[str] = []
    failure_diagnostics: list[SafeFailureDiagnostic] = []
    reason_by_status = {
        "skipped": "required_case_skipped",
        "unexpected": "required_case_failed",
        "flaky": "required_case_flaky",
    }
    by_id = {test.case_id: test for test in pair_tests}
    for case_id in required:
        observed = by_id[case_id]
        retry_values = retry_values_by_id[case_id]
        retries += max(retry_values)
        diagnostic = _failure_diagnostic(observed)
        if diagnostic is not None:
            failure_diagnostics.append(diagnostic)
        if len(observed.results) != 1 or retry_values != [0]:
            if "required_case_retried" not in reasons:
                reasons.append("required_case_retried")
            continue
        if observed.status != "expected":
            reason = reason_by_status[observed.status]
            if reason not in reasons:
                reasons.append(reason)
            continue
        if observed.results[0].get("status") != "passed":
            if "required_case_failed" not in reasons:
                reasons.append("required_case_failed")
            continue
        passed_ids.append(case_id)

    return EsportsPlaywrightSummary(
        spec=spec,
        project=project,
        required_case_ids=list(required),
        passed_case_ids=passed_ids,
        passed=counts["expected"] or 0,
        skipped=counts["skipped"] or 0,
        failed=counts["unexpected"] or 0,
        flaky=counts["flaky"] or 0,
        retries=retries,
        reasons=reasons,
        failure_diagnostics=failure_diagnostics,
        phase_diagnostic=phase_diagnostic,
    )


def _write_summary(path: Path, summary: EsportsPlaywrightSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(summary)
    if payload["phase_diagnostic"] is None:
        del payload["phase_diagnostic"]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
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
    except EsportsPlaywrightReportError as exc:
        summary = _safe_summary(
            args.spec,
            args.project,
            reason=exc.reason,
            phase_diagnostic=exc.phase_diagnostic,
        )
        _write_summary(args.output, summary)
        print(
            "esports_playwright_invalid "
            f"spec={args.spec} project={args.project} reason={exc.reason}",
            file=sys.stderr,
        )
        return 2

    _write_summary(args.output, summary)
    line = (
        "esports_playwright_summary "
        f"spec={summary.spec} project={summary.project} "
        f"passed={summary.passed} skipped={summary.skipped} "
        f"failed={summary.failed} flaky={summary.flaky} retries={summary.retries}"
    )
    if summary.reasons:
        diagnostic = summary.failure_diagnostics[0] if summary.failure_diagnostics else None
        diagnostic_text = ""
        if diagnostic is not None:
            diagnostic_text = f" failure={diagnostic.kind}"
            if diagnostic.source is not None:
                diagnostic_text += (
                    f" source={diagnostic.source.file}:"
                    f"{diagnostic.source.line}:{diagnostic.source.column}"
                )
        print(
            f"{line} reason={summary.reasons[0]}{diagnostic_text}",
            file=sys.stderr,
        )
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
