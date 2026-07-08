import { ColorMode, Edge } from "@xyflow/react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GraphNode, Provider } from "../../src/bridge/endpoints";
import {
  collapseCluster,
  egoGraph,
  mergeWorkspaceGraph,
  namespaceGraph,
  NamespacedEdge,
  NamespacedNode,
  NS_PREFIX,
  Seam,
} from "../../src/graph/graphOps";
import { InspectFile, OutboundMessage, PersistedViewState, ProviderState, TraceInfo } from "../../src/graph/messages";
import { DEFAULT_FOCUS_DEPTH, focusOf, scopeKey, Scope, WORKSPACE_SCOPE } from "../../src/graph/scope";
import { Canvas } from "./canvas/Canvas";
import { FlowNodeType } from "./canvas/FlowNode";
import { computeLayout, Positioned } from "./layout/computeLayout";
import { Inspector, SelectedNode } from "./panels/Inspector";
import { Toolbar } from "./panels/Toolbar";
import { send } from "./vscodeApi";

const EMPTY_PROVIDER: ProviderState = { payload: null, error: null };
const EMPTY_POSITIONS: Record<string, Positioned> = {};
const PERSIST_DEBOUNCE_MS = 400;

function useVsCodeColorMode(): ColorMode {
  const [mode, setMode] = useState<ColorMode>(() => (document.body.classList.contains("vscode-light") ? "light" : "dark"));
  useEffect(() => {
    const update = () => setMode(document.body.classList.contains("vscode-light") ? "light" : "dark");
    const observer = new MutationObserver(update);
    observer.observe(document.body, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return mode;
}

interface ClusterGraph {
  nodes: NamespacedNode[];
  edges: NamespacedEdge[];
  seamEdges: NamespacedEdge[];
  brokenSeams: Seam[];
}

function graphForScope(
  scope: Scope,
  graphagents: ProviderState,
  systemmap: ProviderState,
  seams: Seam[],
  collapsed: Set<Provider>,
): ClusterGraph {
  if (scope.kind === "workspace") {
    let g = mergeWorkspaceGraph(graphagents.payload, systemmap.payload, seams);
    for (const system of collapsed) {
      g = collapseCluster(g, system, `cluster:${system}`);
    }
    return g;
  }

  const payload = scope.system === "graphagents" ? graphagents.payload : systemmap.payload;
  if (!payload) {
    return { nodes: [], edges: [], seamEdges: [], brokenSeams: [] };
  }
  const ns = namespaceGraph(scope.system, payload);
  if (scope.kind === "system") {
    return { nodes: ns.nodes, edges: ns.edges, seamEdges: [], brokenSeams: [] };
  }

  // focus: ego-graph — namespaceGraph ya normalizó id/source/target al
  // espacio namespaced, así que se usa tal cual.
  const centerId = `${NS_PREFIX[scope.system]}:${scope.nodeId}`;
  const result = egoGraph(ns.nodes, ns.edges, centerId, scope.depth);
  return { nodes: result.nodes, edges: result.edges, seamEdges: [], brokenSeams: [] };
}

export function App(): React.ReactElement {
  const [hydrated, setHydrated] = useState(false);
  const [graphagents, setGraphagents] = useState<ProviderState>(EMPTY_PROVIDER);
  const [systemmap, setSystemmap] = useState<ProviderState>(EMPTY_PROVIDER);
  const [seams, setSeams] = useState<Seam[]>([]);
  const [scope, setScopeState] = useState<Scope>(WORKSPACE_SCOPE);
  const [positionsByScopeKey, setPositionsByScopeKey] = useState<Record<string, Record<string, Positioned>>>({});
  const [collapsedClusters, setCollapsedClusters] = useState<Set<Provider>>(new Set());
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [inspectFiles, setInspectFiles] = useState<Record<string, InspectFile[]>>({});
  const [inspectLoading, setInspectLoading] = useState<string | null>(null);
  const [inspectError, setInspectError] = useState<Record<string, string>>({});
  const [layoutPositions, setLayoutPositions] = useState<Map<string, Positioned>>(new Map());
  const [trace, setTrace] = useState<TraceInfo | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  const colorMode = useVsCodeColorMode();

  // Bootstrap único al montar.
  useEffect(() => {
    const onMessage = (ev: MessageEvent<OutboundMessage>) => {
      const msg = ev.data;
      switch (msg.type) {
        case "bootstrap":
          setGraphagents(msg.graphagents);
          setSystemmap(msg.systemmap);
          setSeams(msg.seams);
          setScopeState(msg.restored.scope);
          setPositionsByScopeKey(msg.restored.positionsByScopeKey);
          setCollapsedClusters(new Set(msg.restored.collapsedClusters));
          setLoading(false);
          setHydrated(true);
          return;
        case "providerUpdate":
          if (msg.provider === "graphagents") {
            setGraphagents(msg.state);
          } else {
            setSystemmap(msg.state);
          }
          setLoading(false);
          return;
        case "refreshing":
          setLoading(true);
          return;
        case "jumpScope":
          setScopeState(msg.scope);
          return;
        case "inspectResult":
          setInspectFiles((prev) => ({ ...prev, [inspectKey(msg.system, msg.nodeId)]: msg.files }));
          setInspectLoading((cur) => (cur === inspectKey(msg.system, msg.nodeId) ? null : cur));
          return;
        case "inspectError":
          setInspectError((prev) => ({ ...prev, [inspectKey(msg.system, msg.nodeId)]: msg.message }));
          setInspectLoading((cur) => (cur === inspectKey(msg.system, msg.nodeId) ? null : cur));
          return;
        case "trace":
          // Identidad estable: el poll de 2s manda un objeto nuevo aunque nada
          // cambió — sin esta guarda, TODOS los nodos de React Flow se
          // reconstruyen en cada tick.
          setTrace((prev) => (prev && JSON.stringify(prev) === JSON.stringify(msg.info) ? prev : msg.info));
          setTraceError(null);
          return;
        case "traceError":
          setTraceError(msg.message);
          return;
        case "traceCleared":
          setTrace(null);
          setTraceError(null);
          return;
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const graph = useMemo(
    () => graphForScope(scope, graphagents, systemmap, seams, collapsedClusters),
    [scope, graphagents, systemmap, seams, collapsedClusters],
  );

  // Layout: recalcula cuando cambia el SET de nodos/edges del scope (no en
  // cada drag — eso lo maneja React Flow localmente vía onNodesChange).
  const layoutSignature = useMemo(() => graph.nodes.map((n) => n.nsId).sort().join(","), [graph.nodes]);
  useEffect(() => {
    let cancelled = false;
    void computeLayout(scope, graph.nodes, graph.edges).then((positions) => {
      if (!cancelled) {
        setLayoutPositions(positions);
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutSignature, scope.kind]);

  const sKey = scopeKey(scope);
  // EMPTY_POSITIONS (identidad estable) evita que un scope sin posiciones
  // guardadas invalide flowNodes en cada render.
  const savedPositions = positionsByScopeKey[sKey] ?? EMPTY_POSITIONS;

  // El trace overlay matchea por nsId: `step.agent` (id crudo del sub-nodo,
  // p. ej. "ctwa-report") → "ga:agent:ctwa-report", el MISMO namespace que
  // usa graphForScope. Solo GraphAgents tiene runtime (AgentSpan).
  const runtimeByNsId = useMemo(() => {
    const m = new Map<string, { status: string; ms?: number; retries?: number }>();
    if (!trace) {
      return m;
    }
    for (const step of trace.steps) {
      if (step.runtime?.status) {
        m.set(`${NS_PREFIX.graphagents}:agent:${step.agent}`, {
          status: step.runtime.status,
          ms: step.runtime.ms,
          retries: step.runtime.retries,
        });
      }
    }
    return m;
  }, [trace]);

  const flowNodes: FlowNodeType[] = useMemo(
    () =>
      graph.nodes.map((n) => {
        const pos = savedPositions[n.nsId] ?? layoutPositions.get(n.nsId) ?? { x: 0, y: 0 };
        const runtime = runtimeByNsId.get(n.nsId);
        return {
          id: n.nsId,
          type: "hubara",
          position: pos,
          data: {
            label: (n.label as string | undefined) ?? n.rawId,
            kind: n.kind as string,
            system: n.system,
            certification: typeof n.certification === "string" ? (n.certification as string) : undefined,
            archetype: typeof n.archetype === "string" ? (n.archetype as string) : undefined,
            sideEffect: typeof n.side_effect === "string" ? (n.side_effect as string) : undefined,
            collapsedCount: typeof n.collapsedCount === "number" ? (n.collapsedCount as number) : undefined,
            runtimeStatus:
              !runtime || runtime.status === "pending" || runtime.status === "other"
                ? "idle"
                : (runtime.status as "running" | "done" | "failed" | "awaiting"),
            runtimeMs: runtime?.ms,
            runtimeRetries: runtime?.retries,
          },
        };
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [graph.nodes, savedPositions, layoutPositions, runtimeByNsId],
  );

  const flowEdges: Edge[] = useMemo(() => {
    const internal: Edge[] = [...graph.edges].map((e) => ({
      id: `${e.nsSource}->${e.nsTarget}:${e.kind}`,
      source: e.nsSource,
      target: e.nsTarget,
      label: e.kind,
      className: `edge-${e.kind}`,
      data: { kind: e.kind },
    }));
    const seamsList: Edge[] = graph.seamEdges.map((e) => ({
      id: `seam:${e.nsSource}->${e.nsTarget}`,
      source: e.nsSource,
      target: e.nsTarget,
      label: (e.label as string | undefined) ?? "seam",
      className: "edge-seam",
      animated: true,
    }));
    return [...internal, ...seamsList];
  }, [graph.edges, graph.seamEdges]);

  const persistTimer = useRef<ReturnType<typeof setTimeout>>();
  const persist = useCallback(
    (nextPositions: Record<string, Record<string, Positioned>>, nextCollapsed: Set<Provider>, nextScope: Scope) => {
      if (persistTimer.current) {
        clearTimeout(persistTimer.current);
      }
      persistTimer.current = setTimeout(() => {
        const state: PersistedViewState = {
          scope: nextScope,
          positionsByScopeKey: nextPositions,
          collapsedClusters: [...nextCollapsed],
        };
        send({ type: "persistState", state });
      }, PERSIST_DEBOUNCE_MS);
    },
    [],
  );

  useEffect(() => {
    if (!hydrated) {
      return;
    }
    persist(positionsByScopeKey, collapsedClusters, scope);
  }, [hydrated, positionsByScopeKey, collapsedClusters, scope, persist]);

  const handleNodesChange = useCallback(
    (nextNodes: FlowNodeType[]) => {
      setPositionsByScopeKey((prev) => {
        const next = { ...prev, [sKey]: { ...prev[sKey] } };
        for (const n of nextNodes) {
          next[sKey][n.id] = n.position;
        }
        return next;
      });
    },
    [sKey],
  );

  const setScope = useCallback((next: Scope) => {
    setScopeState(next);
    setSelectedId(null);
  }, []);

  const handleNodeClick = useCallback(
    (id: string) => {
      if (id.startsWith("cluster:")) {
        return; // el click en un cluster colapsado no selecciona, solo lo doble-click expande
      }
      setSelectedId(id);
      const found = graph.nodes.find((n) => n.nsId === id);
      if (found && found.system === "graphagents") {
        const key = inspectKey(found.system, found.rawId);
        setInspectLoading(key);
        send({ type: "inspectNode", system: found.system, nodeId: found.rawId });
      }
    },
    [graph.nodes],
  );

  const handleNodeDoubleClick = useCallback(
    (id: string) => {
      if (id.startsWith("cluster:")) {
        // Doble-click en un cluster lo EXPANDE in-place (si quedara en el set
        // sin esta rama, no habría forma de des-colapsarlo).
        const system = id.slice("cluster:".length) as Provider;
        setCollapsedClusters((prev) => {
          const next = new Set(prev);
          next.delete(system);
          return next;
        });
        return;
      }
      const found = graph.nodes.find((n) => n.nsId === id);
      if (found) {
        setScope(focusOf(found.system, found.rawId, DEFAULT_FOCUS_DEPTH));
      }
    },
    [graph.nodes, setScope],
  );

  const handleToggleCluster = useCallback((system: Provider) => {
    setCollapsedClusters((prev) => {
      const next = new Set(prev);
      if (next.has(system)) {
        next.delete(system);
      } else {
        next.add(system);
      }
      return next;
    });
  }, []);

  const handleDepthChange = useCallback((depth: number) => {
    setScopeState((prev) => (prev.kind === "focus" ? { ...prev, depth } : prev));
  }, []);

  const handleRefresh = useCallback(() => {
    send({ type: "refresh" });
  }, []);

  // Edit mode (§F5): drag-connect / right-click-disconnect. Solo tiene
  // sentido DENTRO de GraphAgents (System Map es read-only; workspace mezcla
  // namespaces) — la confirmación SIEMPRE la muestra la extensión (los
  // webviews de VS Code no soportan window.confirm/alert de forma confiable).
  const editable = scope.kind !== "workspace" && scope.system === "graphagents";
  const handleConnect = useCallback((source: string, target: string) => {
    send({ type: "connectRequest", source, target });
  }, []);
  const handleEdgeDisconnect = useCallback((edge: Edge) => {
    const kind = (edge.data as { kind?: string } | undefined)?.kind;
    if ((kind === "uses" || kind === "agent") && edge.source && edge.target) {
      send({ type: "disconnectRequest", source: edge.source, target: edge.target, kind });
    }
  }, []);

  const selectedNode: SelectedNode | null = useMemo(() => {
    const found = graph.nodes.find((n) => n.nsId === selectedId);
    return found ? { system: found.system, raw: found as unknown as GraphNode } : null;
  }, [graph.nodes, selectedId]);

  // El tercer crumb es el CENTRO del focus, no el último nodo clickeado.
  const focusCenterLabel = useMemo(() => {
    if (scope.kind !== "focus") {
      return undefined;
    }
    const center = graph.nodes.find((n) => n.system === scope.system && n.rawId === scope.nodeId);
    return ((center?.label as string | undefined) ?? scope.nodeId);
  }, [scope, graph.nodes]);

  const selectedInspectKey = selectedNode ? inspectKey(selectedNode.system, (selectedNode.raw as NamespacedNode).rawId) : null;

  const globalError = graphagents.error && systemmap.error ? `${graphagents.error} · ${systemmap.error}` : null;

  return (
    <div className="app">
      <Toolbar
        scope={scope}
        nodeLabel={focusCenterLabel}
        loading={loading}
        editable={editable}
        collapsedClusters={collapsedClusters}
        onToggleCluster={handleToggleCluster}
        onScopeChange={setScope}
        onDepthChange={handleDepthChange}
        onRefresh={handleRefresh}
      />
      {globalError && <div className="global-error">⚠ {globalError}</div>}
      {graph.brokenSeams.length > 0 && (
        <div className="global-error" title="Costuras de seams.yaml cuyo from/to no existe en el grafo actual">
          ⚠ costuras sin resolver: {graph.brokenSeams.map((s) => s.id).join(", ")}
        </div>
      )}
      {trace && (
        <div className="trace-banner">
          <span className="trace-dot" />
          Trace {trace.executionId.slice(0, 8)} — {trace.workflowStatus}
          <button type="button" className="trace-stop" onClick={() => send({ type: "stopTrace" })}>
            ✕ detener
          </button>
        </div>
      )}
      {traceError && <div className="global-error">⚠ trace: {traceError}</div>}
      <div className="body">
        <div className="canvas-wrap">
          <Canvas
            nodes={flowNodes}
            edges={flowEdges}
            colorMode={colorMode}
            editable={editable}
            onNodesChange={handleNodesChange}
            onNodeClick={handleNodeClick}
            onNodeDoubleClick={handleNodeDoubleClick}
            onConnect={handleConnect}
            onEdgeDisconnect={handleEdgeDisconnect}
          />
        </div>
        <Inspector
          node={selectedNode}
          files={selectedInspectKey ? (inspectFiles[selectedInspectKey] ?? null) : null}
          filesLoading={inspectLoading !== null && inspectLoading === selectedInspectKey}
          filesError={selectedInspectKey ? (inspectError[selectedInspectKey] ?? null) : null}
          onOpenFile={(path) => send({ type: "openFile", path })}
          onFocus={() => {
            if (selectedNode) {
              setScope(focusOf(selectedNode.system, (selectedNode.raw as NamespacedNode).rawId, DEFAULT_FOCUS_DEPTH));
            }
          }}
        />
      </div>
    </div>
  );
}

function inspectKey(system: Provider, nodeId: string): string {
  return `${system}:${nodeId}`;
}
