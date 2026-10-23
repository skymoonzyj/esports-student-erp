/// <reference types="node" />

import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { cwd } from "node:process"

import { expect, it } from "vitest"

it("routes the system evaluation context through the shared hook", () => {
  const source = readFileSync(
    resolve(cwd(), "src/evaluations/SystemEvaluationPage.tsx"),
    "utf8",
  )

  expect(source).toContain("useEvaluationContext(")
  expect(source).not.toMatch(/\buseQuery\s*\(/)
  expect(source).not.toContain("getEvaluationContext")
})

it("stacks the student directory and detail panels at the 768px acceptance viewport", () => {
  const style = document.createElement("style")
  style.textContent = readFileSync(
    resolve(cwd(), "src/evaluations/evaluation-foundation.css"),
    "utf8",
  )
  document.head.append(style)

  try {
    const mediaRules = Array.from(style.sheet?.cssRules ?? []).filter(
      (rule) => rule.type === CSSRule.MEDIA_RULE,
    ) as CSSMediaRule[]
    const stackedMediaRule = mediaRules.find((mediaRule) => {
      const styleRules = Array.from(mediaRule.cssRules).filter(
        (rule) => rule.type === CSSRule.STYLE_RULE,
      ) as CSSStyleRule[]
      return styleRules.some(
        (rule) =>
          rule.selectorText.split(",").map((selector) => selector.trim()).includes(
            ".studentMatchingPanel",
          ) &&
          rule.style.getPropertyValue("grid-template-columns") === "1fr",
      )
    })

    expect(stackedMediaRule).toBeDefined()
    expect(stackedMediaRule?.media.mediaText).toBe("(max-width: 768px)")
  } finally {
    style.remove()
  }
})
