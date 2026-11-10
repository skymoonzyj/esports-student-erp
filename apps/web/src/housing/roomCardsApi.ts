import { z } from "zod"
import { apiFetch } from "../api/client"

const date = z.iso.date().refine((value) => /^[0-9]{4}-/.test(value) && !value.startsWith("0000"))
const datePair = z.object({ issued_on: date.nullable(), expires_on: date.nullable() }).refine((value) =>
  value.issued_on === null && value.expires_on === null || Boolean(value.issued_on && value.expires_on && value.expires_on >= value.issued_on))
const cardSchema = z.object({
  stay_id: z.string(), person_id: z.string(), person_name: z.string(), nickname: z.string().nullable(),
  issued_on: date.nullable(), expires_on: date.nullable(), version: z.number().int().nonnegative(), can_edit: z.boolean(),
})
export type RoomCard = z.infer<typeof cardSchema>
export type RoomCardInput = { issued_on: string | null; expires_on: string | null; expected_version: number }
export function validRoomCardDates(issuedOn: string, expiresOn: string): boolean {
  return datePair.safeParse({ issued_on: issuedOn || null, expires_on: expiresOn || null }).success
}
export async function getRoomCards(roomId: string, signal?: AbortSignal): Promise<{ items: RoomCard[] }> {
  const raw = await apiFetch<unknown>(`/housing/rooms/${encodeURIComponent(roomId)}/room-cards`, { signal })
  const parsed = z.object({ items: z.array(cardSchema) }).safeParse(raw)
  if (!parsed.success) throw new Error("房卡响应格式无效，请重试。")
  return parsed.data
}
export async function saveRoomCard(stayId: string, input: RoomCardInput): Promise<RoomCard> {
  const raw = await apiFetch<unknown>(`/housing/stays/${encodeURIComponent(stayId)}/room-card`, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
  })
  const parsed = cardSchema.safeParse(raw)
  if (!parsed.success) throw new Error("房卡响应格式无效，请重新读取当前日期。")
  return parsed.data
}
