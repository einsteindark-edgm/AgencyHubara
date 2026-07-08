import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";
import { ToolCall, TraceStep } from "../graph/messages";

interface RunSummary {
  execution_id: string;
  agent: string;
  status: string;
  startTime?: number;
}

interface LocalRunSummary {
  caseId: string;
  title: string;
  agent: string;
  /** steps + ledgers de la corrida — alimentan la cascada sin re-ejecutar. */
  steps: TraceStep[];
  nodeTraces: Record<string, ToolCall[]>;
}

/** Un agente de la cascada de ejecución (§F9 — estilo test navigator de Xcode). */
interface StepEntry {
  order: number;
  agent: string;
  status: string;
  ms?: number;
  retries?: number;
  tools: Array<{ order: string; tool: string }>;
}

interface RunDetail {
  steps: StepEntry[];
}

export type RunNode =
  | { kind: "run"; run: RunSummary }
  | { kind: "localRun"; run: LocalRunSummary; index: number }
  | { kind: "step"; runKey: string; step: StepEntry }
  | { kind: "toolCall"; id: string; order: string; tool: string }
  | { kind: "info"; message: string };

const POLL_MS = 8000;

function statusIcon(status: string): string {
  const s = status.toUpperCase();
  if (s.includes("RUN")) {
    return "sync~spin";
  }
  if (s.includes("COMPLET") || s.includes("DONE")) {
    return "pass";
  }
  if (s.includes("FAIL") || s.includes("TERMINAT")) {
    return "error";
  }
  if (s.includes("PAUS")) {
    return "debug-pause";
  }
  return "circle-outline";
}

/** Proyecta steps de trace + ledgers a la cascada ordenada (agentes 1,2,3…;
 * tools `n.i` — el MISMO esquema de numeración que los badges del canvas). */
function toStepEntries(steps: TraceStep[], nodeTraces: Record<string, ToolCall[]>): StepEntry[] {
  const out: StepEntry[] = [];
  let n = 0;
  for (const step of steps) {
    if (!step.agent) {
      continue;
    }
    n++;
    const ledger = nodeTraces[step.agent];
    const tools = ledger ? ledger.map((c) => c.tool) : (step.tools ?? []);
    out.push({
      order: n,
      agent: step.agent,
      status: step.runtime?.status ?? "done",
      ms: step.runtime?.ms,
      retries: step.runtime?.retries,
      tools: tools.map((tool, i) => ({ order: `${n}.${i + 1}`, tool })),
    });
  }
  return out;
}

/** TreeView "Runs" — las ejecuciones de ESTA sesión (⚡ locales + ▶ durables
 * lanzados acá; toggle ⟲ para el histórico completo de AgentSpan). Cada run
 * se DESPLIEGA en su cascada de ejecución: agentes en orden, y dentro de cada
 * agente sus tools en orden — como el test navigator de Xcode (§F9). */
export class RunsTreeProvider implements vscode.TreeDataProvider<RunNode>, vscode.Disposable {
  private readonly _onDidChangeTreeData = new vscode.EventEmitter<RunNode | undefined | void>();
  readonly onDidChangeTreeData = this._onDidChangeTreeData.event;
  private runs: RunSummary[] = [];
  private lastError: string | null = null;
  private pollTimer?: NodeJS.Timeout;
  /** ejecuciones durables LANZADAS EN ESTA SESIÓN — la vista default; el
   * histórico completo de AgentSpan se abre con el toggle (⟲). */
  private readonly sessionEids = new Set<string>();
  private localRuns: LocalRunSummary[] = [];
  private showAll = false;
  /** cascada por run (lazy: se resuelve al expandir; los locales llegan ya resueltos). */
  private readonly details = new Map<string, RunDetail | "loading" | { error: string }>();

  constructor(private readonly bridges: BridgeHub) {
    // Sin poll eager: arrancarlo acá spawnearía el bridge Python en la
    // activación de CADA ventana aunque la vista nunca se abra — el poll
    // vive atado a la visibilidad de la vista (attach()).
  }

  /** Atar el ciclo del poll a la visibilidad del TreeView: corre solo
   * mientras la vista está visible, para y retoma con ella. */
  attach(view: vscode.TreeView<RunNode>): vscode.Disposable {
    this.setPolling(view.visible);
    return view.onDidChangeVisibility((e) => this.setPolling(e.visible));
  }

