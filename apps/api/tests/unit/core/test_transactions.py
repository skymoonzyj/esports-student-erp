from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session, SessionTransactionOrigin

from app.core.transactions import write_transaction


class _CommitFailureSession:
    def __init__(self) -> None:
        self.rollback_calls = 0
        self.info: dict[str, object] = {}

    def get_transaction(self) -> object:
        return SimpleNamespace(origin=SessionTransactionOrigin.AUTOBEGIN)

    def commit(self) -> None:
        raise RuntimeError("commit failed")

    def rollback(self) -> None:
        self.rollback_calls += 1


class _AfterCommitListenerFailureSession(_CommitFailureSession):
    def commit(self) -> None:
        raise OSError("after commit listener failed")

    def rollback(self) -> None:
        self.rollback_calls += 1
        raise InvalidRequestError("transaction is already committed")


def test_write_transaction_rolls_back_when_autobegin_commit_fails() -> None:
    fake_db = _CommitFailureSession()

    with (
        pytest.raises(RuntimeError, match="commit failed"),
        write_transaction(cast(Session, fake_db)),
    ):
        pass

    assert fake_db.rollback_calls == 1


def test_write_transaction_does_not_rollback_after_commit_completed() -> None:
    fake_db = _AfterCommitListenerFailureSession()

    with (
        pytest.raises(OSError, match="after commit listener failed"),
        write_transaction(cast(Session, fake_db)),
    ):
        pass

    assert fake_db.rollback_calls == 1
