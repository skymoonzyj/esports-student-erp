# Housing care Task 7 sanitized validation evidence

As of 2026-10-23. Synthetic local data only; not a NAS release statement or physical-device acceptance.

| Gate | Actual result |
| --- | --- |
| New care/photo/export browser launcher, 1440/1024/768/360/touch390 | Exit 0; 25 passed |
| Old room-status foundation, 1440/1024/768/360 | Exit 0; 4 passed |
| Synthetic focus/reauthorization desktop1440/mobile360 | Exit 0; 2 passed |
| Old occupancy lifecycle/revocation/profile/person/history across four viewports | Exit 0; 40 passed |
| Old immutable reporting lifecycle + authorization/revocation at four viewports | Exit 0; 8 passed |
| Actual encrypted backup/restore with current immutable API image | Exit 0; 1 actual rehearsal passed; all 9 domain assertions true |

New browser proof includes actual UI checkout/cleaning/photo upload-replace-lightbox-delete/export, current XLSX/CSV download parsing with empty-room/current-only data, retained historical export columns/people, multi-resident departure epochs, source-only transfer dirtiness, exact-role permission and private404 boundaries.

Browser total: **79 passed**. All nine own loopback ports checked free after owner-scoped service cleanup. Browser DB lease returned to controller; separate regression DB untouched.

No screenshots, traces, videos, session cookies, keys, download PII or restored row values are included here. Private test output directories are removed by owner-scoped cleanup. Browser services bind only task-owned loopback ports. Recovery uses exact immutable IDs, private network namespaces, fresh random ownership labels, anonymous volume ledger guards, and preserves failed private evidence without exposing values.

## Actual recovery proof summary

- API image: `sha256:3f5eec6a31885dae13517e9249f853f2d1e5edb3be28af8d8fca249bd57df76e`
- PostgreSQL image: `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`
- Backup: `backup-20261023T095032Z-1097dbb4`; snapshot `2026-10-23T09:50:32+00:00`
- Encrypted archive SHA-256: `edb3de2697be77d9d8bf460f7a971e6f2a73a88198d9a9d16eaf49dff41ed5c1`
- Bound production source SHA-256: `ae4a4baeb1b95a79f96444cb637a2d8659141506df63f1c538aa81a94dfd3bd4`
- Restored schema SHA-256: `8fa9b2ce8562a6a59c6a72c6ac8f92d2246a2fdb721472d6d25af9facbf314d1`
- Restored revision: `0071_housing_room_care`; recognized fresh restore probes: 1
- Preserved assertions (all true): original photo bytes, thumbnail bytes, coherent complete proof, room care state/version/events/actor, care/photo audits, complete people/accounts/stays/transfers row hashes.
- Ownership cleanup: 4 removed, 0 remaining, successful scratch removed. Failed preliminary attempts retained private diagnostic evidence, with their own containers removed; no shared volume cleanup performed.

After controller authorization, exactly three known failed-run scratch directories (`4ga42yi3`, `qxqy7jty`, `7fzkulm5` under the supported `assisted-restore-0069.` prefix) were also removed. Each passed private owner/ledger/created-ID checks and exact Docker absence checks for its two recorded container IDs before reused guarded cleanup. All six earlier container IDs were absent. No unrelated directory or shared Docker resource was modified.

## Final frozen image recovery rerun (2026-10-23)

After the audit-only old/new boolean fix, the same exact opt-in pytest encrypted recovery test was rerun on the final immutable API image. **Exit 0, 1 actual recovery passed, all 9 domain assertions true.** No application or restore code was changed. Earlier proof above is preserved as historical evidence.