  private setPolling(active: boolean): void {
    if (active && !this.pollTimer) {
      void this.poll();
      this.pollTimer = setInterval(() => void this.poll(), POLL_MS);
    } else if (!active && this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = undefined;
    }
  }

  refresh(): void {
    void this.poll();
  }

  /** Registrar un run durable lanzado desde ESTA sesión (▶ Run Durable). */
  noteDurableLaunched(executionId: string): void {
    this.sessionEids.add(executionId);
    this.details.delete(executionId); // la cascada se resuelve fresca al expandir
    this._onDidChangeTreeData.fire();
  }

  /** Registrar una ejecución local (⚡ — en proceso, sin AgentSpan) con su
   * cascada YA resuelta (steps + ledgers vienen de /api/run-local). */
  noteLocalRun(run: LocalRunSummary): void {
    this.localRuns = [run, ...this.localRuns.filter((r) => r.caseId !== run.caseId)];
    this.details.set(`local:${run.caseId}`, { steps: toStepEntries(run.steps, run.nodeTraces) });
    this._onDidChangeTreeData.fire();
  }

  /** Alterna entre "solo esta sesión" (default) y el histórico completo. */
  toggleShowAll(): boolean {
    this.showAll = !this.showAll;
    this._onDidChangeTreeData.fire();
    return this.showAll;
  }

  private async poll(): Promise<void> {
    try {
      const res = await this.bridges.get("graphagents").request({ method: "GET", path: "/api/runs", params: { limit: "25" } });
      if (res.status === 200) {
        const payload = res.payload as { runs?: RunSummary[] };
        this.runs = payload.runs ?? [];
        this.lastError = null;
      } else {
        const payload = res.payload as { error?: string };
        this.lastError = payload.error ?? `status ${res.status}`;
        this.runs = [];
      }
    } catch (e) {
      this.lastError = e instanceof Error ? e.message : String(e);
      this.runs = [];
    }
    this._onDidChangeTreeData.fire();
  }

  /** La cascada de un run DURABLE: /api/trace (orden + estado) + /api/flow-trace
   * (tools reales por agente; si no se puede reconstruir, las declaradas). */
  private async loadDurableDetail(executionId: string): Promise<void> {
    try {
      const traceRes = await this.bridges
        .get("graphagents")
        .request({ method: "GET", path: "/api/trace", params: { execution_id: executionId } });
      if (traceRes.status !== 200) {
        const payload = traceRes.payload as { error?: string };
        this.details.set(executionId, { error: payload.error ?? `status ${traceRes.status}` });
        this._onDidChangeTreeData.fire();
        return;
      }
      const steps = ((traceRes.payload as { steps?: TraceStep[] }).steps ?? []);
      let nodeTraces: Record<string, ToolCall[]> = {};
      try {
        const ftRes = await this.bridges
          .get("graphagents")
          .request({ method: "GET", path: "/api/flow-trace", params: { execution_id: executionId } });
        if (ftRes.status === 200) {
          nodeTraces = (ftRes.payload as { node_traces?: Record<string, ToolCall[]> }).node_traces ?? {};
        }
      } catch {
        // sin flow-trace la cascada usa las tools DECLARADAS del plan — honesto igual
      }
      this.details.set(executionId, { steps: toStepEntries(steps, nodeTraces) });
    } catch (e) {
      this.details.set(executionId, { error: e instanceof Error ? e.message : String(e) });
    }
    this._onDidChangeTreeData.fire();
  }

