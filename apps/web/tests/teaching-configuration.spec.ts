import { expect, test } from "@playwright/test"
import type { Page } from "@playwright/test"

import {
  expectNoHorizontalOverflow,
  loginAsAdmin,
  loginAsCoach,
  openNavigationItem,
} from "./helpers"

async function createProject(page: Page, name: string, sortOrder: number): Promise<void> {
  await page.getByRole("button", { name: "新增项目" }).click()
  const dialog = page.getByRole("dialog", { name: "新增项目" })
  await dialog.getByLabel("项目名称").fill(name)
  await dialog.getByLabel("排序").fill(String(sortOrder))
  await dialog.getByRole("button", { name: "保存项目" }).click()
  await expect(page.getByRole("cell", { name: `项目 ${name}`, exact: true })).toBeVisible()
}

async function createTeam(page: Page, projectName: string, teamName: string): Promise<void> {
  await page.getByRole("button", { name: "新增队伍" }).click()
  const dialog = page.getByRole("dialog", { name: "新增队伍" })
  await dialog.getByLabel("所属项目").selectOption({ label: projectName })
  await dialog.getByLabel("队伍名称").fill(teamName)
  await dialog.getByLabel("备注").fill("四视口教学配置验证")
  await dialog.getByRole("button", { name: "保存队伍" }).click()
  await expect(page.getByRole("cell", { name: `队伍 ${teamName}`, exact: true })).toBeVisible()
}

async function createCohort(
  page: Page,
  name: string,
  options: { enableAutomation: boolean },
): Promise<string> {
  await page.goto("/teaching/cohorts")
  await page.getByRole("button", { name: "新增营期" }).click()
  const dialog = page.getByRole("dialog", { name: "新增营期" })
  await dialog.getByLabel("营期名称").fill(name)
  await dialog.getByLabel("开营日期").fill("2030-08-01")
  await dialog.getByLabel("结营日期").fill("2030-08-07")

  if (options.enableAutomation) {
    await expect(dialog.getByLabel("开营日期")).toHaveValue("2030-08-01")
    await expect(dialog.getByLabel("结营日期")).toHaveValue("2030-08-07")
    await dialog.getByLabel("自动开营").check()
    await dialog.getByLabel("自动结营").check()
  }

  await dialog.getByRole("button", { name: "保存营期" }).click()
  await expect(page.getByRole("heading", { name })).toBeVisible()
  return page.url()
}

async function createClass(
  page: Page,
  input: {
    name: string
    projectName: string
    type: "standard" | "elite"
    teamName?: string
    excludedTeamName?: string
  },
): Promise<void> {
  await page.getByRole("button", { name: "新增班级" }).click()
  const dialog = page.getByRole("dialog", { name: "新增班级" })
  if (input.type === "elite") {
    const eliteRadio = dialog.getByRole("radio", { name: "菁英班" })
    await dialog.getByText("菁英班", { exact: true }).click()
    await expect(eliteRadio).toBeChecked()
  } else {
    await expect(dialog.getByRole("radio", { name: "普通班" })).toBeChecked()
  }
  await dialog.getByLabel("电竞项目").selectOption({ label: input.projectName })
  if (input.type === "elite" && input.teamName) {
    const teamSelect = dialog.getByLabel("职业队伍")
    await expect(
      teamSelect.locator("option").filter({ hasText: input.teamName }),
    ).toHaveCount(1)
    if (input.excludedTeamName) {
      await expect(
        teamSelect.locator("option").filter({ hasText: input.excludedTeamName }),
      ).toHaveCount(0)
    }
    await teamSelect.selectOption({ label: input.teamName })
  }
  await dialog.getByLabel("班级名称").fill(input.name)
  await dialog.getByLabel("人数上限").fill(input.type === "elite" ? "10" : "20")
  await dialog.getByLabel("默认学费（元）").fill(
    input.type === "elite" ? "36800.05" : "12800.00",
  )
  await dialog.getByRole("button", { name: "保存班级" }).click()
  await expect(page.getByText(input.name, { exact: true })).toBeVisible()
}

