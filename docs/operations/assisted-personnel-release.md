# Assisted personnel: release and encrypted recovery gate

## Status

This is the **pre-release source-document snapshot**. Pending NAS entries
describe the evidence available before source packaging, not a later deployment
outcome. 本次实际 NAS 发布及在线核验结果见主控交付的
`release-verification.md`；该独立交付记录在最终结果取得后生成，不是本源目录
中已存在的成功证明，也不预填发布编号。

Implementation is complete and production source `d06e57e` is source-reviewed.
Later reviewed changes through `3129677` align old test contracts and complete
check-script orchestration; production application inputs remain `d06e57e`.
Final Web: **253 files / 2646 tests passed**; build/lint and full API Ruff/mypy
(408 source files) passed. The existing >500 kB bundle advisory remains.
Named final-fix backend regression: **232 passed**; permission-contract bounded
regression: **18 passed**. These do not establish a full API/E2E pass.

Full API session `79125` **failed: 7761 passed, 5 failed, 6 skipped, exit1,
2341.50s**. Four stale contract assertions were repaired at `19dbc50` and the
complete three affected files passed **283 tests**. The fifth failure, missing
assisted-spec ownership in the complete gate, was repaired with a separately
reviewed script change and port-pinning fix through `3129677`: **38 fake/source
checks passed, 1 actual Compose-render check explicitly deselected** under that
worker's no-Docker boundary. No application source or production permission was
changed. Full API rerun `62262` at `3129677` **actually exited0: 7788 passed,
6 skipped, 0 failed in 2434.90s (40:34)**. The previously deselected actual
Compose-render test ran and passed in this full suite. The earlier failed run
is historical, not erased by the successful rerun. No ordinary DB/migration
test was skipped. The standalone complete `scripts/check.sh` was **not run**:
the isolation plan forbids provisioning its shared Compose project. Full API
and separate browser matrices do not imply complete-gate orchestration ran.

The final `-rfEs` skip ledger is explicit; opt-in skips are not passes:

| Test path under `apps/api/tests/cli/` | Line | Count | Opt-in integration not run in full API |
| --- | ---: | ---: | --- |
| `test_assisted_personnel_backup_docker.py` | 594 | 1 | Assisted encrypted Docker recovery; separately passed final-image session10496 below |
| `test_cloud_authorization_joint_recovery.py` | 358 | 1 | Joint Docker recovery |
| `test_cloud_connector_candidates.py` | 816 | 1 | Isolated OpenList4.2.6 candidates |
| `test_cloud_connector_recovery.py` | 746 | 1 | Explicit isolated connector recovery |
| `test_erp_backup_docker.py` | 21 | 2 | Generic Docker backup tests |

Only the assisted recovery opt-in was separately executed for this final
release. No success is inferred for the other five skipped cases.

Final-candidate browser acceptance **42 passed**, all five hardened runners
actually exited0; both assisted financial checks passed. Final-image encrypted
recovery **1 passed / 0 skipped, exit0, 16.57s**, with verified exact-owned
cleanup. **NAS is not deployed.** The earlier recovery below stays historical.
Source approval, local test success and dry-run preflight are not a release.
This document does not authorize a NAS restore or claim native App, simulator
keyboard or physical-device acceptance.

## Before release

- Freeze and review the accepted source, including migration
  `0066_assisted_personnel`. Record the source digest and exact API image ID.
- Coordinate one serial database-test window. Do not reuse any existing worker
  database, inherited `DATABASE_URL`, Compose stack, host port, or data volume.
- Check local Docker context and image availability read-only. Starting or
  reconfiguring Docker, pulling dependencies, building images, and running the
  rehearsal need the controller's explicit authorization. Use a unique local
  image tag and record its immutable `sha256:` ID; never default to `latest`.
- Run applicable regression and encrypted recovery checks against that source.
  A skipped opt-in test does not pass the recovery gate.
- Release through `scripts/deploy-nas.sh --source '<accepted-source-directory>'`
  only. The controller owns this step. Failed preflight/deployment blocks the
  release; do not bypass backups, revision checks, secrets preservation, or
  migration checks with direct copies or ad hoc Docker operations.

## Isolated encrypted rehearsal

This test owns a fresh portless PostgreSQL source container, upgrades an empty
database to 0065, seeds synthetic old business facts, verifies their preservation
across 0066, and adds all seven assisted tables. It does not import database
reset fixtures. Its small legacy business seed is extracted as a single pure
function from the migration test, without executing that test module.

