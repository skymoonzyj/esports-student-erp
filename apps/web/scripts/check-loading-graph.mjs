import { readFile, readdir } from 'node:fs/promises'
import { fileURLToPath, pathToFileURL } from 'node:url'
import process from 'node:process'
import console from 'node:console'
import path from 'node:path'
import ts from 'typescript'

// Enumerated bootstrap contracts only. A new implementation cannot slip through
// by being renamed to a harmless chunk or by being statically re-exported.
const lightweight = new Set([
  'housing/HousingFilterState.tsx', 'housing/housingKeys.ts', 'housing/reportingKeys.ts',
  'talentTests/api.ts', 'talentTests/contracts.ts',
  'talentTests/pendingStorage.ts', 'esportsAssessments/pendingStorage.ts',
  'evaluations/api.ts', 'evaluations/EvaluationAuthorization.tsx',
  'evaluations/reportKeys.ts', 'evaluations/systemEvaluationKeys.ts',
  'evaluations/useEvaluationAccessError.ts', 'evaluations/useEvaluationContext.ts',
  'evaluations/useTeachingReportContext.ts',
  'evaluations/foundationApi.ts', 'evaluations/reportApi.ts',
])
const domains = /^(people|housing|procurement|evaluations|talentTests|esportsAssessments)\//

// Fixed pure leaves only. Never capture their helper/runtime dependencies or
// expand to the whole icon package; this policy is also used by the build.
const authenticatedIcons = new Set(['download', 'filter', 'pencil', 'trash-2', 'activity', 'calendar-days',
  'chevron-down', 'message-square-text', 'plane-takeoff', 'wallet-cards', 'mail', 'shield-check',
  'sliders-horizontal', 'user-plus', 'user-round', 'x'])
export function isAuthenticatedIconModule(id) {
  const match = id.replaceAll('\\', '/').match(/\/node_modules\/lucide-react\/dist\/esm\/icons\/([^/]+)\.js$/)
  return Boolean(match && authenticatedIcons.has(match[1]))
}

export function inspectAuthenticatedIcons({ graph, manifest, initialChunks, files }) {
  const violations = []
  const groups = Object.entries(graph.chunks).filter(([, chunk]) => chunk.moduleIds.some(isAuthenticatedIconModule))
  const [file, group] = groups[0] ?? []
  if (groups.length !== 1 || group?.moduleIds.length !== 16
    || new Set(group.moduleIds.map(id => id.replaceAll('\\', '/').split('/').at(-1))).size !== 16
    || !group.moduleIds.every(isAuthenticatedIconModule)) violations.push('not-exact-sixteen-leaf-group')
  if (file && initialChunks.includes(file)) violations.push('initial-icon-group')
  if (!file || !files.includes(file) || !Object.values(manifest).some(entry => entry.file === file)) violations.push('missing-final-icon-file')
  // The pre-change leaves only used bootstrap helpers. A new lazy dependency
  // here would move that boundary and could introduce a fetch/evaluation cycle.
  if (group?.imports.some(dependency => !initialChunks.includes(dependency))) violations.push('moved-icon-dependency')
  const active = new Set(), visited = new Set(), identities = new Set()
  function visit(name) {
    if (active.has(name)) { violations.push('static-cycle'); return }
    if (visited.has(name)) return
    const chunk = graph.chunks[name]
    if (!chunk) { violations.push('missing-static-dependency'); return }
    visited.add(name); active.add(name)
    for (const id of chunk.moduleIds) {
      if (/\/node_modules\/(?:react|react-dom|@tanstack\/react-query|@tanstack\/query-core)\/|\/src\/auth\/AuthProvider\.tsx$/.test(id)) {
        const identity = id.includes('/node_modules/') ? id.split('/node_modules/').at(-1) : 'src/auth/AuthProvider.tsx'
        if (identities.has(identity)) violations.push('duplicate-runtime-identity')
        identities.add(identity)
      }
    }
    chunk.imports.forEach(visit)
    active.delete(name)
  }
  Object.keys(graph.chunks).forEach(visit)
  return { ok: violations.length === 0, file: file ?? null, leafCount: group?.moduleIds.length ?? 0, violations: [...new Set(violations)] }
}

