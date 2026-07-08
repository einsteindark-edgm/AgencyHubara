import { Provider, PROVIDER_LABEL } from "../bridge/endpoints";

/**
 * El modelo de scopes (§2.6 del plan) — el equivalente project/target de
 * Xcode aplicado al canvas. Pure types + helpers, sin dependencias de DOM ni
 * `vscode` — importable tanto por la extensión (para persistencia) como por
 * la webview (para render), y testeable en aislamiento.
 */
export type Scope =
  | { kind: "workspace" }
  | { kind: "system"; system: Provider }
  | { kind: "focus"; system: Provider; nodeId: string; depth: number }
  /** UN workflow completo: todo lo alcanzable desde su nodo raíz siguiendo
   * las aristas dirigidas (uses/agent/consumes). El "target" de Xcode. */
  | { kind: "workflow"; system: Provider; rootId: string };

export const WORKSPACE_SCOPE: Scope = { kind: "workspace" };

export const DEFAULT_FOCUS_DEPTH = 1;
export const MIN_FOCUS_DEPTH = 1;
export const MAX_FOCUS_DEPTH = 3;

/** Clave estable para persistencia (posiciones guardadas POR scope). */
export function scopeKey(scope: Scope): string {
  switch (scope.kind) {
    case "workspace":
      return "workspace";
    case "system":
      return `system:${scope.system}`;
    case "focus":
      return `focus:${scope.system}:${scope.nodeId}:${scope.depth}`;
    case "workflow":
      return `workflow:${scope.system}:${scope.rootId}`;
  }
}

export function focusOf(system: Provider, nodeId: string, depth = DEFAULT_FOCUS_DEPTH): Scope {
  return { kind: "focus", system, nodeId, depth };
}

export function systemOf(system: Provider): Scope {
  return { kind: "system", system };
}

export function workflowOf(system: Provider, rootId: string): Scope {
  return { kind: "workflow", system, rootId };
}

export function clampDepth(depth: number): number {
  return Math.min(MAX_FOCUS_DEPTH, Math.max(MIN_FOCUS_DEPTH, Math.round(depth)));
}

/** Breadcrumb clickeable: ["Workspace", "GraphAgents", "agent:ctwa-report"].
 * `nodeLabel` es el label humano del nodo enfocado si se conoce (si no, cae
 * al id crudo). */
export function breadcrumb(scope: Scope, nodeLabel?: string): string[] {
  const crumbs = ["Workspace"];
  if (scope.kind === "workspace") {
    return crumbs;
  }
  crumbs.push(PROVIDER_LABEL[scope.system]);
  if (scope.kind === "focus") {
    crumbs.push(nodeLabel ?? scope.nodeId);
  }
  if (scope.kind === "workflow") {
    crumbs.push(`⚙ ${nodeLabel ?? scope.rootId}`);
  }
  return crumbs;
}