It uses the existing backup encryption, HMAC, inventory, archive authentication,
decryption and restore functions. The plaintext source archive and payload are
removed before `verify_archive`. The restored-domain probe runs once inside the
actual owned restore namespace before normal cleanup, without modifying the
production backup helper or its aggregate output contract.

Checks cover exact old/new row fingerprints, encrypted payload/person/guardian
snapshots and lookup digests, all seven nonempty tables, constraints and partial
uniqueness, trigger/function definitions, and runtime immutable account origin
and original approval. Provenance mutation attempts use valid alternate
origin/approval pairs for the same person so a CHECK/FK cannot falsely satisfy
the immutability check. A corrected approval does not replace the account's
original approval. An accountless independent player remains without a student
profile or account.

After an explicit image-build and serial-run grant, use the already verified
interpreter and immutable IDs (the following is a template, not execution
evidence):

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL -u DATABASE_URL_FILE \
  -u ERP_CLOUD_CONNECTOR_RECOVERY_TEST \
  PYTHONPATH="$S/apps/api" PYTHONDONTWRITEBYTECODE=1 \
  ERP_BACKUP_DOCKER_TEST=1 \
  ERP_BACKUP_TEST_API_IMAGE="$API_IMAGE_ID" \
  ERP_BACKUP_TEST_POSTGRES_IMAGE="$POSTGRES_IMAGE_ID" \
  "$PYTHON" -m pytest -q -s --tb=short -p no:cacheprovider \
  "$S/apps/api/tests/cli/test_assisted_personnel_backup_docker.py"
