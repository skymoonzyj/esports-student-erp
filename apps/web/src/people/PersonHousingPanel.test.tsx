import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { setCsrfToken } from "../api/client"
import type { HousingStay } from "../housing/api"
import { housingKeys } from "../housing/housingKeys"
import { PersonHousingPanel } from "./PersonHousingPanel"
import { personHousingKeys, type PersonHousing } from "./housingApi"

const revision = "a".repeat(64)
const personRevision = "b".repeat(64)
const accountId = "manager-account"
const personId = "person-1"
const stay: HousingStay = {
  id: "stay-1", person_id: personId, room_id: "room-409", person_name: "张三", person_type: "student", sex: "male", stay_purpose: "permanent", duration_mode: "long_term", cohort_id: null, visit_reason: null,
  planned_check_in_at: "2026-08-01T00:00:00+08:00", planned_check_out_at: null, actual_check_in_at: "2026-08-01T00:00:00+08:00", actual_check_out_at: null, status: "checked_in", room_name_snapshot: "409", floor_name_snapshot: "四层", building_name_snapshot: "一号楼", notes: null, cancellation_reason: null, checkout_requested_actual_at: null, checkout_requested_at: null, version: 7,
  allowed_actions: { transfer: true, edit_planned_departure: false, edit_reservation: false, confirm_check_in: false, cancel_reservation: false, request_checkout: false, confirm_checkout: false }, created_at: "2026-08-01T00:00:00Z", updated_at: "2026-08-01T00:00:00Z",
}
const current = { building_name: "一号楼", floor_name: "四层", room_name: "409", status: "checked_in" as const, stay_purpose: "permanent" as const, duration_mode: "long_term" as const, planned_check_in_at: stay.planned_check_in_at, planned_check_out_at: null, actual_check_in_at: stay.actual_check_in_at }
const occupied: PersonHousing = { access_revision: personRevision, housing_access_revision: revision, current, planned: null, management_stay: stay, allowed_actions: { create_stay: false } }
const vacant: PersonHousing = { access_revision: personRevision, housing_access_revision: revision, current: null, planned: null, management_stay: null, allowed_actions: { create_stay: true } }
const checkInOptions = {
  access_revision: personRevision,
  housing_access_revision: revision,
  resident: { person_id: personId, full_name: "张三", person_type: "student", sex: "male", masked_phone: null, account_ready: false, housing_ready: true, account_status: "not_activated", profile_ready: true, guardian_ready: true },
  items: [{ id: "room-409", room_number: "409", building_name: "一号楼", floor_name: "四层", allowed_actions: { create_stay: true } }],
}
const transferOptions = { access_revision: revision, items: [{ room_id: "room-410", building_label: "一号楼", floor_label: "四层", room_label: "410", capacity: 2, current_occupants: 1, current_gender: "male", advisory_compatible: true, status_text: "可入住" }] }
function json(value: unknown, status = 200) { return new Response(JSON.stringify(value), { status, headers: { "Content-Type": status < 400 ? "application/json" : "application/problem+json" } }) }

function mount(fetcher: typeof fetch, options: { client?: QueryClient; onAccessRevoked?: () => void; accountId?: string; personId?: string } = {}) {
  vi.stubGlobal("fetch", fetcher)
  setCsrfToken("test-csrf")
  const client = options.client ?? new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(<QueryClientProvider client={client}><PersonHousingPanel personId={options.personId ?? personId} accountId={options.accountId ?? accountId} onAccessRevoked={options.onAccessRevoked} /></QueryClientProvider>)
  return { ...view, client }
}

afterEach(() => { setCsrfToken(null); vi.unstubAllGlobals() })

it("arranges an approved inactive exact person using existing stay API without searching other people", async () => {
  let created = false
  const writes: unknown[] = []
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (init?.method === "POST" && url === "/api/v1/housing/rooms/room-409/stays") { writes.push(JSON.parse(String(init.body))); created = true; return json(stay) }
    if (url === `/api/v1/people/${personId}/housing`) {
      if (created) await new Promise((resolve) => setTimeout(resolve, 30))
      return json(created ? occupied : vacant)
    }
    if (url.endsWith("/housing/check-in-options")) return created ? json({ detail: "入住后已不可重复入住" }, 403) : json(checkInOptions)
    if (url.endsWith("/cohort-options")) return json({ access_revision: revision, items: [] })
    throw new Error(`Unexpected request: ${url}`)
  })
  mount(fetcher)
  expect(await screen.findByText("当前未入住")).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "安排入住" }))
  await userEvent.selectOptions(await screen.findByLabelText("选择房间"), "room-409")
  await userEvent.click(screen.getByRole("button", { name: "下一步" }))
  expect(screen.getByLabelText("入住人员")).toHaveTextContent("张三")
  expect(screen.queryByLabelText("搜索入住人员")).not.toBeInTheDocument()
  await userEvent.selectOptions(screen.getByLabelText("住宿类型"), "permanent")
  await userEvent.selectOptions(screen.getByLabelText("时长类型"), "long_term")
  fireEvent.change(screen.getByLabelText("计划入住日期"), { target: { value: "2026-08-10" } })
  await userEvent.click(screen.getByRole("button", { name: "确认入住" }))
  await waitFor(() => expect(screen.getByLabelText("当前住宿")).toHaveTextContent("一号楼 · 四层 · 409"))
  expect(writes).toEqual([{ person_id: personId, stay_purpose: "permanent", duration_mode: "long_term", cohort_id: null, visit_reason: null, planned_check_in_at: "2026-08-10T00:00:00+08:00", planned_check_out_at: null, notes: null }])
  expect(fetcher.mock.calls.some(([url]) => String(url).includes("resident-options"))).toBe(false)
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
})

