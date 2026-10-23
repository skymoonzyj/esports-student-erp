/** Transport DTOs mirror personnel_intake/schemas.py; secrets are input strings, never SecretStr JSON. */
export type PersonKind = "student" | "player" | "both"

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
  email: string
  sex: "male" | "female"
  date_of_birth: string
  guardians: AssistedGuardianInput[]
  coach_person_ids: string[]
  team_id: string | null
}
