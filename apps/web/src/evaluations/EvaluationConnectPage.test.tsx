import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import {
  createMemoryRouter,
  RouterProvider,
} from "react-router-dom"
import { StrictMode } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { EvaluationConnectPage } from "./EvaluationConnectPage"

const firstRequestId = "90000000-0000-4000-8000-000000000001"
const secondRequestId = "90000000-0000-4000-8000-000000000002"

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  })
}

function ticketResponse(
  ticket = "ticket-value",
  state = "state-value",
): Response {
  return jsonResponse(
    {
      callback_url: "http://localhost:8010/auth/erp/callback",
      ticket,
      state,
      expires_at: "2026-07-31T12:01:00+08:00",
    },
    201,
  )
}

function problemResponse(status: number, detail = "server detail"): Response {
  return jsonResponse(
    {
      type: "about:blank",
      title: "Bridge Error",
      status,
      detail,
      code:
        status === 404
          ? "bridge_request_not_found"
          : status === 409
            ? "bridge_request_conflict"
            : "bridge_request_expired",
      request_id: `connect-${status}`,
    },
    status,
  )
}

function renderConnectPage(
  requestId: string,
  responseForCall: (call: number) => Response | Promise<Response> = () =>
    ticketResponse(),
) {
  let calls = 0
  const fetchMock = vi.fn<typeof fetch>(async () => {
    calls += 1
    return responseForCall(calls)
  })
  vi.stubGlobal("fetch", fetchMock)
  const router = createMemoryRouter(
    [
      {
        path: "/evaluation-connect/:requestId",
        element: <EvaluationConnectPage />,
      },
    ],
    { initialEntries: [`/evaluation-connect/${requestId}`] },
  )

  render(
    <StrictMode>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}
      >
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  )

  return { calls: () => calls, fetchMock, router }
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it("creates one ticket and form-posts ticket plus state outside the URL", async () => {
  const submit = vi
    .spyOn(HTMLFormElement.prototype, "submit")
    .mockImplementation(() => undefined)
  const result = renderConnectPage(firstRequestId)

  await waitFor(() => expect(result.calls()).toBe(1))
  const form = await screen.findByTestId("evaluation-callback-form")
  expect(form).toHaveAttribute(
    "action",
    "http://localhost:8010/auth/erp/callback",
  )
  expect(form).toHaveAttribute("method", "post")
  expect(within(form).getByDisplayValue("ticket-value")).toHaveAttribute(
    "name",
    "ticket",
  )
  expect(within(form).getByDisplayValue("state-value")).toHaveAttribute(
    "name",
    "state",
  )
  expect(window.location.search).not.toContain("ticket-value")
  expect(submit).toHaveBeenCalledTimes(1)
  expect(result.calls()).toBe(1)
})

it("rejects an invalid request id without calling the API", async () => {
  const result = renderConnectPage("not-a-uuid")

  expect(await screen.findByRole("alert")).toHaveTextContent("连接地址无效")
  expect(result.fetchMock).not.toHaveBeenCalled()
})

it.each([404, 410, 409])(
  "shows the safe expired message for a %s bridge response",
  async (status) => {
    const result = renderConnectPage(firstRequestId, () =>
      problemResponse(status, `sensitive-ticket-${status}`),
    )

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "连接已失效，请返回评价页面重试",
    )
    expect(screen.queryByText(`sensitive-ticket-${status}`)).not.toBeInTheDocument()
    expect(result.calls()).toBe(1)
  },
)

it("retry issues exactly one new ticket mutation", async () => {
  const user = userEvent.setup()
  const result = renderConnectPage(firstRequestId, () => problemResponse(410))

  await user.click(await screen.findByRole("button", { name: "重试" }))

  await waitFor(() => expect(result.calls()).toBe(2))
  expect(screen.getByRole("alert")).toBeVisible()
})

it("removes old ticket fields when a new request ends in an error", async () => {
  vi.spyOn(HTMLFormElement.prototype, "submit").mockImplementation(
    () => undefined,
  )
  const result = renderConnectPage(firstRequestId, (call) =>
    call === 1
      ? ticketResponse("old-ticket-secret", "old-state-secret")
      : problemResponse(410),
  )
  expect(
    await screen.findByDisplayValue("old-ticket-secret"),
  ).toBeInTheDocument()

  await act(async () => {
    await result.router.navigate(`/evaluation-connect/${secondRequestId}`)
  })

  expect(await screen.findByRole("alert")).toHaveTextContent(
    "连接已失效，请返回评价页面重试",
  )
  expect(screen.queryByDisplayValue("old-ticket-secret")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("old-state-secret")).not.toBeInTheDocument()
  expect(screen.queryByTestId("evaluation-callback-form")).not.toBeInTheDocument()
  expect(result.calls()).toBe(2)
})
