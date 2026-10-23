"""Exercise the actual remote deployment body with only Docker/network mocked."""

import hashlib
import json
import os
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


def copy_asset_helpers(destination):
    for name in ('nas-deploy-web-assets.py', 'web_assets.py', 'web-assets-contract.json'):
        shutil.copyfile(SOURCE / 'scripts' / name, destination / 'scripts' / name)
    schema = destination / 'apps/web/scripts/web-assets.schema.json'
    schema.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SOURCE / 'apps/web/scripts/web-assets.schema.json', schema)


class DeploymentTests(unittest.TestCase):
    def test_surviving_runtime_is_carried_across_two_failed_switches(self):
        first, app, _ = self.run_deploy('unhealthy')
        self.assertNotEqual(first.returncode, 0)
        checkpoint = (app / '.web-last-successful.json').read_bytes()
        self.deploy_arguments[6] = 'next'
        second = self.repair_attempt(app, 'failedrepair', 'migration_failed')
        self.assertEqual(second.returncode, 23, second.stderr)
        self.assertEqual((app / 'current').resolve(), app / 'releases/failedrepair')
        self.assertEqual((app / 'recreated').read_text(), 'new')
        record = dict(line.split('=', 1) for line in (app / 'releases/failedrepair/.release-metadata').read_text().splitlines())
        observed = json.loads(record['web_admitted_previous_runtime'])
        self.assertEqual(set(observed), {'db', 'api', 'scheduler', 'web', 'proxy', 'caddy'})
        self.assertEqual(observed['web']['release'], str(app / 'releases/new'))
        self.assertEqual(observed['web']['image_id'], 'sha256:' + '2' * 64)
        third = self.repair_attempt(app)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual((app / 'current').resolve(), app / 'releases/repair')
        self.assertEqual((app / '.web-last-successful.json').read_bytes(), checkpoint)
        self.assertEqual([group['release_id'] for group in json.loads((app / 'candidate-manifest.json').read_text())['sets']], ['repair', 'old'])
        for name in ('new', 'failedrepair'):
            self.assertNotIn('web_assets_status=verified', (app / 'releases' / name / '.release-metadata').read_text())

    def test_carried_runtime_tampering_is_rejected_after_two_failed_switches(self):
        for damage in ('image', 'path', 'config', 'source_config', 'service_count'):
            with self.subTest(damage=damage):
                first, app, _ = self.run_deploy('unhealthy')
                self.assertNotEqual(first.returncode, 0)
                self.deploy_arguments[6] = 'next'
                second = self.repair_attempt(app, 'failedrepair', 'migration_failed')
                self.assertEqual(second.returncode, 23, second.stderr)
                path = app / 'releases/failedrepair/.release-metadata'
                values = dict(line.split('=', 1) for line in path.read_text().splitlines())
                choices = json.loads(values['web_admitted_previous_runtime'])
                if damage == 'image':
                    choices['web']['image_id'] = 'sha256:' + '3' * 64
                elif damage == 'path':
                    choices['web']['release'] = str(app / 'releases/old')
                elif damage == 'config':
                    choices['web']['config_sha256'] = '0' * 64
                elif damage == 'service_count':
                    choices.pop('api')
                else:
                    source = app / 'releases/new/.web-assets-compose.json'
                    cfg = json.loads(source.read_text())
                    cfg['services']['web']['image'] = 'sha256:' + 'a' * 64
                    source.write_text(json.dumps(cfg))
                values['web_admitted_previous_runtime'] = json.dumps(choices)
                path.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
                before = (app / 'destructive.log').read_bytes()
                rejected = self.repair_attempt(app)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual((app / 'current').resolve(), app / 'releases/failedrepair')
                self.assertFalse((app / '.active-release').exists())
                self.assertFalse((app / 'releases/repair').exists())
                self.assertEqual((app / 'destructive.log').read_bytes(), before)

    def test_mixed_surviving_runtime_is_carried_across_two_failed_switches(self):
        first, app, _ = self.run_deploy('unhealthy', prior_protocol=True)
        self.assertNotEqual(first.returncode, 0)
        self.deploy_environment['MOCK_RUNTIME_MIXED'] = '1'
        self.deploy_arguments[6] = 'next'
        second = self.repair_attempt(app, 'failedrepair', 'migration_failed')
        self.assertEqual(second.returncode, 23, second.stderr)
        record = dict(line.split('=', 1) for line in (app / 'releases/failedrepair/.release-metadata').read_text().splitlines())
        observed = json.loads(record['web_admitted_previous_runtime'])
        self.assertEqual(observed['api']['release'], str(app / 'releases/new'))
        self.assertEqual(observed['proxy']['release'], str(app / 'releases/old'))
        self.assertEqual(len(observed), 6)
        repaired = self.repair_attempt(app)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)

    def repair_attempt(self, app, name='repair', fault=''):
        incoming = app / 'incoming' / name
        shutil.copytree(app / 'incoming/new', incoming)
        digest = subprocess.check_output(['python3', str(SOURCE / 'scripts/nas-deploy-guard.py'),
            'digest', str(incoming)], text=True).strip()
        arguments = [*self.deploy_arguments]
        arguments[4], arguments[-1] = name, digest
        return subprocess.run(arguments, input=self.deploy_body, text=True, capture_output=True,
            check=False, env={**self.deploy_environment, 'MOCK_FAULT': fault, 'MOCK_CANDIDATE': name})

    def test_failed_switch_can_be_repaired_without_old_pointer_or_failed_assets(self):
        for protocol in (False, True):
            with self.subTest(protocol=protocol):
                first, app, previous = self.run_deploy('runtime_image_swap', prior_protocol=protocol)
                self.assertNotEqual(first.returncode, 0)
                failed = app / 'releases/new'
                self.assertEqual((app / 'current').resolve(), failed)
                self.assertFalse((app / '.active-release').exists())
                checkpoint = (app / '.web-last-successful.json').read_bytes()
                second = self.repair_attempt(app)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual((app / 'current').resolve(), app / 'releases/repair')
                self.assertEqual((app / '.active-release').read_text().strip(), str(app / 'releases/repair'))
                self.assertNotEqual((app / 'current').resolve(), previous)
                groups = json.loads((app / 'candidate-manifest.json').read_text())['sets']
                self.assertEqual([group['release_id'] for group in groups], ['repair', 'old'])
                self.assertNotIn('web_assets_status=verified', (failed / '.release-metadata').read_text())
                self.assertEqual((app / '.web-last-successful.json').read_bytes(), checkpoint)
                history = (app / 'pointer-history.log').read_text().splitlines()
                self.assertNotIn('old', history[history.index('new'):])

    def test_repeated_failed_repair_never_overwrites_successful_predecessor(self):
        first, app, _ = self.run_deploy('runtime_image_swap')
        self.assertNotEqual(first.returncode, 0)
        checkpoint = (app / '.web-last-successful.json').read_bytes()
        second = self.repair_attempt(app, 'failedrepair', 'build_failed')
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual((app / 'current').resolve(), app / 'releases/new')
        self.assertEqual((app / '.web-last-successful.json').read_bytes(), checkpoint)
        third = self.repair_attempt(app)
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual((app / '.web-last-successful.json').read_bytes(), checkpoint)

    def test_repair_refuses_missing_tampered_checkpoint_admission_and_unknown_image(self):
        for fault in ('missing_checkpoint', 'tampered_checkpoint', 'missing_admission', 'tampered_admission',
                      'unknown_runtime', 'wrong_media', 'stale_labels', 'wrong_env'):
            with self.subTest(fault=fault):
                first, app, _ = self.run_deploy('runtime_image_swap')
                self.assertNotEqual(first.returncode, 0)
                if fault == 'missing_checkpoint':
                    (app / '.web-last-successful.json').unlink()
                elif fault == 'tampered_checkpoint':
                    with (app / 'releases/old/.release-metadata').open('a') as stream:
                        stream.write('source_tampered=yes\n')
                elif fault in ('missing_admission', 'tampered_admission'):
                    path = app / 'releases/new/.release-metadata'
                    value = path.read_text()
                    if fault == 'missing_admission':
                        value = '\n'.join(line for line in value.splitlines() if not line.startswith('web_admitted_runtime_images=')) + '\n'
                    else:
                        value = '\n'.join('web_admitted_config_sha256=' + '0' * 64
                            if line.startswith('web_admitted_config_sha256=') else line
                            for line in value.splitlines()) + '\n'
                    path.write_text(value)
                before = (app / 'destructive.log').read_bytes()
                second = self.repair_attempt(app, fault=fault if fault in ('unknown_runtime', 'wrong_media', 'stale_labels', 'wrong_env') else '')
                self.assertNotEqual(second.returncode, 0)
                self.assertEqual((app / 'current').resolve(), app / 'releases/new')
                self.assertFalse((app / '.active-release').exists())
                self.assertEqual((app / 'destructive.log').read_bytes(), before)
                self.assertFalse((app / 'releases/repair').exists())

    def test_failed_migration_allows_known_previous_runtime_forward_repair(self):
        first, app, _ = self.run_deploy('migration_failed')
        self.assertEqual(first.returncode, 23, first.stderr)
        self.assertFalse((app / 'recreated').exists())
        second = self.repair_attempt(app)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual((app / 'db-revision').read_text(), 'next')

    def test_rejected_unknown_runtime_does_not_destroy_repair_context(self):
        first, app, _ = self.run_deploy('runtime_image_swap')
        self.assertNotEqual(first.returncode, 0)
        rejected = self.repair_attempt(app, 'unknown', 'unknown_runtime')
        self.assertNotEqual(rejected.returncode, 0)
        repaired = self.repair_attempt(app)
        self.assertEqual(repaired.returncode, 0, repaired.stderr)

    def test_unhealthy_but_authenticated_failed_runtime_can_be_repaired(self):
        first, app, _ = self.run_deploy('unhealthy')
        self.assertNotEqual(first.returncode, 0)
        repaired = self.repair_attempt(app, fault='repair_unhealthy')
        self.assertEqual(repaired.returncode, 0, repaired.stderr)

    def test_mixed_runtime_requires_each_exact_previous_or_admitted_identity(self):
        first, app, _ = self.run_deploy('runtime_image_swap')
        self.assertNotEqual(first.returncode, 0)
        repaired = self.repair_attempt(app, fault='mixed_runtime')
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
    def test_assets_failure_precedes_media_and_activation(self):
        for fault in ('asset_hash', 'candidate_binding', 'ignored_context', 'candidate_capacity'):
            with self.subTest(fault=fault):
                result, app, previous = self.run_deploy(fault)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual((app / "current").resolve(), previous)
                self.assertEqual((app / ".active-release").read_text().strip(), str(previous))
                self.assertEqual((app / "destructive.log").read_text().splitlines()
                                 if (app / "destructive.log").exists() else [], [])
                self.assertEqual(list(app.glob('owned-*.json')), [])
                self.assertEqual(list(app.glob('.web-assets-scratch.*')), [])

    def test_runtime_image_swap_rejected(self):
        result, app, _ = self.run_deploy("runtime_image_swap")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((app / ".active-release").exists())

    def test_failed_candidate_never_retained(self):
        for fault in ('asset_hash', 'post_activation_failure'):
            with self.subTest(fault=fault):
                result, app, _ = self.run_deploy(fault)
                self.assertNotEqual(result.returncode, 0)
                metadata = app / "releases/new/.release-metadata"
                self.assertNotIn("activated_verified_utc=", metadata.read_text())
                self.assertNotIn('web_assets_status=verified', metadata.read_text())

    def test_bootstrap_once_only(self):
        result, _, _ = self.run_deploy("bootstrap_already_used")
        self.assertNotEqual(result.returncode, 0)

    def test_protocol_one_missing_manifest_never_bootstraps(self):
        result, _, _ = self.run_deploy("protocol_one_missing_manifest")
        self.assertNotEqual(result.returncode, 0)

    def test_receipt_write_failure_cannot_report_healthy(self):
        result, app, _ = self.run_deploy("receipt_write_failure")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((app / ".active-release").exists())
        self.assertNotIn("status=healthy", (app / ".deployment-state").read_text())

    def test_activation_receipt_refuses_changed_binding(self):
        result, app, _ = self.run_deploy('activation_binding')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((app / '.active-release').exists())
        self.assertNotIn('web_assets_status=verified', (app / 'releases/new/.release-metadata').read_text())

    def test_protocol_one_predecessor_is_verified_and_retained(self):
        result, app, _ = self.run_deploy('protocol_one')
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads((app / 'candidate-manifest.json').read_text())
        self.assertEqual([group['release_id'] for group in manifest['sets']], ['new', 'old'])
        receipt = (app / 'releases/new/.release-metadata').read_text()
        self.assertIn('web_assets_status=verified\n', receipt)
        self.assertIn('source_alembic_head=head\n', receipt)

    def test_verified_protocol_one_prior_is_retryable_after_preparation_failure(self):
        result, _, _ = self.run_deploy('protocol_one', prior_state='attempt_failed')
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_launcher_rejects_required_contract(self):
        result, app, previous = self.run_deploy('old_launcher')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((app / 'current').resolve(), previous)
        self.assertFalse((app / 'destructive.log').exists())

    def test_preparation_never_mutates_verified_source_snapshot(self):
        result, app, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = subprocess.check_output(['python3', str(SOURCE / 'scripts/nas-deploy-guard.py'),
            'digest', str(app / 'incoming/new')], text=True).strip()
        self.assertEqual(actual, self.source_digest)

    def test_stale_current_never_supplies_source_authority(self):
        result, app, _ = self.run_deploy('stale_pointer')
        self.assertEqual(result.returncode, 0, result.stderr)
        value = (app / 'releases/new/.env').read_text()
        self.assertIn('PRESERVE_ME=yes', value)
        self.assertNotIn('STALE_SOURCE', value)

    def run_deploy(self, fault="", with_backup=False, prior_state=None, hardened_backup=False, prior_protocol=False):
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
        (app / '.deployment-state').write_text(f'status=complete\nrelease={previous}\n')
        (previous / '.release-metadata').write_text('source_release_id=old\nsource_alembic_head=head\n')
        if prior_protocol or fault in ('protocol_one', 'protocol_one_missing_manifest'):
            def item(name):
                return {'path': name, 'size': 5, 'sha256': hashlib.sha256(b'hello').hexdigest()}
            manifest = {'schema_version': 1, 'release_id': 'old', 'build_id': 'old',
                'predecessor_image_id': 'sha256:' + '4' * 64, 'input_sha256': '5' * 64,
                'total_bytes': 15, 'documents': [item('index.html'), item('build-info.json')],
                'sets': [{'release_id': 'old', 'build_id': 'old', 'superseded_by_release_id': None,
                          'files': [item('assets/old.js')]}]}
            raw = json.dumps(manifest).encode()
            (app / 'old-manifest.json').write_bytes(raw)
            with (previous / '.release-metadata').open('a') as stream:
                stream.write('web_assets_contract=1\nweb_assets_status=verified\n'
                    'web_image_id=sha256:' + '1' * 64 + '\nweb_assets_manifest_sha256=' + hashlib.sha256(raw).hexdigest() +
                    '\nsource_tree_sha256=' + 'a' * 64 + '\nweb_assets_input_sha256=' + '5' * 64 +
                    '\nweb_predecessor_image_id=sha256:' + '4' * 64 + '\nactivated_verified_utc=2026-10-18T00:00:00Z\n')
        if fault == 'bootstrap_already_used':
            past = app / 'releases/past'
            past.mkdir()
            (past / '.release-metadata').write_text('web_assets_contract=1\nweb_assets_status=verified\n')
        if prior_state:
            state = "failed" if prior_state == "unverified" else "attempt_failed" if prior_state == 'attempt_failed' else "complete"
            (app / ".deployment-state").write_text(
                f"status={state}\nrelease={previous}\n" + ('active_unchanged=true\n' if state == 'attempt_failed' else '')
            )
            head = "unexpected" if prior_state == "schema_drift" else "head"
            metadata = previous / '.release-metadata'
            metadata.write_text(metadata.read_text().replace('source_alembic_head=head', f'source_alembic_head={head}'))
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
        if fault == 'stale_pointer':
            stale = app / 'releases/stale'
            stale.mkdir()
            (stale / '.env').write_text('STALE_SOURCE=yes\n')
            (stale / 'secrets').mkdir()
            (app / 'current').unlink()
            (app / 'current').symlink_to(stale)
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
        copy_asset_helpers(incoming)
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
import hashlib, io, json, os, sys, tarfile
from pathlib import Path
a=sys.argv[1:]; app=Path(os.environ['MOCK_APP']); fault=os.environ['MOCK_FAULT']
with (app/'pointer-history.log').open('a') as log: log.write((app/'current').resolve().name+'\\n')
candidate=os.environ.get('MOCK_CANDIDATE','new')
def image_for(release): return 'sha256:'+('2' if release=='new' else '4' if release=='repair' else '6')*64
old_id='sha256:'+'1'*64; new_id=image_for(candidate)
actual_image=image_for((app/'recreated').read_text() or 'new') if (app/'recreated').exists() else old_id
def record(name, body):
    return {'path':name,'size':len(body),'sha256':hashlib.sha256(body).hexdigest()}
