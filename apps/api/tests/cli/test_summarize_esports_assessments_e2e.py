from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cli.summarize_esports_assessments_e2e import main

EXPECTED_CASES = {
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


def _report(
    spec: str,
    project: str,
    case_ids: tuple[str, ...],
    *,
    status_by_case: dict[str, str] | None = None,
    results_by_case: dict[str, list[dict[str, object]]] | None = None,
    bracketed_titles: bool = False,
    title_suffix: str = "synthetic safe title",
) -> dict[str, object]:
    statuses = status_by_case or {}
    results = results_by_case or {}
    specs: list[dict[str, object]] = []
    counts = {"expected": 0, "skipped": 0, "unexpected": 0, "flaky": 0}
    for case_id in case_ids:
        status = statuses.get(case_id, "expected")
        counts[status] += 1
        specs.append(
            {
                "file": f"tests/{spec}",
                "title": (
                    f"[{case_id}] {title_suffix}"
                    if bracketed_titles
                    else f"{case_id} {title_suffix}"
                ),
                "tests": [
                    {
                        "projectName": project,
                        "status": status,
                        "results": results.get(
                            case_id,
                            [{"status": "passed", "retry": 0}],
                        ),
                    }
                ],
            }
        )
    return {
        "stats": counts,
        "suites": [{"file": f"tests/{spec}", "specs": specs}],
    }


def test_accepts_actual_playwright_unbracketed_case_title_prefix(
    tmp_path: Path,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]

    status, summary = _run(
        tmp_path,
        _report(spec, project, cases, bracketed_titles=False),
        spec=spec,
        project=project,
    )

    assert status == 0
    assert summary["passed_case_ids"] == list(cases)


def test_bracketed_case_title_prefix_remains_compatible(tmp_path: Path) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]

    status, summary = _run(
        tmp_path,
        _report(spec, project, cases, bracketed_titles=True),
        spec=spec,
        project=project,
    )

    assert status == 0
    assert summary["passed_case_ids"] == list(cases)


