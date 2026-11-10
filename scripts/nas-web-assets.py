#!/usr/bin/env python3
"""Preserve bounded, verified immutable assets before the NAS service switch.

Only local pinned image IDs are read. No application container is ever started.
The manifest is outside nginx's document root; retired deadlines never refresh.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_BYTES = 256 * 1024 * 1024
MAX_FILES = 5000
MAX_MANIFEST = 2 * 1024 * 1024
MANIFEST_PATH = '/usr/share/erp-web-assets/manifest.json'
LABEL = 'org.esports-erp.web-assets-manifest'
IMAGE_ID = re.compile(r'sha256:[a-f0-9]{64}\Z')
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def utc(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value):
        raise ValueError('invalid UTC deadline')
    return datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)


def stamp(value):
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError('UTC clock required')
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def safe_path(name):
    if (not isinstance(name, str) or len(name) > 512 or
            not re.fullmatch(r'[A-Za-z0-9_.\-/]+', name) or
            any(part in ('', '.', '..') for part in name.split('/'))):
        raise ValueError('unsafe asset path')
    return name


def inventory(files, *, max_bytes=MAX_BYTES, max_files=MAX_FILES):
    if type(files) is not dict or len(files) > min(max_files, MAX_FILES):
        raise ValueError('asset capacity exceeded')
    total = 0
    result = {}
    for name, data in files.items():
        safe_path(name)
        if type(data) is not bytes:
            raise ValueError('invalid asset bytes')
        total += len(data)
        if total > min(max_bytes, MAX_BYTES):
            raise ValueError('asset capacity exceeded')
        result[name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
    return result


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate manifest field')
        result[key] = value
    return result


def parse_manifest(raw):
    if len(raw) > MAX_MANIFEST:
        raise ValueError('manifest capacity exceeded')
    value = json.loads(raw, object_pairs_hook=unique_object)
    if type(value) is not dict or set(value) != {'version', 'files'} or type(value['version']) is not int or value['version'] != 1:
        raise ValueError('invalid manifest')
    if type(value['files']) is not dict or len(value['files']) > MAX_FILES:
        raise ValueError('invalid manifest inventory')
    total = 0
    for name, item in value['files'].items():
        safe_path(name)
        if type(item) is not dict or set(item) != {'sha256', 'size', 'expires_utc'}:
            raise ValueError('invalid manifest entry')
        if not isinstance(item['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', item['sha256']):
            raise ValueError('invalid manifest hash')
        if type(item['size']) is not int or item['size'] < 0:
            raise ValueError('invalid manifest size')
        total += item['size']
        if total > MAX_BYTES:
            raise ValueError('manifest capacity exceeded')
        if item['expires_utc'] is not None:
            utc(item['expires_utc'])
    return value


def merge_assets(candidate, sources, now, *, max_bytes=MAX_BYTES, max_files=MAX_FILES):
    stamp(now)
    result = dict(candidate)
    records = {name: {**item, 'expires_utc': None} for name, item in
               inventory(candidate, max_bytes=max_bytes, max_files=max_files).items()}
    total = sum(map(len, result.values()))
    for files, manifest, legacy_expiry in sources:
        utc(legacy_expiry)
        source_inventory = inventory(files)
        if manifest is not None:
            parse_manifest(json.dumps(manifest).encode())
            expected = {name: {k: item[k] for k in ('sha256', 'size')} for name, item in manifest['files'].items()}
            if expected != source_inventory:
                raise ValueError('manifest asset hash/inventory mismatch')
        for name, item in source_inventory.items():
            deadline = (manifest['files'][name]['expires_utc'] if manifest else None) or legacy_expiry
            if utc(deadline) > now + timedelta(days=7):
                raise ValueError('retirement deadline exceeds seven days')
            if utc(deadline) <= now:
                continue
            if name in result:
                if result[name] != files[name]:
                    raise ValueError('immutable asset conflict')
                if records[name]['expires_utc'] is not None:
                    # One copy shared by multiple releases must serve the latest
                    # still-valid deadline, not refresh it on every deployment.
                    records[name]['expires_utc'] = max(records[name]['expires_utc'], deadline)
            else:
                if len(result) >= min(max_files, MAX_FILES) or total + item['size'] > min(max_bytes, MAX_BYTES):
                    raise ValueError('asset capacity exceeded')
                total += item['size']
                result[name] = files[name]
                records[name] = {**item, 'expires_utc': deadline}
    inventory(result, max_bytes=max_bytes, max_files=max_files)
    return result, {'version': 1, 'files': records}


class LimitedReader:
    def __init__(self, source, limit):
        self.source, self.remaining = source, limit

    def read(self, size=-1):
        size = min(size if size >= 0 else self.remaining + 1, self.remaining + 1)
        data = self.source.read(size)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ValueError('archive capacity exceeded')
        return data


def read_assets_tar(stream, *, max_bytes=MAX_BYTES, max_files=MAX_FILES):
    files = {}
    seen = set()
    total = 0
    try:
        with tarfile.open(fileobj=LimitedReader(stream, min(max_bytes, MAX_BYTES) + 16 * 1024 * 1024), mode='r|') as archive:
            for member in archive:
                name = member.name.removeprefix('./')
                if name in ('', '.') and member.isdir():
                    continue
                name = safe_path(name.rstrip('/') if member.isdir() else name)
                if name in seen or member.pax_headers or len(seen) >= MAX_FILES * 2:
                    raise ValueError('duplicate or unsupported archive entry')
                seen.add(name)
                if member.isdir():
                    continue
                if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    raise ValueError('archive links/special files forbidden')
                total += member.size
                if total > min(max_bytes, MAX_BYTES) or len(files) >= min(max_files, MAX_FILES):
                    raise ValueError('asset capacity exceeded')
                data = archive.extractfile(member).read(member.size + 1)
                if len(data) != member.size:
                    raise ValueError('truncated asset archive')
                files[name] = data
    except tarfile.TarError as error:
        raise ValueError('invalid asset archive') from error
    return files


class Docker:
    def __init__(self):
        self.containers = []
        self.tags = []

    def call(self, *args):
        return subprocess.check_output(['docker', *args], text=True).strip()

    def inspect_image(self, ref):
        item, = json.loads(self.call('image', 'inspect', ref))
        if not IMAGE_ID.fullmatch(item.get('Id', '')):
            raise ValueError('image must resolve to immutable local ID')
        return item

    def copy(self, container, path, **limits):
        process = subprocess.Popen(['docker', 'cp', f'{container}:{path}', '-'], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            result = read_assets_tar(process.stdout, **limits)
            process.stdout.close()
            if process.wait(timeout=30):
                raise ValueError('image asset export failed')
            return result
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
            process.wait()

    def export(self, image):
        name = 'erp-assets-' + uuid.uuid4().hex
        self.containers.append(name)
        self.call('create', '--pull=never', '--name', name, '--network', 'none', '--read-only', '--entrypoint', '/bin/false', image['Id'])
        files = self.copy(name, '/usr/share/nginx/html/assets/.')
        manifest = None
        marker = (image.get('Config', {}).get('Labels') or {}).get(LABEL)
        if marker is not None:
            if marker != '1':
                raise ValueError('unsupported asset manifest version')
            entries = self.copy(name, MANIFEST_PATH, max_bytes=MAX_MANIFEST, max_files=1)
            if set(entries) != {'manifest.json'}:
                raise ValueError('missing asset manifest')
            manifest = parse_manifest(entries['manifest.json'])
        index = self.copy(name, '/usr/share/nginx/html/index.html', max_bytes=MAX_MANIFEST, max_files=1)
        if set(index) != {'index.html'}:
            raise ValueError('missing candidate index')
        return files, manifest, index['index.html']

    def cleanup(self):
        errors = []
        for kind, refs in (('container', self.containers), ('image', self.tags)):
            for ref in refs:
                if subprocess.run(['docker', kind, 'rm', ref], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False).returncode:
                    errors.append(ref)
        if errors:
            raise ValueError('temporary Docker resource cleanup failed: ' + ', '.join(errors))


def release_metadata(app_dir, release, now, *, recent=False):
    if not IDENTITY.fullmatch(release):
        raise ValueError('invalid release identity')
    path = app_dir / 'releases' / release
    if path.is_symlink() or path.resolve().parent != (app_dir / 'releases').resolve():
        raise ValueError('untrusted release path')
    metadata = path / '.release-metadata'
    if metadata.is_symlink() or not metadata.is_file() or metadata.stat().st_size > 4096:
        raise ValueError('untrusted release metadata')
    fields = {}
    for line in metadata.read_text().splitlines():
        key, sep, value = line.partition('=')
        if not sep or key in fields:
            raise ValueError('invalid release metadata')
        fields[key] = value
    if fields.get('source_release_id') != release:
        raise ValueError('release metadata identity mismatch')
    prepared = utc(fields.get('prepared_utc'))
    if prepared > now or (recent and now - prepared > timedelta(days=7)):
        raise ValueError('historical release outside seven-day window')
    return prepared


def preserve(args, docker=None, now=None):
    docker = docker or Docker()
    now = now or datetime.now(timezone.utc).replace(microsecond=0)
    try:
        if not IDENTITY.fullmatch(args.project):
            raise ValueError('invalid project identity')
        release_metadata(args.app_dir, args.release_id, now)
        candidate_tag = f'{args.project}-web:{args.release_id}'
        candidate = docker.inspect_image(candidate_tag)
        active, = json.loads(docker.call('inspect', f'{args.project}-web-1'))
        labels = active.get('Config', {}).get('Labels') or {}
        if labels.get('com.docker.compose.project') != args.project or labels.get('com.docker.compose.service') != 'web':
            raise ValueError('active web is not the requested project/service')
        active_tag = active.get('Config', {}).get('Image', '')
        prefix = f'{args.project}-web:'
        if not active_tag.startswith(prefix):
            raise ValueError('active image release tag mismatch')
        active_release = active_tag[len(prefix):]
        if active_release == args.release_id:
            raise ValueError('active release cannot be the candidate release')
        release_metadata(args.app_dir, active_release, now)
        previous = docker.inspect_image(active['Image'])
        if docker.inspect_image(active_tag)['Id'] != previous['Id']:
            raise ValueError('active release tag no longer matches running image')
        history = json.loads(args.history, object_pairs_hook=unique_object)
        if type(history) is not list or len(history) > 20:
            raise ValueError('invalid historical image allowlist')
        trusted = [(previous, stamp(now + timedelta(days=7)))]
        seen = {previous['Id'], candidate['Id']}
        for item in history:
            if type(item) is not dict or set(item) != {'release_id', 'image_id'} or not IMAGE_ID.fullmatch(item.get('image_id', '')):
                raise ValueError('invalid historical image allowlist entry')
            prepared = release_metadata(args.app_dir, item['release_id'], now, recent=True)
            image = docker.inspect_image(f'{args.project}-web:{item["release_id"]}')
            if image['Id'] != item['image_id'] or image['Id'] in seen:
                raise ValueError('historical image identity mismatch or duplicate')
            seen.add(image['Id'])
            trusted.append((image, stamp(prepared + timedelta(days=7))))
        candidate_files, candidate_manifest, candidate_index = docker.export(candidate)
        if candidate_manifest is not None:
            raise ValueError('candidate must be a fresh build, not a retained release')
        def sources():
            # Stream groups instead of accumulating twenty complete historical
            # exports in memory before union validation can fail.
            for image, expiry in trusted:
                files, manifest, _ = docker.export(image)
                yield files, manifest, expiry
        merged, manifest = merge_assets(candidate_files, sources(), now)
        with tempfile.TemporaryDirectory(prefix='erp-web-assets-') as directory:
            root = Path(directory)
            for name, data in merged.items():
                output = root / 'assets' / name
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(data)
            (root / 'assets').mkdir(exist_ok=True)
            (root / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, separators=(',', ':')))
            # BuildKit interprets a bare sha256:ID in FROM as a Docker Hub
            # repository/tag, unlike the legacy builder. Create a private UUID
            # alias from the pinned local ID; localhost prevents a public Hub
            # fallback, and both sides of the build must retain that exact ID.
            base_tag = 'localhost/erp-web-assets-base:' + uuid.uuid4().hex
            docker.call('tag', candidate['Id'], base_tag)
            docker.tags.append(base_tag)
            if docker.inspect_image(base_tag)['Id'] != candidate['Id']:
                raise ValueError('temporary base tag identity mismatch')
            (root / 'Dockerfile').write_text(
                f'FROM {base_tag}\n'
                'COPY --chown=101:101 assets/ /usr/share/nginx/html/assets/\n'
                f'COPY --chown=101:101 manifest.json {MANIFEST_PATH}\n'
                f'LABEL {LABEL}="1"\n')
            temporary_tag = 'erp-web-assets-verify:' + uuid.uuid4().hex
            docker.call('build', '--network', 'none', '--pull=false', '--tag', temporary_tag, str(root))
            # A failed build normally never produces its output tag. Register
            # only successful creations, so cleanup cannot mask that failure.
            docker.tags.append(temporary_tag)
            if docker.inspect_image(base_tag)['Id'] != candidate['Id']:
                raise ValueError('temporary base tag identity changed during build')
            built = docker.inspect_image(temporary_tag)
            actual, actual_manifest, actual_index = docker.export(built)
            if actual != merged or actual_manifest != manifest or actual_index != candidate_index:
                raise ValueError('built web image byte verification failed')
            # The mutable candidate tag changes only after all bytes are checked.
            docker.call('tag', built['Id'], candidate_tag)
        return {'image_id': built['Id'], 'files': len(merged), 'bytes': sum(map(len, merged.values()))}
    finally:
        primary = sys.exc_info()[1]
        try:
            docker.cleanup()
        except Exception as cleanup_error:
            if primary is None:
                raise
            # Preserve the build/export/validation error as the release blocker;
            # secondary cleanup trouble remains visible in diagnostic output.
            print(f'Additional temporary-resource cleanup failure: {cleanup_error}', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-dir', type=Path, required=True)
    parser.add_argument('--project', required=True)
    parser.add_argument('--release-id', required=True)
    parser.add_argument('--history', default='[]', help='Explicit JSON list of approved {release_id,image_id}; never auto-discovered')
    args = parser.parse_args()
    try:
        print(json.dumps(preserve(args), sort_keys=True))
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as error:
        parser.exit(1, f'Web asset preservation blocked deployment: {error}\n')


if __name__ == '__main__':
    main()
