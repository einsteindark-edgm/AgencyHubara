// Forge Console — la flota de clientes, la migración paso a paso y el stream
// de runs del CLI. Piel pura: todo estado real viene de la extensión (fleet,
// inspected, busy) o del stdout del CLI (runs). Acá no hay lógica de clonación
// ni secretos: las llaves de los pasos automáticos se leen del entorno de
// VS Code y la UI solo dice si están o no.

import { useEffect, useMemo, useRef, useState } from "react";
import {
  DestState,
  FleetClient,
  ForgeOutbound,
  MigrationStepView,
} from "../../../src/forge/messages";
import { send } from "./vscodeApi";

interface LogEntry {
  kind: "cmd" | "line" | "end";
  text: string;
  tone?: "ok" | "warn" | "err";
}

const MAX_LOG = 5000;

function toneOf(line: string): LogEntry["tone"] {
  if (
    /^(forge|migrate|temporal_provision|supabase_provision|medusa_provision):/.test(line) ||
    line.includes("PyYAML") ||
    line.startsWith("Traceback") ||
    line.includes("FAIL")
  ) {
    return "err";
  }
  if (line.includes("⚠") || line.includes("TODO")) return "warn";
  if (line.startsWith("✓") || line.includes("passed")) return "ok";
  return undefined;
}

function suggestDest(client: FleetClient, repoRoot: string): string {
  // Si Studio corre en un worktree (<repo>/.claude/worktrees/<x>), el parent
  // "natural" quedaría DENTRO del repo madre — subir hasta afuera del
  // checkout productivo (el CLI igual lo rechaza; esto evita sugerirlo).
  const marker = "/.claude/worktrees/";
  const base = repoRoot.includes(marker) ? repoRoot.slice(0, repoRoot.indexOf(marker)) : repoRoot;
  const parent = base.replace(/\/[^/]+$/, "");
  return `${parent}/${client.repoName}`;
}

interface Inspection {
  dest: string;
  destState: DestState;
  steps: MigrationStepView[] | null;
  error: string | null;
}

function StepRow({
  step,
  busy,
  dest,
  destState,
  canForge,
  onRun,
  onDone,
}: {
  step: MigrationStepView;
  busy: boolean;
  dest: string;
  destState: DestState | null;
  canForge: boolean;
  onRun: () => void;
  onDone: () => void;
}) {
  const missingEnv = step.needs.filter((n) => !n.optional && !n.present);
  let blocked = "";
  if (busy) blocked = "hay otro proceso en curso";
  else if (!dest.trim()) blocked = "falta la carpeta destino del clon";
  else if (step.id === "clone" && destState?.isClone) blocked = "ya hay un clon en esa carpeta";
  else if (step.id === "clone" && !canForge) blocked = "quedan TODO-BRAND o archivos requeridos — o marca «clon de prueba»";
  else if (step.kind === "auto" && step.done) blocked = "este paso ya está hecho";
  else if (step.needsClone && !destState?.isClone) blocked = "primero hay que forjar el clon (paso S1)";
  else if (missingEnv.length > 0) blocked = `falta en el entorno: ${missingEnv.map((n) => n.name).join(", ")}`;

  const state = step.done ? "done" : step.kind === "guided" ? "guided" : "pending";
  const stateLabel = step.done ? "hecho" : step.kind === "guided" ? "guiado" : "pendiente";
  return (
    <li className={`step step-${state}`}>
      <div className="step-head">
        <span className="step-icon">{step.done ? "✓" : step.kind === "guided" ? "⧖" : "○"}</span>
        <span className="step-title">{step.title}</span>
        <span className={`step-state ${state}`}>{stateLabel}</span>
      </div>
      <div className="step-summary">{step.summary}</div>
      {(step.needs.length > 0 || step.tools.length > 0) && (
        <div className="chips">
          {step.needs.map((n) => (
            <span
              key={n.name}
              className={`chip ${n.present ? "ok" : n.optional ? "warn" : "err"}`}
              title={
                n.present
                  ? "está en el entorno con que abriste VS Code"
                  : "no está en el entorno con que abriste VS Code"
              }
            >
              {n.present ? "✓" : "✗"} {n.name}
              {n.optional ? " (opcional)" : ""}
            </span>
          ))}
          {step.tools.map((t) => (
            <span key={t.name} className={`chip ${t.present ? "ok" : "warn"}`}>
              {t.present ? `✓ ${t.name} instalado` : `${t.name} no instalado — solo mostrará comandos`}
            </span>
          ))}
        </div>
      )}
      {step.note && <div className="step-note">{step.note}</div>}
      <div className="actions">
        <button
          className={step.done ? "" : "primary"}
          disabled={blocked !== ""}
          title={blocked}
          onClick={onRun}
        >
          {step.action}
        </button>
        {step.kind === "guided" && !step.done && (
          <button
            disabled={busy}
            title="Cuando hayas corrido los comandos y terminaron bien"
            onClick={onDone}
          >
            Marcar como hecho
          </button>
        )}
      </div>
    </li>
  );
}

