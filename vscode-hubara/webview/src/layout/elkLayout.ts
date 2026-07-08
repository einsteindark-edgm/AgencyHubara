// `elk.bundled.js` corre el algoritmo sincrónicamente en el hilo principal
// (sin Web Worker) — la variante segura para una webview con CSP estricta.
// eslint-disable-next-line @typescript-eslint/no-var-requires
import ELK from "elkjs/lib/elk.bundled.js";

export interface Positioned {
  x: number;
  y: number;
}

const elk = new ELK();

/** Layout layered (ELK, dirección RIGHT) — el que usa el system_explorer. */
export async function elkLayout(
  nodes: Array<{ id: string; width: number; height: number }>,
  edges: Array<{ source: string; target: string }>,
): Promise<Map<string, Positioned>> {
  const nodeIds = new Set(nodes.map((n) => n.id));
  const graph = {
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.spacing.nodeNode": "40",
      "elk.layered.spacing.nodeNodeBetweenLayers": "70",
    },
    children: nodes.map((n) => ({ id: n.id, width: n.width, height: n.height })),
    edges: edges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map((e, i) => ({ id: `e${i}`, sources: [e.source], targets: [e.target] })),
  };
  const result = await elk.layout(graph);
  const positions = new Map<string, Positioned>();
  for (const child of result.children ?? []) {
    if (child.id) {
      positions.set(child.id, { x: child.x ?? 0, y: child.y ?? 0 });
    }
  }
  return positions;
}
