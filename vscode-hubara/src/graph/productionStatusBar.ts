import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";

const POLL_MS = 15000;

interface ProductionStatus {
  saved?: boolean;
  dirty?: boolean;
}

/** "¿está guardado?" — status bar item que pollea `/api/production-status`
 * (§F5). Click dispara `hubara.saveProduction`. Solo tiene sentido para
 * GraphAgents (System Map no tiene noción de producción/snapshot). */
export class ProductionStatusBar implements vscode.Disposable {
  private readonly item: vscode.StatusBarItem;
  private pollTimer?: NodeJS.Timeout;

  constructor(private readonly bridges: BridgeHub) {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 90);
    this.item.command = "hubara.saveProduction";
    this.item.text = "$(loading~spin) GraphAgents";
    this.item.show();
    void this.poll();
    this.pollTimer = setInterval(() => void this.poll(), POLL_MS);
  }

  refresh(): void {
    void this.poll();
  }

  private async poll(): Promise<void> {
    try {
      const res = await this.bridges.get("graphagents").request({ method: "GET", path: "/api/production-status" });
      if (res.status !== 200) {
        this.item.text = "$(question) GraphAgents: ?";
        this.item.tooltip = "No pude leer /api/production-status";
        return;
      }
      const payload = res.payload as ProductionStatus;
      if (payload.dirty) {
        this.item.text = "$(circle-filled) GraphAgents: dirty";
        this.item.tooltip = "El wiring cambió desde el último save — click para guardar producción";
      } else {
        this.item.text = "$(check) GraphAgents: saved";
        this.item.tooltip = "El wiring matchea el snapshot bendecido (production.yaml)";
      }
    } catch (e) {
      this.item.text = "$(question) GraphAgents: ?";
      this.item.tooltip = String(e);
    }
  }

  dispose(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
    }
    this.item.dispose();
  }
}
