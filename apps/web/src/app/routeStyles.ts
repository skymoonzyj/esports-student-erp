import { createStyleGate } from './loading/styleGate'

// Finite source-owned CSS groups, never a runtime manifest/URL registry.
// These promises guard mount even for the two leaves that overlap page import.
export const styleGates = {
  'OrderNumber': createStyleGate(() => import('../admissions/order-number.css')),
  'teaching': createStyleGate(() => import('../teaching/teaching.css')),
  'admissions': createStyleGate(() => import('../admissions/admissions.css')),
  'assets': createStyleGate(() => import('../assets/assets.css')),
  'AssistedPersonForm': createStyleGate(() => import('../assistedPeople/assisted-people.css')),
  'people': createStyleGate(() => import('../people/people.css')),
  'registration': createStyleGate(() => import('../registration/registration.css')),
  'reports': createStyleGate(() => import('../esportsAssessments/reports.css')),
  'AssessmentRankingPanel': createStyleGate(() => import('../assessmentRankings/rankings.css')),
  'camp-surveys': createStyleGate(() => import('../evaluations/camp-surveys.css')),
  'food-preferences': createStyleGate(() => import('../foodPreferences/food-preferences.css')),
  'evaluation-foundation': createStyleGate(() => import('../evaluations/evaluation-foundation.css')),
  'system-evaluation': createStyleGate(() => import('../evaluations/system-evaluation.css')),
  'evaluations': createStyleGate(() => import('../evaluations/evaluations.css')),
  'teaching-reports': createStyleGate(() => import('../evaluations/teaching-reports.css')),
  'SelfProfilePanel': createStyleGate(() => import('../people/self-profile.css')),
  'teams': createStyleGate(() => import('../teams/teams.css')),
  'housing': createStyleGate(() => import('../housing/housing.css')),
  'revenue-share': createStyleGate(() => import('../revenueShare/revenue-share.css')),
  'settings': createStyleGate(() => import('../settings/settings.css')),
  'talentTests': createStyleGate(() => import('../talentTests/talentTests.css')),
  'procurement-dialogs': createStyleGate(() => import('../procurement/procurement-dialogs.css')),
}

// Checked against actual emitted static CSS closures at build acceptance.
export const routeStyleOwners: Record<string, readonly (keyof typeof styleGates)[]> = {
  "AdmissionCasesPage": [
    "OrderNumber",
    "teaching",
    "admissions"
  ],
  "EnrollmentDetailPage": [
    "OrderNumber",
    "teaching",
    "admissions"
  ],
  "EnrollmentsPage": [
    "OrderNumber",
    "teaching",
    "admissions"
  ],
  "PaymentsPage": [
    "OrderNumber",
    "teaching",
    "admissions"
  ],
  "AssetScanPage": [
    "assets"
  ],
  "AssetsPage": [
    "assets"
  ],
  "MyAssetsPage": [
    "assets"
  ],
  "AssistedRequestsPage": [
    "AssistedPersonForm",
    "people"
  ],
  "AssistedDirectCreatePage": ["AssistedPersonForm", "people"],
  "ManagedPeoplePage": ["AssistedPersonForm"],
  "CorrectionDraft": ["AssistedPersonForm", "people"],
  "ProcurementDialogs": ["procurement-dialogs"],
  "ActivateProfilePage": [
    "registration"
  ],
  "EnrollmentCatalogPage": [
    "OrderNumber"
  ],
  "AssessmentHistoryPage": [
    "reports"
  ],
  "AssessmentReportPage": [
    "reports",
    "AssessmentRankingPanel"
  ],
  "AssessmentReportsPanel": [
    "reports"
  ],
  "AssessmentSessionPage": [
    "reports"
  ],
  "PracticePage": [
    "reports"
  ],
  "CampSurveyAdminPage": [
    "camp-surveys"
  ],
  "CampSurveyPrintPage": [
    "camp-surveys"
  ],
  "CampSurveysPage": [
    "camp-surveys",
    "food-preferences"
  ],
  "EvaluationAdminPage": [
    "evaluation-foundation",
    "system-evaluation"
  ],
  "EvaluationCenterPage": [
    "system-evaluation",
    "evaluations"
  ],
  "EvaluationConnectPage": [
    "evaluations"
  ],
  "EvaluationTaskDetail": [
    "system-evaluation"
  ],
  "EvaluationTaskList": [
    "system-evaluation"
  ],
  "GameAccountsPage": [
    "evaluation-foundation"
  ],
  "MySurveyFeedbackPage": [
    "camp-surveys"
  ],
  "SystemEvaluationPage": [
    "evaluations"
  ],
  "TeachingReportReviewPage": [
    "teaching-reports",
    "system-evaluation"
  ],
  "TeachingReportsPage": [
    "system-evaluation"
  ],
  "FoodCuisineSettingsPage": [
    "food-preferences"
  ],
  "FoodOnboardingPage": [
    "food-preferences"
  ],
  "FoodPreferenceStatisticsPage": [
    "food-preferences"
  ],
  "MyFoodProfilePage": [
    "registration",
    "food-preferences",
    "SelfProfilePanel",
    "teams"
  ],
  "HousingOverviewPage": [
    "housing"
  ],
  "HousingReportsPage": [
    "housing"
  ],
  "HousingRoomDetailPage": [
    "housing"
  ],
  "MyHousingPage": [
    "housing"
  ],
  "PeoplePage": [
    "people"
  ],
  "PersonDetailPage": [
    "people",
    "evaluation-foundation",
    "assets",
    "food-preferences"
  ],
  "PersonForm": [
    "people"
  ],
  "SelfProfilePanel": [
    "SelfProfilePanel",
    "teams"
  ],
  "LearningPage": [
    "system-evaluation",
    "teaching-reports",
    "food-preferences"
  ],
  "MyPaymentsPage": [
    "OrderNumber"
  ],
  "AdultGuardianInvitationsPage": [
    "registration"
  ],
  "ForgotPasswordPage": [
    "registration"
  ],
  "GuardianInvitationPage": [
    "registration"
  ],
  "RegistrationAdminPage": [
    "registration"
  ],
  "RegistrationGuardianPage": [
    "registration"
  ],
  "RegistrationProfilePage": [
    "registration"
  ],
  "RegistrationStartPage": [
    "registration"
  ],
  "RegistrationStatusPage": [
    "registration"
  ],
  "RegistrationVerifyPage": [
    "registration"
  ],
  "StaffApplicationsAdminPage": [
    "registration"
  ],
  "MyRevenueSharePage": [
    "revenue-share"
  ],
  "RevenueShareFinancePage": [
    "revenue-share"
  ],
  "RevenueShareManagementPage": [
    "revenue-share"
  ],
  "AccountsPage": [
    "settings"
  ],
  "AuditPage": [
    "settings"
  ],
  "MailSettingsPage": [
    "settings"
  ],
  "PermissionsPage": [
    "settings"
  ],
  "GameHost": [
    "talentTests"
  ],
  "TalentHistoryPage": [
    "talentTests"
  ],
  "TalentReportPage": [
    "talentTests",
    "AssessmentRankingPanel"
  ],
  "TalentTestsPage": [
    "talentTests"
  ],
  "CohortDetailPage": [
    "evaluation-foundation",
    "teaching",
    "admissions"
  ],
  "CohortsPage": [
    "teaching"
  ],
  "TeachingCatalogPage": [
    "teaching"
  ],
  "TeamManagementPage": [
    "teams"
  ]
}

export async function loadRouteStyles(moduleName: string): Promise<void> {
  await Promise.all((routeStyleOwners[moduleName] ?? []).map(group => styleGates[group]()))
}
