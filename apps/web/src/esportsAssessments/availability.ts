import type { SuiteId } from './contracts'

export const ASSESSMENT_SUITE_AVAILABILITY: Readonly<Record<SuiteId, boolean>> = {
  'pc-fps': true,
  'mobile-fps': false,
  'pc-moba': true,
  'mobile-moba': false,
}
