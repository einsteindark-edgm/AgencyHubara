import { ColorMode, Edge } from "@xyflow/react";
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Provider } from "../../src/bridge/endpoints";
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
import { OutboundMessage, PersistedViewState, ProviderState, TraceInfo } from "../../src/graph/messages";
import { DEFAULT_FOCUS_DEPTH, focusOf, scopeKey, Scope, WORKSPACE_SCOPE } from "../../src/graph/scope";
import { Canvas, PaletteDragItem } from "./canvas/Canvas";
import { FlowNodeType } from "./canvas/FlowNode";
import { clusterGrid, computeLayout, Positioned } from "./layout/computeLayout";
import { FlowTraceState } from "./panels/Inspector";
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
  const [layoutPositions, setLayoutPositions] = useState<Map<string, Positioned>>(new Map());
  const [trace, setTrace] = useState<TraceInfo | null>(null);
  const [traceError, setTraceError] = useState<string | null>(null);
  // flowTrace alimenta el ORDEN real de las tools en los badges; el detalle
  // entró/salió vive en el panel nativo "Ejecución" (F10), no acá.
  const [flowTrace, setFlowTrace] = useState<FlowTraceState | null>(null);
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
        case "trace":
          // Cambió la ejecución mirada → el flow-trace viejo no aplica.
          if (lastTraceEid.current && lastTraceEid.current !== msg.info.executionId) {
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
          return;
        case "flowTrace":
          setFlowTrace({
            executionId: msg.executionId,
            nodeTraces: msg.nodeTraces,
            reconstructed: msg.reconstructed,
            reason: msg.reason,
          });
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

  const sKey = scopeKey(scope);

  // Layout: recalcula cuando cambia el SET de nodos/edges del scope (no en
  // cada drag — eso lo maneja React Flow localmente vía onNodesChange). Al
  // resolver el layout de un scope NUEVO se bumpea fitToken → el canvas
  // re-encuadra el viewport (sin esto la cámara queda donde estaba y el grafo
  // nuevo puede caer completamente fuera de pantalla — el "System Map vacío").
  const layoutSignature = useMemo(() => graph.nodes.map((n) => n.nsId).sort().join(","), [graph.nodes]);
  const [fitToken, setFitToken] = useState(0);
  const lastFitKey = useRef("");
  useEffect(() => {
    let cancelled = false;
    void computeLayout(scope, graph.nodes, graph.edges).then((positions) => {
      if (cancelled) {
        return;
      }
      setLayoutPositions(positions);
      if (lastFitKey.current !== sKey) {
        lastFitKey.current = sKey;
        setFitToken((t) => t + 1);
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layoutSignature, sKey]);

  // EMPTY_POSITIONS (identidad estable) evita que un scope sin posiciones
  // guardadas invalide flowNodes en cada render. Las posiciones DEGENERADAS
  // se descartan: builds viejos commiteaban TODOS los nodos al soltar un drag,
  // y si el layout async no había resuelto quedaban persistidos apilados en
  // (0,0) — un drag real no puede dejar 3+ nodos en la coordenada exacta.
  const savedPositions = useMemo(() => {
    const raw = positionsByScopeKey[sKey] ?? EMPTY_POSITIONS;
    const freq = new Map<string, number>();
    for (const p of Object.values(raw)) {
      const c = `${p.x}|${p.y}`;
      freq.set(c, (freq.get(c) ?? 0) + 1);
    }
    if (![...freq.values()].some((n) => n >= 3)) {
      return raw;
    }
    const clean: Record<string, Positioned> = {};
    for (const [id, p] of Object.entries(raw)) {
      if ((freq.get(`${p.x}|${p.y}`) ?? 0) < 3) {
        clean[id] = p;
      }
    }
    return clean;
  }, [positionsByScopeKey, sKey]);

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

  // Sub-cajas de clusters colapsados (endpoints de costuras): índice ordenado
  // por cluster — posición RELATIVA determinista dentro del contenedor (la
  // misma grilla que dimensiona el cluster en computeLayout).
  const clusterChildren = useMemo(() => {
    const m = new Map<string, string[]>();
    for (const n of graph.nodes) {
      if (n.parentCluster) {
        const list = m.get(n.parentCluster) ?? [];
        list.push(n.nsId);
        m.set(n.parentCluster, list);
      }
    }
    for (const list of m.values()) {
      list.sort();
    }
    return m;
  }, [graph.nodes]);

  const flowNodes: FlowNodeType[] = useMemo(() => {
    const mapped = graph.nodes.map((n): FlowNodeType => {
      const runtime = runtimeByNsId.get(n.nsId);
      const data = {
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
            ? ("idle" as const)
            : (runtime.status as "running" | "done" | "failed" | "awaiting"),
        runtimeMs: runtime?.ms,
        runtimeRetries: runtime?.retries,
      };
      if (n.parentCluster) {
        const siblings = clusterChildren.get(n.parentCluster) ?? [];
        const grid = clusterGrid(siblings.length);
        return {
          id: n.nsId,
          type: "hubara",
          position: grid.childPos(Math.max(0, siblings.indexOf(n.nsId))),
          parentId: n.parentCluster,
          extent: "parent",
          data,
        };
      }
      if (n.kind === "cluster") {
        const grid = clusterGrid((clusterChildren.get(n.nsId) ?? []).length);
        return {
          id: n.nsId,
          type: "hubara",
          position: savedPositions[n.nsId] ?? layoutPositions.get(n.nsId) ?? { x: 0, y: 0 },
          style: { width: grid.width, height: grid.height },
          data,
        };
      }
      return {
        id: n.nsId,
        type: "hubara",
        position: savedPositions[n.nsId] ?? layoutPositions.get(n.nsId) ?? { x: 0, y: 0 },
        data,
      };
    });
    // React Flow exige que el padre aparezca ANTES que sus hijos en el array.
    return [...mapped.filter((n) => n.data.kind === "cluster"), ...mapped.filter((n) => n.data.kind !== "cluster")];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph.nodes, clusterChildren, savedPositions, layoutPositions, runtimeByNsId, orderByNsId]);

  const flowEdges: Edge[] = useMemo(() => {
    // ids únicos aunque el bridge repita una arista (p. ej. dos call sites que
    // invocan el mismo worker) — React Flow con ids duplicados dropea edges.
    const seen = new Map<string, number>();
    const internal: Edge[] = [...graph.edges].map((e) => {
      const base = `${e.nsSource}->${e.nsTarget}:${e.kind}`;
      const n = seen.get(base) ?? 0;
      seen.set(base, n + 1);
      return {
        id: n === 0 ? base : `${base}#${n}`,
        source: e.nsSource,
        target: e.nsTarget,
        label: e.kind,
        className: `edge-${e.kind}`,
        // hit-area generosa: el path SVG es de 1-2px — sin esto, clickear una
        // arista para seleccionarla/desconectarla es una lotería.
        interactionWidth: 24,
        data: { kind: e.kind },
      };
    });
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
  // Las sub-cajas de un cluster NO se persisten: su posición es RELATIVA al
  // contenedor — guardarla contaminaría el espacio absoluto que ese mismo
  // nsId usa en los demás scopes.
  const handlePositionsCommit = useCallback(
    (positions: Record<string, Positioned>) => {
      const childIds = new Set([...clusterChildren.values()].flat());
      const absolute = Object.fromEntries(Object.entries(positions).filter(([id]) => !childIds.has(id)));
      if (Object.keys(absolute).length === 0) {
        return;
      }
      setPositionsByScopeKey((prev) => ({ ...prev, [sKey]: { ...prev[sKey], ...absolute } }));
    },
    [sKey, clusterChildren],
  );


  const setScope = useCallback((next: Scope) => {
    setScopeState(next);
  }, []);

  const handleNodeClick = useCallback(
    (id: string) => {
      if (id.startsWith("cluster:")) {
        return; // el click en un cluster colapsado no selecciona, solo lo doble-click expande
      }
      const found = graph.nodes.find((n) => n.nsId === id);
      if (found) {
        // el detalle vive en el panel nativo "Ejecución" (F10) — se le manda
        // el nodo completo para que no dependa del payload del grafo.
        send({ type: "nodeSelected", system: found.system, node: found });
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

  // Descarta las posiciones guardadas del scope actual y re-encuadra — la
  // salida manual si un layout viejo/arrastres dejaron el grafo ilegible.
  const handleRelayout = useCallback(() => {
    setPositionsByScopeKey((prev) => {
      if (!prev[sKey]) {
        return prev;
      }
      const next = { ...prev };
      delete next[sKey];
      return next;
    });
    setFitToken((t) => t + 1);
  }, [sKey]);

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
        onRelayout={handleRelayout}
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
          <span className="trace-meta"> · el detalle vive en el panel Ejecución (abajo)</span>
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
            fitToken={fitToken}
          />
        </div>
      </div>
    </div>
  );
}
