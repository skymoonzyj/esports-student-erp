import { z } from "zod"
import { apiFetch } from "../api/client"

const row = z.strictObject({
  row_number: z.number().int().positive(), full_name: z.string(), room_label: z.string(),
  arrival_date: z.iso.date().nullable(), departure_date: z.iso.date().nullable(), identity_label: z.string(), project_label: z.string(),
  team_options: z.array(z.strictObject({ id: z.uuid(), project_id: z.uuid(), project_name: z.string(), name: z.string() })),
  selected_team_id: z.uuid().nullable(), needs_team_choice: z.boolean(), birth_date: z.iso.date().nullable(),
  sex: z.enum(["male", "female"]).nullable(), masked_identity: z.string(), masked_phone: z.string(),
  errors: z.array(z.string()), warnings: z.array(z.string()), ticket: z.string().nullable(),
})
const preview = z.strictObject({ import_date: z.iso.date(), rows: z.array(row).max(100) })
const result = z.strictObject({ person_id: z.uuid(), stay_id: z.uuid(), stay_status: z.enum(["planned", "checked_in"]), role_pending: z.boolean() })
export type QuickPreview = z.infer<typeof preview>
export type QuickRow = z.infer<typeof row>
export type QuickResult = z.infer<typeof result>
export type TeamChoices = Record<number, string>
const base = "/personnel-intake/quick-import"
async function post(path: string, body: unknown, signal: AbortSignal) {
  return apiFetch<unknown>(base + path, { method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
}
export const quickImportApi = {
  preview: async (text: string, date: string | undefined, choices: TeamChoices, signal: AbortSignal) =>
    preview.parse(await post("/preview", { text, default_arrival_date: date, team_choices: choices }, signal)),
  commit: async (ticket: string, key: string, signal: AbortSignal) =>
    result.parse(await post("/commit", { ticket, idempotency_key: key }, signal)),
}
