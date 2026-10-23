import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Link, MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import {
  PersonDetailPage,
  SENSITIVE_REVEAL_DURATION_MS,
} from "./PersonDetailPage"
import { currentAge } from "./presentation"

vi.mock("../auth/AuthProvider", () => {
  const refreshUser = vi.fn(async () => undefined)
  return {
    useOptionalAuth: () => ({ status: "authenticated", user: { account_id: "10000000-0000-4000-8000-000000000001" } }),
    useAuth: () => ({
      user: { account_id: "10000000-0000-4000-8000-000000000001", permissions: ["esports_assessments.read"], scoped_permissions: [] },
      refreshUser,
    }),
  }
})

const personId = "30000000-0000-4000-8000-000000000001"
const otherPersonId = "30000000-0000-4000-8000-000000000002"
const foodRevision = "a".repeat(64)
const cuisineId = "40000000-0000-4000-8000-000000000099"

it("renders accountless player summary to a read-only viewer without fetching editor metadata or inventing student profile", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ...personResponse(), person_types: [], assisted_kind: "player", account_status: "not_activated", student_number: null, student_status: null }), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetcher)
  render(<MemoryRouter initialEntries={[`/people/${personId}`]}><QueryClientProvider client={new QueryClient()}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(["people.read"])} />} /></Routes></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("队员")).toBeVisible()
  expect(screen.getByText("未激活")).toBeVisible()
  expect(screen.queryByRole("link", { name: "编辑档案" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /更正本人|变更负责/ })).not.toBeInTheDocument()
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([`/api/v1/people/${personId}`])
})

it("uses dedicated allowed actions for a pure player without inventing a student guardian workflow", async () => {
  const fetcher = vi.fn<typeof fetch>(async (url) => new Response(JSON.stringify(String(url).endsWith("editor-context") ? {
    person_id: personId, kind: "player", profile_version: 7, current_coach_person_ids: [], eligible_coaches: [], allowed_actions: ["correct_profile"],
  } : { ...personResponse(), person_types: [], student_number: null, student_status: null, assisted_kind: "player", account_status: "not_activated" }), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetcher)
  render(<MemoryRouter initialEntries={[`/people/${personId}`]}><QueryClientProvider client={new QueryClient()}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(["people.read"])} scopedPermissions={new Set(["people.update"])} />} /></Routes></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByRole("button", { name: "更正本人及监护资料" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "变更负责教练" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "编辑档案" })).not.toBeInTheDocument()
  expect(fetcher.mock.calls.filter(([url]) => String(url).includes("guardians"))).toHaveLength(0)
  expect(screen.getByText("队员")).toBeVisible()
})

