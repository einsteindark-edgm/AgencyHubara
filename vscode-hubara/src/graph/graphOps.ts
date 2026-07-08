import { GraphEdge, GraphNode, GraphPayload, Provider } from "../bridge/endpoints";

/**
 * Álgebra pura del grafo: namespacing por sistema, merge del workspace con
 * costuras declaradas, y ego-graph (focus scope). Cero dependencias de DOM
 * ni `vscode` — importable por extensión y webview, testeable standalone.
 */

export const NS_PREFIX: Record<Provider, string> = { graphagents: "ga", systemmap: "hub" };

export interface NamespacedNode extends GraphNode {
  /** id namespaced, p. ej. "ga:agent:ctwa-report" — único en el workspace. */
  nsId: string;
  system: Provider;
  /** id tal cual lo devuelve el bridge, sin namespacing. */
  rawId: string;
  /** presente cuando el nodo sobrevive DENTRO de un sistema colapsado por ser
   * endpoint de una costura: sub-caja del nodo-cluster (subflow de React Flow). */
  parentCluster?: string;
}

export interface NamespacedEdge extends GraphEdge {
  nsSource: string;
  nsTarget: string;
  /** ausente en edges de costura (cruzan dos sistemas, no pertenecen a uno). */
  system?: Provider;
}

export function namespaceGraph(
  system: Provider,
  payload: GraphPayload,
): { nodes: NamespacedNode[]; edges: NamespacedEdge[] } {
  const prefix = NS_PREFIX[system];
  // `id`/`source`/`target` también se normalizan al espacio namespaced: todo
  // consumidor aguas abajo (layout, ego-graph, React Flow) opera en UN solo
  // espacio de ids; el id crudo del bridge queda en `rawId`.
  const nodes: NamespacedNode[] = payload.nodes.map((n) => ({
    ...n,
    rawId: n.id,
    id: `${prefix}:${n.id}`,
    nsId: `${prefix}:${n.id}`,
    system,
  }));
  const edges: NamespacedEdge[] = payload.edges.map((e) => ({
    ...e,
    source: `${prefix}:${e.source}`,
    target: `${prefix}:${e.target}`,
    nsSource: `${prefix}:${e.source}`,
    nsTarget: `${prefix}:${e.target}`,
    system,
  }));
  return { nodes, edges };
}

/** Quita el prefijo de namespace de `system` a un nsId; null si no matchea.
 * Única fuente de la cirugía de prefijos — no hardcodear "ga:"/"hub:". */
export function stripNs(system: Provider, nsId: string): string | null {
  const prefix = `${NS_PREFIX[system]}:`;
  return nsId.startsWith(prefix) ? nsId.slice(prefix.length) : null;
}

/** Una costura cross-sistema declarada (`seams.yaml`) — ver §2.6 del plan. */
export interface Seam {
  id: string;
  /** id namespaced del origen, p. ej. "hub:plugin:reengagement". */
  from: string;
  /** id namespaced del destino, p. ej. "ga:agent:window-strategist". */
  to: string;
  label?: string;
  kind?: string;
}

interface MinimalNode {
  id: string;
}
interface MinimalEdge {
  source: string;
  target: string;
}

/** BFS no dirigida a `depth` saltos desde `centerId`. Funciona sobre
 * cualquier par nodos/edges cuyos `id`/`source`/`target` compartan espacio de
 * IDs — se usa tal cual con los ids crudos del bridge (focus dentro de UN
 * sistema; el workspace no soporta focus cross-sistema). */
export function egoGraph<N extends MinimalNode, E extends MinimalEdge>(
  nodes: N[],
  edges: E[],
  centerId: string,
  depth: number,
): { nodes: N[]; edges: E[] } {
  const adjacency = new Map<string, Set<string>>();
  const touch = (a: string, b: string) => {
    let s = adjacency.get(a);
    if (!s) {
      s = new Set();
      adjacency.set(a, s);
    }
    s.add(b);
  };
  for (const e of edges) {
    touch(e.source, e.target);
    touch(e.target, e.source);
  }

  const visited = new Set<string>([centerId]);
  let frontier = new Set<string>([centerId]);
  for (let hop = 0; hop < depth && frontier.size > 0; hop++) {
    const next = new Set<string>();
    for (const id of frontier) {
      for (const neighbor of adjacency.get(id) ?? []) {
        if (!visited.has(neighbor)) {
          visited.add(neighbor);
          next.add(neighbor);
        }
      }
    }
    frontier = next;
  }

  const resultNodes = nodes.filter((n) => visited.has(n.id));
  const resultEdges = edges.filter((e) => visited.has(e.source) && visited.has(e.target));
  return { nodes: resultNodes, edges: resultEdges };
}

/** El subgrafo de UN workflow: todo lo ALCANZABLE desde `rootId` siguiendo
 * las aristas en su dirección (supervisor → agente → tool → port). A
 * diferencia del ego-graph (BFS no dirigida a N saltos), esto es la clausura
 * dirigida completa — "el flujo que de verdad cuelga de esta raíz". */
