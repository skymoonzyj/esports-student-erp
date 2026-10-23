/// <reference types="node" />

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { cwd } from "node:process"

import { expect, it } from "vitest"

function teachingReportStyle(): HTMLStyleElement {
  const style = document.createElement("style")
  style.textContent = readFileSync(
    resolve(cwd(), "src/evaluations/teaching-reports.css"),
    "utf8",
  )
  document.head.append(style)
  return style
}

function teachingReportCss(): string {
  return readFileSync(
    resolve(cwd(), "src/evaluations/teaching-reports.css"),
    "utf8",
  )
}

function matchingStyleRule(
  rules: readonly CSSRule[],
  selector: string,
): CSSStyleRule | undefined {
  return rules
    .filter((rule) => rule.type === CSSRule.STYLE_RULE)
    .map((rule) => rule as CSSStyleRule)
    .find((rule) =>
      rule.selectorText
        .split(",")
        .map((candidate) => candidate.trim())
        .includes(selector),
    )
}

it("stacks author, review, and version-comparison grids at the inclusive 768px boundary", () => {
  const style = teachingReportStyle()
  try {
    const media = Array.from(style.sheet?.cssRules ?? [])
      .filter((rule) => rule.type === CSSRule.MEDIA_RULE)
      .map((rule) => rule as CSSMediaRule)
      .find((rule) => rule.media.mediaText === "(max-width: 768px)")
    expect(media).toBeDefined()

    const rules = Array.from(media?.cssRules ?? [])
    for (const selector of [
      ".teachingReportsAuthorPage .teachingReportWorkspace",
      ".teachingReportReviewPage .teachingReportWorkspace",
      ".teachingReportVersionComparison",
    ]) {
      expect(
        matchingStyleRule(rules, selector)?.style.getPropertyValue(
          "grid-template-columns",
        ),
      ).toBe("1fr")
    }
  } finally {
    style.remove()
  }
})

it("provides mobile safe-area padding and 44px interactive controls", () => {
  const style = teachingReportStyle()
  try {
    const rules = Array.from(style.sheet?.cssRules ?? [])
    expect(teachingReportCss()).toMatch(
      /\.teachingReportPage\s*\{[^}]*padding-bottom:\s*calc\([^;]*env\(safe-area-inset-bottom\)[^;]*\)/s,
    )

    for (const selector of [
      ".teachingReportPage button",
      ".teachingReportPage input",
      ".teachingReportPage select",
      ".teachingReportPage textarea",
      ".teachingReportEditorBody textarea",
      ".teachingReportReviewForm textarea",
      ".teachingReportReviewForm fieldset label",
      ".publishedReportsPanel button",
    ]) {
      expect(
        matchingStyleRule(rules, selector)?.style.getPropertyValue("min-height"),
      ).toBe("44px")
    }
  } finally {
    style.remove()
  }
})
