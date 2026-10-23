import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { renderHook, waitFor } from "@testing-library/react"
import type { PropsWithChildren } from "react"
import { afterEach, expect, it, vi } from "vitest"

import { reportKeys } from "./reportKeys"
import { useTeachingReportContext } from "./useTeachingReportContext"

const accountId = "10000000-0000-4000-8000-000000000095"

vi.mock("../auth/AuthProvider", () => ({
  useAuth: () => ({ user: { account_id: accountId } }),
}))

afterEach(() => vi.unstubAllGlobals())

it("uses a live account-scoped cancellable report context query", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(
      JSON.stringify({
        author_available: true,
        reviewer_available: false,
        family_available: false,
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    ),
  )
  vi.stubGlobal("fetch", fetchMock)
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: PropsWithChildren) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )

  const { result } = renderHook(() => useTeachingReportContext(), { wrapper })

  await waitFor(() => expect(result.current.isSuccess).toBe(true))
  expect(fetchMock).toHaveBeenCalledTimes(1)
  expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
    "/api/v1/evaluations/reports/context",
  )
  expect(fetchMock.mock.calls[0]?.[1]?.signal).toBeInstanceOf(AbortSignal)

  const query = queryClient.getQueryCache().find({
    queryKey: reportKeys.context(accountId),
  })
  expect(query).toBeDefined()
  if (!query) throw new Error("report context query was not registered")
  const options = query.options as unknown as Record<string, unknown>
  expect(options.staleTime).toBe(0)
  expect(options.gcTime).toBe(0)
  expect(options.refetchOnMount).toBe("always")
  expect(options.refetchOnWindowFocus).toBe(true)
  expect(options.retry).toBe(false)
})