export function reachableGraph<N extends MinimalNode, E extends MinimalEdge>(
  nodes: N[],
  edges: E[],
  rootId: string,
): { nodes: N[]; edges: E[] } {
  const out = new Map<string, string[]>();
  for (const e of edges) {
    const list = out.get(e.source);
    if (list) {
      list.push(e.target);
    } else {
      out.set(e.source, [e.target]);
    }
  }
  const visited = new Set<string>([rootId]);
  const stack = [rootId];
  while (stack.length > 0) {
    const id = stack.pop()!;
    for (const next of out.get(id) ?? []) {
      if (!visited.has(next)) {
        visited.add(next);
        stack.push(next);
      }
    }
  }
  return {
    nodes: nodes.filter((n) => visited.has(n.id)),
    edges: edges.filter((e) => visited.has(e.source) && visited.has(e.target)),
  };
}

export interface WorkspaceGraph {
  nodes: NamespacedNode[];
  /** edges internos de cada sistema (namespaced). */
  edges: NamespacedEdge[];
  /** costuras que resolvieron contra nodos existentes en ambos lados. */
  seamEdges: NamespacedEdge[];
  /** costuras declaradas en seams.yaml cuyo from/to no existe en el grafo
   * actual — se reportan, NUNCA rompen el merge (honestidad sin crashear). */
  brokenSeams: Seam[];
}

/** El grafo del scope "workspace": ambos sistemas namespaced + sus costuras
 * declaradas. Tolerante a que un lado falte (bridge caído): esa mitad sale
 * vacía, no se cae el merge entero. */
export function mergeWorkspaceGraph(
  graphagents: GraphPayload | null,
  systemmap: GraphPayload | null,
  seams: Seam[],
): WorkspaceGraph {
  const gaNs = graphagents ? namespaceGraph("graphagents", graphagents) : { nodes: [], edges: [] };
  const hubNs = systemmap ? namespaceGraph("systemmap", systemmap) : { nodes: [], edges: [] };
  const nodes = [...gaNs.nodes, ...hubNs.nodes];
  const edges = [...gaNs.edges, ...hubNs.edges];
  const nodeIds = new Set(nodes.map((n) => n.nsId));

  const seamEdges: NamespacedEdge[] = [];
  const brokenSeams: Seam[] = [];
  for (const seam of seams) {
    if (nodeIds.has(seam.from) && nodeIds.has(seam.to)) {
      seamEdges.push({
        source: seam.from,
        target: seam.to,
        kind: seam.kind ?? "seam",
        nsSource: seam.from,
        nsTarget: seam.to,
        label: seam.label,
      });
    } else {
      brokenSeams.push(seam);
    }
  }
  return { nodes, edges, seamEdges, brokenSeams };
}

/** Namespaced id → provider "dueño", para resolver clicks/colapso por cluster. */
export function systemOfNsId(nsId: string): Provider | null {
  if (nsId.startsWith(`${NS_PREFIX.graphagents}:`)) {
    return "graphagents";
  }
  if (nsId.startsWith(`${NS_PREFIX.systemmap}:`)) {
    return "systemmap";
  }
  return null;
}

/** Colapsa un cluster: sus nodos internos desaparecen y un nodo-cluster los
 * reemplaza — EXCEPTO los endpoints de costuras, que sobreviven como
 * sub-cajas DENTRO del cluster (`parentCluster`) para que la línea de cada
 * costura siga apuntando al subsistema REAL que la origina/recibe, no a la
 * caja grande anónima. Costuras hacia un endpoint que no sobrevivió (no
 * debería pasar — mergeWorkspaceGraph solo resuelve contra nodos existentes)
 * se re-enrutan al cluster como red de seguridad. */
export function collapseCluster(
  graph: WorkspaceGraph,
  system: Provider,
  clusterNodeId: string,
): WorkspaceGraph {
  const seamTouched = new Set<string>();
  for (const e of graph.seamEdges) {
    for (const endpoint of [e.nsSource, e.nsTarget]) {
      if (systemOfNsId(endpoint) === system) {
        seamTouched.add(endpoint);
      }
    }
  }
  const kept: NamespacedNode[] = [];
  let hidden = 0;
  for (const n of graph.nodes) {
    if (n.system !== system) {
      kept.push(n);
    } else if (seamTouched.has(n.nsId)) {
      kept.push({ ...n, parentCluster: clusterNodeId });
    } else {
      hidden++;
    }
  }
  const clusterNode: NamespacedNode = {
    id: clusterNodeId,
    nsId: clusterNodeId,
    rawId: clusterNodeId,
    system,
    kind: "cluster",
    label: `${system === "graphagents" ? "GraphAgents" : "System Map"} (${hidden + seamTouched.size})`,
    collapsedCount: hidden,
  };
  const reroute = (id: string) => (systemOfNsId(id) === system && !seamTouched.has(id) ? clusterNodeId : id);
  const rerouteEdge = <E extends NamespacedEdge>(e: E): E => ({
    ...e,
    source: reroute(e.source),
    target: reroute(e.target),
    nsSource: reroute(e.nsSource),
    nsTarget: reroute(e.nsTarget),
  });
  const edges = graph.edges
    .filter((e) => e.system !== system) // los edges INTERNOS del cluster colapsado se ocultan
    .map(rerouteEdge);
  const seamEdges = graph.seamEdges.map(rerouteEdge);
  return { nodes: [...kept, clusterNode], edges, seamEdges, brokenSeams: graph.brokenSeams };
}