- API image: `sha256:e97beae9db6e937e19280da008c504bf24c0a02002e3b1eae601c208b173de97`
- PostgreSQL image unchanged: `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`
- Bound production source SHA-256: `73341481d5e76eb59aa3c6bf89e43f2ed9107a831c33427cc86e0c8bfb0753b9`
- Backup ID: `backup-20261023T100616Z-7f54f562`; snapshot `2026-10-23T10:06:16+00:00`
- Encrypted archive SHA-256: `99e2d49658441c8e4860aaab858a8c41d7f66771ffc989bbeb0a5483b80e1879`
- Restored schema SHA-256: `8fa9b2ce8562a6a59c6a72c6ac8f92d2246a2fdb721472d6d25af9facbf314d1`
- Restored revision `0071_housing_room_care`; recognized fresh restore probes: 1.
- All true: `accounts_sha256_preserved`, `care_photo_audits_preserved`, `care_state_version_and_events_preserved`, `complete_coherent_seed_and_restore`, `original_photo_bytes_preserved`, `people_sha256_preserved`, `stays_sha256_preserved`, `thumbnail_bytes_preserved`, `transfers_sha256_preserved`.
- Cleanup: 4 own containers removed, 0 remain, private scratch removed, no retained directory or cleanup failure.
- Original care DB and both controller regression DB partitions untouched; no shared resource operation or NAS deployment.

## Controller final regression record

The dirty release-source worktree is preserved. No commit, reset, shared-database reset or production test-resident mutation was used. Full backend coverage was split across independent task-owned databases; a single schema-resetting process owns each database. The main `scripts/check.sh` was not executed because its older shared-runtime reset would affect another active project. Its orchestration is covered by behavioral tests and the new care gate runs with its own newly owned database.

| Check | Actual result and qualification |
| --- | --- |
| Final web suite | 269 files / 2,905 tests passed; production build and ESLint passed |
| Broad backend static checks | Ruff `app tests` passed; Mypy `app` passed, 448 source files |
| Full API partition | 2,098 passed / 1 failed initially; the process had collected the old immediate-backfill lifecycle test before the authorized cleaning-step update. Complete final related four-file rerun: 201 passed. The initial command is not reported as exit 0. |
| Full database partition | 884 passed, exit 0, including migration ordering and password-reset concurrency |
| Full services/unit/contracts partition | 4,468 passed / 4 failed initially: three old immediate-backfill fixtures and one unordered portal candidate selection. The three fixtures were corrected with real cleaning and admission-refusal assertions; complete related two-file rerun: 35 passed. Portal final correction and review are recorded separately below. |
| Final feature browser rerun | 31 passed: new care 25, existing foundation 4, focus 2; overlaps the earlier 79 and is not added as unique coverage |
| Current production API bytes | Recomputed SHA-256 `73341481d5e76eb59aa3c6bf89e43f2ed9107a831c33427cc86e0c8bfb0753b9`, identical to the final successful encrypted recovery image proof above |

Earlier non-API execution stopped at its failure threshold (1,946 passed / 8 skipped / 10 failed). Diagnosed historical-fixture and synthetic-seed compatibility defects were corrected and independently reviewed; subsequent exhaustive directory partitions replace that incomplete run. The earlier trace for a password race was truncated, so schema/data pollution was not asserted as its proven cause; the entire later database partition passed without modifying those password tests.

The large web bundle advisory remains preexisting and non-blocking. Five browser viewport sizes, including touch simulation, are not physical-iPhone acceptance. Photo support is JPEG/PNG/WebP, not HEIC. Production history, people and existing room capacity are not rewritten by this feature.

Final CLI, remaining narrow test-helper review, NAS release, and post-release preservation checks were still pending when this section was written. Later entries supersede that status only with actual results.

## Final local gates closed

- Complete CLI partition: **1,344 passed / 8 conditionally skipped**, exit 0, 562.67s. Skips are not counted as passes. The housing-care encrypted restore opt-in was separately executed successfully above.
- Final new gate-helper covering group: **163 passed / 1 opt-in skipped**, exit 0, 75.49s. This full-file rerun covers the latest helper bytes even though the earlier full CLI process collected before the final helper patch.
- Final standalone care gate: **25/25**, five viewports, exit 0. Its exact owned container, network, anonymous volume and scratch were removed and independently verified absent.
- Portal intermittent test root cause: its unordered class candidate query included a cancelled enrollment while the product roster correctly returns only active enrollments. A deterministic ordering experiment reproduced `StopIteration`; the test-only repair filters active enrollment and orders by person ID. All passport, pending-profile, missing-birthdate and unknown-age assertions remain. Complete portal + assisted-housing files: **35 passed**, exit 0, 25.41s. Independent spec/quality review passed.
- New test-gate process-group cleanup correction: real leader-exit/TERM-ignoring-descendant failure reproduced, corrected, and independently reviewed as addressed. The macOS zombie-only EPERM branch requires exact group evidence and rejects live/unknown/malformed cases. No outstanding review findings.
- Broad Ruff `app tests`, Mypy `app` (448 sources), and `git diff --check` passed after the compatibility changes. No production API bytes changed from the successful restore fingerprint.

