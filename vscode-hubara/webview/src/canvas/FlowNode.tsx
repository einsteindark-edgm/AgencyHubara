import { Handle, Node, NodeProps, Position } from "@xyflow/react";
import React from "react";
import { Provider } from "../../../src/bridge/endpoints";

export interface FlowNodeData extends Record<string, unknown> {
  label: string;
  kind: string;
  system?: Provider;
  certification?: string;
  archetype?: string;
  sideEffect?: string;
  collapsedCount?: number;
  runtimeStatus?: "idle" | "running" | "done" | "failed" | "awaiting";
  runtimeMs?: number;
  runtimeRetries?: number;
}

export type FlowNodeType = Node<FlowNodeData, "hubara">;

const KIND_LABEL: Record<string, string> = {
  tool: "tool",
  agent: "agent",
  port: "port",
  plugin: "plugin",
  frontend_unit: "frontend",
  api_router: "router",
  worker: "worker",
  task_queue: "queue",
  cluster: "sistema",
};

const CERT_CLASS: Record<string, string> = {
  C0: "cert-c0",
  C1: "cert-c1",
  C2: "cert-c2",
  C3: "cert-c2",
};

export function FlowNode({ data, selected }: NodeProps<FlowNodeType>): React.ReactElement {
  const certClass = data.certification ? (CERT_CLASS[data.certification] ?? "") : "";
  const statusClass = data.runtimeStatus && data.runtimeStatus !== "idle" ? `status-${data.runtimeStatus}` : "";
  return (
    <div className={`flow-node kind-${data.kind} ${certClass} ${statusClass} ${selected ? "selected" : ""}`}>
      <Handle type="target" position={Position.Top} />
      <div className="flow-node-header">
        <span className="flow-node-kind">{KIND_LABEL[data.kind] ?? data.kind}</span>
        {data.certification && <span className={`flow-node-cert ${certClass}`}>{data.certification}</span>}
      </div>
      <div className="flow-node-label">{data.label}</div>
      {(data.archetype || data.sideEffect) && (
        <div className="flow-node-meta">{data.archetype ?? data.sideEffect}</div>
      )}
      {typeof data.collapsedCount === "number" && (
        <div className="flow-node-meta">{data.collapsedCount} nodos</div>
      )}
      {data.runtimeStatus && data.runtimeStatus !== "idle" && (
        <div className="flow-node-meta">
          {data.runtimeStatus}
          {typeof data.runtimeMs === "number" && ` · ${data.runtimeMs}ms`}
          {!!data.runtimeRetries && ` · ↻${data.runtimeRetries}`}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} />
    </div>
  );
}

export const NODE_TYPES = { hubara: FlowNode };
