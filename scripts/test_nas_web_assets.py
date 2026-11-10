"""Release assets: immutable bytes, bounded retention, untrusted archive rejection."""
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from argparse import Namespace
from contextlib import redirect_stderr
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).with_name('nas-web-assets.py')
spec = importlib.util.spec_from_file_location('nas_web_assets', SCRIPT)
assets = importlib.util.module_from_spec(spec) if SCRIPT.exists() else None
if assets:
    spec.loader.exec_module(assets)
NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)


def archive(entries):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w') as tar:
        for name, value in entries:
            item = tarfile.TarInfo(name)
            if isinstance(value, bytes):
                item.size = len(value)
                tar.addfile(item, io.BytesIO(value))
            else:
                item.type = value[0]
                item.linkname = '../outside'
                tar.addfile(item)
    stream.seek(0)
    return stream


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(assets, 'static asset preservation helper is missing')

    def test_new_and_previous_chunks_coexist_without_replacing_new_bytes(self):
        merged, manifest = assets.merge_assets({'new-abcd.js': b'new'}, [
            ({'old-abcd.js': b'old'}, None, '2026-11-01T12:00:00Z')], NOW)
        self.assertEqual(merged, {'new-abcd.js': b'new', 'old-abcd.js': b'old'})
        self.assertEqual(manifest['files']['new-abcd.js']['expires_utc'], None)
        self.assertEqual(manifest['files']['old-abcd.js']['expires_utc'], '2026-11-01T12:00:00Z')

    def test_multiple_generations_do_not_extend_retired_expiry(self):
        _, prior = assets.merge_assets({'b.js': b'b'}, [
            ({'a.js': b'a'}, None, '2026-10-26T12:00:00Z')], NOW)
        merged, manifest = assets.merge_assets({'c.js': b'c'}, [
            ({'a.js': b'a', 'b.js': b'b'}, prior, '2026-11-01T12:00:00Z')], NOW)
        self.assertEqual(set(merged), {'a.js', 'b.js', 'c.js'})
        self.assertEqual(manifest['files']['a.js']['expires_utc'], '2026-10-26T12:00:00Z')
        self.assertEqual(manifest['files']['b.js']['expires_utc'], '2026-11-01T12:00:00Z')
        merged, _ = assets.merge_assets({'c.js': b'c'}, [
            ({'a.js': b'a', 'b.js': b'b'}, prior, '2026-11-01T12:00:00Z')], datetime(2026, 10, 27, tzinfo=timezone.utc))
        self.assertNotIn('a.js', merged)

    def test_identical_file_deduplicates_but_collision_blocks(self):
        merged, _ = assets.merge_assets({'a.js': b'a'}, [({'a.js': b'a'}, None, '2026-11-01T12:00:00Z')], NOW)
        self.assertEqual(merged, {'a.js': b'a'})
        with self.assertRaisesRegex(ValueError, 'conflict'):
            assets.merge_assets({'a.js': b'new'}, [({'a.js': b'old'}, None, '2026-11-01T12:00:00Z')], NOW)

    def test_capacity_and_file_count_block_instead_of_evicting(self):
        for limits in ({'max_bytes': 1}, {'max_files': 1}):
            with self.subTest(limits=limits), self.assertRaisesRegex(ValueError, 'capacity'):
                assets.merge_assets({'a.js': b'a'}, [({'b.js': b'b'}, None, '2026-11-01T12:00:00Z')], NOW, **limits)

    def test_union_overflow_stops_before_consuming_later_asset_groups(self):
        visited = []
        def sources():
            visited.append('first')
            yield {'b.js': b'b'}, None, '2026-11-01T12:00:00Z'
            visited.append('later')
            yield {'c.js': b'c'}, None, '2026-11-01T12:00:00Z'
        with self.assertRaisesRegex(ValueError, 'capacity'):
            assets.merge_assets({'a.js': b'a'}, sources(), NOW, max_bytes=1)
        self.assertEqual(visited, ['first'])

    def test_invalid_manifest_hash_missing_file_and_duplicate_json_block(self):
        _, manifest = assets.merge_assets({'a.js': b'a'}, [], NOW)
        for files in ({'a.js': b'changed'}, {}):
            with self.subTest(files=files), self.assertRaises(ValueError):
                assets.merge_assets({}, [(files, manifest, '2026-11-01T12:00:00Z')], NOW)
        with self.assertRaises(ValueError):
            assets.parse_manifest(b'{"version":1,"version":1,"files":{}}')

    def test_tar_regular_files_and_nested_paths_are_read(self):
        self.assertEqual(assets.read_assets_tar(archive([('./a.js', b'a'), ('sub/b.css', b'b')])), {'a.js': b'a', 'sub/b.css': b'b'})

    def test_tar_paths_links_duplicates_and_special_files_block(self):
        for entries in ([('../a.js', b'a')], [('/a.js', b'a')], [('a\\b', b'a')],
                        [('a', (tarfile.SYMTYPE,))], [('a', (tarfile.LNKTYPE,))], [('a', (tarfile.FIFOTYPE,))],
                        [('a.js', b'a'), ('a.js', b'a')]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                assets.read_assets_tar(archive(entries))

    def test_non_utc_future_deadlines_and_bad_manifest_shape_are_rejected(self):
        for expiry in ('2026-11-02T12:00:00Z', '2026-11-01T20:00:00+08:00'):
            with self.subTest(expiry=expiry), self.assertRaises(ValueError):
                assets.merge_assets({}, [({'a.js': b'a'}, None, expiry)], NOW)
        for raw in (b'[]', b'{"version":true,"files":{}}', b'{"version":1,"files":{},"extra":0}'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                assets.parse_manifest(raw)

    def test_archive_truncation_and_pax_extended_paths_are_rejected(self):
        raw = archive([('a.js', b'a' * 1024)]).getvalue()[:600]
        with self.assertRaises(ValueError):
            assets.read_assets_tar(io.BytesIO(raw))
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w') as tar:
            member = tarfile.TarInfo('a.js')
            member.pax_headers = {'path': 'safe.js'}
            tar.addfile(member)
        stream.seek(0)
        with self.assertRaises(ValueError):
            assets.read_assets_tar(stream)

    def test_sparse_tar_members_are_not_regular_asset_files(self):
        with self.assertRaises(ValueError):
            assets.read_assets_tar(archive([('a.js', (tarfile.GNUTYPE_SPARSE,))]))

    def test_cleanup_removes_only_uuid_resources_owned_by_this_helper(self):
        docker = assets.Docker()
        with patch.object(docker, 'call', side_effect=ValueError('create failed')), self.assertRaises(ValueError):
            docker.export({'Id': 'sha256:' + 'a' * 64})
        self.assertEqual(len(docker.containers), 1)
        self.assertRegex(docker.containers[0], r'^erp-assets-[a-f0-9]{32}$')
        calls = []
        def remove(command, **kwargs):
            calls.append(command)
            return Namespace(returncode=0)
        with patch.object(assets.subprocess, 'run', side_effect=remove):
            docker.cleanup()
        self.assertEqual(calls, [['docker', 'container', 'rm', docker.containers[0]]])

    def test_export_container_never_pulls_an_image_or_starts_application(self):
        docker = assets.Docker()
        calls = []
        def stop_after_create(*command):
            calls.append(command)
            raise ValueError('stop at Docker boundary')
        with patch.object(docker, 'call', side_effect=stop_after_create), self.assertRaises(ValueError):
            docker.export({'Id': 'sha256:' + 'a' * 64})
        self.assertEqual(calls[0][0], 'create')
        self.assertIn('--pull=never', calls[0])
        self.assertIn('--read-only', calls[0])
        self.assertIn('--entrypoint', calls[0])
        self.assertIn('/bin/false', calls[0])
        self.assertIn('none', calls[0])

    def test_tar_limits_are_enforced_before_reading_content(self):
        with self.assertRaisesRegex(ValueError, 'capacity'):
            assets.read_assets_tar(archive([('a.js', b'ab')]), max_bytes=1)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            assets.read_assets_tar(archive([('a.js', b'a'), ('b.js', b'b')]), max_files=1)


class FakeDocker:
    """Docker boundary only; real merge, metadata and build context are exercised."""
    def __init__(self, fault=''):
        self.fault = fault
        self.tags = []
        self.cleaned = False
        self.published = False
        self.output = None
        self.exported = []
        self.tagged = []
        self.ids = {'p-web:new': 'sha256:' + 'a' * 64, 'p-web:old': 'sha256:' + 'b' * 64,
                    'p-web:history': 'sha256:' + 'c' * 64}

    def inspect_image(self, ref):
        image_id = self.ids.get(ref, ref)
        if self.fault == 'wrong_base_tag' and ref.startswith('localhost/erp-web-assets-base:'):
            image_id = 'sha256:' + 'e' * 64
        marker = {assets.LABEL: '1'} if image_id == 'sha256:' + 'd' * 64 else {}
        return {'Id': image_id, 'Config': {'Labels': marker}}

    def call(self, *args):
        if args[0] == 'inspect':
            return json.dumps([{'Image': self.ids['p-web:old'], 'Config': {'Image': 'p-web:old', 'Labels': {
                'com.docker.compose.project': 'other' if self.fault == 'wrong_project' else 'p',
                'com.docker.compose.service': 'web'}}}])
        if args[0] == 'build':
            if self.fault == 'build_failed':
                raise ValueError('build failed')
            root = Path(args[-1])
            self.output = ({str(path.relative_to(root / 'assets')): path.read_bytes() for path in (root / 'assets').rglob('*') if path.is_file()},
                           json.loads((root / 'manifest.json').read_text()), b'NEW INDEX')
            if self.fault == 'changed_index':
                self.output = (*self.output[:2], b'OLD INDEX')
            self.ids[args[-2]] = 'sha256:' + 'd' * 64
            self.build_options = args
            if self.fault == 'base_changed_during_build':
                for ref in self.ids:
                    if ref.startswith('localhost/erp-web-assets-base:'):
                        self.ids[ref] = 'sha256:' + 'e' * 64
            self.dockerfile = (root / 'Dockerfile').read_text()
            return ''
        if args[0] == 'tag':
            self.ids[args[2]] = args[1]
            self.tagged.append((args[1], args[2]))
            if args[2] == 'p-web:new':
                self.published = True
            return ''
        raise AssertionError(args)

    def export(self, image):
        self.exported.append(image['Id'])
        if image['Id'] == 'sha256:' + 'd' * 64:
            return self.output
        if image['Id'] == self.ids['p-web:new']:
            return {'new.js': b'new'}, None, b'NEW INDEX'
        return {'old.js': b'old'}, None, b'OLD INDEX'

    def cleanup(self):
        self.cleaned = True


class ImageWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(assets, 'static asset preservation helper is missing')
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for release in ('old', 'new', 'history'):
            path = self.root / 'releases' / release
            path.mkdir(parents=True)
            (path / '.release-metadata').write_text(f'source_release_id={release}\nprepared_utc=2026-10-25T10:00:00Z\nsource_alembic_head=old-schema-is-allowed\n')
        self.args = Namespace(app_dir=self.root, project='p', release_id='new', history='[]')

    def test_candidate_published_only_after_assets_manifest_and_new_index_verified(self):
        docker = FakeDocker()
        result = assets.preserve(self.args, docker, NOW)
        self.assertEqual(result['files'], 2)
        self.assertEqual(docker.output[0], {'new.js': b'new', 'old.js': b'old'})
        self.assertTrue(docker.published)
        self.assertTrue(docker.cleaned)
        base_id, base_tag = docker.tagged[0]
        self.assertEqual(base_id, 'sha256:' + 'a' * 64)
        self.assertRegex(base_tag, r'^localhost/erp-web-assets-base:[a-f0-9]{32}$')
        self.assertIn('FROM ' + base_tag + '\n', docker.dockerfile)
        self.assertIn('--pull=false', docker.build_options)
        self.assertIn('none', docker.build_options)

    def test_failed_build_never_registers_nonexistent_output_tag_for_cleanup(self):
        docker = FakeDocker('build_failed')
        with self.assertRaisesRegex(ValueError, 'build failed'):
            assets.preserve(self.args, docker, NOW)
        self.assertFalse(any(tag.startswith('erp-web-assets-verify:') for tag in docker.tags))
        self.assertEqual(len(docker.tags), 1, 'only the successfully created base alias is cleaned')
        self.assertFalse(docker.published)

    def test_temporary_base_tag_must_still_match_pinned_candidate(self):
        docker = FakeDocker('wrong_base_tag')
        with self.assertRaisesRegex(ValueError, 'base.*identity'):
            assets.preserve(self.args, docker, NOW)
        self.assertFalse(docker.published)
        self.assertIsNone(docker.output, 'wrong base must fail before build')
        self.assertTrue(docker.cleaned)

    def test_base_tag_identity_change_during_build_blocks_publication(self):
        docker = FakeDocker('base_changed_during_build')
        with self.assertRaisesRegex(ValueError, 'base tag identity changed'):
            assets.preserve(self.args, docker, NOW)
        self.assertFalse(docker.published)
        self.assertTrue(docker.cleaned)

    def test_cleanup_failure_never_replaces_primary_build_exception(self):
        docker = FakeDocker('build_failed')
        diagnostics = io.StringIO()
        with (patch.object(docker, 'cleanup', side_effect=ValueError('secondary cleanup failure')),
              redirect_stderr(diagnostics), self.assertRaisesRegex(ValueError, '^build failed$')):
            assets.preserve(self.args, docker, NOW)
        self.assertIn('secondary cleanup failure', diagnostics.getvalue())
        self.assertFalse(docker.published)

    def test_cleanup_failure_after_success_still_blocks_release(self):
        docker = FakeDocker()
        with patch.object(docker, 'cleanup', side_effect=ValueError('cleanup failed')), self.assertRaisesRegex(ValueError, 'cleanup failed'):
            assets.preserve(self.args, docker, NOW)

    def test_failed_build_byte_verification_and_wrong_project_do_not_publish(self):
        for fault in ('build_failed', 'changed_index', 'wrong_project'):
            docker = FakeDocker(fault)
            with self.subTest(fault=fault), self.assertRaises(ValueError):
                assets.preserve(self.args, docker, NOW)
            self.assertFalse(docker.published)
            self.assertTrue(docker.cleaned)

    def test_active_release_tag_cannot_be_used_as_candidate(self):
        self.args.release_id = 'old'
        docker = FakeDocker()
        with self.assertRaisesRegex(ValueError, 'active'):
            assets.preserve(self.args, docker, NOW)
        self.assertFalse(docker.published)

    def test_union_overflow_does_not_export_later_history_and_cleans_without_tagging(self):
        self.args.history = json.dumps([{'release_id': 'history', 'image_id': 'sha256:' + 'c' * 64}])
        docker = FakeDocker()
        with patch.object(assets, 'MAX_BYTES', 3), self.assertRaisesRegex(ValueError, 'capacity'):
            assets.preserve(self.args, docker, NOW)
        self.assertEqual(docker.exported, ['sha256:' + 'a' * 64, 'sha256:' + 'b' * 64])
        self.assertTrue(docker.cleaned)
        self.assertFalse(docker.published)

    def test_explicit_history_requires_matching_project_tag_metadata_and_recent_date(self):
        self.args.history = json.dumps([{'release_id': 'history', 'image_id': 'sha256:' + 'c' * 64}])
        docker = FakeDocker()
        assets.preserve(self.args, docker, NOW)
        self.assertTrue(docker.published)
        for invalid in ('wrong-image', 'stale-metadata', 'path-traversal'):
            history = {'release_id': 'history', 'image_id': 'sha256:' + ('e' if invalid == 'wrong-image' else 'c') * 64}
            if invalid == 'path-traversal':
                history['release_id'] = '../history'
            if invalid == 'stale-metadata':
                (self.root / 'releases/history/.release-metadata').write_text('source_release_id=history\nprepared_utc=2026-10-17T10:00:00Z\n')
            self.args.history = json.dumps([history])
            docker = FakeDocker()
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                assets.preserve(self.args, docker, NOW)
            self.assertFalse(docker.published)
            self.assertTrue(docker.cleaned)


if __name__ == '__main__':
    unittest.main()
