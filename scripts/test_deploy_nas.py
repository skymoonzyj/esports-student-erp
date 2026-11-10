"""Exercise the actual remote deployment body with only Docker/network mocked."""

import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parents[1]
LAUNCHER = Path(
    os.environ.get("NAS_DEPLOY_TEST_LAUNCHER", str(ROOT / "scripts/deploy-nas.sh"))
)


class DeploymentTests(unittest.TestCase):
    def run_deploy(self, fault="", with_backup=False, prior_state=None, reactionx=None):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        app = base / "app"
        previous = app / "releases/old"
        previous.mkdir(parents=True)
        (previous / ".env").write_text("PRESERVE_ME=yes\n")
        (previous / ".env").chmod(0o600)
        (previous / "secrets").mkdir()
        (previous / "secrets/database_url").write_text("not-a-real-secret")
        (app / "current").symlink_to(previous)
        (app / ".active-release").write_text(str(previous) + "\n")
        if prior_state:
            state = "failed" if prior_state == "unverified" else "complete"
            (app / ".deployment-state").write_text(
                f"status={state}\nrelease={previous}\n"
            )
            head = "unexpected" if prior_state == "schema_drift" else "head"
            (previous / ".release-metadata").write_text(f"source_alembic_head={head}\n")
            (previous / "scripts").mkdir()
            (previous / "scripts/erp-backup.py").write_text(
                "from pathlib import Path\nPath("
                + repr(str(app / "previous-backup-ran"))
                + ").touch()\n"
            )
            if prior_state == "drifted":
                other = app / "releases/drifted"
                other.mkdir()
                (app / ".active-release").write_text(str(other) + "\n")
        incoming = app / "incoming/new"
        (incoming / "scripts").mkdir(parents=True)
        (incoming / "infra/caddy").mkdir(parents=True)
        (incoming / "infra/caddy/Caddyfile").write_text(":443 {}\n")
        for name in (
            "compose.yaml",
            "compose.prod.yaml",
            "compose.nas-staging.yaml",
            "compose.nas-lan-staging.yaml",
        ):
            (incoming / name).write_text("services: {}\n")
        for helper in SOURCE.glob("scripts/nas-deploy-*"):
            shutil.copyfile(helper, incoming / "scripts" / helper.name)
        bundle_digest = "unavailable"
        if reactionx is not None:
            import hashlib
            import struct

            (incoming / "apps/api/app/reactionx").mkdir(parents=True)
            for name in ("__init__.py", "release_bundle.py"):
                shutil.copyfile(SOURCE / "apps/api/app/reactionx" / name,
                                incoming / "apps/api/app/reactionx" / name)
            shutil.copyfile(SOURCE / "scripts/reactionx-release-bundle.py",
                            incoming / "scripts/reactionx-release-bundle.py")
            bundle = app / "incoming/new-reactionx-bundle"
            bundle.mkdir()
            data = bytearray(512)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 60, 128)
            data[128:132] = b"PE\0\0"
            struct.pack_into("<H", data, 148, 240)
            struct.pack_into("<H", data, 152, 0x20B)
            struct.pack_into("<I", data, 260, 16)
            manifest = {"schema_version": 1, "protocol_version": 1, "version": "0.3.0",
                        "source_commit": "a" * 40, "filename": "ReactionXConnectorSetup.exe",
                        "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                        "signature_status": "unsigned"}
            raw = json.dumps(manifest).encode()
            (bundle / "manifest.json").write_bytes(raw)
            (bundle / "ReactionXConnectorSetup.exe").write_bytes(data)
            bundle_digest = hashlib.sha256(raw + hashlib.sha256(data).digest()).hexdigest()
            if reactionx == "corrupt":
                (bundle / "ReactionXConnectorSetup.exe").write_bytes(b"tampered")
            elif reactionx == "wrong_digest":
                bundle_digest = "0" * 64
            elif reactionx == "unexpected":
                bundle_digest = "unavailable"
        old_bundle = previous / "runtime/reactionx-release"
        old_bundle.mkdir(parents=True)
        (old_bundle / "old.exe").write_bytes(b"must never inherit")
        # Asset helper has its own real merge/archive/image-boundary tests. This
        # deployment boundary injects its failure to prove activation is gated.
        (incoming / 'scripts/nas-web-assets.py').write_text(
            "import os, sys\nfrom pathlib import Path\n"
            "app=Path(os.environ['MOCK_APP'])\n"
            "(app/'assets-called').write_text(str((app/'current').resolve()))\n"
            "raise SystemExit(29 if os.environ['MOCK_FAULT']=='assets_failed' else 0)\n"
        )
        if with_backup:
            shutil.copyfile(
                SOURCE / "scripts/erp-backup-install.py",
                incoming / "scripts/erp-backup-install.py",
            )
            shutil.copytree(
                SOURCE / "scripts/cloud_auth",
                incoming / "scripts/cloud_auth",
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
            (incoming / "scripts/erp-backup.py").write_text("raise SystemExit(0)\n")
            (app / "backup-tools").mkdir()
            (app / "backup-tools/run.py").write_text(
                "from pathlib import Path\nPath("
                + repr(str(app / "unsafe-backup"))
                + ").touch()\n"
            )
        (app / "backups").mkdir()
        operations = app / "operations"
        operations.mkdir()
        bindir = base / "bin"
        bindir.mkdir()
        docker = bindir / "docker"
        docker.write_text("""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a=sys.argv[1:]; app=Path(os.environ['MOCK_APP']); fault=os.environ['MOCK_FAULT']
if a[0]=='compose':
    if fault=='build_failed' and 'build' in a: sys.exit(24)
    if fault=='migration_failed' and 'alembic' in a: sys.exit(23)
    if 'config' in a and '--format' in a:
        services={}
        for s in ['db','api','scheduler','web','proxy','caddy']:
            v=[]
            if s=='db':
                volume='replacement_db' if fault=='wrong_db_volume' else 'postgres_data'
                v=[{'type':'volume','source':volume,'target':'/var/lib/postgresql/data'}]
                if fault=='wrong_db_subpath': v[0]['volume']={'subpath':'new_empty_cluster'}
            if s in ['api','scheduler']:
                v=[{'type':'volume','source':n,'target':'/app/runtime/'+t} for n,t in [('evaluation_media','evaluation-media'),('evaluation_quarantine','evaluation-quarantine')]]
                if s=='api': v.append({'type':'bind','source':str(app/'operations'),'target':'/app/var','read_only':True})
            image=('postgres:17-alpine' if fault=='wrong_db_major' else 'postgres:16-alpine') if s=='db' else 'erp-'+s+':new'
            database_name='replacement' if fault=='wrong_db_environment' else 'erp'
            environment={'POSTGRES_DB':database_name,'POSTGRES_USER':'erp'} if s=='db' else {'APP_ENV':'production'}
            services[s]={'image':image,'volumes':v,'environment':environment,'secrets':[]}
            if s=='db':
                if fault=='wrong_db_command': services[s]['command']=['postgres','-D','/var/lib/postgresql/data/new_empty_cluster']
                if fault=='wrong_db_entrypoint': services[s]['entrypoint']=['postgres','-D','/var/lib/postgresql/data/new_empty_cluster']
                if fault=='empty_db_command': services[s]['command']=[]
                if fault=='empty_db_entrypoint': services[s]['entrypoint']=''
                if fault=='null_db_defaults':
                    services[s].update({'command':None,'entrypoint':None})
                    v[0]['volume']={'subpath':''}
        if fault=='config_missing_media': services['scheduler']['volumes']=[]
        secrets={}
        if fault in ('absolute_secret','relative_secret','default_secret','wrong_secret_target'):
            secrets={'database_url':{'file':str(app/'current/secrets/database_url')}}
            for s in ('api','scheduler'):
                secret={'source':'database_url'}
                if fault=='absolute_secret': secret['target']='/run/secrets/database_url'
                elif fault=='relative_secret': secret['target']='database_url'
                elif fault=='wrong_secret_target': secret['target']='/different/database_url'
                services[s]['secrets']=[secret]
                services[s]['environment']['DATABASE_URL_FILE']='/run/secrets/database_url'
        volumes={n:{'name':'erp_'+n} for n in ['evaluation_media','evaluation_quarantine']}
        volumes['postgres_data']={'name':'erp_postgres_data'}
        volumes['replacement_db']={'name':'new_empty_database_volume'}
        print(json.dumps({'services':services,'volumes':volumes,'secrets':secrets}))
    sys.exit(0)
if a[0]=='inspect':
    if '-f' in a or '--format' in a:
        if any('StartedAt' in arg for arg in a): print('2026-09-14T00:00:00Z'); sys.exit(0)
        print('running' if a[-1].endswith('caddy-1') or a[-1].endswith('scheduler-1') else 'healthy'); sys.exit(0)
    result=[]
    for name in a[1:]:
        s=name.removeprefix('erp-').removesuffix('-1'); mounts=[]
        if s=='db': mounts=[{'Type':'volume','Name':'erp_postgres_data','Source':'/vol/postgres_data','Destination':'/var/lib/postgresql/data','RW':True}]
        if s in ['api','scheduler']:
            mounts=[{'Type':'volume','Name':'erp_'+n,'Source':'/vol/'+n,'Destination':'/app/runtime/'+t,'RW':True} for n,t in [('evaluation_media','evaluation-media'),('evaluation_quarantine','evaluation-quarantine')]]
            if s=='api': mounts.append({'Type':'bind','Source':str(app/'operations'),'Destination':'/app/var','RW':False})
        if fault=='missing_media' and s=='scheduler': mounts=[]
        if fault=='wrong_media' and s=='scheduler': mounts[0]['Name']='empty_volume'; mounts[0]['Source']='/vol/empty'
        if fault=='missing_status' and s=='api': mounts=mounts[:2]
        state={'Status':'running','Health':{'Status':'unhealthy' if fault=='unhealthy' and s=='scheduler' else 'healthy'},'StartedAt':'2026-09-14T00:00:00Z'}
        labels={'com.docker.compose.project.working_dir':str(app/'current'),'com.docker.compose.project.config_files':','.join(str(app/'current'/f) for f in ['compose.yaml','compose.prod.yaml','compose.nas-staging.yaml','compose.nas-lan-staging.yaml'])}
        if fault=='stale_labels': labels['com.docker.compose.project.working_dir']=str(app/'releases/old')
        env=['POSTGRES_DB=erp','POSTGRES_USER=erp'] if s=='db' else ['APP_ENV='+('development' if fault=='wrong_env' else 'production')]
        if fault in ('absolute_secret','relative_secret','default_secret','wrong_secret_target') and s in ('api','scheduler'): env.append('DATABASE_URL_FILE=/run/secrets/database_url')
        image='postgres:16-alpine' if s=='db' else 'erp-'+s+(':latest' if fault=='wrong_image' else ':new')
        runtime_config={'Image':image,'Labels':labels,'Env':env}
        if s=='db': runtime_config.update({'Cmd':['postgres'],'Entrypoint':['docker-entrypoint.sh']})
        result.append({'Name':'/'+name,'Mounts':mounts,'State':state,'Config':runtime_config})
    print(json.dumps(result)); sys.exit(0)
if a[0]=='exec':
    if fault=='db_not_ready' and 'SELECT 1' in a[-1]: sys.exit(1)
    if fault=='store_mismatch' and '--verify-store-identity' in a: sys.exit(1)
    if 'psql' in a: print('t' if 'operations_service_heartbeats' in a[-1] else 'head')
    sys.exit(0)
sys.exit(0)
""")
        docker.chmod(0o755)
        crontab = bindir / "crontab"
        crontab.write_text(
            '#!/usr/bin/env python3\nimport sys\nif "-l" not in sys.argv: sys.stdin.read()\n'
        )
        crontab.chmod(0o755)
        for name in ("flock", "curl"):
            script = bindir / name
            script.write_text(
                '#!/bin/sh\nprintf \'%s\\n\' "$*" >> "$MOCK_APP/'
                + name
                + '.log"\n'
                + ('[ "$MOCK_FAULT" != lock_timeout ] || exit 1\n' if name == "flock" else "")
                + 'exit 0\n'
            )
            script.chmod(0o755)
        body = (
            LAUNCHER.read_text()
            .split("<<'REMOTE_DEPLOY'\n", 1)[1]
            .split("\nREMOTE_DEPLOY", 1)[0]
        )
        digest = subprocess.check_output(
            [
                "python3",
                str(SOURCE / "scripts/nas-deploy-guard.py"),
                "digest",
                str(incoming),
            ],
            text=True,
        ).strip()
        if fault == "digest_mismatch":
            digest = "0" * 64
        result = subprocess.run(
            [
                "bash",
                "-s",
                "--",
                str(app),
                "new",
                "erp",
                "next" if fault == "migration_failed" else "head",
                "head",
                "https://skymoonzyj.l.cd:18080",
                "127.0.0.1",
                digest,
                "[]",
                bundle_digest,
            ],
            input=body,
            text=True,
            capture_output=True,
            check=False,
            env={
                **os.environ,
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "MOCK_APP": str(app),
                "MOCK_FAULT": fault,
                "TZ": "Asia/Shanghai",
            },
        )
        return result, app, previous

    def test_deployment_waits_for_exclusive_lock_with_bounded_timeout(self):
        result, app, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((app / "flock.log").read_text().splitlines()[0], "-x -w 50 9")

    def test_absent_release_bundle_never_inherits_previous_package(self):
        result, app, previous = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list((app / "releases/new/runtime/reactionx-release").iterdir()), [])
        self.assertTrue((previous / "runtime/reactionx-release/old.exe").is_file())

    def test_verified_bundle_uses_separate_transport_then_candidate_read_only_files(self):
        result, app, _ = self.run_deploy(reactionx="valid")
        self.assertEqual(result.returncode, 0, result.stderr)
        package = app / "releases/new/runtime/reactionx-release/ReactionXConnectorSetup.exe"
        self.assertEqual(package.stat().st_size, 512)
        self.assertEqual(package.stat().st_mode & 0o222, 0)

    def test_bad_optional_bundle_blocks_before_candidate_activation(self):
        for fault in ("corrupt", "wrong_digest", "unexpected"):
            with self.subTest(fault=fault):
                result, app, previous = self.run_deploy(reactionx=fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((app / "current").resolve(), previous)
                self.assertFalse((app / "releases/new").exists())

    def test_asset_merge_runs_before_active_pointer_or_migration_can_change(self):
        result, app, previous = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((app / 'assets-called').exists(), 'asset compatibility gate was skipped')
        self.assertEqual((app / 'assets-called').read_text(), str(previous))

    def test_asset_merge_failure_keeps_prior_service_pointer_and_backup_eligibility(self):
        result, app, previous = self.run_deploy('assets_failed', prior_state='verified')
        self.assertEqual(result.returncode, 29, result.stderr)
        self.assertEqual((app / 'current').resolve(), previous)
        self.assertEqual((app / '.active-release').read_text().strip(), str(previous))
        self.assertIn('active_unchanged=true', (app / '.deployment-state').read_text())

    def test_lock_timeout_preserves_previous_release_without_preparation(self):
        result, app, previous = self.run_deploy("lock_timeout", prior_state="verified")
        self.assertEqual(result.returncode, 14, result.stderr)
        self.assertIn("Timed out", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text(), str(previous) + "\n")
        self.assertEqual(
            (app / ".deployment-state").read_text(),
            f"status=complete\nrelease={previous}\n",
        )
        self.assertFalse((app / "releases/new").exists())

    def test_success_repairs_stale_current_and_keeps_active_consistent(self):
        result, app, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((app / "current").resolve(), app / "releases/new")
        self.assertEqual(
            (app / ".active-release").read_text().strip(), str(app / "releases/new")
        )

    def test_missing_scheduler_media_is_release_blocker(self):
        result, app, _ = self.run_deploy("missing_media")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app / "current").resolve(), app / "releases/new")

    def test_wrong_scheduler_volume_is_release_blocker(self):
        result, _, _ = self.run_deploy("wrong_media")
        self.assertNotEqual(result.returncode, 0)

    def test_missing_api_status_mount_is_release_blocker(self):
        result, _, _ = self.run_deploy("missing_status")
        self.assertNotEqual(result.returncode, 0)

    def test_unhealthy_scheduler_is_release_blocker(self):
        result, _, _ = self.run_deploy("unhealthy")
        self.assertNotEqual(result.returncode, 0)

    def test_tls_certificate_is_verified(self):
        result, app, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("--insecure", (app / "curl.log").read_text())

    def test_real_compose_absolute_secret_targets_are_accepted(self):
        result, app, _ = self.run_deploy("absolute_secret")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((app / "current").resolve(), app / "releases/new")

    def test_relative_and_default_secret_targets_remain_accepted(self):
        for target in ("relative_secret", "default_secret"):
            with self.subTest(target=target):
                result, _, _ = self.run_deploy(target)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_same_basename_wrong_absolute_secret_target_is_rejected(self):
        result, app, previous = self.run_deploy("wrong_secret_target")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app / "current").resolve(), previous)

    def test_failure_after_switch_removes_active_claim_and_preserves_diagnostics(self):
        result, app, previous = self.run_deploy("wrong_image")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((app / ".active-release").exists())
        self.assertEqual((app / "current").resolve(), app / "releases/new")
        state = (app / ".deployment-state").read_text()
        self.assertIn("status=failed", state)
        self.assertIn("previous_active=" + str(previous), state)

    def test_failed_first_switch_updates_and_blocks_existing_backup_consumer(self):
        result, app, _ = self.run_deploy("wrong_image", with_backup=True)
        self.assertNotEqual(result.returncode, 0)
        result = subprocess.run(
            ["python3", str(app / "backup-tools/run.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse((app / "unsafe-backup").exists())

    def test_backup_consumer_requires_verified_active_marker_but_allows_success(self):
        result, app, _ = self.run_deploy(with_backup=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        wrapper = app / "backup-tools/run.py"
        result = subprocess.run(
            ["python3", str(wrapper)], capture_output=True, text=True, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        (app / ".active-release").unlink()
        result = subprocess.run(
            ["python3", str(wrapper)], capture_output=True, text=True, check=False
        )
        self.assertNotEqual(result.returncode, 0)

    def test_failed_migration_does_not_leave_previous_executable_entrypoint(self):
        result, app, previous = self.run_deploy("migration_failed")
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual((app / "current").resolve(), app / "releases/new")
        self.assertFalse((app / ".active-release").exists())
        self.assertIn(
            "previous_current=" + str(previous), (app / ".deployment-state").read_text()
        )

    def test_candidate_build_failure_preserves_verified_previous_backup(self):
        result, app, previous = self.run_deploy(
            "build_failed", with_backup=True, prior_state="verified"
        )
        self.assertEqual(result.returncode, 24, result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))
        result = subprocess.run(
            ["python3", str(app / "backup-tools/run.py")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((app / "previous-backup-ran").exists())
        state = (app / ".deployment-state").read_text()
        self.assertIn("status=attempt_failed", state)
        self.assertIn("attempted_release=" + str(app / "releases/new"), state)

    def test_candidate_build_failure_does_not_approve_drifted_or_unverified_prior(self):
        for state in ("drifted", "unverified", "schema_drift"):
            with self.subTest(state=state):
                result, app, _ = self.run_deploy(
                    "build_failed", with_backup=True, prior_state=state
                )
                self.assertEqual(result.returncode, 24, result.stderr)
                result = subprocess.run(
                    ["python3", str(app / "backup-tools/run.py")],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((app / "previous-backup-ran").exists())

    def test_incomplete_candidate_config_fails_before_switch(self):
        result, app, previous = self.run_deploy("config_missing_media")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_candidate_database_volume_drift_fails_before_pointer_mutation(self):
        result, app, previous = self.run_deploy("wrong_db_volume")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database persistent storage differs", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_candidate_database_subpath_drift_fails_before_pointer_mutation(self):
        result, app, previous = self.run_deploy("wrong_db_subpath")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database volume subpath is not supported", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_candidate_database_command_drift_fails_before_pointer_mutation(self):
        result, app, previous = self.run_deploy("wrong_db_command")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database must use image default command and entrypoint", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_candidate_database_entrypoint_drift_fails_before_pointer_mutation(self):
        result, app, previous = self.run_deploy("wrong_db_entrypoint")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database must use image default command and entrypoint", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_empty_database_startup_overrides_fail_before_pointer_mutation(self):
        for fault in ("empty_db_command", "empty_db_entrypoint"):
            with self.subTest(fault=fault):
                result, app, previous = self.run_deploy(fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("database must use image default command and entrypoint", result.stderr)
                self.assertEqual((app / "current").resolve(), previous)
                self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_database_image_inherited_startup_defaults_remain_accepted(self):
        for fault in ("", "null_db_defaults"):
            with self.subTest(fault=fault):
                result, app, _ = self.run_deploy(fault)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual((app / "current").resolve(), app / "releases/new")
                self.assertEqual(
                    (app / ".active-release").read_text().strip(), str(app / "releases/new")
                )

    def test_candidate_database_major_drift_fails_before_pointer_mutation(self):
        result, app, previous = self.run_deploy("wrong_db_major")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database image differs", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_candidate_database_environment_drift_fails_before_pointer_mutation(self):
        result, app, previous = self.run_deploy("wrong_db_environment")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("database runtime configuration differs", result.stderr)
        self.assertEqual((app / "current").resolve(), previous)
        self.assertEqual((app / ".active-release").read_text().strip(), str(previous))

    def test_corrupted_transfer_is_rejected_before_release_creation(self):
        result, app, previous = self.run_deploy("digest_mismatch")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((app / "releases/new").exists())
        self.assertEqual((app / "current").resolve(), previous)

    def test_runtime_environment_mismatch_is_release_blocker(self):
        result, _, _ = self.run_deploy("wrong_env")
        self.assertNotEqual(result.returncode, 0)

    def test_stale_compose_working_directory_is_release_blocker(self):
        result, _, _ = self.run_deploy("stale_labels")
        self.assertNotEqual(result.returncode, 0)

    def test_database_readiness_failure_is_release_blocker(self):
        result, _, _ = self.run_deploy("db_not_ready")
        self.assertNotEqual(result.returncode, 0)

    def test_storage_marker_mismatch_is_release_blocker(self):
        result, _, _ = self.run_deploy("store_mismatch")
        self.assertNotEqual(result.returncode, 0)

    def test_copied_environment_remains_private_and_preserves_values(self):
        result, app, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        env = app / "releases/new/.env"
        self.assertEqual(env.stat().st_mode & 0o777, 0o600)
        self.assertIn("PRESERVE_ME=yes", env.read_text())
        self.assertIn("ERP_RELEASE_ID=new", env.read_text())


class SnapshotTests(unittest.TestCase):
    def test_alias_source_rejects_physical_in_tree_bundle_before_preflight(self):
        import hashlib
        import struct

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "physical-source"
            (source / "apps/api/app/reactionx").mkdir(parents=True)
            (source / "apps/api/alembic.ini").write_text("fixture")
            (source / "scripts").mkdir()
            for name in ("nas-deploy-guard.py", "nas-web-assets.py", "reactionx-release-bundle.py"):
                shutil.copyfile(SOURCE / "scripts" / name, source / "scripts" / name)
            for name in ("__init__.py", "release_bundle.py"):
                shutil.copyfile(SOURCE / "apps/api/app/reactionx" / name,
                                source / "apps/api/app/reactionx" / name)
            (source / ".venv/bin").mkdir(parents=True)
            alembic = source / ".venv/bin/alembic"
            alembic.write_text("#!/bin/sh\necho 'head (head)'\n")
            alembic.chmod(0o755)
            alias = root / "logical-source"
            alias.symlink_to(source, target_is_directory=True)
            self.assertEqual(alias.resolve(), source)
            bundle = source / "bundle"
            bundle.mkdir()
            data = bytearray(512)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 60, 128)
            data[128:132] = b"PE\0\0"
            struct.pack_into("<H", data, 148, 240)
            struct.pack_into("<H", data, 152, 0x20B)
            struct.pack_into("<I", data, 260, 16)
            (bundle / "ReactionXConnectorSetup.exe").write_bytes(data)
            (bundle / "manifest.json").write_text(json.dumps({
                "schema_version": 1, "protocol_version": 1, "version": "0.3.0",
                "source_commit": "a" * 40, "filename": "ReactionXConnectorSetup.exe",
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                "signature_status": "unsigned",
            }))
            key = root / "key"
            key.write_text("synthetic-key")
            bindir = root / "bin"
            bindir.mkdir()
            for name, code in {
                "git": '#!/bin/sh\ncase "$*" in *rev-parse*) echo abcdef012345;; esac\n',
                "ssh": '#!/bin/sh\ntouch "$MOCK_SSH_LOG"\nexit 9\n',
            }.items():
                executable = bindir / name
                executable.write_text(code)
                executable.chmod(0o755)
            marker = root / "preflight-reached"
            result = subprocess.run(
                ["bash", str(LAUNCHER), "--source", str(alias), "--dry-run",
                 "--reactionx-release-bundle", str(bundle)],
                capture_output=True, text=True, check=False,
                env={**os.environ, "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                     "NAS_SSH_IDENTITY_FILE": str(key), "MOCK_SSH_LOG": str(marker)},
            )
            self.assertFalse(marker.exists(), result.stderr)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outside source repository/image context", result.stderr)

    def test_remote_bash_preserves_json_and_literal_shell_characters(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ssh = root / 'ssh'
            # OpenSSH joins command arguments and the remote login shell parses
            # them once more. Reproduce that boundary without any network.
            ssh.write_text('#!/usr/bin/env python3\nimport subprocess, sys\nraise SystemExit(subprocess.run(" ".join(sys.argv[2:]), shell=True, input=sys.stdin.read(), text=True).returncode)\n')
            ssh.chmod(0o755)
            marker = root / 'injected'
            values = ['[{"release_id":"old","image_id":"sha256:abc"}]', f'literal $(touch {marker}) with spaces']
            function = LAUNCHER.read_text().split('remote_bash() {\n', 1)[1].split('\n}', 1)[0]
            body = ('ssh_options=()\nremote_target=fixture\nremote_bash() {\n' + function + '\n}\n' +
                    'remote_bash ' + ' '.join(shlex.quote(v) for v in values) + " <<'REMOTE'\n" +
                    'python3 - "$@" <<\'PY\'\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\nPY\nREMOTE\n')
            result = subprocess.run(['bash', '-s'], input=body, capture_output=True, text=True, check=False,
                                    env={**os.environ, 'PATH': str(root) + os.pathsep + os.environ['PATH']})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), 'remote argument was interpreted as shell code')
            self.assertEqual(json.loads(result.stdout), values)

    def test_default_and_hook_select_established_release_tree(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "scripts").mkdir()
            launcher = root / "scripts/deploy-nas.sh"
            shutil.copyfile(LAUNCHER, launcher)
            source = root / ".nas-release-sync"
            (source / "apps/api").mkdir(parents=True)
            (source / "apps/api/alembic.ini").write_text("fixture")
            (source / "scripts").mkdir()
            shutil.copyfile(
                SOURCE / "scripts/nas-deploy-guard.py",
                source / "scripts/nas-deploy-guard.py",
            )
            shutil.copyfile(SOURCE / 'scripts/nas-web-assets.py', source / 'scripts/nas-web-assets.py')
            (source / ".venv/bin").mkdir(parents=True)
            alembic = source / ".venv/bin/alembic"
            alembic.write_text(
                '#!/bin/sh\ncase "$*" in *heads*) echo "head (head)";; *history*) echo "base -> head";; esac\n'
            )
            alembic.chmod(0o755)
            key = root / "key"
            key.write_text("fixture")
            bindir = root / "bin"
            bindir.mkdir()
            for name, code in {
                "git": '#!/bin/sh\ncase "$*" in *rev-parse*) echo abcdef012345;; esac\n',
                "ssh": '#!/usr/bin/env python3\nimport sys\nb=sys.stdin.read()\nif "select version_num" in b: print("head")\n',
            }.items():
                command = bindir / name
                command.write_text(code)
                command.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "NAS_SSH_IDENTITY_FILE": str(key),
            }
            for flags in (["--dry-run"], ["--from-hook", "--dry-run"]):
                result = subprocess.run(
                    ["bash", str(launcher), *flags],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("Dry run only", result.stdout)

    def test_post_commit_uses_same_source_default_as_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / ".githooks").mkdir()
            hook = root / ".githooks/post-commit"
            shutil.copyfile(LAUNCHER.parent.parent / ".githooks/post-commit", hook)
            (root / "scripts").mkdir()
            launcher = root / "scripts/deploy-nas.sh"
            launcher.write_text(
                '#!/bin/sh\n[ "$*" = "--from-hook" ] || exit 1\ntouch "'
                + str(root / "default-selected")
                + '"\n'
            )
            launcher.chmod(0o755)
            # The real hook delegates source choice; only its external deploy call is mocked.
            result = subprocess.run(
                ["bash", str(hook)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((root / "default-selected").exists(), result.stderr)

    def test_dry_run_hashes_untracked_content_and_performs_no_remote_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            source = root / "source"
            (source / "apps/api").mkdir(parents=True)
            (source / "apps/api/alembic.ini").write_text("fixture")
            (source / "scripts").mkdir()
            shutil.copyfile(
                SOURCE / "scripts/nas-deploy-guard.py",
                source / "scripts/nas-deploy-guard.py",
            )
            shutil.copyfile(SOURCE / 'scripts/nas-web-assets.py', source / 'scripts/nas-web-assets.py')
            (source / ".venv/bin").mkdir(parents=True)
            alembic = source / ".venv/bin/alembic"
            alembic.write_text(
                "#!/bin/sh\ncase \"$*\" in *heads*) echo 'head (head)';; *history*) echo 'base -> head';; esac\n"
            )
            alembic.chmod(0o755)
            (source / "untracked.txt").write_text("first")
            key = root / "key"
            key.write_text("not-a-real-key")
            bindir = root / "bin"
            bindir.mkdir()
            for name, code in {
                "git": '#!/bin/sh\ncase "$*" in *rev-parse*) echo abcdef012345;; esac\n',
                "ssh": "#!/usr/bin/env python3\nimport os,sys\nfrom pathlib import Path\np=Path(os.environ['MOCK_SSH_LOG']); p.write_text(p.read_text()+'call\\n' if p.exists() else 'call\\n')\nb=sys.stdin.read()\nif 'REMOTE' in b or 'mkdir' in b or 'compose' in b and 'test -f' not in b: raise SystemExit('unexpected remote write')\nif 'select version_num' in b: print('head')\n",
            }.items():
                executable = bindir / name
                executable.write_text(code)
                executable.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "NAS_SSH_IDENTITY_FILE": str(key),
                "MOCK_SSH_LOG": str(root / "ssh.log"),
            }

            def dry_run(*extra):
                result = subprocess.run(
                    ["bash", str(LAUNCHER), "--source", str(source), "--dry-run", *extra],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=env,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return (
                    result.stdout.split("Planned release: ")[1]
                    .strip()
                    .rsplit("-", 1)[1]
                )

            first = dry_run()
            (source / "untracked.txt").write_text("second")
            self.assertNotEqual(first, dry_run())
            # Exercise local freezing and dry-run transport without a network
            # connection; supplied missing input must fail before even preflight.
            (source / "apps/api/app/reactionx").mkdir(parents=True)
            for name in ("__init__.py", "release_bundle.py"):
                shutil.copyfile(SOURCE / "apps/api/app/reactionx" / name,
                                source / "apps/api/app/reactionx" / name)
            shutil.copyfile(SOURCE / "scripts/reactionx-release-bundle.py",
                            source / "scripts/reactionx-release-bundle.py")
            calls = (root / "ssh.log").read_text()
            invalid = subprocess.run(
                ["bash", str(LAUNCHER), "--source", str(source), "--dry-run",
                 "--reactionx-release-bundle", str(root / "missing")],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual((root / "ssh.log").read_text(), calls)
            import hashlib
            import struct

            bundle = root / "bundle"
            bundle.mkdir()
            data = bytearray(512)
            data[:2] = b"MZ"
            struct.pack_into("<I", data, 60, 128)
            data[128:132] = b"PE\0\0"
            struct.pack_into("<H", data, 148, 240)
            struct.pack_into("<H", data, 152, 0x20B)
            struct.pack_into("<I", data, 260, 16)
            (bundle / "ReactionXConnectorSetup.exe").write_bytes(data)
            (bundle / "manifest.json").write_text(json.dumps({
                "schema_version": 1, "protocol_version": 1, "version": "0.3.0",
                "source_commit": "a" * 40, "filename": "ReactionXConnectorSetup.exe",
                "sha256": hashlib.sha256(data).hexdigest(), "size": len(data),
                "signature_status": "unsigned",
            }))
            self.assertTrue(dry_run("--reactionx-release-bundle", str(bundle)).startswith("rx"))
            inside = source / "bundle-must-not-enter-source-image"
            shutil.copytree(bundle, inside)
            calls = (root / "ssh.log").read_text()
            invalid = subprocess.run(
                ["bash", str(LAUNCHER), "--source", str(source), "--dry-run",
                 "--reactionx-release-bundle", str(inside)],
                capture_output=True, text=True, check=False, env=env,
            )
            self.assertNotEqual(invalid.returncode, 0)
            self.assertEqual((root / "ssh.log").read_text(), calls)

    def test_actual_untracked_bytes_change_digest_but_location_does_not(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            copy = root / "copy"
            source.mkdir()
            (source / "new-untracked.py").write_text("first")

            def digest(path):
                return subprocess.check_output(
                    [
                        "python3",
                        str(SOURCE / "scripts/nas-deploy-guard.py"),
                        "digest",
                        str(path),
                    ],
                    text=True,
                )

            first = digest(source)
            shutil.copytree(source, copy)
            self.assertEqual(first, digest(copy))
            (source / "new-untracked.py").write_text("second")
            self.assertNotEqual(first, digest(source))


if __name__ == "__main__":
    unittest.main()
