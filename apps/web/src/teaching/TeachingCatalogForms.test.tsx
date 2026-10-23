import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { expect, it, vi } from "vitest"

import { ProjectForm } from "./TeachingCatalogForms"

const project = { id: "project-a", name: "王者荣耀", sort_order: 1, is_active: true, is_team_division: true, created_at: "2026-09-29T00:00:00+08:00", updated_at: "2026-09-29T00:00:00+08:00" }

it("preserves an existing division flag when only the project name changes", async () => {
  const user = userEvent.setup()
  const save = vi.fn()
  render(<ProjectForm state={{ mode: "edit", project }} canEditDivision={true} pending={false} error={null} onClose={vi.fn()} onSubmit={save} />)
  await user.clear(screen.getByLabelText("项目名称"))
  await user.type(screen.getByLabelText("项目名称"), "王者荣耀青训")
  await user.click(screen.getByRole("button", { name: "保存项目" }))
  expect(save).toHaveBeenCalledWith({ name: "王者荣耀青训", sort_order: 1 })
})
