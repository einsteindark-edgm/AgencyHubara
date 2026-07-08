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
  reachableGraph,
  Seam,
} from "../../src/graph/graphOps";
import { InspectFile, OutboundMessage, PersistedViewState, ProviderState, TraceInfo } from "../../src/graph/messages";
import { DEFAULT_FOCUS_DEPTH, focusOf, scopeKey, Scope, WORKSPACE_SCOPE } from "../../src/graph/scope";
import { Canvas, PaletteDragItem } from "./canvas/Canvas";
import { FlowNodeType } from "./canvas/FlowNode";
import { computeLayout, Positioned } from "./layout/computeLayout";
import { FlowTraceState, Inspector, IoEntry, SelectedNode } from "./panels/Inspector";
import { Palette } from "./panels/Palette";
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
  if (scope.kind === "workflow") {
    // el workflow COMPLETO que cuelga de la raíz: clausura dirigida, no ego-graph.
    const rootNsId = `${NS_PREFIX[scope.system]}:${scope.rootId}`;
    const result = reachableGraph(ns.nodes, ns.edges, rootNsId);
    return { nodes: result.nodes, edges: result.edges, seamEdges: [], brokenSeams: [] };
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
  const [flowTrace, setFlowTrace] = useState<FlowTraceState | null>(null);
  /** cache del acc por nodo (key = `${executionId}:${taskId}`) — lazy. */
  const [ioStates, setIoStates] = useState<Record<string, IoEntry>>({});
  const requestedIo = useRef(new Set<string>());
  const lastTraceEid = useRef<string | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null);
  const [hint, setHint] = useState<string | null>(null);
  const hintTimer = useRef<ReturnType<typeof setTimeout>>();
  const colorMode = useVsCodeColorMode();

  const showHint = useCallback((msg: string) => {
    setHint(msg);
    if (hintTimer.current) {
      clearTimeout(hintTimer.current);
    }
    hintTimer.current = setTimeout(() => setHint(null), 4000);
  }, []);

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
          // Cambió la ejecución mirada → el detalle viejo (acc/flow-trace) no aplica.
          if (lastTraceEid.current && lastTraceEid.current !== msg.info.executionId) {
            setIoStates({});
            requestedIo.current.clear();
            setFlowTrace(null);
          }
          lastTraceEid.current = msg.info.executionId;
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
          setFlowTrace(null);
          setIoStates({});
          requestedIo.current.clear();
          return;
        case "flowTrace":
          setFlowTrace({
            executionId: msg.executionId,
            nodeTraces: msg.nodeTraces,
            reconstructed: msg.reconstructed,
            reason: msg.reason,
          });
          return;
        case "nodeStateResult":
          setIoStates((prev) => ({ ...prev, [msg.key]: { loading: false, value: msg.acc } }));
          return;
        case "nodeStateError":
          setIoStates((prev) => ({ ...prev, [msg.key]: { loading: false, error: msg.message } }));
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

  // El ORDEN de ejecución sobre cada cajita (§F9): agentes 1, 2, 3… (posición
  // en el plan) y tools `2.1, 2.2` (orden real dentro de su agente — del
  // ledger del flow-trace; si aún no llegó, el orden declarado del plan).
  const orderByNsId = useMemo(() => {
    const m = new Map<string, string>();
    if (!trace) {
      return m;
    }
    const toolOrders = new Map<string, string[]>();
    let n = 0;
    for (const step of trace.steps) {
      if (!step.agent) {
        continue;
      }
      n++;
      m.set(`${NS_PREFIX.graphagents}:agent:${step.agent}`, String(n));
      const ledger = flowTrace && flowTrace.executionId === trace.executionId ? flowTrace.nodeTraces[step.agent] : undefined;
      const tools = ledger ? ledger.map((c) => c.tool) : (step.tools ?? []);
      tools.forEach((tool, i) => {
        const key = `${NS_PREFIX.graphagents}:tool:${tool}`;
        const orders = toolOrders.get(key) ?? [];
        orders.push(`${n}.${i + 1}`);
        toolOrders.set(key, orders);
      });
    }
    for (const [key, orders] of toolOrders) {
      m.set(key, orders.slice(0, 3).join(" · ") + (orders.length > 3 ? " …" : ""));
    }
    return m;
  }, [trace, flowTrace]);

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
            orderBadge: orderByNsId.get(n.nsId),
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
    [graph.nodes, savedPositions, layoutPositions, runtimeByNsId, orderByNsId],
  );

  const flowEdges: Edge[] = useMemo(() => {
    const internal: Edge[] = [...graph.edges].map((e) => ({
      id: `${e.nsSource}->${e.nsTarget}:${e.kind}`,
      source: e.nsSource,
      target: e.nsTarget,
      label: e.kind,
      className: `edge-${e.kind}`,
      // hit-area generosa: el path SVG es de 1-2px — sin esto, clickear una
      // arista para seleccionarla/desconectarla es una lotería.
      interactionWidth: 24,
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

  // UNA escritura por gesto de drag (dragStop) — no por frame; el canvas
  // maneja el movimiento en su estado local (ver Canvas.tsx, fix del parpadeo).
  const handlePositionsCommit = useCallback(
    (positions: Record<string, Positioned>) => {
      setPositionsByScopeKey((prev) => ({ ...prev, [sKey]: { ...prev[sKey], ...positions } }));
    },
    [sKey],
  );

  /** Pide el acc de un nodo a la extensión (lazy, dedupe por key). */
  const requestNodeState = useCallback((key: string, executionId: string, taskId: string) => {
    if (requestedIo.current.has(key)) {
      return;
    }
    requestedIo.current.add(key);
    setIoStates((prev) => ({ ...prev, [key]: { loading: true } }));
    send({ type: "nodeStateRequest", key, executionId, taskId });
  }, []);

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
  const handleEdgeDisconnect = useCallback(
    (edge: Edge) => {
      const kind = (edge.data as { kind?: string } | undefined)?.kind;
      if ((kind === "uses" || kind === "agent") && edge.source && edge.target) {
        send({ type: "disconnectRequest", source: edge.source, target: edge.target, kind });
        setSelectedEdge(null);
      } else {
        showHint(`la relación "${kind ?? "seam"}" no es editable — solo uses/agent se pueden desconectar`);
      }
    },
    [showHint],
  );

  // Drop de la palette: soltar un tool/agente SOBRE un agente lo conecta
  // (agente uses tool · supervisor uses agente) — misma secuencia
  // validate→confirm→mutate que el drag-connect.
  const handlePaletteDrop = useCallback(
    (item: PaletteDragItem, targetNsId: string | null) => {
      if (!targetNsId) {
        showHint("soltá el elemento SOBRE un agente para conectarlo al flujo");
        return;
      }
      const target = graph.nodes.find((n) => n.nsId === targetNsId);
      if (!target || target.system !== "graphagents" || target.kind !== "agent") {
        showHint("solo se puede conectar sobre un AGENTE de GraphAgents");
        return;
      }
      if (`${NS_PREFIX.graphagents}:${item.id}` === target.nsId) {
        showHint("un nodo no se conecta consigo mismo");
        return;
      }
      send({ type: "connectRequest", source: target.nsId, target: `${NS_PREFIX.graphagents}:${item.id}` });
    },
    [graph.nodes, showHint],
  );

  const selectedNode: SelectedNode | null = useMemo(() => {
    const found = graph.nodes.find((n) => n.nsId === selectedId);
    return found ? { system: found.system, raw: found as unknown as GraphNode } : null;
  }, [graph.nodes, selectedId]);

  // El tercer crumb es el CENTRO del focus / la RAÍZ del workflow — no el
  // último nodo clickeado.
  const focusCenterLabel = useMemo(() => {
    if (scope.kind === "focus") {
      const center = graph.nodes.find((n) => n.system === scope.system && n.rawId === scope.nodeId);
      return ((center?.label as string | undefined) ?? scope.nodeId);
    }
    if (scope.kind === "workflow") {
      const root = graph.nodes.find((n) => n.system === scope.system && n.rawId === scope.rootId);
      return ((root?.label as string | undefined) ?? scope.rootId);
    }
    return undefined;
  }, [scope, graph.nodes]);

  // Catálogo completo de GraphAgents para la palette (agentes + tools),
  // marcando cuáles ya están en el scope actual.
  const paletteItems = useMemo(() => {
    if (!editable || !graphagents.payload) {
      return [];
    }
    const visible = new Set(graph.nodes.map((n) => n.rawId));
    return graphagents.payload.nodes
      .filter((n) => n.kind === "agent" || n.kind === "tool")
      .map((n) => ({
        id: n.id,
        kind: n.kind as string,
        label: (n.label as string | undefined) ?? n.id,
        inScope: visible.has(n.id),
      }));
  }, [editable, graphagents.payload, graph.nodes]);

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
          {trace.strategy && <span className="trace-meta"> · {trace.strategy}</span>}
          <span className="trace-meta"> · tocá un nodo para ver su entró/salió</span>
          <button type="button" className="trace-stop" onClick={() => send({ type: "stopTrace" })}>
            ✕ cerrar trace
          </button>
        </div>
      )}
      {traceError && <div className="global-error">⚠ trace: {traceError}</div>}
      {editable && selectedEdge && (
        <div className="edge-action-bar">
          <span className="edge-action-label">
            arista: {selectedEdge.source.replace(/^ga:/, "")} → {selectedEdge.target.replace(/^ga:/, "")}
            {" "}({((selectedEdge.data as { kind?: string } | undefined)?.kind) ?? "?"})
          </span>
          <button type="button" className="edge-disconnect-btn" onClick={() => handleEdgeDisconnect(selectedEdge)}>
            ✕ desconectar
          </button>
          <button type="button" className="trace-stop" onClick={() => setSelectedEdge(null)}>
            cerrar
          </button>
        </div>
      )}
      {hint && <div className="hint-toast">{hint}</div>}
      <div className="body">
        {editable && <Palette items={paletteItems} />}
        <div className="canvas-wrap">
          <Canvas
            nodes={flowNodes}
            edges={flowEdges}
            colorMode={colorMode}
            editable={editable}
            onPositionsCommit={handlePositionsCommit}
            onNodeClick={handleNodeClick}
            onNodeDoubleClick={handleNodeDoubleClick}
            onConnect={handleConnect}
            onEdgeSelect={setSelectedEdge}
            onEdgeDisconnect={handleEdgeDisconnect}
            onPaletteDrop={handlePaletteDrop}
          />
        </div>
        <Inspector
          node={selectedNode}
          files={selectedInspectKey ? (inspectFiles[selectedInspectKey] ?? null) : null}
          filesLoading={inspectLoading !== null && inspectLoading === selectedInspectKey}
          filesError={selectedInspectKey ? (inspectError[selectedInspectKey] ?? null) : null}
          trace={trace}
          flowTrace={flowTrace}
          ioStates={ioStates}
          onRequestNodeState={requestNodeState}
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
