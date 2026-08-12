/**
 * Contrato de mensajes del webview `exportview` (la pantalla visual de
 * relaciones del export). SIN imports de `vscode` — lo comparten el panel
 * (extension host) y el bundle React (webview), como `forge/messages.ts`.
 *
 * El id de nodo es COMPUESTO (`plugin:<id>` / `graphagent:<id>`) para que un
 * plugin y un graph agent que casualmente compartan id no colisionen en el
 * grafo. El panel lo parsea de vuelta al confirmar.
 */

export type UnitKind = "plugin" | "graphagent";

export interface ExportNode {
  /** id compuesto: `${unitKind}:${rawId}`. */
  id: string;
  unitKind: UnitKind;
  rawId: string;
  label: string;
  archetype?: string;
  /** por qué está en el grafo. */
  relation: "root" | "dep" | "seam";
  /** si relation === "seam": el plugin que lanza este graph agent. */
  seamFrom?: string;
  seamLabel?: string;
  /** dependencia DURA: destildarlo deja incompleta la funcionalidad. */
  required: boolean;
}

export interface ExportEdge {
  source: string; // id compuesto
  target: string; // id compuesto
  kind: "depends_on" | "seam" | "agent";
}

export interface ExportGraph {
  nodes: ExportNode[];
  edges: ExportEdge[];
  packageName: string;
}

/** extensión → webview */
export type ExportOutbound = { type: "graph" } & ExportGraph;

/** webview → extensión */
export type ExportInbound =
  | { type: "ready" }
  | { type: "confirm"; selected: string[] } // ids compuestos marcados
  | { type: "cancel" };
