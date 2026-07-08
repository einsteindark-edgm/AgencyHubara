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

/** Geometría del cluster COLAPSADO con sub-cajas (endpoints de costuras):
 * grilla de 2 columnas dentro del contenedor. Única fuente — la usan el
 * layout (tamaño del contenedor) y App (posición RELATIVA de cada hijo,
 * subflow de React Flow). */
export function clusterGrid(childCount: number): {
  width: number;
  height: number;
  childPos: (index: number) => Positioned;
} {
  const PAD = 16;
  const HEADER = 46; // header del contenedor (kind + label)
  const GAP = 12;
  const cols = childCount > 1 ? 2 : 1;
  const rows = Math.max(1, Math.ceil(childCount / cols));
  const width = Math.max(240, PAD * 2 + cols * NODE_W + (cols - 1) * GAP);
  const height = childCount === 0 ? 96 : HEADER + PAD + rows * NODE_H + (rows - 1) * GAP;
  return {
    width,
    height,
    childPos: (i) => ({ x: PAD + (i % cols) * (NODE_W + GAP), y: HEADER + Math.floor(i / cols) * (NODE_H + GAP) }),
  };
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
  const [ga, hub] = await Promise.all([
    layoutSide("graphagents", nodes, edges),
    layoutSide("systemmap", nodes, edges),
  ]);
  const offsetX = ga.width > 0 ? ga.width + CLUSTER_GAP : 0;
  const merged = new Map(ga.positions);
  for (const [id, pos] of hub.positions) {
    merged.set(id, { x: pos.x + offsetX, y: pos.y });
  }
  return merged;
}

/** Layout de UN lado del workspace. Si el sistema está COLAPSADO (hay un
 * nodo-cluster), el único nodo top-level es el contenedor — su tamaño sale de
 * clusterGrid y los hijos NO se posicionan acá (App les da la posición
 * relativa con la misma grilla). */
async function layoutSide(
  system: "graphagents" | "systemmap",
  nodes: NamespacedNode[],
  edges: NamespacedEdge[],
): Promise<{ positions: Map<string, Positioned>; width: number }> {
  const sideNodes = nodes.filter((n) => n.system === system);
  if (sideNodes.length === 0) {
    return { positions: new Map(), width: 0 };
  }
  const cluster = sideNodes.find((n) => n.kind === "cluster");
  if (cluster) {
    const childCount = sideNodes.filter((n) => n.parentCluster === cluster.id).length;
    return { positions: new Map([[cluster.id, { x: 0, y: 0 }]]), width: clusterGrid(childCount).width };
  }
  const ids = new Set(sideNodes.map((n) => n.id));
  const sideEdges = edges.filter((e) => ids.has(e.source) && ids.has(e.target));
  const positions = await layoutSingleSystem(system, sideNodes, sideEdges);
  return { positions, width: boundingMaxX(positions, sideNodes) };
}
