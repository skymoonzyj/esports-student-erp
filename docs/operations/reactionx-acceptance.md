# ReactionX restricted integration acceptance

This record distinguishes implemented source and synthetic evidence from an
installer, physical hardware acceptance, a NAS release and permission to open
formal leaderboards. A missing installer is **unavailable**, never a fabricated
download. Synthetic PE bytes in tests are not a deliverable.

## Current gate status

| Gate | Status |
| --- | --- |
| Source integration | Implemented for root review: live-authorized release HTTP, stream cleanup, endpoint body bounds, explicit server-only synthetic policy and isolated acceptance tooling |
| Simulated checks | Fresh five-case real browser/fake-connector lane passed,0 skipped. Persisted baseline5 results /182 events remains independent of1 archive /2 archive audits /0 archive results or events. RunPanel terminal display and stopped polling passed; root combined review/regression remains separate |
| Windows 11 / Chrome / Edge / real six lights | Deferred by user; not executed or counted as passed/skipped acceptance |
| Installer / SHA-256 / signing | No real EXE supplied, no real package hash/signature measured; signed claims currently unsupported/rejected |
| NAS publication | Worker has not deployed; root must run the approved script and verify backup/migration/active release/health |
| Formal opening | Closed: all eight formal hardware modes remain unavailable/unverified in normal environments; manual practice never ranks |

## Safe isolated acceptance lane

Use only an explicitly reserved disposable PostgreSQL database. The existing
launcher requires `TEST_DATABASE_URL`, encryption/session secrets, three distinct
evaluation service tokens, local Python/Vite/Playwright and free loopback ports.
The root controller supplies these privately; do not copy operational secrets
into commands, reports or source control.

```sh
E2E_CORE_MODE=reactionx scripts/run-core-e2e.sh
```

The `reactionx` lane is registered exactly once under the core runner; `all`
executes it once after ordinary lanes. It seeds synthetic accounts and three
accountless profiles, probes default-closed real HTTP, stops/waits the ordinary
API, starts a new API with both `APP_ENV=test` and explicit server-only
`REACTIONX_SYNTHETIC_FORMAL_ENABLED=true`, runs the dedicated browser project,
independently verifies persisted evidence, and restarts the API with formal
policy closed. No ordinary core/talent/esports API inherits this opt-in. Both
flag and `_FILE` form are removed from Vite/browser environments; the flag is not
a request field, public API setting or client-controlled validation choice.
Settings reject opt-in outside test; the server factory independently closes
formal policy outside test. Frozen `RULES` verification and UI formal cards stay
unverified/closed, including within the synthetic lane.

The process closure probe does **not** infer execution policy from `/modes`:
that metadata deliberately remains closed even when isolated synthetic execution
is enabled. The probe freshly logs in with real session/CSRF, performs two-sided
pairing using a separately generated machine credential and cookie jar, sends
valid compatible device inventory, and submits a validated fixed standard-mode
prepare for an authorized synthetic profile. Only HTTP409 with exact code
`reactionx_hardware_unverified` passes. An unrelated403/409/422 is a failure,
not closure evidence. The matching enabled-policy negative regression requires
the same valid request to be accepted202, makes the closure probe fail, and
cancels that prepared-only run through real stop/poll/OFF ACK before revoking
the connector and logging out. It never delivers prepare, issues start or
fabricates a result/receipt to clean up a failed closure assertion. The ordinary
closed probe creates no run or result. Pairing throttles remain enabled.

The dedicated `reactionx-desktop-1440` project uses a Windows user-agent only to
exercise the existing UI branch. It does not emulate Windows Bluetooth, DPAPI,
an EXE, Chrome/Edge installation or six lights. Traces/screenshots/videos are
disabled. Raw browser/service/CLI output stays in an umask077 temporary directory
and is deleted on exit. Durable summaries contain allowlisted case IDs/statuses
and counts only, never pairing challenges, machine headers, credentials, DOM,
arbitrary errors, stdout or attachments. A launcher/configuration failure is
reported separately from an executed product case failure. Skipped/missing cases
cannot pass the summary gate.

## What is exercised

- `RX-UI-001`: actual UI two-sided pairing, authorized accountless subject,
  manual prepare/start/action/stop, matching connector OFF, exact original result
  retry after ignored application ACK, receipt recovery and saved report.
