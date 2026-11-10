import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query"
import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { portalKeys } from "./api"
import type { DepartureReminders } from "./departureApi"
import { DepartureRemindersPanel } from "./DepartureRemindersPanel"
import { useDepartureReminders } from "./useDepartureReminders"

const personA = "10000000-0000-4000-8000-000000000001"
const personB = "10000000-0000-4000-8000-000000000002"
const sourceA = "20000000-0000-4000-8000-000000000001"
const sourceB = "20000000-0000-4000-8000-000000000002"
const sourceC = "20000000-0000-4000-8000-000000000003"
const roomPath = `/housing/rooms/${sourceA}`
const clients: QueryClient[] = []

function reminders(overrides: Partial<DepartureReminders> = {}): DepartureReminders {
  return {
    available: true, observed_at: "2026-10-24T08:00:00+08:00", business_date: "2026-10-24",
    timezone: "Asia/Shanghai", next_refresh_at: "2026-10-25T00:00:00+08:00", window: "three_days",
    today_count: 1, three_day_count: 2, page: 1, page_size: 20, total: 2,
    items: [
      { person_id: personA, full_name: "同名学员", nickname: "小星", events: [
        { kind: "planned_checkout", source_id: sourceA, date: "2026-10-24", detail_path: roomPath, building_name: "一号楼", room_number: "101" },
        { kind: "enrollment_end", source_id: sourceB, date: "2026-10-25", detail_path: null, project_name: "英雄联盟", cohort_name: "秋季营", enrollment_status: "completed" },
        { kind: "enrollment_end", source_id: sourceC, date: "2026-10-26", detail_path: null, project_name: "无畏契约", cohort_name: "第二营", enrollment_status: "active" },
      ] },
      { person_id: personB, full_name: "同名学员", nickname: null, events: [
        { kind: "enrollment_end", source_id: sourceA, date: "2026-10-26", detail_path: null, project_name: "和平精英", cohort_name: "走读营", enrollment_status: "active" },
      ] },
    ], ...overrides,
  }
}

function renderPanel(response: DepartureReminders = reminders(), status = 200) {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = new URL(String(input), "http://localhost")
    if (status !== 200) return Response.json({ type: "about:blank", title: "Request failed", detail: "private upstream text", status, code: "departure_failed", request_id: "panel-error" }, { status })
    return Response.json({ ...response, window: url.searchParams.get("window"), page: Number(url.searchParams.get("page")) })
  })
  vi.stubGlobal("fetch", fetchMock)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  clients.push(client)
  function Harness() {
    const context = useQuery({ queryKey: portalKeys.context("panel-account"), queryFn: async () => true })
    const state = useDepartureReminders({ accountId: "panel-account", contextReady: context.isSuccess && !context.isFetching, contextEpoch: context.dataUpdatedAt })
    return <DepartureRemindersPanel state={state} />
  }
  render(<MemoryRouter><QueryClientProvider client={client}><Harness /></QueryClientProvider></MemoryRouter>)
  return { fetchMock, client }
}

afterEach(() => { clients.forEach((client) => client.clear()); clients.length = 0; vi.unstubAllGlobals() })

