# Frontend loading verification

This procedure is local, disposable acceptance, not NAS deployment or native-device
acceptance. The release still uses `scripts/deploy-nas.sh` in the separate approved
release step. Keep `ERP_NAS_AUTO_DEPLOY=0` for intentionally paused acceptance commits.

## Production structure and budgets

The initial authentication shell contains only the explicit lightweight contracts.
Business route implementations, optional correction/intake/procurement/QR panels and
the eight legacy talent game engines load through finite, source-owned module gates.
Shared CSS must complete before its dependent module mounts. CSS failure is sticky
for that document; retrying another consumer must not expose unstyled content.
Account/module identity changes clear private module state; ordinary same-account
reauthorization must not remount a successful draft.

Build metadata is private: `apps/web/.build-metadata/vite-manifest.json` and
`bundle-graph.json` are not served. Verify actual emitted static closures, not only
source imports or chunk names:

```sh
npm --prefix apps/web run build
node apps/web/scripts/check-loading-graph.mjs \
  apps/web/.build-metadata/vite-manifest.json \
  apps/web/.build-metadata/bundle-graph.json
node --test apps/web/scripts/*.test.mjs
```

The immutable accepted baseline is product commit
`95da4227ae831eeb4edfd75d3fc31bcdaa8882ae`, in the private Task1 accepted-baseline
evidence directory. It has 280 valid samples: 40 groups, seven each. Cold-login JS
response bodies are 517022 gzip-encoded bytes; CSS is 70314 encoded bytes. The
40% JS reduction gate is at most 310213 integer bytes. Every group's ready-time
median may regress by at most 10%; retain all samples, including the slowest.
Unsupported metrics are null, not zero. Warm zero JS transfer does not mean zero
requests. SPA FCP/LCP are null because they are document metrics.

Accepted throttled baseline timings (ms):

| View/page | Cold ready median | Cold ready worst | Warm ready median |
| --- | ---: | ---: | ---: |
| Desktop/login | 1183.0 | 1195.5 | 258.0 |
| Desktop/housing | 1903.0 | 1919.7 | 912.3 |
| Desktop/personnel | 1810.2 | 1847.7 | 878.0 |
| Mobile/login | 1185.1 | 1214.4 | 255.7 |
| Mobile/housing | 1933.7 | 1954.3 | 955.1 |
| Mobile/personnel | 1802.8 | 1847.2 | 875.5 |

Final candidate graph, encoded totals, all-group comparisons, failure counts and
median/worst timings are recorded in Task11's report/evidence; acceptance is pending
until its final gates complete. Do not substitute earlier green unit or browser runs
for a missing final measurement.

## Fixed acceptance entry points

Use the existing Python environment with API dependencies and installed Playwright
browsers. Run from the committed worktree without dotenv or inherited application,
SMTP, database, Compose or Vite overrides. The launcher deliberately rejects them.
Its fixed Docker context/config are local Colima and the private SDD probe config.
No external origin, command, spec or existing database selector is accepted.

```sh
env -i HOME="$HOME" PATH="$PATH" LANG=C.UTF-8 "$PYTHON" \
  scripts/run-frontend-loading-acceptance.py \
  --baseline-source "$WORKTREE" --candidate-source "$WORKTREE" \
  --phase named --check state-safety --evidence-dir "$NEW_PRIVATE_EVIDENCE"
```

The fixed named checks are `module-recovery`, `style-order`, `route-loading`,
`talent-games`, `optional-panels`, `esports-games`, `state-safety` and
`legacy-functional`. A new evidence directory preserves failed attempts. Do not
overlap browser owners or database resets. For compare, copy the original accepted
`baseline.json` byte-for-byte into a new private evidence directory, verify its hash,
then use `--phase compare` without `--check`. Do not run builds, tests or other heavy
work concurrently with the 280 serial measurements. `comparison.json` includes
per-group actual percentages, aggregate JS/readiness gates, exact sample counts and
failure reasons; nonzero exit is a failed gate, not permission to alter thresholds.

## Coverage and limits

| Check | Actual environment and assertions |
| --- | --- |
| release-window | Production A/B/C/D images, one long-lived browser, unchanged API/DB; both 1440 desktop and 390 touch contexts |
| state-safety | Production Web, real test API/DB/auth, real permission revocation, same-node drafts, delayed real imports, private-mailbox registration/activation |
| esports-games | Original six fixed spec/project/seed/verifier entries and real elapsed game timing; then paired synthesized-audio resource/gesture/native-completion checks |
| legacy-functional | Original five hardened script suites, 28 serial spec/project invocations, local loopback API and Vite development server; **not production loading proof** |
| earlier approved Task9/10 | Preserve their exact source/image provenance, game/static-closure evidence and 16 Task10 production cases; do not relabel them as a newer build |

The release fixture changes exactly two files only inside temporary archived build
trees: an inert History root data attribute and unused reports CSS custom property.
Literal A/B/C/D markers force distinct actual JS/CSS paths and hashes. Stopped image
readers have no network or mounts and share owner cleanup. Real current HTML/build
HTTP bytes must match each image before an activation receipt is recorded.

A tabs load their original JS/CSS after actual B and C switches. For D, only the
production retention selector's supplied test time advances to actual B receipt time
plus seven days and one second; no OS/browser clock changes. The successful History
contexts do not intercept network. Independent PersonDetail contexts hold one real
reports CSS request while the genuine sibling correction form is edited, then let
that request reach D's real 404. This is a CSS-only failure proof; separate HTTP
requests prove both old JS and CSS are expired. No automatic refresh, logout or
submission is allowed; cancel preserves the original editable node/value, and only
explicit confirmation navigates. Controlled textarea labels can incorporate their
value in Playwright's exact text matching: capture node identity before typing and
use the unique textarea in the actual correction region afterward.

Only state-safety uses the separately fingerprinted email topology: API/proxy share
their owned network namespace; API binds 127.0.0.1 with `--no-proxy-headers`, proxy
uses that actual loopback upstream and strips forwarding headers, and only the API
namespace publishes selected loopback HTTP8080. The private sender remains guarded
by the unchanged product code. Preflight requires UID10001, 0700 mailbox directory,
0600 sentinel/file, no SMTP environment/settings, and actual private sender selection.
The first code must come from the original private-mailbox CLI; there is no SMTP
fallback. The owner-only bridge accepts at most eight ordered, fixed-schema messages
and exactly the three approved synthetic recipient aliases. Browser workers have no
Docker or database authority. Exact account/person/application/business counters and
the original financial baseline are verified after the scenario.

Audio is synthesized PCM: the five actual preview HTML/CSS/module resources must be
ready, native user activation must accompany real clicks, and the engine's native
completion must enable the next controls. This does not prove audible output,
headphone routing, hearing or physical-device behavior. Headless refocus uses the
existing helper and is not proof of physical tab/window switching. Headed game
focus/input failures must be reported as environment blockers, never forced/faked.

No screenshot, trace, video, raw console text, DOM, passwords, identifiers or OTPs
belong in retained evidence. Keep fixed stages, booleans/counts, status categories,
source/manifest/image digests and actual exit codes. Task9's prior console counts
remain unattributed evidence limitations; passing later runs cannot explain them.
The known development-dependency audit findings remain unresolved; production audit
zero is not a complete reachability or security proof. Native iOS WIP is excluded.

On interruption or failure, stop only owned child sessions and validate exact
container IDs plus owner/project labels before cleanup. Namespace-dependent proxy
must be removed before API. Never prune Docker, delete shared images/volumes, reset
an existing database, or restore business data to repair an acceptance failure.
