from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


backup = load("erp-backup.py")
helper = load("erp_backup_container.py")


def test_dump_state_parsing_preserves_quotes_multiple_sequences_and_called_flag():
    dumped = '''-- PostgreSQL database dump
SELECT pg_catalog.setval('public.admission_order_number_seq', 42, true);
SELECT pg_catalog.setval('public."student''s ""seq"""', 7, false);
SELECT pg_catalog.setval('public.down_seq', -12, true);
'''
    assert backup.parse_sequence_state(dumped) == [
        ["public.admission_order_number_seq", 42, True],
        ['public."student\'s ""seq"""', 7, False],
        ["public.down_seq", -12, True],
    ]


@pytest.mark.parametrize("dumped", [
    "SELECT pg_catalog.setval('public.s', secret, true);",
    "SELECT pg_catalog.setval('public.s', 1, NULL);",
    ("SELECT pg_catalog.setval('public.s', 1, true);\n"
     "SELECT pg_catalog.setval('public.s', 2, true);"),
])
def test_invalid_or_duplicate_dump_state_is_sanitized(dumped):
    with pytest.raises(backup.BackupError, match="^restore_sequence_evidence_invalid$"):
        backup.parse_sequence_state(dumped)


def test_quoted_sequence_names_in_dump_comments_are_not_executed_or_misparsed():
    dumped = """-- Name: odd setval( seq; Type: SEQUENCE SET; Schema: public
SELECT pg_catalog.setval('public."odd setval( seq"', 3, true);
"""
    assert backup.parse_sequence_state(dumped) == [['public."odd setval( seq"', 3, True]]


@pytest.mark.parametrize("standard_strings,literal", [
    ("on", 'public."student\\seq"'),
    ("off", 'public."student\\\\seq"'),
])
def test_dump_string_settings_preserve_literal_backslash_names(standard_strings, literal):
    dumped = (
        f"SET standard_conforming_strings = {standard_strings};\n"
        f"SELECT pg_catalog.setval('{literal}', 9, false);\n"
    )
    assert backup.parse_sequence_state(dumped) == [['public."student\\seq"', 9, False]]


class SequenceConnection:
    """DB boundary fake; both query types return explicit independent fixture rows."""

    def __init__(self, state):
        self.state = state

    def execute(self, query, params=None):
        if params:
            self.row = ("public", "orders") if params == ("public.orders",) else None
        else:
            assert query.as_string() == 'SELECT last_value,is_called FROM "public"."orders"'
            self.row = self.state
        return self

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("state", [(1, False), (41, True), (42, False)])
def test_restored_position_or_called_loss_rejected_even_with_identical_definition(state):
    with pytest.raises(helper.CheckFailure, match="^restore_sequence_state_mismatch$"):
        helper.verify_sequence_state(
            SequenceConnection(state), [["public.orders", 42, True]],
            [["orders", "bigint", 1, 1, 99999, 1, False]], [],
        )


def test_valid_dump_position_is_independent_of_pre_dump_live_sample():
    # A source nextval after the MVCC snapshot may advance the dumped state to 42.
    helper.verify_sequence_state(
        SequenceConnection((42, True)), [["public.orders", 42, True]],
        [["orders", "bigint", 1, 1, 99999, 1, False]], [],
    )


@pytest.mark.parametrize("evidence", [None, [], [["public.extra", 42, True]],
    [["public.orders", 42, True], ["public.orders", 42, True]]])
def test_missing_extra_or_duplicate_sequence_evidence_is_rejected(evidence):
    with pytest.raises(helper.CheckFailure, match="^restore_sequence_evidence_invalid$"):
        helper.verify_sequence_state(
            SequenceConnection((42, True)), evidence,
            [["orders", "bigint", 1, 1, 99999, 1, False]], [],
        )


def test_legacy_database_without_sequences_accepts_empty_dump_evidence():
    helper.verify_sequence_state(SequenceConnection(None), [], [], [])


def test_dump_reader_streams_archive_via_stdin(tmp_path):
    archive = tmp_path / "archive"
    archive.write_bytes(b"archive-fixture")
    with archive.open("rb") as stream:
        assert backup.run([sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
                          stdin=stream) == b"15\n"


def test_malformed_regclass_evidence_has_fixed_safe_error():
    class InvalidConnection:
        def execute(self, query, params=None):
            raise helper.psycopg.errors.InvalidName("secret-identifier")

    with pytest.raises(helper.CheckFailure, match="^restore_sequence_evidence_invalid$"):
        helper.verify_sequence_state(InvalidConnection(), [['public."broken', 42, True]], [], [])


@pytest.mark.parametrize("code,fragment", [
    ("restore_sequence_evidence_invalid", "序列位置证据"),
    ("restore_sequence_state_mismatch", "序列位置或调用状态"),
    ("restore_sequence_next_identifier_unsafe", "下一编号"),
])
def test_sequence_failures_are_allowlisted_with_chinese_messages(code, fragment):
    from app.operations.backup_messages import backup_message

    assert backup.sanitized_helper_error(("ERP_BACKUP_ERROR:" + code).encode()) == code
    assert fragment in backup_message("failed", code, "verify_restored_database")[1]
