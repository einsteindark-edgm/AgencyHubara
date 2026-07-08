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
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import React, { useCallback } from "react";
import { FlowNodeType, NODE_TYPES } from "./FlowNode";

export interface CanvasProps {
  nodes: FlowNodeType[];
  edges: Edge[];
  colorMode: ColorMode;
  /** edit mode (§F5) — solo true en scope system/focus DENTRO de GraphAgents. */
  editable: boolean;
  onNodesChange: (nodes: FlowNodeType[]) => void;
  onNodeClick: (id: string) => void;
  onNodeDoubleClick: (id: string) => void;
  onConnect: (source: string, target: string) => void;
  onEdgeDisconnect: (edge: Edge) => void;
}

export function Canvas({
  nodes,
  edges,
  colorMode,
  editable,
  onNodesChange,
  onNodeClick,
  onNodeDoubleClick,
  onConnect,
  onEdgeDisconnect,
}: CanvasProps): React.ReactElement {
  const handleChange = useCallback(
    (changes: NodeChange<FlowNodeType>[]) => {
      onNodesChange(applyNodeChanges(changes, nodes));
    },
    [nodes, onNodesChange],
  );
  const handleConnect = useCallback(
    (connection: Connection) => {
      if (connection.source && connection.target) {
        onConnect(connection.source, connection.target);
      }
    },
    [onConnect],
  );
  return (
    <ReactFlowProvider>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        colorMode={colorMode}
        onNodesChange={handleChange}
        onNodeClick={(_ev, node) => onNodeClick(node.id)}
        onNodeDoubleClick={(_ev, node) => onNodeDoubleClick(node.id)}
        onConnect={editable ? handleConnect : undefined}
        onEdgeContextMenu={
          editable
            ? (ev, edge) => {
                ev.preventDefault();
                onEdgeDisconnect(edge);
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
