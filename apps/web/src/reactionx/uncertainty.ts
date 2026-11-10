import { z } from 'zod'

// Separate non-result DTOs: never reuse a ResultReceipt or upload authorization.
const uuid = z.string().regex(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/)
const digest = z.string().regex(/^[0-9a-f]{64}$/)
const utc = z.iso.datetime()
const item = z.strictObject({ run_id: uuid, subject_name: z.string().min(1), mode: z.enum(['manual', 'standard', 'sequence', 'simultaneous', 'base', 'auditory', 'true_false', 'counter', 'whack']), created_at: utc, declaration_digest: digest, state: z.literal('pending_review') })
const page = z.strictObject({ items: z.array(item), total: z.number().int().nonnegative(), offset: z.number().int().nonnegative(), limit: z.number().int().min(1).max(100) }).refine(value => value.items.length <= value.limit && value.items.length <= value.total && new Set(value.items.map(row => row.run_id)).size === value.items.length)
const stamp = z.iso.datetime({ precision: 3 })
const archiveRequest = z.strictObject({ request_id: uuid, run_id: uuid, declaration_digest: digest, expires_at: stamp })
const receipt = z.strictObject({ version: z.literal(1), archive_id: uuid, run_id: uuid, connector_id: uuid, manifest_digest: digest, declaration_digest: digest, stop_operation_id: uuid, status: z.literal('execution_unknown'), closed_at: stamp })
export type UncertaintyItem = z.infer<typeof item>
export type ArchiveRequest = z.infer<typeof archiveRequest>
export type UncertaintyReceipt = z.infer<typeof receipt>
export function parseUncertaintyPage(value: unknown) { return page.parse(value) }
export function parseArchiveRequest(value: unknown, runId: string, declarationDigest: string) {
  const parsed = archiveRequest.parse(value)
  if (parsed.run_id !== runId || parsed.declaration_digest !== declarationDigest) throw new Error('Invalid uncertainty request identity')
  return parsed
}
export function parseUncertaintyReceipt(value: unknown, runId: string, declarationDigest: string) {
  const parsed = receipt.parse(value)
  if (parsed.run_id !== runId || parsed.declaration_digest !== declarationDigest) throw new Error('Invalid uncertainty receipt identity')
  return parsed
}
