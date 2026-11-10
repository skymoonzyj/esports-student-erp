"""Strict allowlist summary: raw Playwright errors/DOM/stdout are never copied."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

CASES = ("RX-UI-001", "RX-HTTP-002", "RX-UI-003", "RX-UI-004", "RX-UI-005")
PROJECT = "reactionx-desktop-1440"
SPEC = "reactionx-integration.spec.ts"
PHASES = frozenset(
    {
        "login",
        "pair_created",
        "machine_claimed",
        "browser_confirmed",
        "machine_online",
        "local_ui_ready",
        "manual_setup",
        "manual_prepared",
        "manual_ready",
        "manual_running",
        "manual_action",
        "manual_stop",
        "manual_receipt",
        "manual_report",
        "formal_runs",
        "disconnect",
        "identity_switch",
        "uncertain_start_delivered",
        "uncertainty_declared",
        "archive_requested",
        "archive_confirmed",
    }
)


def summarize(report: Any) -> dict[str, object]:
    counts: Counter[str] = Counter()
    passed = skipped = failed = 0
    valid = True
    safe_cases: list[dict[str, str]] = []
    failure_sources: list[dict[str, object]] = []
    phases: dict[str, str] = {}

    def walk(suite: dict[str, Any]) -> None:
        nonlocal passed, skipped, failed, valid
        for spec in suite.get("specs", []):
            match = re.match(r"^(RX-(?:UI|HTTP)-[0-9]{3})(?:\s|$)", spec.get("title", ""))
            case = match[1] if match else ""
            for test in spec.get("tests", []):
                results = test.get("results", [])
                counts[case] += 1
                if case in CASES:
                    for annotation in test.get("annotations", []):
                        if (
                            annotation.get("type") == "reactionx.phase.v1"
                            and annotation.get("description") in PHASES
                        ):
                            phases[case] = annotation["description"]
                if (
                    case not in CASES
                    or Path(spec.get("file", "")).name != SPEC
                    or test.get("projectName") != PROJECT
                    or len(results) != 1
                    or results[0].get("retry") != 0
                ):
                    valid = False
                status = results[-1].get("status") if results else None
                if case in CASES:
                    safe_cases.append(
                        {
                            "case_id": case,
                            "status": status
                            if status in {"passed", "failed", "skipped", "timedOut", "interrupted"}
                            else "unknown",
                        }
                    )
                    if status != "passed" and results:
                        stack = results[-1].get("error", {}).get("stack", "")
                        for source in re.finditer(
                            r"reactionx-integration\.spec\.ts:([0-9]{1,7}):([0-9]{1,7})(?![0-9])",
                            stack,
                        ):
                            failure_sources.append(
                                {"case_id": case, "line": int(source[1]), "column": int(source[2])}
                            )
                if status == "passed" and test.get("status") == "expected":
                    passed += 1
                elif status == "skipped":
                    skipped += 1
                else:
                    failed += 1
        for child in suite.get("suites", []):
            walk(child)

    try:
        for suite in report["suites"]:
            walk(suite)
        valid = valid and counts == Counter(CASES)
    except (TypeError, KeyError, AttributeError, IndexError):
        valid = False
    ok = valid and passed == len(CASES) and skipped == 0 and failed == 0
    return {
        "spec": SPEC,
        "project": PROJECT,
        "case_ids": [case for case in CASES if counts[case]],
        "cases": safe_cases,
        "failure_sources": failure_sources,
        "phases": phases,
        "passed": passed,
        "skipped": skipped,
        "failed": failed,
        "ok": ok,
        "reason": "passed" if ok else "acceptance_incomplete",
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        # Raw report exists only in the launcher's private temporary directory.
        result = summarize(json.loads(args.report.read_text(encoding="utf-8")))
    except (OSError, ValueError, RecursionError):
        result = {"spec": SPEC, "project": PROJECT, "ok": False, "reason": "report_unreadable"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
