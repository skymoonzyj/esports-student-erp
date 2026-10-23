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


def run_snapshot_copy(source, snapshot):
    script = LAUNCHER.read_text()
    snapshot_body = "snapshot_filters=(" + script.split("snapshot_filters=(", 1)[1].split(
        "\nsource_state=", 1
    )[0]
    return subprocess.run(
        ["bash", "-euc", snapshot_body],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "repo_root": str(source), "snapshot": str(snapshot)},
    )


class PackagingFilterTests(unittest.TestCase):
    def test_both_release_filters_exclude_acceptance_artifacts(self):
        # Removing any acceptance exclusion must expose its private fixture,
        # while overbroad mail/migration exclusions must lose normal source.
        artifacts = (
            "outputs/acceptance/tokens.json",
            "apps/web/outputs/acceptance/credentials.json",
            "server.log",
            "apps/api/worker.log",
            "acceptance.xcresult/Info.plist",
            "apps/web/native.xcresult/Diagnostics/report.txt",
            "runtime/assisted-personnel/credentials/accounts.json",
            "runtime/assisted-personnel/mailbox/message.eml",
            "secrets/database_url",
            "apps/api/runtime/mailbox/message.eml",
            "apps/web/.build-metadata/assets-manifest.json",
            ".asset-history/assets/old.js",
            ".web-assets-scratch/manifest.json",
            "apps/web/dist-ios/index.html",
            "apps/web/ios/App/App/public/index.html",
            "apps/web/ios/App/App/ERPBuildConfig.json",
            "apps/web/ios/App/App/capacitor.config.json",
            "apps/web/ios/App/App/config.xml",
            "apps/web/ios/App/App/SoundPreview/preview.m4a",
            "apps/web/ios/DerivedData/private-build/data",
            "private.ipa",
            "private.mobileprovision",
            "private.p12",
        )
        sources = (
            "apps/web/src/page.tsx",
            "apps/api/app/mail/template_service.py",
            "apps/api/migrations/versions/0066_assisted_personnel.py",
            "new-untracked.py",
            ".env.example",
            "apps/web/retained-empty/manifest.json",
        )
        for filter_name, filters in packaging_filters().items():
            with self.subTest(filter=filter_name), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "source"
                target = Path(directory) / "target"
                source.mkdir()
                target.mkdir()
                for name in (*artifacts, *sources):
                    path = source / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("fixture, not real private data\n")
                result = subprocess.run(
                    ["rsync", "-a", *filters, str(source) + "/", str(target) + "/"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    [name for name in artifacts if (target / name).exists()],
                    [],
                    f"{filter_name} leaked acceptance artifacts",
                )
                for name in sources:
                    self.assertEqual(
                        (target / name).read_text(),
                        "fixture, not real private data\n",
                        f"{filter_name} lost {name}",
                    )

    def test_packaging_never_follows_directory_symlinks_to_private_outputs(self):
        for filter_name, filters in packaging_filters().items():
            with self.subTest(filter=filter_name), tempfile.TemporaryDirectory() as directory:
                base = Path(directory)
                source = base / "source"
                target = base / "target"
                private = base / "private-outputs"
                source.mkdir()
                target.mkdir()
                private.mkdir()
                (private / "tokens.json").write_text("not-a-real-token")
                (source / "linked-artifacts").symlink_to(private, target_is_directory=True)
                result = subprocess.run(
                    ["rsync", "-a", *filters, str(source) + "/", str(target) + "/"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue((target / "linked-artifacts").is_symlink())
                self.assertEqual(
                    [str(path.relative_to(target)) for path in target.rglob("*")],
                    ["linked-artifacts"],
                )

    def test_snapshot_verification_fails_closed_for_unsupported_source_links(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            snapshot = base / "snapshot"
            private = base / "private-outputs"
            source.mkdir()
            snapshot.mkdir()
            private.mkdir()
            (private / "tokens.json").write_text("not-a-real-token")
            (source / "linked-artifacts").symlink_to(private, target_is_directory=True)
            result = run_snapshot_copy(source, snapshot)
            self.assertEqual(result.returncode, 4, result.stderr)
            self.assertIn("Unsupported source symlink", result.stderr)
            self.assertEqual(
                [str(path.relative_to(snapshot)) for path in snapshot.rglob("*")],
                ["linked-artifacts"],
            )

    def test_snapshot_verification_accepts_source_with_excluded_dependency_links(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = base / "source"
            snapshot = base / "snapshot"
            source.mkdir()
            snapshot.mkdir()
            (source / "source.py").write_text("source bytes\n")
            (source / ".venv").symlink_to(base / "outside-dependencies")
            (source / "node_modules").symlink_to(base / "outside-dependencies")
            result = run_snapshot_copy(source, snapshot)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((snapshot / "source.py").read_text(), "source bytes\n")
            self.assertEqual([path.name for path in snapshot.iterdir()], ["source.py"])

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
