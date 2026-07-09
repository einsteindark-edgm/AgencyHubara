import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GraphNode } from "../../../src/bridge/endpoints";
import { StepEntry, toStepEntries } from "../../../src/graph/cascade";
import { CertState, ExecOutbound, InspectFile, SelectedNodePayload, TraceInfo } from "../../../src/graph/messages";
import { FlowTraceState, Inspector, IoEntry, SelectedNode } from "../panels/Inspector";
import { send } from "./vscodeApi";

/**
 * El panel "Ejecución" (§F10): a la izquierda la CASCADA del run (agentes en
 * orden → tools en orden, estilo test navigator de Xcode); a la derecha el
 * detalle del nodo seleccionado — el MISMO Inspector del canvas (resumen +
 * entró/salió confrontado), reutilizado tal cual.
 */
export function ExecApp(): React.ReactElement {
  const [trace, setTrace] = useState<TraceInfo | null>(null);
  const [flowTrace, setFlowTrace] = useState<FlowTraceState | null>(null);
  const [selected, setSelected] = useState<SelectedNode | null>(null);
  const [ioStates, setIoStates] = useState<Record<string, IoEntry>>({});
  const requestedIo = useRef(new Set<string>());
  const [files, setFiles] = useState<InspectFile[] | null>(null);
  const [filesError, setFilesError] = useState<string | null>(null);
  const lastEid = useRef<string | null>(null);
  // Consola de certificación en vivo (F14). `dismissed` la oculta sin perderla.
  const [cert, setCert] = useState<CertState | null>(null);
  const [certDismissed, setCertDismissed] = useState(false);

  useEffect(() => {
    const onMessage = (ev: MessageEvent<ExecOutbound>) => {
      const msg = ev.data;
      switch (msg.type) {
        case "execTrace":
          if (msg.info && lastEid.current !== msg.info.executionId) {
            setIoStates({});
            requestedIo.current.clear();
            setFlowTrace(null);
          }
          lastEid.current = msg.info?.executionId ?? null;
          setTrace((prev) => (prev && msg.info && JSON.stringify(prev) === JSON.stringify(msg.info) ? prev : msg.info));
          return;
        case "execNodeTraces":
          setFlowTrace({
            executionId: msg.executionId,
            nodeTraces: msg.nodeTraces,
            reconstructed: msg.reconstructed,
            reason: msg.reason,
          });
          return;
        case "selectNode":
          setSelected({ system: msg.system, raw: msg.node as GraphNode });
          setFiles(null);
          setFilesError(null);
          if (msg.system === "graphagents") {
            send({ type: "inspectNode", system: msg.system, nodeId: msg.node.rawId });
          }
          return;
        case "nodeStateResult":
          setIoStates((prev) => ({ ...prev, [msg.key]: { loading: false, value: msg.acc } }));
          return;
        case "nodeStateError":
          setIoStates((prev) => ({ ...prev, [msg.key]: { loading: false, error: msg.message } }));
          return;
        case "inspectResult":
          setFiles(msg.files);
          return;
        case "inspectError":
          setFilesError(msg.message);
          setFiles([]);
          return;
        case "certStart":
          setCertDismissed(false);
          setCert({ suites: msg.suites, logs: {}, phase: null, done: null });
          return;
        case "certLog":
          setCert((prev) =>
            prev ? { ...prev, logs: { ...prev.logs, [msg.suiteId]: (prev.logs[msg.suiteId] ?? "") + msg.chunk } } : prev,
          );
          return;
        case "certSuite":
          setCert((prev) =>
            prev
              ? { ...prev, suites: prev.suites.map((s) => (s.id === msg.suiteId ? { ...s, status: msg.status, detail: msg.detail } : s)) }
              : prev,
          );
          return;
        case "certPhase":
          setCert((prev) => (prev ? { ...prev, phase: msg.phase } : prev));
          return;
        case "certDone":
          setCert((prev) => (prev ? { ...prev, done: { ok: msg.ok, branch: msg.branch, prUrl: msg.prUrl, errors: msg.errors } } : prev));
          return;
        case "certRestore":
          setCertDismissed(false);
          setCert(msg.cert);
          return;
      }
    };
    window.addEventListener("message", onMessage);
    send({ type: "ready" });
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const requestNodeState = useCallback((key: string, executionId: string, taskId: string) => {
    if (requestedIo.current.has(key)) {
      return;
    }
    requestedIo.current.add(key);
    setIoStates((prev) => ({ ...prev, [key]: { loading: true } }));
    send({ type: "nodeStateRequest", key, executionId, taskId });
  }, []);

  const entries = useMemo(
    () =>
      trace
        ? toStepEntries(trace.steps, flowTrace && flowTrace.executionId === trace.executionId ? flowTrace.nodeTraces : {})
        : [],
    [trace, flowTrace],
  );

  /** Cascada → selección local: un pseudo-nodo alcanza (el Inspector completa
   * files vía inspect y el I/O vía trace) — sin depender del canvas. */
  const pickAgent = useCallback((agent: string) => {
    const node: SelectedNodePayload = { id: `agent:${agent}`, rawId: `agent:${agent}`, kind: "agent", label: agent };
    setSelected({ system: "graphagents", raw: node as GraphNode });
    setFiles(null);
    setFilesError(null);
    send({ type: "inspectNode", system: "graphagents", nodeId: `agent:${agent}` });
  }, []);

  const pickTool = useCallback((tool: string) => {
    const node: SelectedNodePayload = { id: `tool:${tool}`, rawId: `tool:${tool}`, kind: "tool", label: tool };
    setSelected({ system: "graphagents", raw: node as GraphNode });
    setFiles(null);
    setFilesError(null);
    send({ type: "inspectNode", system: "graphagents", nodeId: `tool:${tool}` });
  }, []);

  const selectedRawId = selected ? ((selected.raw as { rawId?: string }).rawId ?? selected.raw.id) : null;

  // La certificación en vivo toma el panel: es el foco mientras corre "Guardar &
  // certificar" (el usuario quiere ver exactamente qué se ejecutó y el veredicto).
  if (cert && !certDismissed) {
    return <CertConsole cert={cert} onOpenPr={(url) => send({ type: "openExternal", url })} onClose={() => setCertDismissed(true)} />;
  }

  if (!trace && !selected) {
    return (
      <div className="exec-empty">
        <p className="meta">
          Sin ejecución activa — corré un case (⚡ local o ▶ durable) o tocá un nodo del canvas para ver su detalle acá.
        </p>
      </div>
    );
  }

  return (
    <div className="exec-root">
      {trace && (
        <div className="exec-cascade">
          <div className="exec-run-header">
            <span className={`run-badge run-${trace.workflowStatus.toLowerCase()}`}>{trace.workflowStatus}</span>
            <span className="exec-run-title">{trace.agent ?? trace.executionId.slice(0, 12)}</span>
            {trace.strategy && <span className="trace-meta">· {trace.strategy}</span>}
          </div>
          <ul className="exec-steps">
            {entries.map((e) => (
              <CascadeStep
                key={e.order}
                entry={e}
                selected={selectedRawId === `agent:${e.agent}`}
                selectedRawId={selectedRawId}
                onPickAgent={pickAgent}
                onPickTool={pickTool}
              />
            ))}
            {entries.length === 0 && <li className="meta">— sin steps —</li>}
          </ul>
        </div>
      )}
      <div className="exec-detail">
        <Inspector
          node={selected}
          files={files}
          filesLoading={selected?.system === "graphagents" && files === null && !filesError}
          filesError={filesError}
          trace={trace}
          flowTrace={flowTrace}
          ioStates={ioStates}
          onRequestNodeState={requestNodeState}
          onOpenFile={(path) => send({ type: "openFile", path })}
          onFocus={() => {
            if (selected && selectedRawId) {
              send({ type: "focusNode", system: selected.system, nodeId: selectedRawId });
            }
          }}
        />
      </div>
    </div>
  );
}

const CERT_ICON: Record<string, string> = {
  pending: "○",
  running: "◍",
  pass: "✓",
  fail: "✗",
  skip: "⊘",
};

/** La consola en vivo de "Guardar & certificar" (F14): una fila por suite con su
 * estado + log streameado colapsable, la fase post-suites, y el veredicto final
 * (rama + PR si verde). Es el foco del panel Ejecución mientras corre. */
function CertConsole({
  cert,
  onOpenPr,
  onClose,
}: {
  cert: CertState;
  onOpenPr: (url: string) => void;
  onClose: () => void;
}): React.ReactElement {
  const running = cert.suites.find((s) => s.status === "running");
  const [openId, setOpenId] = useState<string | null>(null);
  // Auto-abre el log de la suite en curso (o de la primera que falló).
  const failed = cert.suites.find((s) => s.status === "fail");
  const autoOpen = running?.id ?? failed?.id ?? null;
  const effectiveOpen = openId ?? autoOpen;
  const logRef = useRef<HTMLPreElement | null>(null);
  const activeLog = effectiveOpen ? (cert.logs[effectiveOpen] ?? "") : "";
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [activeLog, effectiveOpen]);

  const done = cert.done;
  const doneCount = cert.suites.filter((s) => s.status === "pass" || s.status === "fail" || s.status === "skip").length;

  return (
    <div className="cert-root">
      <div className="cert-header">
        <span className="cert-title">
          {done ? (done.ok ? "✓ Certificado y publicado" : "✗ Certificación con fallos") : `◍ Certificando… (${doneCount}/${cert.suites.length})`}
        </span>
        <button type="button" className="trace-stop" onClick={onClose}>
          {done ? "cerrar" : "ocultar"}
        </button>
      </div>

      <ul className="cert-suites">
        {cert.suites.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              className={`cert-suite-row cert-${s.status}${effectiveOpen === s.id ? " on" : ""}`}
              onClick={() => setOpenId(effectiveOpen === s.id ? "" : s.id)}
            >
              <span className={`cert-icon cert-icon-${s.status}`}>{CERT_ICON[s.status] ?? "•"}</span>
              <span className="cert-suite-label">{s.label}</span>
              {s.detail && <span className="cert-suite-detail">{s.detail}</span>}
            </button>
            {effectiveOpen === s.id && (
              <pre className="cert-log" ref={s.id === effectiveOpen ? logRef : undefined}>
                {cert.logs[s.id] ? cert.logs[s.id] : "— sin salida todavía —"}
              </pre>
            )}
          </li>
        ))}
      </ul>

      {cert.phase && !done && <div className="cert-phase">◍ {cert.phase}</div>}

      {done && (
        <div className={`cert-verdict ${done.ok ? "ok" : "bad"}`}>
          {done.ok ? (
            <>
              <span>Wiring bendecido y desplegado{done.branch ? ` en ${done.branch}` : ""}.</span>
              {done.prUrl && (
                <button type="button" className="cert-pr-btn" onClick={() => onOpenPr(done.prUrl!)}>
                  Abrir PR ↗
                </button>
              )}
            </>
          ) : (
            <ul className="cert-errors">
              {done.errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

function CascadeStep({
  entry,
  selected,
  selectedRawId,
  onPickAgent,
  onPickTool,
}: {
  entry: StepEntry;
  selected: boolean;
  selectedRawId: string | null;
  onPickAgent: (agent: string) => void;
  onPickTool: (tool: string) => void;
}): React.ReactElement {
  const [open, setOpen] = useState(true);
  return (
    <li>
      <div className={`exec-step-row${selected ? " on" : ""}`}>
        {entry.tools.length > 0 ? (
          <button type="button" className="exec-chevron" onClick={() => setOpen((o) => !o)}>
            {open ? "▾" : "▸"}
          </button>
        ) : (
          <span className="exec-chevron" />
        )}
        <button type="button" className="exec-step-label" onClick={() => onPickAgent(entry.agent)}>
          <span className={`exec-status status-dot-${entry.status}`} />
          <span className="exec-order">{entry.order}.</span> {entry.agent}
          <span className="exec-step-meta">
            {entry.ms != null && ` ${entry.ms}ms`}
            {!!entry.retries && ` ↻${entry.retries}`}
          </span>
        </button>
      </div>
      {open && entry.tools.length > 0 && (
        <ul className="exec-tools">
          {entry.tools.map((t) => (
            <li key={t.order}>
              <button
                type="button"
                className={`exec-step-label exec-tool${selectedRawId === `tool:${t.tool}` ? " on" : ""}`}
                onClick={() => onPickTool(t.tool)}
              >
                <span className="exec-order">{t.order}</span> {t.tool}
              </button>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
