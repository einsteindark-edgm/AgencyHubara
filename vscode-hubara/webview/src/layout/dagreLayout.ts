import dagre from "dagre";

export interface Positioned {
  x: number;
  y: number;
}

/** Layout jerárquico (dagre) — el que usa el viewer de GraphAgents (`rankdir:TB`). */
export function dagreLayout(
  nodes: Array<{ id: string; width: number; height: number }>,
  edges: Array<{ source: string; target: string }>,
  rankdir: "TB" | "LR" = "TB",
): Map<string, Positioned> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir, nodesep: 40, ranksep: 70 });
  g.setDefaultEdgeLabel(() => ({}));
  for (const n of nodes) {
    g.setNode(n.id, { width: n.width, height: n.height });
  }
  for (const e of edges) {
    if (g.hasNode(e.source) && g.hasNode(e.target)) {
      g.setEdge(e.source, e.target);
    }
  }
  dagre.layout(g);
  const positions = new Map<string, Positioned>();
  for (const n of nodes) {
    const gn = g.node(n.id);
    if (gn) {
      positions.set(n.id, { x: gn.x - n.width / 2, y: gn.y - n.height / 2 });
    }
  }
  return positions;
}
