/** Transport DTOs mirror personnel_intake/schemas.py; secrets are input strings, never SecretStr JSON. */
export type PersonKind = "student" | "player" | "both"
export type PendingPersonKind = PersonKind | "staff"

export interface AssistedGuardianInput {
  full_name: string
  identity_number: string
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
  phone: string
  email: string | null
  sex: "male" | "female"
  date_of_birth: string
  guardians: AssistedGuardianInput[]
  coach_person_ids: string[]
  team_id: string | null
}

/** Only the explicit administrator pending-intake endpoint accepts these nullable facts. */
export interface PendingPersonFields {
  full_name: string
  identity_number: string
  document_type: "cn_id" | "passport"
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
