export function validArtifactIdentity(directory: string | undefined, caseId: string, categories: Record<string, boolean>): boolean {
  return Boolean(directory) && /^EA-[A-Z-]+-\d{3}$/.test(caseId)
    && Object.entries(categories).every(([key, value]) => /^[a-z][a-z0-9_]{0,63}$/.test(key) && typeof value === 'boolean')
}
