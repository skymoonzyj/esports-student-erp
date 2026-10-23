"""Bounded immutable Web inventories. Errors expose only stable categories.

Receipts passed to selection must already be authenticated against locked release
metadata by the deployer. Here their structure and supersession clock are checked.
Archives are uncompressed POSIX tar (no extended headers), never tar.extract().
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
import tarfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

MAX_BYTES = 536870912
MAX_MANIFEST_BYTES = 16777216
MAX_ENTRIES = 100000
SCHEMA = json.loads((Path(__file__).resolve().parents[1] /
                     'apps/web/scripts/web-assets.schema.json').read_text())
KEYWORDS = {'$schema', '$id', 'title', '$defs', '$ref', 'type', 'const', 'enum',
            'required', 'properties', 'additionalProperties', 'items', 'minItems',
            'maxItems', 'contains', 'allOf', 'anyOf', 'minimum', 'maximum',
            'maxLength', 'pattern'}


def _fail(category):
    raise ValueError(category)


def _schema(value, rule):
    """Fail-closed evaluator for this committed schema's actual keywords only."""
    if set(rule) - KEYWORDS:
        _fail('SCHEMA')
    if '$ref' in rule:
        ref = rule['$ref']
        if not ref.startswith('#/$defs/') or ref[8:] not in SCHEMA['$defs']:
            _fail('SCHEMA')
        _schema(value, SCHEMA['$defs'][ref[8:]])
    types = {'object': lambda x: type(x) is dict, 'array': lambda x: type(x) is list,
             'string': lambda x: type(x) is str, 'integer': lambda x: type(x) is int,
             'null': lambda x: x is None}
    if 'type' in rule and not any(types.get(kind, lambda _: False)(value)
                                   for kind in ([rule['type']] if isinstance(rule['type'], str) else rule['type'])):
        _fail('SCHEMA')
    if 'const' in rule and (type(value) is not type(rule['const']) or value != rule['const']):
        _fail('SCHEMA')
    if 'enum' in rule and value not in rule['enum']:
        _fail('SCHEMA')
    for child in rule.get('allOf', []):
        _schema(value, child)
    if 'anyOf' in rule:
        for child in rule['anyOf']:
            try:
                _schema(value, child)
                break
            except ValueError:
                pass
        else:
            _fail('SCHEMA')
    if type(value) is dict:
        if set(rule.get('required', [])) - value.keys():
            _fail('SCHEMA')
        properties = rule.get('properties', {})
        if rule.get('additionalProperties') is False and value.keys() - properties.keys():
            _fail('SCHEMA')
        for key in value.keys() & properties.keys():
            _schema(value[key], properties[key])
    if type(value) is list:
        if not rule.get('minItems', 0) <= len(value) <= rule.get('maxItems', MAX_ENTRIES):
            _fail('SCHEMA')
        if 'contains' in rule and not any(_matches(item, rule['contains']) for item in value):
            _fail('SCHEMA')
        if 'items' in rule:
            for item in value:
                _schema(item, rule['items'])
    if type(value) is str:
        if len(value) > rule.get('maxLength', MAX_MANIFEST_BYTES):
            _fail('SCHEMA')
        if 'pattern' in rule and re.search(rule['pattern'], value, re.ASCII) is None:
            _fail('SCHEMA')
    if type(value) is int and not rule.get('minimum', value) <= value <= rule.get('maximum', value):
        _fail('SCHEMA')


def _matches(value, rule):
    try:
        _schema(value, rule)
        return True
    except ValueError:
        return False


def _path(value):
    if (type(value) is not str or len(value) > 1024 or not value.isascii()
            or any(part in ('', '.', '..') for part in value.split('/'))
            or '\\' in value or value.startswith('/')):
        _fail('PATH')
    if value not in ('index.html', 'build-info.json') and not _matches(value,
            SCHEMA['$defs']['set']['properties']['files']['items']['allOf'][1]['properties']['path']):
        _fail('PATH')


def validate_files(files: list[dict], *, max_bytes=MAX_BYTES) -> int:
    """Validate a flattened inventory; identical cross-set cache keys deduplicate."""
    if type(files) is not list or len(files) > MAX_ENTRIES:
        _fail('SCHEMA')
    unique = {}
    total = 0
    for item in files:
        if type(item) is dict and type(item.get('size')) is int and item['size'] > max_bytes:
            _fail('CAPACITY')
        _schema(item, SCHEMA['$defs']['file'])
        _path(item['path'])
        previous = unique.get(item['path'])
        if previous is not None and previous != item:
            _fail('CONFLICT')
        if previous is None:
            unique[item['path']] = item
            total += item['size']
            if total > min(max_bytes, MAX_BYTES):
                _fail('CAPACITY')
    return total


