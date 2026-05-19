// SystemMap — el canvas React Flow principal.
//
// Flujo:
//  1. Carga el grafo via TanStack Query.
//  2. Transforma a nodos/edges React Flow.
//  3. Aplica posiciones: localStorage (si existe) o ELK auto-layout.
//  4. Suscribe drag a localStorage save (debounced).
//  5. Expose `focusNode(id)` para que el sidebar centre la vista.

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import { useSystemGraph } from "../hooks/useSystemGraph";
import {
  clearAllLayouts,
  loadLayout,
  saveLayout,
} from "../hooks/useLayoutPersist";
import { layoutGraph } from "../layout/elkLayout";
import { nodeTypes } from "./nodes";
import { Sidebar } from "./Sidebar";
import type { SystemGraph } from "../api/schemas";

function toReactFlowNodes(graph: SystemGraph): Node[] {
  return graph.nodes.map((n) => ({
    id: n.id,
    // type debe matchear keys de `nodeTypes`
    type: nodeTypes[n.kind as keyof typeof nodeTypes] ? n.kind : "plugin",
    position: { x: 0, y: 0 },                       // se sobreescribe por ELK / localStorage
    data: {
      kind: n.kind,
      label: n.label,
      plugin_id: n.plugin_id,
      data: n.data,
      is_orphan: n.is_orphan,
      orphan_reason: n.orphan_reason,
    },
  }));
}

// Estilo visual por tipo de edge.
// Modelo: edges funcionales (color + animados) vs pertenencia (gris muted muy fino).
const EDGE_STYLE_BY_KIND: Record<
  string,
  {
    stroke: string;
    strokeWidth: number;
    dash?: string;
    animated?: boolean;
    opacity?: number;
  }
> = {
  depends_on:     { stroke: "#f59e0b", strokeWidth: 2, animated: true },        // amber (plugin → plugin)
  belongs_to:     { stroke: "#3f3f46", strokeWidth: 1, dash: "2 4", opacity: 0.55 }, // zinc-700 muted punteado fino — pertenencia muda
  consumes_queue: { stroke: "#a855f7", strokeWidth: 1.5 },                       // purple (worker → queue)
  uses_api:       { stroke: "#0ea5e9", strokeWidth: 1.5, dash: "4 2" },          // sky dashed (frontend → api, asumido)
  invokes_worker: { stroke: "#ef4444", strokeWidth: 2, animated: true },         // red animado (REAL del código)
  mounts_on:      { stroke: "#52525b", strokeWidth: 1 },                          // V2
};

function toReactFlowEdges(graph: SystemGraph): Edge[] {
  return graph.edges.map((e) => {
    const s = EDGE_STYLE_BY_KIND[e.kind] ?? {
      stroke: "#52525b",
      strokeWidth: 1.5,
    };
    return {
      id: e.id,
      source: e.source,
      target: e.target,
      type: "smoothstep",
      animated: s.animated ?? false,
      label: e.label ?? undefined,
      labelStyle: { fill: "#a1a1aa", fontSize: 10 },
      labelBgStyle: { fill: "#18181b", fillOpacity: 0.8 },
      style: {
        stroke: s.stroke,
        strokeWidth: s.strokeWidth,
        strokeDasharray: s.dash,
        opacity: s.opacity ?? 1,
      },
    };
  });
}

function applyPositions(
  nodes: Node[],
  positions: Record<string, { x: number; y: number }>,
): Node[] {
  return nodes.map((n) => {
    const p = positions[n.id];
    return p ? { ...n, position: p } : n;
  });
}

