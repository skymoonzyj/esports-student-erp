import { createElement, Suspense, type ComponentType } from 'react'
import { createLazyModule } from './loading/lazyModule'
import { ModuleBoundary } from './loading/ModuleBoundary'
import type { HousingOverviewPageProps } from '../housing/HousingOverviewPage'
import { loadRouteStyles } from './routeStyles'

function routeModule<P extends object>(id: string, name: string, loader: () => Promise<{ default: ComponentType<P> }>, mode: 'suspense' | 'controlled' = 'suspense') {
  return createLazyModule(id, async () => {
    const styles = loadRouteStyles(name)
    if (name === 'HousingOverviewPage' || name === 'PeoplePage') {
      // These two authorized leaves may fetch/evaluate code while CSS loads,
      // but never mount it until CSS succeeds. Observe rejection immediately;
      // a sticky CSS failure takes priority even when JS fails first.
      const page = loader().then(
        value => ({ ok: true as const, value }),
        (error: unknown) => ({ ok: false as const, error }),
      )
      await styles
      const result = await page
      if (!result.ok) throw result.error
      return result.value
    }
    await styles
    return loader()
  }, mode)
}

// Factories are document-lifetime declarations, never created during rendering.
// Only the leaf boundary retries; guards, providers and successful parents stay put.
function page<P extends object>(id: string, loader: () => Promise<{ default: ComponentType<P> }>, mode: 'suspense' | 'controlled' = 'suspense'): ComponentType<P> {
  const module = routeModule(id, id, loader, mode)
  return boundaryPage(id, module, mode)
}

function boundaryPage<P extends object>(id: string, module: ReturnType<typeof createLazyModule<P>>, mode: 'suspense' | 'controlled'): ComponentType<P> {
  return function LazyPage(props: P) {
    return createElement(ModuleBoundary, { moduleId: id, retry: module.retry,
      children: mode === 'controlled' ? createElement(module.Component, props)
        : createElement(Suspense, { fallback: createElement('p', { role: 'status' }, '正在加载页面…') }, createElement(module.Component, props)),
    })
  }
}

