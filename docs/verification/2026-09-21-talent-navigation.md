# Talent-test navigation and reaction average

## Approved repair

- Allow any catalog item to start a new round. Retain the default-first shortcut.
- After server-confirmed save, show a link to the first remaining item in the
  round's frozen catalog, plus a return-to-list link.
- Display the server's five-trial reaction-time average before its score.
- Preserve completed-round report navigation, legacy membership, pending-result
  recovery, creation idempotency and account/person isolation.

## Root cause and scope

The result host rendered only the saved score. New-round cards had no selection
controls, while the single start button always navigated to the first item.
Browser coverage previously loaded each game URL directly, bypassing the missing
user navigation. Reaction scoring already used the arithmetic mean of exactly
five samples, rounded half up to milliseconds; no scoring, API, database or
historical-result change is required.

The repair also suppresses the previous item's saved banner after continuing to
an unsaved item. A post-create context-refresh identity guard prevents a delayed
request from navigating a newly signed-in person to the prior person's round.

Real touch acceptance exposed a further navigation defect: the fifth reaction
pointer-down completed the game and collapsed its stage, then touch-up retargeted
the same click to the return link. A captured unintended return-link click and
route change confirmed this before release. Navigation now accepts a pointer
click only if its press originated on that same link; keyboard/assistive clicks
remain supported, without delaying or changing reaction-time measurement.

## Local evidence

- Host RED: six missing UI assertions failed before the fix.
- Navigation status RED: reproduced a stale `结果已保存。` banner on the next item.
- Host GREEN: 55 tests across three host suites passed.
- Touch-retargeting RED: both return and next links reproduced unintended
  navigation. After the guard, all 58 host tests passed, including deliberate
  fresh clicks and keyboard Enter activation.
- Entry-page RED: ten selection/retry/membership cases failed before controls
  existed; a separate identity-switch test reproduced late navigation.
- Entry-page GREEN: 24 tests passed, including all eight possible first items.
- Final full frontend after the gesture fix: 145 files, 1,488 tests passed
  (43.27 seconds).
- Backend scoring/contracts: all 160 pure tests passed, retaining the existing
  five-sample validation and half-up mean-before-score calculation.
- ESLint and TypeScript/Vite production build passed. The existing large-bundle
  advisory remains; no unrelated bundle refactor is included.
- Independent bounded code review approved the host and entry-page changes with
  no findings. Browser and release verification remain separate gates.

The final desktop browser lane passed two complete rounds (16 results, 2
reports, 6 expected audits). The real-touch lane passed one complete round
(8 results, 1 report, 2 expected audits), including the previously failing
fifth reaction tap. Desktop and touch screenshots visually confirm the mean,
score and navigation controls. All five dynamic-vision viewport lanes also
passed: the canonical matrix has 7 passes, no failures, retries or skips.
Mandatory NAS release verification also passed. Artifacts are under
`.superpowers/bugfix/2026-09-21-talent-navigation/`, with final browser evidence
in `browser-final3/`.

## NAS release receipt

The required launcher `scripts/deploy-nas.sh --source .nas-release-sync` exited
0, and NAS `.deployment-state` is `complete` for release
`20260921T140045Z-d6e1accd0692-62775eb5f292`.

- Public login and health endpoint: HTTP 200, TLS verification 0.
- Public and local `index-DnwQVbtZ.js` SHA-256 match:
  `ce53f93b5810fb87f035d8843be4d4a29706537259f5870915021bac46b386d3`.
- Alembic remains `0059_talent_dynamic_vision`; no migration was needed.
- API, Web, proxy and database healthy; scheduler and Caddy running.
- Scheduler and operations-monitor heartbeats healthy with zero consecutive
  failures.
- Encrypted backup creation, archive authentication, isolated database restore
  and restored-database verification completed successfully.

Only this verification receipt was appended after deployment; application and
test sources were frozen before the final browser run and release.
