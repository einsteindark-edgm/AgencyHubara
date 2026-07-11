// Forge Console — la flota de clientes y el stream de runs del CLI forge.
// Piel pura: todo estado real viene de la extensión (fleet) o del stdout del
// CLI (runs). Acá no hay lógica de clonación ni secretos.

import { useEffect, useMemo, useRef, useState } from "react";
import { FleetClient, ForgeOutbound } from "../../../src/forge/messages";
import { send } from "./vscodeApi";

interface LogEntry {
  kind: "cmd" | "line" | "end";
  text: string;
  tone?: "ok" | "warn" | "err";
}

function toneOf(line: string): LogEntry["tone"] {
  if (line.startsWith("forge:") || line.includes("FAIL")) return "err";
  if (line.includes("⚠") || line.includes("TODO")) return "warn";
  if (line.startsWith("✓") || line.includes("passed")) return "ok";
  return undefined;
}

function ClientCard({
  client,
  repoRoot,
  busy,
}: {
  client: FleetClient;
  repoRoot: string;
  busy: boolean;
}) {
  const pending = client.todoFiles.length + client.missingRequired.length;
  const ready = pending === 0;
  const defaultDest = useMemo(() => {
    const repoName = client.repo.split("/")[1] ?? `Agency${client.company}`;
    const parent = repoRoot.replace(/\/[^/]+$/, "");
    return `${parent}/${repoName}`;
  }, [client, repoRoot]);
  const [dest, setDest] = useState(defaultDest);
  const [allowTodos, setAllowTodos] = useState(false);

  useEffect(() => {
    const onMsg = (ev: MessageEvent<ForgeOutbound>) => {
      if (ev.data.type === "destPicked" && ev.data.slug === client.slug) {
        setDest(ev.data.dest);
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [client.slug]);

  const initials = client.company
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className={`card${ready ? " ready" : ""}`}>
      <div className="card-head">
        <div className="avatar">{initials}</div>
        <div className="card-title">
          <div className="company">{client.company}</div>
          <div className="repo">{client.repo || "repo sin definir"}</div>
        </div>
      </div>
      <div className="chips">
        <span className="chip">{client.region}</span>
        <span className="chip">{client.resourcePrefix}-*</span>
        <span className="chip">{`${client.ssmPrefix}/${client.slug}`}</span>
        {ready ? (
          <span className="chip ok">✓ listo para forjar</span>
        ) : (
          <>
            {client.todoFiles.length > 0 && (
              <span
                className="chip warn link"
                title={client.todoFiles.join("\n")}
                onClick={() => send({ type: "openPath", fsPath: `${client.bundleDir}/${client.todoFiles[0]}` })}
              >
                ✎ {client.todoFiles.length} TODO-BRAND
              </span>
            )}
            {client.missingRequired.length > 0 && (
              <span className="chip err" title={client.missingRequired.join("\n")}>
                ✗ {client.missingRequired.length} requeridos faltan
              </span>
            )}
          </>
        )}
        <span
          className="chip link"
          onClick={() => send({ type: "openPath", fsPath: client.clientYamlPath })}
        >
          client.yaml
        </span>
        <span
          className="chip link"
          onClick={() => send({ type: "openPath", fsPath: `${client.bundleDir}/workspace` })}
        >
          personalidad ({client.workspaceFileCount})
        </span>
      </div>
      <div className="dest-row">
        <input
          type="text"
          value={dest}
          onChange={(e) => setDest(e.target.value)}
          title="Carpeta destino del clon"
        />
        <button onClick={() => send({ type: "pickDest", slug: client.slug })}>…</button>
      </div>
      <div className="actions">
        <button disabled={busy} onClick={() => send({ type: "plan", slug: client.slug })}>
          Plan
        </button>
        <button
          className="primary"
          disabled={busy || (!ready && !allowTodos)}
          title={ready || allowTodos ? "" : "quedan TODO-BRAND / requeridos — o marcá clon de prueba"}
          onClick={() => send({ type: "apply", slug: client.slug, dest, allowTodos })}
        >
          ⚒ Forjar
        </button>
        <button disabled={busy} onClick={() => send({ type: "verify", slug: client.slug, dest })}>
          Verificar
        </button>
        <button disabled={busy} onClick={() => send({ type: "publish", slug: client.slug, dest })}>
          Publicar…
        </button>
        {!ready && (
          <label className="allow-todos">
            <input
              type="checkbox"
              checked={allowTodos}
              onChange={(e) => setAllowTodos(e.target.checked)}
            />
            clon de prueba (--allow-todos)
          </label>
        )}
      </div>
    </div>
  );
}

function NewClientCard() {
  const [slug, setSlug] = useState("");
  const valid = /^[a-z][a-z0-9_]*$/.test(slug);
  return (
    <div className="card new">
      <div className="card-head">
        <div className="avatar">＋</div>
        <div className="card-title">
          <div className="company">Nuevo cliente</div>
          <div className="repo">forge init — siembra client.yaml + personalidad</div>
        </div>
      </div>
      <div className="hint">
        El bundle nace del workspace de Hubara con la marca sustituida y marcas
        TODO-BRAND donde hay que redactar (producto, tono, guiones).
      </div>
      <div className="dest-row">
        <input
          type="text"
          placeholder="slug (ej. vincenzo)"
          value={slug}
          onChange={(e) => setSlug(e.target.value.trim())}
          onKeyDown={(e) => {
            if (e.key === "Enter" && valid) {
              send({ type: "initClient", slug });
              setSlug("");
            }
          }}
        />
        <button
          className="primary"
          disabled={!valid}
          onClick={() => {
            send({ type: "initClient", slug });
            setSlug("");
          }}
        >
          Sembrar
        </button>
      </div>
    </div>
  );
}

export function ForgeApp() {
  const [clients, setClients] = useState<FleetClient[]>([]);
  const [repoRoot, setRepoRoot] = useState("");
  const [log, setLog] = useState<LogEntry[]>([]);
  const [running, setRunning] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onMsg = (ev: MessageEvent<ForgeOutbound>) => {
      const msg = ev.data;
      switch (msg.type) {
        case "fleet":
          setClients(msg.clients);
          setRepoRoot(msg.repoRoot);
          break;
        case "runStart":
          setRunning(true);
          setLog((l) => [...l, { kind: "cmd", text: `$ ${msg.cmd}` }]);
          break;
        case "runLine":
          setLog((l) => [...l, { kind: "line", text: msg.line, tone: toneOf(msg.line) }]);
          break;
        case "runEnd":
          setRunning(false);
          setLog((l) => [
            ...l,
            {
              kind: "end",
              text: msg.code === 0 ? "— ok —" : `— salió con código ${msg.code} —`,
              tone: msg.code === 0 ? "ok" : "err",
            },
          ]);
          break;
      }
    };
    window.addEventListener("message", onMsg);
    send({ type: "ready" });
    return () => window.removeEventListener("message", onMsg);
  }, []);

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [log]);

  return (
    <div className="forge-app">
      <header className="forge-header">
        <span className="flame">⚒</span>
        <h1>Forge Console</h1>
        <span className="sub">silo + forge — un clon independiente por cliente</span>
        <span className="spacer" />
        <button onClick={() => send({ type: "refresh" })}>↻ Refrescar</button>
      </header>
      <div className="forge-body">
        <div className="fleet">
          {clients.map((c) => (
            <ClientCard key={c.slug} client={c} repoRoot={repoRoot} busy={running} />
          ))}
          <NewClientCard />
        </div>
        <div className="console">
          <div className="console-head">
            {running ? "⚒ forjando…" : "runs del CLI"}
            <span className="spacer" style={{ flex: 1 }} />
            <button onClick={() => setLog([])}>limpiar</button>
          </div>
          <div className="console-log" ref={logRef}>
            {log.length === 0 ? (
              <div className="empty">
                Los runs de forge (init · plan · apply · verify · publish)
                <br />
                se streamean acá. La UI es piel; el CLI es músculo.
              </div>
            ) : (
              log.map((e, i) => (
                <div
                  key={i}
                  className={
                    e.kind === "cmd"
                      ? "log-cmd"
                      : e.kind === "end"
                        ? `log-end${e.tone ? ` log-${e.tone}` : ""}`
                        : e.tone
                          ? `log-${e.tone}`
                          : ""
                  }
                >
                  {e.text}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