it.each([false, true])("keeps legacy student guardian authorization reachable after later assisted approval (manage=%s)", async (canManage) => {
  const guardianId = "40000000-0000-4000-8000-000000000031"
  const relationshipId = "50000000-0000-4000-8000-000000000031"
  const nextGuardianId = "40000000-0000-4000-8000-000000000032"
  const nextRelationshipId = "50000000-0000-4000-8000-000000000032"
  let ended = false
  let bound = false
  const writes: Array<{ url: string; body: Record<string, unknown> }> = []
  const trusted = { ...personResponse(guardianId), full_name: "既有可信监护", person_types: ["guardian"], assisted_kind: null, account_status: "active", phone: "139****0031", age_status: "adult", guardian_status: "not_applicable", date_of_birth: "1988-01-02", relationship_id: relationshipId, relationship: "母亲", is_primary: true, valid_from: "2026-01-01", valid_until: null, status: "active", ended_at: null, end_reason: null }
  const candidate = { ...trusted, id: nextGuardianId, full_name: "待验证监护", phone: "139****0032", relationship_id: nextRelationshipId }
  const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } })
  const fetcher = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (init?.method === "POST") {
      writes.push({ url, body: JSON.parse(String(init.body)) })
      if (url.endsWith(`/guardians/${relationshipId}/end`)) { ended = true; return response({ ...trusted, status: "ended", end_reason: "监护安排调整" }) }
      if (url.endsWith("/guardians")) { bound = true; return response(candidate) }
      if (url.endsWith("/corrections")) return response({ request_id: null, person_id: personId, approval_id: nextRelationshipId })
    }
    if (url.includes("/readiness?")) return response({ ready: !ended, missing: ended ? ["guardian"] : [], age_status: "minor", guardian_status: ended ? "pending" : "ready", guardian_issues: bound ? [{ relationship_id: nextRelationshipId, guardian_person_id: nextGuardianId, account_id: null, reason_codes: ["relationship_verification_required"] }] : [] })
    if (url.includes("/guardians")) return response([{ ...trusted, status: ended ? "ended" : "active", ended_at: ended ? "2026-10-18T10:00:00+08:00" : null, end_reason: ended ? "监护安排调整" : null }, ...(bound ? [candidate] : [])])
    if (url.includes("/people?")) return response({ items: [candidate], total: 1, page: 1, page_size: 10 })
    if (url.endsWith("/editor-context")) return response({ person_id: personId, kind: "both", profile_version: 7, current_coach_person_ids: [], eligible_coaches: [], allowed_actions: ["correct_profile"] })
    if (url === `/api/v1/people/${personId}`) return response({ ...personResponse(), assisted_kind: "both", account_status: "active" })
    throw new Error(`Unexpected endpoint: ${url}`)
  })
  vi.stubGlobal("fetch", fetcher)
  render(<MemoryRouter initialEntries={[`/people/${personId}`]}><QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(canManage ? ["people.read", "people.update"] : ["people.read"])} />} /></Routes></QueryClientProvider></MemoryRouter>)
  expect(await screen.findByText("既有可信监护")).toBeVisible()
  expect(screen.getByText("未成年 · 监护人已绑定")).toBeVisible()
  expect(screen.getByText("学员 · 队员")).toBeVisible()
  if (!canManage) {
    expect(screen.queryByRole("button", { name: "绑定监护人" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "结束监护关系" })).not.toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "更正本人及监护资料" })).not.toBeInTheDocument()
    expect(fetcher.mock.calls.some(([url]) => String(url).endsWith("editor-context"))).toBe(false)
    expect(writes).toHaveLength(0)
    return
  }
  await userEvent.click(screen.getByRole("button", { name: "结束监护关系" }))
  fireEvent.change(screen.getByLabelText("结束原因"), { target: { value: "监护安排调整" } })
  await userEvent.click(screen.getByRole("button", { name: "确认结束" }))
  expect(await screen.findByText("已结束")).toBeVisible()
  expect(writes[0]).toEqual({ url: `/api/v1/people/${personId}/guardians/${relationshipId}/end`, body: { reason: "监护安排调整" } })
  await userEvent.click(screen.getByRole("button", { name: "绑定监护人" }))
  await userEvent.type(screen.getByLabelText("查找已有监护人"), "待验证监护")
  await userEvent.click(screen.getByRole("button", { name: "搜索" }))
  await userEvent.click(await screen.findByRole("radio", { name: "待验证监护 139****0032" }))
  await userEvent.click(screen.getByRole("button", { name: "确认绑定" }))
  expect(await screen.findByText("监护关系待验证")).toBeVisible()
  expect(writes[1]).toEqual({ url: `/api/v1/people/${personId}/guardians`, body: { guardian_id: nextGuardianId, relationship: "父亲", is_primary: false, valid_from: expect.stringMatching(/^\d{4}-\d{2}-\d{2}$/), valid_until: null } })
  expect(screen.queryByText("未成年 · 监护人已绑定")).not.toBeInTheDocument()
  await userEvent.click(screen.getByRole("button", { name: "更正本人及监护资料" }))
  expect(await screen.findByLabelText("身份证")).toHaveValue("")
  for (const [label, value] of [["姓名", "测试学员"], ["身份证", "110105201201020029"], ["出生日期", "2012-01-02"], ["手机号", "13800000000"], ["邮箱", "student@example.com"], ["资料更正原因", "资料核对"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  await userEvent.selectOptions(screen.getByLabelText("性别"), "female")
  await userEvent.click(screen.getByRole("button", { name: "添加监护人" }))
  for (const [label, value] of [["监护人姓名", "待验证监护"], ["监护人身份证", "110105199001021238"], ["监护人出生日期", "1990-01-02"], ["监护人手机号", "19990000018"], ["与本人关系", "父亲"]]) fireEvent.change(screen.getByLabelText(label), { target: { value } })
  await userEvent.selectOptions(screen.getByLabelText("监护人性别"), "male")
  await userEvent.click(screen.getByRole("button", { name: "提交更正" }))
  expect(await screen.findByText("资料更正成功。")).toBeVisible()
  expect(writes).toHaveLength(3)
  expect(writes[2]).toEqual({ url: `/api/v1/personnel-intake/people/${personId}/corrections`, body: { full_name: "测试学员", identity_number: "110105201201020029", phone: "13800000000", email: "student@example.com", sex: "female", date_of_birth: "2012-01-02", guardians: [{ full_name: "待验证监护", identity_number: "110105199001021238", phone: "19990000018", email: null, relationship: "父亲", sex: "male", date_of_birth: "1990-01-02", is_primary: true }], expected_version: 7, reason: "资料核对", idempotency_key: expect.any(String) } })
  expect(screen.getByText("监护关系待验证")).toBeVisible()
  expect(screen.queryByText("未成年 · 监护人已绑定")).not.toBeInTheDocument()
})

it("preserves an open assisted draft when the ordinary detail focus refresh temporarily fails", async () => {
  let unavailable = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (url) => {
    const editor = String(url).endsWith("editor-context")
    return new Response(JSON.stringify(editor ? { person_id: personId, kind: "player", profile_version: 3, current_coach_person_ids: [], eligible_coaches: [], allowed_actions: ["correct_profile"] } : unavailable ? { detail: "Unavailable" } : { ...personResponse(), person_types: [], assisted_kind: "player", account_status: "not_activated" }), { status: unavailable && !editor ? 503 : 200, headers: { "Content-Type": "application/json" } })
  }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MemoryRouter initialEntries={[`/people/${personId}`]}><QueryClientProvider client={client}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(["people.read", "people.update"])} />} /></Routes></QueryClientProvider></MemoryRouter>)
  await userEvent.click(await screen.findByRole("button", { name: "更正本人及监护资料" }))
  fireEvent.change(screen.getByLabelText("姓名"), { target: { value: "未提交草稿姓名" } })
  unavailable = true
  await act(async () => { await client.refetchQueries({ queryKey: ["person", personId] }) })
  await screen.findByText("人员摘要暂时无法刷新，现有草稿未清空。")
  expect(screen.getByLabelText("姓名")).toHaveValue("未提交草稿姓名")
})

