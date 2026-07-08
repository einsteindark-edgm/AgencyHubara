import * as crypto from "node:crypto";
import * as vscode from "vscode";
import { BridgeResponse, GRAPH_PATH, isGraphPayload, PROVIDER_LABEL, Provider } from "../bridge/endpoints";
import { BridgeHub } from "../bridge/pythonBridge";
import { seamsFilePath } from "../config";
import { connectWithConfirmation, disconnectWithConfirmation } from "./editOps";
import { Seam, stripNs } from "./graphOps";
import {
  EMPTY_VIEW_STATE,
  InboundMessage,
  OutboundMessage,
  PersistedViewState,
  ProviderState,
  TraceInfo,
  TraceStep,
} from "./messages";
import { loadSeams } from "./seams";
import { Scope, workflowOf } from "./scope";

const VIEW_STATE_KEY = "acktos.studio.viewState";
const TRACE_POLL_MS = 2000;
const TERMINAL_STATUSES = ["COMPLETED", "FAILED", "TERMINATED", "TIMED_OUT"];

/**
 * Maneja el WebviewPanel del canvas. Un solo panel muestra AMBOS grafos (§2.6
 * — scopes workspace/system/focus); ambos providers se bootstrapean juntos al
 * abrir, así cambiar de scope no requiere ida y vuelta al bridge salvo un
 * refresh explícito.
 *
 * Persistencia: la EXTENSIÓN es la fuente única del scope/posiciones vía
 * `workspaceState` — sobrevive reloads de la webview y de la ventana. La
 * webview solo refleja lo que llega y emite `persistState` en cada cambio.
 */
export class GraphPanel {
  public static readonly viewType = "acktos.graphPanel";
  private static current?: GraphPanel;

  private readonly panel: vscode.WebviewPanel;
  private readonly disposables: vscode.Disposable[] = [];

  static show(ctx: vscode.ExtensionContext, bridges: BridgeHub, jumpTo?: Scope): void {
    const column = vscode.window.activeTextEditor?.viewColumn ?? vscode.ViewColumn.One;
    if (GraphPanel.current) {
      GraphPanel.current.panel.reveal(column);
      if (jumpTo) {
        GraphPanel.current.post({ type: "jumpScope", scope: jumpTo });
      }
      return;
    }
    const panel = vscode.window.createWebviewPanel(GraphPanel.viewType, "Acktos Studio", column, {
      enableScripts: true,
      retainContextWhenHidden: true,
      localResourceRoots: [vscode.Uri.joinPath(ctx.extensionUri, "dist")],
    });
    GraphPanel.current = new GraphPanel(panel, ctx, bridges, jumpTo);
  }

  /** Fuerza un refresh del panel abierto (comando "Acktos: Refresh Graphs"). No-op si no hay panel. */
  static refreshActive(): void {
    void GraphPanel.current?.refresh();
  }

  /** Abre/enfoca el panel en el WORKFLOW de una ejecución (todo lo alcanzable
   * desde su raíz) y arranca el poll de trace (§F4 — "ver en el canvas por
   * dónde va y dónde falla"). */
  static showTrace(ctx: vscode.ExtensionContext, bridges: BridgeHub, executionId: string, agent: string): void {
    GraphPanel.show(ctx, bridges, workflowOf("graphagents", `agent:${agent}`));
    // el panel puede tardar un tick en existir si se acaba de crear (bootstrap
    // async) — encolamos el arranque del trace al próximo microtask, momento
    // en que `GraphPanel.current` ya está asignado por `show()`.
    void Promise.resolve().then(() => GraphPanel.current?.startTrace(executionId));
  }

  private traceTimer?: NodeJS.Timeout;
  private traceExecutionId?: string;
  /** última ejecución mostrada (queda seteada tras un stop terminal — el
   * flow-trace de un run COMPLETED sigue siendo relevante). */
  private lastTraceId?: string;
  private flowTraceFetched?: string;
  private readonly traceDiagnostics = vscode.languages.createDiagnosticCollection("acktosStudioTrace");
  /** clave del último set de steps failed anotado — si no cambió entre ticks
   * del poll, no se re-consulta /api/inspect ni se reescribe la colección. */
  private lastFailedKey = "";
  /** cache agent → abspath del archivo principal (el mapeo no cambia durante
   * una ejecución; sin esto cada tick re-consulta inspect por CADA failed). */
  private readonly inspectPathCache = new Map<string, string | null>();