export function inspectSharedStyleOwnership({ manifest, cssAssets, entries, groups, owners }) {
  const eager = new Set()
  function initial(key) {
    if (eager.has(key)) return
    if (!manifest[key]) throw new Error('Missing manifest dependency: ' + key)
    eager.add(key); (manifest[key].imports ?? []).forEach(initial)
  }
  Object.keys(manifest).filter(key => manifest[key].isEntry).forEach(initial)
  const eagerCss = new Set([...eager].flatMap(key => manifest[key].css ?? []))
  const ownedEntries = new Set(Object.values(entries))
  function styles(key, seen = new Set()) {
    if (seen.has(key) || eager.has(key)) return []
    const entry = manifest[key]
    if (!entry) throw new Error('Missing manifest dependency: ' + key)
    seen.add(key)
    return [...(entry.css ?? []), ...(entry.file.endsWith('.css') ? [entry.file] : []),
      ...(entry.imports ?? []).flatMap(child => styles(child, seen)),
      ...(entry.dynamicImports ?? []).filter(child => !ownedEntries.has(child)).flatMap(child => styles(child, seen))]
  }
  const consumers = new Map(), dependencies = new Map(), violations = []
  for (const [name, source] of Object.entries(entries)) {
    const needed = new Set(styles(source).filter(css => !eagerCss.has(css)))
    dependencies.set(name, needed)
    for (const css of needed) {
      if (!consumers.has(css)) consumers.set(css, [])
      consumers.get(css).push(name)
    }
  }
  const assetsFor = source => Object.entries(cssAssets).filter(([, sources]) => sources.includes(source)).map(([file]) => file)
  const parentGroups = {
    'src/layout/AppShell.css': ['AppShell', 'PortalDashboardPage'],
    'src/esportsAssessments/assessment.css': ['AssessmentLayout', 'AssessmentHome'],
  }
  const shared = []
  for (const [css, names] of consumers) {
    if (names.length < 2) continue
    const parent = Object.entries(parentGroups).find(([source, allowed]) =>
      cssAssets[css]?.includes(source) && names.every(name => allowed.includes(name)) && names.includes(allowed[0]))
    shared.push({ css, consumers: names, completedParent: parent?.[1][0] ?? null })
    if (parent) continue
    for (const name of names) {
      const covered = (owners[name] ?? []).some(group => groups[group] && assetsFor(groups[group]).includes(css))
      if (!covered) violations.push({ consumer: name, css, reason: 'unowned-shared-style' })
    }
  }
  for (const [name, needed] of dependencies) for (const group of owners[name] ?? []) {
    const assets = groups[group] ? assetsFor(groups[group]) : []
    if (!assets.length || assets.some(css => !needed.has(css))) violations.push({ consumer: name, group, reason: 'unknown-or-unneeded-gate' })
  }
  return { ok: violations.length === 0, shared, violations }
}