function CanvasInner() {
  const { data: graph, isLoading, error } = useSystemGraph();
  const [nodes, setNodes] = useState<Node[]>([]);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [layoutInProgress, setLayoutInProgress] = useState(false);
  const reactFlow = useReactFlow();
  const lastSavedHashRef = useRef<string>("");
  const saveTimerRef = useRef<number | null>(null);

  // 1. Carga inicial → transformar + aplicar layout
  useEffect(() => {
    if (!graph) return;
    const rfNodes = toReactFlowNodes(graph);
    const rfEdges = toReactFlowEdges(graph);
    const ids = rfNodes.map((n) => n.id);
    const saved = loadLayout(ids);

    if (saved) {
      // Restaurar layout guardado
      setNodes(applyPositions(rfNodes, saved));
      setEdges(rfEdges);
      lastSavedHashRef.current = ids.join("|");
      // Fit view después de un tick para que React reconcilie
      requestAnimationFrame(() => {
        reactFlow.fitView({ padding: 0.2, duration: 400 });
      });
    } else {
      // Auto-layout via ELK
      setLayoutInProgress(true);
      layoutGraph(rfNodes, rfEdges)
        .then((laid) => {
          setNodes(laid);
          setEdges(rfEdges);
          lastSavedHashRef.current = ids.join("|");
          requestAnimationFrame(() => {
            reactFlow.fitView({ padding: 0.2, duration: 400 });
          });
        })
        .catch((err) => {
          console.error("ELK layout failed", err);
          setNodes(rfNodes);                          // posiciones [0,0]: el user verá nodos apilados
          setEdges(rfEdges);
        })
        .finally(() => setLayoutInProgress(false));
    }
  }, [graph, reactFlow]);

  // 2. Drag changes → aplicar + save (debounced)
  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((prev) => {
      const next = applyNodeChanges(changes, prev);
      // Solo persistir si el cambio fue position (no select / hover)
      const positionChanged = changes.some(
        (c) => c.type === "position" && c.dragging === false,
      );
      if (positionChanged) {
        if (saveTimerRef.current) window.clearTimeout(saveTimerRef.current);
        saveTimerRef.current = window.setTimeout(() => {
          const ids = next.map((n) => n.id);
          saveLayout(ids, next);
        }, 300);
      }
      return next;
    });
  }, []);

  // 3. Focus en un nodo (desde sidebar)
  const focusNode = useCallback(
    (nodeId: string) => {
      const n = nodes.find((nd) => nd.id === nodeId);
      if (!n) return;
      reactFlow.setCenter(n.position.x + 100, n.position.y + 40, {
        zoom: 1.2,
        duration: 400,
      });
    },
    [nodes, reactFlow],
  );

  // 4. Reset layout (drop saved + relayout)
  const resetLayout = useCallback(() => {
    clearAllLayouts();
    if (!graph) return;
    const rfNodes = toReactFlowNodes(graph);
    const rfEdges = toReactFlowEdges(graph);
    setLayoutInProgress(true);
    layoutGraph(rfNodes, rfEdges)
      .then((laid) => {
        setNodes(laid);
        setEdges(rfEdges);
        requestAnimationFrame(() =>
          reactFlow.fitView({ padding: 0.2, duration: 400 }),
        );
      })
      .finally(() => setLayoutInProgress(false));
  }, [graph, reactFlow]);

  const autoLayout = useCallback(() => {
    setLayoutInProgress(true);
    layoutGraph(nodes, edges)
      .then((laid) => {
        setNodes(laid);
        const ids = laid.map((n) => n.id);
        saveLayout(ids, laid);
        requestAnimationFrame(() =>
          reactFlow.fitView({ padding: 0.2, duration: 400 }),
        );
      })
      .finally(() => setLayoutInProgress(false));
  }, [nodes, edges, reactFlow]);

  if (isLoading) {
    return (
      <div className="flex-1 flex items-center justify-center text-zinc-500">
        Loading system graph…
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-red-400 p-4">
        <p className="font-medium">Failed to load system graph</p>
        <pre className="text-xs mt-2 text-zinc-500 max-w-xl text-center">
          {error instanceof Error ? error.message : String(error)}
        </pre>
      </div>
    );
  }
  if (!graph) {
    return null;
  }

  return (
    <div className="flex h-screen w-screen">
      <Sidebar
        graph={graph}
        onFocusNode={focusNode}
        onResetLayout={resetLayout}
        onAutoLayout={autoLayout}
      />
      <main className="flex-1 relative" data-testid="system-map-canvas">
        {layoutInProgress ? (
          <div className="absolute top-3 right-3 z-10 text-xs text-zinc-400 bg-zinc-900/80 px-2 py-1 rounded">
            laying out…
          </div>
        ) : null}
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          proOptions={{ hideAttribution: true }}
          minZoom={0.2}
          maxZoom={2.5}
          nodesConnectable={false}                  // V1 read-only: sin crear edges
          nodesDraggable
          panOnDrag
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
          <Controls className="!bg-zinc-900 !border-zinc-700" />
          <MiniMap
            pannable
            zoomable
            nodeColor={(n) => {
              const d = n.data as { is_orphan?: boolean };
              return d.is_orphan ? "#dc2626" : "#3f3f46";
            }}
            maskColor="rgba(0,0,0,0.6)"
          />
        </ReactFlow>
      </main>
    </div>
  );
}

export function SystemMap() {
  return (
    <ReactFlowProvider>
      <CanvasInner />
    </ReactFlowProvider>
  );
}
