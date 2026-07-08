import * as vscode from "vscode";
import { BridgeResponse, Provider, PROVIDER_LABEL } from "./bridge/endpoints";
import { readHubaraConfig, repoRoot, resolvePath } from "./config";
import { BridgeHub, BridgeSpec, PythonBridge } from "./bridge/pythonBridge";
import { ManifestCodeLensProvider } from "./codelens/manifestCodeLens";
import { CertDecorationProvider } from "./decorations/certDecorations";
import { ManifestDiagnostics } from "./diagnostics/manifestDiagnostics";
import { connectWithConfirmation } from "./graph/editOps";
import { GraphPanel, LocalRunResult } from "./graph/graphPanel";
import { ToolCall, TraceStep } from "./graph/messages";
import { ProductionStatusBar } from "./graph/productionStatusBar";
import { DEFAULT_FOCUS_DEPTH, focusOf, systemOf, workflowOf } from "./graph/scope";
import { registerYamlSchemas } from "./schemas/yamlSchemas";
import { HubaraTestController } from "./testing/controller";
import { PlanStatusBar } from "./testing/planStatusBar";
import { CatalogTreeProvider } from "./trees/catalogTree";
import { RunsTreeProvider } from "./trees/runsTree";
import { ExecutionViewProvider } from "./views/executionView";

let hub: Hub | undefined;
let testController: HubaraTestController | undefined;
let planStatusBar: PlanStatusBar | undefined;
let manifestDiagnostics: ManifestDiagnostics | undefined;
let certDecorations: CertDecorationProvider | undefined;
let runsTree: RunsTreeProvider | undefined;
let productionStatusBar: ProductionStatusBar | undefined;
let output: vscode.OutputChannel;

