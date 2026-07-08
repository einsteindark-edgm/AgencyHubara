/**
 * Tipos de los payloads que viajan por el puente stdio.
 *
 * Deliberadamente laxos en F0: el grafo se tipa fuerte en F1 cuando se porta
 * el canvas. Acá solo lo justo para listar nodos/edges y detectar errores.
 */

export type Provider = "graphagents" | "systemmap";

export interface BridgeRequest {
  id: number;
  method: "GET" | "POST";
  path: string;
  params?: Record<string, string | string[]>;
  body?: unknown;
}

export interface BridgeResponse {
  id: number | null;
  status: number;
  payload: unknown;
}

/** Handshake que el puente Python emite al arrancar. */
export interface BridgeReady {
  ready: true;
  root: string;
}

/** Nodo mínimo común a ambos grafos (ambos serializan React Flow-friendly). */
export interface GraphNode {
  id: string;
  kind: string;
  label?: string;
  [k: string]: unknown;
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  [k: string]: unknown;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  [k: string]: unknown;
}

export function isGraphPayload(p: unknown): p is GraphPayload {
  return (
    typeof p === "object" &&
    p !== null &&
    Array.isArray((p as GraphPayload).nodes) &&
    Array.isArray((p as GraphPayload).edges)
  );
}

/** Path space LÓGICO compartido: ambos puentes sirven las mismas rutas (el de
 * system_map acepta además sus paths FastAPI largos como alias), así la
 * extensión no carga tablas de paths por provider. */
export const GRAPH_PATH = "/api/graph";

export const PROVIDER_LABEL: Record<Provider, string> = {
  graphagents: "GraphAgents",
  systemmap: "System Map",
};
