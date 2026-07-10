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
        `Acktos Studio: ${source} → ${target} no es conectable — ${(infoPayload.errors ?? []).join("; ") || `status ${info.status}`}`,
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
      void vscode.window.showErrorMessage(`Acktos Studio: conexión rechazada — ${(payload.errors ?? []).join("; ")}`);
      return false;
    }
    void vscode.window.showInformationMessage(`Acktos Studio: ${source} → ${target} conectado (${payload.file ?? ""}).`);
    return true;
  } catch (e) {
    void vscode.window.showErrorMessage(`Acktos Studio: conectar falló: ${e}`);
    return false;
  }
}

interface DeleteResponse {
  ok?: boolean;
  needs_confirmation?: boolean;
  dependents?: string[];
  disconnected?: string[];
  warnings?: string[];
  errors?: string[];
}

/** Desenlace del borrado: `cancelled` = no se tocó nada (no refrescar); `deleted` /
 * `failed` = hubo (o pudo haber) mutación — el caller refresca el grafo. */
export type DeleteOutcome = "deleted" | "cancelled" | "failed";

/**
 * Borrar un nodo (agente/tool) con confirmación — SIEMPRE confirma antes de la primera
 * request (el backend borra al toque cuando no hay dependientes: sin este modal, un click
 * derecho accidental hace `rmtree` de un directorio con código posiblemente sin commitear).
 * Si hay dependientes, el backend devuelve `needs_confirmation` + la lista → segundo modal
 * con el blast radius → reintento con cascade.
 */
export async function deleteNodeWithConfirmation(bridges: BridgeHub, nodeId: string, label: string): Promise<DeleteOutcome> {
  const first = await vscode.window.showWarningMessage(
    `¿Borrar ${label}? Se elimina su manifest/directorio del catálogo (si tiene código sin commitear, no se recupera).`,
    { modal: true },
    "Borrar",
  );
  if (first !== "Borrar") {
    return "cancelled";
  }
  const del = async (cascade: boolean): Promise<DeleteResponse> => {
    const res = await bridges
      .get("graphagents")
      .request({ method: "POST", path: "/api/delete-node", body: { node_id: nodeId, cascade } });
    return res.payload as DeleteResponse;
  };
  try {
    let payload = await del(false);
    if (payload.needs_confirmation) {
      const deps = payload.dependents ?? [];
      const choice = await vscode.window.showWarningMessage(
        `${label} lo usan ${deps.length} nodo(s): ${deps.join(", ")}. Se van a desconectar también.`,
        { modal: true },
        "Borrar en cascada",
      );
      if (choice !== "Borrar en cascada") {
        return "cancelled"; // preguntar no muta — nada que refrescar
      }
      payload = await del(true);
    }
    if (payload.ok) {
      const bits = [
        `Acktos Studio: ${label} borrado`,
        (payload.disconnected ?? []).length ? `· desconectado de ${(payload.disconnected ?? []).join(", ")}` : "",
        (payload.warnings ?? []).length ? `· ⚠ ${(payload.warnings ?? []).join("; ")}` : "",
      ].filter(Boolean).join(" ");
      void vscode.window.showInformationMessage(bits);
      return "deleted";
    }
    void vscode.window.showErrorMessage(`Acktos Studio: no se borró ${label} — ${(payload.errors ?? []).join("; ")}`);
    return "failed";
  } catch (e) {
    void vscode.window.showErrorMessage(`Acktos Studio: borrar ${label} falló: ${e}`);
    return "failed";
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
      void vscode.window.showErrorMessage(`Acktos Studio: desconexión rechazada — ${(payload.errors ?? []).join("; ")}`);
      return false;
    }
    void vscode.window.showInformationMessage(`Acktos Studio: ${source} → ${target} desconectado.`);
    return true;
  } catch (e) {
    void vscode.window.showErrorMessage(`Acktos Studio: desconectar falló: ${e}`);
    return false;
  }
}
