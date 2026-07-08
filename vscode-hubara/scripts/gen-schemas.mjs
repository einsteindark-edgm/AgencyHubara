#!/usr/bin/env node
/**
 * Genera los JSON Schemas de manifests DESDE los modelos pydantic reales — no
 * los reescribe a mano (drift garantizado si se hiciera). Corre una sola vez
 * (dev-time, no en cada build) y commitea el resultado en `schemas/`.
 *
 * Fuentes (una por lado, cada una en SU intérprete):
 *   GraphAgents/sdk/manifest_model.py:AgentNode   → *.agent.yaml + *.taskgraph.yaml
 *   GraphAgents/sdk/tool_model.py:ToolContract    → tool.yaml
 *   hubara_agency/src/sdk/manifest_model.py:PluginManifest → plugin.yaml
 *
 * Uso:  node scripts/gen-schemas.mjs   (desde vscode-hubara/)
 */
import { execFileSync } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(HERE, "..", "..");
const OUT_DIR = path.join(HERE, "..", "schemas");

function runPython(cmd, args, cwd, code) {
  const out = execFileSync(cmd, [...args, "-c", code], { cwd, encoding: "utf-8" });
  return JSON.parse(out);
}

function write(name, schema) {
  const target = path.join(OUT_DIR, name);
  fs.writeFileSync(target, JSON.stringify(schema, null, 2) + "\n");
  console.log(`  wrote ${path.relative(process.cwd(), target)}`);
}

console.log("GraphAgents (AgentNode + ToolContract)…");
const gaSchemas = runPython(
  "python3",
  [],
  path.join(ROOT, "GraphAgents"),
  "import json\n" +
    "from sdk.manifest_model import AgentNode\n" +
    "from sdk.tool_model import ToolContract\n" +
    "print(json.dumps({'agent': AgentNode.model_json_schema(), 'tool': ToolContract.model_json_schema()}))\n",
);
write("graphagents.agent.schema.json", { ...gaSchemas.agent, title: "GraphAgents agent/taskgraph manifest" });
write("graphagents.tool.schema.json", gaSchemas.tool);

console.log("Hubara backend (PluginManifest)…");
const hubSchema = runPython(
  "uv",
  ["run", "python"],
  path.join(ROOT, "hubara_agency"),
  "import json\nfrom src.sdk.manifest_model import PluginManifest\nprint(json.dumps(PluginManifest.model_json_schema()))\n",
);
write("hubara.plugin.schema.json", hubSchema);

console.log("listo.");
