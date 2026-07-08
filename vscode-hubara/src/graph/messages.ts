import { GraphNode, GraphPayload, Provider } from "../bridge/endpoints";
import { Seam } from "./graphOps";
import { Scope } from "./scope";

export interface ProviderState {
  payload: GraphPayload | null;
  error: string | null;
}

export interface Position {
  x: number;
  y: number;
}

/** Vista persistida — la extensión es la fuente única (workspaceState); la
 * webview solo la refleja y emite cambios. Sobrevive reloads de la webview Y
 * de la ventana (a diferencia del `setState` local de F0, que solo sobrevivía
 * dentro de la misma vida del extension host). */
export interface PersistedViewState {
  scope: Scope;
  /** posiciones de nodos guardadas POR scope (scopeKey → nodeId → xy). */
  positionsByScopeKey: Record<string, Record<string, Position>>;
  /** clusters colapsados en el scope "workspace". */
  collapsedClusters: Provider[];
}

export const EMPTY_VIEW_STATE: PersistedViewState = {
  scope: { kind: "workspace" },
  positionsByScopeKey: {},
  collapsedClusters: [],
};

export interface InspectFile {
  path: string;
  role: string;
  abspath: string;
}

/** Un paso del `execution_plan` anotado con su estado de runtime en Conductor
 * (`sdk/trace.py:build_trace`) — el I/O crudo de `/api/trace`. */
export interface TraceStep {
  order?: number;
  agent: string;
  archetype?: string;
  role?: string;
  /** tools que el paso declara componer (del execution_plan). */
  tools?: string[];
  runtime?: {
    status: "done" | "running" | "pending" | "failed" | "other" | "awaiting";
    retries?: number;
    ms?: number;
    task_id?: string;
  };
}

export interface TraceInfo {
  executionId: string;
  workflowStatus: string;
  /** id del agente raíz (el supervisor del pod). */
  agent?: string;
  strategy?: string;
  /** el input con que arrancó el pod (desenvuelto de {acc: seed}). */
  seed?: unknown;
  steps: TraceStep[];
}

/** Una llamada real a una tool dentro de un nodo — I/O reconstruido por
 * replay determinista (`/api/flow-trace`, G-DET). */
export interface ToolCall {
  seq: number;
  tool: string;
  input: unknown;
  output: unknown;
}

/** El nodo tal como viaja del canvas al panel Ejecución (raw + system). */
export type SelectedNodePayload = GraphNode & { rawId: string };

/** Webview del CANVAS → extensión. */
export type InboundMessage =
  | { type: "ready" }
  | { type: "openFile"; path: string }
  | { type: "refresh" }
  | { type: "persistState"; state: PersistedViewState }
  /** click en un nodo — el detalle vive en el panel nativo "Ejecución" (F10). */
  | { type: "nodeSelected"; system: Provider; node: SelectedNodePayload }
  | { type: "stopTrace" }
  | { type: "connectRequest"; source: string; target: string }
  | { type: "disconnectRequest"; source: string; target: string; kind: string };

/** Extensión → webview del CANVAS. */
export type OutboundMessage =
  | {
      type: "bootstrap";
      graphagents: ProviderState;
      systemmap: ProviderState;
      seams: Seam[];
      restored: PersistedViewState;
    }
  | { type: "providerUpdate"; provider: Provider; state: ProviderState }
  | { type: "refreshing" }
  | { type: "jumpScope"; scope: Scope }
  | { type: "trace"; info: TraceInfo }
  | { type: "traceError"; message: string }
  | { type: "traceCleared" }
  /** I/O por-tool reconstruido de un run (`/api/flow-trace`) — el canvas lo
   * usa para el ORDEN real de las tools en los badges. */
  | {
      type: "flowTrace";
      executionId: string;
      nodeTraces: Record<string, ToolCall[]>;
      reconstructed: boolean;
      reason?: string;
    };

/** Webview del panel EJECUCIÓN → extensión. */
export type ExecInbound =
  | { type: "ready" }
  | { type: "openFile"; path: string }
  /** enfocar un nodo en el canvas (⌂ del Inspector / click en la cascada). */
  | { type: "focusNode"; system: Provider; nodeId: string }
  | { type: "inspectNode"; system: Provider; nodeId: string }
  /** el acc (estado acumulador) tras un nodo — lazy, al abrir entró/salió.
   * `key` la genera la webview y viaja de vuelta para matchear la respuesta. */
  | { type: "nodeStateRequest"; key: string; executionId: string; taskId: string };

/** Extensión → webview del panel EJECUCIÓN. */
export type ExecOutbound =
  | { type: "execTrace"; info: TraceInfo | null }
  | {
      type: "execNodeTraces";
      executionId: string;
      nodeTraces: Record<string, ToolCall[]>;
      reconstructed: boolean;
      reason?: string;
    }
  | { type: "selectNode"; system: Provider; node: SelectedNodePayload }
  | { type: "nodeStateResult"; key: string; acc: unknown }
  | { type: "nodeStateError"; key: string; message: string }
  | { type: "inspectResult"; system: Provider; nodeId: string; files: InspectFile[] }
  | { type: "inspectError"; system: Provider; nodeId: string; message: string };
