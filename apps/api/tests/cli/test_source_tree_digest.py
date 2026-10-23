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


def test_source_tree_digest_excludes_untracked_web_build_inputs(tmp_path: Path) -> None:
    assert _run("git", "init", "--quiet", cwd=tmp_path).returncode == 0
    assert _run("git", "config", "user.name", "Test", cwd=tmp_path).returncode == 0
    assert _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path).returncode == 0
    (tmp_path / "tracked.txt").write_text("source")
    assert _run("git", "add", "tracked.txt", cwd=tmp_path).returncode == 0
    assert _run("git", "commit", "--quiet", "-m", "baseline", cwd=tmp_path).returncode == 0
    before = _digest(tmp_path)
    for directory in ["dist", ".build-metadata", ".asset-history"]:
        target = tmp_path / "apps/web" / directory / "generated.json"
        target.parent.mkdir(parents=True)
        target.write_text("build output")
    assert _digest(tmp_path) == before
    source = tmp_path / "apps/web/src/new.ts"
    source.parent.mkdir(parents=True)
    source.write_text("new source")
    assert _digest(tmp_path) != before


def test_source_tree_digest_excludes_tracked_build_output_changes(tmp_path: Path) -> None:
    assert _run("git", "init", "--quiet", cwd=tmp_path).returncode == 0
    assert _run("git", "config", "user.name", "Test", cwd=tmp_path).returncode == 0
    assert _run("git", "config", "user.email", "test@example.invalid", cwd=tmp_path).returncode == 0
    output = tmp_path / "apps/web/.build-metadata/assets-manifest.json"
    output.parent.mkdir(parents=True)
    output.write_text("old generated manifest")
    assert _run("git", "add", ".", cwd=tmp_path).returncode == 0
    assert _run("git", "commit", "--quiet", "-m", "baseline", cwd=tmp_path).returncode == 0
    before = _digest(tmp_path)
    output.write_text("new generated manifest")
    assert _digest(tmp_path) == before
