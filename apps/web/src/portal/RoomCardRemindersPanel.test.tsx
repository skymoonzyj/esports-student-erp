import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"
import { RoomCardRemindersContent, RoomCardRemindersPanel } from "./RoomCardRemindersPanel"
import type { useRoomCardReminders } from "./useRoomCardReminders"

vi.mock("../auth/AuthProvider", () => ({ useAuth: () => ({ user: { account_id: "account-a" } }) }))
vi.mock("./usePortalContext", async (original) => ({ ...await original(), usePortalContext: () => ({ isSuccess: true, isFetching: false, dataUpdatedAt: 1, error: null }) }))
const clients: QueryClient[] = []
function mount(status: number, items: unknown[] = [], available = true) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } }); clients.push(client)
  vi.stubGlobal("fetch", vi.fn(async () => Response.json(status === 200 ? { available, observed_at: "2026-11-05T12:00:00Z", business_date: "2026-11-05", next_refresh_at: "2026-11-05T16:00:00Z", items, total: items.length, page: 1, page_size: 20 } : {}, { status })))
  return render(<QueryClientProvider client={client}><MemoryRouter><RoomCardRemindersPanel /></MemoryRouter></QueryClientProvider>)
}
afterEach(() => { clients.forEach((client) => client.clear()); clients.length = 0; vi.unstubAllGlobals() })

it("distinguishes expired, due-today and seven-day reminders without any edit or physical-card controls", async () => {
  const base = { stay_id: "stay-a", person_id: "person-a", person_name: "住客甲", nickname: "小甲", building_name: "一号楼", room_number: "101", issued_on: "2026-11-01" }
  mount(200, [{ ...base, expires_on: "2026-11-04", days_remaining: -1 }, { ...base, stay_id: "stay-b", person_name: "住客乙", expires_on: "2026-11-05", days_remaining: 0 }, { ...base, stay_id: "stay-c", person_name: "住客丙", expires_on: "2026-11-12", days_remaining: 7 }])
  expect(await screen.findByText("已过期 1 天")).toBeVisible()
  expect(screen.getByText("今日到期")).toBeVisible()
  expect(screen.getByText("7 天后到期")).toBeVisible()
  expect(screen.getByText("住客甲")).toBeVisible()
  expect(screen.getByText("2026-11-12")).toBeVisible()
  expect(screen.queryByRole("button", { name: /保存|续期|开卡|门锁/ })).not.toBeInTheDocument()
})
it("shows empty and error states independently and retries", async () => {
  const view = mount(200)
  expect(await screen.findByText("暂无七日内到期或已过期的房卡")).toBeVisible()
  view.unmount()
  mount(422)
  expect(await screen.findByRole("alert")).toHaveTextContent("房卡提醒暂时无法更新")
  fireEvent.click(screen.getByRole("button", { name: "重试房卡提醒" }))
})
it("hides an unavailable reminder projection", async () => {
  mount(200, [], false)
  await waitFor(() => expect(screen.queryByRole("heading", { name: "房卡到期提醒" })).not.toBeInTheDocument())
})

it("accepts parent-owned query state and hides stale content on fetching/errors while supporting backend pagination", () => {
  const state = { query: { isPending: false, isFetching: false, isError: false, data: { available: true, observed_at: "2026-11-05T12:00:00Z", business_date: "2026-11-05", next_refresh_at: "2026-11-05T16:00:00Z", page: 1, page_size: 20, total: 21, items: [{ stay_id: "stay", person_id: "person", person_name: "分页住客", nickname: null, room_number: "101", building_name: "一号楼", issued_on: "2026-11-01", expires_on: "2026-11-12", days_remaining: 7 }] } }, page: 1, setPage: vi.fn(), retry: vi.fn() } as unknown as ReturnType<typeof useRoomCardReminders>
  const view = render(<RoomCardRemindersContent state={state} />)
  expect(screen.getByText("分页住客")).toBeVisible()
  fireEvent.click(screen.getByRole("button", { name: "下一页房卡提醒" }))
  expect(state.setPage).toHaveBeenCalledWith(2)
  view.rerender(<RoomCardRemindersContent state={{ ...state, query: { ...state.query, isFetching: true } }} />)
  expect(screen.queryByText("分页住客")).not.toBeInTheDocument()
  view.rerender(<RoomCardRemindersContent state={{ ...state, query: { ...state.query, isError: true } }} />)
  expect(screen.queryByText("分页住客")).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole("button", { name: "重试房卡提醒" }))
  expect(state.retry).toHaveBeenCalledOnce()
})
