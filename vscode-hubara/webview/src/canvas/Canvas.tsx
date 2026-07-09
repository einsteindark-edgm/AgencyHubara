import {
  applyNodeChanges,
  Background,
  ColorMode,
  Connection,
  Controls,
  Edge,
  MiniMap,
  NodeChange,
  ReactFlow,
  ReactFlowInstance,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import React, { useCallback, useEffect, useRef, useState } from "react";
import { FlowNodeType, NODE_TYPES } from "./FlowNode";

/** Payload del drag desde la palette (application/acktos-node). */
export interface PaletteDragItem {
  id: string;
  kind: string;
  label: string;
}

export const PALETTE_MIME = "application/acktos-node";

export interface CanvasProps {
  nodes: FlowNodeType[];
  edges: Edge[];
  colorMode: ColorMode;
  /** edit mode (§F5) — solo true en scopes de GraphAgents (no workspace). */
  editable: boolean;
  /** posiciones finales tras un drag (UNA vez por gesto, no por frame). */
  onPositionsCommit: (positions: Record<string, { x: number; y: number }>) => void;
  onNodeClick: (id: string) => void;
  onNodeDoubleClick: (id: string) => void;
  onConnect: (source: string, target: string) => void;
  /** click en una arista (edit mode) — App muestra el botón "✕ desconectar". */
  onEdgeSelect: (edge: Edge | null) => void;
  onEdgeDisconnect: (edge: Edge) => void;
  /** click derecho en un nodo (edit mode) — App confirma y borra el agente/tool. */
  onNodeDelete: (nodeId: string) => void;
  /** un item de la palette soltado sobre el canvas; `targetNodeId` es el nodo
   * bajo el cursor (null = zona vacía). */
  onPaletteDrop: (item: PaletteDragItem, targetNodeId: string | null) => void;
  /** re-encuadra el viewport cuando cambia (scope nuevo con su layout YA
   * resuelto). Sin esto, cambiar de scope deja la cámara donde estaba y el
   * grafo nuevo puede quedar completamente fuera de pantalla. */
  fitToken: number;
}

/**
 * El canvas mantiene sus nodos en ESTADO LOCAL (patrón uncontrolled de React
 * Flow): un drag muta solo acá, a 60fps, sin re-crear los objetos de nodo en
 * el componente padre — eso era la causa del parpadeo de los labels de las
 * aristas (cada frame reconstruía TODOS los nodos → React Flow re-montaba los
 * edge labels). El padre solo recibe las posiciones al SOLTAR (dragStop) y
 * re-sincroniza cuando cambia el grafo/layout/runtime (identidad de `nodes`).
 */
export function Canvas({
  nodes,
  edges,
  colorMode,
  editable,
  onPositionsCommit,
  onNodeClick,
  onNodeDoubleClick,
  onConnect,
  onEdgeSelect,
  onEdgeDisconnect,
  onNodeDelete,
  onPaletteDrop,
  fitToken,
}: CanvasProps): React.ReactElement {
  const [localNodes, setLocalNodes] = useState<FlowNodeType[]>(nodes);
  const dragging = useRef(false);
  const latestNodes = useRef(localNodes);
  latestNodes.current = localNodes;
  const instance = useRef<ReactFlowInstance<FlowNodeType, Edge> | null>(null);

  // Re-sync desde el padre cuando el grafo/layout/overlay cambian — pero NUNCA
  // a mitad de un drag (pisaría la posición bajo el mouse).
  useEffect(() => {
    if (!dragging.current) {
      setLocalNodes(nodes);
    }
  }, [nodes]);

  // fitToken cambia cuando el layout del scope NUEVO ya resolvió — el timeout
  // deja que el re-sync de localNodes llegue al store de React Flow antes de
  // medir el bounding box (efectos + setState del mismo commit).
  useEffect(() => {
    if (fitToken === 0) {
      return;
    }
    const timer = setTimeout(() => {
      void instance.current?.fitView({ padding: 0.15, duration: 250 });
    }, 60);
    return () => clearTimeout(timer);
  }, [fitToken]);

  const handleChange = useCallback((changes: NodeChange<FlowNodeType>[]) => {
    setLocalNodes((nds) => applyNodeChanges(changes, nds));
  }, []);

  // Solo los nodos ARRASTRADOS se persisten. Commitear todos era un veneno:
  // si el layout async aún no había resuelto, los demás quedaban guardados en
  // (0,0) para siempre (workspaceState) — el grafo entero apilado en el origen.
  const commitPositions = useCallback(
    (moved: FlowNodeType[]) => {
      dragging.current = false;
      const byId = new Map(latestNodes.current.map((n) => [n.id, n] as const));
      const positions: Record<string, { x: number; y: number }> = {};
      for (const m of moved) {
        const n = byId.get(m.id);
        if (n) {
          positions[n.id] = n.position;
        }
      }
      if (Object.keys(positions).length > 0) {
        onPositionsCommit(positions);
      }
    },
    [onPositionsCommit],
  );

  const handleConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        onConnect(connection.source, connection.target);
      }
    },
    [onConnect],
  );

  const handleDrop = useCallback(
    (ev: React.DragEvent) => {
      const raw = ev.dataTransfer.getData(PALETTE_MIME);
      if (!raw) {
        return;
      }
      ev.preventDefault();
      let item: PaletteDragItem;
      try {
        item = JSON.parse(raw) as PaletteDragItem;
      } catch {
        return;
      }
      // El nodo bajo el cursor: React Flow marca cada nodo con data-id.
      const el = (ev.target as HTMLElement).closest(".react-flow__node");
      onPaletteDrop(item, el?.getAttribute("data-id") ?? null);
    },
    [onPaletteDrop],
  );

  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={localNodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        colorMode={colorMode}
        onInit={(inst) => {
          instance.current = inst;
        }}
        onNodesChange={handleChange}
        onNodeDragStart={() => {
          dragging.current = true;
        }}
        onNodeDragStop={(_ev, node, draggedNodes) => commitPositions(draggedNodes?.length ? draggedNodes : [node])}
        onNodeClick={(_ev, node) => onNodeClick(node.id)}
        onNodeDoubleClick={(_ev, node) => onNodeDoubleClick(node.id)}
        onNodeContextMenu={
          editable
            ? (ev, node) => {
                ev.preventDefault();
                if (!node.id.startsWith("cluster:")) {
                  onNodeDelete(node.id);
                }
              }
            : undefined
        }
        onConnect={editable ? handleConnect : undefined}
        onEdgeClick={
          editable
            ? (_ev, edge) => {
                onEdgeSelect(edge);
              }
            : undefined
        }
        onEdgeContextMenu={
          editable
            ? (ev, edge) => {
                ev.preventDefault();
                onEdgeDisconnect(edge);
              }
            : undefined
        }
        onPaneClick={() => onEdgeSelect(null)}
        onDrop={editable ? handleDrop : undefined}
        onDragOver={
          editable
            ? (ev) => {
                if (ev.dataTransfer.types.includes(PALETTE_MIME)) {
                  ev.preventDefault();
                  ev.dataTransfer.dropEffect = "link";
                }
              }
            : undefined
        }
        nodesConnectable={editable}
        fitView
        proOptions={{ hideAttribution: true }}
      >
        <Background />
        <Controls />
        <MiniMap pannable zoomable />
      </ReactFlow>
    </ReactFlowProvider>
  );
}
