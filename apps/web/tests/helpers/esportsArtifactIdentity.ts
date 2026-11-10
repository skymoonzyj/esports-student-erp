const SAFE_EVIDENCE_PHASES = ['gridshot_window', 'gridshot_fullscreen', 'sixshot_window', 'sixshot_fullscreen', 'lane_minimum_trial', 'lane_minimum_preflight', 'lane_melee_trial', 'lane_ranged_trial', 'lane_melee_running', 'lane_ranged_running', 'lane_melee_combat', 'lane_ranged_combat', 'lane_melee_death', 'lane_melee_respawn', 'lane_melee_report', 'lane_ranged_report', 'lane_hidden_interrupted', 'lane_blur_interrupted'] as const
export type SafeEvidencePhase = typeof SAFE_EVIDENCE_PHASES[number]

export function validArtifactIdentity(directory: string | undefined, caseId: string, categories: Record<string, boolean>, phase?: string): boolean {
  return Boolean(directory) && /^EA-[A-Z][A-Z0-9-]*-\d{3}$/.test(caseId)
    && (phase === undefined || (SAFE_EVIDENCE_PHASES as readonly string[]).includes(phase))
    && Object.entries(categories).every(([key, value]) => /^[a-z][a-z0-9_]{0,63}$/.test(key) && typeof value === 'boolean')
}
