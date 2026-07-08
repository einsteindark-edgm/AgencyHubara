import React from "react";
import { PROVIDER_LABEL, Provider } from "../../../src/bridge/endpoints";
import {
  breadcrumb,
  clampDepth,
  MAX_FOCUS_DEPTH,
  MIN_FOCUS_DEPTH,
  Scope,
  systemOf,
  WORKSPACE_SCOPE,
} from "../../../src/graph/scope";

export interface ToolbarProps {
  scope: Scope;
  nodeLabel?: string;
  loading: boolean;
  editable: boolean;
  collapsedClusters: Set<Provider>;
  onToggleCluster: (system: Provider) => void;
  onScopeChange: (scope: Scope) => void;
  onDepthChange: (depth: number) => void;
  onRefresh: () => void;
  /** descarta posiciones guardadas del scope actual y re-encuadra. */
  onRelayout: () => void;
}

/** Breadcrumb clickeable + selector de scope + slider de profundidad (solo en
 * focus) — el equivalente project/target de Xcode aplicado al canvas (§2.6). */
export function Toolbar({
  scope,
  nodeLabel,
  loading,
  editable,
  collapsedClusters,
  onToggleCluster,
  onScopeChange,
  onDepthChange,
  onRefresh,
  onRelayout,
}: ToolbarProps): React.ReactElement {
  const crumbs = breadcrumb(scope, nodeLabel);
  return (
    <div className="toolbar">
      <div className="breadcrumb">
        {crumbs.map((label, i) => (
          <React.Fragment key={`${label}-${i}`}>
            {i > 0 && <span className="crumb-sep">▸</span>}
            <button
              type="button"
              className="crumb"
              disabled={i === crumbs.length - 1}
              onClick={() => {
                if (i === 0) {
                  onScopeChange(WORKSPACE_SCOPE);
                } else if (i === 1 && scope.kind !== "workspace") {
                  onScopeChange(systemOf(scope.system));
                }
              }}
            >
              {label}
            </button>
          </React.Fragment>
        ))}
        {editable && (
          <span className="edit-mode-badge" title="Drag entre nodos conecta · click derecho en una arista desconecta">
            ✎ edit mode
          </span>
        )}
      </div>
      <div className="toolbar-actions">
        {scope.kind === "workspace" &&
          (["graphagents", "systemmap"] as Provider[]).map((sys) => (
            <button
              key={sys}
              type="button"
              className="refresh-btn"
              title={`${collapsedClusters.has(sys) ? "Expandir" : "Colapsar"} ${PROVIDER_LABEL[sys]} a un solo nodo`}
              onClick={() => onToggleCluster(sys)}
            >
              {collapsedClusters.has(sys) ? "▣" : "□"} {PROVIDER_LABEL[sys]}
            </button>
          ))}
        {scope.kind === "focus" && (
          <label className="depth-slider" title="Profundidad del ego-graph">
            <span>profundidad</span>
            <input
              type="range"
              min={MIN_FOCUS_DEPTH}
              max={MAX_FOCUS_DEPTH}
              value={scope.depth}
              onChange={(e) => onDepthChange(clampDepth(Number(e.target.value)))}
            />
            <span className="depth-value">{scope.depth}</span>
          </label>
        )}
        <select
          className="scope-picker"
          value={scope.kind === "workspace" ? "workspace" : scope.system}
          onChange={(e) => {
            const v = e.target.value;
            onScopeChange(v === "workspace" ? WORKSPACE_SCOPE : systemOf(v as Provider));
          }}
        >
          <option value="workspace">Workspace</option>
          <option value="graphagents">{PROVIDER_LABEL.graphagents}</option>
          <option value="systemmap">{PROVIDER_LABEL.systemmap}</option>
        </select>
        <button
          type="button"
          className="refresh-btn"
          title="Re-organizar: descarta las posiciones guardadas de esta vista y aplica el layout automático"
          onClick={onRelayout}
        >
          ⧉
        </button>
        <button type="button" className="refresh-btn" title="Refrescar" onClick={onRefresh} disabled={loading}>
          {loading ? "…" : "⟳"}
        </button>
      </div>
    </div>
  );
}