it("shows denied team projection separately from an unassigned person", async () => {
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async () => new Response(JSON.stringify({
    ...personResponse(), roles: [{ key: "team_member", name: "队员" }],
    team: { visibility: "denied", current: null },
  }), { headers: { "Content-Type": "application/json" } })))
  renderDetail()
  expect(await screen.findByText("无查看权限")).toBeVisible()
  expect(screen.getByText("学员 · 队员")).toBeVisible()
  expect(screen.queryByText("未分配队伍")).not.toBeInTheDocument()
})

function personResponse(id = personId) {
  return {
    id,
    full_name: id === personId ? "测试学员" : "另一学员",
    nickname: id === personId ? "小宇" : null,
    person_types: ["student"],
    identity_number: "110105********002X",
    phone: "138****8000",
    email: "student@example.com",
    sex: "female",
    date_of_birth: "2012-01-02",
    created_at: "2026-07-26T10:00:00+08:00",
    age_status: "minor",
    guardian_status: "pending",
    student_number: "S-2026-001",
    student_status: "active",
    staff_type: null,
    visit_reason: null,
    linked_student_id: null,
  }
}

function renderDetail(revealDurationMs?: number) {
  const queryClient = new QueryClient()
  const result = render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set([
                  "people.identity.reveal",
                  "people.phone.reveal",
                  "people.update",
                ])}
                revealDurationMs={revealDurationMs}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { ...result, queryClient }
}

