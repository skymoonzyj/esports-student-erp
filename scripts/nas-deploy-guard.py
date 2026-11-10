"""Fail-closed NAS release gates; never prints rendered environment or secrets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path, PurePosixPath

TARGETS = ("/app/runtime/evaluation-media", "/app/runtime/evaluation-quarantine")
DATABASE_TARGET = "/var/lib/postgresql/data"
REACTIONX_TARGET = "/app/runtime/reactionx-release"
FILES = (
    "compose.yaml",
    "compose.prod.yaml",
    "compose.nas-staging.yaml",
    "compose.nas-lan-staging.yaml",
)


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True)


def config(root, project):
    args = ["compose", "-p", project, "--project-directory", str(root)]
    for name in FILES:
        args.extend(["-f", str(root / name)])
    return json.loads(docker(*args, "config", "--format", "json"))


def configured_mount(cfg, service, target):
    matches = [
        v for v in cfg["services"][service].get("volumes", []) if v["target"] == target
    ]
    require(
        len(matches) == 1, f"{service}: required mount missing or ambiguous: {target}"
    )
    return matches[0]


def actual_mount(container, target):
    matches = [v for v in container["Mounts"] if v["Destination"] == target]
    require(len(matches) == 1, f"Required actual mount missing or ambiguous: {target}")
    return matches[0]


def check_mount(cfg, service, container, target):
    expected = configured_mount(cfg, service, target)
    actual = actual_mount(container, target)
    require(
        actual["Type"] == expected["type"], f"{service}: wrong mount type: {target}"
    )
    if expected["type"] == "volume":
        name = cfg["volumes"][expected["source"]]["name"]
        require(actual.get("Name") == name, f"{service}: wrong named volume: {target}")
    else:
        require(
            Path(actual["Source"]).resolve() == Path(expected["source"]).resolve(),
            f"{service}: wrong bind source: {target}",
        )
    require(
        actual.get("RW") is not bool(expected.get("read_only", False)),
        f"{service}: wrong mount access: {target}",
    )
    return actual


def validate_database_defaults(cfg):
    database = configured_mount(cfg, "db", DATABASE_TARGET)
    require(
        (database.get("volume") or {}).get("subpath") in (None, ""),
        "database volume subpath is not supported",
    )
    service = cfg["services"]["db"]
    # This deployment supports the image's default startup only. Empty values
    # override (disable) image defaults in Compose; absent/null values inherit.
    # Runtime Config.Cmd/Entrypoint contain inherited defaults and are not
    # evidence of an explicit candidate Compose override.
    require(
        service.get("command") is None and service.get("entrypoint") is None,
        "database must use image default command and entrypoint",
    )


def validate_reactionx_mount(cfg, root=None):
    """Old release configs may omit this optional feature; new ones cannot drift."""
    api = cfg["services"]["api"]
    enabled = "REACTIONX_RELEASE_ROOT" in api.get("environment", {})
    for service, value in cfg["services"].items():
        mounts = [v for v in value.get("volumes", []) if v["target"] == REACTIONX_TARGET]
        require(not mounts or (enabled and service == "api"), "Release bundle must be API-only")
    if not enabled:
        return
    require(api["environment"]["REACTIONX_RELEASE_ROOT"] == REACTIONX_TARGET,
            "Release bundle root must be fixed")
    mount = configured_mount(cfg, "api", REACTIONX_TARGET)
    bind = mount.get("bind")
    # Compose's normalized JSON omits create_host_path when it is false.
    require(mount["type"] == "bind" and mount.get("read_only") is True
            and isinstance(bind, dict) and bind.get("create_host_path", False) is False,
            "Release bundle must be a read-only existing host bind")
    if root is not None:
        # Resolve the legitimate release root (current may be its stable link),
        # but NOT its runtime suffix: resolving both sides would approve a
        # runtime-directory symlink escaping the candidate.
        require(Path(mount["source"]).resolve() == root.resolve() / "runtime/reactionx-release",
                "Release bundle source must belong to the candidate")


def validate_config(cfg, root=None):
    validate_reactionx_mount(cfg, root)
    validate_database_defaults(cfg)
    database = configured_mount(cfg, "db", DATABASE_TARGET)
    require(
        database["type"] == "volume" and not database.get("read_only"),
        "Database must use writable persistent volume storage",
    )
    for target in TARGETS:
        api = configured_mount(cfg, "api", target)
        scheduler = configured_mount(cfg, "scheduler", target)
        require(
            api["type"] == scheduler["type"] == "volume"
            and api["source"] == scheduler["source"],
            "API/scheduler must use the same persistent storage",
        )
        require(
            not api.get("read_only") and not scheduler.get("read_only"),
            "Media volumes must be writable",
        )
    status = configured_mount(cfg, "api", "/app/var")
    require(
        status["type"] == "bind" and status.get("read_only"),
        "API operations status must be a read-only host bind",
    )
    for service in ("api", "scheduler"):
        env = cfg["services"][service].get("environment", {})
        for key, value in env.items():
            if key.endswith("_FILE") and value:
                require(
                    any(
                        str(
                            PurePosixPath("/run/secrets")
                            / secret.get("target", secret["source"])
                        )
                        == str(value)
                        for secret in cfg["services"][service].get("secrets", [])
                    ),
                    f"{service}: configured secret is not mounted: {key}",
                )
    for secret in cfg.get("secrets", {}).values():
        if "file" in secret:
            path = Path(secret["file"])
            require(
                path.is_file() and path.stat().st_size > 0 and os.access(path, os.R_OK),
                "Required secret file is absent, empty or unreadable",
            )


def check_database(cfg, container):
    validate_database_defaults(cfg)
    expected = configured_mount(cfg, "db", DATABASE_TARGET)
    actual = actual_mount(container, DATABASE_TARGET)
    require(
        expected["type"] == "volume"
        and actual["Type"] == "volume"
        and actual.get("Name") == cfg["volumes"][expected["source"]]["name"]
        and actual.get("RW") is True,
        "database persistent storage differs from candidate configuration",
    )
    expected_image = cfg["services"]["db"].get("image")
    actual_image = container["Config"].get("Image")
    require(
        isinstance(expected_image, str)
        and isinstance(actual_image, str)
        and actual_image == expected_image,
        "database image differs from candidate configuration",
    )
    expected_major = re.fullmatch(r"(?:[^/]+/)*postgres:(\d+)(?:[-.].*)?", expected_image)
    actual_major = re.fullmatch(r"(?:[^/]+/)*postgres:(\d+)(?:[-.].*)?", actual_image)
    require(
        expected_major is not None
        and actual_major is not None
        and expected_major.group(1) == actual_major.group(1),
        "database major version is not safely identifiable or compatible",
    )
    actual_env = dict(
        item.split("=", 1)
        for item in container["Config"].get("Env", [])
        if "=" in item
    )
    require(
        all(
            actual_env.get(key) == str(value)
            for key, value in cfg["services"]["db"].get("environment", {}).items()
            if value is not None
        ),
        "database runtime configuration differs from candidate configuration",
    )


def runtime(cfg, app, project):
    services = ("db", "api", "scheduler", "web", "proxy", "caddy")
    names = [f"{project}-{service}-1" for service in services]
    containers = json.loads(docker("inspect", *names))
    require(len(containers) == len(services), "Runtime container inventory is incomplete")
    by_service = dict(zip(services, containers))
    for service, container in by_service.items():
        state = container["State"]
        require(state["Status"] == "running", f"{service}: not running")
        if service in ("api", "web", "proxy") or "Health" in state:
            require(
                state.get("Health", {}).get("Status") == "healthy",
                f"{service}: not healthy",
            )
        expected = cfg["services"][service]
        require(
            container["Config"]["Image"] == expected["image"],
            f"{service}: image identity mismatch",
        )
        if service == "db":
            # The database is deliberately not recreated by this deployer, so its
            # old Compose working-directory labels are not candidate identity.
            # Its image and persistent storage are the compatibility boundary.
            check_database(cfg, container)
            continue
        labels = container["Config"].get("Labels", {})
        require(
            labels.get("com.docker.compose.project.working_dir")
            == str(app / "current"),
            f"{service}: stale Compose working directory",
        )
        require(
            labels.get("com.docker.compose.project.config_files", "").split(",")
            == [str(app / "current" / f) for f in FILES],
            f"{service}: stale Compose configuration files",
        )
        actual_env = dict(
            item.split("=", 1)
            for item in container["Config"].get("Env", [])
            if "=" in item
        )
        require(
            all(
                actual_env.get(key) == str(value)
                for key, value in expected.get("environment", {}).items()
                if value is not None
            ),
            f"{service}: runtime environment differs from candidate configuration",
        )
    for target in TARGETS:
        left = check_mount(cfg, "api", by_service["api"], target)
        right = check_mount(cfg, "scheduler", by_service["scheduler"], target)
        require(
            left["Source"] == right["Source"], "API/scheduler physical storage differs"
        )
    check_mount(cfg, "api", by_service["api"], "/app/var")
    if "REACTIONX_RELEASE_ROOT" in cfg["services"]["api"].get("environment", {}):
        check_mount(cfg, "api", by_service["api"], REACTIONX_TARGET)
        for service, container in by_service.items():
            if service != "api":
                require(not any(v["Destination"] == REACTIONX_TARGET
                                for v in container.get("Mounts", [])),
                        "Release bundle unexpectedly mounted outside API")


def atomic_text(path, value):
    temporary = path.with_name(path.name + ".deploy-tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def pointer(app, target):
    temporary = app / ".current.deploy-tmp"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(target)
    os.replace(temporary, app / "current")


def prepare_env(app, release, project):
    env_path = release / ".env"
    lines = env_path.read_text().splitlines()
    values = dict(
        line.split("=", 1) for line in lines if "=" in line and not line.startswith("#")
    )
    store_id = values.get("EVALUATION_MEDIA_STORE_ID", "").strip("\"'")
    if not store_id:
        # Read only from the API's authoritative existing media mount. No user
        # content is read, and failure other than a missing marker aborts.
        code = "from pathlib import Path; p=Path('/app/runtime/evaluation-media/.erp-store-identity'); print(p.read_text().splitlines()[0] if p.exists() else '')"
        store_id = docker("exec", f"{project}-api-1", "python", "-c", code).strip()
    store_id = str(uuid.UUID(store_id)) if store_id else str(uuid.uuid4())
    values_to_set = {
        "ERP_RELEASE_ID": release.name,
        "EVALUATION_MEDIA_STORE_ID": store_id,
    }
    lines = [
        line
        for line in lines
        if not any(line.startswith(key + "=") for key in values_to_set)
    ]
    lines.extend(f"{key}={value}" for key, value in values_to_set.items())
    atomic_text(env_path, "\n".join(lines) + "\n")
    # This is a non-secret storage identity, not any credential from .env.
    print(store_id)


def main():
    command = sys.argv[1]
    if command == "digest":
        root = Path(sys.argv[2])
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if path.is_dir() and not path.is_symlink():
                continue
            digest.update(os.fsencode(str(path.relative_to(root))) + b"\0")
            digest.update(str(path.lstat().st_mode).encode() + b"\0")
            digest.update(
                os.fsencode(os.readlink(path))
                if path.is_symlink()
                else path.read_bytes()
            )
            digest.update(b"\0")
        print(digest.hexdigest())
        return
    app, release, project = Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    if command == "prepare-env":
        prepare_env(app, release, project)
    elif command == "pointer":
        pointer(app, release)
    else:
        if command == "prior-backup":
            marker = app / ".active-release"
            state_file = app / ".deployment-state"
            require(
                marker.is_file() and state_file.is_file(),
                "Prior release has no verified deployment identity",
            )
            state = dict(
                line.split("=", 1)
                for line in state_file.read_text().splitlines()
                if "=" in line
            )
            allowed = state.get("status") in (
                "healthy",
                "complete",
                "backup_failed",
            ) or (
                state.get("status") == "attempt_failed"
                and state.get("active_unchanged") == "true"
            )
            identity = str((app / "current").resolve(strict=True))
            require(
                allowed
                and marker.read_text().strip() == identity
                and state.get("release") == identity,
                "Prior release identity is unverified or inconsistent",
            )
            metadata_file = Path(identity) / ".release-metadata"
            require(metadata_file.is_file(), "Prior release schema identity is missing")
            metadata = dict(
                line.split("=", 1)
                for line in metadata_file.read_text().splitlines()
                if "=" in line
            )
            require(
                len(sys.argv) == 6
                and metadata.get("source_alembic_head") == sys.argv[5],
                "Prior release schema identity differs from database baseline",
            )
        cfg = config(release, project)
        validate_config(cfg, release)
        if command == "authority":
            existing_api, existing_db = json.loads(
                docker("inspect", f"{project}-api-1", f"{project}-db-1")
            )
            for target in TARGETS:
                check_mount(cfg, "api", existing_api, target)
            check_database(cfg, existing_db)
        elif command in ("runtime", "prior-backup"):
            runtime(cfg, app, project)
        elif command != "config":
            raise SystemExit("Unknown deployment gate")


if __name__ == "__main__":
    main()
