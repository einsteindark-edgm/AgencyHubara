import * as vscode from "vscode";
import { HubaraTestController } from "./controller";

const ACTIVE_PLAN_KEY = "hubara.studio.activePlanId";

/** Selector de plan en la status bar — el equivalente al scheme picker de
 * Xcode (§2.5). Muestra el plan activo; click abre un QuickPick y CORRE el
 * plan elegido de inmediato (no solo lo marca para un futuro ▶). */
export class PlanStatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private activePlanId: string | undefined;
  private readonly disposables: vscode.Disposable[] = [];

  constructor(
    private readonly controller: HubaraTestController,
    private readonly ctx: vscode.ExtensionContext,
  ) {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
    this.item.command = "hubara.selectPlan";
    this.disposables.push(this.item, this.controller.onPlansChanged(() => this.render()));
    this.activePlanId = ctx.workspaceState.get<string>(ACTIVE_PLAN_KEY);
    this.render();
    this.item.show();
  }

  private render(): void {
    const plan = this.controller.plans.find((p) => p.id === this.activePlanId);
    this.item.text = plan ? `$(beaker) Plan: ${plan.name}` : "$(beaker) Plan: (elegir)";
    this.item.tooltip = plan
      ? `${plan.description ?? ""}\n\nClick para cambiar de plan o volver a correrlo.`
      : "Click para elegir un test plan de Hubara Studio.";
  }

  async selectAndRun(): Promise<void> {
    if (this.controller.plans.length === 0) {
      void vscode.window.showWarningMessage("Hubara Studio: no encontré ningún *.hubaraplan.yaml en test-plans/.");
      return;
    }
    const picks = this.controller.plans.map((p) => ({ label: p.name, description: p.description, id: p.id }));
    const choice = await vscode.window.showQuickPick(picks, { placeHolder: "¿Qué plan corremos?" });
    if (!choice) {
      return;
    }
    this.activePlanId = choice.id;
    void this.ctx.workspaceState.update(ACTIVE_PLAN_KEY, this.activePlanId);
    this.render();
    await this.controller.runPlanById(this.activePlanId);
  }

  async runActive(): Promise<void> {
    if (!this.activePlanId) {
      await this.selectAndRun();
      return;
    }
    await this.controller.runPlanById(this.activePlanId);
  }

  dispose(): void {
    for (const d of this.disposables) {
      d.dispose();
    }
  }
}
