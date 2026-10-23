/// <reference types="vite/client" />

declare const __ERP_BUILD_ID__: string

declare module "*check-loading-graph.mjs" {
  export function isAuthenticatedIconModule(id: string): boolean
}

declare module "*build-id.mjs" {
  export function resolveBuildId(options: { root: string; mode: string; explicitId?: string }): string
}

declare module "*prepare-web-assets.mjs" {
  export interface AssetFile { path: string; size: number; sha256: string }
  export interface AssetSet { release_id: string; build_id: string; superseded_by_release_id: string | null; files: AssetFile[] }
  export interface AssetManifest {
    schema_version: 1
    build_id: string
    release_id: string
    predecessor_image_id: string | null
    input_sha256: string | null
    total_bytes: number
    documents: AssetFile[]
    sets: AssetSet[]
  }
  export function writeCurrentAssets(options: { distDir: string; metadataDir: string; buildId: string; releaseId: string }): Promise<AssetManifest>
}
