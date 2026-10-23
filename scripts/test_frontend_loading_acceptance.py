"""Behavioral launcher safety tests: real validation, owned-process lifecycle."""
import ast
import base64
import importlib.util
import io
import json
import os
import re
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('run-frontend-loading-acceptance.py')


class DockerModel:
    """Only the Docker boundary is simulated; ownership/lifecycle code is real."""
    def __init__(self, module):
        self.module = module
        self.rows = []
        self.volumes = {'volume-exact'}
        self.networks = {'network-exact'}
        self.fail_remove = False

    def owned(self, identifier='partial-exact'):
        return {'Id': identifier, 'Config': {'Labels': {
            'com.docker.compose.project': 'loading-owned', 'erp.loading.owner': 'nonce'}}}

    def containers(self):
        return list(self.rows)

    def __call__(self, *args, capture=False):
        if args[:2] == ('rm', '-f'):
            if self.fail_remove:
                raise self.module.Refused('cleanup failure')
            self.rows = [row for row in self.rows if row['Id'] not in args[2:]]
        elif args[:2] == ('volume', 'ls'):
            return '\n'.join(self.volumes)
        elif args[:2] == ('volume', 'inspect'):
            return json.dumps([{'Name': args[2], 'Labels': {'com.docker.compose.project': 'loading-owned'}}])
        elif args[:2] == ('volume', 'rm'):
            self.volumes.remove(args[2])
        elif args[:2] == ('network', 'ls'):
            return '\n'.join(self.networks)
        elif args[:2] == ('network', 'inspect'):
            return json.dumps([{'Id': args[2], 'Labels': {'com.docker.compose.project': 'loading-owned'}}])
        elif args[:2] == ('network', 'rm'):
            self.networks.remove(args[2])
        else:
            raise AssertionError('unexpected Docker boundary command')
        return ''