function foodCatalogResponse() {
  return {
    items: [
      {
        id: cuisineId,
        name: "川菜",
        sort_order: 1,
        is_active: true,
        is_other: false,
      },
    ],
    allowed_actions: [],
    access_revision: foodRevision,
  }
}

function foodProfileResponse(allowedActions: Array<"update"> = ["update"]) {
  return {
    person_id: personId,
    version: 2,
    cuisines: [
      {
        cuisine_option_id: cuisineId,
        name: "川菜",
        custom_name: null,
      },
    ],
    dishes: ["鱼香肉丝"],
    updated_at: "2026-08-12T10:00:00+08:00",
    allowed_actions: allowedActions,
    access_revision: foodRevision,
  }
}

function renderFoodDetail(input: {
  fetchMock: ReturnType<typeof vi.fn<typeof fetch>>
  permissions?: ReadonlySet<string>
  scopedPermissions?: ReadonlySet<string>
}) {
  vi.stubGlobal("fetch", input.fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={input.permissions}
                scopedPermissions={input.scopedPermissions}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return queryClient
}

afterEach(() => {
  vi.unstubAllGlobals()
})

function renderAssessmentDetail(permissions: string[], scopedPermissions: string[] = []) {
  const requests: string[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async input => {
    const url = String(input); requests.push(url)
    if (url.includes("/esports-assessments/")) return new Response(JSON.stringify({ detail: "不应泄露的报告姓名", code: "esports_not_found" }), { status: 404, headers: { "Content-Type": "application/json" } })
    return new Response(JSON.stringify({ ...personResponse(), person_types: ["visitor"] }), { status: 200, headers: { "Content-Type": "application/json" } })
  }))
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<MemoryRouter initialEntries={[`/people/${personId}`]}><QueryClientProvider client={queryClient}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(permissions)} scopedPermissions={new Set(scopedPermissions)} />} /></Routes></QueryClientProvider></MemoryRouter>)
  return requests
}

it("does not mount or request assessment reports based on people.read alone", async () => {
  const requests = renderAssessmentDetail(["people.read"])
  await screen.findByText("昵称：小宇")
  expect(screen.queryByRole("region", { name: "天赋评测 2 报告" })).not.toBeInTheDocument()
  expect(requests.some(url => url.includes("/esports-assessments/"))).toBe(false)
})

it.each(["global", "scoped"])("isolates denied assessment reports from the person profile with %s read", async scope => {
  const requests = renderAssessmentDetail(scope === "global" ? ["esports_assessments.read"] : [], scope === "scoped" ? ["esports_assessments.read"] : [])
  expect(await screen.findByRole("region", { name: "天赋评测 2 报告" })).toBeVisible()
  expect(await screen.findByText("报告不存在或无权查看")).toBeVisible()
  expect(screen.getByText("昵称：小宇")).toBeVisible()
  expect(screen.queryByText("不应泄露的报告姓名")).not.toBeInTheDocument()
  expect(new URL(requests.find(url => url.includes("/esports-assessments/"))!, "https://erp.test").searchParams.get("person_id")).toBe(personId)
})

it("requires a purpose before revealing identity", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )
  const user = userEvent.setup()
  renderDetail()

  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))

  expect(screen.getByLabelText("查看用途")).toBeRequired()
  expect(screen.getByRole("dialog", { name: "查看完整身份证" })).toBeVisible()
})

