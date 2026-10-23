import type { AssistedGuardianInput } from "../assistedPeople/types"

export interface ClaimCompletionInput {
  phone?: string | null
  sex?: "male" | "female" | null
  date_of_birth?: string | null
  issuing_country?: string | null
  guardians?: AssistedGuardianInput[]
}

export interface ClaimStartInput {
  full_name: string
  identity_number: string
  email: string
  document_type?: "cn_id" | "passport"
  issuing_country?: string | null
  completion?: ClaimCompletionInput | null
}

export interface ClaimStart {
  accepted: true
  resend_available_at: string
  expires_at: string
}

export interface ClaimPublicStatus {
  status: "waiting_review" | "approved" | "rejected" | "expired" | "closed"
  reason: string | null
}
