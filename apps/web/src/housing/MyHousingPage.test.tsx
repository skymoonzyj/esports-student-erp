import { readFileSync } from "node:fs"
import { cwd } from "node:process"
import { resolve } from "node:path"

import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createMemoryRouter, RouterProvider } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import type { HousingPortalStay, MyHousing } from "../portal/api"
import { portalKeys } from "../portal/api"
import { MyHousingPage } from "./MyHousingPage"

const accountId = "account-housing-guardian"
const minorStudentId = "10000000-0000-4000-8000-000000000001"
const adultStudentId = "10000000-0000-4000-8000-000000000002"
const noStayStudentId = "10000000-0000-4000-8000-000000000003"
const overdueStudentId = "10000000-0000-4000-8000-000000000004"
const checkedInStudentId = "10000000-0000-4000-8000-000000000005"
const pendingStudentId = "10000000-0000-4000-8000-000000000006"
const forbiddenPhone = "13800138000"
const forbiddenFutureRoommate = "未来室友机密姓名"
const housingStyles = readFileSync(
  resolve(cwd(), "src/housing/housing.css"),
  "utf8",
)

function stay(
  overrides: Partial<HousingPortalStay> = {},
): HousingPortalStay {
  return {
    building_name: "青训一号楼",
    floor_name: "三层",
    room_name: "3-201",
    stay_purpose: "camp",
    planned_check_in_at: "2030-08-01T09:00:00+08:00",
    planned_check_out_at: "2030-08-22T09:00:00+08:00",
    actual_check_in_at: null,
    actual_check_out_at: null,
    status: "planned",
    roommates: null,
    ...overrides,
  }
}

const personalHousingResponse = {
  as_of: "2026-08-05T16:00:00+08:00",
  identity_number: "11010520120102106X",
  subjects: [
    {
      subject_person_id: minorStudentId,
      subject_name:
        "未成年住宿学员这是一个需要在窄屏设备上自动换行的很长姓名",
      phone: forbiddenPhone,
      current_stay: {
        ...stay({
          building_name: "青训一号楼这是一个很长的楼栋名称",
          room_name: "3-201-这是一个很长的房间显示名称",
          actual_check_in_at: "2026-08-01T09:05:00+08:00",
          status: "checked_in",
          roommates: ["当前室友甲"],
        }),
        occupant_phone: forbiddenPhone,
      },
      planned_stays: [],
    },
    {
      subject_person_id: adultStudentId,
      subject_name: "成年后仍绑定学员",
      phone: forbiddenPhone,
      current_stay: null,
      planned_stays: [
        {
          ...stay({
            building_name: "未来训练楼",
            floor_name: "五层",
            room_name: "5-501",
            stay_purpose: "temporary",
          }),
          future_roommate_names: [forbiddenFutureRoommate],
        },
      ],
    },
  ],
}