it("shows the calculated age and an obvious minor status in the record", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )

  renderDetail()

  const age = `${currentAge("2012-01-02")} 岁`
  expect(await screen.findAllByText(age)).not.toHaveLength(0)
  expect(screen.getAllByText("未成年").length).toBeGreaterThanOrEqual(2)
  expect(screen.getByLabelText("人员类型与年龄")).toHaveTextContent("学员")
  expect(screen.getAllByText("昵称：小宇")).toHaveLength(1)
  expect(screen.getByText("昵称").parentElement).toHaveTextContent("小宇")
})

it("shows the staff role next to the staff type in a record", async () => {
  const coach = {
    ...personResponse(),
    full_name: "平台教练",
    person_types: ["staff"],
    age_status: "adult",
    guardian_status: "not_applicable",
    staff_type: "coach",
    student_number: null,
    student_status: null,
  }
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(coach), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )

  renderDetail()

  expect(await screen.findByRole("heading", { name: "平台教练" })).toBeVisible()
  expect(screen.getByLabelText("人员类型与年龄")).toHaveTextContent("工作人员 · 教练")
  expect(screen.getByText("工作人员类型").parentElement).toHaveTextContent("教练")
})

it("keeps a revealed identity only in temporary page state", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (...args) => {
    const init = args[1]
    if (init?.method === "POST") {
      return new Response(JSON.stringify({ value: "11010520120102002X" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    return new Response(JSON.stringify(personResponse()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const { queryClient } = renderDetail(20)
  const revealButton = await screen.findByRole("button", { name: "查看完整身份证" })
  const user = userEvent.setup()

  await user.click(revealButton)
  await user.type(screen.getByLabelText("查看用途"), "入住资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))

  expect(await screen.findByText("11010520120102002X")).toBeVisible()
  expect(queryClient.getMutationCache().getAll()).toHaveLength(0)
  expect(SENSITIVE_REVEAL_DURATION_MS).toBe(60_000)
  await waitFor(() => {
    expect(screen.queryByText("11010520120102002X")).not.toBeInTheDocument()
  })
})

it("expires identity and phone reveals on independent timers", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (...args) => {
    const init = args[1]
    if (init?.method === "POST") {
      const field = JSON.parse(String(init.body)).field as "identity_number" | "phone"
      return new Response(
        JSON.stringify({
          value: field === "identity_number" ? "11010520120102002X" : "13800138000",
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(JSON.stringify(personResponse()), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  renderDetail(160)
  const user = userEvent.setup()

  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))
  await user.type(screen.getByLabelText("查看用途"), "入住资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("11010520120102002X")).toBeVisible()

  await new Promise((resolve) => setTimeout(resolve, 100))
  await user.click(screen.getByRole("button", { name: "查看完整手机号" }))
  await user.type(screen.getByLabelText("查看用途"), "账号资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("13800138000")).toBeVisible()

  await waitFor(
    () => expect(screen.queryByText("11010520120102002X")).not.toBeInTheDocument(),
    { timeout: 110 },
  )
  expect(screen.getByText("13800138000")).toBeVisible()
  await waitFor(() => expect(screen.queryByText("13800138000")).not.toBeInTheDocument())
})

it("clears a revealed value when the detail route changes person", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === "POST") {
      return new Response(JSON.stringify({ value: "11010520120102002X" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    const id = String(input).endsWith(otherPersonId) ? otherPersonId : personId
    return new Response(JSON.stringify(personResponse(id)), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    })
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Link to={`/people/${otherPersonId}`}>切换人员</Link>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage permissions={new Set(["people.identity.reveal"])} />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "查看完整身份证" }))
  await user.type(screen.getByLabelText("查看用途"), "入住资料核对")
  await user.click(screen.getByRole("button", { name: "确认查看" }))
  expect(await screen.findByText("11010520120102002X")).toBeVisible()

  await user.click(screen.getByRole("link", { name: "切换人员" }))
  expect(await screen.findByRole("heading", { name: "另一学员" })).toBeVisible()
  expect(screen.queryByText("11010520120102002X")).not.toBeInTheDocument()
  expect(screen.getByText("110105********002X")).toBeVisible()
})

it("only shows account opening to an accounts manager", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ),
  )

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/people/:personId" element={<PersonDetailPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "开通账号" })).not.toBeInTheDocument()
})

it("shows account opening when the detail API grants object-scoped management", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ ...personResponse(), account_management_allowed: true }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route path="/people/:personId" element={<PersonDetailPage />} />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("button", { name: "开通账号" })).toBeVisible()
})

it("honors an object-level denial even when global auth permissions are stale", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn<typeof fetch>().mockResolvedValue(
      new Response(
        JSON.stringify({ ...personResponse(), account_management_allowed: false }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    ),
  )

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route
            path="/people/:personId"
            element={<PersonDetailPage permissions={new Set(["accounts.manage"])} />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByRole("button", { name: "开通账号" })).not.toBeInTheDocument()
})

it("requires confirmation and clears the one-time password after opening an account", async () => {
  const temporaryPassword = "Person-detail-one-time-2026"
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/accounts") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          account: {
            id: "10000000-0000-4000-8000-000000000009",
            person_id: personId,
            full_name: "测试学员",
            phone: "138****8000",
            email: "student@example.com",
            status: "active",
            force_password_change: true,
            created_at: "2026-07-26T10:10:00+08:00",
          },
          temporary_password: temporaryPassword,
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      )
    }
    return new Response(
      JSON.stringify({ ...personResponse(), account_management_allowed: true }),
      {
      status: 200,
      headers: { "Content-Type": "application/json" },
      },
    )
  })
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient()
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route
            path="/people/:personId"
            element={<PersonDetailPage permissions={new Set(["accounts.manage"])} />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  expect(screen.getByRole("dialog", { name: "确认开通账号" })).toBeVisible()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(false)

  await user.click(screen.getByRole("button", { name: "确认开通" }))
  expect(await screen.findByText(temporaryPassword)).toBeVisible()
  expect(JSON.stringify(queryClient.getQueryCache().getAll())).not.toContain(temporaryPassword)

  await user.click(screen.getByRole("button", { name: "我已保存并关闭" }))
  expect(screen.queryByText(temporaryPassword)).not.toBeInTheDocument()
})

