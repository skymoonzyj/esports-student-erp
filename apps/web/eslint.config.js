import js from "@eslint/js"
import tseslint from "typescript-eslint"

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      globals: {
        document: "readonly",
        fetch: "readonly",
        Headers: "readonly",
        localStorage: "readonly",
        Request: "readonly",
        Response: "readonly",
        window: "readonly",
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
    },
  },
)
