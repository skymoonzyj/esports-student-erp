"""Retention and hostile-archive tests; all filesystem operations are real."""
import copy
import hashlib
import importlib.util
import io
import json
import os
import tarfile
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPT = Path(__file__).with_name('web_assets.py')
FIXTURE = SCRIPT.parents[1] / 'apps/web/scripts/fixtures/web-assets-contract.json'
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def record(name='assets/old.js', body=b'hello'):
    return {'path': name, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def asset_set(release, successor=None, files=None):
    return {'release_id': release, 'build_id': release, 'superseded_by_release_id': successor,
            'files': [record()] if files is None else files}


def manifest(sets=None):
    return {'schema_version': 1, 'release_id': 'previous', 'build_id': 'previous',
            'predecessor_image_id': None, 'input_sha256': None,
            'documents': [record('index.html'), record('build-info.json')],
            'sets': [asset_set('previous')] if sets is None else sets, 'total_bytes': 15}


def receipt(release, when):
    return {'schema_version': 1, 'release_id': release, 'source_tree_sha256': 'a' * 64,
            'web_image_id': 'sha256:' + 'b' * 64, 'web_assets_manifest_sha256': 'c' * 64,
            'activated_verified_utc': when.isoformat()}


def archive(entries):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w') as tar:
        for name, body, kind in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(body) if kind == tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                member.linkname = '../outside'
            tar.addfile(member, io.BytesIO(body) if kind == tarfile.REGTYPE else None)
    output.seek(0)
    return output


class AssetsTests(unittest.TestCase):
    def test_legacy_inventory_uses_strict_archive_rules_without_extracting(self):
        self.assertTrue(hasattr(self.module, 'inventory_asset_archive'), 'strict bootstrap inventory required')
        result = self.module.inventory_asset_archive(archive([
            ('assets', b'', tarfile.DIRTYPE), ('assets/old.js', b'hello', tarfile.REGTYPE)]))
        self.assertEqual(result, [record()])
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.GNUTYPE_SPARSE,
                     tarfile.CONTTYPE, tarfile.XHDTYPE, tarfile.GNUTYPE_LONGNAME):
            stream = archive([('assets/old.js', b'hello', kind)])
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, '^ARCHIVE$'):
                self.module.inventory_asset_archive(stream)
            self.assertEqual(stream.tell(), 512)
        for name in ('assets/a.js.map', 'assets/source.ts', '../outside', 'index.html'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                self.module.inventory_asset_archive(archive([(name, b'x', tarfile.REGTYPE)]))

    @classmethod
    def setUpClass(cls):
        if SCRIPT.exists():
            spec = importlib.util.spec_from_file_location('web_assets', SCRIPT)
            cls.module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cls.module)

    def setUp(self):
        self.assertTrue(SCRIPT.is_file(), 'bounded asset retention implementation required')

    def test_retention_uses_supersession_not_build_time(self):
        data = manifest([asset_set('ancient', 'previous'), asset_set('previous'),
                         asset_set('seven', 's7'), asset_set('expired', 's8')])
        receipts = {'previous': receipt('previous', NOW),
                    's7': receipt('s7', NOW - timedelta(days=7)),
                    's8': receipt('s8', NOW - timedelta(days=7, seconds=1))}
        before = copy.deepcopy(data)
        result = self.module.select_retained_sets(data, receipts, previous_release='previous',
                                                 candidate_release='candidate', now=NOW)
        self.assertEqual([item['release_id'] for item in result], ['ancient', 'previous', 'seven'])
        self.assertEqual(result[1]['superseded_by_release_id'], 'candidate')
        self.assertEqual(data, before)

    def test_unknown_receipt_keeps_set(self):
        for bad in (None, receipt('wrong', NOW - timedelta(days=8)),
                    receipt('successor', NOW + timedelta(seconds=1)), {},
                    {**receipt('successor', NOW), 'web_assets_manifest_sha256': 'bad'},
                    {**receipt('successor', NOW), 'activated_verified_utc': '2026-10-01'}):
            with self.subTest(receipt=bad):
                result = self.module.select_retained_sets(
                    manifest([asset_set('old', 'successor'), asset_set('previous')]),
                    {} if bad is None else {'successor': bad}, previous_release='previous',
                    candidate_release='candidate', now=NOW)
                self.assertEqual([item['release_id'] for item in result], ['old', 'previous'])

    def test_shared_cross_language_contract(self):
        fixture = json.loads(FIXTURE.read_text())
        for case in fixture['valid']:
            self.module.validate_retained_input(case)
        for case in fixture['invalid']:
            with self.assertRaisesRegex(ValueError, '^SCHEMA$'):
                self.module.validate_retained_input(case)
        with self.assertRaisesRegex(ValueError, '^SCHEMA$'):
            self.module.verify_asset_tree(Path('/not-read'), fixture['valid'][0])

    def test_deduplicated_capacity_boundary_and_conflict(self):
        # Break caught: counting the same immutable file twice, wrong inclusive cap,
        # or accepting two contents under one public cache key.
        exact = [record('assets/a.js'), {**record('assets/b.js'), 'size': 536870907}]
        self.assertEqual(self.module.validate_files(exact + exact[:1]), 536870912)
        with self.assertRaisesRegex(ValueError, '^CAPACITY$'):
            self.module.validate_files([*exact, record('assets/c.js', b'x')])
        with self.assertRaisesRegex(ValueError, '^CONFLICT$'):
            self.module.validate_files([record(), record(body=b'other')])

    def test_extract_rejects_duplicates_unknown_paths_and_non_regular_members(self):
        cases = [[('assets/old.js', b'hello', tarfile.REGTYPE)] * 2,
                 [('assets/extra.js', b'hello', tarfile.REGTYPE)]]
        cases += [[(name, b'hello', tarfile.REGTYPE)] for name in
                  ('../outside', '/assets/old.js', 'assets/../old.js', 'assets/./old.js',
                   'assets/back\\slash.js', 'assets/' + 'a' * 1025 + '.js')]
        cases += [[('/', b'', tarfile.DIRTYPE), ('assets/old.js', b'hello', tarfile.REGTYPE)]]
        cases += [[('assets/old.js', b'', kind)] for kind in
                  (tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE, tarfile.BLKTYPE,
                   tarfile.FIFOTYPE, tarfile.DIRTYPE)]
        for entries in cases:
            with (self.subTest(entries=entries), tempfile.TemporaryDirectory() as root,
                  self.assertRaisesRegex(ValueError, '^(ARCHIVE|PATH|CONFLICT)$')):
                self.module.extract_asset_archive(archive(entries), Path(root), expected_files=[record()])

    def test_extract_verifies_bytes_and_tree_refuses_missing_unknown_link_or_tampered_file(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            result = self.module.extract_asset_archive(archive([('assets/old.js', b'hello', tarfile.REGTYPE)]),
                                                      root, expected_files=[record()])
            self.assertEqual(result['total_bytes'], 5)
            data = manifest()
            (root / 'index.html').write_bytes(b'hello')
            (root / 'build-info.json').write_bytes(b'hello')
            self.assertEqual(self.module.verify_asset_tree(root, data)['total_bytes'], 15)
            (root / 'assets/old.js').write_bytes(b'HELLO')
            with self.assertRaisesRegex(ValueError, '^HASH$'):
                self.module.verify_asset_tree(root, data)
            (root / 'assets/old.js').unlink()
            with self.assertRaisesRegex(ValueError, '^TREE$'):
                self.module.verify_asset_tree(root, data)
            (root / 'assets/old.js').symlink_to(root / 'index.html')
            with self.assertRaisesRegex(ValueError, '^TREE$'):
                self.module.verify_asset_tree(root, data)

    def test_extract_preflights_empty_directory_and_declared_capacity_without_deleting(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            (root / 'sentinel').write_text('keep')
            with self.assertRaisesRegex(ValueError, '^DESTINATION$'):
                self.module.extract_asset_archive(io.BytesIO(), root, expected_files=[])
            self.assertEqual((root / 'sentinel').read_text(), 'keep')
        for size in (536870913,):
            with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(ValueError, '^CAPACITY$'):
                self.module.extract_asset_archive(io.BytesIO(), Path(root), expected_files=[{**record(), 'size': size}])
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(ValueError, '^CAPACITY$'):
            self.module.extract_asset_archive(archive([('assets/old.js', b'hello', tarfile.REGTYPE)]),
                                              Path(root), expected_files=[record()], max_bytes=4)

    def test_schema_limits_duplicate_entries_unknown_keys_and_manifest_bytes(self):
        valid = json.loads(FIXTURE.read_text())['valid'][1]
        for change in ({'schema_version': 2}, {'extra': True}, {'sets': valid['sets'] * 100001}):
            with self.subTest(change=list(change)), self.assertRaisesRegex(ValueError, '^SCHEMA$'):
                self.module.validate_retained_input({**valid, **change})
        duplicate = copy.deepcopy(valid)
        duplicate['sets'][0]['files'] *= 2
        with self.assertRaisesRegex(ValueError, '^CONFLICT$'):
            self.module.validate_retained_input(duplicate)
        with self.assertRaisesRegex(ValueError, '^SCHEMA$'):
            self.module.read_manifest(io.BytesIO(b' ' * (16777216 + 1)))

    def test_tar_root_and_ancestor_headers_accept_docker_relative_prefix(self):
        entries = [('.', b'', tarfile.DIRTYPE), ('./assets/', b'', tarfile.DIRTYPE),
                   ('./assets/old.js', b'hello', tarfile.REGTYPE)]
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.module.extract_asset_archive(archive(entries), Path(root),
                             expected_files=[record()])['total_bytes'], 5)

    def test_tar_rejects_extended_headers_truncation_padding_payload_and_hash_mismatch(self):
        cases = [archive([('assets/old.js', b'HELLO', tarfile.REGTYPE)]), io.BytesIO(b'x' * 512)]
        for kind in (tarfile.XHDTYPE, tarfile.XGLTYPE, tarfile.GNUTYPE_LONGNAME,
                     tarfile.GNUTYPE_LONGLINK, tarfile.GNUTYPE_SPARSE):
            member = tarfile.TarInfo('assets/old.js')
            member.type, member.size = kind, 536870913
            cases.append(io.BytesIO(member.tobuf()))
        cases.append(io.BytesIO(archive([('assets/old.js', b'hello', tarfile.REGTYPE)]).getvalue() + b'UNKNOWN'))
        for stream in cases:
            with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(ValueError, '^(ARCHIVE|HASH)$'):
                self.module.extract_asset_archive(stream, Path(root), expected_files=[record()])

    def test_tar_rejects_matching_sparse_and_contiguous_headers_before_payload(self):
        for kind in (tarfile.GNUTYPE_SPARSE, tarfile.CONTTYPE):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as root:
                member = tarfile.TarInfo('assets/old.js')
                member.type, member.size = kind, 5
                stream = io.BytesIO(member.tobuf(format=tarfile.USTAR_FORMAT)
                                    + b'hello' + b'\0' * 507 + b'\0' * 1024)
                with self.assertRaisesRegex(ValueError, '^ARCHIVE$'):
                    self.module.extract_asset_archive(stream, Path(root), expected_files=[record()])
                self.assertEqual(stream.tell(), 512, 'forbidden header payload must remain unread')
                self.assertEqual(list(Path(root).iterdir()), [], 'forbidden type must not create files')

    def test_validation_rejects_ambiguous_json_keys_and_non_ascii_or_newline_path(self):
        self.assertEqual(self.module.validate_files([record('assets/' + 'x' * 1014 + '.js')]), 5)
        for path in ('assets/old.js\n', 'assets/é.js', 'assets/' + 'x' * 1015 + '.js'):
            with self.assertRaisesRegex(ValueError, '^(SCHEMA|PATH)$'):
                self.module.validate_files([record(path)])
        raw = b'{"schema_version":1,"schema_version":1,"candidate_release_id":null,"predecessor_image_id":null,"sets":[]}'
        with self.assertRaisesRegex(ValueError, '^SCHEMA$'):
            self.module.read_manifest(io.BytesIO(raw), retained=True)

    def test_parallel_symlink_injection_cannot_escape_verified_empty_destination(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            destination, outside = root / 'destination', root / 'outside'
            destination.mkdir()
            outside.mkdir()
            sentinel = outside / 'old.js'
            sentinel.write_bytes(b'not replaced')
            class RacingStream(io.BytesIO):
                def read(inner, size=-1):
                    if not (destination / 'assets').exists():
                        (destination / 'assets').symlink_to(outside, target_is_directory=True)
                    return super().read(size)
            stream = RacingStream(archive([('assets/old.js', b'hello', tarfile.REGTYPE)]).getvalue())
            with self.assertRaisesRegex(ValueError, '^DESTINATION$'):
                self.module.extract_asset_archive(stream, destination, expected_files=[record()])
            self.assertEqual(sentinel.read_bytes(), b'not replaced')

    def test_tree_refuses_hardlinks_fifo_and_unknown_files(self):
        for kind in ('hardlink', 'fifo', 'unknown'):
            with tempfile.TemporaryDirectory() as root:
                root = Path(root)
                (root / 'assets').mkdir()
                (root / 'index.html').write_bytes(b'hello')
                (root / 'build-info.json').write_bytes(b'hello')
                if kind == 'hardlink':
                    os.link(root / 'index.html', root / 'assets/old.js')
                elif kind == 'fifo':
                    os.mkfifo(root / 'assets/old.js')
                else:
                    (root / 'assets/old.js').write_bytes(b'hello')
                    (root / 'extra').write_bytes(b'unknown')
                with self.assertRaisesRegex(ValueError, '^TREE$'):
                    self.module.verify_asset_tree(root, manifest())

    def test_extract_rechecks_all_hashes_after_complete_stream(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            class MutatingStream(io.BytesIO):
                def read(inner, size=-1):
                    earlier = root / 'assets/old.js'
                    if inner.tell() >= 1024 and earlier.is_file():
                        earlier.write_bytes(b'HELLO')
                    return super().read(size)
            stream = MutatingStream(archive([('assets/old.js', b'hello', tarfile.REGTYPE),
                                              ('assets/next.js', b'world', tarfile.REGTYPE)]).getvalue())
            with self.assertRaisesRegex(ValueError, '^HASH$'):
                self.module.extract_asset_archive(stream, root,
                    expected_files=[record(), record('assets/next.js', b'world')])


if __name__ == '__main__':
    unittest.main()