export const AdmissionCasesPage = page('AdmissionCasesPage', async () => ({ default: (await import('../admissions/AdmissionCasesPage')).AdmissionCasesPage }))
export const AssetScanPage = page('AssetScanPage', async () => ({ default: (await import('../assets/AssetScanPage')).AssetScanPage }))
export const AssetsPage = page('AssetsPage', async () => ({ default: (await import('../assets/AssetsPage')).AssetsPage }))
export const MyAssetsPage = page('MyAssetsPage', async () => ({ default: (await import('../assets/MyAssetsPage')).MyAssetsPage }))
export const EnrollmentDetailPage = page('EnrollmentDetailPage', async () => ({ default: (await import('../admissions/EnrollmentDetailPage')).EnrollmentDetailPage }))
export const EnrollmentsPage = page('EnrollmentsPage', async () => ({ default: (await import('../admissions/EnrollmentsPage')).EnrollmentsPage }))
export const PaymentsPage = page('PaymentsPage', async () => ({ default: (await import('../admissions/PaymentsPage')).PaymentsPage }))
export const ActivateProfilePage = page('ActivateProfilePage', async () => ({ default: (await import('../auth/ActivateProfilePage')).ActivateProfilePage }))
export const ForgotPasswordPage = page('ForgotPasswordPage', async () => ({ default: (await import('../registration/ForgotPasswordPage')).ForgotPasswordPage }))
export const AdultGuardianInvitationsPage = page('AdultGuardianInvitationsPage', async () => ({ default: (await import('../registration/AdultGuardianInvitationsPage')).AdultGuardianInvitationsPage }))
export const GuardianInvitationPage = page('GuardianInvitationPage', async () => ({ default: (await import('../registration/GuardianInvitationPage')).GuardianInvitationPage }))
export const RegistrationAdminPage = page('RegistrationAdminPage', async () => ({ default: (await import('../registration/RegistrationAdminPage')).RegistrationAdminPage }))
export const RegistrationGuardianPage = page('RegistrationGuardianPage', async () => ({ default: (await import('../registration/RegistrationGuardianPage')).RegistrationGuardianPage }))
export const RegistrationProfilePage = page('RegistrationProfilePage', async () => ({ default: (await import('../registration/RegistrationProfilePage')).RegistrationProfilePage }))
export const RegistrationStartPage = page('RegistrationStartPage', async () => ({ default: (await import('../registration/RegistrationStartPage')).RegistrationStartPage }))
export const RegistrationStatusPage = page('RegistrationStatusPage', async () => ({ default: (await import('../registration/RegistrationStatusPage')).RegistrationStatusPage }))
export const RegistrationVerifyPage = page('RegistrationVerifyPage', async () => ({ default: (await import('../registration/RegistrationVerifyPage')).RegistrationVerifyPage }))
export const StaffApplicationsAdminPage = page('StaffApplicationsAdminPage', async () => ({ default: (await import('../registration/StaffApplicationsAdminPage')).StaffApplicationsAdminPage }))
export const AppShell = page('AppShell', async () => ({ default: (await import('../layout/AppShell')).AppShell }))
export const EvaluationCenterPage = page('EvaluationCenterPage', async () => ({ default: (await import('../evaluations/EvaluationCenterPage')).EvaluationCenterPage }))
export const EvaluationConnectPage = page('EvaluationConnectPage', async () => ({ default: (await import('../evaluations/EvaluationConnectPage')).EvaluationConnectPage }))
export const EvaluationAdminPage = page('EvaluationAdminPage', async () => ({ default: (await import('../evaluations/EvaluationAdminPage')).EvaluationAdminPage }))
export const EvaluationTaskDetail = page('EvaluationTaskDetail', async () => ({ default: (await import('../evaluations/EvaluationTaskDetail')).EvaluationTaskDetail }))
export const EvaluationTaskList = page('EvaluationTaskList', async () => ({ default: (await import('../evaluations/EvaluationTaskList')).EvaluationTaskList }))
export const GameAccountsPage = page('GameAccountsPage', async () => ({ default: (await import('../evaluations/GameAccountsPage')).GameAccountsPage }))
export const CampSurveysPage = page('CampSurveysPage', async () => ({ default: (await import('../evaluations/CampSurveysPage')).CampSurveysPage }))
export const MySurveyFeedbackPage = page('MySurveyFeedbackPage', async () => ({ default: (await import('../evaluations/MySurveyFeedbackPage')).MySurveyFeedbackPage }))
export const CampSurveyAdminPage = page('CampSurveyAdminPage', async () => ({ default: (await import('../evaluations/CampSurveyAdminPage')).CampSurveyAdminPage }))
export const CampSurveyPrintPage = page('CampSurveyPrintPage', async () => ({ default: (await import('../evaluations/CampSurveyPrintPage')).CampSurveyPrintPage }))
export const SystemEvaluationPage = page('SystemEvaluationPage', async () => ({ default: (await import('../evaluations/SystemEvaluationPage')).SystemEvaluationPage }))
export const TeachingReportReviewPage = page('TeachingReportReviewPage', async () => ({ default: (await import('../evaluations/TeachingReportReviewPage')).TeachingReportReviewPage }))
export const TeachingReportsPage = page('TeachingReportsPage', async () => ({ default: (await import('../evaluations/TeachingReportsPage')).TeachingReportsPage }))
export const EnrollmentCatalogPage = page('EnrollmentCatalogPage', async () => ({ default: (await import('../enrollment/EnrollmentCatalogPage')).EnrollmentCatalogPage }))
export const FoodOnboardingPage = page('FoodOnboardingPage', async () => ({ default: (await import('../foodPreferences/FoodOnboardingPage')).FoodOnboardingPage }))
export const FoodCuisineSettingsPage = page('FoodCuisineSettingsPage', async () => ({ default: (await import('../foodPreferences/FoodCuisineSettingsPage')).FoodCuisineSettingsPage }))
export const FoodPreferenceStatisticsPage = page('FoodPreferenceStatisticsPage', async () => ({ default: (await import('../foodPreferences/FoodPreferenceStatisticsPage')).FoodPreferenceStatisticsPage }))
export const MyFoodProfilePage = page('MyFoodProfilePage', async () => ({ default: (await import('../foodPreferences/MyFoodProfilePage')).MyFoodProfilePage }))
const housingOverviewModule = routeModule<HousingOverviewPageProps>('HousingOverviewPage', 'HousingOverviewPage', async () => ({ default: (await import('../housing/HousingOverviewPage')).HousingOverviewPage }), 'controlled')
export const HousingOverviewPage = boundaryPage('HousingOverviewPage', housingOverviewModule, 'controlled')
export const prepareHousingOverviewPageCode = housingOverviewModule.prepare!
export const HousingReportsPage = page('HousingReportsPage', async () => ({ default: (await import('../housing/HousingReportsPage')).HousingReportsPage }))
export const HousingRoomDetailPage = page('HousingRoomDetailPage', async () => ({ default: (await import('../housing/HousingRoomDetailPage')).HousingRoomDetailPage }))
export const MyHousingPage = page('MyHousingPage', async () => ({ default: (await import('../housing/MyHousingPage')).MyHousingPage }))
export const MyInvitationsPage = page('MyInvitationsPage', async () => ({ default: (await import('../invitations/MyInvitationsPage')).MyInvitationsPage }))
const peopleModule = routeModule('PeoplePage', 'PeoplePage', async () => ({ default: (await import('../people/PeoplePage')).PeoplePage }), 'controlled')
export const PeoplePage = boundaryPage('PeoplePage', peopleModule, 'controlled')
export const preparePeoplePageCode = peopleModule.prepare!
export const PersonDetailPage = page('PersonDetailPage', async () => ({ default: (await import('../people/PersonDetailPage')).PersonDetailPage }))
export const PersonForm = page('PersonForm', async () => ({ default: (await import('../people/PersonForm')).PersonForm }))
export const SelfProfilePanel = page('SelfProfilePanel', async () => ({ default: (await import('../people/SelfProfilePanel')).SelfProfilePanel }))
export const OperationsMonitorPage = page('OperationsMonitorPage', async () => ({ default: (await import('../operations/OperationsMonitorPage')).OperationsMonitorPage }))
export const CoachNextCohortsPage = page('CoachNextCohortsPage', async () => ({ default: (await import('../portal/CoachNextCohortsPage')).CoachNextCohortsPage }))
export const LearningPage = page('LearningPage', async () => ({ default: (await import('../portal/LearningPage')).LearningPage }))
export const MyPaymentsPage = page('MyPaymentsPage', async () => ({ default: (await import('../portal/MyPaymentsPage')).MyPaymentsPage }))
export const PortalDashboardPage = page('PortalDashboardPage', async () => ({ default: (await import('../portal/PortalDashboardPage')).PortalDashboardPage }))
export const ProcurementPage = page('ProcurementPage', async () => ({ default: (await import('../procurement/ProcurementPage')).ProcurementPage }))
export const MyRevenueSharePage = page('MyRevenueSharePage', async () => ({ default: (await import('../revenueShare/MyRevenueSharePage')).MyRevenueSharePage }))
export const RevenueShareFinancePage = page('RevenueShareFinancePage', async () => ({ default: (await import('../revenueShare/RevenueShareFinancePage')).RevenueShareFinancePage }))
export const RevenueShareManagementPage = page('RevenueShareManagementPage', async () => ({ default: (await import('../revenueShare/RevenueShareManagementPage')).RevenueShareManagementPage }))
export const AccountsPage = page('AccountsPage', async () => ({ default: (await import('../settings/AccountsPage')).AccountsPage }))
export const AuditPage = page('AuditPage', async () => ({ default: (await import('../settings/AuditPage')).AuditPage }))
export const MailSettingsPage = page('MailSettingsPage', async () => ({ default: (await import('../settings/MailSettingsPage')).MailSettingsPage }))
export const PermissionsPage = page('PermissionsPage', async () => ({ default: (await import('../settings/PermissionsPage')).PermissionsPage }))
export const CohortDetailPage = page('CohortDetailPage', async () => ({ default: (await import('../teaching/CohortDetailPage')).CohortDetailPage }))
export const CohortsPage = page('CohortsPage', async () => ({ default: (await import('../teaching/CohortsPage')).CohortsPage }))
export const TeachingCatalogPage = page('TeachingCatalogPage', async () => ({ default: (await import('../teaching/TeachingCatalogPage')).TeachingCatalogPage }))
export const TeamManagementPage = page('TeamManagementPage', async () => ({ default: (await import('../teams/TeamManagementPage')).TeamManagementPage }))
export const GameHost = page('GameHost', async () => ({ default: (await import('../talentTests/GameHost')).GameHost }))
export const TalentHistoryPage = page('TalentHistoryPage', async () => ({ default: (await import('../talentTests/TalentHistoryPage')).TalentHistoryPage }))
export const TalentReportPage = page('TalentReportPage', async () => ({ default: (await import('../talentTests/TalentReportPage')).TalentReportPage }))
export const TalentTestsPage = page('TalentTestsPage', async () => ({ default: (await import('../talentTests/TalentTestsPage')).TalentTestsPage }))
export const TravelReportsPage = page('TravelReportsPage', async () => ({ default: (await import('../travelReports/TravelReportsPage')).TravelReportsPage }))
export const AssessmentLayout = routeModule('assessment2-layout', 'AssessmentLayout', () => import('../esportsAssessments/AssessmentLayout'))
export const AssistedPeopleRoutes = page('AssistedPeopleRoutes', async () => ({ default: (await import('../assistedPeople/AssistedPeopleRoutes')).AssistedPeopleRoutes }))
export const AssessmentHome = routeModule('assessment2-home', 'AssessmentHome', () => import('../esportsAssessments/AssessmentHome'))
export const AssessmentSessionPage = routeModule('assessment2-session', 'AssessmentSessionPage', () => import('../esportsAssessments/AssessmentSessionPage'))
export const AssessmentPracticePage = routeModule('assessment2-practice', 'PracticePage', () => import('../esportsAssessments/PracticePage'))
export const AssessmentHistoryPage = routeModule('assessment2-history', 'AssessmentHistoryPage', () => import('../esportsAssessments/AssessmentHistoryPage'))
export const AssessmentReportPage = routeModule('assessment2-report', 'AssessmentReportPage', () => import('../esportsAssessments/AssessmentReportPage'))