const statusMatrixResponse: MyHousing = {
  as_of: "2040-08-05T16:00:00+08:00",
  subjects: [
    {
      subject_person_id: noStayStudentId,
      subject_name: "暂无住宿学员",
      current_stay: null,
      planned_stays: [],
    },
    {
      subject_person_id: overdueStudentId,
      subject_name: "超时待确认学员",
      current_stay: null,
      planned_stays: [
        stay({ planned_check_in_at: "2039-08-01T09:00:00+08:00" }),
      ],
    },
    {
      subject_person_id: checkedInStudentId,
      subject_name: "已入住学员",
      current_stay: stay({
        actual_check_in_at: "2040-08-01T09:05:00+08:00",
        status: "checked_in",
        roommates: [],
      }),
      planned_stays: [],
    },
    {
      subject_person_id: pendingStudentId,
      subject_name: "待确认退房学员",
      current_stay: stay({
        actual_check_in_at: "2040-08-01T09:05:00+08:00",
        status: "checkout_pending",
        roommates: ["仍在同住室友"],
      }),
      planned_stays: [],
    },
  ],
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function problem(status: number, code: string, detail: string): Response {
  return jsonResponse(
    {
      type: "about:blank",
      title: "Request failed",
      status,
      detail,
      code,
      request_id: `housing-${code}`,
    },
    status,
  )
}

function serializeOwnProperties(
  value: unknown,
  seen = new Set<object>(),
): string {
  if (typeof value !== "object" || value === null) return String(value)
  if (seen.has(value)) return "[circular]"
  seen.add(value)
  return Reflect.ownKeys(value)
    .flatMap((key) => {
      const descriptor = Object.getOwnPropertyDescriptor(value, key)
      if (!descriptor || !("value" in descriptor)) return []
      return [String(key), serializeOwnProperties(descriptor.value, seen)]
    })
    .join("\n")
}

type HousingResponse =
  | Response
  | Promise<Response>
  | ((call: number) => Response | Promise<Response>)

function renderHousingPage(
  housingResponse: HousingResponse,
  options: { seed?: unknown } = {},
) {
  let housingCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (request) => {
    const url = String(request)
    if (url.endsWith("/auth/me")) {
      return jsonResponse({
        account_id: accountId,
        person_id: "person-housing-guardian",
        full_name: "住宿监护人",
        email: "housing-guardian@example.test",
        force_password_change: false,
        permissions: [],
      })
    }
    if (url.endsWith("/auth/csrf")) {
      return jsonResponse({ csrf_token: "housing-page-csrf" })
    }
    if (url.endsWith("/me/housing")) {
      housingCalls += 1
      return typeof housingResponse === "function"
        ? housingResponse(housingCalls)
        : housingResponse
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)

  const router = createMemoryRouter(
    [
      { path: "/me/housing", element: <MyHousingPage /> },
      { path: "/", element: <p>工作台</p> },
    ],
    { initialEntries: ["/me/housing"] },
  )
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  if (options.seed !== undefined) {
    queryClient.setQueryData(portalKeys.housing(accountId), options.seed)
  }
  render(
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
      </AuthProvider>
    </QueryClientProvider>,
  )
  return { fetchMock, queryClient, router }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

it("switches students and distinguishes hidden future roommates from current roommates", async () => {
  const { queryClient } = renderHousingPage(
    jsonResponse(personalHousingResponse),
  )
  const user = userEvent.setup()

  expect(
    await screen.findByRole("heading", { name: "当前住宿" }),
  ).toBeVisible()
  expect(screen.getByText("当前室友甲")).toBeVisible()
  expect(
    screen.getByRole("heading", {
      name: "未成年住宿学员这是一个需要在窄屏设备上自动换行的很长姓名",
    }),
  ).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), adultStudentId)

  expect(
    screen.getByRole("heading", { name: "成年后仍绑定学员" }),
  ).toBeVisible()
  expect(screen.getByText("实际入住后可查看")).toBeVisible()
  expect(screen.getByText("已预约")).toBeVisible()
  expect(screen.queryByText(forbiddenFutureRoommate)).not.toBeInTheDocument()

  const cached = queryClient.getQueryData(portalKeys.housing(accountId))
  expect(JSON.stringify(cached)).not.toMatch(
    /13800138000|identity_number|phone|occupant_phone|future_roommate_names|未来室友机密姓名/,
  )
  expect(document.body.textContent).not.toContain(forbiddenPhone)
  expect(document.body.textContent).not.toContain(minorStudentId)
  expect(document.body.textContent).not.toContain(adultStudentId)
  for (const element of document.querySelectorAll("a,[aria-label],[data-testid]")) {
    const serialized = Array.from(element.attributes)
      .map((attribute) => attribute.value)
      .join(" ")
    expect(serialized).not.toContain(minorStudentId)
    expect(serialized).not.toContain(adultStudentId)
  }
})

it("shows no-stay, overdue planned, checked-in and checkout-pending states", async () => {
  const user = userEvent.setup()
  renderHousingPage(jsonResponse(statusMatrixResponse))

  await user.selectOptions(
    await screen.findByLabelText("当前学员"),
    noStayStudentId,
  )
  expect(await screen.findByText("暂无住宿安排")).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), overdueStudentId)
  expect(screen.getByText("待确认入住")).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), checkedInStudentId)
  expect(screen.getByText("已入住")).toBeVisible()
  expect(screen.getByText("暂无室友")).toBeVisible()

  await user.selectOptions(screen.getByLabelText("当前学员"), pendingStudentId)
  expect(screen.getByText("待确认退房")).toBeVisible()
  expect(screen.getByText("仍在同住室友")).toBeVisible()
})

it("omits the selector for one subject and never fabricates null times", async () => {
  const response: MyHousing = {
    as_of: "2040-08-05T16:00:00+08:00",
    subjects: [
      {
        subject_person_id: checkedInStudentId,
        subject_name: "单学员住宿账号",
        current_stay: stay({
          planned_check_out_at: null,
          actual_check_in_at: "2040-08-01T09:05:00+08:00",
          actual_check_out_at: null,
          status: "checked_in",
          roommates: [],
        }),
        planned_stays: [],
      },
    ],
  }
  renderHousingPage(jsonResponse(response))

  expect(
    await screen.findByRole("heading", { name: "单学员住宿账号" }),
  ).toBeVisible()
  expect(screen.queryByLabelText("当前学员")).not.toBeInTheDocument()
  expect(screen.getByText("2040-08-01 09:05")).toBeVisible()
  expect(screen.queryByText("计划离开")).not.toBeInTheDocument()
  expect(screen.queryByText("实际离开")).not.toBeInTheDocument()
})