```

Both IDs must be full `sha256:` values already available locally. The new test
does not build, pull, load, change context or contact registries.
The wrapper forces `--pull=never` on every actual container create and rejects
conflicting caller policies. If a previously inspected image disappears,
creation must fail locally; it must not try to retrieve a replacement.

Do not use
`--showlocals`, verbose SQL logging, `-n`, shared DB fixtures, real senders or
`recover-connector`. Do not treat the older generic Docker fixture alone as the
assisted recovery gate.

## Ownership and cleanup

The test captures each created container ID before start. Source/restore
containers use network `none`; ephemeral API helpers share only an exact owned
container network namespace. Labels, ID, network, absent published ports/binds,
and captured anonymous volumes are verified before exec/start/cleanup. Unknown
names, changed labels, changed volumes or other mismatches stop deletion.

Only exact owned IDs are removed with their anonymous volumes. No prefix-based
selection, broad container/volume cleanup, prune, or existing-volume deletion is
allowed. A test-only `docker cp` is restricted to the existing verifier's
sequence-selection file under the owned scratch directory and its exact owned
container destination; it is not a deployment path.

Keys/configuration/plaintext live in an owned mode-0700 directory directly under
`$HOME/.codex/assisted-restore-0066.*`; files are mode 0600. On success the test
removes its exact scratch only after checking the directory, owner marker and
all owned container cleanup. On failure the private scratch is retained for
diagnosis; the only path reported is its opaque basename. Verify ownership and
that all owned containers are gone before removing retained artifacts. Do not
print keys, plaintext snapshots, OTP, config, parameters, or captured stderr.

The mode-0600 `ownership.jsonl` file is an append-only, fsynced private ledger.
It records creation intent, each exact captured container ID before inspection,
expected owner label/network, validated anonymous-volume identities before
start, and each removal request/outcome. A label/inspection/volume/removal
failure is recorded without exception text or subprocess output. A failed
target is blocked from automatic retry and retained for diagnosis; no discovery
by name prefix or broad listing is permitted. Cleanup failure also retains the
scratch and ledger. A finally-path sanitized status always reports the retained
directory basename, cleanup-failure boolean, and remaining/removed counts, even
when cleanup itself fails. If ledger storage fails, stop the operation and
retain existing evidence; do not infer successful cleanup from an attempted
removal or an incomplete ledger record.

An externally built drill image is controller-owned and is not removed by the
test. Delete only its unique tag after verifying that tag still resolves to the
recorded image ID. Never force-remove an image ID, shared base image, or existing
PostgreSQL image. Any cleanup mismatch must be reported as unresolved.

## Forward repair and recovery limits

Once assisted records, approvals or activation challenges exist, do not downgrade
0066 or drop its populated tables. Use a reviewed forward-fix migration/code
release with fresh backup and isolated rehearsal. Historical approval snapshots
and account origin are immutable evidence; do not rewrite them to make old
binaries appear compatible.

Old application binaries do not understand assisted onboarding, account origin,
guardian evidence or the new workflows. Reverting application code alone is not
a data rollback and is not presumed safe on a populated 0066 database. Any
temporary old-binary deployment requires a separately demonstrated compatibility
plan; keep the last accepted release available for operational investigation,
not as implicit permission to downgrade the database.

Restoring a backup recovers its snapshot, not subsequent writes. Before any
separately approved real recovery, record the snapshot time, the interval of
later writes at risk and the reconciliation plan. Isolated recovery success is
not authorization to overwrite NAS data. No actual NAS recovery or device test
is claimed here.

## Historical isolated recovery evidence — earlier candidate, 2026-10-18

This completed rehearsal used the earlier `cc73640` image. Later production
fixes changed the API inputs. Preserve these results as historical evidence
only; do not apply its pass, backup ID or cleanup outcome to the final image.

The controller granted the exclusive serial DB window after other DB execution
had actually exited. Preflight matched local Docker context `colima`, both full
image IDs and the frozen API build-input hash. No shared DB fixture was loaded
(`--confcutdir=apps/api/tests/cli`); no existing worker database or NAS was used.

- Source: 487 files; SHA256
  `3e2adc73ef8561e6b6d85df3d28ca6d1b2b1357cfea9e96d95071457ef3db35b`,
  identical before and after the rehearsal. Hash input: sorted API-relative
  paths + NUL + bytes + NUL for `app/`, `migrations/`, `Dockerfile`, `.dockerignore`,
  `pyproject.toml`, `alembic.ini`; exclude `__pycache__`, `*.pyc`, `.DS_Store`,
  `._*`; reject symlinks.
- API: `sha256:25a3830998717872932a2993e7ffd51025e37bcf236beaa02ab8d0f33d61a084`.
  Owned tag `erp-assisted-restore-0066:20261018-cc73640` remains available and
  was rechecked against this ID; image cleanup remains controller-owned.
- PostgreSQL: `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`;
  approved PostgreSQL 16 local image, with `PG_MAJOR=16` independently read from
  its image metadata. No shared/base image was removed.
- Observed UTC window: `2026-10-18T11:07:43Z` through `2026-10-18T11:08:08Z`.
  Execution session `24330` actually exited 0: **1 passed in 17.21s, 0 skipped**.
- Exactly one restored-domain probe; exact head `0066_assisted_personnel`.
  Old upgrade values, restored row fingerprints, constraints, trigger/functions,
  immutable account origin/original approval, and ciphertext/digest checks passed.
- Snapshot: `2026-10-18T11:07:54+00:00`;
  backup ID `backup-20261018T110754Z-b562c8ef`;
  ciphertext SHA256 `50daed5d748606ebd502645c57634450b6b4105ed886a07e8cf46ed21c9906c2`.
- Cleanup reported `cleanup_failed=false`, 4 exact-owned containers removed,
  0 remaining, private scratch removed, retained directory null. Their anonymous
  volumes were included in each guarded exact-ID `rm --volumes`. No runtime
  fixture/source repair was required; the serial DB lease was released only
  after the actual successful process exit.

| Restored table | Rows |
| --- | ---: |
| personnel_intake_requests | 3 |
| personnel_intake_revisions | 4 |
| personnel_credential_reservations | 1 |
| assisted_approvals | 4 |
| assisted_person_profiles | 2 |
| coach_person_responsibilities | 1 |
| assisted_activation_sessions | 1 |

Historical actual invocation from that candidate's source directory:

```sh
env -u DATABASE_URL -u TEST_DATABASE_URL -u DATABASE_URL_FILE \
  -u ERP_CLOUD_CONNECTOR_RECOVERY_TEST \
  PYTHONPATH=apps/api PYTHONDONTWRITEBYTECODE=1 \
  ERP_BACKUP_DOCKER_TEST=1 \
  ERP_BACKUP_TEST_API_IMAGE=sha256:25a3830998717872932a2993e7ffd51025e37bcf236beaa02ab8d0f33d61a084 \
  ERP_BACKUP_TEST_POSTGRES_IMAGE=sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685 \
  '/Users/skymoonzyj/Documents/new project3/.venv/bin/python' -m pytest \
  -o addopts='' -q -s --tb=short -p no:cacheprovider \
  --confcutdir=apps/api/tests/cli \
  apps/api/tests/cli/test_assisted_personnel_backup_docker.py::test_encrypted_0066_recovery_preserves_domain_and_provenance