function Migration({
  client,
  dest,
  allowTodos,
  canForge,
  busy,
  inspection,
}: {
  client: FleetClient;
  dest: string;
  allowTodos: boolean;
  canForge: boolean;
  busy: boolean;
  inspection: Inspection | null;
}) {
  const steps = inspection?.steps ?? null;
  const doneCount = steps?.filter((s) => s.done).length ?? 0;
  return (
    <details className="migration" open>
      <summary>
        Migración paso a paso
        {steps ? ` — ${doneCount}/${steps.length} hechos` : ""}
      </summary>
      {inspection?.error ? (
        <div className="migration-error">No pude leer el estado de la migración: {inspection.error}</div>
      ) : !steps ? (
        <div className="migration-hint">Cargando el estado…</div>
      ) : (
        <>
          <ol className="steps">
            {steps.map((s) => (
              <StepRow
                key={s.id}
                step={s}
                busy={busy}
                dest={dest}
                destState={inspection?.destState ?? null}
                canForge={canForge}
                onRun={() =>
                  send({ type: "migrateRun", slug: client.slug, step: s.id, dest, allowTodos })
                }
                onDone={() => send({ type: "migrateDone", slug: client.slug, step: s.id, dest })}
              />
            ))}
          </ol>
          <div className="migration-hint">
            Las llaves (✓/✗) se leen del entorno con que abriste VS Code; nunca se escriben aquí.
            Si falta una, cierra VS Code y ábrelo desde una terminal donde la hayas exportado
            (por ejemplo <code>code .</code>). Los pasos guiados solo muestran comandos: córrelos tú
            en una terminal y después marca el paso como hecho.
          </div>
        </>
      )}
    </details>
  );
}

