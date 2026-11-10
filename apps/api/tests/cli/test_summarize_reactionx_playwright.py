"""Only fixed case IDs and statuses may leave the private raw-report directory."""

import importlib
import json

import pytest


def module():
    return importlib.import_module("app.cli.summarize_reactionx_playwright")


def report(status="passed", *, secret="challenge-secret-DO-NOT-KEEP"):
    return {
        "suites": [
            {
                "specs": [
                    {
                        "title": case + " " + secret,
                        "file": "reactionx-integration.spec.ts",
                        "tests": [
                            {
                                "projectName": "reactionx-desktop-1440",
                                "status": "expected" if status == "passed" else "unexpected",
                                "results": [
                                    {
                                        "status": status,
                                        "retry": 0,
                                        "error": {"message": secret},
                                        "stdout": [{"text": secret}],
                                        "attachments": [{"body": secret}],
                                    }
                                ],
                            }
                        ],
                    }
                    for case in ("RX-UI-001", "RX-HTTP-002", "RX-UI-003", "RX-UI-004", "RX-UI-005")
                ]
            }
        ]
    }


def test_success_emits_fixed_counts_without_any_raw_payload():
    result = module().summarize(report())
    assert result["passed"] == 5 and result["failed"] == 0
    assert result["case_ids"] == ["RX-UI-001", "RX-HTTP-002", "RX-UI-003", "RX-UI-004", "RX-UI-005"]
    assert "DO-NOT-KEEP" not in json.dumps(result)


@pytest.mark.parametrize("status", ["failed", "skipped", "timedOut", "interrupted"])
def test_failure_statuses_do_not_leak_errors_console_or_attachments(status):
    result = module().summarize(report(status))
    assert not result["ok"]
    assert result["failed"] + result["skipped"] == 5
    assert "DO-NOT-KEEP" not in json.dumps(result)
    assert result["cases"][0] == {"case_id": "RX-UI-001", "status": status}


def test_empty_duplicate_wrong_project_and_retries_cannot_pass():
    for mutation in ("missing", "duplicate", "project", "retry"):
        value = report()
        specs = value["suites"][0]["specs"]
        if mutation == "missing":
            specs.pop()
        elif mutation == "duplicate":
            specs.append(specs[0])
        elif mutation == "project":
            specs[0]["tests"][0]["projectName"] = "desktop-1440"
        else:
            specs[0]["tests"][0]["results"][0]["retry"] = 1
        assert not module().summarize(value)["ok"]


def test_cli_replaces_arbitrary_parse_errors_with_fixed_diagnostic(tmp_path, capsys):
    path = tmp_path / "raw.json"
    path.write_text("secret-unparseable")
    assert module().main(["--report", str(path), "--output", str(tmp_path / "summary.json")]) == 1
    assert "secret-unparseable" not in capsys.readouterr().out


def test_failure_location_is_allowlisted_without_raw_stack_or_path():
    value = report("failed")
    value["suites"][0]["specs"][0]["tests"][0]["results"][0]["error"]["stack"] = (
        "secret at call (/private/secret/reactionx-integration.spec.ts:129:7)"
    )
    result = module().summarize(value)
    assert result["failure_sources"] == [{"case_id": "RX-UI-001", "line": 129, "column": 7}]
    assert "/private/secret" not in json.dumps(result)


def test_only_fixed_phase_annotations_can_be_persisted():
    value = report("timedOut")
    value["suites"][0]["specs"][0]["tests"][0]["annotations"] = [
        {"type": "reactionx.phase.v1", "description": "machine_online"},
        {"type": "reactionx.phase.v1", "description": "secret-must-not-keep"},
    ]
    result = module().summarize(value)
    assert result["phases"]["RX-UI-001"] == "machine_online"
    assert "must-not-keep" not in json.dumps(result)


def test_four_old_cases_alone_cannot_pass_archive_acceptance():
    value = report()
    value["suites"][0]["specs"].pop()
    assert not module().summarize(value)["ok"]


def test_archive_case_phases_are_fixed_and_no_reason_or_receipt_payload_is_copied():
    value = report()
    value["suites"][0]["specs"][4]["tests"][0]["annotations"] = [
        {"type": "reactionx.phase.v1", "description": "archive_confirmed"},
        {"type": "reactionx.phase.v1", "description": "private-reason-and-receipt"},
    ]
    result = module().summarize(value)
    assert result["ok"] and result["phases"]["RX-UI-005"] == "archive_confirmed"
    assert "private-reason" not in json.dumps(result)