async function assignPrimaryCoach(page: Page, className: string): Promise<void> {
  await page.getByLabel("选择班级").selectOption({ label: className })
  await page.getByRole("button", { name: "分配教练" }).click()
  const dialog = page.getByRole("dialog", { name: "分配教练" })
  await dialog.getByLabel("查找教练").fill("E2E教学教练")
  await dialog.getByRole("button", { name: "查询教练" }).click()
  const coach = dialog.getByRole("radio", { name: "E2E教学教练" })
  await expect(coach).toBeVisible()
  await dialog.getByText("E2E教学教练", { exact: true }).click()
  await expect(coach).toBeChecked()
  await expect(dialog.getByRole("radio", { name: "主教练" })).toBeChecked()
  await dialog.getByLabel("生效日期").fill("2026-07-01")
  await dialog.getByRole("button", { name: "确认分配" }).click()
  await expect(dialog).toHaveCount(0)
  await expect(page.getByText("E2E教学教练", { exact: true })).toBeVisible()
}

test("admin configures teaching and the assigned coach receives a scoped workspace", async ({
  page,
}, testInfo) => {
  const suffix = testInfo.project.name
  const projectName = `E2E项目-${suffix}`
  const teamName = `E2E职业队-${suffix}`
  const excludedTeamName = `E2E其他项目队-${suffix}`
  const cohortName = `E2E营期-${suffix}`
  const controlCohortName = `E2E未分配营期-${suffix}`
  const standardClassName = `E2E普通班-${suffix}`
  const eliteClassName = `E2E菁英班-${suffix}`

  await loginAsAdmin(page)
  await page.goto("/teaching/catalog")
  await createProject(page, projectName, 1_000 + (page.viewportSize()?.width ?? 0))

  await page.getByRole("tab", { name: "职业队伍" }).click()
  await createTeam(page, projectName, teamName)
  await createTeam(page, "英雄联盟", excludedTeamName)

  const cohortUrl = await createCohort(page, cohortName, { enableAutomation: true })
  const schedule = page.getByRole("region", { name: "营期安排" })
  await expect(schedule.getByText("2030-08-01", { exact: true })).toHaveCount(2)
  await expect(schedule.getByText("2030-08-07", { exact: true })).toHaveCount(2)

  await createCohort(page, controlCohortName, { enableAutomation: false })
  await page.goto(cohortUrl)
  await page.getByRole("tab", { name: "班级" }).click()
  await createClass(page, {
    name: standardClassName,
    projectName,
    type: "standard",
  })
  await createClass(page, {
    name: eliteClassName,
    projectName,
    type: "elite",
    teamName,
    excludedTeamName,
  })

  await page.getByRole("tab", { name: "教练" }).click()
  await assignPrimaryCoach(page, standardClassName)
  await assignPrimaryCoach(page, eliteClassName)

  await page.getByRole("button", { name: "开放报名" }).click()
  await expect(page.getByRole("button", { name: "标记即将开营" })).toBeVisible()
  await page.getByRole("tab", { name: "概览" }).click()
  await expect(page.getByText("管理员操作", { exact: true })).toBeVisible()
  await expectNoHorizontalOverflow(page)

  await page.getByRole("button", { name: "退出登录" }).click()
  await loginAsCoach(page)
  await openNavigationItem(page, "招生教学")
  await expect(page).toHaveURL(/\/teaching\/cohorts$/)
  await expect(page.getByRole("link", { name: cohortName, exact: true })).toBeVisible()
  await expect(page.getByText(controlCohortName, { exact: true })).toHaveCount(0)
  await expect(page.getByRole("button", { name: "新增营期" })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)

  await page.getByRole("link", { name: cohortName, exact: true }).click()
  await expect(page.getByRole("heading", { name: cohortName })).toBeVisible()
  await expect(page.getByRole("button", { name: "标记即将开营" })).toHaveCount(0)
  await page.getByRole("tab", { name: "班级" }).click()
  await expect(page.getByText(standardClassName, { exact: true })).toBeVisible()
  await expect(page.getByText(eliteClassName, { exact: true })).toBeVisible()
  await expect(page.getByText("2 个可见班级", { exact: true })).toBeVisible()
  await expect(page.getByRole("button", { name: "新增班级" })).toHaveCount(0)
  await page.getByRole("tab", { name: "教练" }).click()
  await expect(page.getByRole("button", { name: "分配教练" })).toHaveCount(0)
  await expectNoHorizontalOverflow(page)

  await page.goto("/teaching/catalog")
  await expect(page).toHaveURL(/\/$/)
  await expect(page.getByRole("heading", { name: "工作台", exact: true })).toBeVisible()
})
