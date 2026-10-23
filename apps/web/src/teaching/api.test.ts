import { afterEach, expect, it, vi } from "vitest"

import { createProject, listProfessionalTeams, listProjects, updateProject } from "./api"

afterEach(() => vi.unstubAllGlobals())

it("uses the backend include_inactive contract for catalog lists", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () =>
    new Response(JSON.stringify({ items: [], page: 1, page_size: 20, total: 0 }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  )
  vi.stubGlobal("fetch", fetchMock)

  await listProjects({ name: "英雄联盟", includeInactive: true, page: 1, pageSize: 20 })
  await listProfessionalTeams({
    projectId: "30000000-0000-4000-8000-000000000001",
    name: "职业一队",
    includeInactive: true,
    page: 2,
    pageSize: 20,
  })

  const urls = fetchMock.mock.calls.map(([input]) => new URL(String(input), "http://local.test"))
  expect(urls[0]?.pathname).toBe("/api/v1/projects")
  expect(urls[0]?.searchParams.get("include_inactive")).toBe("true")
  expect(urls[0]?.searchParams.has("is_active")).toBe(false)
  expect(urls[0]?.searchParams.get("name")).toBe("英雄联盟")
  expect(urls[1]?.pathname).toBe("/api/v1/professional-teams")
  expect(urls[1]?.searchParams.get("include_inactive")).toBe("true")
  expect(urls[1]?.searchParams.has("is_active")).toBe(false)
  expect(urls[1]?.searchParams.get("page")).toBe("2")
})

it("passes the optional division flag without changing ordinary catalog updates", async () => {
  const fetchMock = vi.fn<typeof fetch>().mockImplementation(async () => new Response(JSON.stringify({ id: "project-a", is_team_division: true }), { status: 200, headers: { "Content-Type": "application/json" } }))
  vi.stubGlobal("fetch", fetchMock)
  await createProject({ name: "王者荣耀", sort_order: 0, is_team_division: true })
  await updateProject("project-a", { name: "王者荣耀" })
  expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({ name: "王者荣耀", sort_order: 0, is_team_division: true })
  expect(JSON.parse(String(fetchMock.mock.calls[1]?.[1]?.body))).toEqual({ name: "王者荣耀" })
})
