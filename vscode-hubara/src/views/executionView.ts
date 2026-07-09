import * as crypto from "node:crypto";
import * as vscode from "vscode";
import { errorDetail, Provider } from "../bridge/endpoints";
import { BridgeHub } from "../bridge/pythonBridge";
import { CertState, CertStatus, CertSuiteInfo, ExecInbound, ExecOutbound, SelectedNodePayload, ToolCall, TraceInfo } from "../graph/messages";

/**
 * El panel nativo "Ejecución" (abajo, junto a Terminal — §F10): la cascada de
 * la ejecución en curso/última + el detalle del nodo seleccionado (entró/salió
 * confrontado, sub-ejecución por-tool, files). Vive FUERA del canvas para que
 * el grafo use toda la pantalla; VS Code permite arrastrarlo al sidebar
 * derecho y lo recuerda.
 *
 * Fuentes: el GraphPanel le empuja el trace/ledgers (es quien pollea); las
 * selecciones llegan del canvas (click en nodo) o de la propia cascada. Los
 * accs: locales desde memoria (run-local los trae), durables vía
 * /api/node-state (lazy).
 */
export class ExecutionViewProvider implements vscode.WebviewViewProvider {
  static readonly viewType = "acktos.executionView";

  private view?: vscode.WebviewView;
  private lastTrace: TraceInfo | null = null;
  private lastNodeTraces: {
    executionId: string;
    nodeTraces: Record<string, ToolCall[]>;
    reconstructed: boolean;
    reason?: string;
  } | null = null;
  private lastSelected: { system: Provider; node: SelectedNodePayload } | null = null;
  /** accs de la última ejecución LOCAL (task_id sintético `local:<label>` → acc). */
  private readonly localAccs = new Map<string, unknown>();
  /** estado de la última corrida "Guardar & certificar" (F14) — se re-emite al
   * re-abrir la vista (la webview se recarga al ocultarse). */
  private cert: CertState | null = null;