it("transfers with opening version and refreshes summary and all room/transfer caches", async () => {
  let transferred = false
  const writes: unknown[] = []
  const fetcher: typeof fetch = async (input, init) => {
    const url = String(input)
    if (init?.method === "POST" && url.endsWith("/stays/stay-1/transfer")) { writes.push(JSON.parse(String(init.body))); transferred = true; return json({ ...stay, room_id: "room-410" }) }
    if (url.endsWith("/transfer-options")) return transferred ? json({ detail: "原住宿记录已关闭" }, 404) : json(transferOptions)
    if (url.endsWith(`/people/${personId}/housing`)) {
      if (transferred) await new Promise((resolve) => setTimeout(resolve, 30))
      return json(transferred ? { ...occupied, current: { ...current, room_name: "410" }, management_stay: { ...stay, room_id: "room-410" } } : occupied)
    }
    throw new Error(`Unexpected request: ${url}`)
  }
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const cacheKeys = [housingKeys.detail(accountId, revision, "room-409"), housingKeys.detail(accountId, revision, "room-410"), [...housingKeys.overviews(accountId, revision), "list"], housingKeys.transferOptions(accountId, revision, "other-stay"), personHousingKeys.summary(accountId, "other-person")]
  for (const key of cacheKeys) client.setQueryData(key, { marker: "cached" })
  mount(fetcher, { client })
  await userEvent.click(await screen.findByRole("button", { name: "修改房间" }))
  await userEvent.click(await screen.findByRole("radio", { name: "一号楼 · 四层 · 410" }))
  await userEvent.type(screen.getByLabelText("调房原因"), "房间调整")
  fireEvent.change(screen.getByLabelText("调房日期"), { target: { value: "2026-10-20" } })
  await userEvent.click(screen.getByRole("button", { name: "确认调房" }))
  await waitFor(() => expect(screen.getByLabelText("当前住宿")).toHaveTextContent("一号楼 · 四层 · 410"))
  expect(writes).toEqual([{ version: 7, target_room_id: "room-410", transferred_at: "2026-10-20T00:00:00+08:00", reason: "房间调整" }])
  for (const key of cacheKeys) expect(client.getQueryState(key)?.isInvalidated).toBe(true)
})

it.each([403, 404])("clears sensitive summary and open dialog on a %s summary refresh", async (status) => {
  let denied = false
  const onAccessRevoked = vi.fn()
  const { client } = mount(async (input) => String(input).endsWith("/transfer-options") ? json(transferOptions) : denied ? json({ detail: "private details" }, status) : json(occupied), { onAccessRevoked })
  await userEvent.click(await screen.findByRole("button", { name: "修改房间" }))
  await screen.findByRole("radio", { name: "一号楼 · 四层 · 410" })
  denied = true
  await act(async () => { await client.invalidateQueries({ queryKey: personHousingKeys.summary(accountId, personId), exact: true }) })
  await waitFor(() => expect(screen.queryByRole("region", { name: "住宿信息" })).not.toBeInTheDocument())
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain("private details")
  expect(client.getQueryData(personHousingKeys.summary(accountId, personId))).toBeUndefined()
  expect(client.getQueryData(housingKeys.transferOptions(accountId, revision, "stay-1"))).toBeUndefined()
  expect(onAccessRevoked).toHaveBeenCalled()
})

it("preserves a transfer draft and opening version during harmless background refresh", async () => {
  let version = 7
  const writes: unknown[] = []
  const { client } = mount(async (input, init) => {
    if (init?.method === "POST") { writes.push(JSON.parse(String(init.body))); return json({ code: "housing_stale_version", detail: "版本冲突" }, 409) }
    return String(input).endsWith("/transfer-options") ? json(transferOptions) : json({ ...occupied, management_stay: { ...stay, version } })
  })
  await userEvent.click(await screen.findByRole("button", { name: "修改房间" }))
  await userEvent.click(await screen.findByRole("radio", { name: "一号楼 · 四层 · 410" }))
  await userEvent.type(screen.getByLabelText("调房原因"), "保留草稿")
  version = 8
  await act(async () => { await client.invalidateQueries({ queryKey: personHousingKeys.summary(accountId, personId), exact: true }) })
  expect(screen.getByLabelText("调房原因")).toHaveValue("保留草稿")
  expect(screen.getByRole("radio", { name: "一号楼 · 四层 · 410" })).toBeChecked()
  await userEvent.click(screen.getByRole("button", { name: "确认调房" }))
  expect(await screen.findByRole("alert")).toHaveTextContent("数据已更新")
  expect(writes).toEqual([{ version: 7, target_room_id: "room-410", transferred_at: expect.stringMatching(/^\d{4}-\d{2}-\d{2}T00:00:00\+08:00$/), reason: "保留草稿" }])
  expect(screen.getByLabelText("调房原因")).toHaveValue("保留草稿")
})

