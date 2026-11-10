import type { SuiteId, TestId } from './contracts'

export interface RuleIdentity {
  readonly raw_kind: string
  readonly scoring_version: string
  readonly report_version: string
}

const legacyKinds: Readonly<Record<TestId, string>> = {
  precision: 'aim',
  switching: 'aim',
  tracking: 'tracking',
  recognition: 'recognition',
  'move-fire': 'move_fire',
  'last-hit': 'last_hit',
  'orb-walk': 'rhythm',
  'move-attack': 'rhythm',
  dodge: 'dodge',
  'skill-aim': 'skill_aim',
  'map-awareness': 'map',
  gridshot: 'multi_target',
  sixshot: 'multi_target',
  spheretrack90: 'spheretrack90',
}

const members: Readonly<Record<SuiteId, readonly TestId[]>> = {
  'pc-fps': ['precision', 'tracking', 'switching', 'recognition'],
  'mobile-fps': ['precision', 'tracking', 'switching', 'move-fire'],
  'pc-moba': ['last-hit', 'orb-walk', 'dodge', 'map-awareness'],
  'mobile-moba': ['dodge', 'skill-aim', 'move-attack', 'map-awareness'],
}

export function isPcFpsTriadCatalog(suiteId: SuiteId, catalogVersion: string): boolean {
  return suiteId === 'pc-fps' && (catalogVersion === 'pc-fps-v4' || catalogVersion === 'pc-fps-v5')
}

export function isPcMobaLaneCatalog(suiteId: SuiteId, catalogVersion: string): boolean {
  return suiteId === 'pc-moba' && (catalogVersion === 'pc-moba-v4' || catalogVersion === 'pc-moba-v5')
}

export function registeredRule(suiteId: SuiteId, testId: TestId, catalogVersion: string): RuleIdentity {
  if (suiteId === 'pc-moba' && catalogVersion === 'pc-moba-v5') {
    if (testId !== 'last-hit') throw new Error('unknown suite or task')
    return { raw_kind: 'lane_last_hit_towers', scoring_version: 'pc-moba-v5', report_version: 'pc-moba-v5' }
  }
  if (isPcMobaLaneCatalog(suiteId, catalogVersion)) {
    if (testId !== 'last-hit') throw new Error('unknown suite or task')
    return { raw_kind: 'lane_last_hit', scoring_version: 'pc-moba-v4', report_version: 'pc-moba-v4' }
  }
  if (isPcFpsTriadCatalog(suiteId, catalogVersion)) {
    if (!['gridshot', 'sixshot', 'spheretrack90'].includes(testId)) throw new Error('unknown suite or task')
    return { raw_kind: legacyKinds[testId], scoring_version: 'pc-fps-v4', report_version: 'pc-fps-v4' }
  }
  if (!members[suiteId].includes(testId)) throw new Error('unknown suite or task')
  if (!['v1', 'v2', 'v3'].some(version => catalogVersion === `${suiteId}-${version}`)) {
    throw new Error('unknown catalog version')
  }
  let raw_kind = legacyKinds[testId]
  let scoring_version = `${suiteId}-v1`
  let report_version = `${suiteId}-v1`
  if (catalogVersion === `${suiteId}-v3`) {
    if (testId === 'move-fire') raw_kind = 'move_fire_v3'
    else if (testId === 'dodge') raw_kind = 'dodge_choice'
    if (testId === 'switching' || testId === 'move-fire' || testId === 'dodge') {
      scoring_version = `${suiteId}-v2`
      report_version = `${suiteId}-v2`
    }
  }
  return { raw_kind, scoring_version, report_version }
}

export function usesCurrentFpsView(config: { readonly suite_id: SuiteId; readonly engine_version: string }): boolean {
  return config.engine_version === `${config.suite_id}-v2` || config.engine_version === `${config.suite_id}-v3` || isPcFpsTriadCatalog(config.suite_id, config.engine_version)
}