  private constructor(
    panel: vscode.WebviewPanel,
    private readonly ctx: vscode.ExtensionContext,
    private readonly bridges: BridgeHub,
    private readonly initialJump: Scope | undefined,
  ) {
    this.panel = panel;
    this.panel.webview.html = this.getHtml();
    this.panel.onDidDispose(() => this.dispose(), null, this.disposables);
    this.panel.webview.onDidReceiveMessage((msg: InboundMessage) => this.onMessage(msg), null, this.disposables);
    this.disposables.push(this.traceDiagnostics);
  }

  private async onMessage(msg: InboundMessage): Promise<void> {
    switch (msg.type) {
      case "ready":
        await this.bootstrap();
        return;
      case "refresh":
        await this.refresh();
        return;
      case "openFile":
        await this.openFile(msg.path);
        return;
      case "persistState":
        this.schedulePersist(msg.state);
        return;
      case "inspectNode":
        await this.inspectNode(msg.system, msg.nodeId);
        return;
      case "stopTrace":
        this.stopTrace();
        return;
      case "connectRequest":
        await this.handleConnect(msg.source, msg.target);
        return;
      case "disconnectRequest":
        await this.handleDisconnect(msg.source, msg.target, msg.kind);
        return;
      case "nodeStateRequest":
        await this.fetchNodeState(msg.key, msg.executionId, msg.taskId);
        return;
    }
  }

  /** El acc (estado acumulador) tras un nodo — `/api/node-state`, lazy desde
   * las pestañas input/output del Inspector. */
  private async fetchNodeState(key: string, executionId: string, taskId: string): Promise<void> {
    try {
      const res = await this.bridges
        .get("graphagents")
        .request({ method: "GET", path: "/api/node-state", params: { execution_id: executionId, task_id: taskId } });
      if (res.status !== 200) {
        this.post({ type: "nodeStateError", key, message: errorDetail(res) });
        return;
      }
      this.post({ type: "nodeStateResult", key, acc: (res.payload as { acc?: unknown }).acc ?? null });
    } catch (e) {
      this.post({ type: "nodeStateError", key, message: e instanceof Error ? e.message : String(e) });
    }
  }

  /** El I/O por-tool reconstruido (`/api/flow-trace`) — UNA vez por ejecución
   * (replay determinista: costoso, y el resultado no cambia). */
  private async fetchFlowTrace(executionId: string): Promise<void> {
    if (this.flowTraceFetched === executionId) {
      return;
    }
    this.flowTraceFetched = executionId;
    try {
      const res = await this.bridges
        .get("graphagents")
        .request({ method: "GET", path: "/api/flow-trace", params: { execution_id: executionId } });
      const payload = res.payload as {
        node_traces?: Record<string, Array<{ seq: number; tool: string; input: unknown; output: unknown }>>;
        reconstructed?: boolean;
        reason?: string;
        error?: string;
      };
      if (this.traceExecutionId !== executionId && this.lastTraceId !== executionId) {
        return; // llegó tarde — el usuario ya está mirando otra ejecución
      }
      this.post({
        type: "flowTrace",
        executionId,
        nodeTraces: payload.node_traces ?? {},
        reconstructed: payload.reconstructed === true,
        reason: payload.reason ?? payload.error,
      });
    } catch {
      // sin flow-trace el Inspector degrada al listado declarado de tools —
      // el resto del detalle (acc por nodo) sigue funcionando.
      this.flowTraceFetched = undefined; // reintenta si el usuario re-abre el trace
    }
  }

  /** Drag-connect / right-click-disconnect (§F5, edit mode) — delega la
   * secuencia validate→confirm→mutate en `editOps.ts` (misma lógica que usa
   * el picker "+ Conectar desde…" del CatalogTree; cero duplicación) y solo
   * refresca ESTE panel si la mutación se aplicó de verdad. */
  private async handleConnect(sourceNsId: string, targetNsId: string): Promise<void> {
    const source = stripNs("graphagents", sourceNsId);
    const target = stripNs("graphagents", targetNsId);
    if (!source || !target) {
      return; // edit mode es SOLO GraphAgents — un nsId de otro sistema no debería llegar acá
    }
    if (await connectWithConfirmation(this.bridges, source, target)) {
      const fresh = await this.fetchProvider("graphagents");
      this.post({ type: "providerUpdate", provider: "graphagents", state: fresh });
    }
  }

  private async handleDisconnect(sourceNsId: string, targetNsId: string, kind: string): Promise<void> {
    const source = stripNs("graphagents", sourceNsId);
    const target = stripNs("graphagents", targetNsId);
    if (!source || !target) {
      return;
    }
    if (await disconnectWithConfirmation(this.bridges, source, target, kind)) {
      const fresh = await this.fetchProvider("graphagents");
      this.post({ type: "providerUpdate", provider: "graphagents", state: fresh });
    }
  }

