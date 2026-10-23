"""Fixed private A/B/C/D fixture; never used by production deployment or compare."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import urlopen

import web_assets as assets


def has_asset_witness(data, kind, marker):
    compact = data.replace(' ', '')
    if kind == 'css':
        return f'--erp-release-fixture:{marker};' in compact
    return any(f'"data-release-window":{quote}{marker}{quote}' in compact for quote in ('"', '`'))


class ReleaseWindow:
    def __init__(self, owner, snapshot, state, archive_hash, origin, patch, refuse, save):
        self.owner, self.snapshot, self.state = owner, snapshot, state
        self.archive_hash, self.origin = archive_hash, origin
        self.patch, self.refuse, self.save = patch, refuse, save
        self.releases, self.receipts = {}, {}
        self.originals = {relative: (snapshot / relative).read_bytes() for relative in (
            'apps/web/src/esportsAssessments/AssessmentHistoryPage.tsx',
            'apps/web/src/esportsAssessments/reports.css')}
        self.marker = None
        self.prepare('A')

    def prepare(self, marker):
        if marker != 'ABCD'[len(self.releases)]:
            raise self.refuse('release sequence mismatch')
        for relative, original in self.originals.items():
            (self.snapshot / relative).write_bytes(original)
        patches = self.patch(self.snapshot, marker)
        self.marker = marker
        self.release_id = 'loading-' + marker + '-' + self.owner[:16]
        self.input = None
        self.selector_time = None
        if marker != 'A':
            previous = self.releases[chr(ord(marker) - 1)]
            now = datetime.now(UTC)
            if marker == 'D':
                receipt = self.receipts.get(self.releases['B']['releaseId'])
                if not receipt or receipt != self.releases['B']['receipt']:
                    raise self.refuse('authenticated activation receipt required for expiry')
                now = datetime.fromisoformat(receipt['activated_verified_utc']) + timedelta(days=7, seconds=1)
            self.selector_time = now.isoformat()
            retained = assets.select_retained_sets(previous['manifest'], self.receipts,
                previous_release=previous['releaseId'], candidate_release=self.release_id, now=now)
            value = {'schema_version': 1, 'candidate_release_id': self.release_id,
                     'predecessor_image_id': previous['image'], 'sets': retained}
            assets.validate_retained_input(value)
            self.input = self.state / ('retained-' + marker)
            self.input.mkdir(mode=0o755)
            self.save(self.input / 'manifest.json', value)
            (self.input / 'manifest.json').chmod(0o644)
            for item in {item['path']: item for group in retained for item in group['files']}.values():
                target = self.input / 'assets' / item['path'].removeprefix('assets/')
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                shutil.copyfile(previous['tree'] / item['path'], target)
                target.chmod(0o644)
            if marker == 'D' and any(group['release_id'] == self.releases['A']['releaseId'] for group in retained):
                raise self.refuse('expired release still selected')
        self.patches = patches

    def build_fields(self):
        args = {'ERP_BUILD_ID': self.release_id, 'ERP_RELEASE_ID': self.release_id}
        fields = ''
        if self.input:
            args.update({'ERP_WEB_ASSETS_PROTOCOL': '1', 'ERP_WEB_BUILD_ID': self.release_id,
                'ERP_WEB_ASSET_INPUT_SHA256': hashlib.sha256((self.input / 'manifest.json').read_bytes()).hexdigest()})
            fields += f'      additional_contexts: {{"erp_retained_assets": {json.dumps(str(self.input))}}}\n'
        return fields + f'      args: {json.dumps(args)}\n'

    def inspect_image(self, image, docker, project, ids):
        """Stopped, no-network, no-mount reader participates in the same owner ledger."""
        cid = docker('create', '--pull=never', '--network', 'none',
            '--label', f'erp.loading.owner={self.owner}', '--label', f'com.docker.compose.project={project}',
            '--label', 'com.docker.compose.service=release-reader', '--entrypoint', '/bin/true', image, capture=True).strip()
        ids.add(cid)
        def check():
            row = json.loads(docker('inspect', cid, capture=True))[0]
            labels = row.get('Config', {}).get('Labels', {})
            if (row['Id'] != cid or row['Image'] != image or row.get('Mounts')
                    or row['HostConfig']['NetworkMode'] != 'none'
                    or labels.get('erp.loading.owner') != self.owner
                    or labels.get('com.docker.compose.project') != project):
                raise self.refuse('private image reader ownership mismatch')
        check()
        folder = self.state / ('image-' + self.marker)
        folder.mkdir(mode=0o700)
        try:
            docker('cp', cid + ':/etc/erp-web/assets-manifest.json', str(folder / 'manifest.json'))
            with (folder / 'manifest.json').open('rb') as stream:
                manifest = assets.read_manifest(stream)
            tree = folder / 'public'
            tree.mkdir(mode=0o700)
            check()
            docker('cp', cid + ':/usr/share/nginx/html/.', str(tree))
            assets.verify_asset_tree(tree, manifest)
            if manifest['release_id'] != self.release_id:
                raise self.refuse('actual image release identity mismatch')
            current = next(group for group in manifest['sets'] if group['release_id'] == self.release_id)
            mapped = {}
            for kind, prefix, suffix in (('js', 'assets/AssessmentHistoryPage-', '.js'), ('css', 'assets/reports-', '.css')):
                matches = [item for item in current['files'] if item['path'].startswith(prefix) and item['path'].endswith(suffix)]
                if len(matches) != 1:
                    raise self.refuse('unique actual release asset mapping required')
                mapped[kind] = matches[0]
                data = (tree / matches[0]['path']).read_text()
                if not has_asset_witness(data, kind, self.marker):
                    raise self.refuse('release asset marker missing from emitted bytes')
                if any(previous['assets'][kind]['sha256'] == matches[0]['sha256']
                       or previous['assets'][kind]['path'] == matches[0]['path'] for previous in self.releases.values()):
                    raise self.refuse('release fixture asset bytes are not distinct')
            source_hash = hashlib.sha256()
            for source in sorted(self.snapshot.rglob('*')):
                if source.is_symlink():
                    source_hash.update((str(source.relative_to(self.snapshot)) + '\0' + os.readlink(source)).encode())
                elif source.is_file():
                    source_hash.update((str(source.relative_to(self.snapshot)) + '\0').encode())
                    source_hash.update(hashlib.sha256(source.read_bytes()).digest())
            source_digest = source_hash.hexdigest()
            if shutil.disk_usage(self.state).free < manifest['total_bytes'] * 2 + 64 * 1024 * 1024:
                raise self.refuse('release fixture disk capacity insufficient before activation')
            self.releases[self.marker] = {'releaseId': self.release_id, 'image': image, 'manifest': manifest,
                'manifestSha256': hashlib.sha256((folder / 'manifest.json').read_bytes()).hexdigest(),
                'sourceTreeSha256': source_digest, 'patches': self.patches,
                'patchSha256': hashlib.sha256(json.dumps(self.patches, sort_keys=True).encode()).hexdigest(),
                'tree': tree, 'assets': mapped, 'selectorClock': self.selector_time}
        finally:
            check()
            docker('rm', cid)

    def activated(self, actual_ids):
        row = self.releases[self.marker]
        for path in ('build-info.json', 'index.html'):
            with urlopen(self.origin + '/' + path, timeout=15) as response:
                body = response.read(2_000_001)
                if response.status != 200 or 'no-cache' not in response.headers.get('Cache-Control', ''):
                    raise self.refuse('actual current release HTTP cache/status mismatch')
            item = next(item for item in row['manifest']['documents'] if item['path'] == path)
            if len(body) != item['size'] or hashlib.sha256(body).hexdigest() != item['sha256']:
                raise self.refuse('actual current HTTP bytes differ from image')
            if path == 'build-info.json' and json.loads(body)['build_id'] != row['releaseId']:
                raise self.refuse('actual current HTTP build identity mismatch')
        if self.marker == 'A':
            self.stable_ids = {key: actual_ids[key] for key in ('api', 'db')}
        elif any(actual_ids[key] != value for key, value in self.stable_ids.items()):
            raise self.refuse('release switch replaced API or database')
        receipt = {'schema_version': 1, 'release_id': row['releaseId'],
            'source_tree_sha256': row['sourceTreeSha256'], 'web_image_id': row['image'],
            'web_assets_manifest_sha256': row['manifestSha256'], 'activated_verified_utc': datetime.now(UTC).isoformat()}
        row['receipt'] = receipt
        self.receipts[row['releaseId']] = receipt
        self.save(self.state / 'activation-ledger.json', self.receipts)

    def evidence(self):
        return {key: {name: value for name, value in row.items() if name not in ('tree', 'manifest')}
                for key, row in self.releases.items()}


def wait_barrier(path: Path, expected, validate, deadline):
    if time.monotonic() > deadline:
        raise TimeoutError('private release barrier timed out')
    if any(item.name != path.name for item in path.parent.glob('ready-*.json')):
        raise ValueError('unknown private release barrier message')
    if not path.exists():
        return False
    info = path.lstat()
    if not path.is_file() or path.is_symlink() or info.st_nlink != 1 or info.st_mode & 0o077 or info.st_size > 128 or info.st_uid != os.getuid():
        raise ValueError('private release barrier invalid')
    validate(json.loads(path.read_text()), expected)
    return True
