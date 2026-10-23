// Forge Console — el panel webview (dist/forgeview.js). Piel sobre el CLI:
// cada acción del panel se traduce a `forge <cmd>` / `migrate <cmd>` vía
// ForgeService y el output vuelve streameado. Los secretos NUNCA pasan por acá:
// las llaves de los pasos automáticos se leen del entorno con que se abrió
// VS Code (la UI solo dice si están o no) y los secretos del cliente van a SSM
// por los CLIs de provisioning, fuera de banda.

import * as fs from "fs";
import * as path from "path";
import * as vscode from "vscode";
import { ForgeService } from "./forgeService";
import { ForgeInbound, ForgeOutbound } from "./messages";
import { checkDestPath, SLUG_RE } from "./pure";

export class ForgeConsolePanel {
  static current: ForgeConsolePanel | undefined;
  private readonly disposables: vscode.Disposable[] = [];
  /** hasta que el webview dice "ready", lo que se postea se encola (si no, se pierde) */
  private ready = false;
  private queue: ForgeOutbound[] = [];

  static open(ctx: vscode.ExtensionContext, service: ForgeService): ForgeConsolePanel {
    if (ForgeConsolePanel.current) {
      ForgeConsolePanel.current.panel.reveal();
      return ForgeConsolePanel.current;
    }
    const panel = vscode.window.createWebviewPanel(
      "forge.console",
      "Forge Console",
      vscode.ViewColumn.Active,
      { enableScripts: true, retainContextWhenHidden: true },
    );
    panel.iconPath = new vscode.ThemeIcon("flame");
    ForgeConsolePanel.current = new ForgeConsolePanel(panel, ctx, service);
    return ForgeConsolePanel.current;
  }

  private constructor(
    private readonly panel: vscode.WebviewPanel,
    ctx: vscode.ExtensionContext,
    private readonly service: ForgeService,
  ) {
    panel.webview.html = this.getHtml(panel.webview, ctx);
    this.disposables.push(
      panel.onDidDispose(() => this.dispose()),
      panel.webview.onDidReceiveMessage((msg: ForgeInbound) => void this.onMessage(msg)),
      service.onRun((ev) => {
        if (ev.kind === "start") this.post({ type: "runStart", runId: ev.runId, cmd: ev.cmd ?? "" });
        if (ev.kind === "line")
          this.post({ type: "runLine", runId: ev.runId, line: ev.line ?? "", tone: ev.tone });
        if (ev.kind === "end") this.post({ type: "runEnd", runId: ev.runId, code: ev.code ?? -1 });
        if (ev.kind === "notice") this.post({ type: "notice", text: ev.line ?? "", tone: ev.tone ?? "warn" });
      }),
      service.onBusy((cmd) => this.post({ type: "busy", busy: cmd !== null, cmd: cmd ?? "" })),
      service.onFleetChanged(() => this.sendFleet()),
    );
  }

  private post(msg: ForgeOutbound): void {
    if (!this.ready) {
      this.queue.push(msg);
      return;
    }
    void this.panel.webview.postMessage(msg);
  }

  private sendFleet(): void {
    this.post({
      type: "fleet",
      clients: this.service.listClients(),
      repoRoot: this.service.repoRoot,
    });
  }

