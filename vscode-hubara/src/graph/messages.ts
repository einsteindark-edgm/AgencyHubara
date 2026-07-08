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
  steps: TraceStep[];
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
  | { type: "disconnectRequest"; source: string; target: string; kind: string };

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
  | { type: "traceCleared" };