  getTreeItem(element: RunNode): vscode.TreeItem {
    if (element.kind === "info") {
      const item = new vscode.TreeItem(element.message, vscode.TreeItemCollapsibleState.None);
      item.iconPath = new vscode.ThemeIcon("info");
      return item;
    }
    if (element.kind === "step") {
      const { step } = element;
      const item = new vscode.TreeItem(
        `${step.order}. ${step.agent}`,
        step.tools.length > 0 ? vscode.TreeItemCollapsibleState.Collapsed : vscode.TreeItemCollapsibleState.None,
      );
      item.description = [step.status, step.ms != null ? `${step.ms}ms` : null, step.retries ? `↻${step.retries}` : null]
        .filter(Boolean)
        .join(" · ");
      item.iconPath = new vscode.ThemeIcon(statusIcon(step.status));
      item.tooltip = `Orden ${step.order} — click abre el nodo en el grafo`;
      item.command = {
        command: "acktos.focusNode",
        title: "Ver en grafo",
        arguments: ["graphagents", `agent:${step.agent}`],
      };
      return item;
    }
    if (element.kind === "toolCall") {
      const item = new vscode.TreeItem(`${element.order}  ${element.tool}`, vscode.TreeItemCollapsibleState.None);
      item.iconPath = new vscode.ThemeIcon("tools");
      item.command = {
        command: "acktos.focusNode",
        title: "Ver en grafo",
        arguments: ["graphagents", `tool:${element.tool}`],
      };
      return item;
    }
    if (element.kind === "localRun") {
      const item = new vscode.TreeItem(`⚡ ${element.run.title}`, vscode.TreeItemCollapsibleState.Collapsed);
      item.description = "local · determinista";
      item.iconPath = new vscode.ThemeIcon("zap");
      item.tooltip =
        "Ejecución en proceso (fixtures del caso, sin AgentSpan) — expandí para la cascada; click re-ejecuta y muestra el detalle";
      item.command = {
        command: "acktos.runLocalCase",
        title: "Ejecutar local",
        arguments: [element.run.caseId, element.run.title],
      };
      return item;
    }
    const { run } = element;
    const item = new vscode.TreeItem(`${run.agent} — ${run.execution_id.slice(0, 8)}`, vscode.TreeItemCollapsibleState.Collapsed);
    item.description = run.status;
    item.iconPath = new vscode.ThemeIcon(statusIcon(run.status));
    item.tooltip = "Expandí para la cascada de ejecución; click abre el trace en el canvas";
    item.command = {
      command: "acktos.viewTrace",
      title: "Ver trace",
      arguments: [run.execution_id, run.agent],
    };
    return item;
  }

  getChildren(element?: RunNode): vscode.ProviderResult<RunNode[]> {
    if (element?.kind === "run" || element?.kind === "localRun") {
      const key = element.kind === "run" ? element.run.execution_id : `local:${element.run.caseId}`;
      const detail = this.details.get(key);
      if (!detail) {
        // durable: lazy — se resuelve al primer expand (los locales ya vienen)
        this.details.set(key, "loading");
        if (element.kind === "run") {
          void this.loadDurableDetail(key);
        }
        return [{ kind: "info", message: "cargando la cascada…" }];
      }
      if (detail === "loading") {
        return [{ kind: "info", message: "cargando la cascada…" }];
      }
      if ("error" in detail) {
        return [{ kind: "info", message: `sin detalle: ${detail.error}` }];
      }
      return detail.steps.map((step): RunNode => ({ kind: "step", runKey: key, step }));
    }
    if (element?.kind === "step") {
      return element.step.tools.map(
        (t): RunNode => ({ kind: "toolCall", id: `${element.runKey}:${t.order}:${t.tool}`, order: t.order, tool: t.tool }),
      );
    }
    if (element) {
      return [];
    }
    const locals: RunNode[] = this.localRuns.map((run, index) => ({ kind: "localRun" as const, run, index }));
    // default: SOLO lo lanzado en esta sesión — el histórico completo de
    // AgentSpan no suma para el loop de desarrollo (toggle ⟲ para verlo).
    const durables = this.showAll ? this.runs : this.runs.filter((r) => this.sessionEids.has(r.execution_id));
    const durableNodes: RunNode[] = durables.map((run) => ({ kind: "run" as const, run }));

    // AgentSpan caído solo es noticia si el usuario está usando el durable
    // (lanzó runs en la sesión o pidió el histórico) — el flujo local no lo necesita.
    if (this.lastError && (this.showAll || this.sessionEids.size > 0)) {
      return [
        ...locals,
        {
          kind: "info",
          message: `AgentSpan no disponible: ${this.lastError} — usá "Iniciar AgentSpan" (⚙) o corré los cases en local (⚡)`,
        },
      ];
    }
    if (locals.length === 0 && durableNodes.length === 0) {
      return [
        {
          kind: "info",
          message: this.showAll
            ? "Sin ejecuciones recientes."
            : "Sin ejecuciones en esta sesión — click en un case (⚡ local) o ⟲ para el histórico.",
        },
      ];
    }
    return [...locals, ...durableNodes];
  }

  dispose(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
    }
    this._onDidChangeTreeData.dispose();
  }
}