  private async onMessage(msg: ForgeInbound): Promise<void> {
    switch (msg.type) {
      case "ready": {
        // (re)carga del webview: primero el estado, después lo encolado
        this.ready = true;
        this.sendFleet();
        const busy = this.service.busy;
        this.post({ type: "busy", busy: busy !== null, cmd: busy ?? "" });
        const queued = this.queue;
        this.queue = [];
        for (const m of queued) this.post(m);
        void this.service.checkPython().then((r) => this.post({ type: "python", ...r }));
        break;
      }
      case "refresh":
        this.sendFleet();
        void this.service.checkPython().then((r) => this.post({ type: "python", ...r }));
        break;
      case "initClient":
        if (SLUG_RE.test(msg.slug)) {
          // el nombre real desde el principio: domain.yaml y las voces lo citan
          const company = msg.company.trim();
          await this.service.runForge("init", company ? [msg.slug, "--company", company] : [msg.slug]);
        } else {
          void vscode.window.showErrorMessage(
            `Forge: nombre corto inválido «${msg.slug}» — de 2 a 20 minúsculas y dígitos, sin _ ni guiones ` +
              "(va en nombres de AWS como los buckets S3, que no los aceptan)",
          );
        }
        break;
      case "plan":
        await this.service.runForge("plan", [msg.slug]);
        break;
      case "apply":
        // lo mismo que el paso S1: migrate deja registrado el clon (carpeta y paso hecho)
        await this.migrateRun(msg.slug, "clone", msg.dest, msg.allowTodos);
        break;
      case "verify": {
        const dest = msg.dest.trim();
        if (!this.requireClone(dest)) break;
        await this.service.runForge("verify", [dest, "--client", msg.slug]);
        break;
      }
      case "publish": {
        // publish solo IMPRIME los comandos gh (el operador los corre a mano).
        const dest = msg.dest.trim();
        if (!this.requireClone(dest)) break;
        await this.service.runForge("publish", [dest, "--client", msg.slug]);
        break;
      }
      case "inspect": {
        const destState = this.service.destState(msg.dest);
        let steps = null;
        let recordedDest: string | null = null;
        let error: string | null = null;
        try {
          ({ steps, recordedDest } = await this.service.migrationStatus(msg.slug, msg.dest));
        } catch (e) {
          error = e instanceof Error ? e.message : String(e);
        }
        this.post({ type: "inspected", slug: msg.slug, dest: msg.dest, destState, steps, recordedDest, error });
        break;
      }
      case "migrateRun":
        await this.migrateRun(msg.slug, msg.step, msg.dest, msg.allowTodos);
        break;
      case "migrateDone": {
        const ok = await vscode.window.showInformationMessage(
          `¿Marcar el paso «${msg.step}» de ${msg.slug} como hecho?`,
          {
            modal: true,
            detail:
              "Hazlo solo después de correr los comandos que se mostraron y comprobar que terminaron bien.",
          },
          "Marcar como hecho",
        );
        if (ok) await this.service.runMigrate(["done", msg.slug, msg.step]);
        break;
      }
      case "openPath":
        await this.openPath(msg.fsPath);
        break;
      case "openSettings":
        void vscode.commands.executeCommand("workbench.action.openSettings", "acktos.forge.python");
        break;
      case "cancel": {
        if (!this.service.busy) break;
        const ok = await vscode.window.showWarningMessage(
          `¿Detener «${this.service.busy}»?`,
          {
            modal: true,
            detail:
              "El comando se corta donde vaya. Un paso que estaba creando algo en un servicio externo " +
              "puede dejarlo a medias: vuelve a correrlo (los pasos se pueden repetir) o revísalo en ese servicio.",
          },
          "Detener",
        );
        if (ok === "Detener") this.service.cancel();
        break;
      }
      case "pickDest": {
        const picked = await vscode.window.showOpenDialog({
          canSelectFiles: false,
          canSelectFolders: true,
          canSelectMany: false,
          openLabel: "Carpeta destino del clon",
        });
        if (picked?.[0]) {
          this.post({ type: "destPicked", slug: msg.slug, dest: picked[0].fsPath });
        }
        break;
      }
    }
  }

  private async migrateRun(slug: string, step: string, rawDest: string, allowTodos: boolean): Promise<void> {
    const dest = this.checkDest(rawDest);
    if (dest === null) return;
    if (step === "clone") {
      if (!(await this.confirmClone(slug, dest, allowTodos))) return;
    } else if (["supabase", "medusa", "medusa-seed", "temporal"].includes(step)) {
      // Crean recursos REALES en cuentas de terceros (con la llave del entorno).
      const ok = await vscode.window.showWarningMessage(
        `Ejecutar el paso «${step}» para ${slug}`,
        {
          modal: true,
          detail:
            "Crea o configura recursos reales del cliente en el servicio externo, con la llave que " +
            "está en tu entorno. No toca nada de Hubara.",
        },
        "Ejecutar",
      );
      if (ok !== "Ejecutar") return;
    }
    const args = ["run", slug, step, "--dest", dest];
    if (allowTodos) args.push("--allow-todos");
    await this.service.runMigrate(args);
  }

