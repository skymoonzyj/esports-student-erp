# Person profile assignments implementation

Spec: `docs/superpowers/specs/2026-10-25-person-profile-assignments-design.md`

## Global Constraints

Work only in `/Users/skymoonzyj/.codex/worktrees/profile-housing-edit/new project3`. Preserve unrelated dirty changes. No commits, stage, pushes, resets, production data edits or credential reads. Use apply_patch, TDD and real behavior tests. A single database pytest process owns the disposable test schema at a time. Do not stop other projects. Actual authorization is checked server-side; no implicit activation, RBAC grants, orders or payments. Keep signed approval history, housing provenance and single-current-team invariants. Production release only through `scripts/deploy-nas.sh --source "$PWD"` after applicable checks. Final evidence distinguishes tests, review and release.

## Task 1: Backend business identity and student assignments

Ownership: `apps/api/app/identity/assignments/`, identity router/schemas/service/models as needed, personnel-intake and team-management integration strictly needed, migration `0074_person_profile_assignments.py`, backend tests for this feature. Own migration manifest/head updates in scripts and DB/CLI tests. Do not edit frontend.

Read the spec above completely and existing `approved_facts.py`, `responsibilities.py`, `write_support.py`/team access, identity student-number allocation. Implement the GET/PUT contract exactly. Null business kind means neither student nor player; adding retains StaffProfile. Existing StudentProfile, approved player/both, actual team_member role or membership count toward business identity compatibility. Additive identities only, no removal, explicit409.

Create audited independent state/project history/operation models. Do not modify authenticated intake kind/revisions or activation-role derivation. Existing StudentProfile number allocator must be reused. No account creation, activation, RBAC changes, order/payment/enrollment side effects. State.version plus current assisted version, student status, active coach/project relation IDs form opaque token; check locked current state. Same actor/key+payload retries return prior result without duplicate writes/audit; current authorization rechecked before replay; different payload conflict. Replay result must not expose newly forbidden data.

Read requires fresh people.read scoped to target. Write requires actual current admin/super_admin plus target people.update, rechecked after sorted participant/account locks. Use existing in-transaction responsibility helper; selected coaches must be eligible and assigned to at least one selected active project. No hidden legacy coach erasure. GET must show current selected IDs even if inactive, with display labels enough to explain unavailable legacy selections (add `current_projects` and `current_coaches` arrays matching candidate label shapes if needed, coordinate frontend immediately). Nonstudent current legacy coaches may display; leave unchanged when adding player only, but any edited student-coach set must validate selected projects. Define and test this exact compatibility rather than silently removing data.

Accountless player candidate exception requires trusted live approval and independent player state; active-account rules unchanged. Account role management capability uses existing live permissions.manage rules; expose account_id only when permitted. No team mutation inside new PUT (independent existing APIs handle it). Register models for migrations/tests and add audit labels.

Red tests before code: approved staff add student/player/both while approvals/guardians/housing remain valid; registered student update; no implicit side effects; authorization/current permission loss; stale due to legacy coach writer; idempotency/collision; project/coach invalid or inactive; blocked identity removal; accountless team eligibility vs active-account missing role; actual DB migration upgrade/downgrade. Run focused DB tests only while holding controller-granted DB test ownership, then scoped Ruff/mypy. Report paths, exact red/green counts and concerns.

## Task 2: Frontend identity, project, coach and account-role panels

Ownership: new `apps/web/src/people/PersonAssignmentsPanel.tsx`, `personAssignmentsApi.ts`, `PersonAccountRolesPanel.tsx` and their tests. Do not edit PersonDetailPage (controller integrates), teams files or backend.

Read spec and existing personnel-intake CoachResponsibilityPanel, people TeamAllocationPanel, permissions/account role UIs. Implement props `{personId:string}` for main panel. Export separate account-role panel props as needed. Main loads GET assignments using actor/person/scopes-aware query keys, abort/stale response protection; call PUT with reusable operation UUID for unchanged retry, new UUID for changed payload. Save identity/projects/coaches separately from team/RBAC. Show business identity checkboxes with existing identities nonremovable and explanatory text. Projects multiselect; coaches eligible union for selected projects, disabled/stale choices clearly labeled rather than silently removed. Display existing values read-only when no edit capability. Missing selection may be saved as pending; validation errors retain form. Required safe reason, 409 refresh option, no auto-resubmit. Clear on target/account/scope change or401/403/404.

Account role panel only appears with capability and account ID; use existing GET roles/account access, POST grant and DELETE revoke with existing permissions. Show assigned role labels and existing UI confirmations/feedback. Do not auto-grant student/team_member based on business identity. For no account show activation note; for added business identity explain that login permissions are maintained separately. Never grant elevated roles implicitly.

Frontend contract spec: GET/PUT shape as spec; can additionally receive optional `current_projects`/`current_coaches` labels for inactive existing IDs, coordinate with backend. Main may accept optional `onSaved` callback so controller invalidates detail. Team UI is separate; don't fetch team data here.

TDD with rendered real components/API-boundary mocks: approved unactivated staff add identity/project/coach; existing student; nonremovable identity; candidates filtered by selected projects; errors/retry/version; target/account changes ignore late response; read-only; role permissions/absence/explicit grant/revoke. Scoped lint, test, then report. No subagents or commits.

## Task 3: Inline team membership panel

Ownership: new `apps/web/src/people/PersonTeamMembershipPanel.tsx` and tests only; no other code except with controller approval. Props `{personId:string; projection:PersonTeamProjection; onSaved?:()=>void}` (import teams/api). Root conditionally mounts for player/both/known team.

Read current teams API/page and PersonDetail team projection. Reuse getTeamContext, paginated listTeams, join/leave/transfer. Show current division/team read-only where allowed. Denied projection never shown as unassigned and no writes. Load edit choices only for actor with current management capability; respect each team's allowed_actions. Filter division then team with pagination (no first-page-only truncation). Source-team permissions required for leave, can_transfer plus target permissions for transfer. No leave-then-join workaround. Existing same target no-op; explicit reason/confirm for transfer and confirm for leave. UUID reused for same failed operation, regenerated on changed input/action/target. Independently save, refresh person/team caches after success, keep business-assignment form unrelated. No autoassign or account creation. Accountless approved player accepted by backend, existing-account role error shown clearly.

TDD rendering joins, transfer and leave, denied/read-only, stale failures, retry key, target/account change ignoring late responses. Use existing auth/query scoping conventions. Scoped lint/tests. No subagents, backend changes, production writes or commits.

## Task 4: Integration, verification, review and release (controller)

Integrate panels into PersonDetail, replace duplicate assisted-only coach editor with new central entry; retain assisted correction and existing allocation marker. Ensure player panel reacts immediately after identity save/detail reload. Update directory business identity display minimally if required by backend. Run focused suite then full frontend/backend with only one disposable DB suite at a time, lint/build and migration checks. Exercise synthetic local browser; no real account mutation. Per-task reviews use task-only baseline diff; final whole-feature security/behavior review. Resolve material findings, then standard NAS deploy; verify active release, migration head, health, asset availability and read-only access. Write concise verification evidence and handoff.
