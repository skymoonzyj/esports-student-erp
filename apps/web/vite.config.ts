import react from "@vitejs/plugin-react"
import { defineConfig, loadEnv } from "vite"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "")
  const apiTarget = env.ERP_API_PROXY_TARGET ?? "http://127.0.0.1:8000"
  const evaluationTarget =
    env.EVALUATION_TOOL_PROXY_TARGET ?? "http://127.0.0.1:8010"
  return {
    plugins: [react()],
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
