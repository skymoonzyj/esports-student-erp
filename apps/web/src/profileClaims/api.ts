import { z } from "zod"
import { ProblemDetailsError } from "../api/client"
import { beginProfileClaimRequest, replaceProfileClaimGeneration } from "./keys"
import type { ClaimPublicStatus, ClaimStart, ClaimStartInput } from "./types"
import { identityDocumentTypes, normalizeHkId, normalizeHkMoPermit, personDocumentTypes } from "../assistedPeople/documentFacts"
export { clearProfileClaimSession } from "./keys"

const country = z.string().trim().toUpperCase().regex(/^[A-Z]{2}$/)
const noControlCharacters = (value: string) => ![...value].some((character) => character.charCodeAt(0) < 32)
const guardianInput = z.strictObject({
  full_name: z.string().trim().min(1).max(80), identity_number: z.string().trim().min(1),
  document_type: z.enum(identityDocumentTypes).optional(), document_issuing_country: country.nullable().optional(),
  phone: z.string().trim().min(1), email: z.email().nullable().optional(), relationship: z.string().trim().min(2).max(40),
  sex: z.enum(["male", "female"]), date_of_birth: z.iso.date(), is_primary: z.boolean().optional(),
}).superRefine((input, ctx) => {
  if (input.document_type === "hk_id" && (!normalizeHkId(input.identity_number) || input.document_issuing_country && input.document_issuing_country !== "HK")) {
    ctx.addIssue({ code: "custom", message: "香港身份证信息无效", path: ["identity_number"] })
  }
  if (input.document_type === "hk_mo_permit" && (!normalizeHkMoPermit(input.identity_number) || input.document_issuing_country != null)) {
    ctx.addIssue({ code: "custom", message: "回乡证信息无效", path: ["identity_number"] })
  }
}).transform((input) => ({ ...input, identity_number: input.document_type === "hk_mo_permit" ? normalizeHkMoPermit(input.identity_number)! : input.document_type === "hk_id" ? normalizeHkId(input.identity_number)! : input.identity_number }))
const completionInput = z.strictObject({
  phone: z.string().trim().max(80).refine(noControlCharacters).transform((value) => value || null).nullable().default(null),
  sex: z.enum(["male", "female"]).nullable().default(null), date_of_birth: z.iso.date().nullable().default(null),
  issuing_country: country.nullable().default(null), guardians: z.array(guardianInput).max(10).default([]),
})
const startInput = z.strictObject({
  full_name: z.string().trim().min(1).max(80),
  identity_number: z.string(),
  email: z.string().trim().toLowerCase().pipe(z.email()),
  document_type: z.enum(personDocumentTypes).optional(), issuing_country: country.nullable().optional(),
  completion: completionInput.nullable().optional(),
}).superRefine((input, ctx) => {
  const cn = !input.document_type || input.document_type === "cn_id"
  const hk = input.document_type === "hk_id"
  const permit = input.document_type === "hk_mo_permit"
  const number = input.identity_number
  if (cn ? !/^\d{17}[\dX]$/.test(number.trim().toUpperCase()) : hk ? !normalizeHkId(number) : permit ? !normalizeHkMoPermit(number) : !number.trim() || number.trim().length > 80 || !noControlCharacters(number)) {
    ctx.addIssue({ code: "custom", message: "证件号码格式无效", path: ["identity_number"] })
  }
  const extra = input.completion?.issuing_country
  if (input.issuing_country && extra && input.issuing_country !== extra || (cn || hk) && [input.issuing_country, extra].some((value) => value && value !== (hk ? "HK" : "CN")) || permit && [input.issuing_country, extra].some((value) => value != null)) {
    ctx.addIssue({ code: "custom", message: "签发国家不一致", path: ["issuing_country"] })
  }
}).transform((input) => ({ ...input,
  identity_number: input.document_type === "passport" ? input.identity_number : input.document_type === "hk_mo_permit" ? normalizeHkMoPermit(input.identity_number)! : input.document_type === "hk_id" ? normalizeHkId(input.identity_number)! : input.identity_number.trim().toUpperCase(),
}))
const startResult = z.strictObject({
  accepted: z.literal(true), resend_available_at: z.iso.datetime({ offset: true }), expires_at: z.iso.datetime({ offset: true }),
})
const statusResult = z.strictObject({
  status: z.enum(["waiting_review", "approved", "rejected", "expired", "closed"]), reason: z.string().nullable(),
})

async function post<T>(path: string, body: object, schema: z.ZodType<T>, signal?: AbortSignal): Promise<T> {
  const request = beginProfileClaimRequest(signal)
  try {
    request.check()
    const response = await fetch(`/api/v1/auth/profile-claims/${path}`, {
      method: "POST", credentials: "include", cache: "no-store", signal: request.signal,
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })
    request.check()
    if (!response.ok) {
      const error = await ProblemDetailsError.fromResponse(response)
      request.check()
      throw error
    }
    const value: unknown = await response.json()
    request.check()
    return schema.parse(value)
  } finally { request.release() }
}

export async function startProfileClaim(input: ClaimStartInput, signal?: AbortSignal): Promise<ClaimStart> {
  const normalized = startInput.parse(input)
  replaceProfileClaimGeneration()
  return post("start", normalized, startResult, signal)
}
export async function verifyProfileClaim(code: string, signal?: AbortSignal): Promise<ClaimPublicStatus> {
  return post("verify", { code: z.string().regex(/^[0-9]{6}$/).parse(code) }, statusResult, signal)
}
export async function getProfileClaimStatus(signal?: AbortSignal): Promise<ClaimPublicStatus> {
  return post("status", {}, statusResult, signal)
}