export function activate(ctx: vscode.ExtensionContext): void {
  output = vscode.window.createOutputChannel("Acktos Studio");
  ctx.subscriptions.push(output);

  hub = new Hub(output);
  ctx.subscriptions.push(hub);

  const catalogTree = new CatalogTreeProvider(hub);
  ctx.subscriptions.push(vscode.window.registerTreeDataProvider("acktos.catalogTree", catalogTree));

  runsTree = new RunsTreeProvider(hub);
  const runsView = vscode.window.createTreeView("acktos.runsTree", { treeDataProvider: runsTree });
  ctx.subscriptions.push(runsTree, runsView, runsTree.attach(runsView));

  // El panel nativo "Ejecución" (F10) — abajo, junto a Terminal: la cascada
  // del run + el detalle del nodo seleccionado. El GraphPanel le empuja los
  // traces; el canvas le manda las selecciones.
  const executionView = new ExecutionViewProvider(ctx, hub);
  GraphPanel.executionSink = executionView;
  ctx.subscriptions.push(
    vscode.window.registerWebviewViewProvider(ExecutionViewProvider.viewType, executionView, {
      webviewOptions: { retainContextWhenHidden: true },
    }),
  );

  productionStatusBar = new ProductionStatusBar(hub);
  ctx.subscriptions.push(productionStatusBar);

  testController = new HubaraTestController(ctx, repoRoot(), resolvePath);
  ctx.subscriptions.push(testController);
  planStatusBar = new PlanStatusBar(testController, ctx);
  ctx.subscriptions.push(planStatusBar);

  certDecorations = new CertDecorationProvider();
  ctx.subscriptions.push(certDecorations, vscode.window.registerFileDecorationProvider(certDecorations));
  // `lado` acota el refresh al bridge del lado que corrió — guardar un
  // manifest de GraphAgents no debe golpear el bridge/TCK de Hubara.
  const refreshDecorations = (lado?: "graphagents" | "backend") =>
    void certDecorations!.refresh(hub!, repoRoot(), graphAgentsCwd(), lado);

  // Las dos corridas eager del constructor ya disparan onRan → decoraciones
  // pobladas por ambos lados; no hace falta un tercer refresh directo acá.
  manifestDiagnostics = new ManifestDiagnostics(repoRoot(), resolvePath, refreshDecorations);
  ctx.subscriptions.push(manifestDiagnostics);

  const codeLensProvider = new ManifestCodeLensProvider();
  ctx.subscriptions.push(
    vscode.languages.registerCodeLensProvider({ pattern: "**/manifests/*.{agent,taskgraph}.yaml" }, codeLensProvider),
    vscode.languages.registerCodeLensProvider({ pattern: "**/tools/*/tool.yaml" }, codeLensProvider),
    vscode.languages.registerCodeLensProvider(
      { pattern: "**/frontend_dashboard/src/plugins/*/plugin.yaml" },
      codeLensProvider,
    ),
  );

  void registerYamlSchemas(ctx, output);

  ctx.subscriptions.push(
    // Cambió la config de los puentes → reconstruirlos; los paneles llegan al
    // nuevo a través del hub (no capturan instancias).
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("acktos")) {
        hub!.rebuild();
        catalogTree.refresh();
        testController!.onConfigChanged(repoRoot());
        manifestDiagnostics!.onConfigChanged(repoRoot());
        output.appendLine("[extension] settings de hubara cambiaron → puentes/comandos reconstruidos");
      }
    }),
    vscode.commands.registerCommand("acktos.openStudio", () => {
      GraphPanel.show(ctx, hub!);
    }),
    vscode.commands.registerCommand("acktos.openGraphAgents", () => {
      GraphPanel.show(ctx, hub!, systemOf("graphagents"));
    }),
    vscode.commands.registerCommand("acktos.openSystemMap", () => {
      GraphPanel.show(ctx, hub!, systemOf("systemmap"));
    }),
    vscode.commands.registerCommand("acktos.refreshGraphs", () => {
      GraphPanel.refreshActive();
      catalogTree.refresh();
      refreshDecorations();
    }),
    vscode.commands.registerCommand("acktos.focusNode", (system?: Provider, nodeId?: string) => {
      if (!system || !nodeId) {
        return; // solo invocable con args (codelens/inspector) — guard defensivo
      }
      GraphPanel.show(ctx, hub!, focusOf(system, nodeId, DEFAULT_FOCUS_DEPTH));
    }),
    // Un workflow COMPLETO en el canvas: todo lo alcanzable desde su raíz
    // (la vista "target" — desde el grupo Workflows del catálogo).
    vscode.commands.registerCommand("acktos.openWorkflow", (rootId?: string) => {
      if (!rootId) {
        return; // solo invocable desde el árbol (manda la raíz)
      }
      GraphPanel.show(ctx, hub!, workflowOf("graphagents", rootId));
    }),
    vscode.commands.registerCommand("acktos.restartBridges", () => {
      hub!.rebuild();
      output.appendLine("[extension] puentes reiniciados");
      void vscode.window.showInformationMessage("Acktos Studio: puentes Python reiniciados.");
    }),
    vscode.commands.registerCommand("acktos.selectPlan", () => planStatusBar!.selectAndRun()),
    vscode.commands.registerCommand("acktos.runActivePlan", () => planStatusBar!.runActive()),
    vscode.commands.registerCommand("acktos.checkManifest", async (system?: Provider) => {
      if (system !== "graphagents" && system !== "systemmap") {
        return; // solo invocable desde el codelens (manda el provider)
      }
      const summary = system === "graphagents" ? await manifestDiagnostics!.runGraphAgents() : await manifestDiagnostics!.runHubara();
      const label = PROVIDER_LABEL[system];
      if (summary.parseFailure) {
        void vscode.window.showErrorMessage(
          `Acktos Studio: ${label} — el check terminó con error pero no reconocí su salida (¿cambió el formato del CLI?). Ver Output → Acktos Studio.`,
        );
      } else if (summary.errors === 0 && summary.warnings === 0) {
        void vscode.window.showInformationMessage(`Acktos Studio: ${label} — check OK.`);
      } else {
        void vscode.window.showWarningMessage(
          `Acktos Studio: ${label} — ${summary.errors} error(es), ${summary.warnings} warning(s). Ver Problems.`,
        );
      }
    }),
    vscode.commands.registerCommand("acktos.refreshRuns", () => runsTree!.refresh()),
    vscode.commands.registerCommand("acktos.viewTrace", (executionId?: string, agent?: string) => {
      if (!executionId) {
        return; // solo invocable desde el árbol de Runs (manda los args)
      }
      GraphPanel.showTrace(ctx, hub!, executionId, agent ?? "");
    }),
    // La ejecución SENCILLA (⚡): corre el caso EN PROCESO con sus fixtures —
    // cero AgentSpan/Conductor — y muestra el detalle completo en el canvas.
    // Es el camino default (click en un case); el durable queda para probar
    // la infraestructura real.
    registerCaseCommand("acktos.runLocalCase", "/api/run-local", "ejecución local", (res, payload, caseId, title) => {
      if (res.status !== 200) {
        return false;
      }
      const result: LocalRunResult = {
        case: caseId,
        agent: typeof payload.agent === "string" ? payload.agent : caseId,
        strategy: typeof payload.strategy === "string" ? payload.strategy : undefined,
        seed: payload.seed,
        steps: Array.isArray(payload.steps) ? (payload.steps as TraceStep[]) : [],
        nodeTraces: (payload.node_traces ?? {}) as Record<string, ToolCall[]>,
        nodeAccs: (payload.node_accs ?? {}) as Record<string, unknown>,
      };
      GraphPanel.showLocalRun(ctx, hub!, result);
      // la cascada del árbol de Runs viaja ya resuelta (steps + ledgers)
      runsTree!.noteLocalRun({ caseId, title, agent: result.agent, steps: result.steps, nodeTraces: result.nodeTraces });
      return true;
    }),
    // AgentSpan (:6767) es OPCIONAL — este botón lo levanta en una terminal
    // visible cuando se quiere probar el runtime durable de verdad.
    vscode.commands.registerCommand("acktos.startInfra", () => {
      const cfg = readHubaraConfig(repoRoot());
      const existing = vscode.window.terminals.find((t) => t.name === "AgentSpan");
      if (existing) {
        existing.show();
        void vscode.window.showInformationMessage(
          `Acktos Studio: la terminal AgentSpan ya existe — si el server no corre, ejecutá ahí: ${cfg.agentspanStart}`,
        );
        return;
      }
      const term = vscode.window.createTerminal({ name: "AgentSpan", cwd: cfg.gaCwd });
      term.show();
      term.sendText(cfg.agentspanStart);
      void vscode.window.showInformationMessage(
        "Acktos Studio: levantando AgentSpan (:6767) — la primera vez descarga el server (~50MB). Los ▶ Run Durable quedan disponibles cuando el log diga que escucha.",
      );
    }),
    vscode.commands.registerCommand("acktos.toggleAllRuns", () => {
      const showAll = runsTree!.toggleShowAll();
      void vscode.window.showInformationMessage(
        showAll ? "Acktos Studio: Runs muestra el histórico completo de AgentSpan." : "Acktos Studio: Runs muestra solo esta sesión.",
      );
    }),
    // Los dos comandos por-caso comparten el esqueleto (normalización de
    // args del árbol, POST al bridge, toasts de error) vía registerCaseCommand.
    registerCaseCommand("acktos.replayCase", "/api/replay", "replay", (res, payload, caseId, title) => {
      if (res.status !== 200 && res.status !== 422) {
        return false;
      }
      const ok = payload.matches === true;
      testController!.reportAdHocResult(
        "graphagents/graphs",
        caseId,
        title,
        ok,
        ok ? undefined : "el output no matchea el golden — ver detalle en el panel de tests",
      );
      if (ok) {
        void vscode.window.showInformationMessage(`Acktos Studio: replay '${title}' → matches golden ✓`);
      } else {
        void vscode.window.showWarningMessage(`Acktos Studio: replay '${title}' → NO matchea el golden.`);
      }
      return true;
    }),
    registerCaseCommand("acktos.runDurableCase", "/api/run-durable", "run durable", (res, payload, caseId, title) => {
      const executionId = typeof payload.execution_id === "string" ? payload.execution_id : undefined;
      if (res.status !== 200 || !executionId) {
        // Camino de rescate explícito: el fallo típico es AgentSpan caído —
        // ofrecer levantar la infra o correr el MISMO caso en local (sin infra).
        void vscode.window
          .showErrorMessage(
            `Acktos Studio: run durable '${title}' — ${(payload.error as string | undefined) ?? `status ${res.status}`}`,
            "▶ Iniciar AgentSpan",
            "⚡ Ejecutar local",
          )
          .then((action) => {
            if (action === "▶ Iniciar AgentSpan") {
              void vscode.commands.executeCommand("acktos.startInfra");
            } else if (action === "⚡ Ejecutar local") {
              void vscode.commands.executeCommand("acktos.runLocalCase", caseId, title);
            }
          });
        return true; // error ya presentado (con acciones) — sin toast genérico
      }
      void vscode.window.showInformationMessage(`Acktos Studio: '${title}' corriendo (${executionId.slice(0, 8)}…)`);
      runsTree!.noteDurableLaunched(executionId);
      runsTree!.refresh();
      GraphPanel.showTrace(ctx, hub!, executionId, typeof payload.agent === "string" ? payload.agent : title);
      return true;
    }),
    // El menú contextual manda el elemento del árbol como único arg (mismo
    // shape que devuelve CatalogTreeProvider.getChildren): {system, nodeId, label}.
    vscode.commands.registerCommand("acktos.connectFromPicker", async (item?: { system: Provider; nodeId: string; label: string }) => {
      if (!item) {
        return; // solo invocable desde el menú contextual del catálogo
      }
      const { system, nodeId, label } = item;
      if (system !== "graphagents") {
        return; // edit mode es solo GraphAgents (system_map es read-only)
      }
      try {
        const res = await hub!.get("graphagents").request({ method: "GET", path: "/api/graph" });
        const payload = res.payload as { nodes?: Array<{ id: string; kind: string; label?: string }> };
        const candidates = (payload.nodes ?? []).filter((n) => n.kind === "agent" && n.id !== nodeId);
        if (candidates.length === 0) {
          void vscode.window.showWarningMessage("Acktos Studio: no hay agentes candidatos para conectar.");
          return;
        }
        const choice = await vscode.window.showQuickPick(
          candidates.map((n) => ({ label: n.label ?? n.id, description: n.id, id: n.id })),
          { placeHolder: `¿Desde qué agente conectar hacia ${label}?` },
        );
        if (!choice) {
          return;
        }
        if (await connectWithConfirmation(hub!, choice.id, nodeId)) {
          GraphPanel.refreshActive();
          catalogTree.refresh();
        }
      } catch (e) {
        void vscode.window.showErrorMessage(`Acktos Studio: no pude listar candidatos: ${e}`);
      }
    }),
    vscode.commands.registerCommand("acktos.saveProduction", async () => {
      try {
        const res = await hub!.get("graphagents").request({ method: "POST", path: "/api/save", body: {} });
        const payload = res.payload as { ok?: boolean; errors?: string[] };
        if (payload.ok) {
          void vscode.window.showInformationMessage("Acktos Studio: producción guardada (production.yaml).");
        } else {
          void vscode.window.showWarningMessage(`Acktos Studio: save rechazado — ${(payload.errors ?? []).join("; ")}`);
        }
      } catch (e) {
        void vscode.window.showErrorMessage(`Acktos Studio: save falló: ${e}`);
      } finally {
        productionStatusBar!.refresh();
      }
    }),
    vscode.commands.registerCommand("acktos.publishProduction", async () => {
      // git commit + push + PR — acción con efectos reales fuera del editor,
      // SIEMPRE confirmada antes de disparar (misma regla que cualquier
      // acción de blast-radius compartido).
      const choice = await vscode.window.showWarningMessage(
        "¿Publicar producción? Esto hace commit + push + abre un PR (manifests/ + production.yaml).",
        { modal: true },
        "Publicar",
      );
      if (choice !== "Publicar") {
        return;
      }
      try {
        const res = await hub!.get("graphagents").request({ method: "POST", path: "/api/publish", body: { push: true, pr: true } });
        const payload = res.payload as { ok?: boolean; errors?: string[]; pr_url?: string; branch?: string };
        if (payload.ok) {
          const action = payload.pr_url ? await vscode.window.showInformationMessage(`Acktos Studio: publicado (${payload.branch}).`, "Abrir PR") : undefined;
          if (action === "Abrir PR" && payload.pr_url) {
            void vscode.env.openExternal(vscode.Uri.parse(payload.pr_url));
          }
        } else {
          void vscode.window.showErrorMessage(`Acktos Studio: publish falló — ${(payload.errors ?? []).join("; ")}`);
        }
      } catch (e) {
        void vscode.window.showErrorMessage(`Acktos Studio: publish falló: ${e}`);
      } finally {
        productionStatusBar!.refresh();
      }
    }),
  );

  output.appendLine("[extension] Acktos Studio activo");
}