it("drops a selected subject as soon as it is no longer authorized", async () => {
  const { queryClient } = renderHousingPage((call) =>
    jsonResponse(
      call === 1
        ? personalHousingResponse
        : {
            as_of: personalHousingResponse.as_of,
            subjects: [personalHousingResponse.subjects[0]],
          },
    ),
  )
  const user = userEvent.setup()
  await user.selectOptions(
    await screen.findByLabelText("当前学员"),
    adultStudentId,
  )
  expect(
    screen.getByRole("heading", { name: "成年后仍绑定学员" }),
  ).toBeVisible()

  await act(async () => {
    await queryClient.refetchQueries({
      queryKey: portalKeys.housing(accountId),
      exact: true,
    })
  })

  expect(
    await screen.findByRole("heading", {
      name: "未成年住宿学员这是一个需要在窄屏设备上自动换行的很长姓名",
    }),
  ).toBeVisible()
  expect(screen.queryByText("成年后仍绑定学员")).not.toBeInTheDocument()
  expect(screen.queryByLabelText("当前学员")).not.toBeInTheDocument()
})

it("hides seeded housing while the authorization refresh is pending", async () => {
  const pending = new Promise<Response>(() => undefined)
  renderHousingPage(pending, {
    seed: {
      as_of: "2026-08-05T16:00:00+08:00",
      subjects: [
        {
          subject_person_id: minorStudentId,
          subject_name: "旧缓存学员",
          current_stay: null,
          planned_stays: [],
        },
      ],
    },
  })

  expect(await screen.findByText("正在更新住宿信息")).toBeVisible()
  expect(screen.queryByText("旧缓存学员")).not.toBeInTheDocument()
})

it("removes cached names and navigates home on private 404", async () => {
  const { queryClient, router } = renderHousingPage(
    problem(404, "housing_subject_not_found", "住宿资源不存在"),
    {
      seed: {
        as_of: "2026-08-05T16:00:00+08:00",
        subjects: [
          {
            subject_person_id: minorStudentId,
            subject_name: "旧缓存学员",
            current_stay: null,
            planned_stays: [],
          },
        ],
      },
    },
  )

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  expect(screen.queryByText("旧缓存学员")).not.toBeInTheDocument()
  expect(
    queryClient.getQueryData(portalKeys.housing(accountId)),
  ).toBeUndefined()
})

it("clears the account portal tree and navigates home on malformed HTTP 404 JSON", async () => {
  const leakedBody = "private-invalid-json-roommate-13800138000"
  const { queryClient, router } = renderHousingPage(
    new Response(leakedBody, {
      status: 404,
      headers: { "Content-Type": "application/json" },
    }),
    {
      seed: {
        as_of: "2026-08-05T16:00:00+08:00",
        subjects: [
          {
            subject_person_id: minorStudentId,
            subject_name: "旧缓存学员",
            current_stay: null,
            planned_stays: [],
          },
        ],
      },
    },
  )
  queryClient.setQueryData(portalKeys.context(accountId), {
    private_name: "旧门户上下文",
  })
  queryClient.setQueryData(portalKeys.payments(accountId, 1), {
    private_payment: "旧付款记录",
  })

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  await waitFor(() =>
    expect(
      queryClient.getQueriesData({ queryKey: portalKeys.root(accountId) }),
    ).toEqual([]),
  )
  expect(document.body.textContent).not.toContain(leakedBody)
})

it("treats the HTTP 404 status as authoritative when the body claims 500", async () => {
  const { queryClient, router } = renderHousingPage(
    jsonResponse(
      {
        type: "about:blank",
        title: "Disguised",
        status: 500,
        detail: "伪装成服务错误的私有资源",
        code: "internal_error",
        request_id: "housing-disguised-private-404",
      },
      404,
    ),
    {
      seed: {
        as_of: "2026-08-05T16:00:00+08:00",
        subjects: [
          {
            subject_person_id: minorStudentId,
            subject_name: "必须清除的旧缓存学员",
            current_stay: null,
            planned_stays: [],
          },
        ],
      },
    },
  )

  await waitFor(() => expect(router.state.location.pathname).toBe("/"))
  await waitFor(() =>
    expect(
      queryClient.getQueriesData({ queryKey: portalKeys.root(accountId) }),
    ).toEqual([]),
  )
  expect(document.body.textContent).not.toContain("伪装成服务错误的私有资源")
})