  /** Arranca el poll de `/api/trace?execution_id=` — cada tick reconstruye el
   * estado por-nodo (done/running/failed/awaiting) y lo empuja al canvas; un
   * step failed además deja un Diagnostic sobre el/los archivos del agente
   * (reusa `/api/inspect`, la MISMA fuente que el inspector de F1). Para de
   * solo cuando el workflow llega a un estado terminal, el panel se cierra, o
   * arranca OTRO trace (un solo trace vivo a la vez). */
  startTrace(executionId: string): void {
    this.stopTrace();
    this.traceExecutionId = executionId;
    this.lastTraceId = executionId;
    this.lastFailedKey = "";
    this.inspectPathCache.clear();
    const poll = async () => {
      if (this.traceExecutionId !== executionId) {
        return; // un startTrace más nuevo canceló este poll en vuelo
      }
      try {
        const res = await this.bridges
          .get("graphagents")
          .request({ method: "GET", path: "/api/trace", params: { execution_id: executionId } });
        if (this.traceExecutionId !== executionId) {
          return;
        }
        if (res.status !== 200) {
          this.post({ type: "traceError", message: errorDetail(res) });
          return; // se sigue pollenado — puede ser un 502 transitorio (:6767 reiniciando)
        }
        const payload = res.payload as {
          workflow_status?: string;
          agent?: string;
          strategy?: string;
          seed?: unknown;
          steps?: TraceStep[];
        };
        const info: TraceInfo = {
          executionId,
          workflowStatus: payload.workflow_status ?? "UNKNOWN",
          agent: payload.agent,
          strategy: payload.strategy,
          seed: payload.seed,
          steps: payload.steps ?? [],
        };
        this.post({ type: "trace", info });
        // El I/O por-tool reconstruido se pide UNA vez, en paralelo al poll —
        // para un run ya COMPLETED llega junto con el primer trace.
        void this.fetchFlowTrace(executionId);
        await this.annotateFailures(info.steps);
        if (TERMINAL_STATUSES.includes(info.workflowStatus)) {
          this.stopTrace(false);
          return;
        }
      } catch (e) {
        if (this.traceExecutionId === executionId) {
          this.post({ type: "traceError", message: e instanceof Error ? e.message : String(e) });
        }
      }
      if (this.traceExecutionId === executionId) {
        this.traceTimer = setTimeout(poll, TRACE_POLL_MS);
      }
    };
    void poll();
  }

  private stopTrace(notifyWebview = true): void {
    if (this.traceTimer) {
      clearTimeout(this.traceTimer);
      this.traceTimer = undefined;
    }
    this.traceExecutionId = undefined;
    if (notifyWebview) {
      this.post({ type: "traceCleared" });
    }
  }

  private async annotateFailures(steps: TraceStep[]): Promise<void> {
    const failed = steps.filter((s) => s.runtime?.status === "failed");
    const key = failed
      .map((s) => `${s.agent}:${s.runtime?.task_id ?? "?"}:${s.runtime?.retries ?? 0}`)
      .sort()
      .join("|");
    if (key === this.lastFailedKey) {
      return; // mismo set de fallos que el tick anterior — nada que rehacer
    }
    this.lastFailedKey = key;
    this.traceDiagnostics.clear();
    for (const step of failed) {
      try {
        const abspath = await this.inspectMainFile(step.agent);
        if (!abspath) {
          continue;
        }
        const range = new vscode.Range(0, 0, 0, 1);
        const diag = new vscode.Diagnostic(
          range,
          `agent:${step.agent} falló en la ejecución en curso (task ${step.runtime?.task_id ?? "?"}, ${step.runtime?.retries ?? 0} retries)`,
          vscode.DiagnosticSeverity.Error,
        );
        diag.source = "Acktos Studio (trace)";
        this.traceDiagnostics.set(vscode.Uri.file(abspath), [diag]);
      } catch {
        // sin inspect no hay dónde anclar el diagnostic — el overlay del canvas
        // ya mostró el fallo, esto es un extra, no la única señal.
      }
    }
  }

  private async inspectMainFile(agent: string): Promise<string | null> {
    const cached = this.inspectPathCache.get(agent);
    if (cached !== undefined) {
      return cached;
    }
    const res = await this.bridges
      .get("graphagents")
      .request({ method: "GET", path: "/api/inspect", params: { node: `agent:${agent}` } });
    if (res.status !== 200) {
      return null; // no cachear errores — puede ser transitorio
    }
    const payload = res.payload as { files?: Array<{ abspath: string }> };
    const abspath = payload.files?.[0]?.abspath ?? null;
    this.inspectPathCache.set(agent, abspath);
    return abspath;
  }

