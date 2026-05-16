import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// `__dirname` no existe en ESM (package.json "type": "module").
// Derivamos el path src/ desde import.meta.url — funciona en local y en Docker.
const srcDir = fileURLToPath(new URL("./src", import.meta.url));
// `@plugins` apunta al directorio de plugins frontend. El loader Python en
// hubara_agency lee los mismos manifests vía path relativo. Ver
// PLUGIN_REFACTOR_PLAN.md §1.
const pluginsDir = fileURLToPath(new URL("./src/plugins", import.meta.url));

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": srcDir,
      "@plugins": pluginsDir,
    },
  },
  clearScreen: false,
  server: {
    port: 5173,
    strictPort: true,
    host: "0.0.0.0",
  },
  envPrefix: ["VITE_", "TAURI_"],
});
