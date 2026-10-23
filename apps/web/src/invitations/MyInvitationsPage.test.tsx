import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { render, screen, waitFor } from "@testing-library/react"
import { afterEach, expect, it, vi } from "vitest"
import { AuthProvider } from "../auth/AuthProvider"
import { MyInvitationsPage } from "./MyInvitationsPage"
afterEach(() => vi.unstubAllGlobals())
it("does not render an invitation page without an authenticated account", async () => {
  const client = new QueryClient()
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 401 })))
  render(<QueryClientProvider client={client}><AuthProvider><MyInvitationsPage /></AuthProvider></QueryClientProvider>)
  await waitFor(() => expect(client.getQueryCache().getAll()).toHaveLength(0))
  expect(screen.queryByRole("heading", { name: "我的邀请" })).not.toBeInTheDocument()
})
