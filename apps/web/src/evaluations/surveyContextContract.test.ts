import { expect, it } from "vitest"

import type { EvaluationFoundationContext } from "./foundationApi"

it("keeps camp-survey availability as a server-controlled boolean", () => {
  const context: EvaluationFoundationContext = {
    accessible: true,
    system_evaluation_available: true,
    embedded_start_url: "/embedded",
    standalone_start_url: "/standalone",
    upload_available: false,
    video_available: false,
    reports_available: false,
    surveys_available: true,
    survey_sections: {
      respondent: true,
      staff_feedback: false,
      administration: false,
    },
    game_accounts_available: false,
    admin_available: false,
    admin_sections: {
      cohort_staff: false,
      student_matching: false,
      analysis_profiles: false,
      system_tasks: false,
      adapter_certifications: false,
    },
    integration_api_version: "v1",
  }

  expect(context.surveys_available).toBe(true)
  expect(context.survey_sections).toEqual({
    respondent: true,
    staff_feedback: false,
    administration: false,
  })
})