```

## Final-candidate recovery — actual pass

The rebuilt final-production image is
`sha256:eae3d50a031005363f0314bfc5d69e47e14ed4a7c65081bfa3d3d6fee3e09c4a`,
owned tag `erp-assisted-restore-0066:20261018-d06e57e`. Build-input identity is
487 files, SHA256
`321368c30ba013bfc965567378af355127aab6dec2d90ab36f174e287e6075c5`, using
the same hash definition as above. The controller recorded identical pre/post
build hashes and the `d06e57e` commit label. The PostgreSQL immutable image is
unchanged from the historical rehearsal above.

Under an exclusive serial grant after actual preceding DB exits, the dedicated
recovery node ran at HEAD `a165b9c` against those immutable images. The API input
hash/count and image/tag identities matched both before and after execution.
The command and guards were unchanged from the historical invocation except
for the final API image ID above; `--confcutdir=apps/api/tests/cli` still excluded
shared database fixtures. No pull/build or NAS recovery occurred.

- Session `10496`: **actual exit0, 1 passed in 16.57s, 0 skipped**.
- Observed UTC window `2026-10-18T12:34:40Z` to post-exit verification
  `2026-10-18T12:35:14Z`; snapshot `2026-10-18T12:34:52+00:00`.
- Exact head `0066_assisted_personnel`, one restored-domain probe;
  legacy upgrade values, new row fingerprints, constraints, triggers/functions,
  immutable account origin/original approval and authenticated encrypted
  snapshot/decryption checks passed.
- Seven table counts, in the same named order as the historical table above:
  requests3 / revisions4 / reservations1 / approvals4 / profiles2 /
  responsibilities1 / activation_sessions1.
- Backup ID `backup-20261018T123452Z-bdffdbc2`; ciphertext SHA256
  `2aa6d8c14f7088260c08e93696a551956ab7df56d88cdecbd2c0e6f3db02944b`.
- Cleanup: `cleanup_failed=false`, four exact-owned containers and their
  guarded anonymous volumes removed, zero remaining, private scratch removed,
  retained directory null. The DB lease was released after actual exit/cleanup.
  API/PostgreSQL images and unique API tag remain; no shared-resource cleanup.

This is actual final-image proof, not an inference from the earlier rehearsal.
Retain the unique tag until the controller makes a separate guarded cleanup
decision. This isolated success never authorizes a real NAS restore.

## Final browser matrix — actual pass

The unchanged browser specs ran through five separate hardened-runner processes,
using synthetic data/private mail and canonical then supplementary fixtures.
The application source stayed `d06e57e`; intervening commits only changed the
reported test contracts and standalone complete-check orchestration.

| Selector | Viewports | Tests | Runner actual exit | Seconds |
| --- | --- | ---: | ---: | ---: |
| assisted | desktop-1440 / mobile-360 | 6 | 0 | 79.48 |
| team | desktop-1440 / mobile-360 | 12 | 0 | 41.37 |
| registration | desktop-1440 / desktop-1024 / tablet-768 / mobile-360 | 8 | 0 | 132.16 |
| housing-occupancy | Same four | 8 | 0 | 103.96 |
| housing-reporting | Same four | 8 | 0 | 56.09 |

Both assisted financial baselines matched. That run's observer separately
exited1 in a post-run plain socket-bind probe, **not** the runner/business tests.
Bounded independent checks found no listeners, refused connections, successful
canonical SO_REUSEADDR rebinds and zero private runtime directories; cleanup was
verified before the next selector. Do not report the observer itself as exit0.
The other four observers exited0; each selector's owned ports/private runtime
cleanup completed before the next launch, and the lease was released after the
final selector. This is browser coverage, not native keyboard/device coverage.

## Final NAS release evidence — pending

Packaging checks passed for both the implementation script and the actual
startup-repository launcher: **5 packaging checks and 36 deployment unit tests
per script**. These are local tests, not deployment. A read-only dry-run found
source `0066_assisted_personnel` compatible with NAS
`0065_invited_housing_visibility`; its planned release identity is not active.

At this source-document snapshot, NAS preflight/deploy result, target health,
source/release identity and backup reference remain pending; any failure remains
a release blocker. The controller's separate `release-verification.md` must
record the actual final API result, checked-script deployment exit, active
release/source identity, migration head, backup reference, health/scheduler and
static-output checks. It does not substitute for any failed gate. Neither
isolated rehearsal performed NAS recovery, real-person mailing or device tests.
