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
    // Vitest no levanta `.env.development` por default (mode=test).
    // Definimos las VITE_* mínimas para que `shared/config/env.ts` no truene.
    env: {
      VITE_API_URL: "http://localhost:8000",
    },
  },
});
