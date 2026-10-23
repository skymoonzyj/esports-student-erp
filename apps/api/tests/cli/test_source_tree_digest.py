from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SCRIPT = REPOSITORY_ROOT / "scripts/source-tree-digest.py"


def _run(*arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _digest(repository: Path) -> str:
    result = _run(sys.executable, str(SCRIPT), str(repository), cwd=repository)
    assert result.returncode == 0, result.stderr
    value = result.stdout.strip()
    assert re.fullmatch(r"[0-9a-f]{64}", value)
    return value


def test_source_tree_digest_tracks_dirty_and_untracked_content_but_not_ignored_data(
    tmp_path: Path,
) -> None:
    assert SCRIPT.is_file()
    assert _run("git", "init", "--quiet", cwd=tmp_path).returncode == 0
    assert _run("git", "config", "user.name", "Test", cwd=tmp_path).returncode == 0
    assert (
        _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path).returncode
        == 0
    )
    (tmp_path / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("tracked-v1\n", encoding="utf-8")
    assert _run("git", "add", ".gitignore", "tracked.txt", cwd=tmp_path).returncode == 0
    assert _run("git", "commit", "--quiet", "-m", "baseline", cwd=tmp_path).returncode == 0

    clean = _digest(tmp_path)
    tracked.write_text("tracked-v2\n", encoding="utf-8")
    dirty = _digest(tmp_path)
    assert dirty != clean

    untracked = tmp_path / "new.txt"
    untracked.write_text("new-v1\n", encoding="utf-8")
    with_untracked = _digest(tmp_path)
    assert with_untracked != dirty

    ignored = tmp_path / "ignored.txt"
    ignored.write_text("secret-v1\n", encoding="utf-8")
    ignored_once = _digest(tmp_path)
    ignored.write_text("secret-v2\n", encoding="utf-8")
    assert _digest(tmp_path) == ignored_once
