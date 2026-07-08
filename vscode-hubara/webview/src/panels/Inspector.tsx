import React, { useEffect, useMemo, useState } from "react";
import { GraphNode, PROVIDER_LABEL, Provider } from "../../../src/bridge/endpoints";
import { InspectFile, ToolCall, TraceInfo } from "../../../src/graph/messages";

export interface SelectedNode {
  system: Provider;
  raw: GraphNode;
}

/** Estado de una lectura lazy del acc de un nodo (`/api/node-state`). */
export interface IoEntry {
  loading: boolean;
  value?: unknown;
  error?: string;
}

/** El I/O por-tool reconstruido de una ejecución (`/api/flow-trace`). */
export interface FlowTraceState {
  executionId: string;
  nodeTraces: Record<string, ToolCall[]>;
  reconstructed: boolean;
  reason?: string;
}

export interface InspectorProps {
  node: SelectedNode | null;
  files: InspectFile[] | null;
  filesLoading: boolean;
  filesError: string | null;
  /** trace activo (o el último terminal) — habilita las pestañas input/output. */
  trace: TraceInfo | null;
  flowTrace: FlowTraceState | null;
  ioStates: Record<string, IoEntry>;
  onRequestNodeState: (key: string, executionId: string, taskId: string) => void;
  onOpenFile: (path: string) => void;
  onFocus: () => void;
}

type Tab = "resumen" | "input" | "output";

function fmt(v: unknown): string {
  if (v == null) {
    return "";
  }
  return typeof v === "string" ? v : JSON.stringify(v, null, 2);
}

/** Panel derecho: description, contract, tags, certificación, files — y con un
 * trace activo, el ENTRÓ/SALIÓ real de cada nodo (el acc de Conductor) + la
 * sub-ejecución por-tool reconstruida (replay determinista, G-DET). */
