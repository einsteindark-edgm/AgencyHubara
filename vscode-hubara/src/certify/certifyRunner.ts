import * as vscode from "vscode";
import { BridgeHub } from "../bridge/pythonBridge";
import { repoRoot, resolvePath } from "../config";
import { CertStatus, CertSuiteInfo } from "../graph/messages";
import { makeSuiteResolver } from "../testing/resolve";
import { runSuite } from "../testing/runner";
import { SUITES } from "../testing/suites";
import { publishWithFallback } from "./publisher";

/**
 * El sink al que el orquestador empuja la corrida — lo implementa el panel
 * "Ejecución" (F10), que la pinta como consola en vivo. Separado del provider
 * concreto para que `runCertifyAndPublish` no dependa de la webview.
 */
export interface CertSink {
  certStart(suites: CertSuiteInfo[]): void;
  certLog(suiteId: string, chunk: string): void;
  certSuite(suiteId: string, status: CertStatus, detail?: string): void;
  certPhase(phase: string): void;
  certDone(ok: boolean, branch: string | undefined, prUrl: string | undefined, errors: string[]): void;
}

/** Tipos de suite EXCLUIDOS del gate del PR, con el motivo que ve el usuario.
 * Exclusión (no inclusión) a propósito: una suite NUEVA de GraphAgents entra al gate
 * por defecto — el modo de fallo seguro es correr de más, nunca publicar sin correrla
 * (la "etiqueta verde sin check" que este gate existe para prevenir, L-19). */
const GATE_EXCLUDED: Record<string, string> = {
  integration: "requiere runtime durable (:6767) — no bloquea el PR",
};

/**
 * "Guardar & certificar": corre la suite COMPLETA de GraphAgents en vivo
 * (streameada al panel Ejecución) y, SOLO si todo pasa, bendice el wiring
 * (`/api/save`) y lo despliega (`/api/publish` → rama + commit + push + PR).
 *
 * La suite es el gate real: una sola suite roja aborta y NO genera PR. Todo el
 * despliegue vive en git (rama/PR, nunca main) → 100% reversible.
 */
export async function runCertifyAndPublish(
  bridges: BridgeHub,
  sink: CertSink,
  token: vscode.CancellationToken,
): Promise<void> {
  const resolve = makeSuiteResolver(repoRoot(), resolvePath);
  const ga = SUITES.filter((s) => s.lado === "graphagents");
  const gate = ga.filter((s) => !(s.tipo in GATE_EXCLUDED));
  const skipped = ga.filter((s) => s.tipo in GATE_EXCLUDED);

  // certStart YA lleva el estado skip con su motivo — una sola señal (el provider y la
  // webview acumulan el mismo estado; señalizarlo dos veces era una costura de drift).
  const suiteInfos: CertSuiteInfo[] = [
    ...gate.map((s) => ({ id: s.id, label: s.label, status: "pending" as CertStatus })),
    ...skipped.map((s) => ({ id: s.id, label: s.label, status: "skip" as CertStatus, detail: GATE_EXCLUDED[s.tipo] })),
  ];
  sink.certStart(suiteInfos);

  let allPass = true;
  for (const suite of gate) {
    if (token.isCancellationRequested) {
      sink.certDone(false, undefined, undefined, ["cancelado"]);
      return;
    }
    sink.certSuite(suite.id, "running");
    const resolved = resolve(suite);
    const result = await runSuite(suite, resolved, token, (chunk) => sink.certLog(suite.id, chunk));
    const pass = result.exitCode === 0;
    allPass = allPass && pass;
    sink.certSuite(suite.id, pass ? "pass" : "fail", suiteDetail(result.exitCode, result.cases, pass));
    // NO fail-fast: se corren todas para que la consola muestre el cuadro completo.
  }

  if (!allPass) {
    sink.certDone(false, undefined, undefined, [
      "la certificación no pasó — no se generó PR. Revisá arriba qué suite quedó en rojo.",
    ]);
    return;
  }

  // Todo verde → bendecir el wiring y desplegarlo.
  try {
    sink.certPhase("Certificación en verde — bendiciendo el wiring (guardar producción)…");
    const save = await bridges.get("graphagents").request({ method: "POST", path: "/api/save", body: {} });
    const savePayload = save.payload as { ok?: boolean; errors?: string[] };
    if (!savePayload.ok) {
      sink.certDone(false, undefined, undefined, [
        `el save rechazó el snapshot: ${(savePayload.errors ?? []).join("; ") || `status ${save.status}`}`,
      ]);
      return;
    }

    // Publicar con las APIs NATIVAS de VS Code (git + GitHub, sin `gh`), con fallback
    // headless — el flujo completo vive en publisher.ts (compartido con el comando
    // "Publish Production", para que ambos disparadores publiquen idéntico).
    sink.certPhase("Publicando (rama + commit + push + PR)…");
    const outcome = await publishWithFallback(bridges, (line) => sink.certPhase(line));
    sink.certDone(outcome.ok, outcome.branch, outcome.prUrl, outcome.errors);
  } catch (e) {
    sink.certDone(false, undefined, undefined, [`el despliegue falló: ${e instanceof Error ? e.message : String(e)}`]);
  }
}

function suiteDetail(exitCode: number | null, cases: { failureMessage?: string; skipped: boolean }[] | null, pass: boolean): string {
  if (cases && cases.length > 0) {
    const failed = cases.filter((c) => c.failureMessage).length;
    const skipped = cases.filter((c) => c.skipped).length;
    const passed = cases.length - failed - skipped;
    return pass ? `${passed} pasaron` : `${failed}/${cases.length} fallaron`;
  }
  return pass ? "ok" : `exit ${exitCode ?? "?"}`;
}