it("keeps plans separate when there is no current stay and grants no frontend-only actions", async () => {
  mount(async () => json({ ...vacant, allowed_actions: { create_stay: false }, planned: { ...current, status: "planned", actual_check_in_at: null } }))
  expect(await screen.findByText("当前未入住")).toBeVisible()
  expect(screen.getByLabelText("当前住宿")).not.toHaveTextContent("409")
  expect(screen.getByLabelText("计划住宿")).toHaveTextContent("一号楼 · 四层 · 409")
  expect(screen.queryByRole("button", { name: "安排入住" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "修改房间" })).not.toBeInTheDocument()
})

it.each(["person", "account"])("discards a pending mutation after switching %s and never invalidates the new scope", async (switchType) => {
  let resolveWrite: (response: Response) => void = () => undefined
  const pending = new Promise<Response>((resolve) => { resolveWrite = resolve })
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === "POST") return pending
    return String(input).endsWith("/transfer-options") ? json(transferOptions) : String(input).includes("person-2") ? json({ ...vacant, allowed_actions: { create_stay: false } }) : json(occupied)
  })
  const view = mount(fetcher)
  await userEvent.click(await screen.findByRole("button", { name: "修改房间" }))
  await userEvent.click(await screen.findByRole("radio", { name: "一号楼 · 四层 · 410" }))
  await userEvent.type(screen.getByLabelText("调房原因"), "切换前草稿")
  await userEvent.click(screen.getByRole("button", { name: "确认调房" }))
  expect(screen.getByRole("button", { name: "确认调房" })).toBeDisabled()
  const nextAccount = switchType === "account" ? "next-account" : accountId
  const nextPerson = switchType === "person" ? "person-2" : personId
  view.client.setQueryData(personHousingKeys.summary(nextAccount, "unrelated-person"), { marker: "must stay fresh" })
  view.rerender(<QueryClientProvider client={view.client}><PersonHousingPanel accountId={nextAccount} personId={nextPerson} /></QueryClientProvider>)
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  await act(async () => { resolveWrite(json(stay)); await pending })
  expect(screen.queryByDisplayValue("切换前草稿")).not.toBeInTheDocument()
  expect(view.client.getQueryState(personHousingKeys.summary(nextAccount, "unrelated-person"))?.isInvalidated).toBe(false)
  expect(fetcher.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1)
})

it("closes a transfer draft when server allowed_actions is revoked on refresh", async () => {
  let transferAllowed = true
  const { client } = mount(async (input) => String(input).endsWith("/transfer-options") ? json(transferOptions) : json({ ...occupied, management_stay: { ...stay, allowed_actions: { ...stay.allowed_actions, transfer: transferAllowed } } }))
  await userEvent.click(await screen.findByRole("button", { name: "修改房间" }))
  await userEvent.type(screen.getByLabelText("调房原因"), "撤销后不保留")
  transferAllowed = false
  await act(async () => { await client.invalidateQueries({ queryKey: personHousingKeys.summary(accountId, personId), exact: true }) })
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument())
  expect(screen.queryByRole("button", { name: "修改房间" })).not.toBeInTheDocument()
})

it("removes a create dialog's selected room when authorization options refresh", async () => {
  let available = true
  const { client } = mount(async (input) => {
    const url = String(input)
    if (url.endsWith("/check-in-options")) return json({ ...checkInOptions, items: available ? checkInOptions.items : [] })
    if (url.endsWith("/cohort-options")) return json({ access_revision: revision, items: [] })
    return json(vacant)
  })
  await userEvent.click(await screen.findByRole("button", { name: "安排入住" }))
  await userEvent.selectOptions(await screen.findByLabelText("选择房间"), "room-409")
  await userEvent.click(screen.getByRole("button", { name: "下一步" }))
  expect(screen.getByLabelText("入住人员")).toHaveTextContent("张三")
  available = false
  await act(async () => { await client.invalidateQueries({ queryKey: personHousingKeys.options(accountId, personId, personRevision), exact: true }) })
  await waitFor(() => expect(screen.queryByLabelText("入住人员")).not.toBeInTheDocument())
  expect(screen.getByRole("button", { name: "下一步" })).toBeDisabled()
})
