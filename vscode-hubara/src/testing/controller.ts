import * as path from "node:path";
import * as vscode from "vscode";
import { discoverPlans, HubaraPlan, suitesForPlan } from "./plans";
import { makeSuiteResolver } from "./resolve";
import { ResolvedCommand, runSuite, SuiteRunResult } from "./runner";
import { LADO_LABEL, Lado, SuiteDef, SUITES } from "./suites";

/**
 * TestController "Hubara Studio" — el panel de ejecución estilo Xcode (§2.5).
 * Árbol estático: 3 lados (GraphAgents / Hubara Backend / Frontend) → suites
 * (los "bundles atómicos", espejo 1:1 de los comandos de `/hubara-gates` y
 * `/graphagents-gates`) → casos (poblados lazy tras el primer run vía JUnit).
 *
 * Un run profile por CADA test plan descubierto (`test-plans/*.hubaraplan.yaml`)
 * + un profile "Run" default que corre exactamente lo seleccionado (o todo).
 */
export class HubaraTestController implements vscode.Disposable {
  readonly controller: vscode.TestController;
  private readonly ladoItems = new Map<Lado, vscode.TestItem>();
  private readonly suiteItems = new Map<string, vscode.TestItem>();
  private readonly caseItems = new Map<string, vscode.TestItem>();
  private planProfiles: vscode.TestRunProfile[] = [];
  private resolveCommand: (s: SuiteDef) => ResolvedCommand;
  private _plans: HubaraPlan[] = [];
  private readonly disposables: vscode.Disposable[] = [];
  private readonly _onPlansChanged = new vscode.EventEmitter<HubaraPlan[]>();
  readonly onPlansChanged = this._onPlansChanged.event;

  constructor(
    private readonly ctx: vscode.ExtensionContext,
    repoRoot: string,
    private readonly resolvePathFn: (value: string, root: string) => string,
  ) {
    this.controller = vscode.tests.createTestController("hubaraStudio", "Hubara Studio");
    this.controller.refreshHandler = () => this.loadPlans();
    this.disposables.push(this.controller, this._onPlansChanged);
    this.resolveCommand = makeSuiteResolver(repoRoot, resolvePathFn);
    this.buildStaticTree();
    this.disposables.push(
      this.controller.createRunProfile(
        "Run",
        vscode.TestRunProfileKind.Run,
        (request, token) => this.executeRun(request, token, null),
        true,
      ),
    );
    void this.loadPlans();
  }

  get plans(): readonly HubaraPlan[] {
    return this._plans;
  }

  /** La config de `hubara.*` cambió — reconstruir el resolvedor de comandos. */
  onConfigChanged(repoRoot: string): void {
    this.resolveCommand = makeSuiteResolver(repoRoot, this.resolvePathFn);
  }

  private buildStaticTree(): void {
    for (const lado of ["graphagents", "backend", "frontend"] as Lado[]) {
      const item = this.controller.createTestItem(`lado:${lado}`, LADO_LABEL[lado]);
      this.controller.items.add(item);
      this.ladoItems.set(lado, item);
    }
    for (const suite of SUITES) {
      const parent = this.ladoItems.get(suite.lado)!;
      const item = this.controller.createTestItem(suite.id, suite.label);
      item.description = suite.description;
      item.tags = [new vscode.TestTag(suite.lado), new vscode.TestTag(suite.tipo)];
      parent.children.add(item);
      this.suiteItems.set(suite.id, item);
    }
  }

  async loadPlans(): Promise<void> {
    for (const p of this.planProfiles) {
      p.dispose();
    }
    this.planProfiles = [];
    this._plans = await discoverPlans(this.plansDir());
    for (const plan of this._plans) {
      const profile = this.controller.createRunProfile(
        plan.name,
        vscode.TestRunProfileKind.Run,
        (request, token) => this.executeRun(request, token, plan),
        false,
      );
      this.planProfiles.push(profile);
    }
    this._onPlansChanged.fire(this._plans);
  }

  private plansDir(): string {
    return path.join(this.ctx.extensionUri.fsPath, "test-plans");
  }

  /** Dispara un plan por id sin pasar por el picker de profiles del Test
   * Explorer — lo usa la status bar (§2.5, "scheme selector" estilo Xcode). */
  async runPlanById(planId: string): Promise<void> {
    const plan = this._plans.find((p) => p.id === planId);
    if (!plan) {
      void vscode.window.showWarningMessage(`Hubara Studio: el plan '${planId}' no existe (¿se borró el YAML?).`);
      return;
    }
    const targets = suitesForPlan(plan, SUITES)
      .map((s) => this.suiteItems.get(s.id))
      .filter((x): x is vscode.TestItem => x !== undefined);
    if (targets.length === 0) {
      void vscode.window.showWarningMessage(`Hubara Studio: el plan '${plan.name}' no matchea ninguna suite.`);
      return;
    }
    const request = new vscode.TestRunRequest(targets);
    const cts = new vscode.CancellationTokenSource();
    this.disposables.push(cts);
    await this.executeRun(request, cts.token, plan);
  }

