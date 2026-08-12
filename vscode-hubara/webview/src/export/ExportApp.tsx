import {
  Background,
  ColorMode,
  Controls,
  Edge,
  Handle,
  Node,
  NodeProps,
  Position,
  ReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ExportEdge, ExportNode, ExportOutbound } from "../../../src/export/messages";
import { dagreLayout } from "../layout/dagreLayout";
import { send } from "./vscodeApi";

const NODE_W = 214;
const NODE_H = 104;

interface ExportNodeData extends Record<string, unknown> {
  node: ExportNode;
  checked: boolean;
  broken: boolean;
  onToggle: (id: string) => void;
}
type ExportFlowNode = Node<ExportNodeData, "export">;

function ExportFlowNodeComp({ data }: NodeProps<ExportFlowNode>) {
  const { node, checked, broken, onToggle } = data;
  const rel =
    node.relation === "root"
      ? "★ elegiste esto"
      : node.relation === "seam"
        ? `⚡ lo lanza ${node.seamFrom}`
        : node.unitKind === "plugin"
          ? "dependencia (depends_on)"
          : "dependencia (agent://)";
  return (
    <div className={`xnode ${node.unitKind} ${checked ? "on" : "off"} ${broken ? "broken" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <label className="xnode-head" title={checked ? "Quitar del paquete" : "Agregar al paquete"}>
        <input type="checkbox" checked={checked} onChange={() => onToggle(node.id)} />
        <span className="xnode-kind">{node.unitKind === "plugin" ? "PLUGIN" : "GRAPH AGENT"}</span>
        {node.required && <span className="xnode-req" title="Dependencia dura (seam)">requerido</span>}
      </label>
      <div className="xnode-label">{node.label}</div>
      <div className="xnode-rel">{rel}</div>
      {broken && <div className="xnode-warn">⚠ sin esto el plugin no funciona</div>}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

const NODE_TYPES = { export: ExportFlowNodeComp };

interface GraphState {
  nodes: ExportNode[];
  edges: ExportEdge[];
  packageName: string;
}

export function ExportApp() {
  const [graph, setGraph] = useState<GraphState | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  useEffect(() => {
    const onMsg = (ev: MessageEvent<ExportOutbound>) => {
      if (ev.data.type === "graph") {
        setGraph({ nodes: ev.data.nodes, edges: ev.data.edges, packageName: ev.data.packageName });
        setSelected(new Set(ev.data.nodes.map((n) => n.id))); // todo pre-marcado
      }
    };
    window.addEventListener("message", onMsg);
    send({ type: "ready" });
    return () => window.removeEventListener("message", onMsg);
  }, []);

  const toggle = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // graph agents requeridos (seam) destildados cuyo plugin fuente SÍ está marcado
  const brokenIds = useMemo(() => {
    const b = new Set<string>();
    if (!graph) {
      return b;
    }
    for (const n of graph.nodes) {
      if (n.relation === "seam" && n.required && !selected.has(n.id) && selected.has(`plugin:${n.seamFrom}`)) {
        b.add(n.id);
      }
    }
    return b;
  }, [graph, selected]);

  const colorMode: ColorMode = document.body.classList.contains("vscode-light") ? "light" : "dark";

  const flowNodes = useMemo<ExportFlowNode[]>(() => {
    if (!graph) {
      return [];
    }
    const pos = dagreLayout(
      graph.nodes.map((n) => ({ id: n.id, width: NODE_W, height: NODE_H })),
      graph.edges,
      "TB",
    );
    return graph.nodes.map((n) => ({
      id: n.id,
      type: "export",
      position: pos.get(n.id) ?? { x: 0, y: 0 },
      data: { node: n, checked: selected.has(n.id), broken: brokenIds.has(n.id), onToggle: toggle },
    }));
  }, [graph, selected, brokenIds, toggle]);

  const flowEdges = useMemo<Edge[]>(() => {
    if (!graph) {
      return [];
    }
    return graph.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.source,
      target: e.target,
      animated: e.kind === "seam",
      label: e.kind === "seam" ? "⚡ lanza" : undefined,
      className: `edge-${e.kind}${e.kind === "seam" && brokenIds.has(e.target) ? " edge-broken" : ""}`,
    }));
  }, [graph, brokenIds]);

  if (!graph) {
    return <div className="loading">Resolviendo relaciones…</div>;
  }

  const selPlugins = [...selected].filter((id) => id.startsWith("plugin:")).length;
  const selAgents = [...selected].filter((id) => id.startsWith("graphagent:")).length;

  return (
    <div className="export-root">
      <header className="export-bar">
        <div className="export-title">
          <strong>Exportar: {graph.packageName}</strong>
          <span className="export-sub">
            {selPlugins} plugin(s) · {selAgents} graph agent(s) — destildá lo que no quieras en el paquete
          </span>
        </div>
        <div className="export-actions">
          <button className="btn-secondary" onClick={() => send({ type: "cancel" })}>
            Cancelar
          </button>
          <button
            className="btn-primary"
            disabled={selected.size === 0}
            onClick={() => send({ type: "confirm", selected: [...selected] })}
          >
            Exportar ({selected.size})
          </button>
        </div>
      </header>
      {brokenIds.size > 0 && (
        <div className="export-warn">
          ⚠ {brokenIds.size} graph agent(s) requerido(s) sin marcar — el/los plugin(s) que los lanzan quedarán sin
          funcionar en el destino.
        </div>
      )}
      <div className="export-canvas">
        <ReactFlowProvider>
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={NODE_TYPES}
            colorMode={colorMode}
            fitView
            proOptions={{ hideAttribution: true }}
            nodesConnectable={false}
          >
            <Background />
            <Controls showInteractive={false} />
          </ReactFlow>
        </ReactFlowProvider>
      </div>
    </div>
  );
}
