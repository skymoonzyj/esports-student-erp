import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, expect, it, vi } from "vitest"

import { SelfProfilePanel } from "./SelfProfilePanel"

const personId = "30000000-0000-4000-8000-000000000088"

function profileResponse(nickname: string | null) {
  return {
    person_id: personId,
    full_name: "张英君",
    nickname,
    person_types: ["staff"],
    staff_type: "administration",
    sex: "male",
    date_of_birth: "1985-02-20",
    age_status: "adult",
    masked_identity: "130823********0031",
    masked_phone: "186****0424",
    masked_email: "m***@foxmail.com",
  }
}

function renderPanel(fetchMock: ReturnType<typeof vi.fn<typeof fetch>>) {
  vi.stubGlobal("fetch", fetchMock)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <SelfProfilePanel personId={personId} />
    </QueryClientProvider>,
  )
  return client
}

afterEach(() => vi.unstubAllGlobals())

it("shows personal information and saves a nickname", async () => {
  let storedNickname: string | null = null
  const fetchMock = vi.fn<typeof fetch>(async (_input, init) => {
    if (init?.method === "PATCH") {
      storedNickname = (JSON.parse(String(init.body)) as { nickname: string | null }).nickname
    }
    return new Response(JSON.stringify(profileResponse(storedNickname)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  renderPanel(fetchMock)
  const user = userEvent.setup()

  expect(await screen.findByText("张英君")).toBeVisible()
  expect(screen.getByText("工作人员 · 行政")).toBeVisible()
  expect(screen.getByText("130823********0031")).toBeVisible()
  expect(screen.getByText("186****0424")).toBeVisible()
  expect(screen.getByText("m***@foxmail.com")).toBeVisible()

  await user.type(screen.getByLabelText("设置昵称"), "小君")
  await user.click(screen.getByRole("button", { name: "保存昵称" }))

  await waitFor(() => expect(storedNickname).toBe("小君"))
  expect(await screen.findByText("昵称已保存。")).toBeVisible()
  expect(screen.getByLabelText("设置昵称")).toHaveValue("小君")
})

it("shows my team in the existing personal profile for a member account", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    const body = url.endsWith("/team-management/context")
      ? { can_open_management: false, can_read_self: true, can_manage_catalog: false, can_assign_managers: false, can_transfer: false }
      : url.includes("/team-management/me?")
        ? { current: { membership_id: "m1", project_id: "pr1", project_name: "王者荣耀", team_id: "t1", team_name: "青训一队", joined_on: "2026-09-01" }, history: { items: [], page: 1, page_size: 20, total: 0 } }
        : profileResponse(null)
    return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } })
  }))
  render(<QueryClientProvider client={new QueryClient()}><SelfProfilePanel personId={personId} accountId="account-member" /></QueryClientProvider>)
  expect(await screen.findByText("王者荣耀 · 青训一队")).toBeVisible()
  expect(screen.queryByRole("button", { name: "添加队员" })).not.toBeInTheDocument()
})

it("sends null when the user clears an existing nickname", async () => {
  let storedNickname: string | null = "旧昵称"
  const fetchMock = vi.fn<typeof fetch>(async (_input, init) => {
    if (init?.method === "PATCH") {
      storedNickname = (JSON.parse(String(init.body)) as { nickname: string | null }).nickname
    }
    return new Response(JSON.stringify(profileResponse(storedNickname)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  renderPanel(fetchMock)
  const user = userEvent.setup()
  const input = await screen.findByLabelText("设置昵称")

  expect(input).toHaveValue("旧昵称")
  await user.clear(input)
  await user.click(screen.getByRole("button", { name: "保存昵称" }))

  await waitFor(() => expect(storedNickname).toBeNull())
  expect(screen.getByText("未设置")).toBeVisible()
})
