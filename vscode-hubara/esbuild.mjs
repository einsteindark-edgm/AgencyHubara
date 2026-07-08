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

if (watch) {
  const [ext, web] = await Promise.all([
    context(extensionConfig),
    context(webviewConfig),
  ]);
  await Promise.all([ext.watch(), web.watch()]);
  console.log("[esbuild] watching…");
} else {
  await Promise.all([build(extensionConfig), build(webviewConfig)]);
  console.log("[esbuild] build complete");
}