// Offline declaration extraction only. Never evaluate application code or fetch
// a manifest at runtime; the coverage itself uses actual emitted dependencies.
export async function checkSharedStyleOwnership({ manifestPath, bundleGraphPath, registryPath, lazyPagesPath, personDetailPath = path.resolve(path.dirname(lazyPagesPath), '../people/PersonDetailPage.tsx') }) {
  const parse = async file => ts.createSourceFile(file, await readFile(file, 'utf8'), ts.ScriptTarget.Latest, true)
  const declarations = source => {
    const result = new Map()
    source.forEachChild(node => { if (ts.isVariableStatement(node)) for (const declaration of node.declarationList.declarations) result.set(declaration.name.getText(source), declaration.initializer) })
    return result
  }
  const imports = node => {
    const result = []
    function visit(child) {
      if (ts.isCallExpression(child) && child.expression.kind === ts.SyntaxKind.ImportKeyword && ts.isStringLiteral(child.arguments[0])) result.push(child.arguments[0].text)
      child.forEachChild(visit)
    }
    visit(node); return result
  }
  const registry = await parse(registryPath), declared = declarations(registry)
  const groups = {}, owners = {}, entries = {}
  const gates = declared.get('styleGates'), ownerMap = declared.get('routeStyleOwners')
  if (!gates || !ts.isObjectLiteralExpression(gates) || !ownerMap || !ts.isObjectLiteralExpression(ownerMap)) throw new Error('Missing literal style registry')
  for (const prop of gates.properties) {
    if (!ts.isPropertyAssignment(prop)) throw new Error('Nonliteral style group')
    const sources = imports(prop.initializer)
    if (sources.length !== 1 || !sources[0].endsWith('.css')) throw new Error('Style owner must have one literal CSS import')
    groups[prop.name.text] = path.posix.normalize('src/app/' + sources[0])
  }
  for (const prop of ownerMap.properties) {
    if (!ts.isPropertyAssignment(prop) || !ts.isArrayLiteralExpression(prop.initializer) || prop.initializer.elements.some(item => !ts.isStringLiteral(item))) throw new Error('Nonliteral consumer ownership')
    owners[prop.name.text] = prop.initializer.elements.map(item => item.text)
  }
  const pages = await parse(lazyPagesPath)
  for (const initializer of declarations(pages).values()) {
    if (!initializer || !ts.isCallExpression(initializer)) continue
    const kind = initializer.expression.getText(pages)
    if (!['page', 'routeModule'].includes(kind)) continue
    const name = initializer.arguments[kind === 'page' ? 0 : 1]
    const sources = imports(initializer)
    if (!ts.isStringLiteral(name) || sources.length !== 1) throw new Error('Nonliteral route entry')
    entries[name.text] = path.posix.normalize('src/app/' + sources[0]) + '.tsx'
  }
  if (!Object.keys(entries).length) throw new Error('No checked route entries')
  // The one approved nested optional panel has its own local boundary and gate.
  // Unknown nested entries are NOT excluded by the traversal above.
  const detail = await parse(personDetailPath)
  const panel = declarations(detail).get('AssessmentReportsPanel')
  const panelImports = panel ? imports(panel) : []
  const loader = panel?.arguments?.[1]
  const first = loader?.body?.statements?.[0]
  const gate = first && ts.isExpressionStatement(first) && ts.isAwaitExpression(first.expression) ? first.expression.expression : null
  if (!panel || !ts.isCallExpression(panel) || panel.expression.getText(detail) !== 'createLazyModule'
    || panel.arguments[0]?.text !== 'assessment2-person-reports'
    || !gate || !ts.isCallExpression(gate) || gate.expression.getText(detail) !== 'loadRouteStyles'
    || gate.arguments[0]?.text !== 'AssessmentReportsPanel'
    || panelImports.length !== 1 || panelImports[0] !== '../esportsAssessments/AssessmentReportsPanel') throw new Error('Missing owned nested report-panel entry')
  entries.AssessmentReportsPanel = 'src/esportsAssessments/AssessmentReportsPanel.tsx'
  // Finite Task10 nested entries. Validate every caller, including all four
  // named factories sharing one emitted dialog bundle and sticky CSS owner.
  const nested = [
    ['assistedPeople/AssistedPeopleRoutes.tsx', 'Requests', 'assisted-requests', 'AssistedRequestsPage', './MyAssistedRequestsPage'],
    ['assistedPeople/AssistedPeopleRoutes.tsx', 'Create', 'assisted-create', 'AssistedDirectCreatePage', './AssistedDirectCreatePage'],
    ['assistedPeople/AssistedPeopleRoutes.tsx', 'Managed', 'assisted-managed', 'ManagedPeoplePage', './ManagedPeoplePage'],
    ['assistedPeople/AssistedProfileCorrectionPanel.tsx', 'CorrectionDraft', 'assisted-correction-draft', 'CorrectionDraft', './CorrectionDraft'],
    ...['PurchaseFormDialog', 'InvoiceDialog', 'SettlementDialog', 'ReimbursementDialog'].map(name =>
      ['procurement/ProcurementPage.tsx', name, `procurement-${name}`, 'ProcurementDialogs', './ProcurementDialogs']),
  ]
  for (const [sourcePath, binding, id, name, imported] of nested) {
    const source = await parse(path.resolve(path.dirname(lazyPagesPath), '..', sourcePath))
    const initializer = declarations(source).get(binding)
    const first = initializer?.arguments?.[1]?.body?.statements?.[0]
    const gate = first && ts.isExpressionStatement(first) && ts.isAwaitExpression(first.expression) ? first.expression.expression : null
    const sources = initializer ? imports(initializer) : []
    if (!initializer || !ts.isCallExpression(initializer) || initializer.expression.getText(source) !== 'createLazyModule'
      || initializer.arguments[0]?.text !== id || !gate || !ts.isCallExpression(gate)
      || gate.expression.getText(source) !== 'loadRouteStyles' || gate.arguments[0]?.text !== name
      || sources.length !== 1 || sources[0] !== imported) throw new Error(`Missing owned nested entry: ${binding}`)
    entries[name] = path.posix.normalize('src/' + path.posix.dirname(sourcePath) + '/' + imported) + '.tsx'
  }
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const graph = JSON.parse(await readFile(bundleGraphPath, 'utf8'))
  for (const source of Object.values(entries)) {
    const chunk = graph.chunks[manifest[source]?.file]
    if (!chunk?.moduleIds.some(id => id.replaceAll('\\', '/').endsWith('/' + source))) throw new Error('Route module missing from emitted graph: ' + source)
  }
  return inspectSharedStyleOwnership({ manifest, cssAssets: graph.cssAssets, entries, groups, owners })
}

