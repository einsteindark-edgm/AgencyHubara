#!/usr/bin/env node
// Separa el resultado del workflow document-hubara-pipelines en data/<id>.json + prose/<id>.md.
// Uso: node split.mjs <ruta-al-output-del-workflow>
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const SRC = process.argv[2];
if (!SRC || !fs.existsSync(SRC)) {
  console.error("✗ Pasá la ruta al output del workflow: node split.mjs <archivo>");
  process.exit(1);
}
fs.mkdirSync(path.join(DIR, "data"), { recursive: true });
fs.mkdirSync(path.join(DIR, "prose"), { recursive: true });

const obj = JSON.parse(fs.readFileSync(SRC, "utf8"));
const items = Array.isArray(obj) ? obj : (obj.result || []);
if (!items.length) { console.error("✗ No hay items en result[]"); process.exit(1); }

let problems = 0;
for (const it of items) {
  if (!it || !it.id || !it.verified) { console.error("  ! item sin id/verified, salteado"); continue; }
  const v = it.verified;
  fs.writeFileSync(path.join(DIR, "data", it.id + ".json"), JSON.stringify(v, null, 2));
  fs.writeFileSync(path.join(DIR, "prose", it.id + ".md"), (it.prose || "").trim() + "\n");
  const nc = v.node_count, nn = (v.nodes || []).length;
  const mismatch = nc != null && nc !== nn;
  if (mismatch) problems++;
  console.log(
    "  ✓ " + it.id +
    " | node_count=" + nc + " nodes[]=" + nn + (mismatch ? "  ⚠️ MISMATCH" : "") +
    " | edges=" + (v.edges || []).length +
    " | phases=" + (v.phases || []).length +
    " | vnotes=" + (v.verification_notes || []).length +
    " | prose=" + (it.prose || "").length + "ch"
  );
}
console.log(problems ? ("⚠️ " + problems + " pipelines con node_count != nodes[].length") : "✓ node_count == nodes[].length en todos");