def body_for(name):
    return b'hello' if name=='assets/old.js' else b'new'
def emit_tar(files):
    out=io.BytesIO()
    with tarfile.open(fileobj=out,mode='w',format=tarfile.USTAR_FORMAT) as archive:
        for name, body in files.items():
            item=tarfile.TarInfo(name); item.size=len(body)
            archive.addfile(item,io.BytesIO(body))
    sys.stdout.buffer.write(out.getvalue())
if a[0]=='image' and a[1]=='inspect':
    image=a[2] if a[2].startswith('sha256:') else new_id
    if '--format' in a: print(image); sys.exit(0)
    label={} if image==old_id else {'cn.jingxue.erp.web-assets-contract':'1'}
    if image==old_id and os.environ.get('MOCK_OLD_PROTOCOL')=='1': label={'cn.jingxue.erp.web-assets-contract':'1'}
    print(json.dumps([{'Id':image,'Config':{'Labels':label}}])); sys.exit(0)
if a[0]=='create':
    owner=a[a.index('--label')+1].split('=',1)[1]
    cid=hashlib.sha256(owner.encode()).hexdigest()
    (app/('owned-'+cid+'.json')).write_text(json.dumps({'Id':cid,'Image':a[-1],
      'Config':{'Labels':{'cn.jingxue.erp.web-assets-owner':owner}},'Mounts':[],
      'HostConfig':{'NetworkMode':'none'}}))
    print(cid); sys.exit(0)
