#!/usr/bin/env node
// Generador determinista de la documentación de los pipelines Archon de Hubara.
// Lee data/<id>.json (modelo verificado) + prose/<id>.md (narrativa) y emite:
//   - data.js          (window.PIPELINES + window.PIPELINE_ORDER, consumido por index.html)
//   - <id>.md          (referencia detallada por pipeline: fases, nodos, conexiones, prosa)
//   - README.md        (overview + tabla comparativa + metodología)
//
// Uso:  node gen.mjs
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DIR = path.dirname(fileURLToPath(import.meta.url));
const DATA = path.join(DIR, "data");
const PROSE = path.join(DIR, "prose");

const ORDER = [
  "hu-hubara-pipeline",
  "hu-hubara-plugin-pipeline",
  "review-pr-hubara",
  "idea-a-hu-hubara",
];

// ---------- load ----------
function loadPipeline(id) {
  const f = path.join(DATA, id + ".json");
  if (!fs.existsSync(f)) return null;
  try { return JSON.parse(fs.readFileSync(f, "utf8")); }
  catch (e) { console.error("  ! JSON inválido en " + f + ": " + e.message); return null; }
}
function loadProse(id) {
  const f = path.join(PROSE, id + ".md");
  return fs.existsSync(f) ? fs.readFileSync(f, "utf8").trim() : "";
}

