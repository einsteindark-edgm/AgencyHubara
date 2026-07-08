import { NamespacedEdge, NamespacedNode } from "../../../src/graph/graphOps";
import { Scope } from "../../../src/graph/scope";
import { dagreLayout, Positioned } from "./dagreLayout";
import { elkLayout } from "./elkLayout";

export type { Positioned };

export const NODE_W = 190;
export const NODE_H = 68;
export const CLUSTER_GAP = 120;

interface MinimalNode {
  id: string;
  kind?: string;
}
interface MinimalEdge {
  source: string;
  target: string;
}

function widthOf(kind?: string): number {
  return kind === "cluster" ? 240 : NODE_W;
}
function heightOf(kind?: string): number {
  return kind === "cluster" ? 96 : NODE_H;
}

/** Layout de UN sistema: dagre (TB) para GraphAgents, ELK (layered RIGHT)
 * para System Map — el mismo algoritmo que usaba cada viewer original. */
async function layoutSingleSystem<N extends MinimalNode, E extends MinimalEdge>(
  system: "graphagents" | "systemmap",
  nodes: N[],
  edges: E[],
): Promise<Map<string, Positioned>> {
  const sized = nodes.map((n) => ({ id: n.id, width: widthOf(n.kind), height: heightOf(n.kind) }));
  if (system === "graphagents") {
    return dagreLayout(sized, edges, "TB");
  }
  return elkLayout(sized, edges);
}

function boundingMaxX(positions: Map<string, Positioned>, nodes: MinimalNode[]): number {
  let max = 0;
  for (const n of nodes) {
    const pos = positions.get(n.id);
    if (pos) {
      max = Math.max(max, pos.x + widthOf(n.kind));
    }
  }
  return max;
}

/**
 * Calcula posiciones por defecto para el scope actual. Para "workspace"
 * layoutea cada cluster con SU algoritmo y los coloca lado a lado (el
 * segundo offset por el ancho real del primero, no un valor fijo).
 */
export async function computeLayout(
  scope: Scope,
  nodes: NamespacedNode[],
  edges: NamespacedEdge[],
): Promise<Map<string, Positioned>> {
  if (scope.kind !== "workspace") {
    return layoutSingleSystem(scope.system, nodes, edges);
  }
  const gaNodes = nodes.filter((n) => n.system === "graphagents");
  const hubNodes = nodes.filter((n) => n.system === "systemmap");
  const gaIds = new Set(gaNodes.map((n) => n.id));
  const hubIds = new Set(hubNodes.map((n) => n.id));
  const gaEdges = edges.filter((e) => gaIds.has(e.source) && gaIds.has(e.target));
  const hubEdges = edges.filter((e) => hubIds.has(e.source) && hubIds.has(e.target));

  const [gaPositions, hubPositionsRaw] = await Promise.all([
    layoutSingleSystem("graphagents", gaNodes, gaEdges),
    layoutSingleSystem("systemmap", hubNodes, hubEdges),
  ]);

  const offsetX = gaNodes.length > 0 ? boundingMaxX(gaPositions, gaNodes) + CLUSTER_GAP : 0;
  const hubPositions = new Map<string, Positioned>();
  for (const [id, pos] of hubPositionsRaw) {
    hubPositions.set(id, { x: pos.x + offsetX, y: pos.y });
  }
  return new Map([...gaPositions, ...hubPositions]);
}
