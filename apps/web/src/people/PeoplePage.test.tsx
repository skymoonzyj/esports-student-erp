import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PeoplePage } from "./PeoplePage"

afterEach(() => vi.unstubAllGlobals())

it("renders masked data and searches exact phone", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    new Response(
      JSON.stringify({
        items: [
          {
            id: "30000000-0000-4000-8000-000000000001",
            full_name: "测试学员",
            person_types: ["student"],
            identity_number: "110105********002X",
            phone: "138****8000",
            email: "student@example.com",
            sex: "female",
            date_of_birth: "2012-01-02",
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
  expect(screen.getByLabelText("姓名")).toHaveValue("链接测试学员")
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
  expect(screen.getByText("2012-01-02")).toBeVisible()
  expect(screen.getByText("成年")).toBeVisible()
  expect(screen.getByText("1990-01-02")).toBeVisible()
  expect(screen.getAllByText("待绑定监护人")).toHaveLength(1)
})
