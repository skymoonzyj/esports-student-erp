import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { Component, type ReactNode } from "react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PersonDetailPage } from "./PersonDetailPage"

const chunk = vi.hoisted(() => ({
  load: vi.fn(),
  reject: undefined as undefined | ((error: Error) => void),
}))
const reactionxChunk = vi.hoisted(() => ({ load: vi.fn(), reject: undefined as undefined | ((error: Error) => void) }))
vi.mock("../reactionx/ReactionxReportsPanel", () => {
  reactionxChunk.load()
  return new Promise((_resolve, reject) => { reactionxChunk.reject = reject })
})

vi.mock("../esportsAssessments/AssessmentReportsPanel", () => {
  chunk.load()
  return new Promise((_resolve, reject) => { chunk.reject = reject })
})

class RouteCrashProbe extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  render() { return this.state.failed ? <p>整个档案已崩溃</p> : this.props.children }
}

afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

function renderProfile(reportPermission: boolean | 'reactionx') {
  const fetcher = vi.fn<typeof fetch>(async () => Response.json({
    id: "chunk-person", full_name: "合成档案", nickname: null,
    person_types: [], identity_number: null, phone: null, email: null,
    sex: null, date_of_birth: null, created_at: "2026-10-25T00:00:00Z",
    age_status: "unknown", guardian_status: "not_applicable",
    student_number: null, student_status: null, staff_type: null,
    visit_reason: null, linked_student_id: null,
    team_allocation: { visibility: "allowed", status: "pending", version: 1, can_update: true },
  }))
  vi.stubGlobal("fetch", fetcher)
  render(<MemoryRouter initialEntries={["/people/chunk-person"]}>
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RouteCrashProbe><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(reportPermission === 'reactionx' ? ["people.read", "talent_tests.read"] : reportPermission ? ["people.read", "esports_assessments.read"] : ["people.read"])} />} /></Routes></RouteCrashProbe>
    </QueryClientProvider>
  </MemoryRouter>)
  return fetcher
}

it("does not import the report chunk or request reports without report permission", async () => {
  const fetcher = renderProfile(false)
  expect(await screen.findByRole("heading", { name: "合成档案" })).toBeVisible()
  expect(chunk.load).not.toHaveBeenCalled()
  expect(reactionxChunk.load).not.toHaveBeenCalled()
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual(["/api/v1/people/chunk-person"])
})

it("isolates a ReactionX lazy chunk rejection and preserves the personnel draft", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined)
  renderProfile('reactionx')
  await userEvent.click(await screen.findByRole('button', { name: '标记无需分队' }))
  fireEvent.change(screen.getByLabelText('队伍归属变更原因'), { target: { value: '实体灯不应丢失此草稿' } })
  expect(reactionxChunk.load).toHaveBeenCalledTimes(1)
  await act(async () => reactionxChunk.reject?.(new TypeError('private-reactionx-chunk')))
  expect(await screen.findByRole('alert')).toHaveTextContent('实体灯报告暂时无法加载')
  expect(screen.getByRole('heading', { name: '合成档案' })).toBeVisible()
  expect(screen.getByLabelText('队伍归属变更原因')).toHaveValue('实体灯不应丢失此草稿')
  expect(screen.queryByText('整个档案已崩溃')).not.toBeInTheDocument()
})

it("keeps the profile and unsaved draft mounted after a genuine lazy import rejection", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined)
  renderProfile(true)
  await userEvent.click(await screen.findByRole("button", { name: "标记无需分队" }))
  fireEvent.change(screen.getByLabelText("队伍归属变更原因"), { target: { value: "未提交的分队草稿" } })
  expect(chunk.load).toHaveBeenCalledTimes(1)
  await act(async () => chunk.reject?.(new TypeError("Failed to fetch dynamically imported module: private-chunk-url")))
  expect(await screen.findByRole("alert")).toHaveTextContent("天赋评测报告暂时无法加载")
  expect(screen.getByRole("heading", { name: "合成档案" })).toBeVisible()
  expect(screen.getByLabelText("队伍归属变更原因")).toHaveValue("未提交的分队草稿")
  expect(screen.getByRole("alert")).toHaveTextContent("未保存")
  expect(screen.queryByText(/private-chunk-url/)).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: /重试/ })).not.toBeInTheDocument()
})