- `RX-HTTP-002`: formal natural completion is explicitly **HTTP coverage**, not
  a formal-control UI claim. The same real browser session/CSRF and paired tab
  call prepare/start; the independent fake connector uses real machine routes to
  poll, ACK and submit complete deterministic synthetic event evidence for three
  subjects. Anonymous top-three/rank/score gaps are then read via HTTP/report UI.
- `RX-UI-003`: disconnect does not prove OFF; explicit OFF remains required,
  connector revoke denies machine access, ordinary coach cannot operate.
- `RX-UI-004`: logout/account switch clears current document control/private
  state without pretending logout proves saved results or lights OFF.
- `RX-UI-005`: separate guarded synthetic superadmin performs a genuine fake-
  connector delivered START with no START ACK, then real STOP/OFF HTTP, strict
  uncertainty declaration and actual two-step archive UI. It verifies the reason
  request alone does not archive, a separate confirmation does, repeated confirm/
  machine retrieval preserves the same archive receipt, and late result upload
  fails with exact `reactionx_uncertain_closed`. This creates no result or rank.
  The actual RunPanel shows the archive terminal state, no longer claims pending
  save, and stops its ordinary two-second run-status polling.

The fifth case uses the existing `life_teacher` synthetic credential promoted
to a second superadmin **only inside the guarded ReactionX seed**. Original
coach-denial coverage remains unchanged. This avoids exceeding the original
admin's five-pairing/minute budget (one closed-policy probe plus four old cases);
neither a limiter reset nor a time-dependent wait is used.

The fake connector generates its own secret and claims/confirms through actual
HTTP. No active pairing, run, receipt, ranking row or result audit is seeded.
Persisted verification re-reads original actor/subject/session/tab/connector
binding, manifest/result digests, exact event sequence/storage, recalculated
metrics, one saved audit per result, manual exclusion and three-person ranking.
The extended verifier keeps exactly5 existing results /182 events independent
from exactly1 `uncertain_closed` archive. For the archive it separately checks
original actor/subject/session/tab/connector and manifest/declaration/receipt
bindings, delivered original START with NULL ACK, original STOP/completed full
OFF evidence, exactly2 request/archive audits, and zero result/event rows. The
unchanged three-person ranking still derives exclusively from real result rows.
The UI must show “执行状态未知，需复测” and explain that backend archival is not
proof the original connector received and protected-persisted its receipt or
released local quota. The browser fake connector does not implement the D2
protected store; actual connector/receipt/quota evidence belongs to separate C6.
Process-interrupted activity time is only a last-observed lower bound, never a
complete duration. Original Talent1 eight-item round and Talent2 behavior remain
separate and require root regression; ReactionX does not replace either.

## Release / deployment boundary

Metadata and download freshly require an effective actual superadmin with
`reactionx.manage`, not only a cached role or known release ID. The optional
reviewed bundle contains exactly `manifest.json` and
`ReactionXConnectorSetup.exe` at the fixed read-only API mount
`/app/runtime/reactionx-release`. Downloads use verified frozen descriptor bytes,
not a user-selected path; cleanup runs after iteration, disconnect and failure
before the first chunk. Missing metadata is unavailable; malformed metadata is a
generic503 and missing/stale/invalid downloads generic404 without filesystem
details. A verified unsigned package is internal-test-only and is not formal
mode authorization or proof of publisher identity/install safety.

Production proxy limits are 256KiB for generic ReactionX requests and 8MiB for
the fixed machine final-result route. API limits use actual streamed bytes,
including absent/false Content-Length. Pairing claim throttle still runs before
the first body read. Unrelated uploads retain global25MiB and trusted forwarding
plus response security headers are unchanged.

Only root deploys, using `scripts/deploy-nas.sh --source "$PWD"` (optional
reviewed external `--reactionx-release-bundle` input only when a real validated
package exists). Do not bypass the script, modify DB volumes, reuse an old
package when omitted or generate production synthetic learner results. Failed
NAS preflight/deployment blocks release.

Remaining real acceptance: known-build Windows installer and manifest; actual
hash/signature status read from it; install/upgrade/DPAPI checks; Windows11
Chrome/Edge plus real six-light nine-mode execution, notification attribution,
late-notification isolation, exit/rest interruptions and stop-failure behavior;
per-mode ranking approval only after all evidence. A mode whose notification
ownership cannot be proved must stay closed. Root also owns full backend and
frontend regressions, reactionx/talent/esports lanes, integrated independent
review and NAS release evidence. No all-green release claim follows from this
document or a passing synthetic test alone.