if a[0]=='rm':
    (app/('owned-'+a[-1]+'.json')).unlink(); sys.exit(0)
if a[0]=='cp':
    cid,source=a[1].split(':',1)
    image=json.loads((app/('owned-'+cid+'.json')).read_text())['Image']
    if source=='/etc/erp-web/assets-manifest.json':
        if image==old_id:
            if fault=='protocol_one_missing_manifest': sys.exit(1)
            emit_tar({'assets-manifest.json':(app/'old-manifest.json').read_bytes()}); sys.exit(0)
        emit_tar({'assets-manifest.json':(app/'candidate-manifest.json').read_bytes()}); sys.exit(0)
    if image==old_id:
        files={'assets/old.js':b'hello'}
        if source.endswith('/.'): files.update({'index.html':b'hello','build-info.json':b'hello'})
        emit_tar(files); sys.exit(0)
    manifest=json.loads((app/'candidate-manifest.json').read_text())
    files={f['path']:body_for(f['path']) for f in [*manifest['documents'],*(f for s in manifest['sets'] for f in s['files'])]}
    if fault=='asset_hash': files['assets/new.js']=b'bad'
    emit_tar(files); sys.exit(0)
if a[0]=='compose':
    root=Path(a[a.index('--project-directory')+1])
    if root.resolve().name=='old' and 'config' in a and os.environ.get('MOCK_OLD_PROTOCOL')=='1':
        if os.environ.get('ERP_WEB_BUILD_ID')!='old' or os.environ.get('ERP_WEB_ASSET_INPUT_SHA256')!='5'*64: sys.exit(32)
    if root.resolve().name in ('old','stale'): fault=''
    if 'config' in a and root.name=='new' and not all(os.environ.get(k) for k in ('ERP_WEB_ASSETS_PROTOCOL','ERP_WEB_ASSET_INPUT_SHA256','ERP_WEB_BUILD_ID')): sys.exit(31)
    if '--initialize-store-identity' in a or 'alembic' in a or 'up' in a:
        with (app/'destructive.log').open('a') as log: log.write(('recreate' if 'up' in a else 'migrate' if 'alembic' in a else 'media_init')+'\\n')
    if 'build' in a and fault!='build_failed':
        (app/'build.log').write_text('build')
        override=json.loads((root/'.web-assets-compose.json').read_text())
        source=Path(override['services']['web']['build']['additional_contexts']['erp_retained_assets'])/'manifest.json'
        raw=source.read_bytes(); pre=json.loads(raw)
        docs=[record(name,b'new') for name in ('index.html','build-info.json')]
        sets=[{'release_id':candidate,'build_id':candidate,'superseded_by_release_id':None,'files':[record('assets/new.js',b'new')]}]+pre['sets']
        unique={f['path']:f for f in [*docs,*(f for s in sets for f in s['files'])]}
        manifest={'schema_version':1,'release_id':candidate,'build_id':candidate,'predecessor_image_id':old_id,
          'input_sha256':hashlib.sha256(raw).hexdigest(),'total_bytes':sum(f['size'] for f in unique.values()),'documents':docs,'sets':sets}
        (app/'candidate-manifest.json').write_text(json.dumps(manifest))
        if fault=='candidate_binding': manifest['input_sha256']='0'*64
        if fault=='ignored_context': manifest['sets']=manifest['sets'][:1]; manifest['total_bytes']=9
        if fault=='candidate_capacity': manifest['total_bytes']=536870913
        (app/'candidate-manifest.json').write_text(json.dumps(manifest))
    if 'up' in a:
        (app/'recreated').write_text(candidate)
        if fault=='receipt_write_failure': (app/'releases/new/.release-metadata.deploy-tmp').mkdir()
        if fault=='activation_binding':
            context=Path(json.loads((root/'.web-assets-compose.json').read_text())['services']['web']['build']['additional_contexts']['erp_retained_assets'])
            path=context.parent.parent/'prepare.json'
            value=json.loads(path.read_text()); value['input_sha256']='f'*64
            path.write_text(json.dumps(value))
    if fault=='build_failed' and 'build' in a: sys.exit(24)
    if fault=='migration_failed' and 'alembic' in a: sys.exit(23)
    if 'alembic' in a: (app/'db-revision').write_text('next')
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
            if s=='web' and (root/'.web-assets-compose.json').is_file(): image=json.loads((root/'.web-assets-compose.json').read_text())['services']['web']['image']
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
    if len(a)==2 and (app/('owned-'+a[1]+'.json')).is_file():
        print('['+(app/('owned-'+a[1]+'.json')).read_text()+']'); sys.exit(0)
    if '-f' in a or '--format' in a:
        if any('StartedAt' in arg for arg in a): print('2026-09-14T00:00:00Z'); sys.exit(0)
        print('running' if a[-1].endswith('caddy-1') or a[-1].endswith('scheduler-1') else 'healthy'); sys.exit(0)
    result=[]
    if not (app/'recreated').exists() and fault in ('missing_media','wrong_media','missing_status','unhealthy','wrong_image','wrong_env','stale_labels'):
        fault=''
    for name in a[1:]:
        s=name.removeprefix('erp-').removesuffix('-1'); mounts=[]
        if s=='db': mounts=[{'Type':'volume','Name':'erp_postgres_data','Source':'/vol/postgres_data','Destination':'/var/lib/postgresql/data','RW':True}]
        if s in ['api','scheduler']:
            mounts=[{'Type':'volume','Name':'erp_'+n,'Source':'/vol/'+n,'Destination':'/app/runtime/'+t,'RW':True} for n,t in [('evaluation_media','evaluation-media'),('evaluation_quarantine','evaluation-quarantine')]]
            if s=='api': mounts.append({'Type':'bind','Source':str(app/'operations'),'Destination':'/app/var','RW':False})
        if fault=='missing_media' and s=='scheduler': mounts=[]
        if fault=='wrong_media' and s=='scheduler': mounts[0]['Name']='empty_volume'; mounts[0]['Source']='/vol/empty'
        if fault=='missing_status' and s=='api': mounts=mounts[:2]
        unhealthy=s=='scheduler' and (fault=='unhealthy' or fault=='repair_unhealthy' and (app/'recreated').read_text()=='new')
        state={'Status':'running','Health':{'Status':'unhealthy' if unhealthy else 'healthy'},'StartedAt':'2026-09-14T00:00:00Z'}
        labels={'com.docker.compose.project.working_dir':str(app/'current'),'com.docker.compose.project.config_files':','.join(str(app/'current'/f) for f in ['compose.yaml','compose.prod.yaml','compose.nas-staging.yaml','compose.nas-lan-staging.yaml'])}
        labels.update({'com.docker.compose.project':'erp','com.docker.compose.service':s})
        if (app/'recreated').exists(): labels['com.docker.compose.project.config_files']+=','+str(app/'current/.web-assets-compose.json')
        if fault=='stale_labels': labels['com.docker.compose.project.working_dir']=str(app/'releases/old')
        env=['POSTGRES_DB=erp','POSTGRES_USER=erp'] if s=='db' else ['APP_ENV='+('development' if fault=='wrong_env' else 'production')]
        if fault in ('absolute_secret','relative_secret','default_secret','wrong_secret_target') and s in ('api','scheduler'): env.append('DATABASE_URL_FILE=/run/secrets/database_url')
        image='postgres:16-alpine' if s=='db' else 'erp-'+s+(':latest' if fault=='wrong_image' and (app/'recreated').exists() else ':new')
        if s=='web' and (app/'recreated').exists(): image=actual_image
        runtime_config={'Image':image,'Labels':labels,'Env':env}
        if s=='db': runtime_config.update({'Cmd':['postgres'],'Entrypoint':['docker-entrypoint.sh']})
        immutable=(('sha256:'+'3'*64) if fault=='runtime_image_swap' or fault=='unknown_runtime' and s=='web' else actual_image) if (app/'recreated').exists() and s!='db' else old_id
        if (fault=='mixed_runtime' or os.environ.get('MOCK_RUNTIME_MIXED')=='1') and (app/'recreated').read_text()=='new' and s in ('proxy','caddy'):
            immutable=old_id
            labels['com.docker.compose.project.config_files']=','.join(str(app/'current'/f) for f in ['compose.yaml','compose.prod.yaml','compose.nas-staging.yaml','compose.nas-lan-staging.yaml'])
        result.append({'Name':'/'+name,'Mounts':mounts,'State':state,'Config':runtime_config,'Image':immutable})
    print(json.dumps(result)); sys.exit(0)
