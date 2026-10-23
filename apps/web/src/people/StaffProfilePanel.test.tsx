import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { PersonDetailPage } from "./PersonDetailPage"

afterEach(() => vi.unstubAllGlobals())

const personId = "30000000-0000-4000-8000-000000000001"
const person = { id: personId, full_name: "测试人员", nickname: "旧昵称", person_types: ["staff"], assisted_kind: null, account_status: "active", identity_number: "110105********002X", phone: null, email: null, sex: "female", date_of_birth: "1990-01-02", staff_type: "other", created_at: "2026-07-26T10:00:00+08:00", age_status: "adult", guardian_status: "not_applicable", student_number: null, student_status: null, visit_reason: null, linked_student_id: null }

function renderDetail(permissions: string[], scopedPermissions: string[] = []) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return { client, ...render(<MemoryRouter initialEntries={[`/people/${personId}`]}><QueryClientProvider client={client}><Routes><Route path="/people/:personId" element={<PersonDetailPage permissions={new Set(permissions)} scopedPermissions={new Set(scopedPermissions)} />} /></Routes></QueryClientProvider></MemoryRouter>) }
}

it("lets a global personnel editor update nickname and internal notes using only dirty fields", async () => {
  let nickname = "旧昵称"
  let notes: string | null = "仅后台可见的备注"
  const writes: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === "PATCH") {
      const body = JSON.parse(String(init.body))
      writes.push(body)
      if ("nickname" in body) nickname = body.nickname
      if ("profile_notes" in body) notes = body.profile_notes
    }
    return new Response(JSON.stringify(String(input).endsWith("staff-profile") ? { person_id: personId, nickname, profile_notes: notes } : { ...person, nickname }), { headers: { "Content-Type": "application/json" } })
  }))
  renderDetail(["people.read", "people.update"])
  expect(await screen.findByText("仅后台可见的备注")).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "编辑昵称与内部备注" }))
  fireEvent.change(screen.getByRole("textbox", { name: "内部备注" }), { target: { value: "后续跟进" } })
  await userEvent.click(screen.getByRole("button", { name: "保存昵称与备注" }))
  await waitFor(() => expect(writes).toEqual([{ profile_notes: "后续跟进" }]))
  expect(await screen.findByText("后续跟进")).toBeVisible()
  await userEvent.click(screen.getByRole("button", { name: "编辑昵称与内部备注" }))
  fireEvent.change(screen.getByLabelText("昵称"), { target: { value: "新昵称" } })
  fireEvent.change(screen.getByRole("textbox", { name: "内部备注" }), { target: { value: "" } })
  await userEvent.click(screen.getByRole("button", { name: "保存昵称与备注" }))
  await waitFor(() => expect(writes[1]).toEqual({ nickname: "新昵称", profile_notes: null }))
  expect(await screen.findByText("暂无内部备注")).toBeVisible()
})

it("does not fetch or render internal notes for read-only or scoped personnel editors", async () => {
  const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(person), { headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetcher)
  renderDetail(["people.read"], ["people.update"])
  expect(await screen.findByRole("heading", { name: "测试人员" })).toBeVisible()
  expect(screen.queryByText("内部备注")).not.toBeInTheDocument()
  expect(fetcher.mock.calls.map(([url]) => String(url))).toEqual([`/api/v1/people/${personId}`])
})

it("hides stale internal notes and the editor after access is denied on save", async () => {
  let forbidden = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    const staff = String(input).endsWith("staff-profile")
    if (init?.method === "PATCH") forbidden = true
    return new Response(JSON.stringify(forbidden && (staff || init?.method === "PATCH") ? { detail: "无权访问", status: 403 } : staff ? { person_id: personId, nickname: "旧昵称", profile_notes: "已撤销权限的备注" } : person), { status: forbidden && (staff || init?.method === "PATCH") ? 403 : 200, headers: { "Content-Type": "application/json" } })
  }))
  renderDetail(["people.read", "people.update"])
  await screen.findByText("已撤销权限的备注")
  await userEvent.click(screen.getByRole("button", { name: "编辑昵称与内部备注" }))
  fireEvent.change(screen.getByRole("textbox", { name: "内部备注" }), { target: { value: "未保存草稿" } })
  await userEvent.click(screen.getByRole("button", { name: "保存昵称与备注" }))
  expect(await screen.findByText("无权查看内部备注")).toBeVisible()
  expect(screen.queryByRole("textbox", { name: "内部备注" })).not.toBeInTheDocument()
  expect(screen.queryByText("已撤销权限的备注")).not.toBeInTheDocument()
})

it("preserves an open draft across background refresh without resubmitting untouched nickname", async () => {
  let nickname = "旧昵称"
  let notes = "旧备注"
  const writes: unknown[] = []
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input, init) => {
    if (init?.method === "PATCH") writes.push(JSON.parse(String(init.body)))
    return new Response(JSON.stringify(String(input).endsWith("staff-profile") ? { person_id: personId, nickname, profile_notes: notes } : { ...person, nickname }), { headers: { "Content-Type": "application/json" } })
  }))
  const { client } = renderDetail(["people.read", "people.update"])
  await screen.findByText("旧备注")
  await userEvent.click(screen.getByRole("button", { name: "编辑昵称与内部备注" }))
  fireEvent.change(screen.getByRole("textbox", { name: "内部备注" }), { target: { value: "我的备注草稿" } })
  nickname = "他人更新昵称"
  notes = "他人备注"
  await client.refetchQueries({ queryKey: ["staff-person-profile", personId] })
  expect(screen.getByRole("textbox", { name: "内部备注" })).toHaveValue("我的备注草稿")
  await userEvent.click(screen.getByRole("button", { name: "保存昵称与备注" }))
  await waitFor(() => expect(writes).toEqual([{ profile_notes: "我的备注草稿" }]))
})

it("removes private note text and an open draft when background refresh revokes access", async () => {
  let revoked = false
  vi.stubGlobal("fetch", vi.fn<typeof fetch>(async (input) => {
    const staff = String(input).endsWith("staff-profile")
    return new Response(JSON.stringify(staff && revoked ? { status: 403, detail: "权限已撤销" } : staff ? { person_id: personId, nickname: "旧昵称", profile_notes: "权限撤销前的内部备注" } : person), { status: staff && revoked ? 403 : 200, headers: { "Content-Type": "application/json" } })
  }))
  const { client } = renderDetail(["people.read", "people.update"])
  await screen.findByText("权限撤销前的内部备注")
  await userEvent.click(screen.getByRole("button", { name: "编辑昵称与内部备注" }))
  fireEvent.change(screen.getByRole("textbox", { name: "内部备注" }), { target: { value: "权限撤销前的草稿" } })
  revoked = true
  await act(async () => { await client.refetchQueries({ queryKey: ["staff-person-profile", personId] }) })
  expect(await screen.findByText("无权查看内部备注")).toBeVisible()
  expect(screen.queryByText("权限撤销前的内部备注")).not.toBeInTheDocument()
  expect(screen.queryByDisplayValue("权限撤销前的草稿")).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "编辑昵称与内部备注" })).not.toBeInTheDocument()
  expect(screen.queryByRole("button", { name: "保存昵称与备注" })).not.toBeInTheDocument()
})