  /** La carpeta destino, sin espacios a los lados; null (y el aviso) si no sirve. */
  private checkDest(raw: string): string | null {
    const dest = raw.trim();
    const problem = checkDestPath(dest);
    if (problem) {
      void vscode.window.showErrorMessage(`Forge: carpeta destino del clon — ${problem}.`);
      return null;
    }
    return dest;
  }

  private requireClone(dest: string): boolean {
    if (this.service.destState(dest).isClone) return true;
    void vscode.window.showErrorMessage(
      `Forge: en ${dest} todavía no hay un clon forjado. Usa «Forjar» primero.`,
    );
    return false;
  }

  /**
   * Confirmación modal SIEMPRE antes de forjar (crea la carpeta del clon), con
   * el destino Y el origen visibles: rama, commit, y si hay cambios sin commit
   * (no viajan: el clon sale del último commit) o si la rama no es main.
   */
  private async confirmClone(slug: string, dest: string, allowTodos: boolean): Promise<boolean> {
    const src = await this.service.sourceInfo();
    const lines = [
      `Destino: ${dest}`,
      `Origen: rama ${src.branch} · commit ${src.sha}`,
      "",
    ];
    if (src.branch !== "main") {
      lines.push(
        `⚠ La rama «${src.branch}» no es main: el clon saldrá de una rama de trabajo, no de la versión estable.`,
      );
    }
    if (src.dirtyCount > 0) {
      lines.push(
        `⚠ Hay ${src.dirtyCount} cambio(s) sin commit: NO viajan al clon (sale del último commit).`,
      );
    }
    if (allowTodos) {
      lines.push("⚠ Clon de prueba: se permite que queden textos TODO-BRAND sin redactar.");
    }
    lines.push("Crea la carpeta del repo nuevo. No toca AWS ni GitHub.");
    const ok = await vscode.window.showWarningMessage(
      `Forjar el clon de «${slug}»`,
      { modal: true, detail: lines.join("\n") },
      "Forjar",
    );
    return ok === "Forjar";
  }

  private async openPath(fsPath: string): Promise<void> {
    const uri = vscode.Uri.file(fsPath);
    try {
      if (fs.statSync(fsPath).isDirectory()) {
        await vscode.commands.executeCommand("revealInExplorer", uri);
      } else if (path.basename(fsPath) === "NEXT_STEPS.md") {
        // la guía del clon se LEE (vista previa); los textos del bundle se editan
        await vscode.commands.executeCommand("markdown.showPreview", uri);
      } else {
        await vscode.commands.executeCommand("vscode.open", uri);
      }
    } catch {
      void vscode.window.showErrorMessage(`Forge: no pude abrir ${fsPath}`);
    }
  }

  private getHtml(webview: vscode.Webview, ctx: vscode.ExtensionContext): string {
    const scriptUri = webview.asWebviewUri(
      vscode.Uri.joinPath(ctx.extensionUri, "dist", "forgeview.js"),
    );
    const styleUri = webview.asWebviewUri(
      vscode.Uri.joinPath(ctx.extensionUri, "dist", "forgeview.css"),
    );
    const csp = [
      "default-src 'none'",
      `img-src ${webview.cspSource} data:`,
      `style-src ${webview.cspSource} 'unsafe-inline'`,
      `script-src ${webview.cspSource}`,
      `font-src ${webview.cspSource}`,
    ].join("; ");
    return `<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <link rel="stylesheet" href="${styleUri}" />
  <title>Forge Console</title>
</head>
<body>
  <div id="root"></div>
  <script src="${scriptUri}"></script>
</body>
</html>`;
  }

  private dispose(): void {
    ForgeConsolePanel.current = undefined;
    for (const d of this.disposables) d.dispose();
  }
}
