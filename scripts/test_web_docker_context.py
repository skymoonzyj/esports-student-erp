"""Exercise public Docker COPY inputs with real Compose, TypeScript and Vite."""

import functools
import json
import os
import posixpath
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "shared/reactionx/v1"
PUBLIC_WEB_INPUTS = (
    "package.json",
    "package-lock.json",
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "tsconfig.app.json",
    "tsconfig.node.json",
    "src",
    "public",
    "tests",
)


@functools.lru_cache
def compose_builds(*overlays):
    command = [
        "docker-compose",
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(ROOT),
        "--profile",
        "*",
        "-p",
        "docker-context-regression",
        "-f",
        str(ROOT / "compose.yaml"),
    ]
    for overlay in overlays:
        command += ["-f", str(ROOT / overlay)]
    command += ["config", "--format", "json"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise AssertionError("Compose configuration normalization failed")
    cfg = json.loads(result.stdout)
    # Never expose normalized environments, secrets, or full Compose output.
    return {name: service.get("build") for name, service in cfg["services"].items()}


def named_contexts(build):
    value = build.get("additional_contexts", {})
    if isinstance(value, list):
        value = dict(entry.split("=", 1) for entry in value)
    return {name: Path(directory).resolve() for name, directory in value.items()}


def source_copies():
    """Read executable COPY operands from the actual web source stage."""
    in_source = False
    for line in (ROOT / "apps/web/Dockerfile").read_text().splitlines():
        parts = shlex.split(line, comments=True)
        if not parts:
            continue
        if parts[0] == "FROM":
            if in_source:
                break
            in_source = parts[-1] == "source"
        if in_source and parts[0] == "COPY":
            options = {}
            operands = parts[1:]
            while operands and operands[0].startswith("--"):
                key, value = operands.pop(0)[2:].split("=", 1)
                options[key] = value
            yield options, operands[:-1], operands[-1]


def materialize_image(image):
    """Replay actual COPY paths, limited to public build inputs, never private artifacts."""
    build = compose_builds()["web"]
    primary = Path(build["context"]).resolve()
    assert primary == ROOT / "apps/web", "Web context must stay narrow"
    contexts = named_contexts(build)
    for options, sources, destination in source_copies():
        origin = contexts[options["from"]] if "from" in options else primary
        if "from" in options:
            assert origin == CONTRACTS
            assert set(sources) == {"/schema.json", "/fixtures.json"}
        target = image / posixpath.normpath(posixpath.join("/app", destination)).lstrip(
            "/"
        )
        target.mkdir(parents=True, exist_ok=True)
        for source in sources:
            names = PUBLIC_WEB_INPUTS if source == "." else (source.lstrip("/"),)
            for name in names:
                src = origin / name
                if not src.exists():
                    if source == "." and name == "public":
                        continue
                    raise AssertionError("A required public build input is missing")
                dest = target / Path(name).name
                if src.is_dir():
                    shutil.copytree(src, dest, dirs_exist_ok=True)
                else:
                    shutil.copyfile(src, dest)
    return image / "app"


class WebDockerContextTests(unittest.TestCase):
    def test_dev_prod_nas_and_e2e_inherit_only_the_narrow_contract_context(self):
        for overlays in (
            (),
            ("compose.prod.yaml",),
            (
                "compose.prod.yaml",
                "compose.nas-staging.yaml",
                "compose.nas-lan-staging.yaml",
            ),
            ("compose.e2e.yaml",),
        ):
            with self.subTest(overlays=overlays):
                builds = compose_builds(*overlays)
                self.assertEqual(
                    Path(builds["web"]["context"]).resolve(), ROOT / "apps/web"
                )
                self.assertEqual(
                    named_contexts(builds["web"]), {"reactionx_contracts": CONTRACTS}
                )
                for service in ("api", "scheduler"):
                    self.assertEqual(
                        Path(builds[service]["context"]).resolve(), ROOT / "apps/api"
                    )
                    self.assertEqual(named_contexts(builds[service]), {})

    def test_materialized_container_inputs_resolve_and_build_real_production(self):
        with tempfile.TemporaryDirectory(prefix="web-context-regression-") as directory:
            base = Path(directory)
            image = base / "image"
            app = materialize_image(image)
            # Module paths normalize at the container root; inspect the actual
            # COPY destination before adapting /app into a host project layout.
            importer = "/app/src/reactionx/contracts.ts"
            target = posixpath.normpath(
                posixpath.join(
                    posixpath.dirname(importer),
                    "../../../../shared/reactionx/v1/schema.json",
                )
            )
            schema = image / target.lstrip("/")
            fixtures = schema.with_name("fixtures.json")
            # A host has more ancestors than container /app. Re-root only the
            # already-materialized inputs into the equivalent repository layout;
            # never copy contracts directly from the source repository here.
            host = base / "host"
            web = host / "apps/web"
            web.parent.mkdir(parents=True)
            shutil.copytree(app, web)
            if schema.is_file() and fixtures.is_file():
                (host / "shared/reactionx").mkdir(parents=True)
                shutil.copytree(schema.parent, host / "shared/reactionx/v1")
            (web / "node_modules").symlink_to(
                ROOT / "apps/web/node_modules", target_is_directory=True
            )
            env = {
                key: os.environ[key]
                for key in ("PATH", "HOME", "TMPDIR")
                if key in os.environ
            }
            result = subprocess.run(
                ["npm", "run", "build"],
                cwd=web,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                timeout=180,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(schema.is_file())
            self.assertTrue(fixtures.is_file())
            self.assertEqual(
                schema.read_bytes(), (CONTRACTS / "schema.json").read_bytes()
            )
            self.assertEqual(
                fixtures.read_bytes(), (CONTRACTS / "fixtures.json").read_bytes()
            )
            self.assertTrue((web / "dist/index.html").is_file())

    def test_api_runtime_contracts_import_without_any_shared_json_tree(self):
        with tempfile.TemporaryDirectory(prefix="api-contract-context-") as directory:
            image = Path(directory)
            production = False
            for line in (ROOT / "apps/api/Dockerfile").read_text().splitlines():
                if not line.startswith(("FROM ", "COPY ")):
                    continue
                parts = shlex.split(line, comments=True)
                if not parts:
                    continue
                if parts[0] == "FROM":
                    production = parts[-1] == "production"
                if production and parts[0] == "COPY" and parts[1].startswith("app/"):
                    for source in parts[1:-1]:
                        src = ROOT / "apps/api" / source
                        target = image / parts[-1]
                        dest = target if src.is_dir() else target / src.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        if src.is_dir():
                            shutil.copytree(src, dest)
                        else:
                            shutil.copyfile(src, dest)
            result = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-c",
                    """
import json, sys
sys.path.insert(0, sys.argv[1])
from typing import get_args
from app.reactionx.schemas import Mode
from app.reactionx.metrics import calculate_metrics
print(json.dumps({'mode_count': len(get_args(Mode)), 'metrics_callable': callable(calculate_metrics)}))
""",
                    str(image),
                ],
                cwd=image,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout), {"mode_count": 9, "metrics_callable": True}
            )
            self.assertFalse((image / "shared").exists())
            self.assertFalse((image / "app/cli/seed_reactionx_e2e.py").exists())


if __name__ == "__main__":
    unittest.main()