export function deactivate(): void {
  hub?.dispose();
}

type CaseArg = string | { caseId: string; title: string };

/** Single-click (item.command) manda (caseId, title) como args separados; el
 * menú contextual/ícono inline (view/item/context) manda el TreeItem completo
 * como único arg. Este helper normaliza las dos formas y comparte el POST al
 * bridge + los toasts de error; `onResponse` devuelve false para el toast
 * genérico de error. */
function registerCaseCommand(
  command: string,
  apiPath: string,
  verb: string,
  onResponse: (
    res: BridgeResponse,
    payload: Record<string, unknown> & { error?: string },
    caseId: string,
    title: string,
  ) => boolean,
): vscode.Disposable {
  return vscode.commands.registerCommand(command, async (arg?: CaseArg, argTitle?: string) => {
    if (arg == null) {
      return; // sin args (p. ej. paleta) — el comando vive en el árbol de casos
    }
    const { caseId, title } = typeof arg === "string" ? { caseId: arg, title: argTitle ?? arg } : arg;
    try {
      const res = await hub!.get("graphagents").request({ method: "POST", path: apiPath, body: { case: caseId } });
      const payload = res.payload as Record<string, unknown> & { error?: string };
      if (!onResponse(res, payload, caseId, title)) {
        void vscode.window.showErrorMessage(`Acktos Studio: ${verb} '${title}' — ${payload.error ?? `status ${res.status}`}`);
      }
    } catch (e) {
      void vscode.window.showErrorMessage(`Acktos Studio: ${verb} '${title}' falló: ${e}`);
    }
  });
}