/** Inspect the emitted chunk graph, not filenames or the source import syntax. */
export async function checkInitialGraph({ manifestPath, bundleGraphPath }) {
  const manifest = JSON.parse(await readFile(manifestPath, 'utf8'))
  const graph = JSON.parse(await readFile(bundleGraphPath, 'utf8'))
  if (graph.schemaVersion !== 1 || !graph.chunks) throw new Error('Invalid bundle graph')
  const roots = Object.values(manifest).filter(entry => entry.isEntry).map(entry => entry.file)
  if (!roots.length) throw new Error('Missing manifest entry')
  const seen = new Set(), modules = new Set()
  function visit(file) {
    if (seen.has(file)) return
    const chunk = graph.chunks[file]
    if (!chunk || !Array.isArray(chunk.moduleIds) || !Array.isArray(chunk.imports)) throw new Error('Missing or invalid static chunk: ' + file)
    seen.add(file)
    chunk.moduleIds.forEach(id => modules.add(id))
    chunk.imports.forEach(visit)
  }
  roots.forEach(visit)
  const forbiddenModules = [...modules].filter(id => {
    if (isAuthenticatedIconModule(id)) return true
    const relative = id.replaceAll('\\', '/').split('/src/')[1]?.split('?')[0]
    return relative && domains.test(relative) && !lightweight.has(relative)
  }).sort()
  return { ok: forbiddenModules.length === 0, initialChunks: [...seen].sort(), initialModules: [...modules].sort(), forbiddenModules }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const result = await checkInitialGraph({ manifestPath: process.argv[2], bundleGraphPath: process.argv[3] })
  const source = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../src/app')
  const styles = await checkSharedStyleOwnership({ manifestPath: process.argv[2], bundleGraphPath: process.argv[3], registryPath: path.join(source, 'routeStyles.ts'), lazyPagesPath: path.join(source, 'lazyPages.ts') })
  const icons = inspectAuthenticatedIcons({
    graph: JSON.parse(await readFile(process.argv[3], 'utf8')),
    manifest: JSON.parse(await readFile(process.argv[2], 'utf8')), initialChunks: result.initialChunks,
    files: (await readdir(path.resolve(path.dirname(process.argv[2]), '../dist/assets'))).map(file => 'assets/' + file),
  })
  console.log(JSON.stringify({ initial: result, styles, icons }, null, 2))
  process.exitCode = result.ok && styles.ok && icons.ok ? 0 : 1
}
