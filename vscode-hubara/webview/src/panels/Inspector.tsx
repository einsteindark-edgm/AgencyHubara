import React from "react";
import { GraphNode, PROVIDER_LABEL, Provider } from "../../../src/bridge/endpoints";
import { InspectFile } from "../../../src/graph/messages";

export interface SelectedNode {
  system: Provider;
  raw: GraphNode;
}

export interface InspectorProps {
  node: SelectedNode | null;
  files: InspectFile[] | null;
  filesLoading: boolean;
  filesError: string | null;
  onOpenFile: (path: string) => void;
  onFocus: () => void;
}

/** Panel derecho: description, contract, tags, certificación — y los files
 * (GraphAgents) clickeables para abrir en el editor (`vscode.open`). */
export function Inspector({ node, files, filesLoading, filesError, onOpenFile, onFocus }: InspectorProps): React.ReactElement {
  if (!node) {
    return (
      <div className="inspector inspector-empty">
        <p className="meta">Seleccioná un nodo para ver sus detalles.</p>
      </div>
    );
  }
  const n = node.raw;
  const description = typeof n.description === "string" ? n.description : undefined;
  const certification = typeof n.certification === "string" ? n.certification : undefined;
  const archetype = typeof n.archetype === "string" ? n.archetype : undefined;
  const sideEffect = typeof n.side_effect === "string" ? n.side_effect : undefined;
  const version = typeof n.version === "string" ? n.version : undefined;
  const tags = Array.isArray(n.tags) ? (n.tags as unknown[]).filter((t) => typeof t === "string") : [];
  // inputs/outputs viven en el nodo (GraphAgents) o dentro de `contract`
  // (System Map) — un solo helper para que los dos gemelos no drifteen.
  const contract = isRecord(n.contract) ? n.contract : undefined;
  const contractField = (key: "inputs" | "outputs"): Record<string, unknown> | undefined => {
    if (isRecord(n[key])) {
      return n[key] as Record<string, unknown>;
    }
    if (isRecord(contract?.[key])) {
      return contract[key] as Record<string, unknown>;
    }
    return undefined;
  };
  const inputs = contractField("inputs");
  const outputs = contractField("outputs");

  return (
    <div className="inspector">
      <div className="inspector-header">
        <span className="inspector-system">{PROVIDER_LABEL[node.system]}</span>
        <button type="button" className="focus-btn" title="Enfocar este nodo" onClick={onFocus}>
          ⌂ enfocar
        </button>
      </div>
      <h2 className="inspector-title">{(n.label as string | undefined) ?? n.id}</h2>
      <div className="inspector-badges">
        <span className="badge">{n.kind as string}</span>
        {archetype && <span className="badge">{archetype}</span>}
        {sideEffect && <span className="badge">{sideEffect}</span>}
        {version && <span className="badge badge-muted">v{version}</span>}
        {certification && <span className={`badge cert-${certification.toLowerCase()}`}>{certification}</span>}
      </div>
      {description && <p className="inspector-description">{description}</p>}
      {tags.length > 0 && (
        <div className="inspector-tags">
          {tags.map((t) => (
            <span key={String(t)} className="tag">
              {String(t)}
            </span>
          ))}
        </div>
      )}
      {inputs && <ContractSection title="inputs" fields={inputs} />}
      {outputs && <ContractSection title="outputs" fields={outputs} />}
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
