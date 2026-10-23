import { existsSync, readFileSync } from 'node:fs'
import path from 'node:path'
import ts from 'typescript'
import { expect, test } from 'vitest'

test('public food path has no transitive page, panel or stylesheet dependency', () => {
  // A real import edge to the page/panel must fail even if it only re-exports
  // the constant: those edges pull the domain CSS into the eager shell graph.
  const entry = path.resolve('src/foodPreferences/paths.ts')
  expect(existsSync(entry), 'lightweight public path entry is missing').toBe(true)
  const pending = [entry]
  const seen = new Set<string>()
  while (pending.length) {
    const file = pending.pop()!
    if (seen.has(file)) continue
    seen.add(file)
    expect(file).not.toMatch(/(?:Page|Panel)\.[cm]?[jt]sx?$|\.css$/)
    const source = ts.createSourceFile(file, readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true)
    for (const node of source.statements) {
      if (!(ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) || !node.moduleSpecifier || !ts.isStringLiteral(node.moduleSpecifier)) continue
      const specifier = node.moduleSpecifier.text
      expect(specifier.startsWith('.')).toBe(true)
      const resolved = ts.resolveModuleName(specifier, file, { moduleResolution: ts.ModuleResolutionKind.Bundler }, ts.sys).resolvedModule
      expect(resolved, `unresolved dependency ${specifier}`).toBeDefined()
      pending.push(resolved!.resolvedFileName)
    }
  }
})