if a[0]=='exec':
    if fault=='db_not_ready' and 'SELECT 1' in a[-1]: sys.exit(1)
    if fault=='store_mismatch' and '--verify-store-identity' in a: sys.exit(1)
    if 'psql' in a: print('t' if 'operations_service_heartbeats' in a[-1] else (app/'db-revision').read_text() if (app/'db-revision').exists() else 'head')
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
        launcher_text = LAUNCHER.read_text()
        if fault == 'old_launcher':
            launcher_text = subprocess.check_output(['git', '-C', str(SOURCE), 'show',
                '317b8925797102160395b233afdcd655008635b9:scripts/deploy-nas.sh'], text=True)
        if hardened_backup:
            installed = subprocess.run(['python3', str(SOURCE / 'scripts/erp-backup-install.py'),
                '--app-dir', str(app), '--project', 'erp', '--backup-dir', str(app / 'backups'),
                '--key-file', str(base / 'backup.key'), '--status-file', str(operations / 'backup-status.json')],
                capture_output=True, text=True, check=False, env={**os.environ, 'PATH': str(bindir) + os.pathsep + os.environ['PATH'],
                    'TZ': 'Asia/Shanghai'})
            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.prior_wrapper = (app / 'backup-tools/run.py').read_bytes()
        body = (
            launcher_text
            .split("<<'REMOTE_DEPLOY'\n", 1)[1]
            .split("\nREMOTE_DEPLOY", 1)[0]
        )
        if fault == 'post_activation_failure':
            body += '\nfalse\n'
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
        self.source_digest = digest
        self.deploy_body = body
        self.deploy_arguments = [
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
            ]
        self.deploy_environment = {
                **os.environ,
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "MOCK_APP": str(app),
                "MOCK_FAULT": fault,
                'MOCK_OLD_PROTOCOL': '1' if prior_protocol or fault in ('protocol_one', 'protocol_one_missing_manifest') else '0',
                "TZ": "Asia/Shanghai",
            }
        result = subprocess.run(
            self.deploy_arguments,
            input=body,
            text=True,
            capture_output=True,
            check=False,
            env=self.deploy_environment,
        )
        return result, app, previous

    def test_deployment_waits_for_exclusive_lock_with_bounded_timeout(self):
        result, app, _ = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((app / "flock.log").read_text().splitlines()[0], "-x -w 50 9")

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
                    "build_failed", with_backup=True, prior_state=state, hardened_backup=state != 'schema_drift'
                )
                if state == 'schema_drift':
                    self.assertEqual(result.returncode, 24, result.stderr)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertFalse((app / 'build.log').exists())
                    self.assertFalse((app / 'destructive.log').exists())
                    self.assertFalse((app / 'releases/new/.release-metadata').exists())
                    self.assertEqual((app / 'backup-tools/run.py').read_bytes(), self.prior_wrapper)
                result = subprocess.run(
                    ["python3", str(app / "backup-tools/run.py")],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((app / "previous-backup-ran").exists())
                self.assertFalse((app / 'unsafe-backup').exists())

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
            copy_asset_helpers(source)
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
            root = Path(directory)
            source = root / "source"
            (source / "apps/api").mkdir(parents=True)
            (source / "apps/api/alembic.ini").write_text("fixture")
            (source / "scripts").mkdir()
            shutil.copyfile(
                SOURCE / "scripts/nas-deploy-guard.py",
                source / "scripts/nas-deploy-guard.py",
            )
            copy_asset_helpers(source)
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
                "ssh": "#!/usr/bin/env python3\nimport sys\nb=sys.stdin.read()\nif 'REMOTE' in b or 'mkdir' in b or 'compose' in b and 'test -f' not in b: raise SystemExit('unexpected remote write')\nif 'select version_num' in b: print('head')\n",
            }.items():
                executable = bindir / name
                executable.write_text(code)
                executable.chmod(0o755)
            env = {
                **os.environ,
                "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
                "NAS_SSH_IDENTITY_FILE": str(key),
            }

            def dry_run():
                result = subprocess.run(
                    ["bash", str(LAUNCHER), "--source", str(source), "--dry-run"],
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