The exhaustive backend partitions are API, services/unit/contracts, database, and CLI. Initial failures are retained above; final complete affected-file reruns close them without excluding tests or weakening application constraints. Counts from overlapping reruns must not be summed as distinct tests.

Local implementation and verification are complete. NAS deployment and post-release checks remain a separate gate, recorded below only after actual execution.

## NAS release completed — 2026-10-23

Executed the unchanged paired release-source standard script from `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`:

`PATH="/Users/skymoonzyj/Documents/new project3/.venv/bin:$PATH" bash scripts/deploy-nas.sh --source '/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3'`

Session 99452 exited **0** after the required encrypted backup and isolated restore verification. No direct release copy, ad hoc Docker deployment, secret replacement, database-volume deletion, or bypass of preflight was used.

- Active release and `.deployment-state` agree: `20261023T104116Z-62cb1a29b325-b45cb60ae312`, **status=complete**.
- Source 0071 / existing NAS 0070 preflight passed; a database dump was created before migration. Actual new revision: `0071_housing_room_care`.
- NAS API image: `sha256:112511c6201608f1fe447a781eecc16a2c8e951f916dc62f4723c5749393d4bc`; web: `sha256:425f16f76ecb75f4fef6ec5b7e4e1217bba9f7eef968b8115d03f860f8823d10`; scheduler: `sha256:eef66f4f5ac9c06655af067333c5906dd95196f3d034b3d641638c2576b04f0f`.
- Running NAS API production source fingerprint was independently recomputed: `73341481d5e76eb59aa3c6bf89e43f2ed9107a831c33427cc86e0c8bfb0753b9`, exactly matching the locally verified/restored production bytes.
- Final backup ID and verified backup ID both `backup-20261023T104322Z-4d8692bc`; backup status **ok**, stage **complete**, last success `2026-10-23T10:46:01+00:00`.
- API/web/proxy/database healthy, scheduler and Caddy running; original PostgreSQL container remains up for two weeks (not recreated).
- Public `/login` and `/housing` return HTTP 200 with TLS verification result 0; `/api/v1/health/live` returns `{"status":"ok"}` at the existing HTTPS port 18080. This proves public reachability, not a new authenticated online write test.

Read-only repeatable-read comparison after migration (only counts and hashes, no personal row values retained):

| Data | Rows | Before = after digest |
| --- | ---: | --- |
| Existing room fields, excluding five newly added care columns | 212 | `f05f3d751bfb6972f9adaf68588f6398` |
| Housing stays | 74 | `c46f00b3857c597eece6ebccc7d91039` |
| Transfers | 3 | `99e37bac1ba2bd623be6a7eedd3596d1` |
| People | 77 | `7cf1898f70574fd8e1c1a2869f00c191` |

All 212 existing rooms have false/ready/null initial care fields; photos initially zero. No historical checkout was replayed, no actual person was checked out, and no trial photo or financial record was added on NAS.

After all test-runner leases were released and zero other database clients were proved, the exact task-owned local database container and its sole anonymous volume were removed; both exact IDs independently verified absent. Gate and restore drills had already removed their exact owned resources. Other projects, shared databases and production volumes were untouched. The detached worktree and ignored execution evidence are retained because source remains uncommitted with preexisting shared edits.

User entry: **房态管理 → 房间详情/编辑** for daily cleaning, pending cleaning completion and optional photos; **导出** for current room-state filters or preserved historical-stay mode. Physical iPhone validation and HEIC support are not claimed.
