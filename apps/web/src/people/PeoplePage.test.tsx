import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { PeoplePage } from "./PeoplePage"

it("test_role_filter_and_denied_projection_are_distinct", async () => {
  const requests: string[] = []
  const person = { id: "p1", full_name: "双身份学员", nickname: null, person_types: ["student"], roles: [{ key: "team_member", name: "队员" }, { key: "team_manager", name: "队伍管理" }], team: { visibility: "denied", current: null }, identity_number: "****", phone: null, email: null, sex: "male", date_of_birth: "2000-01-01", staff_type: null, created_at: "2026-01-01T00:00:00+08:00", age_status: "adult", guardian_status: "not_applicable" }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    requests.push(String(input))
    return new Response(JSON.stringify(String(input).endsWith("/people/counts") ? { total: 1, student: 1, team_member: 1, team_manager: 1, in_team: 0 } : { items: [person], page: 1, page_size: 20, total: 1 }), { headers: { "Content-Type": "application/json" } })
  }))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><PeoplePage /></QueryClientProvider></MemoryRouter>)
  const user = userEvent.setup()
  expect(await screen.findByText("双身份学员")).toBeVisible()
  expect(screen.getByText("学员 · 队员 · 队伍管理")).toBeVisible()
  expect(screen.getByText("无查看权限")).toBeVisible()
  const counts = screen.getByRole("region", { name: "人员数量统计" })
  expect(counts).toHaveTextContent("学员1")
  expect(counts).toHaveTextContent("队员1")
  expect(within(counts).getByRole("button", { name: "队伍管理1" })).toBeVisible()
  expect(within(counts).getByText("在队人员").parentElement).toHaveTextContent(/^在队人员0$/)
  await user.selectOptions(screen.getByLabelText("角色筛选"), "team_manager")
  await user.click(screen.getByRole("button", { name: "查询" }))
  expect(requests.some((url) => url.includes("role=team_manager"))).toBe(true)
  await user.selectOptions(screen.getByLabelText("角色筛选"), "team_member")
  await user.click(screen.getByRole("button", { name: "查询" }))
  expect(requests.some((url) => new URL(url, "http://localhost").searchParams.get("role") === "team_member")).toBe(true)
})

afterEach(() => vi.unstubAllGlobals())

it("shows server-filtered accountless player and both rows once with activation labels and server counts", async () => {
  const base = { nickname: null, person_types: [], roles: [], team: { visibility: "denied", current: null }, identity_number: "****", phone: null, email: null, sex: "male", date_of_birth: "2000-01-01", staff_type: null, created_at: "2026-01-01T00:00:00+08:00", age_status: "adult", guardian_status: "not_applicable", account_status: "not_activated" }
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url) => new Response(JSON.stringify(String(url).endsWith("counts") ? { total: 2, student: 1, team_member: 2, in_team: 0 } : { items: [{ ...base, id: "p1", full_name: "独立队员", assisted_kind: "player" }, { ...base, id: "p2", full_name: "双身份", assisted_kind: "both", roles: [{ key: "team_member", name: "队员" }] }], page: 1, page_size: 20, total: 2 }), { headers: { "Content-Type": "application/json" } })))
  render(<MemoryRouter initialEntries={["/people?role=team_member"]}><QueryClientProvider client={new QueryClient()}><PeoplePage /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByRole("link", { name: "独立队员" })).toBeVisible()
  expect(screen.getAllByRole("link", { name: "双身份" })).toHaveLength(1)
  expect(screen.getByText("学员 · 队员")).toBeVisible()
  expect(screen.getAllByText("未激活")).toHaveLength(2)
  expect(screen.queryByText("队员 · 队员")).not.toBeInTheDocument()
  expect(within(screen.getByRole("region", { name: "人员数量统计" })).getByRole("button", { name: "队员2" })).toBeVisible()
})

it("renders masked data and searches exact phone", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    new Response(
      JSON.stringify({
        items: [
          {
            id: "30000000-0000-4000-8000-000000000001",
            full_name: "测试学员",
            nickname: "小宇",
            person_types: ["student"],
            identity_number: "110105********002X",
            phone: "138****8000",
            email: "student@example.com",
            sex: "female",
            date_of_birth: "2012-01-02",
            staff_type: null,
            created_at: "2026-07-26T10:00:00+08:00",
            age_status: "minor",
            guardian_status: "pending",
          },
        ],
        page: 1,
        page_size: 20,
        total: 1,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PeoplePage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.type(screen.getByLabelText("手机号查询"), "13800138000")
  await user.click(screen.getByRole("button", { name: "查询" }))

  expect(await screen.findByText("138****8000")).toBeVisible()
  const nicknameCell = screen.getByRole("cell", { name: "小宇" })
  expect(nicknameCell).toHaveAttribute("data-label", "昵称")
  expect(nicknameCell).toBeVisible()
  expect(screen.getByText("110105********002X")).toBeVisible()
  expect(screen.queryByText("13800138000")).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("phone=13800138000")),
  ).toBe(true)
})

