#!/usr/bin/env python3
"""Own four disposable services and collect production loading evidence, never NAS.

The baseline is always a git archive of the approved product commit. Candidate
sources must be committed local Git trees. No caller-selected tests or URLs.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

BASELINE_SHA = '95da4227ae831eeb4edfd75d3fc31bcdaa8882ae'
ROOT = Path(__file__).resolve().parents[1]
SERVICES = ('db', 'api', 'web', 'proxy')
DEPENDENCY_IMAGE_TAG = 'esports-erp-e2e-api:latest'
NAMED_RUNNERS = {'module-recovery': 'run-module-recovery.mjs', 'style-order': 'run-style-order.mjs', 'route-loading': 'run-route-loading.mjs', 'talent-games': 'run-talent-games.mjs', 'optional-panels': 'run-optional-panels.mjs', 'esports-games': 'run-esports-games.mjs', 'state-safety': 'run-state-safety.mjs'}
NAMED_RUNNERS['legacy-functional'] = None
NAMED_RUNNERS['esports-capabilities-diagnostic'] = 'run-esports-games.mjs'
NAMED_RUNNERS['direct-document-diagnostic'] = 'run-direct-document.mjs'
DIRECT_PRODUCT_BASE = 'd37c5f6959901e108f3c9873e004d8580bbf7b4e'
DIRECT_TOOL_FILES = (
    'scripts/run-frontend-loading-acceptance.py', 'scripts/test_frontend_loading_acceptance.py',
    'apps/web/scripts/run-direct-document.mjs', 'apps/web/scripts/direct-document-diagnostic.test.mjs',
    'apps/web/scripts/direct-document-journey.test.mjs', 'apps/web/scripts/direct-document-runner.test.mjs',
    'apps/web/tests/helpers/directDocumentDiagnostic.mjs', 'apps/web/tests/helpers/directDocumentJourney.ts',
    'apps/web/tests/frontend-direct-document.spec.ts', 'apps/web/playwright.direct-document.config.ts',
    'apps/web/tests/helpers/readyChainDiagnostic.mjs',
)
DIRECT_HARNESS_FILES = (*DIRECT_TOOL_FILES,
    'apps/web/playwright.loading.config.ts', 'apps/web/package-lock.json')
LEGACY_SUITES = (('assisted', 2), ('team', 2), ('registration', 8), ('housing-occupancy', 8), ('housing-reporting', 8))
ESPORTS_MATRIX = (
    ('esports-pc.spec.ts', 'esports-desktop-1440', 'pc'),
    ('esports-touch.spec.ts', 'esports-touch-844x390', 'touch'),
    ('esports-recovery.spec.ts', 'esports-desktop-1440', 'recovery'),
    ('esports-capabilities.spec.ts', 'esports-desktop-1440', 'capabilities-desktop'),
    ('esports-capabilities.spec.ts', 'esports-touch-844x390', 'capabilities-touch'),
    ('esports-capabilities.spec.ts', 'esports-webkit-capabilities', 'capabilities-webkit'),
)
TALENT_MATRIX = (
    ('talent-tests.spec.ts', 'desktop-1440', None, 'double-full'),
    ('talent-touch.spec.ts', 'touch-mobile-390', None, 'full'),
    ('talent-dynamic-vision.spec.ts', 'desktop-1440', '1', 'dynamic'),
    ('talent-dynamic-vision.spec.ts', 'desktop-1024', '1', 'dynamic'),
    ('talent-dynamic-vision.spec.ts', 'tablet-768', '1', 'dynamic'),
    ('talent-dynamic-vision.spec.ts', 'mobile-360', '1', 'dynamic'),
    ('talent-dynamic-vision.spec.ts', 'touch-mobile-390', '1', 'dynamic'),
)


class Refused(Exception):
    pass


def validate_release_message(message, expected):
    if expected not in ('ready-A', 'ready-D') or message != {'stage': expected}:
        raise Refused('unknown or out-of-order private release barrier')


def validate_otp_request(value, sequence):
    aliases = {'dst_' + hashlib.sha256(('registration-test-destination\0' + address).encode()).hexdigest()[:24]
               for address in ('registration-adult-e2e@example.com', 'registration-loading-touch@example.com', 'assisted-seed-1@example.com')}
    if (type(value) is not dict or set(value) != {'sequence', 'purpose', 'challengeId', 'destinationAlias'}
            or type(value['sequence']) is not int or value['sequence'] != sequence or not 1 <= sequence <= 8
            or value['purpose'] not in ('registration', 'assisted_activation')
            or not isinstance(value['destinationAlias'], str) or value['destinationAlias'] not in aliases
            or (value['challengeId'] is not None and (not isinstance(value['challengeId'], str)
                or not re.fullmatch(r'[a-f0-9]{8}-(?:[a-f0-9]{4}-){3}[a-f0-9]{12}', value['challengeId'])))
            or (value['purpose'] == 'registration' and value['challengeId'] is None)):
        raise Refused('private OTP bridge refused fixed schema/recipient/sequence')


class OtpBridge:
    """Eight bounded private messages; no caller-selected commands or recipients."""
    def __init__(self, directory, docker, owned_api):
        self.directory, self.docker, self.owned_api = directory, docker, owned_api
        self.sequence = 1

    def tick(self):
        request = self.directory / 'otp-request.json'
        try: metadata = request.lstat()
        except FileNotFoundError: return
        folder = self.directory.lstat()
        response = self.directory / 'otp-response.json'
        if (not stat.S_ISDIR(folder.st_mode) or folder.st_uid != os.getuid() or stat.S_IMODE(folder.st_mode) != 0o700
                or not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1 or metadata.st_size > 512
                or response.exists()):
            raise Refused('private OTP bridge file ownership/mode/order refused')
        descriptor = os.open(request, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as content:
            opened = os.fstat(content.fileno())
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise Refused('private OTP request changed during read')
            value = json.loads(content.read(513))
        validate_otp_request(value, self.sequence)
        api_id = self.owned_api()
        command = ['exec', '--user', '10001:10001', api_id, 'python', '-m', 'app.cli.read_registration_test_code',
                   '--purpose', value['purpose'], '--destination-alias', value['destinationAlias']]
        if value['challengeId'] is not None: command.extend(['--challenge-id', value['challengeId']])
        code = self.docker(*command, capture=True).strip()
        if not re.fullmatch(r'[0-9]{6}', code): raise Refused('private OTP reader did not produce a code')
        request.unlink()
        temporary = response.with_suffix('.tmp')
        private_json(temporary, {'sequence': self.sequence, 'code': code})
        temporary.replace(response)
        self.sequence += 1


def patch_release_fixture(snapshot, marker):
    if marker not in ('A', 'B', 'C', 'D'):
        raise Refused('unknown fixed archive release marker')
    changes = (
        ('apps/web/src/esportsAssessments/AssessmentHistoryPage.tsx',
         '<div className="assessmentReports">', f'<div className="assessmentReports" data-release-window="{marker}">'),
        ('apps/web/src/esportsAssessments/reports.css',
         '.assessmentReports {', f'.assessmentReports {{\n  --erp-release-fixture: {marker};'),
    )
    originals = [(relative, (snapshot / relative).read_text(), old, new) for relative, old, new in changes]
    if any(source.count(old) != 1 or 'release-window=' in source or '--erp-release-fixture:' in source
           for _relative, source, old, _new in originals):
        raise Refused('archive marker source/count mismatch')
    result = []
    for relative, source, old, new in originals:
        updated = source.replace(old, new)
        (snapshot / relative).write_text(updated)
        result.append({'path': relative, 'replacements': 1,
            'originalSha256': hashlib.sha256(source.encode()).hexdigest(),
            'patchedSha256': hashlib.sha256(updated.encode()).hexdigest()})
    return result


def diagnostic_categories(raw):
    # Never copy a Docker/build log line: it can contain paths, build args,
    # credentials, URLs or source. Only these fixed classifications escape.
    patterns = {
        'build_context_unavailable': r'context .*does not exist|unable to get buildx instance|failed to find driver',
        'build_image_metadata': r'failed to resolve source metadata|pull access denied|failed to authorize',
        'build_solver': r'failed to solve',
        'buildx_unavailable': r'buildx.*(?:not found|not installed|requires)|requires.*buildx',
        'build_bake': r'bake|bake definition',
        'build_context_files': r'failed to (?:compute cache key|calculate checksum|read dockerfile)|lstat|unable to prepare context',
        'build_target': r'target stage.*could not be found',
        'build_retained_input': r'erp_retained_assets|retained.*(?:invalid|missing|refused)',
        'daemon_unavailable': r'cannot connect to the docker daemon|connection refused',
        'disk_capacity': r'no space left|disk quota exceeded',
        'network_lookup': r'no such host|network is unreachable|i/o timeout|TLS handshake timeout',
        'npm_dependency': r'npm (?:ERR!|error)',
        'filesystem_permission': r'permission denied|operation not permitted',
    }
    result = {name for name, pattern in patterns.items() if re.search(pattern, raw, re.IGNORECASE)}
    result.update('typescript_' + code for code in re.findall(r'error (TS\d{4,6}):', raw))
    return sorted(result)


def validate_environment(env):
    prefixes = ('DATABASE_', 'TEST_DATABASE_', 'SMTP_', 'MAIL_', 'VITE_', 'COMPOSE_',
                'SESSION_', 'FIELD_', 'REGISTRATION_', 'EVALUATION_', 'ERP_')
    forbidden = {'APP_ENV', 'NODE_OPTIONS', 'NODE_ENV', 'PYTHONPATH', 'BASH_ENV', 'ENV'}
    if any(key in forbidden or key.startswith(prefixes) for key in env):
        raise Refused('inherited application, bind, or Compose configuration forbidden')


def validate_dotenv(root):
    for directory in (root, root / 'apps/web'):
        for name in ('.env', '.env.local', '.env.production', '.env.production.local',
                     '.env.development', '.env.development.local', '.env.test', '.env.test.local'):
            try:
                (directory / name).lstat()
            except FileNotFoundError:
                continue
            raise Refused('dotenv forbidden in isolated source')


def verify_source(path, *, baseline):
    path = path.resolve(strict=True)
    validate_dotenv(path)
    sha = BASELINE_SHA if baseline else 'HEAD'
    result = subprocess.run(['git', '-C', str(path), 'rev-parse', '--verify', sha + '^{commit}'],
                            capture_output=True, check=False)
    if result.returncode or (baseline and result.stdout.decode().strip() != BASELINE_SHA):
        raise Refused('approved local source commit unavailable')
    if not baseline and subprocess.run(['git', '-C', str(path), 'diff', '--quiet', 'HEAD'],
                                       capture_output=True, check=False).returncode:
        raise Refused('candidate tracked source must be committed')
    return result.stdout.decode().strip()


def validate_ownership(containers, project, owner, ids):
    for container in containers:
        labels = container.get('Config', {}).get('Labels', {}) or {}
        if (container['Id'] not in ids or labels.get('com.docker.compose.project') != project
                or labels.get('erp.loading.owner') != owner):
            raise Refused('foreign or unverified container; no cleanup or reuse allowed')


class Supervisor:
    def __init__(self):
        self.children = []

    def run(self, args, *, cwd=None, env=None, log=None, pidfile=None, capture=False, on_tick=None):
        with ExitStack() as handles:
            mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
            try:
                output = handles.enter_context(tempfile.TemporaryFile()) if capture else handles.enter_context(log.open('ab')) if log else None
                # Single-threaded supervisor: child must restore its signal mask
                # before exec; start_new_session keeps ownership unambiguous.
                process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                    stdout=output or subprocess.DEVNULL,
                    stderr=output or subprocess.DEVNULL, start_new_session=True,
                    preexec_fn=lambda: signal.pthread_sigmask(signal.SIG_SETMASK, mask))  # noqa: PLW1509
                self.children.append(process)
                if pidfile:
                    Path(pidfile).write_text(str(process.pid))
            finally:
                signal.pthread_sigmask(signal.SIG_SETMASK, mask)
            # Do not reap the group leader before killing descendants: retaining
            # its zombie protects the session/group ID from unrelated PID reuse.
            while not self.finished(process):
                if on_tick is not None:
                    on_tick()
                time.sleep(.05)
            self.signal_group(process, signal.SIGKILL)
            process.wait()
            self.children.remove(process)
            if process.returncode:
                diagnostics = []
                if log and log.exists():
                    raw = log.read_text(errors='replace')[-20000:]
                    diagnostics = sorted(set(re.findall(r'\b[A-Za-z_]+Error\b|connection refused|password authentication failed', raw)))
                    diagnostics.extend('missing_module_' + module for module in re.findall(r"No module named '([A-Za-z0-9_.]+)'", raw))
                    diagnostics.extend(diagnostic_categories(raw))
                raise Refused(f'isolated {Path(args[0]).name} failed (exit {process.returncode}); categories={",".join(diagnostics)}; private output discarded')
            if capture:
                output.seek(0)
                return output.read().decode()
            return ''

    @staticmethod
    def finished(process):
        if hasattr(os, 'waitid'):
            return os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
        status = subprocess.run(['/bin/ps', '-o', 'stat=', '-p', str(process.pid)],
                                capture_output=True, text=True, env={'PATH': '/usr/bin:/bin'}, check=False).stdout.strip()
        return not status or status.startswith('Z')

    def signal_group(self, process, signum):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        except PermissionError:
            if not self.finished(process):
                raise

    def close(self):
        for process in self.children:
            self.signal_group(process, signal.SIGTERM)
        # The fixed legacy launcher owns separate child sessions and needs to
        # complete its own bounded finally before its supervisor is reaped.
        deadline = time.monotonic() + 5
        while self.children and not all(self.finished(process) for process in self.children):
            if time.monotonic() >= deadline: break
            time.sleep(.05)
        for process in self.children:
            self.signal_group(process, signal.SIGKILL)
            process.wait()
        self.children.clear()


def private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'w') as output:
        json.dump(value, output, indent=2, sort_keys=True)


def publish_release_activation(directory, marker, releases):
    if marker not in ('C', 'D'):
        raise Refused('unknown release activation message')
    destination = directory / f'active-{marker}.json'
    temporary = destination.with_suffix('.json.tmp')
    private_json(temporary, {'releases': releases})
    os.replace(temporary, destination)


def build_owned_services(compose_run, docker, expected_image_id):
    def check_identity():
        current = docker('image', 'inspect', DEPENDENCY_IMAGE_TAG, '--format', '{{.Id}}', capture=True).strip()
        if not current or current != expected_image_id:
            raise Refused('local dependency tag identity changed or missing; no services created')
    check_identity()
    # FROM sha256:<local-ID> is interpreted by BuildKit as library/sha256.
    # Keep a fixed existing local reference while checking its immutable ID on
    # both sides of the non-pulling source build, before any service is created.
    compose_run('build', '--pull=false', '--build-arg',
                'ERP_DEPENDENCY_IMAGE=' + DEPENDENCY_IMAGE_TAG, 'api', 'web')
    check_identity()


def free_port():
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        return probe.getsockname()[1]


def extract_snapshot(archive, snapshot):
    with tarfile.open(archive) as content:
        content.extractall(snapshot, filter='data')
    # Python's data filter deliberately discards archive directory modes. Under
    # umask077 they become0700 and Docker COPY then blocks uid10001 imports.
    # The enclosing private runtime remains0700; only build-tree dirs are0755.
    for directory in snapshot.rglob('*'):
        if directory.is_dir() and not directory.is_symlink():
            directory.chmod(0o755)


def create_runtime_secrets():
    return {name: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode() for name in (
        'FIELD_ENCRYPTION_MASTER_KEY', 'FIELD_LOOKUP_KEY', 'SESSION_TOKEN_PEPPER',
        'REGISTRATION_VERIFICATION_PEPPER', 'REGISTRATION_SESSION_TOKEN_PEPPER',
        'EVALUATION_TOOL_SERVICE_TOKEN', 'EVALUATION_TOOL_WEB_SERVICE_TOKEN', 'EVALUATION_TOOL_WORKER_SERVICE_TOKEN')}


def prepare_evidence(path, phase, *, check='module-recovery'):
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if phase == 'named' and check not in NAMED_RUNNERS:
        raise Refused('unknown named check; no services created')
    label = check if phase == 'named' else phase if phase in ('baseline', 'release-window') else 'candidate'
    if any((path / (label + '.json' + suffix)).exists() for suffix in ('', '.tmp', '.progress.json', '.smoke.json')):
        raise Refused('prior evidence exists; preserve failures and use a new evidence directory')
    if ((phase == 'release-window' and any((path / name).exists() for name in ('release-window-owner.json', 'release-window-stage.json')))
            or (check == 'state-safety' and any((path / name).exists() for name in ('state-email-preflight.json', 'state-record-counts.json')))):
        raise Refused('prior owner evidence exists; preserve failures and use a new evidence directory')


def collect_candidate(source, sha, evidence, runtime, supervisor, base_env, browser_path):
    baseline = evidence / 'baseline.json'
    if not baseline.is_file() or json.loads(baseline.read_text())['fingerprint']['sourceCommit'] != BASELINE_SHA:
        raise Refused('verified baseline evidence required')
    proxy_image = supervisor.run(['docker', 'image', 'inspect', 'nginxinc/nginx-unprivileged:1.27-alpine',
        '--format', '{{.Id}}'], env=base_env, capture=True).strip()
    supervisor.run(['node', str(ROOT / 'apps/web/scripts/measure-loading.mjs'), '--preflight-baseline', str(baseline),
        hashlib.sha256(PROXY.encode()).hexdigest(), proxy_image],
        env={**base_env, 'PLAYWRIGHT_BROWSERS_PATH': browser_path})
    collect(source, sha, evidence, 'candidate', runtime, supervisor, base_env, browser_path)
    supervisor.run(['node', str(ROOT / 'apps/web/scripts/measure-loading.mjs'), '--compare', str(evidence)], env=base_env)


def direct_source_identity(source, sha):
    """New archive identity, unchanged product inputs; never relabel it d37c5f6."""
    if verify_source(source, baseline=False) != sha:
        raise Refused('frozen direct candidate changed')
    def git(*args):
        return subprocess.check_output(['git', '-C', str(source), *args], text=True).strip()
    if git('ls-files', '--others', '--exclude-standard'):
        raise Refused('direct candidate has untracked non-ignored source')
    changed = git('diff', '--name-only', DIRECT_PRODUCT_BASE, sha, '--').splitlines()
    if any(name not in DIRECT_TOOL_FILES for name in changed):
        raise Refused('direct diagnostic candidate product/build inputs differ from approved base')
    tool_commit = verify_source(ROOT, baseline=False)
    hashes = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in DIRECT_HARNESS_FILES}
    return {'productBase': DIRECT_PRODUCT_BASE, 'candidateCommit': sha, 'toolCommit': tool_commit,
        'productInputsUnchanged': True, 'toolFilesSha256': hashes,
        'harnessSha256': hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def collect_direct_pair(baseline_source, baseline_sha, candidate_source, candidate_sha,
                        evidence, runtime, supervisor, base_env, browser_path):
    """One bounded pair. collect owns archive/build/financial checks and cleanup."""
    if baseline_sha != BASELINE_SHA:
        raise Refused('direct baseline is fixed')
    identity = direct_source_identity(candidate_source, candidate_sha)
    evidence.mkdir(parents=True, exist_ok=True, mode=0o700)
    output = evidence / 'direct-document-diagnostic.json'
    if output.exists() or any((evidence / name).exists() for name in ('direct-baseline', 'direct-candidate')):
        raise Refused('direct diagnostic evidence exists; preserve original result')
    result = {'schemaVersion': 1, 'kind': 'non-performance', 'check': 'direct-document-diagnostic',
        'ok': False, 'identity': identity, 'arms': [
            {'arm': label, 'sourceCommit': sha, 'status': 'not_run', 'cleanup': 'not_run'}
            for label, sha in (('direct-baseline', baseline_sha), ('direct-candidate', candidate_sha))]}
    private_json(output, result)
    observed = []
    for index, (source, sha) in enumerate(((baseline_source, baseline_sha), (candidate_source, candidate_sha))):
        arm = result['arms'][index]
        if verify_source(source, baseline=index == 0) != sha or direct_source_identity(candidate_source, candidate_sha) != identity:
            raise Refused('direct frozen source or harness drift before lifecycle')
        directory = evidence / arm['arm']
        directory.mkdir(mode=0o700)
        arm.update(status='running', cleanup='unconfirmed'); private_json(output, result)
        handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)}
        try:
            # Returns only after the existing owned scope has completed cleanup.
            collect(source, sha, directory, arm['arm'], runtime, supervisor, base_env, browser_path,
                    check='direct-document-diagnostic')
            arm['cleanup'] = 'complete'
            data = json.loads((directory / (arm['arm'] + '.json')).read_text())
            if not data.get('ok') or data.get('fingerprint', {}).get('sourceCommit') != sha:
                raise Refused('direct arm incomplete or source binding missing')
            if direct_source_identity(candidate_source, candidate_sha) != identity:
                raise Refused('direct source/harness drift during lifecycle')
            observed.append(data)
            arm['status'] = 'complete'
        except BaseException:
            arm['status'] = 'failed'
            raise
        finally:
            # Existing cleanup ignores interruption while removing its resources.
            # Restore admission's handlers before starting the second lifecycle.
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
            private_json(output, result)
    baseline, candidate = observed
    if any(baseline['fingerprint'].get(key) != candidate['fingerprint'].get(key) for key in ('dependencyImage', 'proxySha256')) \
            or baseline['fingerprint']['images']['proxy'] != candidate['fingerprint']['images']['proxy'] \
            or baseline.get('browserVersion') != candidate.get('browserVersion'):
        raise Refused('direct pair environment identity differs')
    result['ok'] = True
    private_json(output, result)


@contextmanager
def owned_cleanup_scope(docker, containers, project, owner, ids, supervisor):
    try:
        yield
    finally:
        primary = sys.exc_info()[1]
        for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, signal.SIG_IGN)
        try:
            supervisor.close()
            cleanup_owned_resources(docker, containers, project, owner, ids)
        except Exception:
            if primary is None:
                raise
            print('Loading cleanup failed; original failure preserved; owned resources require inspection', file=sys.stderr)


def cleanup_owned_resources(docker, containers, project, owner, ids):
    # create may have failed or been interrupted before returning any IDs.
    # Reconcile only marker-verified containers after the create process stops.
    current = containers()
    verified = []
    for container in current:
        identifier = container['Id']
        try:
            validate_ownership([container], project, owner, {identifier})
        except Refused:
            continue
        ids.add(identifier)
        verified.append(container)
    validate_ownership(verified, project, owner, ids)
    if verified:
        shared = any(container.get('HostConfig', {}).get('NetworkMode', '').startswith('container:') for container in verified)
        if shared:
            for container in sorted(verified, key=lambda row: 0 if row.get('Config', {}).get('Labels', {}).get('com.docker.compose.service') == 'proxy' else 1):
                docker('rm', '-f', container['Id'])
        else:
            docker('rm', '-f', *(container['Id'] for container in verified))
    if len(verified) != len(current):
        raise Refused('foreign project container left untouched')
    volume_ids = docker('volume', 'ls', '-q', '--filter', f'label=com.docker.compose.project={project}', capture=True).split()
    for volume in volume_ids:
        info = json.loads(docker('volume', 'inspect', volume, capture=True))[0]
        if info.get('Labels', {}).get('com.docker.compose.project') != project or info['Name'] != volume:
            raise Refused('volume ownership mismatch')
        docker('volume', 'rm', volume)
    network_ids = docker('network', 'ls', '-q', '--no-trunc', '--filter', f'label=com.docker.compose.project={project}', capture=True).split()
    for network in network_ids:
        info = json.loads(docker('network', 'inspect', network, capture=True))[0]
        if info.get('Labels', {}).get('com.docker.compose.project') != project or info['Id'] != network:
            raise Refused('network ownership mismatch')
        docker('network', 'rm', network)


PROXY = '''worker_processes 1;
pid /tmp/nginx.pid;
events { worker_connections 1024; }
http {
 include /etc/nginx/mime.types;
 default_type application/octet-stream;
 access_log off;
 error_log /dev/stderr warn;
 client_body_temp_path /tmp/client_temp;
 proxy_temp_path /tmp/proxy_temp;
 fastcgi_temp_path /tmp/fastcgi_temp;
 uwsgi_temp_path /tmp/uwsgi_temp;
 scgi_temp_path /tmp/scgi_temp;
 gzip on;
 gzip_vary on;
 gzip_proxied any;
 gzip_min_length 256;
 gzip_types text/css application/javascript application/json image/svg+xml;
 server {
  listen 8080;
  location = /healthz { return 200 "ok"; }
  location /api/ {
   proxy_pass http://api:8000;
   proxy_set_header Host $host;
   proxy_set_header X-Forwarded-Proto http;
  }
  location / {
   proxy_pass http://web:5173;
   proxy_set_header Host $host;
   proxy_set_header Accept-Encoding "";
  }
 }
}
'''

EMAIL_PROXY = PROXY.replace('proxy_pass http://api:8000;', 'proxy_pass http://127.0.0.1:8000;\n'
    '   proxy_set_header Forwarded "";\n   proxy_set_header X-Forwarded-For "";\n   proxy_set_header X-Real-IP "";')

EMAIL_PREFLIGHT = '''import os,stat,json
from pathlib import Path
from sqlalchemy import text
from starlette.requests import Request
from app.main import app
from app.core.database import SessionLocal
from app.registration.dependencies import get_registration_verification_sender
from app.registration.delivery import TestMailboxVerificationSender
assert os.getuid()==10001
assert not any(key.startswith(('SMTP_','MAIL_')) for key in os.environ)
assert app.state.settings.app_env=='test'
for name,mode in [('/loading-mailbox',0o700),('/loading-mailbox/.registration-test-mailbox',0o600),('/loading-mailbox/verification-mailbox.jsonl',0o600)]:
 p=Path(name); s=p.lstat(); assert not p.is_symlink() and s.st_uid==10001 and stat.S_IMODE(s.st_mode)==mode
with SessionLocal() as db:
 assert db.execute(text('SELECT count(*) FROM mail_delivery_settings')).scalar_one()==0
 request=Request({'type':'http','client':('127.0.0.1',12345),'app':app,'headers':[(b'x-forwarded-for',b'203.0.113.1'),(b'forwarded',b'for=203.0.113.1')]})
 assert isinstance(get_registration_verification_sender(request,db),TestMailboxVerificationSender)
print(json.dumps({'uidVerified':True,'privateModesVerified':True,'smtpSettingsAbsent':True,'loopbackSenderVerified':True,'forwardedHeaderIgnored':True}))
'''

STATE_COUNTS = '''import json
from sqlalchemy import text
from app.core.database import SessionLocal
queries={'accounts':'user_accounts','activeAccounts':"user_accounts WHERE status='active'",'people':'people',
 'applications':'registration_applications','finalized':"registration_applications WHERE state='finalized'",
 'assistedAccounts':"user_accounts WHERE account_access_origin='assisted_activation'",
 'intakes':'personnel_intake_requests','approvals':'assisted_approvals','stays':'housing_stays','overrides':'user_permission_overrides'}
with SessionLocal() as db:
 print(json.dumps({key:db.execute(text('SELECT count(*) FROM '+query)).scalar_one() for key,query in queries.items()}))
'''


def verify_state_counts(before, after):
    deltas = {'accounts': 3, 'activeAccounts': 3, 'people': 2, 'applications': 2, 'finalized': 2,
              'assistedAccounts': 1, 'intakes': 0, 'approvals': 0, 'stays': 0, 'overrides': 0}
    if (set(before) != set(deltas) or set(after) != set(deltas)
            or any(type(before[key]) is not int or type(after[key]) is not int
                   or after[key] != before[key] + value for key, value in deltas.items())):
        raise Refused('state scene exact new/activated/business counts mismatch')


def validate_legacy_database(row, project, owner, identifier, volume, image, port):
    validate_ownership([row], project, owner, {identifier})
    mounts = row.get('Mounts', [])
    if (row['Image'] != image or row['Config']['Labels'].get('com.docker.compose.service') != 'db'
            or row['HostConfig']['PortBindings'] != {'5432/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]}
            or len(mounts) != 1 or mounts[0].get('Type') != 'volume' or mounts[0].get('Name') != volume
            or mounts[0].get('Destination') != '/var/lib/postgresql/data'):
        raise Refused('legacy fresh database identity/mount/port mismatch')


def run_legacy_suites(state, result, output, supervisor, env, verify_owner):
    for suite, expected in LEGACY_SUITES:
        verify_owner()
        print(f'Loading legacy-functional: {suite}; original {expected} serial invocations', flush=True)
        log = state / (suite + '.log')
        entry = {'suite': suite, 'expectedInvocations': expected, 'completedInvocations': 0, 'exitCode': None}
        result['suites'].append(entry)
        ports = {int(value) for key, value in env.items() if key in ('E2E_API_PORT', 'E2E_WEB_PORT')}
        database_port = re.search(r'@127\.0\.0\.1:(\d+)/erp_e2e$', env.get('TEST_DATABASE_URL', ''))
        if database_port: ports.add(int(database_port[1]))
        api_port, web_port = free_port(), free_port()
        while api_port == web_port or api_port in ports or web_port in ports:
            api_port, web_port = free_port(), free_port()
        try:
            supervisor.run([str(ROOT / 'scripts/run-assisted-personnel-e2e.sh'), '--suite', suite], cwd=ROOT,
                env={**env, 'E2E_API_PORT': str(api_port), 'E2E_WEB_PORT': str(web_port)}, log=log)
            entry['exitCode'] = 0
        except Refused as error:
            match = re.search(r'\(exit (\d+)\)', str(error))
            entry['exitCode'] = int(match[1]) if match else None
            raise
        finally:
            raw = log.read_text(errors='replace') if log.exists() else ''
            completed = [int(value) for value in re.findall(r'^Assisted E2E passed: phase=' + re.escape(suite) + r'/[^\n ]+ count=(\d+)$', raw, re.MULTILINE)]
            entry['completedInvocations'] = len(completed)
            entry['orderedOriginalEntries'] = completed == list(range(1, expected + 1))
            private_json(output, result)
        if not entry['orderedOriginalEntries']:
            raise Refused('legacy original invocation count/order mismatch')


def collect_legacy(source, sha, evidence, runtime, supervisor, base_env, browser_path):
    if source.resolve() != ROOT or verify_source(ROOT, baseline=False) != sha:
        raise Refused('legacy fixed committed worktree required')
    owner = secrets.token_hex(16)
    project = 'erp-loading-' + owner[:16]
    volume = project + '-legacy-db'
    port = free_port()
    state = runtime / 'legacy-private'
    state.mkdir(mode=0o700)
    output = evidence / 'legacy-functional.json'
    log = state / 'owner.log'
    ids = set()
    result = {'schemaVersion': 1, 'kind': 'development-functional', 'productionLoadingEvidence': False,
              'sourceCommit': sha, 'originalMatrixInvocations': 28, 'ok': False, 'suites': []}
    private_json(output, result)
    def docker(*args, capture=False):
        value = supervisor.run(['docker', *args], env=base_env, log=log, capture=capture)
        if args[:2] == ('volume', 'inspect'):
            data = json.loads(value)
            if len(data) != 1 or data[0]['Name'] != volume or data[0].get('Labels', {}).get('erp.loading.owner') != owner:
                raise Refused('legacy exact new volume ownership mismatch')
        return value
    def containers():
        current = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}', capture=True).split()
        return json.loads(docker('inspect', *current, capture=True)) if current else []
    if containers() or docker('volume', 'ls', '-q', '--filter', f'name=^{volume}$', capture=True).strip():
        raise Refused('legacy project/volume already exists')
    image = docker('image', 'inspect', 'postgres:16-alpine', '--format', '{{.Id}}', capture=True).strip()
    with owned_cleanup_scope(docker, containers, project, owner, ids, supervisor):
        docker('volume', 'create', '--label', 'com.docker.compose.project=' + project,
               '--label', 'erp.loading.owner=' + owner, volume)
        docker('volume', 'inspect', volume, capture=True)
        identifier = docker('create', '--name', project + '-db', '--label', 'com.docker.compose.project=' + project,
            '--label', 'erp.loading.owner=' + owner, '--label', 'com.docker.compose.service=db',
            '--publish', f'127.0.0.1:{port}:5432', '--mount', f'type=volume,source={volume},destination=/var/lib/postgresql/data',
            '--env', 'POSTGRES_DB=erp_e2e', '--env', 'POSTGRES_USER=erp_e2e', '--env', 'POSTGRES_PASSWORD=erp-e2e-only',
            '--health-cmd', 'pg_isready -U erp_e2e -d erp_e2e', '--health-interval', '1s', '--health-retries', '30', image, capture=True).strip()
        ids.add(identifier)
        def verify_owner():
            rows = containers()
            if len(rows) != 1: raise Refused('exactly one legacy owned database required')
            validate_legacy_database(rows[0], project, owner, identifier, volume, image, port)
            docker('volume', 'inspect', volume, capture=True)
            return rows[0]
        verify_owner()
        docker('start', identifier)
        deadline = time.monotonic() + 60
        while verify_owner()['State'].get('Health', {}).get('Status') != 'healthy':
            if time.monotonic() >= deadline: raise Refused('legacy owned database readiness timeout')
            time.sleep(.2)
        empty = docker('exec', identifier, 'psql', '-U', 'erp_e2e', '-d', 'erp_e2e', '-Atc',
            "SELECT count(*) FROM pg_tables WHERE schemaname='public'", capture=True).strip()
        if empty != '0': raise Refused('legacy new database is not empty')
        result.update({'freshEmptyDatabaseVerified': True, 'databaseImage': image, 'databaseContainer': identifier})
        private_json(output, result)
        env = {key: base_env[key] for key in ('PATH', 'HOME', 'LANG')}
        env.update({'TEST_DATABASE_URL': f'postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:{port}/erp_e2e',
            'ASSISTED_E2E_DISPOSABLE_DATABASE': 'erp_e2e', 'ASSISTED_E2E_NO_MEDIA': '1',
            'E2E_PYTHON_BIN': sys.executable, 'PLAYWRIGHT_BROWSERS_PATH': browser_path})
        run_legacy_suites(state, result, output, supervisor, env, verify_owner)
        if verify_source(ROOT, baseline=False) != sha: raise Refused('legacy source changed during functional matrix')
    result['ok'] = True
    result['ownedCleanupCompleted'] = True
    private_json(output, result)


def validate_email_topology(rows, project, owner, ids, port):
    validate_ownership(rows, project, owner, ids)
    by_service = {row['Config']['Labels']['com.docker.compose.service']: row for row in rows}
    if len(rows) != 4 or set(by_service) != set(SERVICES): raise Refused('exact email fixture services required')
    api, proxy = by_service['api'], by_service['proxy']
    if (proxy['HostConfig']['NetworkMode'] != 'container:' + api['Id'] or proxy['HostConfig'].get('PortBindings')
            or api['HostConfig']['PortBindings'] != {'8080/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]}
            or '--no-proxy-headers' not in ''.join(api['Config']['Cmd'])
            or "'127.0.0.1'" not in ''.join(api['Config']['Cmd'])):
        raise Refused('actual private email namespace/port/argv mismatch')
    network_ids = [{value['NetworkID'] for value in by_service[service]['NetworkSettings']['Networks'].values()}
                   for service in ('api', 'db', 'web')]
    if any(len(value) != 1 or value != network_ids[0] for value in network_ids):
        raise Refused('email fixture owned network mismatch')
    return api['Id']


def talent_game_assets(manifest):
    names = {'reactiontime': 'ReactionGame', 'aim': 'AimGame', 'sequence': 'SequenceGame',
             'number-memory': 'NumberGame', 'verbal-memory': 'VerbalGame', 'chimp': 'ChimpGame',
             'memory': 'VisualGame', 'dynamic-vision': 'DynamicVisionGame'}
    current = [item for item in manifest['sets'] if item['release_id'] == manifest['release_id']]
    if len(current) != 1: raise Refused('exactly one current production asset set required')
    result = {}
    for test_id, name in names.items():
        result[test_id] = []
        for extension in ('js', 'css'):
            matches = [item for item in current[0]['files'] if re.fullmatch(r'assets/' + name + r'-[A-Za-z0-9_-]+\.' + extension, item['path'])]
            if len(matches) != 1 or not re.fullmatch(r'[a-f0-9]{64}', matches[0]['sha256']) or type(matches[0]['size']) is not int or matches[0]['size'] <= 0:
                raise Refused('exact unique production game JS/CSS mapping required')
            result[test_id].append(matches[0])
    return result


def run_talent_matrix(snapshot, state, payload, supervisor, local_env, browser_env, log):
    """Seven closed seed/browser/verifier lifetimes inside one owned runtime.

    Browser subprocesses receive neither seed/DB secrets nor arbitrary selectors.
    Supervisor.run reaps each browser process group before this function resets.
    """
    actors = json.loads((snapshot / 'apps/api/app/cli/e2e_credentials.json').read_text())
    output = Path(payload['output'])
    results = []
    result = {'schemaVersion': 1, 'kind': 'non-performance', 'check': 'talent-games',
              'fingerprint': payload['fingerprint'], 'ok': False, 'matrixPassed': False, 'finalFinancialVerified': False, 'entries': results}
    baseline = Path(local_env['ASSISTED_E2E_BASELINE_FILE'])
    baseline_hash = hashlib.sha256(baseline.read_bytes()).hexdigest()
    def save():
        temporary = output.with_suffix('.json.tmp')
        private_json(temporary, result)
        temporary.replace(output)
    def python(module, *args, env=local_env):
        supervisor.run([sys.executable, '-m', 'app.cli.' + module, *args],
                       cwd=snapshot / 'apps/api', env=env, log=log)
    def financial():
        if hashlib.sha256(baseline.read_bytes()).hexdigest() != baseline_hash:
            raise Refused('original financial baseline changed')
        python('seed_assisted_personnel_e2e', '--verify-financial-baseline')
        if hashlib.sha256(baseline.read_bytes()).hexdigest() != baseline_hash:
            raise Refused('original financial baseline changed')
    save()
    for index, (spec, project, legacy, mode) in enumerate(TALENT_MATRIX):
        print(f'Loading talent-games: entry {index + 1}/7 {project}', flush=True)
        entry = {'spec': spec, 'project': project, 'ok': False,
                 'financialBefore': False, 'financialAfter': False, 'resultsVerified': False}
        results.append(entry)
        directory = state / f'talent-{index}'
        directory.mkdir(mode=0o700)
        credential_path = directory / 'credentials.json'
        selected = ('admin', 'customer_service', 'coach', 'guardian', 'student', 'life_teacher') if index == 0 else ('student',)
        private_json(credential_path, {key: actors[key] for key in selected})
        config = directory / 'measurement.json'
        private_json(config, {**payload, 'entry': index, 'mode': 'existing', 'credentialsFile': str(credential_path)})
        seed_env = {**local_env, **({'TALENT_E2E_LEGACY_V1': legacy} if legacy else {})}
        try:
            python('seed_talent_e2e', env=seed_env)
            # Never learn/rewrite a baseline after a specialized destructive seed.
            financial()
            entry['financialBefore'] = True
        finally: save()
        original_failure = None
        try:
            supervisor.run(['node', str(ROOT / 'apps/web/scripts/run-talent-games.mjs'), str(config)],
                           cwd=ROOT / 'apps/web', env=browser_env, log=log)
        except Refused as error:
            original_failure = error
        safe_browser = directory / 'safe-browser.json'
        if safe_browser.is_file(): entry['browser'] = json.loads(safe_browser.read_text())
        try:
            summary = directory / 'summary.json'
            python('summarize_talent_playwright', '--report', str(directory / 'browser-results.json'),
                   '--output', str(summary), '--spec', spec, '--project', project)
            entry['summary'] = json.loads(summary.read_text())
            if original_failure is None:
                python('seed_talent_e2e', env={**local_env, 'TALENT_E2E_VERIFY_MODE': mode})
                entry['resultsVerified'] = True
        except Refused as error:
            if original_failure is None: original_failure = error
        finally:
            try:
                financial()
                entry['financialAfter'] = True
            except Refused as error:
                if original_failure is None: original_failure = error
            save()
        if original_failure is not None:
            raise original_failure
        # The completed desktop dataset supports only READ-ONLY history/report
        # checks; even starting a new session would alter its exact audit counts.
        if index == 0:
            resources = state / 'talent-history-resources'
            resources.mkdir(mode=0o700)
            resource_credentials = resources / 'credentials.json'
            private_json(resource_credentials, {'student': actors['student']})
            resource_config = resources / 'measurement.json'
            private_json(resource_config, {**payload, 'entry': 0, 'mode': 'history',
                'credentialsFile': str(resource_credentials), 'output': str(output.with_name('talent-history-resources.json'))})
            try:
                supervisor.run(['node', str(ROOT / 'apps/web/scripts/run-talent-games.mjs'), str(resource_config)],
                               cwd=ROOT / 'apps/web', env=browser_env, log=log)
                python('seed_talent_e2e', env={**local_env, 'TALENT_E2E_VERIFY_MODE': mode})
            except Refused as error:
                original_failure = error
            finally:
                try: financial()
                except Refused as error:
                    if original_failure is None: original_failure = error
            if original_failure is not None: raise original_failure
        entry['ok'] = True
        save()
    # Eighth, fixed dataset: exactly one real v3 session, zero game attempts/results.
    # It cannot be folded into any of the original seven result/audit verifiers.
    python('seed_talent_e2e')
    financial()
    resources = state / 'talent-resources'
    resources.mkdir(mode=0o700)
    resource_credentials = resources / 'credentials.json'
    private_json(resource_credentials, {'student': actors['student']})
    resource_config = resources / 'measurement.json'
    private_json(resource_config, {**payload, 'entry': 0, 'mode': 'resources',
        'credentialsFile': str(resource_credentials), 'output': str(output.with_name('talent-resources.json'))})
    original_failure = None
    result['resourceDatasetVerified'] = False
    try:
        supervisor.run(['node', str(ROOT / 'apps/web/scripts/run-talent-games.mjs'), str(resource_config)],
                       cwd=ROOT / 'apps/web', env=browser_env, log=log)
        python('verify_talent_resources_e2e')
        result['resourceDatasetVerified'] = True
    except Refused as error:
        original_failure = error
    finally:
        try: financial()
        except Refused as error:
            if original_failure is None: original_failure = error
        save()
    if original_failure is not None: raise original_failure
    result['matrixPassed'] = True
    save()


def run_esports_matrix(snapshot, state, payload, supervisor, local_env, browser_env, log):
    diagnostic = payload.get('check') == 'esports-capabilities-diagnostic'
    actors = json.loads((snapshot / 'apps/api/app/cli/e2e_credentials.json').read_text())
    baseline = Path(local_env['ASSISTED_E2E_BASELINE_FILE'])
    digest = hashlib.sha256(baseline.read_bytes()).hexdigest()
    output = Path(payload['output'])
    result = {'schemaVersion': 1, 'kind': 'non-performance', 'check': 'esports-games',
              'fingerprint': payload['fingerprint'], 'ok': False, 'matrixPassed': False,
              'finalFinancialVerified': False, 'entries': []}
    if diagnostic:
        result.update(check='esports-capabilities-diagnostic', diagnosticPassed=False)
    def save(): private_json(output, result)
    def python(module, *args, env=local_env):
        supervisor.run([sys.executable, '-m', 'app.cli.' + module, *args], cwd=snapshot / 'apps/api', env=env, log=log)
    def financial():
        if hashlib.sha256(baseline.read_bytes()).hexdigest() != digest:
            raise Refused('original financial baseline changed')
        python('seed_assisted_personnel_e2e', '--verify-financial-baseline')
        if hashlib.sha256(baseline.read_bytes()).hexdigest() != digest:
            raise Refused('original financial baseline changed')
    save()
    entries = [(3, ESPORTS_MATRIX[3])] if diagnostic else enumerate(ESPORTS_MATRIX)
    for index, (spec, project, mode) in entries:
        print(f'Loading esports-games: real-time entry {index + 1}/6 {project}', flush=True)
        directory = state / f'esports-{index}'
        directory.mkdir(mode=0o700)
        credentials = directory / 'credentials.json'
        private_json(credentials, {key: actors[key] for key in ('admin', 'customer_service')})
        config = directory / 'measurement.json'
        private_json(config, {**payload, 'check': 'esports-games', 'entry': index,
            'mode': 'capability-diagnostic' if diagnostic else 'existing', 'credentialsFile': str(credentials)})
        entry = {'spec': spec, 'project': project, 'ok': False, 'financialBefore': False, 'financialAfter': False, 'resultsVerified': False}
        result['entries'].append(entry)
        failure = None
        try:
            python('seed_esports_assessments_e2e')
            financial()
            entry['financialBefore'] = True
            supervisor.run(['node', str(ROOT / 'apps/web/scripts/run-esports-games.mjs'), str(config)],
                           cwd=ROOT / 'apps/web', env=browser_env, log=log)
        except Refused as error: failure = error
        safe = directory / 'safe-browser.json'
        if safe.is_file(): entry['browser'] = json.loads(safe.read_text())
        if diagnostic and failure is None and not (entry.get('browser', {}).get('ok') is True
                and entry.get('browser', {}).get('diagnosticEvidenceValid') is True):
            failure = Refused('capability diagnostic evidence unavailable')
        summary = directory / 'summary.json'
        try:
            python('summarize_esports_assessments_e2e', '--report', str(directory / 'browser-results.json'),
                   '--output', str(summary), '--spec', spec, '--project', project)
            if failure is None:
                python('seed_esports_assessments_e2e', env={**local_env, 'ESPORTS_E2E_VERIFY_MODE': mode})
                entry['resultsVerified'] = True
        except Refused as error:
            if failure is None: failure = error
        finally:
            # The original summarizer writes its validated safe diagnostic before
            # returning nonzero for a failed/skipped case. Preserve that evidence.
            try:
                entry['summary'] = json.loads(summary.read_text())
            except (OSError, ValueError):
                if failure is None: failure = Refused('safe esports summary unavailable')
            try:
                financial()
                entry['financialAfter'] = True
            except Refused as error:
                if failure is None: failure = error
            save()
        if failure is not None: raise failure
        entry['ok'] = True
        save()
    if diagnostic:
        result['diagnosticPassed'] = True
        save()
        return
    # Read-only preview uses the last exact seeded role mapping and changes no ERP results.
    private_json(config, {**payload, 'check': 'esports-games', 'entry': 5, 'mode': 'audio', 'credentialsFile': str(credentials)})
    try:
        supervisor.run(['node', str(ROOT / 'apps/web/scripts/run-esports-games.mjs'), str(config)],
                       cwd=ROOT / 'apps/web', env=browser_env, log=log)
        python('seed_esports_assessments_e2e', env={**local_env, 'ESPORTS_E2E_VERIFY_MODE': 'capabilities-webkit'})
    finally:
        audio = directory / 'safe-audio.json'
        if audio.is_file(): result['audio'] = json.loads(audio.read_text())
        financial()
        save()
    if not result.get('audio', {}).get('ok'):
        raise Refused('audio browser proof missing or failed')
    result['matrixPassed'] = True
    save()


def collect(source, sha, evidence, label, runtime, supervisor, base_env, browser_path, *, check=None):
    if check is not None and check not in NAMED_RUNNERS:
        raise Refused('unknown named check; no services created')
    snapshot = runtime / label
    snapshot.mkdir()
    archive = runtime / (label + '.tar')
    with archive.open('wb') as output:
        subprocess.run(['git', '-C', str(source), 'archive', sha], stdout=output, check=True)
    archive_hash = hashlib.sha256(archive.read_bytes()).hexdigest()
    extract_snapshot(archive, snapshot)
    validate_dotenv(snapshot)
    # Measurement code stays in the tool tree, never in the product snapshot.
    owner = secrets.token_hex(16)
    project = 'erp-loading-' + owner[:16]
    http_port, db_port = free_port(), free_port()
    while db_port == http_port:
        db_port = free_port()
    state = runtime / (label + '-private')
    state.mkdir(mode=0o700)
    release = None
    email_fixture = check == 'state-safety'
    if label == 'release-window':
        from frontend_release_window import ReleaseWindow
        release = ReleaseWindow(owner, snapshot, state, archive_hash, f'http://127.0.0.1:{http_port}',
                                patch_release_fixture, Refused, private_json)
    mailbox = state / 'mailbox'
    mailbox.mkdir(mode=0o700)
    (mailbox / '.registration-test-mailbox').write_text('self-service-registration-e2e\n')
    (mailbox / 'verification-mailbox.jsonl').touch(mode=0o600)
    credentials = json.loads((snapshot / 'apps/api/app/cli/e2e_credentials.json').read_text())['admin']
    credential_path = state / 'credentials.json'
    private_json(credential_path, credentials)
    restricted_credential_path = state / 'restricted-credentials.json'
    if check in ('module-recovery', 'route-loading', 'state-safety'):
        private_json(restricted_credential_path,
            json.loads((snapshot / 'apps/api/app/cli/e2e_credentials.json').read_text())['customer_service'])
    student_credential_path = state / 'student-credentials.json'
    if check in ('style-order', 'route-loading'):
        private_json(student_credential_path,
            json.loads((snapshot / 'apps/api/app/cli/e2e_credentials.json').read_text())['student'])
    proxy = state / 'nginx.conf'
    proxy_text = EMAIL_PROXY if email_fixture else PROXY
    proxy.write_text(proxy_text)
    proxy.chmod(0o644)
    dependency_image = supervisor.run(['docker', 'image', 'inspect', DEPENDENCY_IMAGE_TAG,
        '--format', '{{.Id}}'], env=base_env, capture=True).strip()
    secret_env = create_runtime_secrets()
    api_env = {**secret_env, 'OPERATIONS_MONITOR_ENABLED': 'false',
        'REGISTRATION_TEST_MAILBOX_DIR': '/loading-mailbox', 'REGISTRATION_TEST_SERVER_HOST': '127.0.0.1',
        'ERP_WEB_BASE_URL': f'http://127.0.0.1:{http_port}'}
    override = state / 'compose.yaml'
    api_bootstrap = ("from pathlib import Path;import os; p=Path('/loading-mailbox');"
        "(p/'.registration-test-mailbox').write_text('self-service-registration-e2e\\n');"
        "(p/'verification-mailbox.jsonl').touch(mode=0o600);"
        "os.execvp('uvicorn',['uvicorn','app.main:app','--host','0.0.0.0','--port','8000','--no-access-log'])")
    if email_fixture:
        api_bootstrap = api_bootstrap.replace("os.execvp", "(p/'.registration-test-mailbox').chmod(0o600);os.execvp")
        api_bootstrap = api_bootstrap.replace("'0.0.0.0'", "'127.0.0.1'").replace("'--no-access-log'", "'--no-access-log','--no-proxy-headers'")
    # JSON objects are valid YAML; only reset/override fields need YAML tags.
    override_content = ('services:\n' + '\n'.join(
        f'  {service}:\n    labels: {json.dumps({"erp.loading.owner": owner})}\n' + (
            f'    build:\n      target: e2e\n      args: {json.dumps({"ERP_USE_DEPENDENCY_IMAGE": "1"})}\n'
            f'    environment: {json.dumps(api_env)}\n'
            f'    volumes: !override ["{project}-runtime:/app/runtime"]\n'
            '    tmpfs: !override ["/tmp:rw,noexec,nosuid,size=64m", "/loading-mailbox:rw,noexec,nosuid,uid=10001,gid=10001,mode=0700,size=4m"]\n'
            f'    command: !override {json.dumps(["python", "-c", api_bootstrap])}\n'
            + (f'    ports: !override ["127.0.0.1:{http_port}:8080"]\n' if email_fixture else '')
            if service == 'api' else
            '    build:\n      target: production\n__RELEASE_FIELDS__    healthcheck:\n      test: ["CMD-SHELL", "wget -q -O - http://127.0.0.1:5173/healthz >/dev/null"]\n'
            if service == 'web' else
            f'    volumes: !override [{json.dumps(str(proxy) + ":/etc/nginx/nginx.conf:ro")}]\n'
            + ('    ports: !reset []\n    network_mode: "service:api"\n' if email_fixture else '')
            if service == 'proxy' else '') for service in SERVICES)
        + f'\nvolumes:\n  {project}-runtime:\n    labels: {json.dumps({"erp.loading.owner": owner})}\n')
    override.write_text(override_content.replace('__RELEASE_FIELDS__', release.build_fields() if release else ''))
    compose = shutil.which('docker-compose') or '/opt/homebrew/bin/docker-compose'
    env = {**base_env, 'COMPOSE_DISABLE_ENV_FILE': '1', 'E2E_HTTP_PORT': str(http_port), 'E2E_DB_PORT': str(db_port)}
    command = [compose, '--env-file', '/dev/null', '-p', project, '-f', str(snapshot / 'compose.yaml'),
               '-f', str(snapshot / 'compose.e2e.yaml'), '-f', str(override)]
    log = state / 'runtime.log'
    ids = set()
    def docker(*args, capture=False):
        return supervisor.run(['docker', *args], env=base_env, log=log, capture=capture)
    def compose_run(*args, capture=False):
        return supervisor.run([*command, *args], cwd=snapshot, env=env, log=log, capture=capture)
    def containers():
        current = docker('ps', '-aq', '--filter', f'label=com.docker.compose.project={project}', capture=True).split()
        return json.loads(docker('inspect', *current, capture=True)) if current else []
    if containers():
        raise Refused('existing project containers forbidden')
    with owned_cleanup_scope(docker, containers, project, owner, ids, supervisor):
        try:
            print(f'Loading {label}: build isolated production services', flush=True)
            build_owned_services(compose_run, docker, dependency_image)
            if release:
                built_image = docker('image', 'inspect', project + '-web', '--format', '{{.Id}}', capture=True).strip()
                release.inspect_image(built_image, docker, project, ids)
            print(f'Loading {label}: production build complete; create four services', flush=True)
            compose_run('create', *SERVICES)
            created = containers()
            created_ids = {container['Id'] for container in created}
            validate_ownership(created, project, owner, created_ids)
            ids.update(created_ids)
            if len(created_ids) != 4:
                raise Refused('expected exactly four owned containers')
            compose_run('up', '-d', '--wait', '--wait-timeout', '180', '--no-build', *SERVICES)
            if release:
                release.activated({row['Config']['Labels']['com.docker.compose.service']: row['Id'] for row in containers()})
                private_json(evidence / 'release-window-owner.json', release.evidence())
            print(f'Loading {label}: migrate and seed owned disposable database', flush=True)
            compose_run('exec', '-T', 'api', 'python', '-m', 'alembic', 'upgrade', 'head')
            # Canonical assisted seeder insists on a proven loopback database, so run
            # both seeds locally against only the newly-owned exposed database port.
            local_env = {**base_env, **secret_env, 'APP_ENV': 'test',
                'PYTHONPATH': str(snapshot / 'apps/api'),
                'DATABASE_URL': f'postgresql+psycopg://erp_e2e:erp-e2e-only@127.0.0.1:{db_port}/erp_e2e',
                'ASSISTED_E2E_DISPOSABLE_DATABASE': 'erp_e2e',
                'ASSISTED_E2E_BASELINE_FILE': str(state / 'assisted-baseline.json'),
                'REGISTRATION_TEST_MAILBOX_DIR': str(mailbox), 'REGISTRATION_TEST_SERVER_HOST': '127.0.0.1'}
            for seed in ('seed_e2e', 'seed_assisted_personnel_e2e'):
                print(f'Loading {label}: {seed}', flush=True)
                supervisor.run([sys.executable, '-m', 'app.cli.' + seed], cwd=snapshot / 'apps/api', env=local_env, log=log)
            otp_bridge = None
            if email_fixture:
                def owned_api():
                    return validate_email_topology(containers(), project, owner, ids, http_port)
                api_id = owned_api()
                email_proof = json.loads(docker('exec', '--user', '10001:10001', api_id, 'python', '-c', EMAIL_PREFLIGHT, capture=True))
                if email_proof != {key: True for key in ('uidVerified', 'privateModesVerified', 'smtpSettingsAbsent', 'loopbackSenderVerified', 'forwardedHeaderIgnored')}:
                    raise Refused('private email preflight did not prove every guard')
                private_json(evidence / 'state-email-preflight.json', email_proof)
                otp_bridge = OtpBridge(state, docker, owned_api)
                before_counts = json.loads(docker('exec', '--user', '10001:10001', api_id, 'python', '-c', STATE_COUNTS, capture=True))
            coach_credentials = state / 'assisted-coach-credentials.json'
            if check == 'optional-panels':
                # Exact existing seeder actor only, never a caller-selected role.
                selected = supervisor.run([sys.executable, '-c',
                    ('import json;from app.cli.seed_assisted_personnel_e2e import ACCOUNTS;'
                     'from app.cli.e2e_credentials import E2E_CREDENTIALS;'
                     'print(json.dumps({"identifier":ACCOUNTS["coach"].phone,"password":E2E_CREDENTIALS["admin"].password}))')],
                    cwd=snapshot / 'apps/api', env=local_env, log=log, capture=True)
                private_json(coach_credentials, json.loads(selected))
            images = {container['Config']['Labels']['com.docker.compose.service']: container['Image'] for container in containers()}
            config_hash = hashlib.sha256((proxy_text + override.read_text().replace(owner, '<owner>')).encode()).hexdigest()
            fingerprint = {'sourceCommit': sha, 'sourceArchiveSha256': archive_hash,
                'images': images, 'dependencyImage': dependency_image, 'proxySha256': hashlib.sha256(proxy_text.encode()).hexdigest(),
                'configSha256': config_hash, 'productionWebTarget': True}
            config = state / 'measurement.json'
            payload = {'baseURL': f'http://127.0.0.1:{http_port}', 'credentialsFile': str(credential_path),
                'output': str(evidence / (label + '.json')), 'fingerprint': fingerprint}
            if release:
                payload.update({'check': 'release-window', 'releases': release.evidence(), 'barrierDir': str(state)})
            if check == 'talent-games':
                current = containers()
                validate_ownership(current, project, owner, ids)
                web_id = next(item['Id'] for item in current if item['Config']['Labels']['com.docker.compose.service'] == 'web')
                raw_manifest = docker('exec', web_id, 'cat', '/etc/erp-web/assets-manifest.json', capture=True)
                if len(raw_manifest) > 2_000_000: raise Refused('production asset manifest exceeds bound')
                payload['gameAssets'] = talent_game_assets(json.loads(raw_manifest))
                fingerprint['assetManifestSha256'] = hashlib.sha256(raw_manifest.encode()).hexdigest()
            if check is not None:
                payload.update({'check': check})
                if check in ('module-recovery', 'route-loading', 'state-safety'):
                    payload['restrictedCredentialsFile'] = str(restricted_credential_path)
                if check in ('style-order', 'route-loading'):
                    payload['studentCredentialsFile'] = str(student_credential_path)
                if check == 'optional-panels':
                    payload['coachCredentialsFile'] = str(coach_credentials)
                if email_fixture:
                    payload['bridgeDir'] = str(state)
            private_json(config, payload)
            browser_runner = NAMED_RUNNERS[check] if check is not None else 'measure-loading.mjs'
            print(f'Loading {label}: fixed non-performance browser checks' if check or release else
                  f'Loading {label}: collect 280 serial measurements', flush=True)
            browser_env = {**base_env, 'PLAYWRIGHT_BROWSERS_PATH': browser_path}
            if release:
                from frontend_release_window import wait_barrier
                stage = 0
                deadline = time.monotonic() + 180
                def barrier_tick():
                    nonlocal stage, deadline
                    if stage >= 2:
                        return
                    expected = ('ready-A', 'ready-D')[stage]
                    if not wait_barrier(state / (expected + '.json'), expected, validate_release_message, deadline):
                        return
                    (state / (expected + '.json')).unlink()
                    for marker in (('B', 'C') if stage == 0 else ('D',)):
                        print(f'Loading release-window: build/validate/activate {marker}; API and DB unchanged', flush=True)
                        release.prepare(marker)
                        override.write_text(override_content.replace('__RELEASE_FIELDS__', release.build_fields()))
                        compose_run('build', '--pull=false', 'web')
                        image = docker('image', 'inspect', project + '-web', '--format', '{{.Id}}', capture=True).strip()
                        release.inspect_image(image, docker, project, ids)
                        compose_run('up', '-d', '--no-deps', '--force-recreate', '--no-build', '--wait', '--wait-timeout', '180', 'web', 'proxy')
                        current = containers()
                        current_ids = {row['Id'] for row in current}
                        validate_ownership(current, project, owner, current_ids)
                        ids.update(current_ids)
                        release.activated({row['Config']['Labels']['com.docker.compose.service']: row['Id'] for row in current})
                        private_json(evidence / 'release-window-owner.json', release.evidence())
                    stage += 1
                    publish_release_activation(state, 'C' if stage == 1 else 'D', release.evidence())
                    deadline = time.monotonic() + 180
                try:
                    supervisor.run(['node', str(ROOT / 'apps/web/scripts/run-release-window.mjs'), str(config)],
                        cwd=ROOT / 'apps/web', env=browser_env, log=log, on_tick=barrier_tick)
                finally:
                    stage_file = state / 'browser-stage.json'
                    if stage_file.is_file():
                        observed = json.loads(stage_file.read_text()).get('stage')
                        allowed = {'late-history-after-C', *(view + '-' + phase for view in ('desktop', 'touch')
                            for phase in ('success-home', 'draft-person', 'draft-css-held', 'draft-fields'))}
                        private_json(evidence / 'release-window-stage.json', {'stage': observed if observed in allowed else 'unknown'})
                if stage != 2:
                    raise Refused('release browser exited before both upgrades completed')
            elif check in ('esports-games', 'esports-capabilities-diagnostic'):
                run_esports_matrix(snapshot, state, payload, supervisor, local_env, browser_env, log)
            elif check == 'talent-games':
                run_talent_matrix(snapshot, state, payload, supervisor, local_env, browser_env, log)
            elif email_fixture:
                try:
                    supervisor.run(['node', str(ROOT / 'apps/web/scripts' / browser_runner), str(config)],
                        cwd=ROOT / 'apps/web', env=browser_env, log=log, on_tick=otp_bridge.tick)
                finally:
                    after_counts = json.loads(docker('exec', '--user', '10001:10001', owned_api(), 'python', '-c', STATE_COUNTS, capture=True))
                    private_json(evidence / 'state-record-counts.json', {'before': before_counts, 'after': after_counts})
                verify_state_counts(before_counts, after_counts)
                if otp_bridge.sequence != 4:
                    raise Refused('exact three private mailbox reads required')
            else:
                supervisor.run(['node', str(ROOT / 'apps/web/scripts' / browser_runner), str(config)],
                    cwd=ROOT / 'apps/web', env=browser_env, log=log)
            if hashlib.sha256(archive.read_bytes()).hexdigest() != archive_hash:
                raise Refused('baseline archive changed during measurement')
            supervisor.run([sys.executable, '-m', 'app.cli.seed_assisted_personnel_e2e', '--verify-financial-baseline'],
                cwd=snapshot / 'apps/api', env=local_env, log=log)
            if check in ('talent-games', 'esports-games', 'esports-capabilities-diagnostic'):
                final_output = Path(payload['output'])
                final = json.loads(final_output.read_text())
                final['ok'] = final['diagnosticPassed'] if check == 'esports-capabilities-diagnostic' else final['matrixPassed']
                final['finalFinancialVerified'] = True
                private_json(final_output, final)
        except Refused:
            try:
                for container in containers():
                    raw = docker('logs', '--tail', '20', container['Id'], capture=True)
                    categories = sorted(set(re.findall(r'\b[A-Za-z_]+Error\b|Permission denied|Read-only file system|not found', raw)))
                    service = container['Config']['Labels']['com.docker.compose.service']
                    state = container['State']
                    health = state.get('Health', {})
                    probes = [entry['ExitCode'] for entry in health.get('Log', [])]
                    print(f'Loading infrastructure {service}: state={state["Status"]} exit={state["ExitCode"]}'
                          f' health={health.get("Status", "none")} probes={probes} categories={",".join(categories)}', flush=True)
                    if service == 'proxy' and state.get('Error'):
                        print('Loading proxy start diagnostic: ' + state['Error'][:700], flush=True)
                    if service in ('web', 'proxy'):
                        # Fixed nginx startup/health output only; access logs are off.
                        lines = [line for line in raw.splitlines() if '[emerg]' in line or '[crit]' in line]
                        for line in lines:
                            print('Loading nginx diagnostic: ' + line[:400], flush=True)
            except (Refused, OSError, ValueError, KeyError, TypeError):
                print('Loading startup diagnostics unavailable; original failure preserved', file=sys.stderr)
            raise


def parse_arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-source', type=Path, required=True)
    parser.add_argument('--candidate-source', type=Path, required=True)
    parser.add_argument('--phase', choices=('baseline', 'compare', 'release-window', 'named'), required=True)
    parser.add_argument('--check', choices=tuple(NAMED_RUNNERS))
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    if (args.phase == 'named') != (args.check is not None):
        parser.error('a fixed --check is required only with --phase named')
    return args


def main():
    args = parse_arguments()
    args.evidence_dir = args.evidence_dir.resolve()
    validate_environment(os.environ)
    validate_dotenv(Path.cwd())
    baseline_sha = verify_source(args.baseline_source, baseline=True)
    candidate_sha = verify_source(args.candidate_source, baseline=False)
    browser_path = str(Path.home() / 'Library/Caches/ms-playwright')
    if not Path(browser_path).is_dir():
        raise Refused('existing local Playwright browser installation required')
    os.umask(0o077)
    prepare_evidence(args.evidence_dir, args.phase, check=args.check)
    base_env = {'PATH': os.environ['PATH'], 'HOME': str(Path.home()), 'LANG': 'C.UTF-8',
                'DOCKER_HOST': 'unix://' + str(Path.home() / '.colima/default/docker.sock'),
                'DOCKER_CONFIG': str(ROOT / '.superpowers/sdd/2026-10-18-frontend-loading-optimization/docker-cli-probe'),
                'COMPOSE_DISABLE_ENV_FILE': '1', 'PYTHONDONTWRITEBYTECODE': '1'}
    supervisor = Supervisor()
    def interrupted(*_args):
        raise KeyboardInterrupt
    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        # Colima shares HOME, not arbitrary /tmp paths. Keep bind-mounted proxy
        # config in a unique private runtime outside all source/build contexts.
        runtime_parent = Path.home() / '.codex/tmp'
        runtime_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='erp-loading-', dir=runtime_parent) as directory:
            runtime = Path(directory)
            runtime.chmod(0o700)
            if args.phase == 'baseline':
                collect(args.baseline_source, baseline_sha, args.evidence_dir, 'baseline', runtime, supervisor, base_env, browser_path)
            elif args.phase == 'named':
                if args.check == 'legacy-functional':
                    collect_legacy(args.candidate_source, candidate_sha, args.evidence_dir, runtime, supervisor, base_env, browser_path)
                elif args.check == 'direct-document-diagnostic':
                    collect_direct_pair(args.baseline_source, baseline_sha, args.candidate_source, candidate_sha,
                        args.evidence_dir, runtime, supervisor, base_env, browser_path)
                else:
                    collect(args.candidate_source, candidate_sha, args.evidence_dir, args.check, runtime, supervisor,
                            base_env, browser_path, check=args.check)
            elif args.phase == 'release-window':
                collect(args.candidate_source, candidate_sha, args.evidence_dir, 'release-window', runtime, supervisor, base_env, browser_path)
            else:
                collect_candidate(args.candidate_source, candidate_sha, args.evidence_dir, runtime, supervisor, base_env, browser_path)
    finally:
        supervisor.close()
    print('Loading acceptance: evidence saved; owned services removed', flush=True)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print('Loading acceptance interrupted; owned cleanup attempted', file=sys.stderr)
        sys.exit(130)
    except (Refused, OSError, subprocess.SubprocessError, ValueError) as error:
        print(f'Loading acceptance refused: {error if isinstance(error, Refused) else type(error).__name__}', file=sys.stderr)
        sys.exit(2)
