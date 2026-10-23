import { expect, it } from "vitest"

import {
  canPrintSurvey,
  canPrintSurveyIdentity,
  canReadSurveyIdentity,
} from "./surveyIdentityCapability"

it("allows identity-only access for the admin real-name projection", () => {
  expect(canReadSurveyIdentity({ permissions: ["camp_surveys.identity.read"] })).toBe(true)
  expect(canReadSurveyIdentity({ permissions: ["camp_surveys.print"] })).toBe(false)
  expect(canReadSurveyIdentity({
    permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
  })).toBe(true)
})

it("accepts the pair when permissions are scoped grants", () => {
  expect(canReadSurveyIdentity({
    scoped_permissions: ["camp_surveys.print", "camp_surveys.identity.read"],
  })).toBe(true)
})

it("keeps print access separate from real-name print access", () => {
  expect(canPrintSurvey({ permissions: ["camp_surveys.print"] })).toBe(true)
  expect(canPrintSurveyIdentity({ permissions: ["camp_surveys.print"] })).toBe(false)
  expect(canPrintSurveyIdentity({ permissions: ["camp_surveys.print", "camp_surveys.identity.read"] })).toBe(true)
})
