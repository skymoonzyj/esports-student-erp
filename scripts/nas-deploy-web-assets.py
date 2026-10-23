"""Locked NAS Web admission. JSON-only stdout; never reads deployment secrets."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import web_assets as assets

LABEL = 'cn.jingxue.erp.web-assets-contract'
OWNER = 'cn.jingxue.erp.web-assets-owner'
HEX = r'[a-f0-9]{64}'
IMAGE = r'sha256:' + HEX
IDENTITY = r'[A-Za-z0-9][A-Za-z0-9_-]{0,199}'
FILES = ('compose.yaml', 'compose.prod.yaml', 'compose.nas-staging.yaml', 'compose.nas-lan-staging.yaml')
SERVICES = ('db', 'api', 'scheduler', 'web', 'proxy', 'caddy')


def require(condition, category='BINDING'):
    if not condition:
        raise ValueError(category)


def bounded_file(path, maximum=assets.MAX_MANIFEST_BYTES):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= maximum)
            value = stream.read(maximum + 1)
            require(len(value) == info.st_size)
            return value
    except OSError as error:
        raise ValueError('BINDING') from error


def metadata(path):
    values = {}
    for line in bounded_file(path, 65536).decode('utf8').splitlines():
        require('=' in line)
        key, value = line.split('=', 1)
        require(key not in values and re.fullmatch(r'[a-z_][a-z0-9_]*', key))
        values[key] = value
    return values


def validate_source_contract(root):
    value = json.loads(bounded_file(root / 'scripts/web-assets-contract.json', 4096), object_pairs_hook=assets._object)
    assets._schema(value, {'type': 'object', 'required': ['schema_version', 'required_protocol'],
        'additionalProperties': False, 'properties': {'schema_version': {'const': 1}, 'required_protocol': {'const': 1}}})


def receipt(value, release):
    require(value.get('source_release_id') == release and value.get('web_assets_contract') == '1'
            and value.get('web_assets_status') == 'verified')
    result = {'schema_version': 1, 'release_id': release}
    for key in ('source_tree_sha256', 'web_image_id', 'web_assets_manifest_sha256', 'activated_verified_utc'):
        result[key] = value.get(key)
    assets._schema(result, assets.SCHEMA['$defs']['activationReceipt'])
    when = datetime.fromisoformat(result['activated_verified_utc'].replace('Z', '+00:00'))
    require(when.tzinfo is not None and when.utcoffset() is not None and when <= datetime.now(timezone.utc))
    require(re.fullmatch(HEX, value.get('web_assets_input_sha256', '')) is not None
            and re.fullmatch(IMAGE, value.get('web_predecessor_image_id', '')) is not None)
    return result


def active_release(app):
    require(app.is_dir() and not app.is_symlink())
    raw = bounded_file(app / '.active-release', 4096).decode().strip()
    path = Path(raw)
    require(path.parent == app / 'releases' and re.fullmatch(IDENTITY, path.name)
            and path.is_dir() and not path.is_symlink() and path.resolve() == path)
    state = metadata(app / '.deployment-state')
    allowed = state.get('status') in ('complete', 'healthy', 'backup_failed') or (
        state.get('status') == 'attempt_failed' and state.get('active_unchanged') == 'true')
    require(allowed and state.get('release') == str(path))
    value = metadata(path / '.release-metadata')
    require(value.get('source_release_id') == path.name)
    return path, value


def runtime_images(value):
    require(type(value) is dict and set(value) == set(SERVICES))
    require(all(re.fullmatch(IMAGE, item) is not None for item in value.values()))
    return value


def admission(value, release):
    require(value.get('source_release_id') == release
            and value.get('web_assets_status') in ('admitted', 'failed')
            and value.get('web_assets_contract') == '1')
    for key in ('source_tree_sha256', 'web_assets_manifest_sha256', 'web_assets_input_sha256',
                'web_admitted_config_sha256'):
        require(re.fullmatch(HEX, value.get(key, '')) is not None)
    for key in ('web_image_id', 'web_predecessor_image_id'):
        require(re.fullmatch(IMAGE, value.get(key, '')) is not None)
    images = runtime_images(json.loads(value.get('web_admitted_runtime_images', ''), object_pairs_hook=assets._object))
    require(images['web'] == value['web_image_id'])
    return images


def carried_runtime(meta, app):
    """At most one observed identity per service; never a failed-release list."""
    if 'web_admitted_previous_runtime' not in meta:
        return {}  # Older admissions authorize no additional runtime choices.
    value = json.loads(meta['web_admitted_previous_runtime'], object_pairs_hook=assets._object)
    require(type(value) is dict and set(value) == set(SERVICES))
    for choice in value.values():
        require(type(choice) is dict and set(choice) == {'release', 'config_sha256', 'image_id'})
        require(type(choice['release']) is str and len(choice['release']) <= 4096)
        path = Path(choice['release'])
        require(path.parent == app / 'releases' and re.fullmatch(IDENTITY, path.name) is not None
                and path.is_dir() and not path.is_symlink() and path.resolve() == path)
        require(re.fullmatch(HEX, choice['config_sha256']) is not None
                and re.fullmatch(IMAGE, choice['image_id']) is not None)
    return value


def read_checkpoint(app):
    value = json.loads(bounded_file(app / '.web-last-successful.json'), object_pairs_hook=assets._object)
    return validate_checkpoint(app, value)


def validate_checkpoint(app, value):
    require(type(value) is dict and set(value) == {'schema_version', 'predecessor', 'authority'}
            and type(value['schema_version']) is int and value['schema_version'] == 1)
    pre, authority = value['predecessor'], value['authority']
    require(type(pre) is dict and set(pre) == {'release', 'release_id', 'metadata_sha256', 'image_id',
                                             'protocol', 'manifest_sha256', 'legacy_files'})
    require(type(authority) is dict and set(authority) == {'config_sha256', 'images'}
            and re.fullmatch(HEX, authority['config_sha256']) is not None)
    runtime_images(authority['images'])
    path = Path(pre['release'])
    require(path.parent == app / 'releases' and path.name == pre['release_id']
            and re.fullmatch(IDENTITY, path.name) is not None and path.is_dir()
            and not path.is_symlink() and path.resolve() == path)
    require(re.fullmatch(IMAGE, pre['image_id']) is not None
            and authority['images']['web'] == pre['image_id']
            and re.fullmatch(HEX, pre['metadata_sha256']) is not None
            and hashlib.sha256(bounded_file(path / '.release-metadata')).hexdigest() == pre['metadata_sha256'])
    old = metadata(path / '.release-metadata')
    require(old.get('source_release_id') == path.name)
    if type(pre['protocol']) is int and pre['protocol'] == 1:
        proof = receipt(old, path.name)
        require(proof['web_image_id'] == pre['image_id']
                and proof['web_assets_manifest_sha256'] == pre['manifest_sha256'] and pre['legacy_files'] is None)
    else:
        require(type(pre['protocol']) is int and pre['protocol'] == 0 and pre['manifest_sha256'] is None
                and old.get('web_assets_contract') is None and old.get('web_assets_status') is None
                and type(pre['legacy_files']) is list and pre['legacy_files'])
        assets.validate_retained_input({'schema_version': 1, 'candidate_release_id': 'checkpoint',
            'predecessor_image_id': pre['image_id'], 'sets': [{'release_id': path.name, 'build_id': path.name,
                'superseded_by_release_id': None, 'files': pre['legacy_files']}]})
    return value


def failed_current(app, checkpoint):
    state = metadata(app / '.deployment-state')
    current = (app / 'current').resolve(strict=True)
    require((app / 'current').is_symlink() and state.get('status') == 'failed'
            and state.get('release') == str(current) and current.parent == app / 'releases'
            and re.fullmatch(IDENTITY, current.name) is not None)
    value = metadata(current / '.release-metadata')
    admission(value, current.name)
    require(value['web_predecessor_image_id'] == checkpoint['predecessor']['image_id'])
    return current, value


def docker(*args):
    return subprocess.check_output(['docker', *args], stderr=subprocess.DEVNULL)


def inspect_image(image):
    require(re.fullmatch(IMAGE, image) is not None)
    items = json.loads(docker('image', 'inspect', image))
    require(len(items) == 1 and items[0]['Id'] == image)
    return items[0]


def running(app, project):
    item = json.loads(docker('inspect', f'{project}-web-1'))[0]
    labels = item['Config'].get('Labels', {})
    require(item.get('State', {}).get('Status') == 'running'
            and item.get('State', {}).get('Health', {}).get('Status') == 'healthy'
            and labels.get('com.docker.compose.project') == project
            and labels.get('com.docker.compose.service') == 'web'
            and labels.get('com.docker.compose.project.working_dir') == str(app / 'current'))
    expected = [str(app / 'current' / name) for name in FILES]
    if (app / 'current/.web-assets-compose.json').is_file():
        expected.append(str(app / 'current/.web-assets-compose.json'))
    require(labels.get('com.docker.compose.project.config_files', '').split(',') == expected)
    require(re.fullmatch(IMAGE, item.get('Image', '')) is not None)
    return item['Image']


@contextmanager
def owned_container(image):
    inspect_image(image)
    owner = uuid.uuid4().hex
    cid = docker('create', '--pull=never', '--network', 'none', '--label', f'{OWNER}={owner}',
                 '--entrypoint', '/bin/true', image).decode().strip()
    require(re.fullmatch(HEX, cid) is not None)
    def check():
        item = json.loads(docker('inspect', cid))[0]
        require(item.get('Id') == cid and item.get('Image') == image
                and item.get('Config', {}).get('Labels', {}).get(OWNER) == owner
                and not item.get('Mounts')
                and item.get('HostConfig', {}).get('NetworkMode') == 'none')
    try:
        check()
        yield cid, check
    finally:
        check()
        docker('rm', cid)


@contextmanager
def cp_stream(cid, source, check):
    check()
    process = subprocess.Popen(['docker', 'cp', f'{cid}:{source}', '-'],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        yield process.stdout
        require(process.wait() == 0, 'ARCHIVE')
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def image_manifest(cid, check):
    with cp_stream(cid, '/etc/erp-web/assets-manifest.json', check) as stream:
        raw = assets._read_exact(stream, 512)
        try:
            member = tarfile.TarInfo.frombuf(raw, 'ascii', 'strict')
        except (tarfile.HeaderError, UnicodeError, ValueError) as error:
            raise ValueError('MANIFEST') from error
        require(member.name in ('assets-manifest.json', './assets-manifest.json')
                and member.type in (tarfile.REGTYPE, tarfile.AREGTYPE)
                and 0 <= member.size <= assets.MAX_MANIFEST_BYTES, 'MANIFEST')
        data = assets._read_exact(stream, member.size)
        assets._read_exact(stream, (-member.size) % 512)
        require(assets._read_exact(stream, 512) == b'\0' * 512, 'MANIFEST')
        assets._archive_end(stream)
    return assets.read_manifest(io.BytesIO(data)), hashlib.sha256(data).hexdigest()


def extracted_manifest(cid, check, directory):
    manifest, digest = image_manifest(cid, check)
    with cp_stream(cid, '/usr/share/nginx/html/.', check) as stream:
        assets.extract_asset_archive(stream, directory, expected_files=[*manifest['documents'],
            *(item for group in manifest['sets'] for item in group['files'])])
    result = assets.verify_asset_tree(directory, manifest)
    return manifest, digest, result


def bootstrap(cid, check, scratch, previous):
    # At most512MiB payload + bounded100k-file headers/padding. Raw spool stays
    # private and is replayed through the exact same strict extractor.
    with tempfile.TemporaryFile(dir=scratch) as spool:
        with cp_stream(cid, '/usr/share/nginx/html/assets', check) as stream:
            total, maximum = 0, assets.MAX_BYTES + assets.MAX_ENTRIES * 2048 + 12288
            while chunk := stream.read(65536):
                total += len(chunk)
                require(total <= maximum, 'CAPACITY')
                spool.write(chunk)
        spool.seek(0)
        files = sorted(assets.inventory_asset_archive(spool), key=lambda item: item['path'])
        require(files, 'MANIFEST')
        spool.seek(0)
        assets.extract_asset_archive(spool, scratch / 'public', expected_files=files)
    return [{'release_id': previous, 'build_id': previous,
             'superseded_by_release_id': None, 'files': files}]


def project_name():
    project = os.environ.get('NAS_COMPOSE_PROJECT', '')
    require(re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,199}', project) is not None)
    return project


def prepare(app, candidate, output):
    validate_source_contract(Path(__file__).resolve().parents[1])
    require(re.fullmatch(IDENTITY, candidate) is not None)
    recovering = not (app / '.active-release').exists() and not (app / '.active-release').is_symlink()
    checkpoint = read_checkpoint(app) if recovering else None
    if recovering:
        failed_current(app, checkpoint)
        previous = Path(checkpoint['predecessor']['release'])
        old = metadata(previous / '.release-metadata')
    else:
        previous, old = active_release(app)
    require(candidate != previous.name)
    image = checkpoint['predecessor']['image_id'] if recovering else running(app, project_name())
    info = inspect_image(image)
    protocol = info.get('Config', {}).get('Labels', {}).get(LABEL)
    require(protocol in (None, '1'))
    claimed = old.get('web_assets_contract')
    require(claimed in (None, '1'))
    is_bootstrap = protocol is None and claimed is None
    if is_bootstrap:
        for path in (app / 'releases').iterdir():
            require(not path.is_symlink())
            file = path / '.release-metadata'
            if file.is_file():
                value = metadata(file)
                require(not (value.get('web_assets_contract') == '1'
                             and value.get('web_assets_status') == 'verified'), 'BOOTSTRAP')
    else:
        require(protocol == '1' and claimed == '1')
        old_receipt = receipt(old, previous.name)
        require(old_receipt['web_image_id'] == image)
    require(not output.exists() and not output.is_symlink())
    output.mkdir(mode=0o700)
    (output / 'public').mkdir()
    retained = output / 'retained'
    retained.mkdir(mode=0o755)
    with owned_container(image) as (cid, check):
        if is_bootstrap:
            sets = bootstrap(cid, check, output, previous.name)
            if recovering:
                require(sets[0]['files'] == checkpoint['predecessor']['legacy_files'])
            legacy_files = sets[0]['files']
            sets[0]['superseded_by_release_id'] = candidate
        else:
            manifest, digest, _ = extracted_manifest(cid, check, output / 'public')
            require(digest == old_receipt['web_assets_manifest_sha256']
                    and manifest['release_id'] == previous.name and manifest['build_id'] == previous.name
                    and manifest['input_sha256'] == old['web_assets_input_sha256']
                    and manifest['predecessor_image_id'] == old['web_predecessor_image_id'])
            receipts = {}
            for group in manifest['sets']:
                successor = group['superseded_by_release_id']
                if successor:
                    path = app / 'releases' / successor
                    if path.is_dir() and not path.is_symlink():
                        try:
                            receipts[successor] = receipt(metadata(path / '.release-metadata'), successor)
                        except (ValueError, OSError):
                            pass  # Unknown proof conservatively retains the full set.
            sets = assets.select_retained_sets(manifest, receipts, previous_release=previous.name,
                candidate_release=candidate, now=datetime.now(timezone.utc))
    value = {'schema_version': 1, 'candidate_release_id': candidate, 'predecessor_image_id': image, 'sets': sets}
    assets.validate_retained_input(value)
    unique = {item['path']: item for group in sets for item in group['files']}
    require(shutil.disk_usage(output).free >= sum(item['size'] for item in unique.values()) + assets.MAX_MANIFEST_BYTES, 'SPACE')
    for name in unique:
        destination = retained / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(output / 'public' / name, destination)
    raw = (json.dumps(value, separators=(',', ':')) + '\n').encode()
    (retained / 'manifest.json').write_bytes(raw)
    return {'predecessor_image_id': image, 'input_sha256': hashlib.sha256(raw).hexdigest(),
            'scratch': str(output), 'input_manifest': str(retained / 'manifest.json'),
            'retained_context': str(retained), 'bootstrap': is_bootstrap,
            'checkpoint_predecessor': {'release': str(previous), 'release_id': previous.name,
                'metadata_sha256': hashlib.sha256(bounded_file(previous / '.release-metadata')).hexdigest(),
                'image_id': image, 'protocol': 0 if is_bootstrap else 1,
                'manifest_sha256': None if is_bootstrap else digest,
                'legacy_files': legacy_files if is_bootstrap else None}}


def verify_image(image, input_path):
    raw = bounded_file(input_path)
    expected = assets.read_manifest(io.BytesIO(raw), retained=True)
    require(inspect_image(image).get('Config', {}).get('Labels', {}).get(LABEL) == '1')
    with tempfile.TemporaryDirectory(prefix='erp-web-verify-') as root:
        require(shutil.disk_usage(root).free >= assets.MAX_BYTES + assets.MAX_MANIFEST_BYTES, 'SPACE')
        with owned_container(image) as (cid, check):
            manifest, digest, result = extracted_manifest(cid, check, Path(root))
    require(manifest['release_id'] == expected['candidate_release_id']
            and manifest['build_id'] == expected['candidate_release_id']
            and manifest['predecessor_image_id'] == expected['predecessor_image_id']
            and manifest['input_sha256'] == hashlib.sha256(raw).hexdigest())
    require(manifest['sets'][1:] == expected['sets']
            and manifest['sets'][0]['release_id'] == expected['candidate_release_id']
            and manifest['sets'][0]['build_id'] == expected['candidate_release_id']
            and manifest['sets'][0]['superseded_by_release_id'] is None)
    return {**result, 'manifest_sha256': digest, 'image_id': image}


def verify_running(app, expected_image, expected_manifest):
    require(re.fullmatch(HEX, expected_manifest) is not None)
    require(running(app, project_name()) == expected_image)
    require(inspect_image(expected_image).get('Config', {}).get('Labels', {}).get(LABEL) == '1')
    with (tempfile.TemporaryDirectory(prefix='erp-web-running-') as root,
          owned_container(expected_image) as (cid, check)):
        manifest, digest, result = extracted_manifest(cid, check, Path(root))
    require(digest == expected_manifest)
    require(running(app, project_name()) == expected_image)
    return {**result, 'manifest_sha256': digest, 'image_id': expected_image,
            'input_sha256': manifest['input_sha256'], 'predecessor_image_id': manifest['predecessor_image_id'],
            'release_id': manifest['release_id'], 'build_id': manifest['build_id']}


def main():
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    pre = commands.add_parser('prepare')
    pre.add_argument('--app-dir', required=True, type=Path)
    pre.add_argument('--candidate-release', required=True)
    pre.add_argument('--output-dir', required=True, type=Path)
    image = commands.add_parser('verify-image')
    image.add_argument('--image-id', required=True)
    image.add_argument('--input-manifest', required=True, type=Path)
    run = commands.add_parser('verify-running')
    run.add_argument('--app-dir', required=True, type=Path)
    run.add_argument('--expected-image-id', required=True)
    run.add_argument('--expected-manifest-sha256', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.app_dir, args.candidate_release, args.output_dir)
        elif args.command == 'verify-image':
            result = verify_image(args.image_id, args.input_manifest)
        else:
            result = verify_running(args.app_dir, args.expected_image_id, args.expected_manifest_sha256)
        print(json.dumps(result, separators=(',', ':')))
    except (ValueError, KeyError, OSError, subprocess.SubprocessError, IndexError, TypeError):
        print('Web asset identity/admission gate failed.', file=sys.stderr)
        raise SystemExit(18) from None


if __name__ == '__main__':
    main()
