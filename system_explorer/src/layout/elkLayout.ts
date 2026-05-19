// Auto-layout via elkjs. Algoritmo `layered` con dirección LR — apropiado
// para "plugins → contribuciones → endpoints" leído de izquierda a derecha.
//
// elkjs es ~600 KB y bloquea ~50-200ms para 200 nodos en main thread. Para
// V1 lo corremos en main thread porque el grafo es chico. Si crece >300
// nodos, mover a Web Worker (ver xyflow docs).

import ELK from "elkjs/lib/elk.bundled.js";
import type { Edge, Node } from "@xyflow/react";

const elk = new ELK();

// Tamaños "fijos" para que ELK reserve espacio. Si los nodos tienen tamaño
// dinámico (CSS auto), pasar las dimensiones reales después de mount.
// V1: usamos defaults razonables que matchean los custom node JSX.
const NODE_WIDTH_BY_KIND: Record<string, number> = {
  plugin: 220,
  section: 180,
  sidebar: 180,
  api_router: 220,
  api_endpoint: 180,
  worker: 200,
  task_queue: 160,
};

const NODE_HEIGHT_BY_KIND: Record<string, number> = {
  plugin: 100,
  section: 70,
  sidebar: 70,
  api_router: 80,
  api_endpoint: 60,
  worker: 90,
  task_queue: 60,
};

const ELK_OPTIONS = {
  "elk.algorithm": "layered",
  "elk.direction": "RIGHT",
  "elk.layered.spacing.nodeNodeBetweenLayers": "80",
  "elk.spacing.nodeNode": "40",
  "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
  "elk.edgeRouting": "ORTHOGONAL",
};

export async function layoutGraph(
  nodes: Node[],
  edges: Edge[],
): Promise<Node[]> {
  if (nodes.length === 0) return nodes;

  const elkGraph = {
    id: "root",
    layoutOptions: ELK_OPTIONS,
    children: nodes.map((n) => {
      const kind = (n.data as { kind?: string }).kind ?? "plugin";
      return {
        id: n.id,
        width: NODE_WIDTH_BY_KIND[kind] ?? 200,
        height: NODE_HEIGHT_BY_KIND[kind] ?? 80,
      };
    }),
    edges: edges.map((e) => ({
      id: e.id,
      sources: [e.source],
      targets: [e.target],
    })),
  };

  const result = await elk.layout(elkGraph);
  const positions = new Map<string, { x: number; y: number }>();
  for (const c of result.children ?? []) {
    positions.set(c.id, { x: c.x ?? 0, y: c.y ?? 0 });
  }

  return nodes.map((n) => {
    const pos = positions.get(n.id);
    return pos ? { ...n, position: pos } : n;
  });
}
