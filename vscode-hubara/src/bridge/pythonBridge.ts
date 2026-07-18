import { ChildProcessWithoutNullStreams, spawn } from "node:child_process";
import * as os from "node:os";
import * as path from "node:path";
import * as readline from "node:readline";
import * as vscode from "vscode";
import { BridgeReady, BridgeRequest, BridgeResponse, Provider } from "./endpoints";

/** Indirección para que los consumidores (GraphPanel) sigan al puente VIGENTE
 * aunque la extensión los reconstruya (p. ej. al cambiar settings). */
export interface BridgeHub {
  get(provider: Provider): PythonBridge;
}

interface Pending {
  resolve: (r: BridgeResponse) => void;
  reject: (e: Error) => void;
  timer: NodeJS.Timeout;
}

export interface BridgeSpec {
  /** Etiqueta legible para logs/errores (p. ej. "GraphAgents"). */
  label: string;
  /** Ejecutable + args, ya resueltos (p. ej. ["uv","run","python","scripts/system_map_bridge.py"]). */
  command: string[];
  /** cwd absoluto donde vive el puente. */
  cwd: string;
  /** Variables de entorno extra. */
  env?: Record<string, string>;
}

const REQUEST_TIMEOUT_MS = 30_000;
// `uv run` en frío puede resyncear el venv — margen amplio, pero NUNCA colgar
// para siempre: sin este timeout un child vivo-pero-mudo dejaba toda promise
// pendiente eternamente (el timer de request recién se arma post-handshake).
const HANDSHAKE_TIMEOUT_MS = 60_000;
const MAX_CONSECUTIVE_FAILURES = 3;
const RELAUNCH_BACKOFF_MS = 300;

/**
 * Habla con un puente Python por stdio (JSON-lines). Un proceso de larga vida:
 * spawn perezoso al primer request, se relanza si muere (con backoff; el
 * presupuesto de reintentos se resetea en cada handshake exitoso), y hace RPC
 * por id. stdout del puente = SOLO respuestas JSON; stderr va al OutputChannel.
 */
export class PythonBridge implements vscode.Disposable {
  private proc?: ChildProcessWithoutNullStreams;
  private rl?: readline.Interface;
  private readonly pending = new Map<number, Pending>();
  private nextId = 1;
  private consecutiveFailures = 0;
  private ready?: Promise<BridgeReady>;
  private disposed = false;

  constructor(
    private readonly spec: BridgeSpec,
    private readonly out: vscode.OutputChannel,
  ) {}

  private ensureStarted(): Promise<BridgeReady> {
    if (this.ready) {
      return this.ready;
    }
    this.ready = new Promise<BridgeReady>((resolve, reject) => {
      const [cmd, ...args] = this.spec.command;
      this.out.appendLine(`[${this.spec.label}] spawn: ${this.spec.command.join(" ")} (cwd=${this.spec.cwd})`);
      let proc: ChildProcessWithoutNullStreams;
      try {
        proc = spawn(cmd, args, {
          cwd: this.spec.cwd,
          env: { ...process.env, PATH: augmentedPath(), ...this.spec.env },
        });
      } catch (e) {
        this.teardown();
        reject(new Error(`no pude spawnear el puente ${this.spec.label}: ${e}`));
        return;
      }
      this.proc = proc;

      // Todo handler de este spawn queda amarrado a SU proceso: tras un
      // restart, los eventos tardíos del proceso viejo no deben tocar el
      // estado (ni los pendings) del proceso nuevo.
      const isCurrent = () => this.proc === proc;

      const handshakeTimer = setTimeout(() => {
        if (!isCurrent()) {
          return;
        }
        this.out.appendLine(`[${this.spec.label}] handshake timeout (${HANDSHAKE_TIMEOUT_MS}ms) — matando el proceso`);
        proc.kill();
        this.teardown();
        reject(new Error(`el puente ${this.spec.label} no respondió el handshake en ${HANDSHAKE_TIMEOUT_MS / 1000}s (¿${cmd} en PATH? ¿env sano?)`));
      }, HANDSHAKE_TIMEOUT_MS);

      proc.on("error", (e) => {
        if (!isCurrent()) {
          return;
        }
        clearTimeout(handshakeTimer);
        this.out.appendLine(`[${this.spec.label}] error de proceso: ${e.message}`);
        this.teardown();
        reject(new Error(`el puente ${this.spec.label} no arrancó: ${e.message}`));
      });

      proc.stderr.setEncoding("utf-8");
      proc.stderr.on("data", (chunk: string) => {
        for (const l of chunk.split(/\r?\n/)) {
          if (l.trim()) {
            this.out.appendLine(`[${this.spec.label}:py] ${l}`);
          }
        }
      });

      this.rl = readline.createInterface({ input: proc.stdout });
      this.rl.on("line", (line) => {
        if (!isCurrent()) {
          return;
        }
        const trimmed = line.trim();
        if (!trimmed) {
          return;
        }
        let msg: unknown;
        try {
          msg = JSON.parse(trimmed);
        } catch {
          this.out.appendLine(`[${this.spec.label}] línea no-JSON en stdout: ${trimmed}`);
          return;
        }
        if (this.isReady(msg)) {
          clearTimeout(handshakeTimer);
          this.consecutiveFailures = 0; // presupuesto de relanzamiento renovado
          this.out.appendLine(`[${this.spec.label}] listo (root=${msg.root})`);
          resolve(msg);
          return;
        }
        this.handleResponse(msg as BridgeResponse);
      });

      proc.on("exit", (code, signal) => {
        if (!isCurrent()) {
          return; // proceso viejo: su muerte ya no le pertenece al estado actual
        }
        clearTimeout(handshakeTimer);
        this.out.appendLine(`[${this.spec.label}] salió (code=${code}, signal=${signal})`);
        const err = new Error(`el puente ${this.spec.label} murió (code=${code}, signal=${signal})`);
        this.rejectPending(err);
        this.teardown();
        // reject por si murió antes del handshake (resolve ya es no-op si ok).
        reject(err);
      });
    });
    return this.ready;
  }

