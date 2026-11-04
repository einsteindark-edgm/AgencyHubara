// Pruebas de la lógica PURA de Studio (sin `vscode`): esbuild empaqueta cada
// `src/**/*.test.ts` y node:test las corre. `npm test`.
import { build } from "esbuild";
import { spawnSync } from "child_process";
import { mkdtempSync, readdirSync, rmSync, statSync } from "fs";
import { tmpdir } from "os";
import { join, relative } from "path";

const root = new URL("..", import.meta.url).pathname;

function findTests(dir) {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return findTests(p);
    return name.endsWith(".test.ts") ? [p] : [];
  });
}

const tests = findTests(join(root, "src"));
const out = mkdtempSync(join(tmpdir(), "acktos-studio-tests-"));
try {
  const files = [];
  for (const entry of tests) {
    const outfile = join(out, relative(root, entry).replace(/[\\/]/g, "_").replace(/\.ts$/, ".mjs"));
    await build({ entryPoints: [entry], outfile, bundle: true, platform: "node", format: "esm", logLevel: "error" });
    files.push(outfile);
  }
  const r = spawnSync(process.execPath, ["--test", ...files], { stdio: "inherit" });
  process.exitCode = r.status ?? 1;
} finally {
  rmSync(out, { recursive: true, force: true });
}
