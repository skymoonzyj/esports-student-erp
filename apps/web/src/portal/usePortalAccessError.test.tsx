import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { act, render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter } from "react-router-dom"
import { afterEach, expect, it, vi } from "vitest"

import { AuthProvider } from "../auth/AuthProvider"
import { portalKeys } from "./api"
import { usePortalAccessError } from "./usePortalAccessError"
import { usePortalContext } from "./usePortalContext"

afterEach(() => vi.unstubAllGlobals())

function PortalDenialProbe() {
  const context = usePortalContext()
  usePortalAccessError(context.error)
  return <>
    <p>{context.isSuccess ? "authorized" : context.isError ? "denied" : "pending"}</p>
    <button type="button" onClick={() => void context.refetch()}>重试门户</button>
  </>
}

it("handles a denial once, keeps other accounts private data, and resets after a successful response", async () => {
  const accountId = "account-portal-denial"
  const otherAccountId = "account-other"
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const currentPrivateKey = portalKeys.students(accountId)
  const otherPrivateKey = portalKeys.students(otherAccountId)
  client.setQueryData(currentPrivateKey, { private_name: "must clear" })
  client.setQueryData(otherPrivateKey, { private_name: "must keep" })
  let contextCalls = 0
  const fetchMock = vi.fn<typeof fetch>(async (input) => {
    const url = String(input)
    if (url.endsWith("/auth/me")) return new Response(JSON.stringify({ account_id: accountId, person_id: "person-portal-denial", full_name: "门户测试", email: null, force_password_change: false, permissions: [] }), { headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/auth/csrf")) return new Response(JSON.stringify({ csrf_token: "csrf" }), { headers: { "Content-Type": "application/json" } })
    if (url.endsWith("/portal/context")) {
      contextCalls += 1
      if (contextCalls === 3) return new Response(JSON.stringify({ coach_available: false, learning_available: false, payments_available: false, housing_management_available: false, housing_available: false, adult_guardian_invitations_available: false, food_profile_available: false, food_preferences_onboarding_required: false, learning_mode: null }), { headers: { "Content-Type": "application/json" } })
      return new Response(JSON.stringify({ type: "about:blank", title: "Forbidden", status: 403, detail: "门户权限已撤销", code: "forbidden", request_id: `denial-${contextCalls}` }), { status: 403, headers: { "Content-Type": "application/json" } })
    }
    throw new Error(`Unexpected request: ${url}`)
  })
  vi.stubGlobal("fetch", fetchMock)
  render(<MemoryRouter><QueryClientProvider client={client}><AuthProvider><PortalDenialProbe /></AuthProvider></QueryClientProvider></MemoryRouter>)

  expect(await screen.findByText("denied")).toBeVisible()
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) })
  expect(contextCalls).toBe(2)
  expect(client.getQueryData(currentPrivateKey)).toBeUndefined()
  expect(client.getQueryData(otherPrivateKey)).toEqual({ private_name: "must keep" })

  await userEvent.setup().click(screen.getByRole("button", { name: "重试门户" }))
  expect(await screen.findByText("authorized")).toBeVisible()
  expect(contextCalls).toBe(3)
  client.setQueryData(currentPrivateKey, { private_name: "must clear again" })

  await userEvent.setup().click(screen.getByRole("button", { name: "重试门户" }))
  expect(await screen.findByText("denied")).toBeVisible()
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 100)) })
  expect(contextCalls).toBe(5)
  expect(client.getQueryData(currentPrivateKey)).toBeUndefined()
  expect(client.getQueryData(otherPrivateKey)).toEqual({ private_name: "must keep" })
})