  private isReady(m: unknown): m is BridgeReady {
    return typeof m === "object" && m !== null && (m as BridgeReady).ready === true;
  }

  private handleResponse(res: BridgeResponse): void {
    if (res.id == null) {
      this.out.appendLine(`[${this.spec.label}] respuesta sin id: ${JSON.stringify(res)}`);
      return;
    }
    const p = this.pending.get(res.id);
    if (!p) {
      return;
    }
    this.pending.delete(res.id);
    clearTimeout(p.timer);
    p.resolve(res);
  }

  private teardown(): void {
    this.rl?.close();
    this.rl = undefined;
    this.proc = undefined;
    this.ready = undefined;
  }

  /** Rechaza TODOS los requests in-flight. Debe correr en cada camino que
   * mata/reemplaza el proceso: tras teardown() el handler de 'exit' del
   * proceso viejo falla su guard isCurrent() y ya no los rechazaría — sin
   * esto quedarían colgados hasta su timeout de 30s con un error engañoso. */
  private rejectPending(err: Error): void {
    for (const [, p] of this.pending) {
      clearTimeout(p.timer);
      p.reject(err);
    }
    this.pending.clear();
  }

  /** Envía un request y espera su respuesta por id. Relanza (con backoff) si
   * el puente murió; tras MAX_CONSECUTIVE_FAILURES arranques fallidos seguidos
   * propaga el error (el contador se renueva en cada handshake exitoso). */
  async request(req: Omit<BridgeRequest, "id">): Promise<BridgeResponse> {
    if (this.disposed) {
      throw new Error(`el puente ${this.spec.label} fue liberado`);
    }
    try {
      await this.ensureStarted();
    } catch (e) {
      this.consecutiveFailures++;
      if (this.consecutiveFailures < MAX_CONSECUTIVE_FAILURES) {
        this.out.appendLine(`[${this.spec.label}] reintento de arranque (${this.consecutiveFailures}/${MAX_CONSECUTIVE_FAILURES})`);
        await sleep(RELAUNCH_BACKOFF_MS * this.consecutiveFailures);
        return this.request(req);
      }
      this.consecutiveFailures = 0; // el PRÓXIMO request vuelve a intentar
      throw e;
    }
    const proc = this.proc;
    if (!proc) {
      throw new Error(`el puente ${this.spec.label} no tiene proceso vivo`);
    }
    const id = this.nextId++;
    const full: BridgeRequest = { id, ...req };
    return new Promise<BridgeResponse>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`timeout (${REQUEST_TIMEOUT_MS}ms) esperando ${req.method} ${req.path} en ${this.spec.label}`));
      }, REQUEST_TIMEOUT_MS);
      this.pending.set(id, { resolve, reject, timer });
      proc.stdin.write(JSON.stringify(full) + "\n", (err) => {
        if (err) {
          clearTimeout(timer);
          this.pending.delete(id);
          reject(new Error(`no pude escribir al puente ${this.spec.label}: ${err.message}`));
        }
      });
    });
  }

  /** Fuerza un reinicio del proceso (comando "Restart Bridges"). */
  restart(): void {
    this.consecutiveFailures = 0;
    this.rejectPending(new Error(`el puente ${this.spec.label} fue reiniciado`));
    this.proc?.kill();
    this.teardown();
  }

  dispose(): void {
    this.disposed = true;
    this.rejectPending(new Error(`el puente ${this.spec.label} fue liberado`));
    this.proc?.kill();
    this.teardown();
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

/** VS Code lanzado desde el Dock (macOS) hereda el PATH de launchd, sin los
 * dirs donde viven `uv`/`python3` de brew o instaladores de usuario — el
 * mismo comando que funciona en la terminal daría ENOENT acá.
 * Exportado: packageService spawnea los MISMOS CLIs one-shot. */
export function augmentedPath(): string {
  const home = os.homedir();
  const extras = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    path.join(home, ".local", "bin"),
    path.join(home, ".cargo", "bin"),
  ];
  const current = (process.env.PATH ?? "").split(path.delimiter);
  for (const dir of extras) {
    if (!current.includes(dir)) {
      current.push(dir);
    }
  }
  return current.join(path.delimiter);
}
