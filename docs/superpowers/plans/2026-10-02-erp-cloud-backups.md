# ERP-owned portable cloud backups

**Goal:** Add super-admin backup management for Baidu, 115 and Quark without fnOS dependencies. Preserve the established encrypted full-backup and isolated-restore pipeline.

**Approved scope:** ERP owns settings, encrypted connector credentials, queue, scheduling, validation, reporting and alerts. A private portable WebDAV connector handles vendor authorization and remote transfer. No account is silently authorized and no backup is declared usable before remote read-back. All three destinations are selectable; Quark is explicitly experimental because its connector is not a maintained official API. Connecting actual vendor accounts remains a user authorization step.

## Global Constraints

- Work only in the existing team-roles-divisions worktree. Preserve deployed sound-preview changes. No commits, reset, cleanup or direct remote file writes.
- Only actual super administrators with operations.monitor.read may see or change backup settings. Credentials are envelope encrypted, write-only and never placed in logs, command arguments, audit metadata or API responses.
- A fixed operator-configured connector origin (default http://127.0.0.1:5244) is used by the host runner. The web form cannot select arbitrary network endpoints. Disable redirects; configure connector to proxy downloads.
- Upload only completed authenticated archive.tar.gz.enc plus complete.json, never temporary plaintext, backup root, or the separately stored recovery key. Publish complete.json last, after archive read-back; verify both hashes/HMAC through existing backup validation.
- ERP uses three target records (baidu, 115, quark), a queued job table and a host runner using fixed docker exec arguments to an internal CLI. No Docker socket or privileged host files are exposed to the API.
- Config changes invalidate previous tests/success. Enable only after a connection write/read test. Connection success is NOT a usable backup. Each target succeeds/fails separately; retries cannot overwrite a newer config result.
- The runner uses host locks, bounds HTTP responses/timeouts, disables ambient proxies and redirects, and sanitizes all errors. No remote pruning in this version; the UI must say remote versions require manual cleanup.
- Backup schedules remain ERP-owned. The existing local backup runs at 03:00 China time; a portable cron runner checks every two minutes and replicates each new completed backup once per enabled target. Manual connection tests and retry-latest jobs are supported.
- Local checks, independent review, then root scripts/deploy-nas.sh --source WORK. Failed release preflight blocks delivery. Real cloud copies remain pending authorization until round-trip verification succeeds.

## Shared interfaces

API prefix /api/v1/operations/cloud-backups. GET returns {targets: Target[], jobs: Job[], worker_last_seen_at: datetime|null}. Target: provider, enabled, configured, remote_path, username, credential_present, revision, connection_status (untested/ok/failed), last_tested_at, last_success_at, last_backup_id, last_error_code. Job: id, provider, kind (test/copy), status (queued/running/succeeded/failed), created_at, started_at, completed_at, backup_id|null, error_code|null. PUT /{provider}: {remote_path,username,password?: string,enabled: bool}. POST /{provider}/test and /{provider}/copy enqueue and return Job. Errors use existing ProblemError Chinese messages.

Internal CLI: python -m app.operations.cloud_bridge accepts one JSON request on stdin, returns exactly one bounded JSON response. Actions: sync {backup_id|null} (enqueue new valid local backup for enabled/tested targets), claim {} -> {job:null} or {job:{id,provider,kind,revision,remote_path,username,password,backup_id}}, finish {job_id,revision,success,error_code,backup_id|null} -> {ok:true}. CLI is not HTTP-accessible. Connection test successes update last_tested_at only; copy successes update last_success_at/last_backup_id. Claim/finish track worker heartbeat. Agent implementing backend may extend this interface after notifying runner owner.

### Task 1: Backend configuration and job queue

Own apps/api/app/operations/cloud_{models,schemas,service,router,bridge}.py; operations/router.py router inclusion; migration 0062_cloud_backups plus migrations/env.py model imports; tests/api/test_cloud_backups.py and service/unit tests. Implement exact shared API/CLI contract, encrypted credentials, CSRF/session/strict role boundaries, path validation (absolute logical path, no traversal/control chars/query/fragment, bounded segments), max one live job per target, revision CAS, deduplicated automatic copy by target/revision/backup, secret-free failures. Add worker heartbeat record via existing heartbeat system (cloud_backup service key). Protect config changes while job live (409) so completion cannot overwrite settings. Internal sync accepts only strict backup ID pattern. Use TDD and report actual tests; one DB test process at a time, coordinate root first.

### Task 2: Super-admin UI

Own apps/web/src/operations/OperationsCloudBackupPanel.tsx, cloudBackupApi.ts, dedicated test, operations.css additions, OperationsBackupPanel.tsx integration only. Three provider cards, write-only password preserve-on-empty, path/connector username, save, write/read connection test, enable after verified test, retry latest completed backup, recent jobs with Chinese safe reason codes and timestamps. Do not claim configured/tested means backed up. Show ERP-owned schedule, portable connector dependency, Quark experimental warning, remote manual retention, separate recovery key guidance. No secrets in query cache. Align exact shared API contract. TDD with realistic async loading/errors; existing backup panel remains intact.

### Task 3: Portable backup runner

Own scripts/erp-cloud-backup.py and scripts/erp_cloud_transport.py plus tests/cli/test_erp_cloud_backup.py. Implement internal CLI invocation via fixed argv/stdin, sync latest valid locally completed backup, process bounded jobs, flock deploy shared and cloud exclusive and backup shared lock (coordinate current backup lock semantics), stream PUT/GET through fixed connector origin with Basic credentials, no redirects/proxies. Only safe logical provider paths under /dav. Connection test random uniquely-owned file then GET verify then DELETE its own probe only; fail on no write/read. Copy: authenticated verified local pair, unique remote backup directory, upload encrypted archive, stream full read-back to protected temporary dir, check SHA/HMAC, upload complete.json last, read it back and run existing validate_encrypted on local downloaded copy. Never copy arbitrary user-selected files. Disk/size/symlink safety, remote HTTP secret-free errors, existing completed copy safely reverified/reused, target failures isolated. Timeouts bounded, return bridge result sanitized. Import current erp-backup helper by explicit local path. No pruning real remote versions. Unit/local fake-WebDAV integration tests including corruption, redirects, errors, partial writes, no credentials in logs.

### Task 4: Integration, deployment and monitoring

Root owns manifest/current-head expectations, backup installer cron integration, optional private pinned OpenList compose template and documentation, operations backup response/cloud aggregation/monitoring integration and tests. Configure bridge to proxy WebDAV downloads, default connector only loopback, no public management ports. Add new runner to installed wrapper while retaining old cron/backup configuration. Monitoring considers a cloud copy usable only if at least one enabled target is fresh and verified; individual failed/stale enabled targets remain visible alerts even if another succeeds. Run focused suites/build/types, review combined diff, deployment and verify NAS current revision/healthy scheduler/new UI and runner installation. Do not alter unrelated HTTPS probe behavior.

## Acceptance and live activation

Test service-level identity and stale jobs; API superadmin vs ordinary admin; write-only secret redaction; WebDAV actual local server upload/download/corruption; no archive marker on failed upload; old local backups unaffected. Real cloud authorization and large-file restore are pending user login and cannot be claimed completed. User can inspect deployed settings before authorizing providers.