class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('loading_launcher', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def test_implementation_exists(self):
        self.assertTrue(SCRIPT.is_file(), 'isolated launcher must exist')

    def test_direct_pair_fixed_sources_are_serial_and_first_failure_never_starts_candidate(self):
        self.assertTrue(hasattr(self.module, 'collect_direct_pair'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline, candidate = root / 'baseline', root / 'candidate'
            baseline.mkdir(); candidate.mkdir()
            identities = {'productBase': 'd' * 40, 'toolCommit': 'c' * 40, 'harnessSha256': '1' * 64}
            events = []
            def source(path, *, baseline):
                return self.module.BASELINE_SHA if baseline else 'c' * 40
            def collect(path, sha, evidence, label, *args, **kwargs):
                events.append(('start', label, sha, kwargs['check']))
                (evidence / (label + '.json')).write_text(json.dumps({'ok': True, 'browserVersion': '151.0.7922.34',
                    'fingerprint': {'sourceCommit': sha, 'images': {'proxy': 'proxy'}, 'dependencyImage': 'dep', 'proxySha256': 'proxyhash'}}))
                events.append(('cleanup', label))
            with patch.object(self.module, 'verify_source', side_effect=source), \
                    patch.object(self.module, 'direct_source_identity', return_value=identities), \
                    patch.object(self.module, 'collect', side_effect=collect):
                self.module.collect_direct_pair(baseline, self.module.BASELINE_SHA, candidate, 'c' * 40,
                    root / 'evidence', root, object(), {}, 'browser')
            self.assertEqual([event[:2] for event in events], [('start', 'direct-baseline'), ('cleanup', 'direct-baseline'),
                ('start', 'direct-candidate'), ('cleanup', 'direct-candidate')])
            result = json.loads((root / 'evidence/direct-document-diagnostic.json').read_text())
            self.assertTrue(result['ok'])
            self.assertEqual([row['cleanup'] for row in result['arms']], ['complete', 'complete'])
            events.clear()
            def failed(*args, **kwargs):
                events.append('baseline attempted')
                raise self.module.Refused('bounded failure')
            with patch.object(self.module, 'verify_source', side_effect=source), \
                    patch.object(self.module, 'direct_source_identity', return_value=identities), \
                    patch.object(self.module, 'collect', side_effect=failed), self.assertRaises(self.module.Refused):
                self.module.collect_direct_pair(baseline, self.module.BASELINE_SHA, candidate, 'c' * 40,
                    root / 'failure', root, object(), {}, 'browser')
            self.assertEqual(events, ['baseline attempted'])
            result = json.loads((root / 'failure/direct-document-diagnostic.json').read_text())
            self.assertFalse(result['ok'])
            self.assertEqual(result['arms'][1]['status'], 'not_run')
            self.assertEqual(result['arms'][0]['cleanup'], 'unconfirmed')

    def test_direct_identity_real_git_allows_only_tool_delta_and_rejects_product_or_untracked_source(self):
        self.assertTrue(hasattr(self.module, 'direct_source_identity'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL, text=True).strip()
            git('init'); git('config', 'user.email', 'fixture@example.invalid'); git('config', 'user.name', 'fixture')
            product = root / 'product.txt'; product.write_text('unchanged')
            for name in self.module.DIRECT_HARNESS_FILES:
                fixture = root / name
                fixture.parent.mkdir(parents=True, exist_ok=True)
                fixture.write_text('unchanged tool fixture')
            git('add', '.'); git('commit', '-m', 'base'); base = git('rev-parse', 'HEAD')
            tool = root / 'apps/web/scripts/direct-document-diagnostic.test.mjs'
            tool.write_text('test tool')
            git('add', '.'); git('commit', '-m', 'tools'); candidate = git('rev-parse', 'HEAD')
            with patch.object(self.module, 'DIRECT_PRODUCT_BASE', base), patch.object(self.module, 'ROOT', root):
                result = self.module.direct_source_identity(root, candidate)
                self.assertEqual(result['productBase'], base)
                self.assertEqual(result['candidateCommit'], candidate)
                callback_tool = root / 'apps/web/tests/helpers/readyChainDiagnostic.mjs'
                callback_tool.write_text('explicitly approved validator callback fixture')
                git('add', '.'); git('commit', '-m', 'approved validator callback'); candidate = git('rev-parse', 'HEAD')
                result = self.module.direct_source_identity(root, candidate)
                self.assertEqual(result['candidateCommit'], candidate)
                untracked = root / 'untracked.py'
                untracked.write_text('not archived')
                with self.assertRaisesRegex(self.module.Refused, 'untracked non-ignored source'):
                    self.module.direct_source_identity(root, candidate)
                untracked.unlink()
                self.assertEqual(self.module.direct_source_identity(root, candidate), result)
                product.write_text('drift'); git('add', '.'); git('commit', '-m', 'product drift')
                with self.assertRaises(self.module.Refused): self.module.direct_source_identity(root, git('rev-parse', 'HEAD'))
                with self.assertRaises(self.module.Refused): self.module.direct_source_identity(root, candidate)

    def test_direct_named_admission_has_no_ref_url_or_custom_count_interface(self):
        args = None
        with redirect_stderr(io.StringIO()):
            try:
                args = self.module.parse_arguments(['--baseline-source', '.', '--candidate-source', '.', '--evidence-dir', '.',
                    '--phase', 'named', '--check', 'direct-document-diagnostic'])
            except SystemExit:
                pass
        self.assertIsNotNone(args, 'the fixed direct-document check must be admitted')
        self.assertEqual(args.check, 'direct-document-diagnostic')
        for extra in (['--ref', 'HEAD~1'], ['--cycles', '1'], ['--url', 'http://private']):
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.module.parse_arguments(['--baseline-source', '.', '--candidate-source', '.', '--evidence-dir', '.',
                    '--phase', 'named', '--check', 'direct-document-diagnostic', *extra])

    def test_direct_pair_restores_interrupt_handler_after_each_existing_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = signal.getsignal(signal.SIGINT)
            handler = lambda *_: None
            signal.signal(signal.SIGINT, handler)
            def collect(source, sha, evidence, label, *args, **kwargs):
                self.assertIs(signal.getsignal(signal.SIGINT), handler)
                (evidence / (label + '.json')).write_text(json.dumps({'ok': True, 'browserVersion': '151.0.7922.34',
                    'fingerprint': {'sourceCommit': sha, 'images': {'proxy': 'proxy'}, 'dependencyImage': 'dep', 'proxySha256': 'hash'}}))
                signal.signal(signal.SIGINT, signal.SIG_IGN)  # Existing cleanup's actual boundary behavior.
            try:
                with patch.object(self.module, 'verify_source', side_effect=lambda path, baseline: self.module.BASELINE_SHA if baseline else 'c' * 40), \
                        patch.object(self.module, 'direct_source_identity', return_value={'fixed': True}), \
                        patch.object(self.module, 'collect', side_effect=collect):
                    self.module.collect_direct_pair(root, self.module.BASELINE_SHA, root, 'c' * 40,
                        root / 'evidence', root, object(), {}, 'browser')
                self.assertIs(signal.getsignal(signal.SIGINT), handler)
            finally:
                signal.signal(signal.SIGINT, original)

    def test_private_otp_bridge_only_executes_fixed_uid_reader_after_owner_check(self):
        self.assertTrue(hasattr(self.module, 'OtpBridge'))
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            events = []
            def owned():
                events.append('ownership')
                return 'exact-api-id'
            def docker(*args, capture=False):
                events.append(args)
                return '123456\n'
            bridge = self.module.OtpBridge(root, docker, owned)
            bridge.tick()
            self.assertEqual(events, [])
            request = {'sequence': 1, 'purpose': 'registration', 'challengeId': '00000000-0000-4000-8000-000000000001',
                'destinationAlias': 'dst_' + hashlib.sha256(b'registration-test-destination\0registration-adult-e2e@example.com').hexdigest()[:24]}
            self.module.private_json(root / 'otp-request.json', request)
            bridge.tick()
            self.assertEqual(events[0], 'ownership')
            self.assertEqual(events[1][:8], ('exec', '--user', '10001:10001', 'exact-api-id', 'python', '-m', 'app.cli.read_registration_test_code', '--purpose'))
            self.assertEqual(json.loads((root / 'otp-response.json').read_text()), {'sequence': 1, 'code': '123456'})
            self.assertEqual((root / 'otp-response.json').stat().st_mode & 0o777, 0o600)
            self.module.private_json(root / 'otp-request.json', request)
            with self.assertRaises(self.module.Refused): bridge.tick()
            self.assertEqual(len(events), 2)

    def test_email_topology_rejects_duplicate_or_spoofable_service_layout(self):
        import copy
        rows = [{'Id': service, 'Config': {'Labels': {'com.docker.compose.project': 'project', 'erp.loading.owner': 'owner',
                'com.docker.compose.service': service}, 'Cmd': ["uvicorn '--host' '127.0.0.1' '--no-proxy-headers'"]},
            'HostConfig': {'NetworkMode': 'network', 'PortBindings': {}},
            'NetworkSettings': {'Networks': {'owned': {'NetworkID': 'owned-network'}}}}
            for service in self.module.SERVICES]
        rows[1]['HostConfig']['PortBindings'] = {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18088'}]}
        rows[3]['HostConfig']['NetworkMode'] = 'container:api'
        self.assertEqual(self.module.validate_email_topology(rows, 'project', 'owner', set(self.module.SERVICES), 18088), 'api')
        with self.assertRaises(self.module.Refused):
            self.module.validate_email_topology([*rows, rows[1]], 'project', 'owner', set(self.module.SERVICES), 18088)
        for service, field, value in [('api', 'Cmd', ['uvicorn']), ('proxy', 'NetworkMode', 'host'),
                ('api', 'PortBindings', {'8080/tcp': [{'HostIp': '0.0.0.0', 'HostPort': '18088'}]})]:
            altered = copy.deepcopy(rows)
            item = next(row for row in altered if row['Id'] == service)
            item['Config' if field == 'Cmd' else 'HostConfig'][field] = value
            with self.assertRaises(self.module.Refused):
                self.module.validate_email_topology(altered, 'project', 'owner', set(self.module.SERVICES), 18088)

    def test_state_exact_counts_reject_extra_registration_or_unrelated_business_write(self):
        self.assertTrue(hasattr(self.module, 'verify_state_counts'))
        before = {key: 10 for key in ('accounts', 'activeAccounts', 'people', 'applications', 'finalized', 'assistedAccounts', 'intakes', 'approvals', 'stays', 'overrides')}
        after = {**before, 'accounts': 13, 'activeAccounts': 13, 'people': 12, 'applications': 12, 'finalized': 12, 'assistedAccounts': 11}
        self.module.verify_state_counts(before, after)
        for key, value in after.items():
            with self.subTest(key=key), self.assertRaises(self.module.Refused):
                self.module.verify_state_counts(before, {**after, key: value + 1})

    def test_legacy_database_requires_exact_new_id_owner_mount_and_loopback_port(self):
        self.assertTrue(hasattr(self.module, 'validate_legacy_database'))
        row = {'Id': 'exact-db', 'Image': 'sha256:exact', 'Config': {'Labels': {
            'com.docker.compose.project': 'project', 'erp.loading.owner': 'owner', 'com.docker.compose.service': 'db'}},
            'HostConfig': {'PortBindings': {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '12345'}]}},
            'Mounts': [{'Type': 'volume', 'Name': 'new-volume', 'Destination': '/var/lib/postgresql/data'}]}
        self.module.validate_legacy_database(row, 'project', 'owner', 'exact-db', 'new-volume', 'sha256:exact', 12345)
        for field, value in [('Id', 'foreign'), ('Image', 'wrong'), ('Mounts', []), ('HostConfig', {'PortBindings': {}})]:
            with self.subTest(field=field), self.assertRaises(self.module.Refused):
                self.module.validate_legacy_database({**row, field: value}, 'project', 'owner', 'exact-db', 'new-volume', 'sha256:exact', 12345)

    def test_legacy_suites_keep_original_28_entries_and_stop_on_first_failure(self):
        self.assertTrue(hasattr(self.module, 'run_legacy_suites'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            class Boundary:
                def run(inner, args, **kwargs):
                    suite = args[-1]
                    calls.append(suite)
                    if suite == 'registration': raise self.module.Refused('isolated script failed (exit 1)')
                    kwargs['log'].write_text('\n'.join(f'Assisted E2E passed: phase={suite}/fixed count={n}' for n in range(1, 3)))
            result = {'suites': [], 'ok': False}
            with self.assertRaises(self.module.Refused):
                self.module.run_legacy_suites(root, result, root / 'safe.json', Boundary(), {}, lambda: calls.append('owner'))
            self.assertEqual(calls, ['owner', 'assisted', 'owner', 'team', 'owner', 'registration'])
            self.assertEqual([entry['exitCode'] for entry in result['suites']], [0, 0, 1])
            self.assertEqual(self.module.LEGACY_SUITES, (('assisted', 2), ('team', 2), ('registration', 8), ('housing-occupancy', 8), ('housing-reporting', 8)))

    def test_supervisor_allows_hardened_nested_owner_to_reap_its_separate_session(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'nested.pid'
            code = ("import os,signal,subprocess,sys,time;from pathlib import Path;"
                    "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],start_new_session=True);"
                    f"Path({str(pidfile)!r}).write_text(str(child.pid));"
                    "signal.signal(signal.SIGTERM,lambda *args:None);"
                    "\ntry:\n time.sleep(60)\nfinally:\n time.sleep(.4);os.killpg(child.pid,signal.SIGKILL);child.wait()")
            # Match the original runner's signal-driven finally rather than a bare child.
            code = code.replace("lambda *args:None", "lambda *args:(_ for _ in ()).throw(KeyboardInterrupt())")
            parent = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            supervisor = self.module.Supervisor()
            supervisor.children.append(parent)
            child = None
            try:
                for _ in range(100):
                    if pidfile.exists(): break
                    time.sleep(.01)
                child = int(pidfile.read_text())
                supervisor.close()
                with self.assertRaises(ProcessLookupError): os.kill(child, 0)
            finally:
                supervisor.close()
                if child:
                    try: os.killpg(child, signal.SIGKILL)
                    except ProcessLookupError: pass

    def test_private_otp_bridge_refuses_wrong_file_owner_before_docker(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            self.module.private_json(root / 'otp-request.json', {})
            called = []
            bridge = self.module.OtpBridge(root, lambda *_args, **_kwargs: called.append('docker'), lambda: called.append('owner'))
            with patch.object(self.module.os, 'getuid', return_value=os.getuid() + 1), self.assertRaises(self.module.Refused):
                bridge.tick()
            self.assertEqual(called, [])

    def test_release_window_preserves_its_own_failure_without_claiming_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'release-window.json').write_text('{"ok":false}')
            with self.assertRaises(self.module.Refused):
                self.module.prepare_evidence(root, 'release-window')
            self.assertFalse((root / 'candidate.json').exists())

    def test_prebrowser_owner_receipt_also_prevents_evidence_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'release-window-owner.json').write_text('{}')
            with self.assertRaises(self.module.Refused): self.module.prepare_evidence(root, 'release-window')

    def test_release_barrier_only_accepts_exact_next_message(self):
        self.assertTrue(hasattr(self.module, 'validate_release_message'))
        self.module.validate_release_message({'stage': 'ready-A'}, 'ready-A')
        for message in ({'stage': 'ready-C'}, {'stage': 'ready-A', 'command': 'unsafe'},
                        {'stage': '../arbitrary'}, [], None):
            with self.subTest(message=message), self.assertRaises(self.module.Refused):
                self.module.validate_release_message(message, 'ready-A')

    def test_release_marker_changes_exact_two_archive_files_and_refuses_wrong_source(self):
        self.assertTrue(hasattr(self.module, 'patch_release_fixture'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'apps/web/src/esportsAssessments'
            folder.mkdir(parents=True)
            js = folder / 'AssessmentHistoryPage.tsx'
            css = folder / 'reports.css'
            js.write_text('<div className="assessmentReports">unchanged</div>')
            css.write_text('.assessmentReports {\n  color: black;\n}\n')
            result = self.module.patch_release_fixture(root, 'A')
            self.assertIn('data-release-window="A"', js.read_text())
            self.assertIn('--erp-release-fixture: A;', css.read_text())
            self.assertEqual(len(result), 2)
            with self.assertRaises(self.module.Refused): self.module.patch_release_fixture(root, 'B')

    def test_cleanup_reconciles_replacement_and_stopped_reader_ids(self):
        model = DockerModel(self.module)
        model.rows = [model.owned('web-C'), model.owned('proxy-C'), model.owned('reader-D')]
        ids = {'web-A', 'proxy-A'}
        self.module.cleanup_owned_resources(model, model.containers, 'loading-owned', 'nonce', ids)
        self.assertEqual(model.rows, [])
        self.assertTrue({'web-C', 'proxy-C', 'reader-D'}.issubset(ids))

    def test_shared_email_namespace_cleanup_removes_proxy_before_api(self):
        model = DockerModel(self.module)
        api, proxy = model.owned('api-exact'), model.owned('proxy-exact')
        api['Config']['Labels']['com.docker.compose.service'] = 'api'
        proxy['Config']['Labels']['com.docker.compose.service'] = 'proxy'
        proxy['HostConfig'] = {'NetworkMode': 'container:api-exact'}
        model.rows = [api, proxy]
        calls = []
        def docker(*args, **kwargs):
            calls.append(args)
            return model(*args, **kwargs)
        self.module.cleanup_owned_resources(docker, model.containers, 'loading-owned', 'nonce', set())
        self.assertEqual([call for call in calls if call[:2] == ('rm', '-f')],
                         [('rm', '-f', 'proxy-exact'), ('rm', '-f', 'api-exact')])
        self.assertEqual((model.rows, model.volumes, model.networks), ([], set(), set()))

    def test_release_activation_is_private_and_only_visible_after_complete_write(self):
        self.assertTrue(hasattr(self.module, 'publish_release_activation'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = self.module.private_json
            for marker in ('C', 'D'):
                destination = root / f'active-{marker}.json'
                def writing(path, value, target=destination):
                    self.assertNotEqual(path, target)
                    self.assertFalse(target.exists())
                    original(path, value)
                    self.assertFalse(target.exists())
                with patch.object(self.module, 'private_json', side_effect=writing):
                    self.module.publish_release_activation(root, marker, {'marker': marker})
                self.assertEqual(json.loads(destination.read_text()), {'releases': {'marker': marker}})
                self.assertEqual(destination.stat().st_mode & 0o777, 0o600)
                self.assertFalse(destination.with_suffix('.json.tmp').exists())
            with self.assertRaises(self.module.Refused):
                self.module.publish_release_activation(root, 'arbitrary', {})

    def test_release_barrier_timeout_wrong_owner_and_unknown_message_refuse(self):
        sys.path.insert(0, str(SCRIPT.parent))
        from frontend_release_window import wait_barrier
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ready-A.json'
            with self.assertRaises(TimeoutError):
                wait_barrier(path, 'ready-A', self.module.validate_release_message, time.monotonic() - 1)
            self.assertFalse(wait_barrier(path, 'ready-A', self.module.validate_release_message, time.monotonic() + 1))
            self.module.private_json(Path(directory) / 'ready-unknown.json', {'stage': 'arbitrary'})
            with self.assertRaises(ValueError):
                wait_barrier(path, 'ready-A', self.module.validate_release_message, time.monotonic() + 1)

    def test_release_js_witness_accepts_actual_minifier_template_literal_not_wrong_marker(self):
        sys.path.insert(0, str(SCRIPT.parent))
        import frontend_release_window as release
        self.assertTrue(hasattr(release, 'has_asset_witness'))
        self.assertTrue(release.has_asset_witness('"data-release-window":`A`,children:', 'js', 'A'))
        self.assertFalse(release.has_asset_witness('"data-release-window":`B`,children:', 'js', 'A'))
        self.assertTrue(release.has_asset_witness('--erp-release-fixture:A;color:', 'css', 'A'))

    def test_esports_fixed_entries_preserve_seed_browser_verify_financial_order(self):
        self.assertTrue(hasattr(self.module, 'run_esports_matrix'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'apps/api/app/cli'
            cli.mkdir(parents=True)
            (cli / 'e2e_credentials.json').write_text(json.dumps({key: {'identifier': key, 'password': 'synthetic'} for key in ('admin', 'customer_service')}))
            baseline = root / 'original.json'
            baseline.write_text('immutable')
            calls = []
            class Boundary:
                def run(inner, args, **kwargs):
                    if args[0] == 'node':
                        config = json.loads(Path(args[-1]).read_text())
                        calls.append(('browser', config['entry'], config['mode']))
                        self.assertNotIn('DATABASE_URL', kwargs['env'])
                        if config['mode'] == 'audio':
                            (Path(args[-1]).parent / 'safe-audio.json').write_text('{"ok":true,"checks":[{"ok":true},{"ok":true}]}')
                    elif args[2] == 'app.cli.seed_esports_assessments_e2e':
                        calls.append(('verify' if kwargs['env'].get('ESPORTS_E2E_VERIFY_MODE') else 'seed', kwargs['env'].get('ESPORTS_E2E_VERIFY_MODE')))
                    elif args[2] == 'app.cli.summarize_esports_assessments_e2e':
                        Path(args[args.index('--output') + 1]).write_text('{"passed":2}')
                    else: calls.append(('financial',))
            payload = {'output': str(root / 'output.json'), 'fingerprint': {}, 'baseURL': 'http://127.0.0.1:12345'}
            self.module.run_esports_matrix(root, root, payload, Boundary(), {'ASSISTED_E2E_BASELINE_FILE': str(baseline)}, {}, root / 'runtime.log')
            self.assertEqual([item for item in calls if item[0] == 'browser'], [('browser', index, 'existing') for index in range(6)] + [('browser', 5, 'audio')])
            self.assertEqual([item[1] for item in calls if item[0] == 'verify'], ['pc', 'touch', 'recovery', 'capabilities-desktop', 'capabilities-touch', 'capabilities-webkit', 'capabilities-webkit'])
            for index, item in enumerate(calls):
                if item[0] in ('seed', 'verify'): self.assertEqual(calls[index + 1], ('financial',))
            self.assertEqual(baseline.read_text(), 'immutable')
            self.assertEqual(json.loads((root / 'output.json').read_text()).get('audio'), {'ok': True, 'checks': [{'ok': True}, {'ok': True}]})

    def test_esports_failed_summary_is_saved_without_masking_original_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'apps/api/app/cli'
            cli.mkdir(parents=True)
            (cli / 'e2e_credentials.json').write_text(json.dumps({key: {} for key in ('admin', 'customer_service')}))
            baseline = root / 'original.json'
            baseline.write_text('immutable')
            summary = {'failed': 1, 'skipped': 1, 'phase_diagnostic': {'checkpoint': 'pc_initial_trial', 'observed': 'preflight'}}
            calls = []
            module = self.module
            class Boundary:
                def run(inner, args, **kwargs):
                    if args[0] == 'node':
                        calls.append('browser')
                        raise module.Refused('original browser failure (exit 1)')
                    if args[2] == 'app.cli.summarize_esports_assessments_e2e':
                        Path(args[args.index('--output') + 1]).write_text(json.dumps(summary))
                        raise module.Refused('summary correctly rejects failed case (exit 1)')
                    calls.append('financial' if args[-1] == '--verify-financial-baseline' else 'seed')
            output = root / 'result.json'
            with self.assertRaisesRegex(module.Refused, 'original browser failure'):
                module.run_esports_matrix(root, root, {'output': str(output), 'fingerprint': {}}, Boundary(),
                    {'ASSISTED_E2E_BASELINE_FILE': str(baseline)}, {}, root / 'runtime.log')
            result = json.loads(output.read_text())
            self.assertEqual(len(result['entries']), 1)
            self.assertEqual(result['entries'][0].get('summary'), summary)
            self.assertTrue(result['entries'][0]['financialAfter'])
            self.assertFalse(result['entries'][0]['resultsVerified'])
            self.assertEqual(calls, ['seed', 'financial', 'browser', 'financial'])

    def test_esports_successful_summary_command_without_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / 'apps/api/app/cli'
            cli.mkdir(parents=True)
            (cli / 'e2e_credentials.json').write_text(json.dumps({key: {} for key in ('admin', 'customer_service')}))
            baseline = root / 'original.json'
            baseline.write_text('immutable')
            class Boundary:
                def run(inner, args, **kwargs):
                    if args[0] == 'node':
                        config = json.loads(Path(args[-1]).read_text())
                        if config['mode'] == 'audio':
                            (Path(args[-1]).parent / 'safe-audio.json').write_text('{"ok":true}')
                    return ''  # Includes a zero-exit summarizer that wrote nothing.
            output = root / 'result.json'
            with self.assertRaisesRegex(self.module.Refused, 'safe esports summary unavailable'):
                self.module.run_esports_matrix(root, root, {'output': str(output), 'fingerprint': {}}, Boundary(),
                    {'ASSISTED_E2E_BASELINE_FILE': str(baseline)}, {}, root / 'runtime.log')
            result = json.loads(output.read_text())
            self.assertFalse(result['ok'])
            self.assertEqual(len(result['entries']), 1)
            self.assertFalse(result['entries'][0]['ok'])
            self.assertTrue(result['entries'][0]['financialAfter'])

    def test_capability_diagnostic_is_one_original_pair_and_fails_closed_without_safe_evidence(self):
        for missing, original_failure in ((False, False), (True, False), (True, True)):
            with self.subTest(missing=missing, original_failure=original_failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cli = root / 'apps/api/app/cli'
                cli.mkdir(parents=True)
                (cli / 'e2e_credentials.json').write_text(json.dumps({key: {} for key in ('admin', 'customer_service')}))
                baseline = root / 'original.json'
                baseline.write_text('immutable')
                calls = []
                module = self.module
                class Boundary:
                    def run(inner, args, calls=calls, missing=missing, original_failure=original_failure, module=module, **kwargs):
                        if args[0] == 'node':
                            config = json.loads(Path(args[-1]).read_text())
                            calls.append(('browser', config['entry'], config['mode']))
                            if not missing:
                                (Path(args[-1]).parent / 'safe-browser.json').write_text('{"ok":true,"diagnosticEvidenceValid":true}')
                            if original_failure:
                                raise module.Refused('original browser failure')
                        elif args[2] == 'app.cli.summarize_esports_assessments_e2e':
                            Path(args[args.index('--output') + 1]).write_text('{"passed":2}')
                        elif args[2] == 'app.cli.seed_esports_assessments_e2e':
                            calls.append(('verify' if kwargs['env'].get('ESPORTS_E2E_VERIFY_MODE') else 'seed', kwargs['env'].get('ESPORTS_E2E_VERIFY_MODE')))
                        else:
                            calls.append(('financial',))
                output = root / 'result.json'
                def run(root=root, output=output, baseline=baseline, module=module, boundary=Boundary):
                    module.run_esports_matrix(root, root, {'check': 'esports-capabilities-diagnostic', 'output': str(output), 'fingerprint': {}}, boundary(),
                        {'ASSISTED_E2E_BASELINE_FILE': str(baseline)}, {}, root / 'runtime.log')
                if missing:
                    with self.assertRaisesRegex(module.Refused, 'original browser failure' if original_failure else 'capability diagnostic evidence unavailable'):
                        run()
                else:
                    run()
                result = json.loads(output.read_text())
                self.assertEqual([c for c in calls if c[0] == 'browser'], [('browser', 3, 'capability-diagnostic')])
                self.assertEqual([c for c in calls if c[0] == 'verify'], [] if missing else [('verify', 'capabilities-desktop')])
                self.assertEqual(len(result['entries']), 1)
                self.assertTrue(result['entries'][0]['financialBefore'])
                self.assertTrue(result['entries'][0]['financialAfter'])
                self.assertFalse(result['matrixPassed'])
                self.assertEqual(result.get('diagnosticPassed'), not missing)
                self.assertNotIn('audio', result)
                self.assertEqual(baseline.read_text(), 'immutable')

    def test_private_otp_bridge_rejects_command_path_recipient_and_wrong_sequence(self):
        self.assertTrue(hasattr(self.module, 'validate_otp_request'))
        import hashlib
        alias = 'dst_' + hashlib.sha256(b'registration-test-destination\0registration-adult-e2e@example.com').hexdigest()[:24]
        request = {'sequence': 1, 'purpose': 'registration', 'challengeId': '00000000-0000-4000-8000-000000000001', 'destinationAlias': alias}
        self.module.validate_otp_request(request, 1)
        for value in ({**request, 'command': 'docker'}, {**request, 'sequence': 2},
                      {**request, 'destinationAlias': 'dst_' + '0' * 24}, {**request, 'challengeId': '../path'},
                      {**request, 'challengeId': 3}, {**request, 'destinationAlias': []}):
            with self.subTest(value=value), self.assertRaises(self.module.Refused):
                self.module.validate_otp_request(value, 1)

    def test_release_barrier_interrupt_reaps_long_lived_browser_process_group(self):
        with tempfile.TemporaryDirectory() as directory:
            pidfile = Path(directory) / 'browser.pid'
            supervisor = self.module.Supervisor()
            def interrupted(): raise KeyboardInterrupt
            try:
                with self.assertRaises(KeyboardInterrupt):
                    supervisor.run([sys.executable, '-c', 'import time;time.sleep(60)'], pidfile=pidfile, on_tick=interrupted)
            finally: supervisor.close()
            with self.assertRaises(ProcessLookupError): os.kill(int(pidfile.read_text()), 0)

    def talent_run(self, fail_at=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        state = root / 'private'
        state.mkdir(mode=0o700)
        source = root / 'source'
        cli = source / 'apps/api/app/cli'
        cli.mkdir(parents=True)
        actors = ('admin', 'customer_service', 'coach', 'guardian', 'student', 'life_teacher', 'unselected')
        (cli / 'e2e_credentials.json').write_text(json.dumps({key: {'identifier': key, 'password': 'private'} for key in actors}))
        calls = []
        module = self.module
        class Boundary:
            def run(inner, args, **kwargs):
                if args[0] == 'node':
                    payload = json.loads(Path(args[-1]).read_text())
                    calls.append(('browser', payload['entry'], payload['mode']))
                    selected = json.loads(Path(payload['credentialsFile']).read_text())
                    self.assertEqual(set(selected), set(actors[:-1]) if payload['entry'] == 0 and payload['mode'] == 'existing' else {'student'})
                    self.assertNotIn('DATABASE_URL', kwargs['env'])
                    if fail_at == (payload['entry'], payload['mode']):
                        raise module.Refused('original browser failure')
                elif args[2] == 'app.cli.seed_talent_e2e':
                    calls.append(('verify' if kwargs['env'].get('TALENT_E2E_VERIFY_MODE') else 'seed',
                                  kwargs['env'].get('TALENT_E2E_VERIFY_MODE'), kwargs['env'].get('TALENT_E2E_LEGACY_V1')))
                elif args[2] == 'app.cli.summarize_talent_playwright':
                    output = Path(args[args.index('--output') + 1])
                    output.write_text(json.dumps({'passed': 1, 'failed': 0, 'skipped': 0, 'flaky': 0, 'retries': 0, 'reasons': []}))
                    calls.append(('summary',))
                elif args[2] == 'app.cli.verify_talent_resources_e2e':
                    calls.append(('resource-verify',))
                else:
                    self.assertEqual(args[-1], '--verify-financial-baseline')
                    calls.append(('financial', kwargs['env']['ASSISTED_E2E_BASELINE_FILE']))
                    if fail_at == 'baseline-change': original.write_text('rewritten')
                    if fail_at == 'post-browser-financial' and any(item[0] == 'browser' for item in calls):
                        raise module.Refused('financial failure')
                return ''
        original = state / 'assisted-baseline.json'
        original.write_text('original immutable baseline')
        payload = {'baseURL': 'http://127.0.0.1:12345', 'fingerprint': {'sourceCommit': 'fixed'}, 'output': str(root / 'talent-games.json'), 'check': 'talent-games'}
        local = {'DATABASE_URL': 'private', 'ASSISTED_E2E_BASELINE_FILE': str(original)}
        return calls, original, lambda: module.run_talent_matrix(source, state, payload, Boundary(), local, {'PATH': '/usr/bin'}, state / 'runtime.log')

    def test_talent_matrix_is_fixed_and_serial_with_original_financial_baseline(self):
        calls, baseline, run = self.talent_run()
        run()
        browsers = [item for item in calls if item[0] == 'browser']
        self.assertEqual(browsers, [('browser', 0, 'existing'), ('browser', 0, 'history')] + [('browser', index, 'existing') for index in range(1, 7)] + [('browser', 0, 'resources')])
        seeds = [item for item in calls if item[0] == 'seed']
        self.assertEqual(seeds, [('seed', None, None)] * 2 + [('seed', None, '1')] * 5 + [('seed', None, None)])
        self.assertEqual([item[1] for item in calls if item[0] == 'verify'], ['double-full', 'double-full', 'full'] + ['dynamic'] * 5)
        for index, item in enumerate(calls):
            if item[0] == 'seed': self.assertEqual(calls[index + 1], ('financial', str(baseline)))
            if item[0] == 'verify': self.assertEqual(calls[index + 1], ('financial', str(baseline)))
        self.assertEqual(baseline.read_text(), 'original immutable baseline')

    def test_talent_browser_failure_financially_checks_before_abort_without_reset(self):
        calls, baseline, run = self.talent_run(fail_at=(1, 'existing'))
        with self.assertRaisesRegex(self.module.Refused, 'original browser failure'): run()
        self.assertEqual(calls[-1], ('financial', str(baseline)))
        self.assertEqual(len([item for item in calls if item[0] == 'seed']), 2)
        self.assertEqual(baseline.read_text(), 'original immutable baseline')

    def test_talent_readonly_resource_failure_never_advances_to_next_seed(self):
        calls, baseline, run = self.talent_run(fail_at=(0, 'history'))
        with self.assertRaisesRegex(self.module.Refused, 'original browser failure'): run()
        self.assertEqual(calls[-1], ('financial', str(baseline)))
        self.assertEqual(len([item for item in calls if item[0] == 'seed']), 1)

    def test_talent_financial_failure_after_browser_prevents_any_next_reset(self):
        calls, _baseline, run = self.talent_run(fail_at='post-browser-financial')
        with self.assertRaisesRegex(self.module.Refused, 'financial failure'): run()
        self.assertEqual(len([item for item in calls if item[0] == 'seed']), 1)

    def test_talent_rewritten_original_baseline_fails_before_browser(self):
        calls, _baseline, run = self.talent_run(fail_at='baseline-change')
        with self.assertRaisesRegex(self.module.Refused, 'original financial baseline changed'): run()
        self.assertEqual([item for item in calls if item[0] == 'browser'], [])

    def test_talent_eighth_resource_dataset_failure_preserves_seven_completed_entries(self):
        calls, baseline, run = self.talent_run(fail_at=(0, 'resources'))
        with self.assertRaisesRegex(self.module.Refused, 'original browser failure'): run()
        self.assertEqual(len([item for item in calls if item[0] == 'seed']), 8)
        self.assertEqual(len([item for item in calls if item[0] == 'browser' and item[2] == 'existing']), 7)
        self.assertEqual(calls[-1], ('financial', str(baseline)))

    def test_talent_assets_require_all_eight_unique_current_build_pairs(self):
        names = ['ReactionGame', 'AimGame', 'SequenceGame', 'NumberGame', 'VerbalGame', 'ChimpGame', 'VisualGame', 'DynamicVisionGame']
        files = [{'path': f'assets/{name}-opaque.{ext}', 'size': 42, 'sha256': 'a' * 64} for name in names for ext in ('js', 'css')]
        manifest = {'release_id': 'current', 'sets': [{'release_id': 'current', 'files': files}]}
        assets = self.module.talent_game_assets(manifest)
        self.assertEqual(set(assets), {'reactiontime', 'aim', 'sequence', 'number-memory', 'verbal-memory', 'chimp', 'memory', 'dynamic-vision'})
        self.assertEqual(assets['reactiontime'][0]['path'], 'assets/ReactionGame-opaque.js')
        for broken in (files[:-1], files + files[:1]):
            with self.assertRaises(self.module.Refused):
                self.module.talent_game_assets({'release_id': 'current', 'sets': [{'release_id': 'current', 'files': broken}]})

    def test_optional_panels_is_fixed_and_preserves_previous_failure(self):
        args = self.module.parse_arguments(['--baseline-source', '/tmp/base', '--candidate-source', '/tmp/candidate',
            '--phase', 'named', '--check', 'optional-panels', '--evidence-dir', '/tmp/evidence'])
        self.assertEqual(args.check, 'optional-panels')
        self.assertEqual(self.module.NAMED_RUNNERS['optional-panels'], 'run-optional-panels.mjs')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'optional-panels.json').write_text('{"ok":false}')
            with self.assertRaises(self.module.Refused):
                self.module.prepare_evidence(root, 'named', check='optional-panels')

    def test_optional_coach_binding_executes_exact_private_code_without_database(self):
        tree = ast.parse(SCRIPT.read_text())
        binding = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == 'selected' for target in node.targets)
                       and isinstance(node.value, ast.Call))
        code = ast.literal_eval(binding.value.args[0].elts[2])
        result = subprocess.run([sys.executable, '-c', code], cwd=self.module.ROOT / 'apps/api',
                                capture_output=True, check=False,
                                env={'PATH': os.environ['PATH'], 'PYTHONPATH': str(self.module.ROOT / 'apps/api')})
        self.assertEqual(result.returncode, 0)
        selected = json.loads(result.stdout)
        self.assertEqual(set(selected), {'identifier', 'password'})
        self.assertRegex(selected['identifier'], r'^19900009\d{3}$')
        self.assertTrue(selected['password'])

    def test_optional_invoice_fixture_has_valid_png_bytes(self):
        from PIL import Image

        source = (self.module.ROOT / 'apps/web/tests/frontend-optional-panels.spec.ts').read_text()
        match = re.search(r"Buffer.from\('([A-Za-z0-9+/=]+)', 'base64'\)", source)
        self.assertIsNotNone(match)
        with Image.open(io.BytesIO(base64.b64decode(match[1]))) as image:
            image.verify()
            self.assertEqual((image.format, image.size), ('PNG', (2, 2)))

    def test_named_mode_accepts_only_fixed_module_recovery_check(self):
        args = self.module.parse_arguments(['--baseline-source', '.', '--candidate-source', '.',
            '--phase', 'named', '--check', 'module-recovery', '--evidence-dir', '/tmp/evidence'])
        self.assertEqual((args.phase, args.check), ('named', 'module-recovery'))
        for tail in (['--phase', 'named'], ['--phase', 'named', '--check', '../arbitrary'],
                     ['--phase', 'baseline', '--check', 'module-recovery']):
            with self.subTest(tail=tail), self.assertRaises(SystemExit):
                self.module.parse_arguments(['--baseline-source', '.', '--candidate-source', '.',
                    '--evidence-dir', '/tmp/evidence', *tail])

    def test_failed_named_evidence_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'module-recovery.json').write_text('{"ok":false}')
            with self.assertRaises(self.module.Refused):
                self.module.prepare_evidence(path, 'named')

    def test_style_order_is_a_fixed_named_check_and_preserves_failed_evidence(self):
        args = self.module.parse_arguments(['--baseline-source', '.', '--candidate-source', '.',
            '--phase', 'named', '--check', 'style-order', '--evidence-dir', '/tmp/evidence'])
        self.assertEqual(args.check, 'style-order')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'style-order.json').write_text('{"ok":false}')
            with self.assertRaises(self.module.Refused):
                self.module.prepare_evidence(path, 'named', check='style-order')

    def test_route_loading_is_fixed_and_preserves_failed_evidence(self):
        args = self.module.parse_arguments(['--baseline-source', '.', '--candidate-source', '.',
            '--phase', 'named', '--check', 'route-loading', '--evidence-dir', '/tmp/evidence'])
        self.assertEqual(args.check, 'route-loading')
        self.assertEqual(self.module.NAMED_RUNNERS['route-loading'], 'run-route-loading.mjs')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            (path / 'route-loading.json').write_text('{"ok":false}')
            with self.assertRaises(self.module.Refused):
                self.module.prepare_evidence(path, 'named', check='route-loading')

    def test_build_diagnostics_are_fixed_categories_not_private_log_lines(self):
        raw = ('failed to solve: failed to resolve source metadata for private-registry/secret-image\n'
               'error TS2345: private source path and token\n'
               'context colima: context does not exist: private path\n')
        self.assertEqual(self.module.diagnostic_categories(raw),
            ['build_context_unavailable', 'build_image_metadata', 'build_solver', 'typescript_TS2345'])

    def test_owned_build_uses_fixed_local_tag_nonpull_and_checks_image_identity(self):
        expected = 'sha256:' + 'a' * 64
        calls = []
        def docker(*args, capture=False):
            self.assertEqual(args, ('image', 'inspect', 'esports-erp-e2e-api:latest', '--format', '{{.Id}}'))
            self.assertTrue(capture)
            calls.append('inspect')
            return expected
        def compose(*args):
            self.assertEqual(args, ('build', '--pull=false', '--build-arg',
                'ERP_DEPENDENCY_IMAGE=esports-erp-e2e-api:latest', 'api', 'web'))
            calls.append('build')
        self.module.build_owned_services(compose, docker, expected)
        self.assertEqual(calls, ['inspect', 'build', 'inspect'])

    def test_missing_or_changed_dependency_tag_refuses_before_service_creation(self):
        expected = 'sha256:' + 'a' * 64
        for observations in ([''], ['sha256:' + 'b' * 64], [expected, ''], [expected, 'sha256:' + 'b' * 64]):
            with self.subTest(observations=observations):
                replies = iter(observations)
                built = []
                def docker(*_args, replies=replies, **_kwargs):
                    return next(replies)
                with self.assertRaises(self.module.Refused):
                    self.module.build_owned_services(lambda *_args, built=built: built.append('built'), docker, expected)
                self.assertEqual(len(built), len(observations) - 1)

    def setUp(self):
        if not SCRIPT.exists() and self._testMethodName != 'test_implementation_exists':
            self.skipTest('implementation missing')

    def test_non_loopback_and_inherited_production_environment_refused(self):
        for env in ({'ERP_BIND_ADDRESS': '0.0.0.0'}, {'DATABASE_URL': 'postgres://production/db'},
                    {'APP_ENV': 'production'}, {'SMTP_HOST': 'external'}, {'VITE_API_URL': 'remote'},
                    {'COMPOSE_PROJECT_NAME': 'existing'}, {'NODE_OPTIONS': '--inspect'}):
            with self.subTest(keys=list(env)), self.assertRaises(self.module.Refused):
                self.module.validate_environment(env)

    def test_dotenv_refused_without_reading_fifo_or_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'apps/web').mkdir(parents=True)
            os.mkfifo(root / 'apps/web/.env.production')
            with self.assertRaises(self.module.Refused):
                self.module.validate_dotenv(root)

    def test_generated_secrets_satisfy_real_seed_settings_without_inheritance(self):
        sys.path.insert(0, str(SCRIPT.parents[1] / 'apps/api'))
        from app.core.config import Settings
        values = {key.lower(): value for key, value in self.module.create_runtime_secrets().items()}
        try:
            settings = Settings(app_env='test', database_url='postgresql+psycopg://isolated@127.0.0.1:55499/erp_e2e', **values)
        except ValueError:
            self.fail('generated runtime configuration must satisfy required seed settings')
        settings.validate_runtime_secrets()

    def test_wrong_baseline_sha_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            with self.assertRaises(self.module.Refused):
                self.module.verify_source(root, baseline=True)

    def test_existing_failed_evidence_cannot_be_overwritten_by_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            prior = root / 'baseline.json'
            prior.write_text('{"runs":[{"ok":false}]}')
            with self.assertRaises(self.module.Refused):
                self.module.prepare_evidence(root, 'baseline')
            self.assertEqual(prior.read_text(), '{"runs":[{"ok":false}]}')

    def test_private_archive_extraction_keeps_source_traversable_for_runtime_uid(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'source.tar'
            with tarfile.open(archive, 'w') as output:
                info = tarfile.TarInfo('app/api/router.py')
                info.size = 4
                info.mode = 0o644
                output.addfile(info, io.BytesIO(b'test'))
            source = root / 'source'
            source.mkdir()
            old = os.umask(0o077)
            try:
                self.module.extract_snapshot(archive, source)
            finally:
                os.umask(old)
            self.assertEqual((source / 'app/api').stat().st_mode & 0o777, 0o755)
            self.assertEqual((source / 'app/api/router.py').read_bytes(), b'test')

    def test_unknown_selector_refused_before_services(self):
        result = subprocess.run([sys.executable, str(SCRIPT), '--suite', 'arbitrary'],
                                capture_output=True, env={'PATH': os.environ['PATH']}, check=False)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(b'creating', result.stdout.lower())

    def test_existing_foreign_container_prevents_start(self):
        containers = [{'Id': 'foreign', 'Config': {'Labels': {'com.docker.compose.project': 'other'}}}]
        with self.assertRaises(self.module.Refused):
            self.module.validate_ownership(containers, 'loading-owned', 'nonce', set())

    def test_cleanup_requires_exact_ids_and_owner_label(self):
        owned = {'Id': 'exact', 'Config': {'Labels': {
            'com.docker.compose.project': 'loading-owned', 'erp.loading.owner': 'nonce'}}}
        self.module.validate_ownership([owned], 'loading-owned', 'nonce', {'exact'})
        for value in ({**owned, 'Id': 'new'}, {**owned, 'Config': {'Labels': {}}}):
            with self.assertRaises(self.module.Refused):
                self.module.validate_ownership([value], 'loading-owned', 'nonce', {'exact'})

    def test_partial_create_failure_and_interruption_cleanup_preserve_original_exception(self):
        for original in (self.module.Refused('partial create failed'), KeyboardInterrupt()):
            with self.subTest(kind=type(original).__name__):
                model = DockerModel(self.module)
                supervisor = self.module.Supervisor()
                masks = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
                try:
                    with self.assertRaises(type(original)) as caught, self.module.owned_cleanup_scope(
                            model, model.containers, 'loading-owned', 'nonce', set(), supervisor):
                        model.rows.append(model.owned())
                        raise original
                    self.assertIs(caught.exception, original)
                    self.assertEqual(model.rows, [])
                    self.assertEqual(model.volumes, set())
                    self.assertEqual(model.networks, set())
                finally:
                    for sig, handler in masks.items():
                        signal.signal(sig, handler)

    def test_secondary_cleanup_failure_does_not_mask_primary(self):
        model = DockerModel(self.module)
        model.rows.append(model.owned())
        model.fail_remove = True
        original = self.module.Refused('original create failure')
        masks = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        warning = io.StringIO()
        try:
            with redirect_stderr(warning), self.assertRaises(self.module.Refused) as caught, self.module.owned_cleanup_scope(
                    model, model.containers, 'loading-owned', 'nonce', {'partial-exact'}, self.module.Supervisor()):
                raise original
            self.assertIs(caught.exception, original)
            self.assertEqual(warning.getvalue(), 'Loading cleanup failed; original failure preserved; owned resources require inspection\n')
        finally:
            for sig, handler in masks.items():
                signal.signal(sig, handler)

    def test_cleanup_removes_verified_partial_ids_but_never_foreign_container(self):
        model = DockerModel(self.module)
        foreign = model.owned('foreign')
        foreign['Config']['Labels']['erp.loading.owner'] = 'another-owner'
        model.rows = [model.owned(), foreign]
        with self.assertRaises(self.module.Refused):
            self.module.cleanup_owned_resources(model, model.containers, 'loading-owned', 'nonce', set())
        self.assertEqual(model.rows, [foreign])

    def test_cleanup_failure_after_success_is_not_suppressed(self):
        model = DockerModel(self.module)
        model.rows.append(model.owned())
        model.fail_remove = True
        masks = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            with self.assertRaisesRegex(self.module.Refused, 'cleanup failure'), self.module.owned_cleanup_scope(
                    model, model.containers, 'loading-owned', 'nonce', set(), self.module.Supervisor()):
                pass
        finally:
            for sig, handler in masks.items():
                signal.signal(sig, handler)

    def test_partial_baseline_refused_before_any_candidate_collection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'baseline.json').write_text(json.dumps({'fingerprint': {'sourceCommit': self.module.BASELINE_SHA},
                'summaries': {}, 'runs': [], 'collectorErrors': []}))
            class Probe(self.module.Supervisor):
                def run(inner, args, **kwargs):
                    if args[0] == 'docker':
                        return 'sha256:fixture-proxy'
                    if '--preflight-baseline' in args:
                        return super().run(args, **kwargs)
                    return ''
            entered = []
            with patch.object(self.module, 'collect', side_effect=lambda *_args: entered.append('candidate')), self.assertRaises(self.module.Refused):
                self.module.collect_candidate(root, 'candidate-sha', root, root, Probe(),
                    {'PATH': os.environ['PATH'], 'HOME': str(Path.home())}, str(Path.home() / 'Library/Caches/ms-playwright'))
            self.assertEqual(entered, [])

    def test_supervisor_reaps_owned_child_on_interrupt(self):
        with tempfile.TemporaryDirectory() as temporary:
            pidfile = Path(temporary) / 'child'
            code = ("import importlib.util,sys,time,signal; "
                    f"s=importlib.util.spec_from_file_location('launcher',{str(SCRIPT)!r}); "
                    "m=importlib.util.module_from_spec(s);s.loader.exec_module(m); "
                    "p=m.Supervisor();signal.signal(signal.SIGTERM,lambda *a:(_ for _ in ()).throw(KeyboardInterrupt())); "
                    "\ntry:\n p.run([sys.executable,'-c','import time;time.sleep(60)'], "
                    f"pidfile={str(pidfile)!r})\nfinally:\n p.close()")
            parent = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                for _ in range(100):
                    if pidfile.exists():
                        break
                    time.sleep(0.02)
                self.assertTrue(pidfile.exists())
                child = int(pidfile.read_text())
                parent.send_signal(signal.SIGTERM)
                parent.wait(timeout=10)
                with self.assertRaises(ProcessLookupError):
                    os.kill(child, 0)
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait()

    def test_exited_command_does_not_leave_live_descendants(self):
        with tempfile.TemporaryDirectory() as temporary:
            pidfile = Path(temporary) / 'grandchild'
            code = ("import subprocess,sys;from pathlib import Path;"
                    "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],"
                    "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
                    f"Path({str(pidfile)!r}).write_text(str(p.pid))")
            supervisor = self.module.Supervisor()
            descendant = None
            try:
                supervisor.run([sys.executable, '-c', code])
                descendant = int(pidfile.read_text())
                supervisor.close()
                for _ in range(50):
                    state = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', str(descendant)], capture_output=True, text=True, check=False).stdout.strip()
                    if not state or state.startswith('Z'):
                        break
                    time.sleep(.02)
                self.assertTrue(not state or state.startswith('Z'), 'owned descendants must not remain running')
            finally:
                supervisor.close()
                if descendant:
                    try:
                        os.kill(descendant, signal.SIGKILL)
                    except ProcessLookupError:
                        pass


if __name__ == '__main__':
    unittest.main(verbosity=2)