it("loads URL filters and exposes each person's name as a detail link", async () => {
  const person = {
    id: "30000000-0000-4000-8000-000000000001",
    full_name: "链接测试学员",
    person_types: ["student"],
    identity_number: "110105********002X",
    phone: "138****8000",
    email: "student@example.com",
    sex: "female",
    date_of_birth: "2012-01-02",
    staff_type: null,
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "minor",
    guardian_status: "pending",
  }
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify({ items: [person], page: 1, page_size: 20, total: 1 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)

  render(
    <MemoryRouter initialEntries={["/people?name=链接测试学员"]}>
      <QueryClientProvider client={new QueryClient()}>
        <PeoplePage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const nameLink = await screen.findByRole("link", { name: "链接测试学员" })
  expect(nameLink).toHaveAttribute("href", `/people/${person.id}`)
  expect(screen.getByLabelText("姓名或昵称")).toHaveValue("链接测试学员")
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("name=%E9%93%BE%E6%8E%A5")),
  ).toBe(true)
})

it("shows adult status and links a pending minor to guardian management", async () => {
  const minor = {
    id: "30000000-0000-4000-8000-000000000011",
    full_name: "待绑定学员",
    person_types: ["student"],
    identity_number: "110105********002X",
    phone: "138****8011",
    email: "minor@example.com",
    sex: "female",
    date_of_birth: "2012-01-02",
    staff_type: null,
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "minor",
    guardian_status: "pending",
  }
  const adult = {
    id: "30000000-0000-4000-8000-000000000012",
    full_name: "成年学员",
    person_types: ["student"],
    identity_number: "110105********001X",
    phone: "138****8012",
    email: "adult@example.com",
    sex: "male",
    date_of_birth: "1990-01-02",
    staff_type: null,
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "adult",
    guardian_status: "not_applicable",
  }
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ items: [minor, adult], page: 1, page_size: 20, total: 2 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PeoplePage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("未成年")).toBeVisible()
  expect(screen.getByRole("link", { name: "待绑定监护人" })).toHaveAttribute(
    "href",
    `/people/${minor.id}`,
  )
  expect(screen.getByText(/2012-01-02/)).toBeVisible()
  expect(screen.getByText("成年")).toBeVisible()
  expect(screen.getByText(/1990-01-02/)).toBeVisible()
  expect(screen.getAllByText("待绑定监护人")).toHaveLength(1)
})

