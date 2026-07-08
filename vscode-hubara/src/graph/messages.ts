import { GraphPayload, Provider } from "../bridge/endpoints";
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

/** Webview → extensión. */
export type InboundMessage =
  | { type: "ready" }
  | { type: "openFile"; path: string }
  | { type: "refresh" }
  | { type: "persistState"; state: PersistedViewState }
  | { type: "inspectNode"; system: Provider; nodeId: string }
  | { type: "stopTrace" }
  | { type: "connectRequest"; source: string; target: string }
  | { type: "disconnectRequest"; source: string; target: string; kind: string }
  /** el acc (estado acumulador) tras un nodo — lazy, al abrir la pestaña
   * input/output del Inspector. `key` la genera la webview y viaja de vuelta
   * para matchear la respuesta con la pestaña que la pidió. */
  | { type: "nodeStateRequest"; key: string; executionId: string; taskId: string };

/** Extensión → webview. */
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
  | { type: "inspectResult"; system: Provider; nodeId: string; files: InspectFile[] }
  | { type: "inspectError"; system: Provider; nodeId: string; message: string }
  | { type: "trace"; info: TraceInfo }
  | { type: "traceError"; message: string }
  | { type: "traceCleared" }
  | { type: "nodeStateResult"; key: string; acc: unknown }
  | { type: "nodeStateError"; key: string; message: string }
  /** I/O por-tool reconstruido de un run (`/api/flow-trace`) — llega una vez
   * por ejecución; `reason` explica una reconstrucción vacía (run viejo). */
  | {
      type: "flowTrace";
      executionId: string;
      nodeTraces: Record<string, ToolCall[]>;
      reconstructed: boolean;
      reason?: string;
    };