  constructor(
    private readonly ctx: vscode.ExtensionContext,
    private readonly bridges: BridgeHub,
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.ctx.extensionUri, "dist")],
    };
    view.webview.html = this.getHtml(view.webview);
    view.webview.onDidReceiveMessage((msg: ExecInbound) => void this.onMessage(msg));
    view.onDidDispose(() => {
      if (this.view === view) {
        this.view = undefined;
      }
    });
  }

  // ---- entradas desde GraphPanel / canvas (vía extension.ts) ----

  updateTrace(info: TraceInfo): void {
    if (this.lastTrace && this.lastTrace.executionId !== info.executionId && !info.executionId.startsWith("local:")) {
      this.localAccs.clear(); // cambió a una ejecución durable — los accs locales no aplican
    }
    this.lastTrace = info;
    this.post({ type: "execTrace", info });
    this.reveal();
  }

  updateNodeTraces(executionId: string, nodeTraces: Record<string, ToolCall[]>, reconstructed: boolean, reason?: string): void {
    this.lastNodeTraces = { executionId, nodeTraces, reconstructed, reason };
    this.post({ type: "execNodeTraces", executionId, nodeTraces, reconstructed, reason });
  }

  /** Una ejecución LOCAL completa: trace sintético + ledgers + accs en memoria. */
  updateLocal(info: TraceInfo, nodeTraces: Record<string, ToolCall[]>, accs: Map<string, unknown>): void {
    this.localAccs.clear();
    for (const [k, v] of accs) {
      this.localAccs.set(k, v);
    }
    this.lastTrace = info;
    this.lastNodeTraces = { executionId: info.executionId, nodeTraces, reconstructed: true };
    this.post({ type: "execTrace", info });
    this.post({ type: "execNodeTraces", executionId: info.executionId, nodeTraces, reconstructed: true });
    this.reveal();
  }

  clearTrace(): void {
    this.lastTrace = null;
    this.lastNodeTraces = null;
    this.post({ type: "execTrace", info: null });
  }

  // ---- consola de certificación en vivo (F14, CertSink) ----

  certStart(suites: CertSuiteInfo[]): void {
    this.cert = { suites: suites.map((s) => ({ ...s })), logs: {}, phase: null, done: null };
    this.post({ type: "certStart", suites });
    this.reveal();
  }

  certLog(suiteId: string, chunk: string): void {
    if (this.cert) {
      this.cert.logs[suiteId] = (this.cert.logs[suiteId] ?? "") + chunk;
    }
    this.post({ type: "certLog", suiteId, chunk });
  }

  certSuite(suiteId: string, status: CertStatus, detail?: string): void {
    const s = this.cert?.suites.find((x) => x.id === suiteId);
    if (s) {
      s.status = status;
      s.detail = detail;
    }
    this.post({ type: "certSuite", suiteId, status, detail });
  }

  certPhase(phase: string): void {
    if (this.cert) {
      this.cert.phase = phase;
    }
    this.post({ type: "certPhase", phase });
  }

  certDone(ok: boolean, branch: string | undefined, prUrl: string | undefined, errors: string[]): void {
    if (this.cert) {
      this.cert.done = { ok, branch, prUrl, errors };
    }
    this.post({ type: "certDone", ok, branch, prUrl, errors });
    this.reveal();
  }

  selectNode(system: Provider, node: SelectedNodePayload): void {
    this.lastSelected = { system, node };
    this.post({ type: "selectNode", system, node });
    this.reveal();
  }

  /** Trae el panel al frente (sin robar el foco). Si nunca se abrió, el
   * comando auto-generado `<viewId>.focus` lo materializa. */
  private reveal(): void {
    if (this.view) {
      this.view.show?.(true);
    } else {
      void vscode.commands.executeCommand(`${ExecutionViewProvider.viewType}.focus`);
    }
  }

  // ---- mensajes del webview ----

  private async onMessage(msg: ExecInbound): Promise<void> {
    switch (msg.type) {
      case "ready":
        // el view se resuelve lazy: replay del estado acumulado
        if (this.lastTrace) {
          this.post({ type: "execTrace", info: this.lastTrace });
        }
        if (this.lastNodeTraces) {
          this.post({ type: "execNodeTraces", ...this.lastNodeTraces });
        }
        if (this.lastSelected) {
          this.post({ type: "selectNode", ...this.lastSelected });
        }
        if (this.cert) {
          this.post({ type: "certRestore", cert: this.cert });
        }
        return;
      case "openExternal":
        void vscode.env.openExternal(vscode.Uri.parse(msg.url));
        return;
      case "openFile":
        try {
          const doc = await vscode.workspace.openTextDocument(msg.path);
          await vscode.window.showTextDocument(doc, { preview: true });
        } catch (e) {
          void vscode.window.showErrorMessage(`No pude abrir ${msg.path}: ${e}`);
        }
        return;
      case "focusNode":
        void vscode.commands.executeCommand("acktos.focusNode", msg.system, msg.nodeId);
        return;
      case "inspectNode":
        await this.inspectNode(msg.system, msg.nodeId);
        return;
      case "nodeStateRequest":
        await this.fetchNodeState(msg.key, msg.executionId, msg.taskId);
        return;
    }
  }

  private async inspectNode(system: Provider, nodeId: string): Promise<void> {
    if (system !== "graphagents") {
      this.post({ type: "inspectResult", system, nodeId, files: [] });
      return;
    }
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

  private async fetchNodeState(key: string, executionId: string, taskId: string): Promise<void> {
    if (taskId.startsWith("local:")) {
      // ejecución local: el acc ya está en memoria (replay determinista)
      this.post({ type: "nodeStateResult", key, acc: this.localAccs.get(taskId) ?? null });
      return;
    }
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

  private post(msg: ExecOutbound): void {
    void this.view?.webview.postMessage(msg);
  }

  private getHtml(webview: vscode.Webview): string {
    const scriptUri = webview.asWebviewUri(vscode.Uri.joinPath(this.ctx.extensionUri, "dist", "execview.js"));
    const styleUri = webview.asWebviewUri(vscode.Uri.joinPath(this.ctx.extensionUri, "dist", "execview.css"));
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
  <title>Ejecución</title>
</head>
<body>
  <div id="root"></div>
  <script nonce="${nonce}" src="${scriptUri}"></script>
</body>
</html>`;
  }
}
