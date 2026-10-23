from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
from pathlib import Path


def _git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
    ).stdout


def source_tree_digest(repository: Path) -> str:
    root = repository.resolve(strict=True)
    digest = hashlib.sha256()
    digest.update(b"head\0")
    digest.update(_git(root, "rev-parse", "HEAD").strip())
    digest.update(b"\0diff\0")
    artifact_directories = {"dist", ".build-metadata", ".asset-history"}
    exclusions = [f":(exclude,glob)**/{name}/**" for name in sorted(artifact_directories)]
    digest.update(_git(root, "diff", "--binary", "--no-ext-diff", "HEAD", "--", ".", *exclusions))

    untracked = _git(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    ).split(b"\0")
    for encoded_path in sorted(path for path in untracked if path):
        relative_path = os.fsdecode(encoded_path)
        if any(part in artifact_directories for part in Path(relative_path).parts):
            continue
        path = root / relative_path
        metadata = path.lstat()
        digest.update(b"\0untracked\0")
        digest.update(encoded_path)
        digest.update(b"\0mode\0")
        digest.update(f"{stat.S_IMODE(metadata.st_mode):04o}".encode("ascii"))
        digest.update(b"\0content\0")
        if path.is_symlink():
            digest.update(os.fsencode(os.readlink(path)))
        elif path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"[NON_FILE]")
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        raise SystemExit("usage: source-tree-digest.py REPOSITORY")
    print(source_tree_digest(Path(arguments[0])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