function ClientCard({
  client,
  repoRoot,
  busy,
  tick,
}: {
  client: FleetClient;
  repoRoot: string;
  busy: boolean;
  tick: number;
}) {
  const pending = client.todoFiles.length + client.missingRequired.length;
  const ready = pending === 0;
  const defaultDest = useMemo(() => suggestDest(client, repoRoot), [client, repoRoot]);
  const [dest, setDest] = useState(defaultDest);
  const [allowTodos, setAllowTodos] = useState(false);
  const [inspection, setInspection] = useState<Inspection | null>(null);
  const destRef = useRef(dest);
  destRef.current = dest;
  const touched = useRef(false);

  // el destino sugerido llega con el primer "fleet" (repoRoot vacío antes)
  useEffect(() => {
    if (!touched.current) setDest(defaultDest);
  }, [defaultDest]);

  useEffect(() => {
    const onMsg = (ev: MessageEvent<ForgeOutbound>) => {
      const m = ev.data;
      if (m.type === "destPicked" && m.slug === client.slug) {
        touched.current = true;
        setDest(m.dest);
      }
      if (m.type === "inspected" && m.slug === client.slug && m.dest === destRef.current) {
        setInspection({ dest: m.dest, destState: m.destState, steps: m.steps, error: m.error });
      }
    };
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [client.slug]);

  // estado del destino + migración: al cambiar el destino y después de cada run
  useEffect(() => {
    const t = setTimeout(() => send({ type: "inspect", slug: client.slug, dest }), 350);
    return () => clearTimeout(t);
  }, [client.slug, dest, tick]);

  const destState = inspection && inspection.dest === dest ? inspection.destState : null;
  const isClone = destState?.isClone ?? false;
  const canForge = ready || allowTodos;

  const initials = client.company
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  const noClone = "todavía no hay un clon forjado en esa carpeta";

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
        <span className="chip" title="applicationId de la App Operador (Android) del cliente">
          {client.androidAppId}
        </span>
        {ready ? (
          <span className="chip ok">✓ listo para forjar</span>
        ) : (
          <>
            {client.todoFiles.length > 0 && (
              <span
                className="chip warn link"
                title={client.todoFiles.join("\n")}
                onClick={() =>
                  send({ type: "openPath", fsPath: `${client.bundleDir}/${client.todoFiles[0]}` })
                }
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
        {client.clientWarnings.length > 0 && (
          <span
            className="chip warn link"
            title={client.clientWarnings.join("\n")}
            onClick={() => send({ type: "openPath", fsPath: client.clientYamlPath })}
          >
            ⚠ {client.clientWarnings.length} avisos en client.yaml
          </span>
        )}
        <span
          className="chip link"
          onClick={() => send({ type: "openPath", fsPath: client.clientYamlPath })}
        >
          client.yaml
        </span>
        {client.domainYamlPath && (
          <span
            className="chip link"
            title="El concepto de la tienda para el motor de decisiones"
            onClick={() => send({ type: "openPath", fsPath: client.domainYamlPath ?? "" })}
          >
            domain.yaml
          </span>
        )}
        <span
          className="chip link"
          onClick={() => send({ type: "openPath", fsPath: `${client.bundleDir}/workspace` })}
        >
          personalidad ({client.workspaceFileCount})
        </span>
        {destState?.nextStepsPath && (
          <span
            className="chip ok link"
            title="La guía de lo que falta, dentro del clon"
            onClick={() => send({ type: "openPath", fsPath: destState.nextStepsPath ?? "" })}
          >
            📋 NEXT_STEPS.md
          </span>
        )}
      </div>
      <div className="dest-row">
        <input
          type="text"
          value={dest}
          onChange={(e) => {
            touched.current = true;
            setDest(e.target.value);
          }}
          title="Carpeta destino del clon"
        />
        <button onClick={() => send({ type: "pickDest", slug: client.slug })}>…</button>
      </div>
      <div className="dest-state">
        {!destState
          ? " "
          : isClone
            ? "✓ hay un clon forjado en esa carpeta"
            : destState.exists
              ? "la carpeta existe pero no tiene un clon"
              : "la carpeta todavía no existe (Forjar la crea)"}
      </div>
      <div className="actions">
        <button disabled={busy} onClick={() => send({ type: "plan", slug: client.slug })}>
          Plan
        </button>
        <button
          className="primary"
          disabled={busy || !canForge || isClone}
          title={
            isClone
              ? "ya hay un clon en esa carpeta"
              : canForge
                ? ""
                : "quedan TODO-BRAND o requeridos — o marca «clon de prueba»"
          }
          onClick={() => send({ type: "apply", slug: client.slug, dest, allowTodos })}
        >
          ⚒ Forjar
        </button>
        <button
          disabled={busy || !isClone}
          title={isClone ? "" : noClone}
          onClick={() => send({ type: "verify", slug: client.slug, dest })}
        >
          Verificar
        </button>
        <button
          disabled={busy || !isClone}
          title={isClone ? "Muestra los comandos para crear el repo en GitHub" : noClone}
          onClick={() => send({ type: "publish", slug: client.slug, dest })}
        >
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
      <Migration
        client={client}
        dest={dest}
        allowTodos={allowTodos}
        canForge={canForge}
        busy={busy}
        inspection={inspection && inspection.dest === dest ? inspection : null}
      />
    </div>
  );
}

function NewClientCard({ busy }: { busy: boolean }) {
  const [slug, setSlug] = useState("");
  const valid = /^[a-z][a-z0-9_]*$/.test(slug);
  const submit = () => {
    if (!valid || busy) return;
    send({ type: "initClient", slug });
    setSlug("");
  };
  return (
    <div className="card new">
      <div className="card-head">
        <div className="avatar">＋</div>
        <div className="card-title">
          <div className="company">Nuevo cliente</div>
          <div className="repo">forge init — siembra client.yaml, domain.yaml y personalidad</div>
        </div>
      </div>
      <div className="hint">
        El bundle nace del motor con la marca sustituida: client.yaml (datos del cliente),
        domain.yaml (el concepto de la tienda) y la voz de cada agente (sales, remarketing,
        mba_sales…).
        Donde hay que redactar queda la marca TODO-BRAND.
      </div>
      <div className="dest-row">
        <input
          type="text"
          placeholder="nombre corto, ej. mi_tienda"
          value={slug}
          onChange={(e) => setSlug(e.target.value.trim())}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button className="primary" disabled={!valid || busy} onClick={submit}>
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
  const [busy, setBusy] = useState(false);
  const [busyCmd, setBusyCmd] = useState("");
  const [tick, setTick] = useState(0);
  const [python, setPython] = useState<{ ok: boolean; message: string } | null>(null);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const append = (e: LogEntry) => setLog((l) => (l.length >= MAX_LOG ? [...l.slice(-MAX_LOG + 1), e] : [...l, e]));
    const onMsg = (ev: MessageEvent<ForgeOutbound>) => {
      const msg = ev.data;
      switch (msg.type) {
        case "fleet":
          setClients(msg.clients);
          setRepoRoot(msg.repoRoot);
          setTick((t) => t + 1);
          break;
        case "busy":
          // UNA fuente de verdad para habilitar botones: el mutex de la extensión
          setBusy(msg.busy);
          setBusyCmd(msg.cmd);
          break;
        case "python":
          setPython({ ok: msg.ok, message: msg.message });
          break;
        case "runStart":
          append({ kind: "cmd", text: `$ ${msg.cmd}` });
          break;
        case "runLine":
          append({ kind: "line", text: msg.line, tone: msg.tone ?? toneOf(msg.line) });
          break;
        case "runEnd":
          append({
            kind: "end",
            text: msg.code === 0 ? "— ok —" : `— salió con código ${msg.code} —`,
            tone: msg.code === 0 ? "ok" : "err",
          });
          setTick((t) => t + 1);
          break;
        case "notice":
          append({ kind: "line", text: msg.text, tone: msg.tone });
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
        <span className="sub">un clon independiente por cliente: repo, infraestructura y tienda propios</span>
        <span className="spacer" />
        <button onClick={() => send({ type: "refresh" })}>↻ Refrescar</button>
      </header>
      {python && !python.ok && (
        <div className="banner err">
          {python.message}
          <button onClick={() => send({ type: "openSettings" })}>Abrir ajuste</button>
        </div>
      )}
      <div className="forge-body">
        <div className="fleet">
          {clients.map((c) => (
            <ClientCard key={c.slug} client={c} repoRoot={repoRoot} busy={busy} tick={tick} />
          ))}
          <NewClientCard busy={busy} />
        </div>
        <div className="console">
          <div className="console-head">
            <span className="console-title">{busy ? `⚒ en curso: ${busyCmd}` : "salida de los comandos"}</span>
            <span className="spacer" style={{ flex: 1 }} />
            <button onClick={() => setLog([])}>limpiar</button>
          </div>
          <div className="console-log" ref={logRef}>
            {log.length === 0 ? (
              <div className="empty">
                Lo que imprime cada comando (init · plan · forjar · verificar · publicar · pasos de
                la migración)
                <br />
                aparece aquí en vivo. La UI es piel; el CLI es músculo.
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
                  {e.text || " "}
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
