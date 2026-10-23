import { useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { z } from "zod"
import { apiFetch, ProblemDetailsError } from "../api/client"
import type { IntakeInput } from "./types"

export const assistedEditorContextSchema = z.strictObject({
  person_id: z.uuid(), kind: z.enum(["student", "player", "both"]), profile_version: z.number().int().positive(),
  current_coach_person_ids: z.array(z.uuid()), eligible_coaches: z.array(z.strictObject({ id: z.uuid(), label: z.string() })),
  allowed_actions: z.array(z.enum(["correct_profile", "replace_coaches"])),
})
export type AssistedEditorContext = z.infer<typeof assistedEditorContextSchema>
export type ProfileCorrectionInput = Omit<IntakeInput, "kind" | "team_id" | "coach_person_ids"> & {
  expected_version: number; reason: string; idempotency_key: string
}
export type CoachReplacementInput = { coach_person_ids: string[]; expected_version: number; reason: string }
const approval = z.strictObject({ request_id: z.uuid().nullable(), person_id: z.uuid(), approval_id: z.uuid() })
const base = (personId: string) => `/personnel-intake/people/${encodeURIComponent(personId)}`
export const editorKey = (accountId: string, personId: string) => ["assisted-editor", accountId, personId] as const
export const assistedEditorApi = {
  context: async (personId: string, signal: AbortSignal): Promise<AssistedEditorContext> => {
    const value = assistedEditorContextSchema.parse(await apiFetch<unknown>(`${base(personId)}/editor-context`, { signal, cache: "no-store" }))
    signal.throwIfAborted()
    if (value.person_id !== personId) throw new Error("Mismatched editor context")
    return value
  },
  correct: async (personId: string, input: ProfileCorrectionInput, signal: AbortSignal) => {
    const value = await apiFetch<unknown>(`${base(personId)}/corrections`, {
      method: "POST", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    })
    signal.throwIfAborted()
    return approval.parse(value)
  },
  coaches: async (personId: string, input: CoachReplacementInput, signal: AbortSignal) => {
    await apiFetch<void>(`${base(personId)}/coaches`, {
      method: "PUT", signal, cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify(input),
    })
    signal.throwIfAborted()
  },
}

/** Separate from intake context: update-only admins need not have submit/review. */
export function useAssistedEditor(accountId: string, personId: string) {
  const client = useQueryClient()
  const query = useQuery({
    queryKey: editorKey(accountId, personId), queryFn: ({ signal }) => assistedEditorApi.context(personId, signal),
    staleTime: 0, gcTime: 0, retry: false, refetchOnWindowFocus: "always",
  })
  const revoked = query.isError && !(query.error instanceof ProblemDetailsError && query.error.problem.status >= 500 || query.error instanceof TypeError)
  useEffect(() => {
    if (revoked && query.data) client.setQueryData(editorKey(accountId, personId), null)
  }, [accountId, client, personId, query.data, revoked])
  return { ...query, blocked: query.isError, context: revoked ? undefined : query.data ?? undefined,
    reload: async () => {
      const result = await query.refetch()
      return result.isError ? null : result.data ?? null
    } }
}

export function newEditorOperationKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID()
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40; bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = [...bytes].map((value) => value.toString(16).padStart(2, "0")).join("")
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

/** Call only inside an account/person-keyed draft. Focus never changes its version. */
export function useEditorOperation(accountId: string, personId: string, initialVersion: number) {
  const client = useQueryClient()
  const [version, setVersion] = useState(initialVersion)
  const [pending, setPending] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [message, setMessage] = useState("")
  const [succeeded, setSucceeded] = useState(false)
  const active = useRef(false)
  const flight = useRef<AbortController | null>(null)
  useEffect(() => {
    active.current = true
    return () => { active.current = false; flight.current?.abort() }
  }, [])
  async function run(operation: (signal: AbortSignal) => Promise<unknown>, successMessage: string) {
    if (flight.current || conflict || succeeded) return
    const controller = new AbortController()
    flight.current = controller
    setPending(true); setMessage("")
    try {
      await operation(controller.signal)
      if (!active.current || controller.signal.aborted) return
      setSucceeded(true); setMessage(successMessage)
      await Promise.all([
        client.invalidateQueries({ queryKey: editorKey(accountId, personId) }),
        client.invalidateQueries({ queryKey: ["person", personId] }),
        client.invalidateQueries({ queryKey: ["people"] }),
        client.invalidateQueries({ queryKey: ["people-counts"] }),
      ])
    } catch (error) {
      if (!active.current || controller.signal.aborted) return
      if (error instanceof ProblemDetailsError && [401, 403, 404].includes(error.problem.status)) {
        await client.cancelQueries({ queryKey: editorKey(accountId, personId) })
        client.setQueryData(editorKey(accountId, personId), null)
      } else if (error instanceof ProblemDetailsError && error.problem.status === 409) {
        setConflict(true)
        setMessage("版本冲突或现有联系方式受保护，当前草稿已保留。请重新读取管理信息并核对后重试；已开户邮箱或已验证联系方式请使用原有验证变更流程。")
      } else setMessage("操作未成功，请检查完整资料及原因或稍后重试。当前草稿已保留。")
    } finally {
      flight.current = null
      if (active.current) setPending(false)
    }
  }
  return { version, pending, conflict, message, succeeded, run,
    acceptVersion: (next: number) => { setVersion(next); setConflict(false); setMessage("") } }
}
