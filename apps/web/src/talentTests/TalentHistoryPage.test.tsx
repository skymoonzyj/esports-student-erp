import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { TalentHistoryPage } from "./TalentHistoryPage"

afterEach(() => vi.unstubAllGlobals())

it("每页读取二十轮并只使用服务端 report_id 生成报告链接", async () => {
  const calls: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    calls.push(url)
    const page = url.includes("page=2") ? 2 : 1
    return new Response(JSON.stringify({
      items: page === 1
        ? [{
            id: "session-one",
            status: "completed",
            created_at: "2026-09-18T16:00:00Z",
            completed_at: "2026-09-18T16:08:00Z",
            abandoned_at: null,
            saved_count: 7,
            required_count: 7,
            report_id: "server-report-one",
          }, {
            id: "session-two",
            status: "in_progress",
            created_at: "2026-09-19T16:00:00Z",
            completed_at: null,
            abandoned_at: null,
            saved_count: 7,
            required_count: 8,
            report_id: null,
          }, {
            id: "session-three",
            status: "completed",
            created_at: "2026-09-20T16:00:00Z",
            completed_at: "2026-09-20T16:08:00Z",
            abandoned_at: null,
            saved_count: 8,
            required_count: 8,
            report_id: "server-report-three",
          }]
        : [],
      total: 21,
      page,
      page_size: 20,
    }), { status: 200, headers: { "Content-Type": "application/json" } })
  }))
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <TalentHistoryPage accountId="account-self" />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("2026-09-19")).toBeVisible()
  expect(screen.getByText("7/7")).toBeVisible()
  expect(screen.getByText("7/8")).toBeVisible()
  expect(screen.getByText("8/8")).toBeVisible()
  expect(screen.getAllByRole("link", { name: "查看报告" })[0]).toHaveAttribute(
    "href",
    "/talent-tests/reports/server-report-one",
  )
  expect(screen.queryByRole("link", { name: /session-one/ })).not.toBeInTheDocument()

  await user.click(screen.getByRole("button", { name: "下一页" }))
  expect(await screen.findByText("第 2 页")).toBeVisible()
  expect(calls.some((url) => url.includes("page=2") && url.includes("page_size=20"))).toBe(true)
})
