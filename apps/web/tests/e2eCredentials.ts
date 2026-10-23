import { readFileSync } from "node:fs"
import { fileURLToPath } from "node:url"

const credentialKeys = [
  "admin",
  "customer_service",
  "coach",
  "guardian",
  "student",
  "other_guardian",
  "life_teacher",
  "ready_male_student",
  "visitor",
  "second_student",
  "teaching_authorized_assistant",
  "teaching_unauthorized_assistant",
  "camp_survey_no_access",
  "housing_report_building",
  "housing_report_floor",
  "housing_report_none",
  "registration_existing",
  "registration_email_only",
  "registration_reviewer",
  "team_manager_a",
  "team_manager_b",
  "team_manager_multi",
  "team_manager_shared",
  "team_manager_unbound",
  "team_member_only",
  "team_ordinary_admin",
  "team_disabled",
  "team_deleted",
] as const

export type E2ECredentialKey = (typeof credentialKeys)[number]
export type E2ECredential = Readonly<{ identifier: string; password: string }>

const credentialsPath = fileURLToPath(
  new URL("../../api/app/cli/e2e_credentials.json", import.meta.url),
)
const parsed: unknown = JSON.parse(readFileSync(credentialsPath, "utf8"))

function readCredential(key: E2ECredentialKey): E2ECredential {
  if (!parsed || typeof parsed !== "object") {
    throw new Error("E2E credentials must be a JSON object")
  }
  const value = (parsed as Record<string, unknown>)[key]
  if (!value || typeof value !== "object") {
    throw new Error(`Missing E2E credential: ${key}`)
  }
  const { identifier, password } = value as Record<string, unknown>
  if (typeof identifier !== "string" || !identifier.trim()) {
    throw new Error(`Invalid E2E identifier: ${key}`)
  }
  if (
    typeof password !== "string"
    || !/^(?=.{8,16}$)(?=.*[A-Z])(?=.*[a-z])(?=.*\d).*$/.test(password)
  ) {
    throw new Error(`E2E password violates the application policy: ${key}`)
  }
  return Object.freeze({ identifier, password })
}

export const E2E_CREDENTIALS = Object.freeze(
  Object.fromEntries(credentialKeys.map((key) => [key, readCredential(key)])),
) as Readonly<Record<E2ECredentialKey, E2ECredential>>

export function e2eCredentialTuple(
  key: E2ECredentialKey,
): readonly [string, string] {
  const credential = E2E_CREDENTIALS[key]
  return [credential.identifier, credential.password]
}