it("shows global personnel counts by role", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/people/counts")) {
      return new Response(
        JSON.stringify({
          total: 12,
          student: 4,
          guardian: 3,
          staff: 4,
          coach: 2,
          life_teacher: 1,
          customer_service: 0,
          administration: 1,
          other_staff: 0,
          visitor: 1,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PeoplePage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const counts = await screen.findByRole("region", { name: "人员数量统计" })
  await waitFor(() => {
    expect(counts).toHaveTextContent("全部人员12")
    expect(counts).toHaveTextContent("学员4")
    expect(counts).toHaveTextContent("教练2")
    expect(counts).toHaveTextContent("生活老师1")
    expect(counts).toHaveTextContent("行政1")
    expect(counts).toHaveTextContent("访客1")
  })
  expect(fetchMock.mock.calls.some(([input]) => String(input).endsWith("/people/counts"))).toBe(
    true,
  )
})

it("filters the directory when a personnel count card is clicked", async () => {
  const coach = {
    id: "30000000-0000-4000-8000-000000000021",
    full_name: "可点击教练",
    person_types: ["staff"],
    identity_number: "110105********002X",
    phone: "138****8021",
    email: "coach@example.com",
    sex: "male",
    date_of_birth: "1990-01-02",
    staff_type: "coach",
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "adult",
    guardian_status: "not_applicable",
  }
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/people/counts")) {
      return new Response(
        JSON.stringify({
          total: 1,
          student: 0,
          guardian: 0,
          staff: 1,
          coach: 1,
          life_teacher: 0,
          customer_service: 0,
          administration: 0,
          other_staff: 0,
          visitor: 0,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (url.includes("staff_type=coach")) {
      return new Response(
        JSON.stringify({ items: [coach], page: 1, page_size: 20, total: 1 }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PeoplePage />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: /教练/ }))

  expect(await screen.findByRole("link", { name: "可点击教练" })).toBeVisible()
  expect(screen.getByText("工作人员 · 教练")).toBeVisible()
  expect(
    fetchMock.mock.calls.some(
      ([input]) => String(input).includes("type=staff") && String(input).includes("staff_type=coach"),
    ),
  ).toBe(true)
})

it("removes a previously rendered personnel snapshot after an explicit 403", async () => {
  const stalePerson = {
    id: "person-stale",
    full_name: "权限撤销前人员",
    nickname: "旧昵称",
    person_types: ["student"],
    identity_number: "110105********002X",
    phone: "138****8000",
    email: "stale@example.com",
    sex: "female",
    date_of_birth: "2012-01-02",
    staff_type: null,
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "minor",
    guardian_status: "pending",
  }
  let meCalls = 0
  let listCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) {
      meCalls += 1
      return new Response(JSON.stringify({
        account_id: "account-1",
        person_id: "operator-1",
        full_name: "管理员",
        email: "admin@example.com",
        force_password_change: false,
        permissions: meCalls === 1 ? ["people.read"] : [],
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.endsWith("/auth/csrf")) {
      return new Response(JSON.stringify({ csrf_token: "people-csrf" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith("/people/counts")) {
      return new Response(JSON.stringify({
        total: 1, student: 1, guardian: 0, staff: 0, coach: 0,
        life_teacher: 0, customer_service: 0, administration: 0,
        other_staff: 0, visitor: 0,
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.includes("/people?")) {
      listCalls += 1
      if (listCalls === 1) {
        return new Response(
          JSON.stringify({ items: [stalePerson], page: 1, page_size: 20, total: 1 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      return new Response(JSON.stringify({
        type: "about:blank",
        title: "Forbidden",
        status: 403,
        detail: "没有人员名单权限",
        code: "forbidden",
        request_id: "people-revoked",
      }), { status: 403, headers: { "Content-Type": "application/problem+json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })

  render(
    <MemoryRouter initialEntries={["/people"]}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider><PeoplePage /></AuthProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const table = await screen.findByRole("table", { name: "人员名单" })
  expect(within(table).getByText("权限撤销前人员")).toBeVisible()
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["people"] })
  })

  expect(await screen.findByRole("alert")).toHaveTextContent("人员名单权限已撤销")
  expect(screen.queryByText("权限撤销前人员")).not.toBeInTheDocument()
  expect(document.body.textContent).not.toContain("138****8000")
  expect(document.body.textContent).not.toContain("110105********002X")
  await waitFor(() => expect(
    queryClient.getQueriesData({ queryKey: ["people"] }).every(([, data]) => data === undefined),
  ).toBe(true))
  await waitFor(() => expect(meCalls).toBe(2))
  expect(listCalls).toBe(2)
})

it("keeps the prior personnel snapshot available after an ordinary network error", async () => {
  const stalePerson = {
    id: "person-network",
    full_name: "网络错误前人员",
    nickname: null,
    person_types: ["student"],
    identity_number: "110105********001X",
    phone: "139****9000",
    email: "network@example.com",
    sex: "male",
    date_of_birth: "2011-01-02",
    staff_type: null,
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "minor",
    guardian_status: "ready",
  }
  let listCalls = 0
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/people/counts")) {
      return new Response(JSON.stringify({
        total: 1, student: 1, guardian: 0, staff: 0, coach: 0,
        life_teacher: 0, customer_service: 0, administration: 0,
        other_staff: 0, visitor: 0,
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }
    if (url.includes("/people?")) {
      listCalls += 1
      if (listCalls === 1) {
        return new Response(
          JSON.stringify({ items: [stalePerson], page: 1, page_size: 20, total: 1 }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        )
      }
      throw new TypeError("Failed to fetch")
    }
    throw new Error(`Unexpected request: ${url}`)
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}><PeoplePage /></QueryClientProvider>
    </MemoryRouter>,
  )
  expect(await screen.findByText("网络错误前人员")).toBeVisible()
  await act(async () => {
    await queryClient.invalidateQueries({ queryKey: ["people"] })
  })
  expect(await screen.findByRole("alert")).toHaveTextContent("暂时无法读取人员名单")
  expect(screen.getByText("网络错误前人员")).toBeVisible()
  expect(queryClient.getQueriesData({ queryKey: ["people"] })[0]?.[1]).toEqual({
    items: [stalePerson], page: 1, page_size: 20, total: 1,
  })
})