  /** Reporta un resultado AD-HOC (fuera del ciclo normal de `executeRun`) al
   * Test Explorer — lo usa "▶ Replay" de un caso puntual (F4): el usuario
   * dispara UN caso desde el CatalogTree/CodeLens y el pass/fail queda con
   * historia real en el panel de tests, no solo en un toast. */
  reportAdHocResult(suiteId: string, caseKey: string, label: string, ok: boolean, message?: string): void {
    const parent = this.suiteItems.get(suiteId);
    if (!parent) {
      return;
    }
    const key = `${suiteId}::adhoc::${caseKey}`;
    let child = this.caseItems.get(key);
    if (!child) {
      child = this.controller.createTestItem(key, label);
      parent.children.add(child);
      this.caseItems.set(key, child);
    }
    const run = this.controller.createTestRun(new vscode.TestRunRequest([child]));
    run.started(child);
    if (ok) {
      run.passed(child);
    } else {
      run.failed(child, new vscode.TestMessage(message ?? "no matcheó"));
    }
    run.end();
  }

  private suiteOwning(item: vscode.TestItem): vscode.TestItem | undefined {
    let cur: vscode.TestItem | undefined = item;
    while (cur) {
      if (this.suiteItems.has(cur.id)) {
        return cur;
      }
      cur = cur.parent;
    }
    return undefined;
  }

  private expandToSuites(items: readonly vscode.TestItem[]): Set<vscode.TestItem> {
    const acc = new Set<vscode.TestItem>();
    const ladoSet = new Set(this.ladoItems.values());
    const visit = (item: vscode.TestItem) => {
      if (this.suiteItems.has(item.id)) {
        acc.add(item);
        return;
      }
      if (ladoSet.has(item)) {
        item.children.forEach((child) => visit(child));
        return;
      }
      const owner = this.suiteOwning(item);
      if (owner) {
        acc.add(owner);
      }
    };
    for (const item of items) {
      visit(item);
    }
    return acc;
  }

  private computeTargets(request: vscode.TestRunRequest, plan: HubaraPlan | null): vscode.TestItem[] {
    // El exclude se expande a suites igual que el include: excluir el item de
    // un lado ("Hubara Backend") debe excluir sus suites hijas, no solo su id.
    const excluded = new Set([...this.expandToSuites(request.exclude ?? [])].map((i) => i.id));
    if (request.include && request.include.length > 0) {
      return [...this.expandToSuites(request.include)].filter((item) => !excluded.has(item.id));
    }
    if (plan) {
      const wanted = new Set(suitesForPlan(plan, SUITES).map((s) => s.id));
      return [...this.suiteItems.entries()]
        .filter(([id]) => wanted.has(id) && !excluded.has(id))
        .map(([, item]) => item);
    }
    return [...this.suiteItems.values()].filter((item) => !excluded.has(item.id));
  }

  private async executeRun(
    request: vscode.TestRunRequest,
    token: vscode.CancellationToken,
    plan: HubaraPlan | null,
  ): Promise<void> {
    const run = this.controller.createTestRun(request);
    const targets = this.computeTargets(request, plan);
    for (const item of targets) {
      run.enqueued(item);
    }

    const runOne = async (item: vscode.TestItem): Promise<boolean> => {
      if (token.isCancellationRequested) {
        run.skipped(item);
        return true;
      }
      const suite = SUITES.find((s) => s.id === item.id);
      if (!suite) {
        return true;
      }
      run.started(item);
      const resolved = this.resolveCommand(suite);
      const result = await runSuite(suite, resolved, token, (chunk) => run.appendOutput(toCrlf(chunk), undefined, item));
      return this.applyResult(run, item, suite, result);
    };

    const parallel = plan?.parallel ?? true;
    if (parallel) {
      await Promise.all(targets.map(runOne));
    } else {
      const failFast = plan?.failFast ?? false;
      for (const item of targets) {
        const ok = await runOne(item);
        if (failFast && !ok) {
          for (const rest of targets.slice(targets.indexOf(item) + 1)) {
            run.skipped(rest);
          }
          break;
        }
      }
    }
    run.end();
  }

  /** Aplica el resultado de una suite al TestRun; devuelve true si pasó. Con
   * JUnit, puebla/actualiza los casos como hijos (granularidad por-test); sin
   * él (suites CLI puras), el veredicto es el exit code + tail del output. */
  private applyResult(run: vscode.TestRun, item: vscode.TestItem, suite: SuiteDef, result: SuiteRunResult): boolean {
    if (result.cases && result.cases.length > 0) {
      let anyFailed = false;
      for (const c of result.cases) {
        const caseId = `${suite.id}::${c.classname}::${c.name}`;
        let child = this.caseItems.get(caseId);
        if (!child) {
          child = this.controller.createTestItem(caseId, c.name);
          item.children.add(child);
          this.caseItems.set(caseId, child);
        }
        const durationMs = c.time * 1000;
        if (c.skipped) {
          run.skipped(child);
        } else if (c.failureMessage) {
          anyFailed = true;
          run.failed(child, new vscode.TestMessage(c.failureMessage), durationMs);
        } else {
          run.passed(child, durationMs);
        }
      }
      const ok = !anyFailed && result.exitCode === 0;
      if (ok) {
        run.passed(item);
      } else {
        run.failed(item, new vscode.TestMessage(anyFailed ? `${suite.label}: hay casos en rojo` : `exit ${result.exitCode}`));
      }
      return ok;
    }
    if (result.exitCode === 0) {
      run.passed(item);
      return true;
    }
    const tail = result.output.trim().split("\n").slice(-40).join("\n");
    run.failed(item, new vscode.TestMessage(tail || `exit ${result.exitCode}`));
    return false;
  }

  dispose(): void {
    for (const p of this.planProfiles) {
      p.dispose();
    }
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}

function toCrlf(text: string): string {
  // La Testing API espera \r\n para que el output se vea como una terminal.
  return text.replace(/\r?\n/g, "\r\n");
}
