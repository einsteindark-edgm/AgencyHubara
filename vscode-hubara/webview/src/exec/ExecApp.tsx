import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GraphNode } from "../../../src/bridge/endpoints";
import { StepEntry, toStepEntries } from "../../../src/graph/cascade";
import { ExecOutbound, InspectFile, SelectedNodePayload, TraceInfo } from "../../../src/graph/messages";
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
