import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";

/**
 * Connect/disconnect con confirmación nativa (§F5, edit mode) — la MISMA
 * secuencia sin importar quién la dispara (drag-connect en el canvas desde
 * `GraphPanel`, o el picker "+ Conectar desde…" del CatalogTree). El gate +
 * rollback byte-idéntico vive en `sdk/manifest_edit.py`; acá solo se
 * orquesta la UX — cero lógica de validación duplicada.
 *
 * Los webviews de VS Code NO soportan `window.confirm`/`alert` de forma
 * confiable — la confirmación SIEMPRE es un diálogo nativo del lado
 * extensión, nunca del webview.
 */

interface ValidateResponse {
  ok?: boolean;
  errors?: string[];
  suggested?: Record<string, unknown>;
}
interface MutateResponse {
  ok?: boolean;
  errors?: string[];
  file?: string;
}

export async function connectWithConfirmation(bridges: BridgeHub, source: string, target: string): Promise<boolean> {
  try {
    const info = await bridges.get("graphagents").request({ method: "POST", path: "/api/validate-connection", body: { source, target } });
    const infoPayload = info.payload as ValidateResponse;
    if (info.status !== 200 || !infoPayload.ok) {
      void vscode.window.showErrorMessage(
        `Hubara Studio: ${source} → ${target} no es conectable — ${(infoPayload.errors ?? []).join("; ") || `status ${info.status}`}`,
      );
      return false;
    }
    const suggested = infoPayload.suggested ?? {};
    const choice = await vscode.window.showInformationMessage(
      `¿Conectar ${source} → ${target}?\nbinding sugerido: ${JSON.stringify(suggested)}`,
      { modal: true },
      "Conectar",
    );
    if (choice !== "Conectar") {
      return false;
    }
    const res = await bridges
      .get("graphagents")
      .request({ method: "POST", path: "/api/connect", body: { source, target, binding: suggested } });
    const payload = res.payload as MutateResponse;
    if (!payload.ok) {
      void vscode.window.showErrorMessage(`Hubara Studio: conexión rechazada — ${(payload.errors ?? []).join("; ")}`);
      return false;
    }
    void vscode.window.showInformationMessage(`Hubara Studio: ${source} → ${target} conectado (${payload.file ?? ""}).`);
    return true;
  } catch (e) {
    void vscode.window.showErrorMessage(`Hubara Studio: conectar falló: ${e}`);
    return false;
  }
}

export async function disconnectWithConfirmation(bridges: BridgeHub, source: string, target: string, kind: string): Promise<boolean> {
  if (kind !== "uses" && kind !== "agent") {
    return false; // consumes/seam no son editables (G-PORT)
  }
  const choice = await vscode.window.showWarningMessage(`¿Desconectar ${source} → ${target}? Edita el manifest.`, { modal: true }, "Desconectar");
  if (choice !== "Desconectar") {
    return false;
  }
  try {
    const res = await bridges.get("graphagents").request({ method: "POST", path: "/api/disconnect", body: { source, target, kind } });
    const payload = res.payload as MutateResponse;
    if (!payload.ok) {
      void vscode.window.showErrorMessage(`Hubara Studio: desconexión rechazada — ${(payload.errors ?? []).join("; ")}`);
      return false;
    }
    void vscode.window.showInformationMessage(`Hubara Studio: ${source} → ${target} desconectado.`);
    return true;
  } catch (e) {
    void vscode.window.showErrorMessage(`Hubara Studio: desconectar falló: ${e}`);
    return false;
  }
}
