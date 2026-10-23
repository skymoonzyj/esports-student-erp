import type { SuiteId } from './contracts'

export const ASSESSMENT_SUITE_AVAILABILITY: Readonly<Record<SuiteId, boolean>> = {
  'pc-fps': false,
  'mobile-fps': false,
  'pc-moba': false,
  'mobile-moba': false,
}
