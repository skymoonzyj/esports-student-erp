import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PersonForm } from "./PersonForm"

afterEach(() => vi.unstubAllGlobals())

it("keeps unknown sex and birth date empty without weakening ordinary required validation", async () => {
  const writes: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (_url, init) => {
    if (init?.method === "PATCH") writes.push(init.body)
    return new Response(JSON.stringify({ id: "passport", full_name: "待补人员", person_types: ["staff"], nickname: null, identity_number: "****", phone: null, email: null, sex: null, date_of_birth: null, created_at: "2026-10-21T00:00:00Z", staff_type: "other", student_number: null, student_status: null, visit_reason: null, linked_student_id: null }), { headers: { "Content-Type": "application/json" } })
  }))
  render(<MemoryRouter><QueryClientProvider client={new QueryClient()}><PersonForm personId="passport" /></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByLabelText("性别")).toHaveValue("")
  expect(screen.getByLabelText("出生日期")).toHaveValue("")
  await userEvent.click(screen.getByRole("button", { name: "保存修改" }))
  expect(writes).toHaveLength(0)
})

it("places backend identity errors beside the matching field", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Unprocessable Entity",
          status: 422,
          detail: "Request validation failed",
          code: "identity_checksum_invalid",
          request_id: "person-form",
          errors: [{ field: "body.identity_number", message: "身份证校验码错误" }],
        }),
        { status: 422, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PersonForm />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.type(screen.getByLabelText("姓名"), "测试学员")
  await user.type(screen.getByLabelText("身份证"), "110105201201020021")
  await user.selectOptions(screen.getByLabelText("性别"), "female")
  await user.type(screen.getByLabelText("出生日期"), "2012-01-02")
  await user.type(screen.getByLabelText("学员编号"), "S-2026-001")
  await user.click(screen.getByRole("button", { name: "保存人员" }))

  expect(await screen.findByText("身份证校验码错误")).toBeVisible()
  expect(screen.getByLabelText("身份证")).toHaveAttribute("aria-invalid", "true")
})

it("updates only changed fields and never resubmits masked values", async () => {
  const person = {
    id: "30000000-0000-4000-8000-000000000001",
    full_name: "原姓名",
    person_types: ["student"],
    identity_number: "110105********002X",
    phone: "138****8000",
    email: "student@example.com",
    sex: "female",
    date_of_birth: "2012-01-02",
    created_at: "2026-07-26T10:00:00+08:00",
    student_number: "S-2026-001",
    student_status: "active",
    staff_type: null,
    visit_reason: null,
    linked_student_id: null,
  }
  const fetchMock = vi.fn<typeof fetch>(async () =>
    new Response(JSON.stringify(person), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PersonForm personId={person.id} />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  const name = await screen.findByLabelText("姓名")
  await user.clear(name)
  await user.type(name, "新姓名")
  await user.click(screen.getByRole("button", { name: "保存修改" }))

  const patchCall = fetchMock.mock.calls.find(([, init]) => init?.method === "PATCH")
  expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ full_name: "新姓名" })
})

it("places duplicate identity errors beside the identity field", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "人员资料已存在",
          code: "identity_already_exists",
          request_id: "duplicate-person-form",
        }),
        { status: 409, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )
  const user = userEvent.setup()

  render(
    <MemoryRouter>
      <QueryClientProvider client={new QueryClient()}>
        <PersonForm />
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.type(screen.getByLabelText("姓名"), "重复学员")
  await user.type(screen.getByLabelText("身份证"), "110105201201020021")
  await user.selectOptions(screen.getByLabelText("性别"), "female")
  await user.type(screen.getByLabelText("出生日期"), "2012-01-02")
  await user.type(screen.getByLabelText("学员编号"), "S-2026-002")
  await user.click(screen.getByRole("button", { name: "保存人员" }))

  expect(await screen.findByText("人员资料已存在")).toBeVisible()
  expect(screen.getByLabelText("身份证")).toHaveAttribute("aria-invalid", "true")
})