  private async inspectNode(system: Provider, nodeId: string): Promise<void> {
    try {
      const res = await this.bridges.get(system).request({ method: "GET", path: "/api/inspect", params: { node: nodeId } });
      if (res.status !== 200) {
        this.post({ type: "inspectError", system, nodeId, message: errorDetail(res) });
        return;
      }
      const payload = res.payload as { files?: Array<{ path: string; role: string; abspath: string }> };
      this.post({ type: "inspectResult", system, nodeId, files: payload.files ?? [] });
    } catch (e) {
      this.post({ type: "inspectError", system, nodeId, message: e instanceof Error ? e.message : String(e) });
    }
  }

  private async fetchProvider(provider: Provider): Promise<ProviderState> {
    try {
      const res = await this.bridges.get(provider).request({ method: "GET", path: GRAPH_PATH });
      if (res.status !== 200 || !isGraphPayload(res.payload)) {
        return { payload: null, error: errorDetail(res) };
      }
      return { payload: res.payload, error: null };
    } catch (e) {
      return {
        payload: null,
        error: `no pude cargar ${PROVIDER_LABEL[provider]}: ${e instanceof Error ? e.message : String(e)}`,
      };
    }
  }

  private async bootstrap(): Promise<void> {
    const [graphagents, systemmap, seams] = await Promise.all([
      this.fetchProvider("graphagents"),
      this.fetchProvider("systemmap"),
      loadSeams(seamsFilePath(this.ctx.extensionUri.fsPath)),
    ]);
    const restored = this.loadPersisted();
    if (this.initialJump) {
      restored.scope = this.initialJump;
    }
    this.post({ type: "bootstrap", graphagents, systemmap, seams, restored });
  }

  private async refresh(): Promise<void> {
    this.post({ type: "refreshing" });
    const [graphagents, systemmap] = await Promise.all([
      this.fetchProvider("graphagents"),
      this.fetchProvider("systemmap"),
    ]);
    this.post({ type: "providerUpdate", provider: "graphagents", state: graphagents });
    this.post({ type: "providerUpdate", provider: "systemmap", state: systemmap });
  }

  private loadPersisted(): PersistedViewState {
    const stored = this.ctx.workspaceState.get<PersistedViewState>(VIEW_STATE_KEY);
    return stored ?? { ...EMPTY_VIEW_STATE };
  }

  private schedulePersist(state: PersistedViewState): void {
    // Escritura inmediata: la webview YA debouncea (PERSIST_DEBOUNCE_MS en
    // App.tsx) — apilar un segundo debounce acá duplicaba la ventana en la
    // que un reload pierde posiciones.
    void this.ctx.workspaceState.update(VIEW_STATE_KEY, state);
  }

  private async openFile(filePath: string): Promise<void> {
    try {
      const doc = await vscode.workspace.openTextDocument(filePath);
      await vscode.window.showTextDocument(doc, { preview: true });
    } catch (e) {
      void vscode.window.showErrorMessage(`No pude abrir ${filePath}: ${e}`);
    }
  }

  private post(msg: OutboundMessage): void {
    void this.panel.webview.postMessage(msg);
  }

  private getHtml(): string {
    const webview = this.panel.webview;
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.ctx.extensionUri, "dist", "webview.js"));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.ctx.extensionUri, "dist", "webview.css"));
    const nonce = crypto.randomBytes(16).toString("base64url");
    const csp = [
      `default-src 'none'`,
      `img-src ${webview.cspSource} data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src 'nonce-${nonce}'`,
      `font-src ${webview.cspSource}`,
    ].join("; ");
    return /* html */ `<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="${styleUri}" />
  <title>Acktos Studio</title>
</head>
<body>
  <div id="root"></div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }

  dispose(): void {
    GraphPanel.current = undefined;
    this.stopTrace(false);
    this.panel.dispose();
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}

/** Detalle legible del error de una respuesta del bridge (payload.error si
 * existe; si no, el status) — compartido por trace/inspect/fetchProvider. */
function errorDetail(res: BridgeResponse): string {
  return typeof res.payload === "object" && res.payload && "error" in res.payload
    ? String((res.payload as { error: unknown }).error)
    : `status ${res.status}`;
}

// Re-exportado para que otros módulos (TreeViews) no importen `Seam` desde
// graphOps directamente si solo necesitan el tipo de datos del panel.
export type { Seam };
