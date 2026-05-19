// Custom node types para React Flow. Un archivo unificado.
// Diseño: cada Node es una "card" con header (kind + plugin) + body
// (label + metadata clave). Outline rojo si is_orphan.

import { Handle, Position, type NodeProps } from "@xyflow/react";
import {
  Boxes,
  Cpu,
  Globe,
  LayoutGrid,
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
  frontend_unit: LayoutGrid,
  api_router: Globe,
  api_endpoint: Globe,
  worker: Cpu,
  task_queue: Workflow,
};

const ACCENT_BY_KIND: Record<string, string> = {
  plugin: "border-l-amber-500",
  frontend_unit: "border-l-sky-500",
  api_router: "border-l-emerald-500",
  api_endpoint: "border-l-emerald-400",
  worker: "border-l-rose-500",
  task_queue: "border-l-fuchsia-500",
};

function NodeCard({
  data,
  children,
  width,
}: {
  data: SystemNodeData;
  children?: React.ReactNode;
  width?: string;
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
          "px-3 py-2 shadow-sm hover:shadow-md transition-shadow",
          width ?? "min-w-[160px] max-w-[260px]",
          accent,
          orphanClass,
        ].join(" ")}
      >
        <header className="flex items-center gap-2 text-xs uppercase tracking-wide text-zinc-400">
          <Icon className="w-3.5 h-3.5" />
          <span>{data.kind.replace("_", " ")}</span>
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
    tip: "Plugin frontend-only — no se conecta a API ni agent propios",
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
    tip: "Plugin sin contribuciones — probable bug o WIP",
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

// ─── FrontendUnitNode ─────────────────────────────────────────────────────
// Combina sidebar + section en una sola caja con 2 sub-paneles. Si una de
// las 2 mitades falta o está vacía, esa sub-mitad se pinta en rojo (el resto
// queda como válido). Si ambas existen → todo verde-OK.

export function FrontendUnitNode({ data }: NodeProps) {
  const d = data as unknown as SystemNodeData;
  const meta = d.data as {
    entry?: string;
    sections?: Array<{ key: string; label: string; order?: number; icon?: string }>;
    sidebars?: Array<{ route: string; label: string; icon?: string }>;
    has_sections?: boolean;
    has_sidebars?: boolean;
    is_complete?: boolean;
  };
  const hasSections = !!meta.has_sections;
  const hasSidebars = !!meta.has_sidebars;
  const sections = meta.sections ?? [];
  const sidebars = meta.sidebars ?? [];

  // Status visual: ambas mitades válidas = sky (color base del kind).
  // Si una mitad falta = ese half en rojo, la otra normal.
  const sidebarHalfCls = hasSidebars
    ? "bg-zinc-950"
    : "bg-red-950/40 border-l-2 border-red-700";
  const sectionHalfCls = hasSections
    ? "bg-zinc-950"
    : "bg-red-950/40 border-l-2 border-red-700";

  return (
    <>
      <Handle type="target" position={Position.Left} />
      <div
        className={[
          "rounded-md border-l-4 bg-zinc-900 border border-zinc-700",
          "shadow-sm hover:shadow-md transition-shadow overflow-hidden",
          "min-w-[220px] max-w-[280px]",
          meta.is_complete ? "border-l-sky-500" : "border-l-red-600",
          d.is_orphan ? "node-orphan" : "",
        ].join(" ")}
      >
        {/* Header */}
        <header className="px-3 py-2 flex items-center gap-2 text-xs uppercase tracking-wide text-zinc-400 border-b border-zinc-800">
          <LayoutGrid className="w-3.5 h-3.5" />
          <span>frontend</span>
          <span className="text-zinc-600">· {d.plugin_id}</span>
        </header>

        {/* Sidebar half */}
        <div className={`px-3 py-1.5 ${sidebarHalfCls}`}>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">
              sidebar
            </span>
            {!hasSidebars ? (
              <span className="text-[10px] text-red-400 font-medium">missing</span>
            ) : (
              <span className="text-[10px] text-zinc-600">
                {sidebars.length} entr{sidebars.length !== 1 ? "ies" : "y"}
              </span>
            )}
          </div>
          {sidebars.length > 0 ? (
            <ul className="mt-0.5 space-y-0.5">
              {sidebars.map((sb) => (
                <li key={sb.route} className="text-xs text-zinc-200 truncate">
                  <span className="text-zinc-500">{sb.route}</span>
                  <span className="text-zinc-600"> · {sb.label}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11px] text-red-400 italic">
              sin entradas → botón de nav inexistente
            </p>
          )}
        </div>

        {/* Section half */}
        <div className={`px-3 py-1.5 border-t border-zinc-800 ${sectionHalfCls}`}>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wider text-zinc-500">
              section
            </span>
            {!hasSections ? (
              <span className="text-[10px] text-red-400 font-medium">missing</span>
            ) : (
              <span className="text-[10px] text-zinc-600">
                {sections.length} area{sections.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          {sections.length > 0 ? (
            <ul className="mt-0.5 space-y-0.5">
              {sections.map((sc) => (
                <li key={sc.key} className="text-xs text-zinc-200 truncate">
                  <span className="text-zinc-500">{sc.key}</span>
                  <span className="text-zinc-600"> · {sc.label}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-[11px] text-red-400 italic">
              sin areas → sidebar click no muestra nada
            </p>
          )}
        </div>

        {d.is_orphan ? (
          <div className="px-3 py-1.5 bg-red-950/30 border-t border-red-900/30 text-xs text-red-300">
            ⚠ {d.orphan_reason ?? "orphan"}
          </div>
        ) : null}
      </div>
      <Handle type="source" position={Position.Right} />
    </>
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
  frontend_unit: FrontendUnitNode,
  api_router: ApiRouterNode,
  api_endpoint: ApiRouterNode,
  worker: WorkerNode,
  task_queue: TaskQueueNode,
};