def _inventory(data, retained=False):
    _schema(data, SCHEMA['$defs']['retainedInput'] if retained else SCHEMA)
    files = list(data.get('documents', []))
    releases = set()
    for item in data['sets']:
        if item['release_id'] in releases or len({f['path'] for f in item['files']}) != len(item['files']):
            _fail('CONFLICT')
        releases.add(item['release_id'])
        files.extend(item['files'])
        if len(files) > MAX_ENTRIES:
            _fail('SCHEMA')
    total = validate_files(files)
    if not retained and (total != data['total_bytes'] or len({f['path'] for f in data['documents']}) != 2):
        _fail('SCHEMA')
    try:
        if len(json.dumps(data, ensure_ascii=True, separators=(',', ':')).encode()) > MAX_MANIFEST_BYTES:
            _fail('SCHEMA')
    except (RecursionError, TypeError, OverflowError):
        _fail('SCHEMA')
    return data


def validate_retained_input(data: dict) -> dict:
    return _inventory(data, retained=True)


def validate_manifest(data: dict) -> dict:
    return _inventory(data)


def _object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            _fail('SCHEMA')
        value[key] = item
    return value


def read_manifest(stream: BinaryIO, *, retained=False) -> dict:
    """Read at most 16 MiB+1 and validate either final or independent input schema."""
    try:
        raw = _read_bounded(stream, MAX_MANIFEST_BYTES)
        if not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES:
            _fail('SCHEMA')
        value = json.loads(raw, object_pairs_hook=_object)
        return validate_retained_input(value) if retained else validate_manifest(value)
    except (UnicodeError, json.JSONDecodeError, RecursionError, OSError):
        _fail('SCHEMA')


def select_retained_sets(manifest: dict, receipts: dict[str, dict], *,
                         previous_release: str, candidate_release: str, now: datetime) -> list[dict]:
    validate_manifest(manifest)
    for release in (previous_release, candidate_release):
        _schema(release, SCHEMA['$defs']['identity'])
    if (manifest['release_id'] != previous_release or previous_release == candidate_release
            or now.tzinfo is None or now.utcoffset() is None
            or not any(s['release_id'] == previous_release for s in manifest['sets'])
            or any(s['release_id'] == candidate_release for s in manifest['sets'])):
        _fail('SCHEMA')
    retained = []
    for item in manifest['sets']:
        keep = True
        successor = item['superseded_by_release_id']
        if item['release_id'] != previous_release and successor:
            entry = receipts.get(successor)
            if _matches(entry, SCHEMA['$defs']['activationReceipt']) and entry['release_id'] == successor:
                try:
                    activated = datetime.fromisoformat(entry['activated_verified_utc'].replace('Z', '+00:00'))
                    if activated.tzinfo is not None and activated.utcoffset() is not None and activated <= now:
                        keep = now - activated <= timedelta(days=7)
                except (ValueError, OverflowError):
                    pass
        if keep:
            retained.append(copy.deepcopy(item))
            if item['release_id'] == previous_release:
                retained[-1]['superseded_by_release_id'] = candidate_release
    return retained


