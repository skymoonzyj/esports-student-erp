/** Transport DTOs mirror personnel_intake/schemas.py; secrets are input strings, never SecretStr JSON. */
export type PersonKind = "student" | "player" | "both"
export type PendingPersonKind = PersonKind | "staff"
export type { IdentityDocumentType, PersonDocumentType } from "./documentFacts"
import type { IdentityDocumentType, PersonDocumentType } from "./documentFacts"

export interface AssistedGuardianInput {
  full_name: string
  identity_number: string
  document_type?: IdentityDocumentType
  document_issuing_country?: string | null
  phone: string
  email?: string | null
  relationship: string
  sex: "male" | "female"
  date_of_birth: string
  is_primary?: boolean
}

export interface IntakeInput {
  kind: PersonKind
  full_name: string
  identity_number: string
  document_type?: IdentityDocumentType
  document_issuing_country?: string | null
  phone: string
  email: string | null
  sex: "male" | "female"
  date_of_birth: string
  guardians: AssistedGuardianInput[]
  coach_person_ids: string[]
  team_id: string | null
}

/** Only the management direct-create endpoints accept activation authorization. */
export const identityKeys = ["student", "team_member", "team_manager", "coach", "assistant_coach", "administration", "customer_service", "life_teacher", "property", "finance", "guardian", "staff", "registered_user"] as const
export type IdentityKey = typeof identityKeys[number]
export type TeamStaffRole = "manager" | "coach" | "assistant_coach"
export interface ActivationRoleUpdateInput {
  role_keys: string[]
  expected_version: number
  idempotency_key: string
  confirm_end_staff_positions?: boolean
}
export interface ManagedIdentityInput {
  primary_identity: IdentityKey
  concurrent_identities: IdentityKey[]
  primary_staff_identity: IdentityKey | null
}
export interface StaffTeamSelection { staff_role: TeamStaffRole; team_ids: string[] }
interface ManagedIdentityFields { identity_selection: ManagedIdentityInput; staff_teams: StaffTeamSelection[] }
export type DirectCreateInput = Omit<IntakeInput, "kind"> & ({ kind: PendingPersonKind; activation_role_keys?: string[]; identity_selection?: never; staff_teams?: never } | (ManagedIdentityFields & { kind?: PendingPersonKind; activation_role_keys?: never }))
export type DirectCreatePendingInput = Omit<PendingIntakeInput, "kind"> & ({ kind: PendingPersonKind; activation_role_keys?: string[]; identity_selection?: never; staff_teams?: never } | (ManagedIdentityFields & { kind?: PendingPersonKind; activation_role_keys?: never }))

/** Only the explicit administrator pending-intake endpoint accepts these nullable facts. */
export interface PendingPersonFields {
  full_name: string
  identity_number: string
  document_type: PersonDocumentType
  document_issuing_country: string | null
  phone: string | null
  email: string | null
  sex: "male" | "female" | null
  date_of_birth: string | null
  guardians: AssistedGuardianInput[]
}

export interface PendingIntakeInput extends PendingPersonFields {
  kind: PendingPersonKind
  coach_person_ids: string[]
  team_id: string | null
  team_allocation_status: "pending" | "not_required"
}