@pytest.mark.parametrize(
    "malformed_title",
    [
        "EA-PC-REAL-001x synthetic",
        "EA-PC-REAL-001: synthetic",
        "[EA-PC-REAL-001]synthetic",
    ],
)
def test_case_title_prefix_requires_a_strict_delimiter(
    tmp_path: Path,
    malformed_title: str,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    report = _report(spec, project, EXPECTED_CASES[(spec, project)])
    report["suites"][0]["specs"][0]["title"] = malformed_title  # type: ignore[index]

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 2
    assert summary["reasons"] == ["malformed_case_id"]


def _run(
    tmp_path: Path,
    report: object,
    *,
    spec: str,
    project: str,
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


@pytest.mark.parametrize(
    ("spec", "project", "case_ids"),
    [(*key, case_ids) for key, case_ids in EXPECTED_CASES.items()],
)
def test_exact_first_run_case_sets_pass(
    tmp_path: Path,
    spec: str,
    project: str,
    case_ids: tuple[str, ...],
) -> None:
    status, summary = _run(
        tmp_path,
        _report(spec, project, case_ids),
        spec=spec,
        project=project,
    )

    assert status == 0
    assert summary == {
        "spec": spec,
        "project": project,
        "required_case_ids": list(case_ids),
        "passed_case_ids": list(case_ids),
        "passed": len(case_ids),
        "skipped": 0,
        "failed": 0,
        "flaky": 0,
        "retries": 0,
        "reasons": [],
        "failure_diagnostics": [],
    }


@pytest.mark.parametrize(
    ("case_ids", "reason"),
    [
        (("EA-PC-REAL-001",), "required_case_missing"),
        (
            ("EA-PC-REAL-001", "EA-PC-REAL-002", "EA-PC-EXTRA-999"),
            "unexpected_case",
        ),
        (
            ("EA-PC-REAL-001", "EA-PC-REAL-001", "EA-PC-REAL-002"),
            "duplicate_case",
        ),
    ],
)
def test_case_identity_must_match_the_fixed_mapping(
    tmp_path: Path,
    case_ids: tuple[str, ...],
    reason: str,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"

    status, summary = _run(
        tmp_path,
        _report(spec, project, case_ids),
        spec=spec,
        project=project,
    )

    assert status == 2
    assert summary["reasons"] == [reason]


@pytest.mark.parametrize(
    ("status_name", "reason"),
    [
        ("skipped", "required_case_skipped"),
        ("unexpected", "required_case_failed"),
        ("flaky", "required_case_flaky"),
    ],
)
def test_non_clean_case_status_fails(
    tmp_path: Path,
    status_name: str,
    reason: str,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]

    status, summary = _run(
        tmp_path,
        _report(
            spec,
            project,
            cases,
            status_by_case={cases[0]: status_name},
        ),
        spec=spec,
        project=project,
    )

    assert status == 1
    assert summary["reasons"] == [reason]


def test_retry_then_pass_cannot_masquerade_as_first_run_green(tmp_path: Path) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    status, summary = _run(
        tmp_path,
        _report(
            spec,
            project,
            cases,
            results_by_case={
                cases[0]: [
                    {"status": "failed", "retry": 0},
                    {"status": "passed", "retry": 1},
                ]
            },
        ),
        spec=spec,
        project=project,
    )

    assert status == 1
    assert summary["retries"] == 1
    assert summary["reasons"] == ["required_case_retried"]


def test_retry_count_aggregates_across_two_cases(tmp_path: Path) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    retried = [
        {"status": "failed", "retry": 0},
        {"status": "passed", "retry": 1},
    ]

    status, summary = _run(
        tmp_path,
        _report(
            spec,
            project,
            cases,
            results_by_case={case_id: retried for case_id in cases},
        ),
        spec=spec,
        project=project,
    )

    assert status == 1
    assert summary["retries"] == 2
    assert summary["reasons"] == ["required_case_retried"]


def test_failure_diagnostics_keep_only_allowlisted_kind_and_source(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    private = "PrivateStudent42 raw-payload https://private.example"
    report = _report(
        spec,
        project,
        cases,
        status_by_case={case_id: "unexpected" for case_id in cases},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "error": {
                        "message": private,
                        "stack": f"{private}\n/private/{private}/not-allowed.ts:99:7",
                        "location": {
                            "file": f"/private/{private}/apps/web/tests/{spec}",
                            "line": 13,
                            "column": 5,
                        },
                    },
                }
            ],
            cases[1]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "error": {
                        "message": private,
                        "stack": (
                            f"{private}\n/private/{private}/apps/web/tests/helpers/"
                            "esportsAssessment.ts:74:9"
                        ),
                    },
                }
            ],
        },
        bracketed_titles=False,
        title_suffix=private,
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["failure_diagnostics"] == [
        {
            "case_id": cases[0],
            "kind": "test_failed",
            "source": {"file": spec, "line": 13, "column": 5},
        },
        {
            "case_id": cases[1],
            "kind": "test_failed",
            "source": {
                "file": "esportsAssessment.ts",
                "line": 74,
                "column": 9,
            },
        },
    ]
    emitted = capsys.readouterr()
    serialized = json.dumps(summary) + emitted.out + emitted.err
    assert private not in serialized
    assert "not-allowed.ts" not in serialized
    assert "failure=test_failed" in emitted.err
    assert f"source={spec}:13:5" in emitted.err


def test_failure_diagnostic_omits_unallowlisted_source_without_changing_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    private = "PrivateStudent42 raw-payload https://private.example"
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "timedOut",
                    "retry": 0,
                    "error": {
                        "message": private,
                        "stack": f"{private}\n/private/{private}/not-allowed.ts:99:7",
                        "location": {
                            "file": f"/private/{private}/not-allowed.ts",
                            "line": 99,
                            "column": 7,
                        },
                    },
                }
            ]
        },
        title_suffix=private,
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert summary["failure_diagnostics"] == [
        {
            "case_id": cases[0],
            "kind": "test_timed_out",
            "source": None,
        }
    ]
    emitted = capsys.readouterr()
    serialized = json.dumps(summary) + emitted.out + emitted.err
    assert private not in serialized
    assert "not-allowed.ts" not in serialized
    assert "failure=test_timed_out" in emitted.err