def _open_root(root):
    return os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def _parent(root_fd, relative, *, create=False):
    current = os.dup(root_fd)
    try:
        for part in relative.split('/')[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            os.close(current)
            current = child
        return current
    except BaseException:
        os.close(current)
        raise


def _hash_file(root_fd, item):
    parent = _parent(root_fd, item['path'])
    try:
        fd = os.open(item['path'].split('/')[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    with os.fdopen(fd, 'rb') as source:
        info = os.fstat(source.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            _fail('TREE')
        if info.st_size != item['size']:
            _fail('HASH')
        digest = hashlib.sha256()
        count = 0
        while chunk := source.read(min(65536, item['size'] - count + 1)):
            count += len(chunk)
            if count > item['size']:
                _fail('HASH')
            digest.update(chunk)
        if count != item['size'] or digest.hexdigest() != item['sha256']:
            _fail('HASH')


def verify_asset_tree(root: Path, manifest: dict) -> dict:
    validate_manifest(manifest)
    expected = {item['path']: item for item in manifest['documents']}
    expected.update((item['path'], item) for group in manifest['sets'] for item in group['files'])
    try:
        fd = _open_root(root)
        try:
            found = set()
            def walk(directory, prefix=''):
                with os.scandir(directory) as entries:
                    names = [entry.name for entry in entries]
                for entry_name in names:
                    name = prefix + entry_name
                    info = os.stat(entry_name, dir_fd=directory, follow_symlinks=False)
                    if stat.S_ISDIR(info.st_mode) and any(p.startswith(name + '/') for p in expected):
                        child = os.open(entry_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                        try:
                            walk(child, name + '/')
                        finally:
                            os.close(child)
                    elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and name in expected:
                        found.add(name)
                    else:
                        _fail('TREE')
            walk(fd)
            if found != expected.keys():
                _fail('TREE')
            for item in expected.values():
                _hash_file(fd, item)
        finally:
            os.close(fd)
    except OSError:
        _fail('TREE')
    return {'total_bytes': manifest['total_bytes'], 'file_count': len(expected)}


def _read_exact(stream, size):
    chunks = []
    while size:
        chunk = stream.read(min(size, 65536))
        if not chunk:
            _fail('ARCHIVE')
        chunks.append(chunk)
        size -= len(chunk)
    return b''.join(chunks)


def _read_bounded(stream, limit):
    chunks, total = [], 0
    while total <= limit:
        chunk = stream.read(min(65536, limit - total + 1))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b''.join(chunks)


def _archive_header(raw):
    try:
        member = tarfile.TarInfo.frombuf(raw, 'ascii', 'strict')
    except (tarfile.HeaderError, UnicodeError, ValueError):
        _fail('ARCHIVE')
    name = member.name.removeprefix('./')
    if not name or name.startswith('/'):
        _fail('PATH')
    if member.isdir():
        if member.size != 0:
            _fail('ARCHIVE')
    elif member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
        _fail('ARCHIVE')
    else:
        _path(name)
    return member, name


def _archive_end(stream):
    if _read_exact(stream, 512) != b'\0' * 512:
        _fail('ARCHIVE')
    padding = _read_bounded(stream, 10240)
    if len(padding) > 10240 or padding.strip(b'\0'):
        _fail('ARCHIVE')


def inventory_asset_archive(stream: BinaryIO) -> list[dict]:
    """Inventory only an assets/ Docker-cp stream; never extract or admit HTML.

    Caller spools raw bytes with a bounded writer, rewinds, and passes this exact
    inventory and original stream through extract_asset_archive. No repacking.
    """
    files, seen, directories, total = [], set(), [], 0
    while True:
        raw = _read_exact(stream, 512)
        if raw == b'\0' * 512:
            _archive_end(stream)
            break
        if len(seen) >= MAX_ENTRIES * 2:
            _fail('ARCHIVE')
        member, name = _archive_header(raw)
        key = name.rstrip('/') if member.isdir() else name
        if key in seen:
            _fail('ARCHIVE')
        seen.add(key)
        if member.isdir():
            if name not in ('.', 'assets', 'assets/') and not name.startswith('assets/'):
                _fail('PATH')
            directories.append(name.rstrip('/'))
            continue
        item = {'path': name, 'size': member.size, 'sha256': '0' * 64}
        validate_files([item])
        _schema(item['path'], SCHEMA['$defs']['set']['properties']['files']['items']['allOf'][1]['properties']['path'])
        total += member.size
        if total > MAX_BYTES or len(files) >= MAX_ENTRIES:
            _fail('CAPACITY')
        digest, left = hashlib.sha256(), member.size
        while left:
            chunk = _read_exact(stream, min(left, 65536))
            digest.update(chunk)
            left -= len(chunk)
        _read_exact(stream, (-member.size) % 512)
        item['sha256'] = digest.hexdigest()
        files.append(item)
    if any(directory != '.' and not any(f['path'].startswith(directory + '/') for f in files)
           for directory in directories):
        _fail('ARCHIVE')
    return files


def extract_asset_archive(stream: BinaryIO, destination: Path, *,
                          expected_files: list[dict], max_bytes: int = MAX_BYTES) -> dict:
    total = validate_files(expected_files, max_bytes=max_bytes)
    expected = {item['path']: item for item in expected_files}
    seen = set()
    seen_headers = set()
    written = 0
    try:
        fd = _open_root(destination)
        try:
            if os.listdir(fd):
                _fail('DESTINATION')
            headers = 0
            while True:
                raw = _read_exact(stream, 512)
                if raw == b'\0' * 512:
                    _archive_end(stream)
                    break
                headers += 1
                if headers > MAX_ENTRIES * 2:
                    _fail('ARCHIVE')
                member, name = _archive_header(raw)
                header_name = (name.rstrip('/') or '.') if member.isdir() else name
                if header_name in seen_headers:
                    _fail('ARCHIVE')
                seen_headers.add(header_name)
                if member.isdir():
                    directory = name.rstrip('/')
                    if member.size != 0 or (directory not in ('', '.') and not any(p.startswith(directory + '/') for p in expected)):
                        _fail('ARCHIVE')
                    continue
                if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE):
                    _fail('ARCHIVE')
                _path(name)
                if name not in expected or name in seen or member.size != expected[name]['size']:
                    _fail('ARCHIVE')
                if written + member.size > min(max_bytes, MAX_BYTES):
                    _fail('CAPACITY')
                seen.add(name)
                parent = _parent(fd, name, create=True)
                try:
                    output_fd = os.open(name.split('/')[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                        0o644, dir_fd=parent)
                finally:
                    os.close(parent)
                with os.fdopen(output_fd, 'wb') as output:
                    left = member.size
                    while left:
                        chunk = _read_exact(stream, min(left, 65536))
                        written += len(chunk)
                        if written > min(max_bytes, MAX_BYTES):
                            _fail('CAPACITY')
                        output.write(chunk)
                        left -= len(chunk)
                _read_exact(stream, (-member.size) % 512)
                _hash_file(fd, expected[name])
            if seen != expected.keys():
                _fail('ARCHIVE')
            # A later archive read must not invalidate an earlier file's proof.
            for item in expected.values():
                _hash_file(fd, item)
        finally:
            os.close(fd)
    except OSError:
        _fail('DESTINATION')
    return {'total_bytes': total, 'file_count': len(expected)}
