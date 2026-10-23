import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import type { EvaluationCenterContext } from "./api"
import { SystemEvaluationPage } from "./SystemEvaluationPage"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      account_id: "10000000-0000-4000-8000-000000000095",
    },
  }),
}))

const context: EvaluationCenterContext = {
  accessible: true,
  system_evaluation_available: true,
  embedded_start_url:
    "/evaluation-tool/auth/erp/start?mode=embedded&return_path=%2F",
  standalone_start_url:
    "http://localhost:8010/auth/erp/start?mode=standalone&return_path=%2F",
  upload_available: false,
  video_available: false,
  reports_available: false,
  surveys_available: false,
  integration_api_version: "v1",
}

function renderSystemEvaluation() {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    new Response(JSON.stringify(context), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)
  render(
    <MemoryRouter initialEntries={["/evaluations/system"]}>
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <Routes>
          <Route path="/" element={<p>工作台首页</p>} />
          <Route
            path="/evaluations/system"
            element={<SystemEvaluationPage />}
          />
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>,
  )
  return { fetchMock }
}

function readyMessage(
  iframe: HTMLIFrameElement,
  overrides: {
    origin?: string
    source?: MessageEventSource | null
    data?: unknown
  } = {},
): MessageEvent {
  return new MessageEvent("message", {
    origin: overrides.origin ?? window.location.origin,
    source: overrides.source ?? iframe.contentWindow,
    data:
      overrides.data ??
      ({ type: "game-score-tool.ready", api_version: "v1" } as const),
  })
}

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it("accepts ready only from the configured iframe window and current origin", async () => {
  renderSystemEvaluation()
  const iframe = await screen.findByTitle<HTMLIFrameElement>("系统评价")
  expect(screen.getByText("正在连接系统评价")).toBeVisible()

  fireEvent.load(iframe)
  expect(screen.getByText("正在连接系统评价")).toBeVisible()

  act(() => {
    window.dispatchEvent(
      readyMessage(iframe, { origin: "https://attacker.example" }),
    )
  })
  expect(screen.getByText("正在连接系统评价")).toBeVisible()

  act(() => window.dispatchEvent(readyMessage(iframe)))
  expect(
    screen.queryByText("正在连接系统评价"),
  ).not.toBeInTheDocument()
})

it("ignores a same-origin ready message from another window", async () => {
  renderSystemEvaluation()
  const iframe = await screen.findByTitle<HTMLIFrameElement>("系统评价")

  act(() => {
    window.dispatchEvent(readyMessage(iframe, { source: window }))
  })

  expect(screen.getByText("正在连接系统评价")).toBeVisible()
})

it.each([
  { type: "another.ready", api_version: "v1" },
  { type: "game-score-tool.ready", api_version: "v2" },
])("ignores an incompatible ready message %#", async (data) => {
  renderSystemEvaluation()
  const iframe = await screen.findByTitle<HTMLIFrameElement>("系统评价")

  act(() => window.dispatchEvent(readyMessage(iframe, { data })))

  expect(screen.getByText("正在连接系统评价")).toBeVisible()
})

it("shows a timeout and retry creates a new iframe instance", async () => {
  vi.useFakeTimers()
  renderSystemEvaluation()
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0)
  })
  const firstIframe = screen.getByTitle<HTMLIFrameElement>("系统评价")

  await act(async () => {
    await vi.advanceTimersByTimeAsync(10_000)
  })
  expect(screen.getByRole("alert")).toHaveTextContent(
    "系统评价暂时无法连接",
  )

  fireEvent.click(screen.getByRole("button", { name: "重试" }))
  expect(screen.getByTitle("系统评价")).not.toBe(firstIframe)
  expect(screen.getByText("正在连接系统评价")).toBeVisible()
})

it("uses the API standalone URL and renders no upload controls", async () => {
  renderSystemEvaluation()

  const standalone = await screen.findByRole("link", { name: "单独打开" })
  expect(standalone).toHaveAttribute("href", context.standalone_start_url)
  expect(standalone).toHaveAttribute("target", "_blank")
  expect(standalone).toHaveAttribute("rel", "noopener noreferrer")
  expect(document.querySelector('input[type="file"]')).not.toBeInTheDocument()
  expect(document.querySelector('input[accept*="video"]')).not.toBeInTheDocument()
})
