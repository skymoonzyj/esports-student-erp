// eslint-disable-next-line @typescript-eslint/triple-slash-reference -- Shared ambient types also declare the Node ESM build helper interfaces.
/// <reference path="./src/vite-env.d.ts" />
import react from "@vitejs/plugin-react"
import path from "node:path"
import { chmod, mkdir, rename, rmdir, writeFile } from "node:fs/promises"
import { defineConfig, loadEnv } from "vite"
import { resolveBuildId } from "./scripts/build-id.mjs"
import { writeCurrentAssets } from "./scripts/prepare-web-assets.mjs"
import { isAuthenticatedIconModule } from "./scripts/check-loading-graph.mjs"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const apiTarget = env.ERP_API_PROXY_TARGET ?? "http://127.0.0.1:8000"
  const evaluationTarget =
    env.EVALUATION_TOOL_PROXY_TARGET ?? "http://127.0.0.1:8010"
  const buildId = resolveBuildId({ root: process.cwd(), mode, explicitId: process.env.ERP_BUILD_ID || undefined })
  const releaseId = process.env.ERP_RELEASE_ID || buildId
  let distDir: string
  let metadataDir: string
  let bundleGraph: Record<string, { imports: string[]; dynamicImports: string[]; moduleIds: string[] }>
  let cssAssets: Record<string, string[]>
  return {
    build: { manifest: true, rolldownOptions: { output: { codeSplitting: { groups: [{
      name: "authenticated-icons", test: isAuthenticatedIconModule, includeDependenciesRecursively: false,
    }] } } } },
    define: { __ERP_BUILD_ID__: JSON.stringify(buildId) },
    plugins: [react(), {
      name: "erp-current-web-assets",
      apply: "build",
      configResolved(config) {
        distDir = path.resolve(config.root, config.build.outDir)
        metadataDir = path.resolve(config.root, ".build-metadata")
      },
      transformIndexHtml() {
        return [{ tag: "meta", attrs: { name: "erp-build-id", content: buildId }, injectTo: "head" }]
      },
      generateBundle(_options, bundle) {
        cssAssets = Object.fromEntries(Object.values(bundle).flatMap(item => item.type === "asset" && item.fileName.endsWith(".css") ? [[item.fileName, item.originalFileNames]] : []))
        bundleGraph = Object.fromEntries(Object.values(bundle).filter(item => item.type === "chunk").map(chunk => {
          const moduleIds = Object.keys(chunk.modules).sort()
          const css = [...((chunk as typeof chunk & { viteMetadata?: { importedCss: Set<string> } }).viteMetadata?.importedCss ?? [])]
          for (const file of css) cssAssets[file] = [...new Set([...(cssAssets[file] ?? []), ...moduleIds.filter(id => id.endsWith('.css')).map(id => path.relative(process.cwd(), id).replaceAll(path.sep, '/'))])]
          return [chunk.fileName, { imports: chunk.imports, dynamicImports: chunk.dynamicImports, moduleIds, css }]
        }))
        this.emitFile({ type: "asset", fileName: "build-info.json", source: `${JSON.stringify({ schema_version: 1, build_id: buildId })}\n` })
      },
      async closeBundle() {
        // Build metadata contains local source identities. Never retain it in
        // the HTTP-served dist tree or the public retained-asset inventory.
        await mkdir(metadataDir, { recursive: true, mode: 0o700 })
        await chmod(metadataDir, 0o700)
        const manifestPath = path.join(metadataDir, "vite-manifest.json")
        await rename(path.join(distDir, ".vite/manifest.json"), manifestPath)
        await chmod(manifestPath, 0o600)
        await rmdir(path.join(distDir, ".vite"))
        await writeFile(path.join(metadataDir, "bundle-graph.json"), JSON.stringify({ schemaVersion: 1, chunks: bundleGraph, cssAssets }), { mode: 0o600 })
        await writeCurrentAssets({ distDir, metadataDir, buildId, releaseId })
      },
    }],
    server: {
      port: 5173,
      proxy: {
        "/api": apiTarget,
        "/evaluation-tool": {
          target: evaluationTarget,
          changeOrigin: true,
          headers: { "X-Forwarded-Prefix": "/evaluation-tool" },
          rewrite: (path: string) =>
            path.replace(/^\/evaluation-tool/, "") || "/",
        },
      },
    },
  }
})