export function Inspector({
  node,
  files,
  filesLoading,
  filesError,
  trace,
  flowTrace,
  ioStates,
  onRequestNodeState,
  onOpenFile,
  onFocus,
}: InspectorProps): React.ReactElement {
  const raw = node?.raw as (GraphNode & { rawId?: string }) | undefined;
  const rawId = raw ? ((raw.rawId as string | undefined) ?? raw.id) : "";

  // ¿Qué rol juega este nodo en la ejecución mirada?
  const steps = trace?.steps ?? [];
  const stepIndex = steps.findIndex((s) => s.agent && `agent:${s.agent}` === rawId);
  const isStep = stepIndex >= 0;
  const step = isStep ? steps[stepIndex] : null;
  const rt = step?.runtime;
  const isTool = raw?.kind === "tool";
  const isSup = !isStep && !isTool && !!trace?.agent && `agent:${trace.agent}` === rawId;
  const traceRelevant = !!trace && node?.system === "graphagents" && (isStep || isSup || isTool);

  const [tab, setTab] = useState<Tab>("resumen");
  const [openCalls, setOpenCalls] = useState<Record<number, boolean>>({});
  useEffect(() => {
    setTab("resumen");
    setOpenCalls({});
  }, [rawId, trace?.executionId]);

  // Qué acc corresponde a cada pestaña (sdk/trace.py: el input de un nodo es
  // el acc del anterior; el output, su propio acc; el sup entra con el seed y
  // sale con el acc del último nodo terminado — el RESULTADO del workflow).
  const lastDoneTask = useMemo(
    () => [...steps].reverse().find((s) => s.runtime?.task_id)?.runtime?.task_id,
    [steps],
  );
  type IoSpec = { kind: "seed" } | { kind: "task"; taskId: string | undefined } | null;
  const ioSpec = (which: "input" | "output"): IoSpec => {
    if (!trace || (!isStep && !isSup)) {
      return null;
    }
    if (which === "input") {
      if (isSup || stepIndex === 0) {
        return { kind: "seed" };
      }
      return { kind: "task", taskId: steps[stepIndex - 1]?.runtime?.task_id };
    }
    return { kind: "task", taskId: isSup ? lastDoneTask : rt?.task_id };
  };

  const spec = tab === "resumen" ? null : ioSpec(tab);
  const specKey = spec?.kind === "task" && spec.taskId && trace ? `${trace.executionId}:${spec.taskId}` : null;
  useEffect(() => {
    if (specKey && trace) {
      onRequestNodeState(specKey, trace.executionId, specKey.slice(trace.executionId.length + 1));
    }
  }, [specKey, trace, onRequestNodeState]);

  if (!node || !raw) {
    return (
      <div className="inspector inspector-empty">
        <p className="meta">Seleccioná un nodo para ver sus detalles.</p>
      </div>
    );
  }

  const description = typeof raw.description === "string" ? raw.description : undefined;
  const certification = typeof raw.certification === "string" ? raw.certification : undefined;
  const archetype = typeof raw.archetype === "string" ? raw.archetype : undefined;
  const sideEffect = typeof raw.side_effect === "string" ? raw.side_effect : undefined;
  const version = typeof raw.version === "string" ? raw.version : undefined;
  const tags = Array.isArray(raw.tags) ? (raw.tags as unknown[]).filter((t) => typeof t === "string") : [];
  // inputs/outputs viven en el nodo (GraphAgents) o dentro de `contract`
  // (System Map) — un solo helper para que los dos gemelos no drifteen.
  const contract = isRecord(raw.contract) ? raw.contract : undefined;
  const contractField = (key: "inputs" | "outputs"): Record<string, unknown> | undefined => {
    if (isRecord(raw[key])) {
      return raw[key] as Record<string, unknown>;
    }
    if (isRecord(contract?.[key])) {
      return contract[key] as Record<string, unknown>;
    }
    return undefined;
  };
  const declaredInputs = contractField("inputs");
  const declaredOutputs = contractField("outputs");

  const status = isSup ? trace!.workflowStatus.toLowerCase() : isStep ? rt?.status : undefined;
  const ledger: ToolCall[] | null =
    isStep && step && flowTrace && trace && flowTrace.executionId === trace.executionId
      ? (flowTrace.nodeTraces[step.agent] ?? null)
      : null;

  const ioBody = (which: "input" | "output"): React.ReactElement => {
    if (isTool) {
      return (
        <p className="meta">
          El I/O por-tool se reconstruye DENTRO de cada nodo que la compone (Conductor no lo persiste) — abrí el
          agente que la usa y mirá su sub-ejecución en la pestaña resumen.
        </p>
      );
    }
    const s = ioSpec(which);
    if (!s) {
      return <p className="meta">Este nodo no participa de la ejecución mirada.</p>;
    }
    if (s.kind === "seed") {
      return trace?.seed != null ? (
        <pre className="io-pre">{fmt(trace.seed)}</pre>
      ) : (
        <p className="meta">(el seed no viajó en este run — corré el caso de nuevo para verlo)</p>
      );
    }
    if (!s.taskId) {
      const msg =
        isStep && rt?.status === "failed"
          ? "(el nodo falló — sin acc)"
          : isStep && rt?.status === "pending"
            ? "(todavía no corrió)"
            : "(sin estado registrado)";
      return <p className="meta">{msg}</p>;
    }
    const entry = trace ? ioStates[`${trace.executionId}:${s.taskId}`] : undefined;
    if (!entry || entry.loading) {
      return <p className="meta">leyendo el estado del nodo…</p>;
    }
    if (entry.error) {
      return <p className="error">⚠ {entry.error}</p>;
    }
    if (entry.value == null) {
      return <p className="meta">(sin estado)</p>;
    }
    const narrative =
      which === "output" && isRecord(entry.value) && typeof entry.value.narrative === "string"
        ? (entry.value as { narrative: string; narrative_invented?: unknown[] })
        : null;
    return (
      <div>
        {narrative && (
          <div className="narrative-box">
            <div className="narrative-head">
              NARRATIVA · LLM
              {Array.isArray(narrative.narrative_invented) && narrative.narrative_invented.length > 0
                ? " · ⚠ cifras inventadas descartadas"
                : " · ✓ sin cifras inventadas"}
            </div>
            {narrative.narrative}
          </div>
        )}
        <pre className="io-pre">{fmt(entry.value)}</pre>
      </div>
    );
  };

  return (
    <div className="inspector">
      <div className="inspector-header">
        <span className="inspector-system">{PROVIDER_LABEL[node.system]}</span>
        <button type="button" className="focus-btn" title="Enfocar este nodo" onClick={onFocus}>
          ⌂ enfocar
        </button>
      </div>
      <h2 className="inspector-title">
        {(raw.label as string | undefined) ?? raw.id}
        {status && <span className={`run-badge run-${status}`}>{status}</span>}
      </h2>
      {traceRelevant && (isStep || isSup) && (
        <div className="meta trace-node-meta">
          {isSup ? `nodo principal del pod · ${trace!.strategy ?? "pod"}` : (step!.archetype ?? "nodo del flujo")}
          {isStep && rt?.ms != null && ` · ${rt.ms} ms`}
          {isStep && !!rt?.retries && ` · ↻${rt.retries}`}
        </div>
      )}

      {traceRelevant && (
        <div className="io-tabs">
          {(["resumen", "input", "output"] as Tab[]).map((t) => (
            <button key={t} type="button" className={`io-tab${tab === t ? " on" : ""}`} onClick={() => setTab(t)}>
              {t === "input" ? "entró" : t === "output" ? "salió" : t}
            </button>
          ))}
        </div>
      )}

      {tab === "input" && traceRelevant && (
        <div className="inspector-section">
          <h3>ENTRÓ · {isSup ? "el seed con que arrancó el pod" : stepIndex === 0 ? "el seed inicial del flujo" : "el acc del nodo anterior"}</h3>
          {ioBody("input")}
        </div>
      )}
      {tab === "output" && traceRelevant && (
        <div className="inspector-section">
          <h3>SALIÓ · {isSup ? "el estado FINAL del pod (la respuesta del workflow)" : "el acc después de este nodo"}</h3>
          {ioBody("output")}
        </div>
      )}

      {(tab === "resumen" || !traceRelevant) && (
        <>
          <div className="inspector-badges">
            <span className="badge">{raw.kind as string}</span>
            {archetype && <span className="badge">{archetype}</span>}
            {sideEffect && <span className="badge">{sideEffect}</span>}
            {version && <span className="badge badge-muted">v{version}</span>}
            {certification && <span className={`badge cert-${certification.toLowerCase()}`}>{certification}</span>}
          </div>
          {description && <p className="inspector-description">{description}</p>}
          {trace && node.system === "graphagents" && !traceRelevant && !isTool && (
            <p className="meta">— fuera de la ejecución mirada —</p>
          )}
          {tags.length > 0 && (
            <div className="inspector-tags">
              {tags.map((t) => (
                <span key={String(t)} className="tag">
                  {String(t)}
                </span>
              ))}
            </div>
          )}

          {isStep && ledger && ledger.length > 0 && (
            <div className="inspector-section">
              <h3>Sub-ejecución · I/O por-tool real ({ledger.length} llamada{ledger.length === 1 ? "" : "s"})</h3>
              <ul className="subtool-list">
                {ledger.map((call) => (
                  <li key={call.seq}>
                    <button
                      type="button"
                      className="subtool-row"
                      onClick={() => setOpenCalls((p) => ({ ...p, [call.seq]: !p[call.seq] }))}
                    >
                      <span className="subtool-seq">{call.seq + 1}</span>
                      <span className="subtool-name">{call.tool}</span>
                      <span className="subtool-io">{openCalls[call.seq] ? "▾ I/O" : "▸ I/O"}</span>
                    </button>
                    {openCalls[call.seq] && (
                      <div className="subtool-detail">
                        <div className="subtool-sec">entró</div>
                        <pre className="io-pre">{fmt(call.input)}</pre>
                        <div className="subtool-sec">salió</div>
                        <pre className="io-pre">{fmt(call.output)}</pre>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {isStep && !ledger && step?.tools && step.tools.length > 0 && (
            <div className="inspector-section">
              <h3>Tools que compone (declarado)</h3>
              <ul className="contract-list">
                {step.tools.map((t) => (
                  <li key={t}>
                    <code>{t}</code>
                  </li>
                ))}
              </ul>
              {flowTrace && trace && flowTrace.executionId === trace.executionId && !flowTrace.reconstructed && (
                <p className="meta">{flowTrace.reason ?? "el I/O real no se pudo reconstruir para este run"}</p>
              )}
            </div>
          )}
          {isSup && (
            <p className="meta">
              Pod de {steps.filter((s) => s.agent).length} nodos — tocá un nodo del canvas para ver su entró/salió; la
              pestaña «salió» de ESTE nodo es la respuesta final del workflow.
            </p>
          )}

          {declaredInputs && <ContractSection title="inputs" fields={declaredInputs} />}
          {declaredOutputs && <ContractSection title="outputs" fields={declaredOutputs} />}
          <div className="inspector-section">
            <h3>Files</h3>
            {node.system !== "graphagents" && <p className="meta">No disponible para System Map todavía.</p>}
            {node.system === "graphagents" && filesLoading && <p className="meta">Cargando…</p>}
            {node.system === "graphagents" && filesError && <p className="error">⚠ {filesError}</p>}
            {node.system === "graphagents" && files && files.length === 0 && !filesLoading && (
              <p className="meta">Sin archivos asociados.</p>
            )}
            {node.system === "graphagents" && files && files.length > 0 && (
              <ul className="file-list">
                {files.map((f) => (
                  <li key={f.path}>
                    <button type="button" className="file-link" onClick={() => onOpenFile(f.abspath)}>
                      {f.path}
                    </button>
                    <span className="file-role">{f.role}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ContractSection({ title, fields }: { title: string; fields: Record<string, unknown> }): React.ReactElement {
  const keys = Object.keys(fields);
  if (keys.length === 0) {
    return <></>;
  }
  return (
    <div className="inspector-section">
      <h3>{title}</h3>
      <ul className="contract-list">
        {keys.map((k) => (
          <li key={k}>
            <code>{k}</code>
          </li>
        ))}
      </ul>
    </div>
  );
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}