/** Dueño de los PythonBridge vigentes; rebuild() los reemplaza en caliente. */
class Hub implements BridgeHub, vscode.Disposable {
  private bridges: Record<Provider, PythonBridge>;

  constructor(private readonly out: vscode.OutputChannel) {
    this.bridges = buildBridges(out);
  }

  get(provider: Provider): PythonBridge {
    return this.bridges[provider];
  }

  rebuild(): void {
    const old = this.bridges;
    this.bridges = buildBridges(this.out);
    old.graphagents.dispose();
    old.systemmap.dispose();
  }

  dispose(): void {
    this.bridges.graphagents.dispose();
    this.bridges.systemmap.dispose();
  }
}

function buildBridges(out: vscode.OutputChannel): Record<Provider, PythonBridge> {
  const cfg = readHubaraConfig(repoRoot());

  const graphagents: BridgeSpec = {
    label: "GraphAgents",
    command: [cfg.gaPython, "-m", "viewer.bridge"],
    cwd: cfg.gaCwd,
    // AGENTSPAN_SERVER_URL / LITELLM_PROXY_URL etc — infra local o AWS por settings.
    env: cfg.gaEnv,
  };
  const systemmap: BridgeSpec = {
    label: "System Map",
    command: [...cfg.backendCommand, "scripts/system_map_bridge.py"],
    cwd: cfg.backendCwd,
  };

  return {
    graphagents: new PythonBridge(graphagents, out),
    systemmap: new PythonBridge(systemmap, out),
  };
}

function graphAgentsCwd(): string {
  return readHubaraConfig(repoRoot()).gaCwd;
}