it("links back to guardian management when account opening is blocked", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    if (String(input).endsWith("/accounts") && init?.method === "POST") {
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Conflict",
          status: 409,
          detail: "minor student requires a guardian",
          code: "minor_guardian_required",
          request_id: "detail-guardian-error",
          errors: [{ field: "guardian", message: "required" }],
        }),
        { status: 409, headers: { "Content-Type": "application/problem+json" } },
      )
    }
    return new Response(
      JSON.stringify({ ...personResponse(), account_management_allowed: true }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    )
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()
  renderDetail()

  await user.click(await screen.findByRole("button", { name: "开通账号" }))
  await user.click(screen.getByRole("button", { name: "确认开通" }))

  expect(
    await screen.findByText("请先绑定已成年、已实名且账号启用的监护人"),
  ).toBeVisible()
  expect(screen.getByRole("link", { name: "返回人员档案" })).toHaveAttribute(
    "href",
    `/people/${personId}`,
  )
})

it("reuses the student matching panel for an administrator viewing a student", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/evaluations/admin/students/${personId}/matching-profile`)) {
      return new Response(
        JSON.stringify({
          student_person_id: personId,
          student_name: "测试学员",
          student_number: "S-2026-001",
          active_count: 1,
          active_limit: 3,
          game_accounts: [
            {
              id: "40000000-0000-4000-8000-000000000001",
              project_id: "30000000-0000-4000-8000-000000000001",
              project_name: "英雄联盟",
              game_id: "Authorized#PersonDetail",
              status: "active",
              valid_from: "2026-08-01T09:30:00+08:00",
              unbound_at: null,
              unbind_reason: null,
            },
          ],
          game_accounts_total: 1,
          game_accounts_truncated: false,
          auxiliary_phones: [
            {
              id: "60000000-0000-4000-8000-000000000001",
              masked_phone: "139****9000",
              status: "active",
              created_at: "2026-08-01T09:30:00+08:00",
              disabled_at: null,
            },
          ],
          auxiliary_phones_total: 1,
          auxiliary_phones_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set([
                  "people.read",
                  "evaluation_center.access",
                  "student_game_accounts.manage",
                ])}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "评价匹配资料" })).toBeVisible()
  expect(screen.getByText("Authorized#PersonDetail")).toBeVisible()
  expect(screen.getByText("139****9000")).toBeVisible()
  expect(fetchMock).toHaveBeenCalledWith(
    `/api/v1/evaluations/admin/students/${personId}/matching-profile`,
    expect.objectContaining({ credentials: "include" }),
  )
})

it("does not mount the embedded evaluation panel without center access", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient()}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set(["people.read", "student_game_accounts.manage"])}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByRole("heading", { name: "评价匹配资料" })).not.toBeInTheDocument()
  expect(
    fetchMock.mock.calls.some(([input]) => String(input).includes("/evaluations/")),
  ).toBe(false)
})

it("removes a revoked evaluation panel without leaving the person page", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/evaluations/admin/students/${personId}/matching-profile`)) {
      return new Response(
        JSON.stringify({
          student_person_id: personId,
          student_name: "测试学员",
          student_number: "S-2026-001",
          active_count: 1,
          active_limit: 3,
          game_accounts: [
            {
              id: "40000000-0000-4000-8000-000000000001",
              project_id: "30000000-0000-4000-8000-000000000001",
              project_name: "英雄联盟",
              game_id: "Authorized#RevokedPerson",
              status: "active",
              valid_from: "2026-08-01T09:30:00+08:00",
              unbound_at: null,
              unbind_reason: null,
            },
          ],
          game_accounts_total: 1,
          game_accounts_truncated: false,
          auxiliary_phones: [],
          auxiliary_phones_total: 0,
          auxiliary_phones_truncated: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      )
    }
    if (
      url.endsWith(`/evaluations/admin/students/${personId}/game-account-limit`) &&
      init?.method === "PUT"
    ) {
      return new Response(
        JSON.stringify({
          type: "about:blank",
          title: "Forbidden",
          status: 403,
          detail: "评价中心权限已收回",
          code: "evaluation_access_denied",
          request_id: "person-evaluation-revoked",
        }),
        { status: 403, headers: { "Content-Type": "application/problem+json" } },
      )
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  const user = userEvent.setup()

  render(
    <MemoryRouter initialEntries={[`/people/${personId}`]}>
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <Routes>
          <Route
            path="/people/:personId"
            element={
              <PersonDetailPage
                permissions={new Set([
                  "people.read",
                  "evaluation_center.access",
                  "student_game_accounts.manage",
                ])}
              />
            }
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )

  expect(await screen.findByText("Authorized#RevokedPerson")).toBeVisible()
  await user.click(screen.getByRole("button", { name: "调整账号上限" }))
  await user.type(screen.getByLabelText("调整原因"), "整中心撤权")
  await user.click(screen.getByRole("button", { name: "确认调整" }))

  await waitFor(() => {
    expect(screen.queryByRole("heading", { name: "评价匹配资料" })).not.toBeInTheDocument()
    expect(screen.queryByText("正在更新基础数据权限...")).not.toBeInTheDocument()
  })
  expect(screen.getByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(screen.queryByText("Authorized#RevokedPerson")).not.toBeInTheDocument()
})

it.each([
  ["global", new Set(["food_preferences.read", "food_preferences.update"]), new Set<string>()],
  ["exact scoped", new Set<string>(), new Set(["food_preferences.read", "food_preferences.update"])],
] as const)(
  "embeds the exact person's editable food profile for a %s permission hint",
  async (_label, permissions, scopedPermissions) => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith(`/people/${personId}`)) {
        return new Response(JSON.stringify(personResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/food-preferences/cuisines?")) {
        return new Response(JSON.stringify(foodCatalogResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith(`/people/${personId}/food-preferences`)) {
        return new Response(JSON.stringify(foodProfileResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      throw new Error(`Unexpected request: ${url}`)
    })

    renderFoodDetail({ fetchMock, permissions, scopedPermissions })

    expect(await screen.findByText("鱼香肉丝")).toBeVisible()
    expect(
      screen.getByRole("button", { name: "编辑餐饮偏好" }),
    ).toBeVisible()
    expect(
      fetchMock.mock.calls.some(([request]) =>
        String(request).endsWith(`/people/${personId}/food-preferences`),
      ),
    ).toBe(true)
  },
)

it.each([403, 404] as const)(
  "hides a privately denied food profile on %s while keeping the person page",
  async (status) => {
    const fetchMock = vi.fn<typeof fetch>(async (input) => {
      const url = String(input)
      if (url.endsWith(`/people/${personId}`)) {
        return new Response(JSON.stringify(personResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.includes("/food-preferences/cuisines?")) {
        return new Response(JSON.stringify(foodCatalogResponse()), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      }
      if (url.endsWith(`/people/${personId}/food-preferences`)) {
        return new Response(
          JSON.stringify({
            type: "about:blank",
            title: "Not found",
            status,
            detail: "不应展示的餐饮权限详情",
            code: "food_preference_not_found",
            request_id: "private-food-profile",
          }),
          {
            status,
            headers: { "Content-Type": "application/problem+json" },
          },
        )
      }
      throw new Error(`Unexpected request: ${url}`)
    })

    renderFoodDetail({
      fetchMock,
      scopedPermissions: new Set(["food_preferences.read"]),
    })

    expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([request]) =>
          String(request).endsWith(`/people/${personId}/food-preferences`),
        ),
      ).toBe(true),
    )
    await waitFor(() =>
      expect(screen.queryByLabelText("餐饮偏好")).not.toBeInTheDocument(),
    )
    expect(screen.queryByText("不应展示的餐饮权限详情")).not.toBeInTheDocument()
  },
)

it("uses server allowed_actions as the only food edit authority", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.includes("/food-preferences/cuisines?")) {
      return new Response(JSON.stringify(foodCatalogResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    if (url.endsWith(`/people/${personId}/food-preferences`)) {
      if (init?.method === "PUT") throw new Error("read-only profile must not write")
      return new Response(JSON.stringify(foodProfileResponse([])), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })

  renderFoodDetail({
    fetchMock,
    permissions: new Set(["food_preferences.read", "food_preferences.update"]),
  })

  expect(await screen.findByText("鱼香肉丝")).toBeVisible()
  expect(
    screen.queryByRole("button", { name: "编辑餐饮偏好" }),
  ).not.toBeInTheDocument()
  expect(fetchMock.mock.calls.some(([, init]) => init?.method === "PUT")).toBe(
    false,
  )
})

it("does not query food preferences without a global or scoped read hint", async () => {
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith(`/people/${personId}`)) {
      return new Response(JSON.stringify(personResponse()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    }
    throw new Error(`Unexpected request: ${url}`)
  })

  renderFoodDetail({ fetchMock })

  expect(await screen.findByRole("heading", { name: "测试学员" })).toBeVisible()
  expect(
    fetchMock.mock.calls.some(([request]) =>
      String(request).includes("/food-preferences"),
    ),
  ).toBe(false)
})
