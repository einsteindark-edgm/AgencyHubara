import { build, context } from "esbuild";

const production = process.argv.includes("--production");
const watch = process.argv.includes("--watch");

/** @type {import('esbuild').BuildOptions} */
const shared = {
  bundle: true,
  minify: production,
  sourcemap: !production,
  logLevel: "info",
};

// El extension host corre en Node; `vscode` es un módulo provisto por el runtime.
const extensionConfig = {
  ...shared,
  entryPoints: ["src/extension.ts"],
  outfile: "dist/extension.js",
  platform: "node",
  format: "cjs",
  target: "node18",
  external: ["vscode"],
};

// La webview corre en un iframe (navegador). Sin acceso a Node. `process.env`
// no existe ahí — React lo referencia para el modo dev/prod, así que se
// resuelve en build-time con `define` (si no, ReferenceError en runtime).
const webviewConfig = {
  ...shared,
  entryPoints: ["webview/src/main.tsx"],
  outfile: "dist/webview.js",
  platform: "browser",
  format: "iife",
  target: "es2020",
  define: {
    "process.env.NODE_ENV": production ? '"production"' : '"development"',
  },
};

// El panel nativo "Ejecución" (F10) — segundo webview, bundle propio (mucho
// más chico: sin React Flow), mismo CSS compartido vía import de main.css.
const execViewConfig = {
  ...webviewConfig,
  entryPoints: ["webview/src/exec/main.tsx"],
  outfile: "dist/execview.js",
};

// Forge Console — tercer webview, concepto APARTE (migración de clientes, no
// desarrollo de agentes/plugins): bundle y CSS propios, cero imports del canvas.
const forgeViewConfig = {
  ...webviewConfig,
  entryPoints: ["webview/src/forge/main.tsx"],
  outfile: "dist/forgeview.js",
};

// Export view — la pantalla visual de relaciones del export (grafo React Flow
// con checkboxes). Reusa el layout dagre; bundle propio con su CSS.
const exportViewConfig = {
  ...webviewConfig,
  entryPoints: ["webview/src/export/main.tsx"],
  outfile: "dist/exportview.js",
};

if (watch) {
  const [ext, web, exec, forge, exp] = await Promise.all([
    context(extensionConfig),
    context(webviewConfig),
    context(execViewConfig),
    context(forgeViewConfig),
    context(exportViewConfig),
  ]);
  await Promise.all([ext.watch(), web.watch(), exec.watch(), forge.watch(), exp.watch()]);
  console.log("[esbuild] watching…");
} else {
  await Promise.all([
    build(extensionConfig),
    build(webviewConfig),
    build(execViewConfig),
    build(forgeViewConfig),
    build(exportViewConfig),
  ]);
  console.log("[esbuild] build complete");
}
