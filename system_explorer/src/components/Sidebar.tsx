// Panel lateral con stats globales + lista de huérfanos clickeable.
// Cuando el user clickea un huérfano, el SystemMap centra la vista en ese
// nodo (via onFocusNode callback).

import { AlertTriangle, Boxes, Network } from "lucide-react";
import type { SystemGraph } from "../api/schemas";

type Props = {
  graph: SystemGraph;
  onFocusNode: (nodeId: string) => void;
  onResetLayout: () => void;
  onAutoLayout: () => void;
};

export function Sidebar({
  graph,
  onFocusNode,
  onResetLayout,
  onAutoLayout,
}: Props) {
  const orphans = graph.nodes.filter((n) => n.is_orphan);
  const generatedDate = new Date(graph.generated_at).toLocaleString();

  return (
    <aside className="w-72 shrink-0 border-r border-zinc-800 bg-zinc-950 overflow-y-auto h-full">
      <div className="p-4 border-b border-zinc-800">
        <h1 className="text-lg font-semibold text-zinc-100">System Explorer</h1>
        <p className="text-xs text-zinc-500 mt-1">
          AgencyHubara · {graph.plugins.length} plugins · v{graph.version}
        </p>
        <p className="text-[10px] text-zinc-600 mt-1">
          generated {generatedDate}
        </p>
      </div>

      {/* Stats */}
      <section className="p-4 border-b border-zinc-800 space-y-2">
        <h2 className="text-xs uppercase tracking-wider text-zinc-500">
          Stats
        </h2>
        <div className="grid grid-cols-2 gap-2 text-sm">
          <div className="rounded bg-zinc-900 px-2 py-1.5">
            <p className="text-[10px] text-zinc-500 uppercase">Nodes</p>
            <p className="text-zinc-100 font-medium">
              {graph.stats.total_nodes}
            </p>
          </div>
          <div className="rounded bg-zinc-900 px-2 py-1.5">
            <p className="text-[10px] text-zinc-500 uppercase">Edges</p>
            <p className="text-zinc-100 font-medium">
              {graph.stats.total_edges}
            </p>
          </div>
          <div className="rounded bg-zinc-900 px-2 py-1.5">
            <p className="text-[10px] text-zinc-500 uppercase">Plugins</p>
            <p className="text-zinc-100 font-medium">
              {graph.stats.total_plugins}
            </p>
          </div>
          <div className="rounded bg-red-950/30 border border-red-900/30 px-2 py-1.5">
            <p className="text-[10px] text-red-500 uppercase">Orphans</p>
            <p className="text-red-300 font-medium">
              {graph.stats.orphan_count}
            </p>
          </div>
        </div>
        <div className="text-[11px] text-zinc-500 space-y-0.5">
          {Object.entries(graph.stats.by_kind).map(([k, v]) => (
            <div key={k} className="flex justify-between">
              <span>{k}</span>
              <span className="text-zinc-400">{v}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Plugins list */}
      <section className="p-4 border-b border-zinc-800 space-y-2">
        <h2 className="text-xs uppercase tracking-wider text-zinc-500 flex items-center gap-1.5">
          <Boxes className="w-3.5 h-3.5" />
          Plugins
        </h2>
        <ul className="text-sm space-y-1">
          {graph.plugins.map((p) => {
            const layers: string[] = [];
            if (p.has_frontend) layers.push("FE");
            if (p.has_api) layers.push("API");
            if (p.has_agent) layers.push("AGT");
            return (
              <li key={p.id}>
                <button
                  type="button"
                  onClick={() => onFocusNode(`plugin:${p.id}`)}
                  className="w-full text-left rounded px-2 py-1 hover:bg-zinc-900 transition-colors group"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-zinc-200 truncate">
                      {p.display_name}
                    </span>
                    <span className="text-[10px] text-zinc-500 shrink-0">
                      {layers.join("·") || "—"}
                    </span>
                  </div>
                  {p.orphan_count > 0 ? (
                    <p className="text-[10px] text-red-400">
                      {p.orphan_count} orphan{p.orphan_count !== 1 ? "s" : ""}
                    </p>
                  ) : (
                    <p className="text-[10px] text-zinc-600">
                      {p.node_count} node{p.node_count !== 1 ? "s" : ""}
                    </p>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Orphans */}
      {orphans.length > 0 ? (
        <section className="p-4 border-b border-zinc-800 space-y-2">
          <h2 className="text-xs uppercase tracking-wider text-red-500 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" />
            Orphans ({orphans.length})
          </h2>
          <ul className="text-sm space-y-1">
            {orphans.map((n) => (
              <li key={n.id}>
                <button
                  type="button"
                  onClick={() => onFocusNode(n.id)}
                  className="w-full text-left rounded px-2 py-1 hover:bg-zinc-900 transition-colors"
                >
                  <p className="text-zinc-200 truncate">{n.label}</p>
                  <p className="text-[10px] text-red-400">
                    {n.kind} · {n.orphan_reason}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Warnings */}
      {graph.warnings.length > 0 ? (
        <section className="p-4 border-b border-zinc-800 space-y-2">
          <h2 className="text-xs uppercase tracking-wider text-amber-500 flex items-center gap-1.5">
            <AlertTriangle className="w-3.5 h-3.5" />
            Warnings ({graph.warnings.length})
          </h2>
          <ul className="text-[11px] text-amber-300 space-y-1">
            {graph.warnings.map((w, i) => (
              <li key={i} className="break-words">
                {w}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Controls */}
      <section className="p-4 space-y-2">
        <h2 className="text-xs uppercase tracking-wider text-zinc-500 flex items-center gap-1.5">
          <Network className="w-3.5 h-3.5" />
          Layout
        </h2>
        <button
          type="button"
          onClick={onAutoLayout}
          className="w-full text-sm rounded bg-zinc-800 hover:bg-zinc-700 text-zinc-100 px-3 py-1.5 transition-colors"
        >
          Auto-layout (ELK)
        </button>
        <button
          type="button"
          onClick={onResetLayout}
          className="w-full text-sm rounded bg-zinc-900 hover:bg-zinc-800 text-zinc-300 px-3 py-1.5 transition-colors border border-zinc-800"
        >
          Reset saved layout
        </button>
        <p className="text-[10px] text-zinc-600">
          Drag nodes to reorganize. Posiciones se guardan automáticamente en
          localStorage por grafo.
        </p>
      </section>
    </aside>
  );
}
