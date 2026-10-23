"""Exercise the launcher's two packaging filters with real local rsync."""

import os
import re
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = Path(
    os.environ.get("NAS_DEPLOY_TEST_LAUNCHER", str(ROOT / "scripts/deploy-nas.sh"))
)


def packaging_filters():
    script = LAUNCHER.read_text()
    snapshot = script.split("snapshot_filters=(", 1)[1].split("\n)", 1)[0]
    outbound = script.split("rsync -a --delete --human-readable \\\n", 1)[1].split(
        '  -e "$rsync_ssh"', 1
    )[0]
    return {
        "snapshot": shlex.split(snapshot),
        "outbound": [
            shlex.split(option)[0]
            for option in re.findall(r"--(?:include|exclude)='[^']+'", outbound)
        ],
    }


class PackagingFilterTests(unittest.TestCase):
    def test_local_checkout_metadata_and_dependencies_never_enter_release(self):
        for filter_name, filters in packaging_filters().items():
            for git_kind in ("file", "directory"):
                for dependency_kind in ("symlink", "directory"):
                    with self.subTest(
                        filter=filter_name,
                        git=git_kind,
                        dependency=dependency_kind,
                    ), tempfile.TemporaryDirectory() as directory:
                        base = Path(directory)
                        source = base / "source"
                        target = base / "target"
                        source.mkdir()
                        target.mkdir()
                        (source / "apps/web").mkdir(parents=True)
                        (source / "apps/web/page.tsx").write_text("app source")
                        (source / "new-untracked.py").write_text("new source")
                        (source / ".env.example").write_text("example")
                        (source / ".env").write_text("private")
                        (source / ".env.production").write_text("private")
                        (source / "secrets").mkdir()
                        (source / "secrets/key").write_text("private")
                        if git_kind == "file":
                            (source / ".git").write_text("gitdir: /private/local/git")
                        else:
                            (source / ".git").mkdir()
                            (source / ".git/config").write_text("private")
                        for path in (
                            source / ".venv",
                            source / "apps/web/node_modules",
                            source / "apps/api/.venv312",
                        ):
                            path.parent.mkdir(parents=True, exist_ok=True)
                            if dependency_kind == "symlink":
                                path.symlink_to(base / "private-local-dependencies")
                            else:
                                path.mkdir(parents=True)
                                (path / "local-only").write_text("private")
                        result = subprocess.run(
                            ["rsync", "-a", *filters, str(source) + "/", str(target) + "/"],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        for included in (
                            "apps/web/page.tsx",
                            "new-untracked.py",
                            ".env.example",
                        ):
                            self.assertTrue((target / included).is_file(), included)
                        for excluded in (
                            ".git",
                            ".venv",
                            "apps/web/node_modules",
                            "apps/api/.venv312",
                            ".env",
                            ".env.production",
                            "secrets",
                        ):
                            self.assertFalse(
                                (target / excluded).exists() or (target / excluded).is_symlink(),
                                f"{filter_name} transferred {excluded}",
                            )


if __name__ == "__main__":
    unittest.main()