it("redacts a retryable service error from the DOM and query error cache", async () => {
  const leakedPhone = "13800138000"
  const leakedIdentity = "11010520120102106X"
  const leakedDetail = `数据库错误：${leakedPhone} / ${leakedIdentity}`
  const leakedProblemValues = {
    type: `private-error-type-${leakedPhone}`,
    title: `private-error-title-${leakedIdentity}`,
    code: `private_error_code_${leakedPhone}`,
    requestId: `private-error-request-${leakedIdentity}`,
    errorField: `private.error.field.${leakedPhone}`,
    errorMessage: `private-error-message-${leakedIdentity}`,
  }
  const forbiddenErrorText = [
    leakedPhone,
    leakedIdentity,
    leakedDetail,
    ...Object.values(leakedProblemValues),
    "occupant_phone",
    "identity_number",
  ]
  const user = userEvent.setup()
  const { queryClient, router } = renderHousingPage((call) =>
    call === 1
      ? jsonResponse(
          {
            type: leakedProblemValues.type,
            title: leakedProblemValues.title,
            status: 500,
            detail: leakedDetail,
            code: leakedProblemValues.code,
            request_id: leakedProblemValues.requestId,
            errors: [
              {
                field: leakedProblemValues.errorField,
                message: leakedProblemValues.errorMessage,
              },
            ],
            occupant_phone: leakedPhone,
            identity_number: leakedIdentity,
          },
          500,
        )
      : jsonResponse({
          as_of: statusMatrixResponse.as_of,
          subjects: [statusMatrixResponse.subjects[2]],
        }),
  )

  const alert = await screen.findByRole("alert")
  expect(alert).toHaveTextContent("服务暂时不可用，请重试。")
  expect(router.state.location.pathname).toBe("/me/housing")
  for (const forbidden of forbiddenErrorText) {
    expect(document.body.textContent).not.toContain(forbidden)
  }

  const cachedError = queryClient.getQueryCache().find({
    queryKey: portalKeys.housing(accountId),
    exact: true,
  })?.state.error
  expect(cachedError).toBeInstanceOf(Error)
  const typedCachedError = cachedError as Error & {
    problem: unknown
  }
  expect(typedCachedError.message).toBe("服务暂时不可用，请重试。")
  expect(String(typedCachedError)).toBe(
    "ProblemDetailsError: 服务暂时不可用，请重试。",
  )
  expect(typedCachedError.stack).not.toBeUndefined()
  expect(typedCachedError.cause).toBeUndefined()
  expect(typedCachedError.problem).toEqual({
    type: "about:blank",
    title: "Request failed",
    status: 500,
    detail: "服务暂时不可用，请重试。",
    code: "housing_request_failed",
    request_id: "",
  })
  const serializedCachedError = serializeOwnProperties(typedCachedError)
  for (const forbidden of forbiddenErrorText) {
    expect(typedCachedError.stack).not.toContain(forbidden)
    expect(serializedCachedError).not.toContain(forbidden)
  }

  await user.click(screen.getByRole("button", { name: "重试" }))

  expect(
    await screen.findByRole("heading", { name: "已入住学员" }),
  ).toBeVisible()
  expect(screen.queryByRole("alert")).not.toBeInTheDocument()
})

it("defines responsive cards without horizontal clipping workarounds", () => {
  for (const className of [
    "myHousingPage",
    "myHousingSubjectPicker",
    "myHousingCurrent",
    "myHousingPlannedGrid",
    "myHousingStayCard",
    "myHousingTimeGrid",
    "myHousingRoommates",
  ]) {
    expect(housingStyles).toContain(`.${className}`)
  }
  expect(housingStyles).toMatch(/overflow-wrap\s*:\s*anywhere/)
  expect(housingStyles).toMatch(
    /\.myHousingSubjectPicker\s+select\s*{[^}]*min-height\s*:\s*44px/s,
  )
  expect(housingStyles).toMatch(
    /\.myHousingPlannedGrid\s*{[^}]*grid-template-columns\s*:\s*minmax\(0,\s*1fr\)/s,
  )
  expect(housingStyles).toMatch(
    /@media\s*\(min-width:\s*1024px\)[\s\S]*?\.myHousingPlannedGrid\s*{[^}]*repeat\(2,\s*minmax\(0,\s*1fr\)\)/,
  )
  expect(housingStyles).toMatch(
    /@media\s*\(max-width:\s*(?:480|360)px\)[\s\S]*?\.myHousingSubjectPicker\s+select\s*{[^}]*width\s*:\s*100%/,
  )
  expect(housingStyles).toMatch(
    /@media\s*\(max-width:\s*(?:480|360)px\)[\s\S]*?\.myHousingPage\s*{[^}]*padding-bottom\s*:/,
  )
  expect(housingStyles).not.toMatch(
    /\.myHousingPage\s*{[^}]*overflow-x\s*:\s*hidden/s,
  )
})
