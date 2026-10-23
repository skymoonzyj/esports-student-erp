"""NAS admission tests: real filesystem, strict shared validators, no NAS access."""
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('nas-deploy-web-assets.py')


class AdmissionTests(unittest.TestCase):
    def test_real_compose_uses_source_identity_not_current_candidate_environment(self):
        compose = shutil.which('docker-compose')
        if not compose:
            self.skipTest('Compose client unavailable')
        spec = importlib.util.spec_from_file_location('nas_guard', SCRIPT.with_name('nas-deploy-guard.py'))
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            source, bin_dir = base / 'releases/prior', base / 'bin'
            source.mkdir(parents=True)
            bin_dir.mkdir()
            (source / '.env').write_text('ERP_RELEASE_ID=stale-env-identity\nEVALUATION_MEDIA_STORE_ID=prior-public-store\n')
            (source / '.release-metadata').write_text('source_release_id=prior\n')
            (source / 'compose.yaml').write_text('services:\n  api:\n    image: erp-api:${ERP_RELEASE_ID:-legacy}\n    environment:\n      EVALUATION_MEDIA_STORE_ID: ${EVALUATION_MEDIA_STORE_ID:-}\n      ERP_WEB_ASSETS_PROTOCOL: ${ERP_WEB_ASSETS_PROTOCOL:-legacy}\n      ERP_WEB_BUILD_ID: ${ERP_WEB_BUILD_ID:-legacy}\n      ERP_WEB_ASSET_INPUT_SHA256: ${ERP_WEB_ASSET_INPUT_SHA256:-legacy}\n')
            for name in ('compose.prod.yaml', 'compose.nas-staging.yaml', 'compose.nas-lan-staging.yaml'):
                (source / name).write_text('services: {}\n')
            wrapper = bin_dir / 'docker'
            wrapper.write_text('#!/bin/sh\n[ "$1" = compose ] || exit 91\nshift\nexec "$TEST_COMPOSE" "$@"\n')
            wrapper.chmod(0o755)
            with patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                    'TEST_COMPOSE': compose, 'ERP_RELEASE_ID': 'candidate',
                    'EVALUATION_MEDIA_STORE_ID': 'candidate-public-store', 'ERP_WEB_ASSETS_PROTOCOL': '1',
                    'ERP_WEB_BUILD_ID': 'candidate', 'ERP_WEB_ASSET_INPUT_SHA256': 'f' * 64}):
                result = guard.config(source, 'erp', historical=True)
            self.assertEqual(result['services']['api']['image'], 'erp-api:prior')
            self.assertEqual(result['services']['api']['environment']['EVALUATION_MEDIA_STORE_ID'], 'prior-public-store')
            for key in ('ERP_WEB_ASSETS_PROTOCOL', 'ERP_WEB_BUILD_ID', 'ERP_WEB_ASSET_INPUT_SHA256'):
                self.assertEqual(result['services']['api']['environment'][key], 'legacy')
            value = {'source_release_id': 'prior', 'web_assets_contract': '1', 'web_assets_status': 'admitted',
                'source_tree_sha256': 'a' * 64, 'web_assets_manifest_sha256': 'b' * 64,
                'web_assets_input_sha256': 'c' * 64, 'web_admitted_config_sha256': 'd' * 64,
                'web_image_id': 'sha256:' + '2' * 64, 'web_predecessor_image_id': 'sha256:' + '1' * 64,
                'web_admitted_runtime_images': json.dumps({name: 'sha256:' + '2' * 64 for name in
                    ('db', 'api', 'scheduler', 'web', 'proxy', 'caddy')})}
            (source / '.release-metadata').write_text(''.join(f'{key}={item}\n' for key, item in value.items()))
            with patch.dict(os.environ, {'PATH': str(bin_dir) + os.pathsep + os.environ['PATH'],
                    'TEST_COMPOSE': compose, 'ERP_RELEASE_ID': 'candidate',
                    'EVALUATION_MEDIA_STORE_ID': 'candidate-public-store', 'ERP_WEB_ASSETS_PROTOCOL': '1',
                    'ERP_WEB_BUILD_ID': 'candidate', 'ERP_WEB_ASSET_INPUT_SHA256': 'f' * 64}):
                result = guard.config(source, 'erp', historical=True)
            env = result['services']['api']['environment']
            self.assertEqual(result['services']['api']['image'], 'erp-api:prior')
            self.assertEqual(env['EVALUATION_MEDIA_STORE_ID'], 'prior-public-store')
            self.assertEqual(env['ERP_WEB_ASSETS_PROTOCOL'], '1')
            self.assertEqual(env['ERP_WEB_BUILD_ID'], 'prior')
            self.assertEqual(env['ERP_WEB_ASSET_INPUT_SHA256'], 'c' * 64)

    def test_carried_runtime_is_bounded_exact_service_records_and_safe_paths(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'carried_runtime'), 'bounded observed runtime continuity required')
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory).resolve()
            release = app / 'releases/known'
            release.mkdir(parents=True)
            choice = {'release': str(release), 'config_sha256': 'a' * 64, 'image_id': 'sha256:' + '2' * 64}
            choices = {name: dict(choice) for name in ('db', 'api', 'scheduler', 'web', 'proxy', 'caddy')}
            value = {'web_admitted_previous_runtime': json.dumps(choices)}
            self.assertEqual(module.carried_runtime(value, app), choices)
            self.assertEqual(module.carried_runtime({}, app), {})
            for replacement in ([], {**choices, 'extra': choice}, {'web': choice},
                    {**choices, 'web': {**choice, 'release': str(app / 'outside')}},
                    {**choices, 'web': {**choice, 'image_id': 'mutable:tag'}},
                    {**choices, 'web': {**choice, 'config_sha256': 'invalid'}}):
                with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                    module.carried_runtime({'web_admitted_previous_runtime': json.dumps(replacement)}, app)

    def test_guard_never_overwrites_checkpoint_with_unbound_predecessor(self):
        import hashlib
        spec = importlib.util.spec_from_file_location('nas_guard', SCRIPT.with_name('nas-deploy-guard.py'))
        guard = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(guard)
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory).resolve()
            old = app / 'releases/old'
            old.mkdir(parents=True)
            raw = b'source_release_id=old\nsource_alembic_head=head\n'
            (old / '.release-metadata').write_bytes(raw)
            pre = {'release': str(old), 'release_id': 'old', 'metadata_sha256': hashlib.sha256(raw).hexdigest(),
                'image_id': 'sha256:' + '1' * 64, 'protocol': 0, 'manifest_sha256': None,
                'legacy_files': [{'path': 'assets/a.js', 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}]}
            authority = {'mode': 'normal', 'configuration_release': str(old), 'authority': {
                'config_sha256': 'a' * 64, 'images': {name: 'sha256:' + '1' * 64 for name in
                    ('db', 'api', 'scheduler', 'web', 'proxy', 'caddy')}}}
            path = app / '.web-last-successful.json'
            original = json.dumps({'schema_version': 1, 'predecessor': pre, 'authority': authority['authority']}).encode()
            path.write_bytes(original)
            pre['metadata_sha256'] = '0' * 64
            pre_file, auth_file = app / 'prepare.json', app / 'authority.json'
            pre_file.write_text(json.dumps({'checkpoint_predecessor': pre}))
            auth_file.write_text(json.dumps(authority))
            argv = ['guard', 'record-predecessor', str(app), str(old), 'erp', str(pre_file), str(auth_file)]
            with (patch.object(guard, 'old_authority', return_value=authority),
                  patch.object(guard.sys, 'argv', argv), self.assertRaises(ValueError)):
                guard.main()
            self.assertEqual(path.read_bytes(), original)

    def test_admission_is_not_a_success_receipt_or_verified_repair_identity(self):
        module = self.module()
        value = {'source_release_id': 'failed', 'web_assets_contract': '1', 'web_assets_status': 'admitted',
            'source_tree_sha256': 'a' * 64, 'web_assets_manifest_sha256': 'b' * 64,
            'web_assets_input_sha256': 'c' * 64, 'web_admitted_config_sha256': 'd' * 64,
            'web_image_id': 'sha256:' + '2' * 64, 'web_predecessor_image_id': 'sha256:' + '1' * 64,
            'web_admitted_runtime_images': json.dumps({name: 'sha256:' + '2' * 64 for name in
                ('db', 'api', 'scheduler', 'web', 'proxy', 'caddy')})}
        self.assertEqual(module.admission(value, 'failed')['web'], 'sha256:' + '2' * 64)
        with self.assertRaises(ValueError):
            module.receipt(value, 'failed')
        with self.assertRaises(ValueError):
            module.admission({**value, 'web_assets_status': 'verified'}, 'failed')

    def test_missing_or_tampered_last_success_checkpoint_is_refused(self):
        import hashlib
        module = self.module()
        self.assertTrue(hasattr(module, 'read_checkpoint'), 'authenticated predecessor checkpoint required')
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory).resolve()
            old = app / 'releases/old'
            old.mkdir(parents=True)
            raw = b'source_release_id=old\nsource_alembic_head=head\n'
            (old / '.release-metadata').write_bytes(raw)
            checkpoint = {'schema_version': 1, 'predecessor': {'release': str(old), 'release_id': 'old',
                'metadata_sha256': hashlib.sha256(raw).hexdigest(), 'image_id': 'sha256:' + '1' * 64,
                'protocol': 0, 'manifest_sha256': None, 'legacy_files': [{'path': 'assets/old.js', 'size': 1,
                    'sha256': hashlib.sha256(b'x').hexdigest()}]},
                'authority': {'config_sha256': 'a' * 64, 'images': {name: 'sha256:' + '1' * 64
                    for name in ('db', 'api', 'scheduler', 'web', 'proxy', 'caddy')}}}
            path = app / '.web-last-successful.json'
            with self.assertRaises(ValueError):
                module.read_checkpoint(app)
            path.write_text(json.dumps(checkpoint))
            self.assertEqual(module.read_checkpoint(app), checkpoint)
            (old / '.release-metadata').write_bytes(raw + b'web_assets_status=failed\n')
            with self.assertRaises(ValueError):
                module.read_checkpoint(app)
            (old / '.release-metadata').write_bytes(raw)
            checkpoint['predecessor']['image_id'] = 'mutable:tag'
            path.write_text(json.dumps(checkpoint))
            with self.assertRaises(ValueError):
                module.read_checkpoint(app)

    def test_source_contract_refuses_unknown_protocol_or_duplicate_keys(self):
        module = self.module()
        self.assertTrue(hasattr(module, 'validate_source_contract'), 'source protocol admission required')
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'scripts').mkdir()
            path = root / 'scripts/web-assets-contract.json'
            for raw in ('{"schema_version":1,"required_protocol":2}',
                        '{"schema_version":true,"required_protocol":true}',
                        '{"schema_version":1,"required_protocol":1,"required_protocol":2}', '{}'):
                path.write_text(raw)
                with self.subTest(raw=raw), self.assertRaises(ValueError):
                    module.validate_source_contract(root)
    def module(self):
        self.assertTrue(SCRIPT.exists(), 'NAS immutable asset admission helper required')
        spec = importlib.util.spec_from_file_location('nas_assets', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_metadata_requires_unique_keys_and_no_symlink(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.release-metadata'
            path.write_text('web_assets_contract=1\nweb_assets_contract=0\n')
            with self.assertRaises(ValueError):
                module.metadata(path)
            path.unlink()
            path.symlink_to('/not-read')
            with self.assertRaises(ValueError):
                module.metadata(path)

    def test_prepare_rejects_unverified_active_before_docker(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as root:
            app = Path(root)
            (app / '.active-release').write_text(str(app / 'outside') + '\n')
            with self.assertRaises(ValueError):
                module.prepare(app, 'candidate', app / 'scratch')
            self.assertFalse((app / 'scratch').exists())

    def test_receipt_conversion_preserves_original_key_value_metadata(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / '.release-metadata'
            value = ('source_release_id=old\nweb_assets_contract=1\nweb_assets_status=verified\n'
                     'web_assets_input_sha256=' + 'd' * 64 + '\nweb_predecessor_image_id=sha256:' + 'e' * 64 + '\n'
                     'source_tree_sha256=' + 'a' * 64 + '\nweb_image_id=sha256:' + 'b' * 64 +
                     '\nweb_assets_manifest_sha256=' + 'c' * 64 +
                     '\nactivated_verified_utc=2026-10-18T00:00:00Z\n')
            path.write_text(value)
            result = module.receipt(module.metadata(path), 'old')
            self.assertEqual(result['release_id'], 'old')
            self.assertEqual(result['schema_version'], 1)
            self.assertEqual(path.read_text(), value)
            self.assertNotIn('web_assets_contract', json.dumps(result))
            for key, bad in (('web_assets_status', 'preparing'), ('web_image_id', 'mutable:tag'),
                             ('source_tree_sha256', 'bad'), ('source_release_id', 'other'),
                             ('web_assets_input_sha256', 'bad'), ('activated_verified_utc', '2099-01-01T00:00:00Z')):
                with self.subTest(key=key), self.assertRaises(ValueError):
                    module.receipt({**module.metadata(path), key: bad}, 'old')

    def test_unowned_container_is_never_read_or_removed(self):
        module = self.module()
        image, cid = 'sha256:' + 'a' * 64, 'b' * 64
        commands = []
        def fake(*args):
            commands.append(args)
            if args[:2] == ('image', 'inspect'):
                return json.dumps([{'Id': image}]).encode()
            if args[0] == 'create':
                return cid.encode()
            if args[0] == 'inspect':
                return json.dumps([{'Id': cid, 'Image': image, 'Config': {'Labels': {}},
                                    'Mounts': [], 'HostConfig': {'NetworkMode': 'none'}}]).encode()
            raise AssertionError('Unowned resource must not be read or deleted')
        with (patch.object(module, 'docker', fake), self.assertRaises(ValueError),
              module.owned_container(image)):
            self.fail('unowned container admitted')
        self.assertFalse(any(args[0] in ('cp', 'rm') for args in commands))

    def test_manifest_archive_rejects_extension_before_payload(self):
        import io
        import tarfile
        from contextlib import contextmanager
        module = self.module()
        member = tarfile.TarInfo('assets-manifest.json')
        member.type = tarfile.XHDTYPE
        member.size = module.assets.MAX_MANIFEST_BYTES
        stream = io.BytesIO(member.tobuf(format=tarfile.USTAR_FORMAT))
        @contextmanager
        def fake(*args):
            yield stream
        with patch.object(module, 'cp_stream', fake), self.assertRaises(ValueError):
            module.image_manifest('not-used', lambda: None)
        self.assertEqual(stream.tell(), 512)


if __name__ == '__main__':
    unittest.main()
