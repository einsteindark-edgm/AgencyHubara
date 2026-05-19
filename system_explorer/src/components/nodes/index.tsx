// Custom node types para React Flow. Un archivo unificado por simplicidad —
// V1 son 6 kinds que comparten styling base. Si crecen mucho, split en
// archivos individuales y export agregado desde acá.
//
// Diseño: cada Node es una "card" con header (kind + plugin) + body (label
// + metadata clave). Outline rojo si is_orphan.

import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  Boxes,
  Cpu,
  Globe,
  LayoutGrid,
  ListTree,
  Server,
  Workflow,
} from "lucide-react";

export type SystemNodeData = {
  kind: string;
  label: string;
  plugin_id: string;
  data: Record<string, unknown>;
  is_orphan: boolean;
  orphan_reason: string | null;
};

const ICON_BY_KIND: Record<string, React.ComponentType<{ className?: string }>> = {
  plugin: Boxes,
  section: LayoutGrid,
  sidebar: ListTree,
  api_router: Globe,
  api_endpoint: Globe,
  worker: Cpu,
  task_queue: Workflow,
};

const ACCENT_BY_KIND: Record<string, string> = {
  plugin: "border-l-amber-500",
  section: "border-l-sky-500",
  sidebar: "border-l-indigo-500",
  api_router: "border-l-emerald-500",
  api_endpoint: "border-l-emerald-400",
  worker: "border-l-rose-500",
  task_queue: "border-l-fuchsia-500",
};

function NodeCard({
  data,
  children,
}: {
  data: SystemNodeData;
  children?: React.ReactNode;
}) {
  const Icon = ICON_BY_KIND[data.kind] ?? Server;
  const accent = ACCENT_BY_KIND[data.kind] ?? "border-l-zinc-500";
  const orphanClass = data.is_orphan ? "node-orphan" : "";

  return (
    <>
      <Handle type="target" position={Position.Left} />
      <div
        className={[
          "rounded-md border-l-4 bg-zinc-900 border border-zinc-700",
          "px-3 py-2 min-w-[160px] max-w-[260px]",
          "shadow-sm hover:shadow-md transition-shadow",
          accent,
          orphanClass,
        ].join(" ")}
      >
        <header className="flex items-center gap-2 text-xs uppercase tracking-wide text-zinc-400">
          <Icon className="w-3.5 h-3.5" />
          <span>{data.kind}</span>
          {data.plugin_id && data.kind !== "plugin" ? (
            <span className="text-zinc-600">· {data.plugin_id}</span>
          ) : null}
        </header>
        <h3 className="mt-1 text-sm font-medium text-zinc-100 truncate">
          {data.label}
        </h3>
        {children ? (
          <div className="mt-1 text-xs text-zinc-400 space-y-0.5">{children}</div>
        ) : null}
        {data.is_orphan ? (
          <p className="mt-1 text-xs text-red-400">
            ⚠ {data.orphan_reason ?? "orphan"}
          </p>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} />
    </>
  );
}

const COMPLETENESS_BADGE: Record<
  string,
  { label: string; cls: string; tip: string }
> = {
  complete: {
    label: "FE+API+AGT",
    cls: "bg-emerald-900/50 text-emerald-200 border-emerald-700",
    tip: "Plugin completo — frontend + API + agent",
  },
  frontend_api: {
    label: "FE+API",
    cls: "bg-sky-900/50 text-sky-200 border-sky-700",
    tip: "Frontend + API, sin agent",
  },
  frontend_agent: {
    label: "FE+AGT",
    cls: "bg-fuchsia-900/50 text-fuchsia-200 border-fuchsia-700",
    tip: "Frontend + agent, sin API directa",
  },
  api_agent: {
    label: "API+AGT",
    cls: "bg-indigo-900/50 text-indigo-200 border-indigo-700",
    tip: "Backend service (API + agent)",
  },
  frontend_only: {
    label: "FE only",
    cls: "bg-amber-900/50 text-amber-200 border-amber-700",
    tip: "Plugin frontend-only — no se conecta a API ni agent propios (usa entities/shared)",
  },
  api_only: {
    label: "API only",
    cls: "bg-zinc-800 text-zinc-300 border-zinc-700",
    tip: "Solo API (meta-plugin o servicio headless)",
  },
  agent_only: {
    label: "AGT only",
    cls: "bg-rose-900/50 text-rose-200 border-rose-700",
    tip: "Solo agent worker (sin UI ni HTTP)",
  },
  empty: {
    label: "empty",
    cls: "bg-red-900/50 text-red-200 border-red-700",
    tip: "Plugin sin contribuciones — probable bug o trabajo en progreso",
  },
};

export function PluginNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  const meta = d.data as {
    version?: string;
    description?: string;
    completeness?: string;
  };
  const badge = meta.completeness
    ? COMPLETENESS_BADGE[meta.completeness]
    : null;
  return (
    <NodeCard data={d}>
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-zinc-500">v{meta.version ?? "?"}</span>
        {badge ? (
          <span
            title={badge.tip}
            className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${badge.cls}`}
          >
            {badge.label}
          </span>
        ) : null}
      </div>
      {meta.description ? (
        <p className="line-clamp-2 mt-1">{meta.description}</p>
      ) : null}
    </NodeCard>
  );
}

export function SectionNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  const meta = d.data as { key?: string; order?: number };
  return (
    <NodeCard data={d}>
      <p>
        key: <code className="text-zinc-300">{meta.key}</code>
      </p>
      {typeof meta.order === "number" ? (
        <p>order: {meta.order}</p>
      ) : null}
    </NodeCard>
  );
}

export function SidebarNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  const meta = d.data as { route?: string };
  return (
    <NodeCard data={d}>
      <p>
        route: <code className="text-zinc-300">{meta.route}</code>
      </p>
    </NodeCard>
  );
}

export function ApiRouterNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  const meta = d.data as { prefix?: string; tags?: string[]; module?: string };
  return (
    <NodeCard data={d}>
      <p>
        prefix: <code className="text-zinc-300">{meta.prefix ?? "—"}</code>
      </p>
      {meta.tags && meta.tags.length > 0 ? (
        <p className="text-zinc-500 truncate">tags: {meta.tags.join(", ")}</p>
      ) : null}
    </NodeCard>
  );
}

export function WorkerNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  const meta = d.data as { task_queue?: string; replicas?: number };
  return (
    <NodeCard data={d}>
      <p>
        queue: <code className="text-zinc-300">{meta.task_queue ?? "—"}</code>
      </p>
      {typeof meta.replicas === "number" ? (
        <p>replicas: {meta.replicas}</p>
      ) : null}
    </NodeCard>
  );
}

export function TaskQueueNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  return <NodeCard data={d} />;
}

export const nodeTypes = {
  plugin: PluginNode,
  section: SectionNode,
  sidebar: SidebarNode,
  api_router: ApiRouterNode,
  api_endpoint: ApiRouterNode,
  worker: WorkerNode,
  task_queue: TaskQueueNode,
};
