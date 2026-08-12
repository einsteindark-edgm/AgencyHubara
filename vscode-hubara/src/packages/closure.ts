import { ExportEdge, ExportGraph, ExportNode } from "../export/messages";
import { NS_PREFIX, Seam } from "../graph/graphOps";

/**
 * Resolución de la clausura CROSS-SISTEMA para el export.
 *
 * Los CLIs de hubara y GraphAgents resuelven la clausura DENTRO de su sistema
 * (plugin `depends_on` · graph agent `agent://`), pero el seam plugin↔graphagent
 * (declarado en `seams.yaml`) lo cruza SOLO Studio. Sin esto, exportar
 * `order_sentinel` sale sin su graph agent `order-sentinel` — un paquete que
 * "no funciona": el plugin lanza el agente por el puente execution-id, y sin el
 * agente en el destino la funcionalidad queda rota.
 *
 * Puras — sin `vscode`, sin filesystem. Testeables standalone.
 */

export interface SeamEndpoint {
  /** namespace: "hub" (System Map) | "ga" (GraphAgents). */
  ns: string;
  /** "plugin" | "agent" | "taskgraph" | "tool" | "port" | … */
  kind: string;
  id: string;
}

/** Parsea un id namespaced de seam (`hub:plugin:ads`). null si no matchea. */
export function parseSeamEndpoint(nsId: string): SeamEndpoint | null {
  const parts = nsId.split(":");
  if (parts.length < 3 || !parts[0] || !parts[1] || !parts.slice(2).join(":")) {
    return null;
  }
  return { ns: parts[0], kind: parts[1], id: parts.slice(2).join(":") };
}

export interface CrossLink {
  /** graph agent que el plugin necesita para funcionar. */
  agent: string;
  /** plugin que lo lanza (origen del seam). */
  fromPlugin: string;
  label?: string;
}

/**
 * Graph agents que los `pluginIds` dados lanzan (seam `hub:plugin:X →
 * ga:agent:Y`). Es la dependencia dura que el export debe arrastrar: el plugin
 * no funciona sin su agente. Dedup por agente (varios plugins podrían lanzar el
 * mismo). Dirección estricta: solo plugin→agent (un graph agent NO arrastra al
 * plugin que lo lanza — es una unidad válida por sí sola).
 */
export function crossAgentsForPlugins(pluginIds: string[], seams: Seam[]): CrossLink[] {
  const plugins = new Set(pluginIds);
  const out: CrossLink[] = [];
  const seen = new Set<string>();
  for (const s of seams) {
    const from = parseSeamEndpoint(s.from);
    const to = parseSeamEndpoint(s.to);
    if (!from || !to) {
      continue;
    }
    const pluginSource =
      from.ns === NS_PREFIX.systemmap && from.kind === "plugin" && plugins.has(from.id);
    const agentTarget =
      to.ns === NS_PREFIX.graphagents && (to.kind === "agent" || to.kind === "taskgraph");
    if (pluginSource && agentTarget && !seen.has(to.id)) {
      seen.add(to.id);
      out.push({ agent: to.id, fromPlugin: from.id, label: s.label });
    }
  }
  return out;
}

/**
 * Costuras que quedarían ROTAS por la selección: un plugin incluido cuyo graph
 * agent (que lanza) NO está en la selección. Es lo que dispara la advertencia
 * de "funcionalidad incompleta" antes de sellar.
 */
export function brokenCrossLinks(
  selectedPlugins: string[],
  selectedAgents: string[],
  crossLinks: CrossLink[],
): CrossLink[] {
  const plugins = new Set(selectedPlugins);
  const agents = new Set(selectedAgents);
  return crossLinks.filter((c) => plugins.has(c.fromPlugin) && !agents.has(c.agent));
}

// ---------------------------------------------------------------------------
// Grafo visual del export — nodos (plugins + graph agents de la clausura) +
// aristas (depends_on · seam ⚡ · agent://). Puro: la UI (React Flow) solo lo
// dibuja. El id de nodo es COMPUESTO para no colisionar plugin↔graphagent.
// ---------------------------------------------------------------------------

export interface PluginUnitLike {
  id: string;
  archetype: string;
  requires: { plugins: string[] };
}
export interface AgentUnitLike {
  id: string;
  archetype: string;
  requires: { agents: string[] };
}

export const pluginNodeId = (id: string): string => `plugin:${id}`;
export const agentNodeId = (id: string): string => `graphagent:${id}`;

/** Parsea el id compuesto de vuelta a {unitKind, rawId}. null si no matchea. */
export function parseNodeId(nodeId: string): { unitKind: "plugin" | "graphagent"; rawId: string } | null {
  if (nodeId.startsWith("plugin:")) {
    return { unitKind: "plugin", rawId: nodeId.slice("plugin:".length) };
  }
  if (nodeId.startsWith("graphagent:")) {
    return { unitKind: "graphagent", rawId: nodeId.slice("graphagent:".length) };
  }
  return null;
}

export function buildExportGraph(
  pluginUnits: PluginUnitLike[],
  agentUnits: AgentUnitLike[],
  crossLinks: CrossLink[],
  rootIds: string[],
  packageName: string,
): ExportGraph {
  const roots = new Set(rootIds);
  const seamByAgent = new Map(crossLinks.map((c) => [c.agent, c]));
  const pluginIds = new Set(pluginUnits.map((u) => u.id));
  const agentIds = new Set(agentUnits.map((u) => u.id));
  const nodes: ExportNode[] = [];
  const edges: ExportEdge[] = [];

  for (const u of pluginUnits) {
    nodes.push({
      id: pluginNodeId(u.id),
      unitKind: "plugin",
      rawId: u.id,
      label: u.id,
      archetype: u.archetype,
      relation: roots.has(u.id) ? "root" : "dep",
      required: false,
    });
    for (const dep of u.requires.plugins) {
      if (pluginIds.has(dep)) {
        edges.push({ source: pluginNodeId(u.id), target: pluginNodeId(dep), kind: "depends_on" });
      }
    }
  }

  for (const u of agentUnits) {
    const link = seamByAgent.get(u.id);
    nodes.push({
      id: agentNodeId(u.id),
      unitKind: "graphagent",
      rawId: u.id,
      label: u.id,
      archetype: u.archetype,
      relation: link ? "seam" : roots.has(u.id) ? "root" : "dep",
      seamFrom: link?.fromPlugin,
      seamLabel: link?.label,
      required: Boolean(link), // el seam es dependencia dura del plugin
    });
    for (const dep of u.requires.agents) {
      if (agentIds.has(dep)) {
        edges.push({ source: agentNodeId(u.id), target: agentNodeId(dep), kind: "agent" });
      }
    }
  }

  // aristas de seam: plugin → graph agent (la relación cross-system)
  for (const c of crossLinks) {
    if (pluginIds.has(c.fromPlugin) && agentIds.has(c.agent)) {
      edges.push({ source: pluginNodeId(c.fromPlugin), target: agentNodeId(c.agent), kind: "seam" });
    }
  }

  return { nodes, edges, packageName };
}