def test_valid_runtime_phase_annotation_adds_only_the_safe_diagnostic(
    tmp_path: Path,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    description = json.dumps(
        {
            "checkpoint": "pc_initial_trial",
            "expected": "trial",
            "observed": "preflight",
            "pointer_lock": False,
            "document_focus": True,
            "webgl2": True,
        },
        separators=(",", ":"),
    )
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": description,
                        }
                    ],
                }
            ]
        },
    )
    # Playwright duplicates runtime annotations at test level. The parser must
    # bind only the result-level copy to avoid treating this as a duplicate.
    test_entry = report["suites"][0]["specs"][0]["tests"][0]  # type: ignore[index]
    test_entry["annotations"] = [{"type": "esports.phase.v1", "description": description}]

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert summary["phase_diagnostic"] == {
        "checkpoint": "pc_initial_trial",
        "expected": "trial",
        "observed": "preflight",
        "pointer_lock": False,
        "document_focus": True,
        "webgl2": True,
    }


def test_single_case_diagnostic_keeps_the_missing_case_gate(
    tmp_path: Path,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    description = json.dumps(
        {
            "checkpoint": "pc_formal_countdown",
            "expected": "countdown",
            "observed": "trial",
            "pointer_lock": True,
            "document_focus": True,
            "webgl2": True,
        },
        separators=(",", ":"),
    )
    report = _report(
        spec,
        project,
        (cases[0],),
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": description,
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 2
    assert summary["reasons"] == ["required_case_missing"]
    assert summary["passed_case_ids"] == []
    assert summary["passed"] == 0
    assert summary["phase_diagnostic"] == {
        "checkpoint": "pc_formal_countdown",
        "expected": "countdown",
        "observed": "trial",
        "pointer_lock": True,
        "document_focus": True,
        "webgl2": True,
    }


@pytest.mark.parametrize(
    ("defect", "reason"),
    [
        ("status", "malformed_report"),
        ("stats", "inconsistent_counts"),
        ("empty_results", "malformed_report"),
        ("retry", "malformed_report"),
        ("result_status", "malformed_report"),
    ],
)
def test_single_case_diagnostic_requires_a_well_formed_observed_subset(
    tmp_path: Path,
    defect: str,
    reason: str,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    report = _report(
        spec,
        project,
        (cases[0],),
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": json.dumps(
                                {
                                    "checkpoint": "pc_initial_trial",
                                    "expected": "trial",
                                    "observed": "preflight",
                                    "pointer_lock": False,
                                    "document_focus": True,
                                    "webgl2": True,
                                }
                            ),
                        }
                    ],
                }
            ]
        },
    )
    test_entry = report["suites"][0]["specs"][0]["tests"][0]  # type: ignore[index]
    result = test_entry["results"][0]
    if defect == "status":
        test_entry["status"] = "private-status"
    elif defect == "stats":
        report["stats"]["unexpected"] = 99  # type: ignore[index]
    elif defect == "empty_results":
        test_entry["results"] = []
    elif defect == "retry":
        result["retry"] = True
    else:
        result["status"] = 7

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 2
    assert summary["reasons"] == [reason]
    assert summary["passed_case_ids"] == []
    assert summary["passed"] == 0
    assert "phase_diagnostic" not in summary


