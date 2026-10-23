import { QueryClient } from "@tanstack/react-query"
import { expect, it } from "vitest"
import { invitationKeys } from "./keys"
it("removes only one account's invitation tree without colliding with sources, filters or financial queries", () => {
  const client = new QueryClient()
  const filters = { sourceKind: "organization" as const, organizationId: "org-a", status: "checked_in" as const, page: 2, pageSize: 20 }
  client.setQueryData(invitationKeys.housing("a", filters, 1), "a")
  client.setQueryData(invitationKeys.sources("a"), "sources")
  client.setQueryData(invitationKeys.housing("b", filters, 1), "b")
  client.setQueryData(["portal", "a", "payments"], "payments")
  expect(invitationKeys.housing("a", filters, 1)).not.toEqual(invitationKeys.housing("a", { ...filters, page: 1 }, 1))
  expect(invitationKeys.housing("a", filters, 1)).not.toEqual(invitationKeys.housing("a", { ...filters, organizationId: "org-b" }, 1))
  client.removeQueries({ queryKey: invitationKeys.root("a") })
  expect(client.getQueryData(invitationKeys.housing("a", filters, 1))).toBeUndefined()
  expect(client.getQueryData(invitationKeys.sources("a"))).toBeUndefined()
  expect(client.getQueryData(invitationKeys.housing("b", filters, 1))).toBe("b")
  expect(client.getQueryData(["portal", "a", "payments"])).toBe("payments")
})