// ---------- helpers ----------
function nodeCategory(n) {
  if (n.is_cancel) return "cancel";
  if (n.is_gate) return "gate";
  const t = (n.type || "").toLowerCase();
  if (t.includes("command")) return "command";
  if (t.includes("skill")) return "skills";
  if (t.includes("script")) return "script";
  if (t.includes("manual")) return "manual";
  if (t.includes("sub") || t.includes("workflow")) return "subworkflow";
  if (t.includes("bash")) return "bash";
  return "other";
}
function mdEsc(s) { return (s == null ? "" : String(s)).replace(/\|/g, "\\|").replace(/\n+/g, " "); }
function code(s) { return s ? "`" + String(s).replace(/`/g, "ʼ") + "`" : ""; }
function safeKey(id) { return "n_" + String(id).replace(/[^A-Za-z0-9]/g, "_"); }
function mmLabel(s) { return String(s).replace(/["()|\[\]{}<>]/g, "").replace(/\$/g, "").slice(0, 28); }
// Truncación que respeta límites de palabra (para etiquetas de fase más largas).
function mmLabelLong(s, n) {
  s = String(s).replace(/["()|\[\]{}<>]/g, "").replace(/\$/g, "").trim();
  if (s.length <= n) return s;
  const cut = s.slice(0, n);
  const sp = cut.lastIndexOf(" ");
  return (sp > n * 0.6 ? cut.slice(0, sp) : cut).trim() + "…";
}
function badge(n) {
  const b = [];
  if (n.is_gate) b.push("◆gate");
  if (n.is_cancel) b.push("✕cancel");
  if (n.loop) b.push("↻loop");
  return b.join(" ");
}

// ---------- mermaid (phase-level always; full graph if small) ----------
function phaseMermaid(p) {
  const phases = p.phases || [];
  if (!phases.length) return "";
  let out = "```mermaid\nflowchart LR\n";
  phases.forEach((ph, i) => {
    out += "  P" + i + '["' + mmLabelLong(ph.name, 44) + "\\n(" + (ph.nodes || []).length + ' nodos)"]\n';
  });
  for (let i = 0; i < phases.length - 1; i++) out += "  P" + i + " --> P" + (i + 1) + "\n";
  out += "```\n";
  return out;
}
function fullMermaid(p) {
  const nodes = p.nodes || [];
  if (nodes.length > 46) return ""; // demasiado grande para markdown; usar el visor HTML
  const idset = new Set(nodes.map((n) => n.id));
  let out = "```mermaid\nflowchart TD\n";
  nodes.forEach((n) => {
    const cat = nodeCategory(n);
    const shape = cat === "gate" ? ["{{", "}}"] : cat === "cancel" ? ["[/", "/]"] : ["[", "]"];
    out += "  " + safeKey(n.id) + shape[0] + '"' + mmLabel(n.id) + '"' + shape[1] + "\n";
  });
  (p.edges || []).forEach((e) => {
    if (!idset.has(e.from) || !idset.has(e.to)) return;
    const lbl = e.condition ? "|" + mmLabel(e.condition) + "|" : "";
    const arr = e.kind === "cancel" ? "-.->" : e.kind === "loop-back" ? "-.->" : "-->";
    out += "  " + safeKey(e.from) + " " + arr + lbl + " " + safeKey(e.to) + "\n";
  });
  // class styling
  out += "  classDef gate fill:#3a2d05,stroke:#d29922,color:#fff;\n";
  out += "  classDef cancel fill:#3a0d0b,stroke:#f85149,color:#fff;\n";
  nodes.forEach((n) => {
    const c = nodeCategory(n);
    if (c === "gate") out += "  class " + safeKey(n.id) + " gate;\n";
    if (c === "cancel") out += "  class " + safeKey(n.id) + " cancel;\n";
  });
  out += "```\n";
  return out;
}

// ---------- per-pipeline markdown ----------
function pipelineDoc(p, prose) {
  const nodes = p.nodes || [];
  const edges = p.edges || [];
  const phases = p.phases || [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  const L = [];
  L.push("# " + (p.title || p.pipeline_id));
  L.push("");
  L.push("> **`" + p.pipeline_id + ".yaml`** · " + nodes.length + " nodos · " + edges.length + " conexiones · " + phases.length + " fases");
  L.push("> ");
  L.push("> Generado por extracción + **verificación adversarial** (doble lectura independiente del YAML). Fuente de verdad: el YAML. Visor interactivo: [`index.html`](./index.html).");
  L.push("");
  L.push("## Propósito");
  L.push("");
  L.push(p.purpose || "_(sin descripción)_");
  if (p.trigger) { L.push(""); L.push("**Trigger / invocación:** `" + p.trigger + "`"); }
  if (p.inputs && p.inputs.length) { L.push(""); L.push("**Inputs:** " + p.inputs.map((x) => code(x)).join(", ")); }
  if (p.global_logic) { L.push(""); L.push("## Lógica global, invariantes y env vars"); L.push(""); L.push(p.global_logic); }

  // phase-level diagram
  const pm = phaseMermaid(p);
  if (pm) { L.push(""); L.push("## Mapa de fases"); L.push(""); L.push(pm); }

  // full graph if small enough
  const fm = fullMermaid(p);
  if (fm) {
    L.push("");
    L.push("## Grafo completo");
    L.push("");
    L.push("<sub>◆ = gate · borde rojo / `-.->` = cancelación · `-.->` punteado = loop-back. Para el grafo navegable usá [`index.html`](./index.html).</sub>");
    L.push("");
    L.push(fm);
  } else {
    L.push("");
    L.push("> ℹ️ El grafo completo (" + nodes.length + " nodos) es demasiado grande para renderizar inline. Abrí [`index.html`](./index.html) para verlo navegable.");
  }

  // reference table
  L.push("");
  L.push("## Tabla de nodos (referencia rápida)");
  L.push("");
  L.push("| # | Nodo | Tipo | Flags | depends_on | when |");
  L.push("|---|------|------|-------|-----------|------|");
  nodes.forEach((n, i) => {
    L.push("| " + (i + 1) + " | `" + n.id + "` | " + mdEsc(n.type || "") + " | " + (badge(n) || "—") + " | " +
      ((n.depends_on || []).map((d) => "`" + d + "`").join(", ") || "—") + " | " +
      (n.when ? "`" + mdEsc(n.when) + "`" : "—") + " |");
  });

  // detail by phase
  L.push("");
  L.push("## Nodos en detalle (por fase)");
  const seen = new Set();
  phases.forEach((ph) => {
    L.push("");
    L.push("### Fase · " + ph.name);
    if (ph.description) { L.push(""); L.push("_" + ph.description + "_"); }
    (ph.nodes || []).forEach((id) => {
      const n = byId.get(id);
      if (!n) { L.push(""); L.push("- ⚠️ `" + id + "` referenciado en la fase pero no existe como nodo."); return; }
      seen.add(id);
      L.push(nodeBlock(n, p));
    });
  });
  // nodes not in any phase
  const orphans = nodes.filter((n) => !seen.has(n.id));
  if (orphans.length) {
    L.push("");
    L.push("### Fase · (sin clasificar)");
    orphans.forEach((n) => L.push(nodeBlock(n, p)));
  }

  // connections table
  L.push("");
  L.push("## Conexiones (aristas)");
  L.push("");
  L.push("Cada arista es un par `depends_on → nodo`. `kind`: sequence (secuencia normal) · gate (la condición `when` enruta) · cancel (va a un nodo de cancelación) · loop-back (reintento) · fan-out/fan-in (sub-pipelines).");
  L.push("");
  L.push("| Desde | Hacia | kind | Condición (when) |");
  L.push("|-------|-------|------|------------------|");
  edges.forEach((e) => {
    L.push("| `" + e.from + "` | `" + e.to + "` | " + (e.kind || "") + " | " + (e.condition ? "`" + mdEsc(e.condition) + "`" : "—") + " |");
  });

  // verification notes
  if (p.verification_notes && p.verification_notes.length) {
    L.push("");
    L.push("## Notas de verificación (segunda lectura independiente)");
    L.push("");
    p.verification_notes.forEach((v) => L.push("- " + v));
  }

  // prose narrative
  if (prose) {
    L.push("");
    L.push("---");
    L.push("");
    L.push("# Recorrido narrativo");
    L.push("");
    L.push(prose);
  }

  L.push("");
  return L.join("\n");
}

function nodeBlock(n, p) {
  const L = [];
  L.push("");
  L.push("#### `" + n.id + "`" + (badge(n) ? "  —  " + badge(n) : ""));
  L.push("");
  L.push("- **Tipo:** " + (n.type || "—") + (n.invokes ? " · invoca " + code(n.invokes) : ""));
  if (n.summary) L.push("- **Resumen:** " + n.summary);
  if (n.detail) L.push("- **Detalle:** " + n.detail);
  L.push("- **depends_on:** " + ((n.depends_on || []).map((d) => code(d)).join(", ") || "_(raíz)_"));
  L.push("- **trigger_rule:** " + code(n.trigger_rule || "all_success (default)"));
  if (n.when) L.push("- **when:** " + code(n.when));
  if (n.produces) L.push("- **produces:** " + n.produces);
  if (n.loop) L.push("- **loop:** " + code(n.loop));
  // dependents
  const deps = (p.edges || []).filter((e) => e.from === n.id && e.to !== "END").map((e) => e.to);
  if (deps.length) L.push("- **lo siguen:** " + deps.map((d) => code(d)).join(", "));
  if (n.notes) L.push("- **⚠️ notas:** " + n.notes);
  return L.join("\n");
}

// ---------- README ----------
function readmeDoc(loaded) {
  const L = [];
  L.push("# Pipelines Archon de Hubara — documentación de referencia");
  L.push("");
  L.push("Documentación **al detalle, nodo por nodo y conexión por conexión**, de los 4 workflows Archon que conforman el pipeline de Hubara. Pensada como base para **mejorar** estos pipelines y como **plantilla** para pipelines nuevos.");
  L.push("");
  L.push("## Cómo usar esta carpeta");
  L.push("");
  L.push("- **[`index.html`](./index.html)** — visor **interactivo** (abrí el archivo en el navegador). Grafo navegable por pipeline: zoom/pan, click en un nodo para ver su detalle completo, highlight de conexiones, búsqueda, tinte por fase, y un overview comparativo. Funciona offline (sin internet).");
  L.push("- **`<pipeline>.md`** — referencia **estática detallada** por pipeline: mapa de fases, tabla de nodos, cada nodo en detalle, tabla de conexiones, notas de verificación y un recorrido narrativo.");
  L.push("- **`data/<pipeline>.json`** — el modelo estructurado verificado (fuente de los dos anteriores).");
  L.push("- **`data.js`** — el mismo modelo empaquetado para el visor.");
  L.push("");
  L.push("## Los 4 pipelines");
  L.push("");
  L.push("| Pipeline | Rol | Nodos | Conexiones | Fases |");
  L.push("|----------|-----|:-----:|:----------:|:-----:|");
  ORDER.forEach((id) => {
    const p = loaded[id];
    if (!p) { L.push("| `" + id + "` | _(no generado)_ | — | — | — |"); return; }
    L.push("| **[`" + id + "`](./" + id + ".md)** | " + mdEsc(p.purpose || p.title || "") + " | " +
      (p.nodes || []).length + " | " + (p.edges || []).length + " | " + (p.phases || []).length + " |");
  });
  L.push("");
  L.push("### Cómo encajan");
  L.push("");
  L.push("```mermaid\nflowchart LR");
  L.push('  idea["idea-a-hu-hubara\\n(idea → issue HU)"]');
  L.push('  main["hu-hubara-pipeline\\n(issue → PR)"]');
  L.push('  plugin["hu-hubara-plugin-pipeline\\n(1 plugin, en multi_plugin)"]');
  L.push('  review["review-pr-hubara\\n(review post-PR)"]');
  L.push("  idea -->|crea issue| main");
  L.push("  main -->|fan-out multi_plugin| plugin");
  L.push("  main -->|abre PR| review");
  L.push("```");
  L.push("");
  L.push("## Metodología (por qué confiar en estos datos)");
  L.push("");
  L.push("Cada pipeline se modeló con un workflow multi-agente en 3 etapas por archivo:");
  L.push("");
  L.push("1. **Extracción** — un agente lee el YAML completo y emite un modelo estructurado (nodos, aristas, fases, condiciones `when` verbatim).");
  L.push("2. **Verificación adversarial** — un segundo agente **vuelve a leer el YAML desde cero**, reconstruye su propio modelo, lo contrasta contra el de la etapa 1 y emite la versión **autoritativa corregida** + las discrepancias encontradas (ver `verification_notes` en cada doc).");
  L.push("3. **Narrativa** — un tercer agente escribe el recorrido fase por fase, anclado al modelo verificado.");
  L.push("");
  L.push("> La fuente de verdad es siempre el YAML vivo. Si un YAML cambia, regenerá con `node gen.mjs` tras actualizar `data/<id>.json`.");
  L.push("");
  L.push("## Convenciones del modelo");
  L.push("");
  L.push("- **Nodo**: `id`, `type` (bash / script / command / skills / manual / sub-workflow), `depends_on`, `trigger_rule` (`all_success` por defecto · `all_done` · `one_success`), `when` (condición de guarda), `produces` (qué emite), `loop`, flags `is_gate` / `is_cancel`.");
  L.push("- **Arista**: un par `depends_on → nodo`. `condition` = el `when` que gobierna al destino. `kind` ∈ {sequence, gate, cancel, loop-back, fan-out, fan-in}.");
  L.push("- **Gate**: nodo cuyo valor de salida enruta la cadena (continuar vs cancelar).");
  L.push("- **Silent-hole**: estado de salida de un gate que no matchea ni un `when` de continuar ni uno de cancelar → la cadena downstream se skipea en silencio. Clase de bug recurrente; señalada en las notas.");
  L.push("");
  return L.join("\n");
}

// ---------- data.js ----------
function dataJs(loaded) {
  const obj = {};
  ORDER.forEach((id) => { if (loaded[id]) obj[id] = loaded[id]; });
  const present = ORDER.filter((id) => loaded[id]);
  return "/* Auto-generado por gen.mjs — no editar a mano. */\n" +
    "window.PIPELINE_ORDER = " + JSON.stringify(present) + ";\n" +
    "window.PIPELINES = " + JSON.stringify(obj) + ";\n";
}

// ---------- main ----------
const loaded = {};
ORDER.forEach((id) => { loaded[id] = loadPipeline(id); });
const present = ORDER.filter((id) => loaded[id]);
if (!present.length) {
  console.error("✗ No se encontró ningún data/<id>.json. Generá los modelos primero.");
  process.exit(1);
}

present.forEach((id) => {
  const doc = pipelineDoc(loaded[id], loadProse(id));
  fs.writeFileSync(path.join(DIR, id + ".md"), doc);
  console.log("  ✓ " + id + ".md (" + (loaded[id].nodes || []).length + " nodos)");
});
fs.writeFileSync(path.join(DIR, "README.md"), readmeDoc(loaded));
console.log("  ✓ README.md");
fs.writeFileSync(path.join(DIR, "data.js"), dataJs(loaded));
console.log("  ✓ data.js (" + present.length + " pipelines)");
console.log("Listo. Abrí docs/archon-workflows-hubara/index.html en el navegador.");
