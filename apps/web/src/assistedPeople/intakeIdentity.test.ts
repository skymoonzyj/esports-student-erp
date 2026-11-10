import { describe, expect, it } from "vitest"
import { emptyIdentityDraft, extraIdentityKeys, identityFingerprint, migrateLegacyIdentityDraft, normalizeIdentityInput, previewIdentityChange } from "./intakeIdentity"
import type { IdentityKey } from "./types"

describe("management identity contract", () => {
  // Missing a legitimate identity must not silently turn its request into another role.
  it.each<IdentityKey>(["student", "team_member", "team_manager", "coach", "assistant_coach", "administration", "customer_service", "life_teacher", "property", "finance", "guardian", "staff", "registered_user"])("preserves the %s identity", primary_identity => {
    expect(normalizeIdentityInput({ ...emptyIdentityDraft(), primary_identity }).identity_selection.primary_identity).toBe(primary_identity)
  })
  it("rejects duplicate identities and ambiguous primary staff classification", () => {
    expect(() => normalizeIdentityInput({ ...emptyIdentityDraft(), primary_identity: "student", concurrent_identities: ["student"] })).toThrow()
    expect(() => normalizeIdentityInput({ ...emptyIdentityDraft(), primary_identity: "student", concurrent_identities: ["coach", "team_manager"] })).toThrow()
    expect(normalizeIdentityInput({ ...emptyIdentityDraft(), primary_identity: "student", concurrent_identities: ["coach", "team_manager"], primary_staff_identity: "coach" }).identity_selection.primary_staff_identity).toBe("coach")
  })
  it("normalizes a single player team and distinct multi-team staff roles without deleting hidden assignments", () => {
    const value = { ...emptyIdentityDraft(), primary_identity: "team_member" as const, concurrent_identities: ["coach", "team_manager"] as IdentityKey[], primary_staff_identity: "coach" as const,
      player_team_id: "player-team", staff_team_ids: { manager: ["b", "a"], coach: ["a"], assistant_coach: [] } }
    expect(normalizeIdentityInput(value)).toEqual({ identity_selection: { primary_identity: "team_member", concurrent_identities: ["coach", "team_manager"], primary_staff_identity: "coach" }, team_id: "player-team", staff_teams: [{ staff_role: "coach", team_ids: ["a"] }, { staff_role: "manager", team_ids: ["a", "b"] }] })
    expect(() => normalizeIdentityInput({ ...value, concurrent_identities: ["coach"] })).toThrow()
    expect(() => normalizeIdentityInput({ ...value, staff_team_ids: { ...value.staff_team_ids, coach: ["a", "a"] } })).toThrow()
  })
  it("keeps legacy roles and requires primary confirmation", () => {
    const saved = { draft: { kind: "both" as const, team_id: "player-team" }, roleKeys: ["coach"], attempt: { fingerprint: "old", key: "retry-key" } }
    const migrated = migrateLegacyIdentityDraft(saved)
    expect(migrated.needsPrimaryConfirmation).toBe(true)
    expect(migrated.value.primary_identity).toBeNull()
    expect(migrated.value.concurrent_identities).toEqual(expect.arrayContaining(["student", "team_member", "coach"]))
    expect(migrated.value.player_team_id).toBe(saved.draft.team_id)
    expect(saved.attempt).toEqual({ fingerprint: "old", key: "retry-key" })
    expect(() => migrateLegacyIdentityDraft({ ...saved, roleKeys: ["unknown-role"] })).toThrow(/unknown-role/)
  })
  it.each([
    { roleKeys: [], want: ["registered_user"], extras: [] },
    { roleKeys: ["coach"], want: ["registered_user", "coach"], extras: ["coach"] },
    { roleKeys: ["staff"], want: ["registered_user", "staff"], extras: ["staff"] },
  ])("restores only the staff-kind baseline and explicitly saved extra roles: $roleKeys", ({ roleKeys, want, extras }) => {
    const migrated = migrateLegacyIdentityDraft({ draft: { kind: "staff", team_id: null }, roleKeys })
    expect(migrated.needsPrimaryConfirmation).toBe(true)
    expect(migrated.value.primary_identity).toBeNull()
    expect(migrated.value.concurrent_identities).toEqual(want)
    expect(extraIdentityKeys(migrated.value)).toEqual(extras)
  })
  it("previews all removed assignments without mutating either source", () => {
    const current = { ...emptyIdentityDraft(), primary_identity: "student" as const, concurrent_identities: ["team_member", "coach"] as IdentityKey[], player_team_id: "p", staff_team_ids: { manager: [], coach: ["c"], assistant_coach: [] } }
    const result = previewIdentityChange(current, { primary_identity: "registered_user", concurrent_identities: [], primary_staff_identity: null }, ["teacher"])
    expect(result.removedAssignments).toEqual([{ kind: "player_team", id: "p", staff_role: null }, { kind: "staff_team", id: "c", staff_role: "coach" }, { kind: "coach", id: "teacher", staff_role: null }])
    expect(result.nextCoachPersonIds).toEqual([])
    expect(current.player_team_id).toBe("p")
    expect(current.staff_team_ids.coach).toEqual(["c"])
  })
  it("canonicalizes identity and assignment sets but distinguishes changed assignments", () => {
    const a = { ...emptyIdentityDraft(), primary_identity: "coach" as const, concurrent_identities: ["student", "guardian"] as IdentityKey[], staff_team_ids: { manager: [], coach: ["b", "a"], assistant_coach: [] } }
    const b = { ...a, concurrent_identities: ["guardian", "student"] as IdentityKey[], staff_team_ids: { ...a.staff_team_ids, coach: ["a", "b"] } }
    expect(identityFingerprint(a)).toBe(identityFingerprint(b))
    expect(identityFingerprint(a)).not.toBe(identityFingerprint({ ...b, staff_team_ids: { ...b.staff_team_ids, coach: ["a"] } }))
  })
})