it("keeps same-name people separate and every authorized source date under one person", async () => {
  renderPanel()
  const panel = await screen.findByRole("region", { name: "退宿／结营提醒" })
  expect(within(panel).getByText("依据计划日期提示，不代表已实际离开")).toBeVisible()
  expect(await within(panel).findByRole("button", { name: "今日 1 人" })).toBeVisible()
  expect(within(panel).getByRole("button", { name: "近三日 2 人" })).toHaveAttribute("aria-pressed", "true")
  const people = within(panel).getAllByRole("article")
  expect(people).toHaveLength(2)
  expect(within(people[0]).getByText("小星")).toBeVisible()
  expect(within(people[1]).queryByText("小星")).not.toBeInTheDocument()
  expect(within(people[0]).getByText("计划退宿")).toBeVisible()
  expect(within(people[0]).getByText("2026-10-24")).toHaveAttribute("dateTime", "2026-10-24")
  expect(within(people[0]).getByText("2026-10-25")).toBeVisible()
  expect(within(people[0]).getByText("2026-10-26")).toBeVisible()
  expect(within(people[0]).getByText("教学已完成")).toBeVisible()
  expect(within(people[0]).queryByText("已退宿")).not.toBeInTheDocument()
  expect(within(people[0]).getByText("今日")).toBeVisible()
  expect(within(panel).getAllByRole("link")).toHaveLength(1)
  expect(within(panel).getByRole("link", { name: /查看计划退宿详情/ })).toHaveAttribute("href", roomPath)
  expect(within(panel).queryByRole("button", { name: /入住|退房|编辑|导出/ })).not.toBeInTheDocument()
})

it("hides unavailable reminders instead of claiming an authorized empty list", async () => {
  const { fetchMock } = renderPanel(reminders({ available: false, items: [], today_count: 0, three_day_count: 0, total: 0 }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce())
  await waitFor(() => expect(screen.queryByRole("region", { name: "退宿／结营提醒" })).not.toBeInTheDocument())
  expect(screen.queryByText("所选日期内暂无退宿或结营提醒")).not.toBeInTheDocument()
})

it("shows an explicit authorized empty result", async () => {
  renderPanel(reminders({ items: [], today_count: 0, three_day_count: 0, total: 0 }))
  expect(await screen.findByText("所选日期内暂无退宿或结营提醒")).toBeVisible()
  expect(screen.getByRole("button", { name: "近三日 0 人" })).toBeVisible()
})

it("shows a retryable failure without zero counts or upstream details", async () => {
  const user = userEvent.setup()
  const { fetchMock } = renderPanel(reminders(), 400)
  expect(await screen.findByRole("alert")).toHaveTextContent("退宿／结营提醒暂时无法更新")
  expect(screen.queryByRole("button", { name: /0 人/ })).not.toBeInTheDocument()
  expect(screen.queryByText("所选日期内暂无退宿或结营提醒")).not.toBeInTheDocument()
  expect(screen.queryByText("private upstream text")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "重试提醒" }))
  await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
})

it("hides cached names and counts throughout a refresh", async () => {
  const { client, fetchMock } = renderPanel()
  await screen.findByText("小星")
  let finish!: (response: Response) => void
  fetchMock.mockImplementationOnce(async () => new Promise<Response>((resolve) => { finish = resolve }))
  void client.invalidateQueries({ queryKey: ["portal", "panel-account", "departures"] })
  expect(await screen.findByText("正在更新退宿／结营提醒")).toBeVisible()
  expect(screen.queryByText("小星")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "今日 1 人" })).not.toBeInTheDocument()
  finish(Response.json(reminders()))
  expect(await screen.findByText("小星")).toBeVisible()
})

it("switches the requested window and pages with keyboard-operable controls", async () => {
  const user = userEvent.setup()
  const { fetchMock } = renderPanel(reminders({ total: 41, today_count: 41, three_day_count: 41 }))
  const today = await screen.findByRole("button", { name: "今日 41 人" })
  today.focus()
  await user.keyboard("{Enter}")
  await waitFor(() => expect(screen.getByRole("button", { name: "今日 41 人" })).toHaveAttribute("aria-pressed", "true"))
  await user.click(screen.getByRole("button", { name: "下一页提醒" }))
  expect(await screen.findByText("第 2 / 3 页 · 共 41 人")).toBeVisible()
  expect(fetchMock.mock.calls.some(([input]) => String(input).includes("window=today&page=2&page_size=20"))).toBe(true)
  await user.click(screen.getByRole("button", { name: "近三日 41 人" }))
  expect(await screen.findByText("第 1 / 3 页 · 共 41 人")).toBeVisible()
  expect(screen.getByRole("button", { name: "上一页提醒" })).toBeDisabled()
})
