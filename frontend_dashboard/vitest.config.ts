import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const srcDir = fileURLToPath(new URL("./src", import.meta.url));

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": srcDir,
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    css: false,
    // e2e/** son specs de @playwright/test; el runner de vitest no inicializa
    // ese runtime, así que si los recoge truenan con "expect is not a function".
    exclude: ["**/node_modules/**", "**/dist/**", "e2e/**"],
    // Vitest no levanta `.env.development` por default (mode=test).
    // Definimos las VITE_* mínimas para que `shared/config/env.ts` no truene.
    env: {
      VITE_API_URL: "http://localhost:8000",
    },
  },
});
