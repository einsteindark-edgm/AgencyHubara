import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { fileURLToPath } from "node:url";

// system_explorer — Vite dev/build config.
//
// Dev server proxy: redirige /api → FastAPI backend (default localhost:8000).
// Override con env `VITE_API_TARGET=http://host:port npm run dev`.
//
// En producción nginx hace el proxy (ver nginx.conf), por lo que esto sólo
// aplica al dev server.

const srcDir = fileURLToPath(new URL("./src", import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, fileURLToPath(new URL(".", import.meta.url)), "");
  const apiTarget = env.VITE_API_TARGET || "http://localhost:8000";

  return {
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": srcDir,
      },
    },
    server: {
      port: 5175,                                  // 5173=frontend_dashboard dev, 5174=docker host port
      host: true,                                  // 0.0.0.0 para docker dev
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: 5175,
      host: true,
    },
    build: {
      outDir: "dist",
      sourcemap: mode === "development",
    },
  };
});
