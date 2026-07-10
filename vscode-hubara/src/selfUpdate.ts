import { spawn } from "node:child_process";
import * as fs from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";
import { repoRoot } from "./config";

/**
 * Self-update: Acktos Studio es una extensión SIDELOADED (VSIX privado) — el
 * Auto Update de VS Code solo cubre extensiones del Marketplace, así que acá
 * el update viene del propio repo: al activar comparamos la versión instalada
 * contra `vscode-hubara/package.json` del workspace; si el repo trae una más
 * nueva (llegó por `git pull`), re-empaquetamos el VSIX desde el source y lo
 * instalamos in-process. El operador solo ve un toast "Recargar".
 *
 * Requisito: `node_modules` presente en `<repoRoot>/vscode-hubara` (esbuild +
 * vsce). Los subprocesos corren con el node del PROPIO VS Code
 * (`ELECTRON_RUN_AS_NODE`) — el PATH del extension host no trae `node`.
 */

function versionParts(v: string): number[] {
  return v.split(".").map((n) => Number.parseInt(n, 10) || 0);
}

export function isNewer(a: string, b: string): boolean {
  const pa = versionParts(a);
  const pb = versionParts(b);
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    if ((pa[i] ?? 0) !== (pb[i] ?? 0)) {
      return (pa[i] ?? 0) > (pb[i] ?? 0);
    }
  }
  return false;
}

function runNodeScript(script: string, args: string[], cwd: string, out: vscode.OutputChannel): Promise<void> {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script, ...args], {
      cwd,
      env: { ...process.env, ELECTRON_RUN_AS_NODE: "1" },
    });
    child.stdout.on("data", (d) => out.append(String(d)));
    child.stderr.on("data", (d) => out.append(String(d)));
    child.on("error", reject);
    child.on("exit", (code) =>
      code === 0 ? resolve() : reject(new Error(`${path.basename(script)} salió con código ${code}`)),
    );
  });
}

function sourceDir(): string | null {
  const dir = path.join(repoRoot(), "vscode-hubara");
  return fs.existsSync(path.join(dir, "package.json")) ? dir : null;
}

/** Chequea y aplica el update. `manual: true` (comando de la paleta) reporta
 * también el caso "ya estás al día"; el chequeo de activación es silencioso
 * salvo que haya algo que hacer. */
export async function selfUpdate(ctx: vscode.ExtensionContext, out: vscode.OutputChannel, manual: boolean): Promise<void> {
  const dir = sourceDir();
  if (!dir) {
    if (manual) {
      void vscode.window.showWarningMessage("Acktos Studio: no encuentro vscode-hubara/ en el workspace — nada que actualizar.");
    }
    return;
  }
  const installed: string = ctx.extension.packageJSON.version;
  let source: string;
  try {
    source = JSON.parse(fs.readFileSync(path.join(dir, "package.json"), "utf8")).version;
  } catch (e) {
    out.appendLine(`[self-update] package.json del source ilegible: ${e}`);
    return;
  }
  if (!isNewer(source, installed)) {
    if (manual) {
      void vscode.window.showInformationMessage(`Acktos Studio ${installed} ya es la versión del repo.`);
    }
    return;
  }
  if (!fs.existsSync(path.join(dir, "node_modules"))) {
    void vscode.window.showWarningMessage(
      `Acktos Studio ${source} disponible en el repo (instalada: ${installed}), pero falta node_modules en vscode-hubara/ — corré npm ci ahí para habilitar el self-update.`,
    );
    return;
  }

  const vsix = path.join(dir, `acktos-studio-${source}.vsix`);
  try {
    await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Acktos Studio: actualizando ${installed} → ${source}…` },
      async (progress) => {
        progress.report({ message: "compilando" });
        await runNodeScript("esbuild.mjs", ["--production"], dir, out);
        progress.report({ message: "empaquetando VSIX" });
        await runNodeScript(
          path.join("node_modules", "@vscode", "vsce", "vsce"),
          ["package", "--no-dependencies", "-o", vsix],
          dir,
          out,
        );
        progress.report({ message: "instalando" });
        await vscode.commands.executeCommand("workbench.extensions.installExtension", vscode.Uri.file(vsix));
      },
    );
  } catch (e) {
    out.appendLine(`[self-update] falló: ${e}`);
    const pick = await vscode.window.showErrorMessage(
      `Acktos Studio: el self-update a ${source} falló — instalada sigue ${installed}.`,
      "Ver log",
    );
    if (pick === "Ver log") {
      out.show();
    }
    return;
  }
  const pick = await vscode.window.showInformationMessage(
    `Acktos Studio actualizado a ${source} — recargá la ventana para aplicarlo.`,
    "Recargar ahora",
  );
  if (pick === "Recargar ahora") {
    void vscode.commands.executeCommand("workbench.action.reloadWindow");
  }
}