def test_single_case_hostile_phase_annotation_is_not_retained(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    private = "PrivateStudent42 raw-payload https://private.example"
    report = _report(
        spec,
        project,
        (cases[0],),
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": json.dumps(
                                {
                                    "checkpoint": "pc_initial_trial",
                                    "expected": "trial",
                                    "observed": "preflight",
                                    "pointer_lock": False,
                                    "document_focus": True,
                                    "webgl2": True,
                                    "private": private,
                                }
                            ),
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 2
    assert summary["reasons"] == ["required_case_missing"]
    assert summary["passed_case_ids"] == []
    assert "phase_diagnostic" not in summary
    emitted = capsys.readouterr()
    assert private not in json.dumps(summary) + emitted.out + emitted.err


def test_phase_annotation_with_extra_keys_is_omitted_without_leaking(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    private = "PrivateStudent42 raw-payload https://private.example"
    description = json.dumps(
        {
            "checkpoint": "pc_initial_trial",
            "expected": "trial",
            "observed": "preflight",
            "pointer_lock": False,
            "document_focus": True,
            "webgl2": True,
            "private": private,
        },
        separators=(",", ":"),
    )
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": description,
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert "phase_diagnostic" not in summary
    emitted = capsys.readouterr()
    assert private not in json.dumps(summary) + emitted.out + emitted.err


def test_duplicate_runtime_phase_annotations_are_omitted(
    tmp_path: Path,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    annotation = {
        "type": "esports.phase.v1",
        "description": json.dumps(
            {
                "checkpoint": "pc_pointer_lock_exit",
                "expected": "trial",
                "observed": "missing",
                "pointer_lock": False,
                "document_focus": False,
                "webgl2": True,
            },
            separators=(",", ":"),
        ),
    }
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [annotation, annotation],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert summary["retries"] == 0
    assert "phase_diagnostic" not in summary


def test_duplicate_phase_description_keys_are_omitted(
    tmp_path: Path,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    description = (
        '{"checkpoint":"pc_initial_trial","checkpoint":"pc_trial_finish",'
        '"expected":"pending","observed":"running","pointer_lock":true,'
        '"document_focus":true,"webgl2":true}'
    )
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": description,
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert "phase_diagnostic" not in summary


def test_successful_result_phase_annotation_is_ignored(
    tmp_path: Path,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    description = json.dumps(
        {
            "checkpoint": "pc_formal_running",
            "expected": "running",
            "observed": "running",
            "pointer_lock": True,
            "document_focus": True,
            "webgl2": True,
        },
        separators=(",", ":"),
    )
    report = _report(
        spec,
        project,
        cases,
        results_by_case={
            cases[0]: [
                {
                    "status": "passed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": description,
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 0
    assert summary["passed_case_ids"] == list(cases)
    assert "phase_diagnostic" not in summary


@pytest.mark.parametrize("field", ["pointer_lock", "document_focus", "webgl2"])
def test_phase_annotation_requires_real_booleans(
    tmp_path: Path,
    field: str,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    payload: dict[str, object] = {
        "checkpoint": "pc_trial_finish",
        "expected": "pending",
        "observed": "running",
        "pointer_lock": True,
        "document_focus": True,
        "webgl2": True,
    }
    payload[field] = 1
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": json.dumps(payload),
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert "phase_diagnostic" not in summary


@pytest.mark.parametrize(
    "description",
    [
        "not-json PrivateStudent42",
        json.dumps(
            {
                "checkpoint": "pc_private_checkpoint",
                "expected": "trial",
                "observed": "preflight",
                "pointer_lock": False,
                "document_focus": True,
                "webgl2": True,
            }
        ),
        json.dumps(
            {
                "checkpoint": "pc_initial_trial",
                "expected": "private-phase",
                "observed": "preflight",
                "pointer_lock": False,
                "document_focus": True,
                "webgl2": True,
            }
        ),
        json.dumps(
            {
                "checkpoint": "pc_initial_trial",
                "expected": "trial",
                "observed": "private-phase",
                "pointer_lock": False,
                "document_focus": True,
                "webgl2": True,
            }
        ),
        (
            json.dumps(
                {
                    "checkpoint": "pc_initial_trial",
                    "expected": "trial",
                    "observed": "preflight",
                    "pointer_lock": False,
                    "document_focus": True,
                    "webgl2": True,
                },
                separators=(",", ":"),
            ).ljust(513)
        ),
    ],
)
def test_unknown_malformed_or_oversized_phase_annotations_are_omitted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    description: str,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    cases = EXPECTED_CASES[(spec, project)]
    report = _report(
        spec,
        project,
        cases,
        status_by_case={cases[0]: "unexpected"},
        results_by_case={
            cases[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": description,
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == 1
    assert summary["reasons"] == ["required_case_failed"]
    assert "phase_diagnostic" not in summary
    emitted = capsys.readouterr()
    serialized = json.dumps(summary) + emitted.out + emitted.err
    assert "PrivateStudent42" not in serialized
    assert "private-phase" not in serialized
    assert "pc_private_checkpoint" not in serialized


@pytest.mark.parametrize(
    ("real_only", "expected_status", "expected_reason", "expected_passed"),
    [
        (False, 1, "required_case_failed", 1),
        (True, 2, "required_case_missing", 0),
    ],
)
def test_surrogate_phase_description_is_safely_omitted_for_full_and_single_case(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    real_only: bool,
    expected_status: int,
    expected_reason: str,
    expected_passed: int,
) -> None:
    spec, project = "esports-pc.spec.ts", "esports-desktop-1440"
    required = EXPECTED_CASES[(spec, project)]
    cases = (required[0],) if real_only else required
    private = "PrivateStudent42 raw-payload https://private.example"
    report = _report(
        spec,
        project,
        cases,
        status_by_case={required[0]: "unexpected"},
        results_by_case={
            required[0]: [
                {
                    "status": "failed",
                    "retry": 0,
                    "annotations": [
                        {
                            "type": "esports.phase.v1",
                            "description": f"\ud800{private}",
                        }
                    ],
                }
            ]
        },
    )

    status, summary = _run(tmp_path, report, spec=spec, project=project)

    assert status == expected_status
    assert summary["reasons"] == [expected_reason]
    assert summary["passed"] == expected_passed
    assert "phase_diagnostic" not in summary
    emitted = capsys.readouterr()
    serialized = json.dumps(summary) + emitted.out + emitted.err
    assert "Traceback" not in serialized
    assert private not in serialized


@pytest.mark.parametrize(
    ("report", "reason"),
    [
        ({"private": {"password": "DoNotEcho42"}}, "malformed_report"),
        (
            _report(
                "esports-pc.spec.ts",
                "wrong-project",
                EXPECTED_CASES[("esports-pc.spec.ts", "esports-desktop-1440")],
            ),
            "required_project_missing",
        ),
        (
            {
                **_report(
                    "esports-pc.spec.ts",
                    "esports-desktop-1440",
                    EXPECTED_CASES[("esports-pc.spec.ts", "esports-desktop-1440")],
                ),
                "stats": {
                    "expected": 99,
                    "skipped": 0,
                    "unexpected": 0,
                    "flaky": 0,
                },
            },
            "inconsistent_counts",
        ),
    ],
)
def test_invalid_reports_fail_without_echoing_private_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    report: object,
    reason: str,
) -> None:
    status, summary = _run(
        tmp_path,
        report,
        spec="esports-pc.spec.ts",
        project="esports-desktop-1440",
    )

    assert status == 2
    assert summary["reasons"] == [reason]
    emitted = capsys.readouterr()
    assert "DoNotEcho42" not in emitted.out + emitted.err
    assert "password" not in (emitted.out + emitted.err).casefold()


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
            "esports-pc.spec.ts",
            "--project",
            "esports-desktop-1440",
        ]
    )

    assert status == 2
    assert json.loads(output_path.read_text(encoding="utf-8"))["reasons"] == ["report_unavailable"]
    assert "Traceback" not in capsys.readouterr().err
