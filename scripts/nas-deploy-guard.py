"""Fail-closed NAS release gates; never prints rendered environment or secrets."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

TARGETS = ("/app/runtime/evaluation-media", "/app/runtime/evaluation-quarantine")
DATABASE_TARGET = "/var/lib/postgresql/data"
FILES = (
    "compose.yaml",
    "compose.prod.yaml",
    "compose.nas-staging.yaml",
    "compose.nas-lan-staging.yaml",
)


def web_assets_helper():
    spec = importlib.util.spec_from_file_location('nas_web_assets', Path(__file__).with_name('nas-deploy-web-assets.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def require(condition, message):
    if not condition:
        raise SystemExit(message)


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True)


def config(root, project, *, historical=False):
    args = ["compose", "-p", project, "--project-directory", str(root)]
    for name in FILES:
        args.extend(["-f", str(root / name)])
    if (root / '.web-assets-compose.json').is_file():
        args.extend(['-f', str(root / '.web-assets-compose.json')])
    child_env = dict(os.environ)
    # These exports belong to the in-progress candidate, not this source. Let
    # Compose read its own media identity; release identity is explicit metadata.
    for key in ('ERP_RELEASE_ID', 'EVALUATION_MEDIA_STORE_ID'):
        child_env.pop(key, None)
    if historical:
        for key in ('ERP_WEB_ASSETS_PROTOCOL', 'ERP_WEB_BUILD_ID', 'ERP_WEB_ASSET_INPUT_SHA256'):
            child_env.pop(key, None)
    if (root / '.release-metadata').is_file():
        helper = web_assets_helper()
        meta = helper.metadata(root / '.release-metadata')
        require(meta.get('source_release_id') == root.resolve(strict=True).name,
                'Source configuration release identity differs')
        child_env['ERP_RELEASE_ID'] = meta['source_release_id']
        if meta.get('web_assets_status') in ('verified', 'admitted', 'failed'):
            if meta['web_assets_status'] == 'verified':
                helper.receipt(meta, root.resolve().name)
            else:
                helper.admission(meta, root.resolve().name)
            child_env.update(ERP_WEB_ASSETS_PROTOCOL='1', ERP_WEB_BUILD_ID=meta['source_release_id'],
                ERP_WEB_ASSET_INPUT_SHA256=meta['web_assets_input_sha256'])
    result = json.loads(subprocess.check_output(['docker', *args, 'config', '--format', 'json'],
                                              text=True, env=child_env))
    result['_deployment_config_files'] = [str(root.parent.parent / 'current' / name) for name in FILES]
    if (root / '.web-assets-compose.json').is_file():
        result['_deployment_config_files'].append(str(root.parent.parent / 'current/.web-assets-compose.json'))
    # root can also be the stable current symlink rather than releases/<id>.
    if root.name == 'current':
        result['_deployment_config_files'] = [str(root / name) for name in FILES] + (
            [str(root / '.web-assets-compose.json')] if (root / '.web-assets-compose.json').is_file() else [])
    result['_deployment_release'] = str(root.resolve(strict=True))
    return result


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


def validate_config(cfg):
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


def runtime(cfg, app, project, *, healthy=True, bindings=None, observe=False):
    services = ("db", "api", "scheduler", "web", "proxy", "caddy")
    names = [f"{project}-{service}-1" for service in services]
    containers = json.loads(docker("inspect", *names))
    require(len(containers) == len(services), "Runtime container inventory is incomplete")
    by_service = dict(zip(services, containers))
    selected, images = {}, {}
    for service, container in by_service.items():
        state = container["State"]
        labels = container['Config'].get('Labels', {})
        require(labels.get('com.docker.compose.project') == project
                and labels.get('com.docker.compose.service') == service,
                f'{service}: Compose ownership mismatch')
        if healthy or service == 'db':
            require(state["Status"] == "running", f"{service}: not running")
        if healthy and (service in ("api", "web", "proxy") or "Health" in state):
            require(
                state.get("Health", {}).get("Status") == "healthy",
                f"{service}: not healthy",
            )
        chosen = cfg
        if bindings:
            actual_env = dict(item.split('=', 1) for item in container['Config'].get('Env', []) if '=' in item)
            choices = [variant for variant, ids in bindings
                if container.get('Image') == ids.get(service)
                and container['Config']['Image'] == variant['services'][service]['image']
                and (service == 'db' or labels.get('com.docker.compose.project.config_files', '').split(',')
                     == variant['_deployment_config_files'])
                and all(actual_env.get(key) == str(value) for key, value in
                    variant['services'][service].get('environment', {}).items() if value is not None)]
            require(choices, f'{service}: no authenticated repair runtime identity')
            chosen = choices[0]
        selected[service] = chosen
        images[service] = container.get('Image')
        expected = chosen["services"][service]
        require(
            container["Config"]["Image"] == expected["image"],
            f"{service}: image identity mismatch",
        )
        if service == "db":
            # The database is deliberately not recreated by this deployer, so its
            # old Compose working-directory labels are not candidate identity.
            # Its image and persistent storage are the compatibility boundary.
            check_database(chosen, container)
            continue
        labels = container["Config"].get("Labels", {})
        require(
            labels.get("com.docker.compose.project.working_dir")
            == str(app / "current"),
            f"{service}: stale Compose working directory",
        )
        config_paths = chosen['_deployment_config_files']
        require(
            labels.get("com.docker.compose.project.config_files", "").split(",") == config_paths,
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
        left = check_mount(selected['api'], "api", by_service["api"], target)
        right = check_mount(selected['scheduler'], "scheduler", by_service["scheduler"], target)
        require(
            left["Source"] == right["Source"], "API/scheduler physical storage differs"
        )
    check_mount(selected['api'], "api", by_service["api"], "/app/var")
    web_assets_helper().runtime_images(images)
    if observe:
        return images, {service: {'release': chosen['_deployment_release'],
            'config_sha256': config_digest(chosen), 'image_id': images[service]}
            for service, chosen in selected.items()}
    return images


def config_digest(cfg):
    stable = {key: value for key, value in cfg.items() if key != '_deployment_release'}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def carried_bindings(app, project, choices, checkpoint):
    """Reauthenticate the bounded observed sources, without recursively expanding them."""
    helper = web_assets_helper()
    groups = {}
    for service, choice in choices.items():
        source = Path(choice['release'])
        if choice['release'] == checkpoint['predecessor']['release']:
            require(choice['config_sha256'] == checkpoint['authority']['config_sha256']
                    and choice['image_id'] == checkpoint['authority']['images'][service],
                    'Carried successful runtime binding differs')
        else:
            meta = helper.metadata(source / '.release-metadata')
            ids = helper.admission(meta, source.name)
            require(choice['config_sha256'] == meta['web_admitted_config_sha256']
                    and choice['image_id'] == ids[service]
                    and meta['web_predecessor_image_id'] == checkpoint['predecessor']['image_id'],
                    'Carried admitted runtime binding differs')
        if choice['release'] not in groups:
            cfg = config(source, project, historical=True)
            require(config_digest(cfg) == choice['config_sha256'], 'Carried runtime configuration changed')
            validate_config(cfg)
            groups[choice['release']] = (cfg, {})
        else:
            require(config_digest(groups[choice['release']][0]) == choice['config_sha256'],
                    'Carried runtime configuration conflicts')
        groups[choice['release']][1][service] = choice['image_id']
    return list(groups.values())


def old_authority(app, project):
    helper = web_assets_helper()
    if (app / '.active-release').exists() or (app / '.active-release').is_symlink():
        active, _ = helper.active_release(app)
        cfg = config(active, project, historical=True)
        validate_config(cfg)
        images, observed = runtime(cfg, app, project, observe=True)
        helper.running(app, project)
        return {'mode': 'normal', 'configuration_release': str(active),
                'authority': {'config_sha256': config_digest(cfg), 'images': images}, 'observed_runtime': observed}
    checkpoint = helper.read_checkpoint(app)
    current, meta = helper.failed_current(app, checkpoint)
    candidate_ids = helper.admission(meta, current.name)
    require(meta['web_predecessor_image_id'] == checkpoint['predecessor']['image_id'], 'Repair predecessor differs')
    failed_cfg = config(current, project, historical=True)
    predecessor_cfg = config(Path(checkpoint['predecessor']['release']), project, historical=True)
    require(config_digest(failed_cfg) == meta['web_admitted_config_sha256']
            and config_digest(predecessor_cfg) == checkpoint['authority']['config_sha256'],
            'Repair configuration digest differs')
    validate_config(failed_cfg)
    validate_config(predecessor_cfg)
    choices = helper.carried_runtime(meta, app)
    _, observed = runtime(failed_cfg, app, project, healthy=False, observe=True,
            bindings=[(failed_cfg, candidate_ids), (predecessor_cfg, checkpoint['authority']['images']),
                      *carried_bindings(app, project, choices, checkpoint)])
    return {'mode': 'forward', 'configuration_release': str(current), 'observed_runtime': observed}


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
    if command == 'source-contract':
        web_assets_helper().validate_source_contract(Path(sys.argv[2]))
        return
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
    if command == 'old-authority':
        print(json.dumps(old_authority(app, project), separators=(',', ':')))
    elif command == 'record-predecessor':
        helper = web_assets_helper()
        pre = json.loads(helper.bounded_file(Path(sys.argv[5])))
        auth = json.loads(helper.bounded_file(Path(sys.argv[6])))
        require(auth == old_authority(app, project), 'Predecessor authority changed during extraction')
        if auth['mode'] == 'normal':
            require(auth['configuration_release'] == pre['checkpoint_predecessor']['release'], 'Predecessor source differs')
            checkpoint = {'schema_version': 1, 'predecessor': pre['checkpoint_predecessor'], 'authority': auth['authority']}
            helper.validate_checkpoint(app, checkpoint)
            atomic_text(app / '.web-last-successful.json', json.dumps(checkpoint, separators=(',', ':')) + '\n')
            helper.read_checkpoint(app)
        else:
            require(pre['checkpoint_predecessor'] == helper.read_checkpoint(app)['predecessor'], 'Repair checkpoint changed')
    elif command == 'admit-web':
        helper = web_assets_helper()
        pre = json.loads(helper.bounded_file(Path(sys.argv[5])))
        verified = json.loads(helper.bounded_file(Path(sys.argv[6])))
        auth = json.loads(helper.bounded_file(Path(sys.argv[8])))
        carried = helper.carried_runtime({'web_admitted_previous_runtime': json.dumps(auth['observed_runtime'])}, app)
        require(carried, 'Observed runtime continuity is missing')
        bindings = carried_bindings(app, project, carried, helper.read_checkpoint(app))
        # Builds may change tags, never the independently observed running images.
        runtime(bindings[0][0], app, project, healthy=False, bindings=bindings)
        require(hashlib.sha256(helper.bounded_file(Path(pre['input_manifest']))).hexdigest() == pre['input_sha256'],
                'Candidate retained input differs')
        cfg = config(release, project)
        images = {}
        for service in helper.SERVICES:
            if service == 'db':
                images[service] = json.loads(docker('inspect', f'{project}-db-1'))[0]['Image']
            elif service == 'web':
                images[service] = verified['image_id']
            else:
                images[service] = json.loads(docker('image', 'inspect', cfg['services'][service]['image']))[0]['Id']
        values = helper.metadata(release / '.release-metadata')
        values.update(web_assets_contract='1', web_assets_status='admitted', source_tree_sha256=sys.argv[7],
            web_image_id=verified['image_id'], web_assets_manifest_sha256=verified['manifest_sha256'],
            web_assets_input_sha256=pre['input_sha256'], web_predecessor_image_id=pre['predecessor_image_id'],
            web_admitted_config_sha256=config_digest(cfg), web_admitted_runtime_images=json.dumps(images, separators=(',', ':')),
            web_admitted_previous_runtime=json.dumps(carried, separators=(',', ':')))
        helper.admission(values, release.name)
        atomic_text(release / '.release-metadata', ''.join(f'{key}={value}\n' for key, value in values.items()))
    elif command == 'invalidate-web':
        helper = web_assets_helper()
        path = release / '.release-metadata'
        values = helper.metadata(path)
        if values.get('web_assets_status') == 'verified':
            values['web_assets_status'] = 'failed'
            values.pop('activated_verified_utc', None)
            try:
                atomic_text(path, ''.join(f'{key}={value}\n' for key, value in values.items()))
            except OSError:
                # Even an interrupted atomic writer must not leave a failed
                # candidate eligible as historical activation evidence.
                os.replace(path, path.with_name('.release-metadata.failed'))
                raise
    elif command == 'activate-web':
        helper = web_assets_helper()
        pre = json.loads(helper.bounded_file(Path(sys.argv[5])))
        verified = json.loads(helper.bounded_file(Path(sys.argv[6])))
        source_digest = sys.argv[7]
        require(re.fullmatch(helper.HEX, source_digest), 'Invalid source receipt binding')
        proof = helper.verify_running(app, verified['image_id'], verified['manifest_sha256'])
        require(proof['input_sha256'] == pre['input_sha256']
                and proof['predecessor_image_id'] == pre['predecessor_image_id']
                and proof['release_id'] == release.name and proof['build_id'] == release.name,
                'Activation receipt differs from verified image manifest')
        path = release / '.release-metadata'
        original = path.read_text()
        values = helper.metadata(path)
        require(values.get('source_release_id') == release.name, 'Invalid release receipt identity')
        values.update(web_assets_contract='1', web_assets_status='verified',
            web_image_id=verified['image_id'], web_assets_manifest_sha256=verified['manifest_sha256'],
            web_assets_input_sha256=pre['input_sha256'], web_predecessor_image_id=pre['predecessor_image_id'],
            source_tree_sha256=source_digest, activated_verified_utc=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
        helper.receipt(values, release.name)
        try:
            atomic_text(app / '.active-release', str(release) + '\n')
            atomic_text(path, ''.join(f'{key}={value}\n' for key, value in values.items()))
            atomic_text(app / '.deployment-state', f'status=healthy\nrelease={release}\n')
        except BaseException:
            (app / '.active-release').unlink(missing_ok=True)
            atomic_text(path, original)
            raise
    elif command == "prepare-env":
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
        validate_config(cfg)
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
