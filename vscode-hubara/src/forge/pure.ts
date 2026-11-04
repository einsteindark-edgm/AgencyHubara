// Forge Console — lógica PURA (sin `vscode`, sin `fs`, sin `child_process`).
// Todo lo que acá vive se puede probar con `node` a secas: parseo del overlay
// del manifest (espejo de forge.py `overlay_agents()`), el cortador de líneas
// del stream del CLI, el parseo de `migrate.py status --json`, los avisos de
// client.yaml y la ficha en lenguaje llano de cada paso de la migración.

// ── Overlay del manifest (espejo exacto de forge.py overlay_agents) ──────────

export interface OverlayAgent {
  /** path del workspace del agente dentro del repo madre */
  path: string;
  /** archivos que el bundle del cliente DEBE traer para ese agente */
  required: string[];
}

/**
 * `{agente: {path, required}}` desde `manifest.workspace_overlay`.
 * Un valor string es el path (aplica el `required` común); un mapping
 * `{path, required}` trae su propia lista (p. ej. `mba_sales`). Igual que en
 * forge.py, un mapping sin `required` cae al común.
 */
export function overlayAgents(manifest: unknown): Record<string, OverlayAgent> {
  const ov = asRecord(asRecord(manifest).workspace_overlay);
  const common = asStringList(ov.required);
  const out: Record<string, OverlayAgent> = {};
  for (const [agent, spec] of Object.entries(asRecord(ov.agents))) {
    if (typeof spec === "string") {
      out[agent] = { path: spec, required: [...common] };
    } else {
      const m = asRecord(spec);
      const own = asStringList(m.required);
      out[agent] = { path: String(m.path ?? ""), required: own.length > 0 ? own : [...common] };
    }
  }
  return out;
}

/** La marca de redacción pendiente (`scan.todo_marker`), con el default del CLI. */
export function todoMarker(manifest: unknown): string {
  const m = asRecord(asRecord(manifest).scan).todo_marker;
  return typeof m === "string" && m ? m : "TODO-BRAND";
}

// ── Cortador de líneas del stream ────────────────────────────────────────────

/**
 * Corta un stream de texto en líneas COMPLETAS: la línea parcial del final de
 * un chunk se guarda hasta el siguiente (un chunk puede cortar a mitad de
 * línea). Las líneas en blanco se conservan — el CLI las usa para separar
 * bloques. `\r\n` se normaliza.
 */
export class LineSplitter {
  private rest = "";

  push(chunk: string): string[] {
    const text = this.rest + chunk;
    const parts = text.split(/\r?\n/);
    this.rest = parts.pop() ?? "";
    return parts;
  }

  /** Lo que quedó sin `\n` final (al cerrar el proceso). */
  flush(): string[] {
    const r = this.rest;
    this.rest = "";
    return r === "" ? [] : [r];
  }
}

// ── `migrate.py status --json` ───────────────────────────────────────────────

export interface MigrationStepRaw {
  id: string;
  title: string;
  kind: "auto" | "guided";
  done: boolean;
}

export interface MigrationStatusRaw {
  slug: string;
  company: string;
  /** la carpeta del clon que forjó el paso S1 (null si aún no) */
  dest: string | null;
  steps: MigrationStepRaw[];
}

/** Valida la forma del JSON; lanza Error con un mensaje claro si no cuadra. */
export function parseMigrationStatus(stdout: string): MigrationStatusRaw {
  // El JSON es la ÚLTIMA línea no vacía (por si algo más se coló al stdout).
  const lines = stdout.split(/\r?\n/).filter((l) => l.trim() !== "");
  const last = lines[lines.length - 1];
  if (!last) throw new Error("migrate.py no devolvió nada");
  let doc: unknown;
  try {
    doc = JSON.parse(last);
  } catch {
    throw new Error(`migrate.py devolvió algo que no es JSON: ${last.slice(0, 200)}`);
  }
  const d = asRecord(doc);
  if (!Array.isArray(d.steps)) throw new Error("migrate.py: falta la lista de pasos (steps)");
  const steps: MigrationStepRaw[] = d.steps.map((s, i) => {
    const r = asRecord(s);
    if (typeof r.id !== "string" || !r.id) throw new Error(`migrate.py: el paso #${i + 1} no tiene id`);
    return {
      id: r.id,
      title: typeof r.title === "string" ? r.title : r.id,
      kind: r.kind === "guided" ? "guided" : "auto",
      done: r.done === true,
    };
  });
  return {
    slug: String(d.slug ?? ""),
    company: String(d.company ?? ""),
    dest: typeof d.dest === "string" && d.dest ? d.dest : null,
    steps,
  };
}

// ── Ficha en lenguaje llano de cada paso ─────────────────────────────────────

export interface StepNeed {
  /** variable de entorno (nunca se pide en la UI: se lee del entorno de VS Code) */
  name: string;
  optional?: boolean;
}

export interface StepInfo {
  /** qué hace, para alguien que no programa */
  summary: string;
  /** texto del botón principal */
  action: string;
  /** llaves que el paso lee del entorno */
  needs: StepNeed[];
  /** programas de la terminal que el paso usa si están instalados */
  tools?: string[];
  /** necesita el clon ya forjado (imprime comandos que apuntan a él) */
  needsClone: boolean;
  /** solo muestra comandos: después hay que marcarlo como hecho */
  printsOnly?: boolean;
  /** aviso extra */
  note?: string;
}

export const STEP_INFO: Record<string, StepInfo> = {
  clone: {
    summary:
      "Crea la carpeta del repo nuevo del cliente a partir de este repo (lo mismo que «Forjar»).",
    action: "Forjar",
    needs: [],
    needsClone: false,
  },
  supabase: {
    summary: "Crea la base de datos del cliente: un proyecto Postgres nuevo en Supabase, solo para él.",
    action: "Ejecutar",
    needs: [{ name: "SUPABASE_ACCESS_TOKEN" }],
    needsClone: false,
  },
  medusa: {
    summary:
      "Crea la tienda (Medusa) del cliente en Railway, conectada a su base de datos del paso anterior.",
    action: "Ejecutar",
    needs: [{ name: "RAILWAY_API_TOKEN" }],
    needsClone: false,
  },
  "medusa-seed": {
    summary:
      "Prepara la tienda recién creada: región, canal de venta y la llave que usa el bot para leerla.",
    action: "Ejecutar",
    needs: [{ name: "MEDUSA_ADMIN_EMAIL" }, { name: "MEDUSA_ADMIN_PASSWORD" }],
    needsClone: false,
  },
  whatsapp: {
    summary:
      "Muestra los comandos para conectar el número de WhatsApp y pedir las aprobaciones de Meta (plantillas, formularios, medición). Meta puede tardar días: conviene empezar pronto.",
    action: "Ver comandos",
    needs: [],
    needsClone: true,
    printsOnly: true,
  },
  temporal: {
    summary:
      "Crea el espacio del cliente en Temporal Cloud (donde corren los procesos del bot) y su llave. Si el programa tcld no está instalado, solo muestra los comandos.",
    action: "Ejecutar",
    needs: [{ name: "TEMPORAL_CLOUD_API_KEY", optional: true }],
    tools: ["tcld"],
    needsClone: false,
    printsOnly: true,
    note: "Necesitas la llave en el entorno o haber hecho «tcld login» antes.",
  },
  "aws-bootstrap": {
    summary:
      "Muestra los comandos para preparar la cuenta de AWS del cliente (donde se guarda el estado, la llave SSH y el acceso de GitHub). Los corres tú, desde el clon.",
    action: "Ver comandos",
    needs: [],
    needsClone: true,
    printsOnly: true,
  },
  platform: {
    summary:
      "Muestra los comandos para crear la base de la plataforma en AWS y guardar los secretos del cliente. Los corres tú, desde el clon.",
    action: "Ver comandos",
    needs: [],
    needsClone: true,
    printsOnly: true,
  },
  compute: {
    summary:
      "Muestra los comandos para crear el servidor, hacer el primer despliegue y dejar las tareas programadas. Los corres tú, desde el clon.",
    action: "Ver comandos",
    needs: [],
    needsClone: true,
    printsOnly: true,
  },
};

/** Ficha de un paso; un paso que el CLI agregó y Studio no conoce sale genérico. */
export function stepInfo(id: string, kind: "auto" | "guided"): StepInfo {
  return (
    STEP_INFO[id] ?? {
      summary: "Paso nuevo de la migración (Studio aún no tiene su descripción).",
      action: kind === "guided" ? "Ver comandos" : "Ejecutar",
      needs: [],
      needsClone: kind === "guided",
      printsOnly: kind === "guided",
    }
  );
}

// ── Avisos de client.yaml (no bloquean: el CLI los tolera) ───────────────────

/**
 * Valores de client.yaml que siguen en plantilla: cualquier texto con «TODO»
 * (`TODO`, `TODO-owner/…`, `TODO-org-id`) y `api_url` vacío. Son avisos, no
 * bloqueos: forge los acepta, pero el clon saldrá con esos huecos.
 */
export function clientYamlWarnings(doc: unknown, slug: string): string[] {
  const out: string[] = [];
  const walk = (v: unknown, keyPath: string) => {
    if (typeof v === "string") {
      if (v.includes("TODO")) out.push(`${keyPath}: «${v}» sigue en plantilla`);
    } else if (Array.isArray(v)) {
      v.forEach((x, i) => walk(x, `${keyPath}[${i}]`));
    } else if (v && typeof v === "object") {
      for (const [k, x] of Object.entries(v)) walk(x, keyPath ? `${keyPath}.${k}` : k);
    }
  };
  walk(doc, "");
  const d = asRecord(doc);
  if (typeof d.api_url !== "string" || d.api_url.trim() === "") {
    out.push("api_url: vacío — se completa cuando exista el servidor del cliente (paso S8/S9)");
  }
  if (!d.android_app_id) {
    out.push(`android_app_id: no está — se usará com.acktos.${slug}`);
  }
  return out;
}

// ── Nombres por defecto (espejo de forge.py render_vars) ─────────────────────

/** `str.title()` de Python para un slug: «mitienda» → «Mitienda», «tienda2» → «Tienda2». */
export function pyTitle(s: string): string {
  return s.replace(/[A-Za-z]+/g, (w) => w[0].toUpperCase() + w.slice(1).toLowerCase());
}

/**
 * Nombre de la carpeta/repo del clon: la parte después de `/` en `repo`, o el
 * mismo default que forge.py (`Agency{slug.title()}`). Nunca con espacios ni
 * caracteres raros (va en una ruta de disco).
 */
export function defaultRepoName(repo: string, slug: string): string {
  const fromRepo = repo.includes("/") ? repo.split("/")[1] ?? "" : "";
  const name = fromRepo || `Agency${pyTitle(slug)}`;
  return name.replace(/[^A-Za-z0-9._-]/g, "") || `Agency${pyTitle(slug)}`;
}

// ── Validaciones compartidas (extensión + webview) ─────────────────────────────

/**
 * El nombre corto del cliente (espejo de forge.py `render_vars`). Termina en
 * nombres de AWS de alcance global: los buckets S3 y el dominio de Cognito no
 * aceptan `_`, y el más largo (`agency<slug>-<slug>-frontend-oac`) topa en 64.
 */
export const SLUG_RE = /^[a-z][a-z0-9]{1,19}$/;

/** Nombres de parámetro cuyo valor es un secreto. */
const SECRET_PARAM = /(TOKEN|SECRET|PASSWORD|API_?KEY|PRIVATE)/i;

/**
 * Tapa los secretos que un paso imprime antes de que lleguen al panel o al
 * canal de salida (VS Code guarda ese canal en disco): el valor de un
 * `--value` cuyo `--name` es un secreto (salvo `file://…`, que es la ruta) y
 * la contraseña de una URL `esquema://usuario:clave@host`.
 */
export function redactSecrets(line: string): string {
  return line
    .replace(
      /(--name\s+(\S+)\s.*?--value\s+)('[^']*'|"[^"]*"|\S+)/g,
      (m: string, head: string, name: string, value: string) =>
        SECRET_PARAM.test(name) && !/^['"]?file:\/\//.test(value) ? `${head}'••••'` : m,
    )
    .replace(/(\b[a-z][a-z0-9+.-]*:\/\/[^\s:@/]+:)[^\s@/]+@/gi, "$1••••@");
}

/**
 * La carpeta destino del clon: ruta COMPLETA y sin espacios ni caracteres que
 * la terminal interprete. Los pasos guiados la imprimen en `cd <dest>`: con un
 * espacio el `cd` falla y los comandos siguientes corren en la carpeta donde
 * estabas (el repo madre). null = válida.
 */
export function checkDestPath(dest: string): string | null {
  if (!dest.startsWith("/")) return "indica la ruta completa de la carpeta (empieza por /)";
  if (!/^[\p{L}\p{N}._@+\/-]+$/u.test(dest)) {
    return "la ruta no puede tener espacios ni caracteres como ' \" $ ; & ( ) * # — usa una carpeta sin ellos";
  }
  return null;
}

/**
 * Campos de `commerce` (client.yaml) que siguen en plantilla («TODO»): forge no
 * forja sin ellos salvo un clon de prueba. Sin el bloque, falta todo.
 */
export function commercePending(doc: unknown): string[] {
  const c = asRecord(doc).commerce;
  if (!c || typeof c !== "object" || Array.isArray(c)) {
    return ["commerce (falta el bloque: la política comercial de la tienda)"];
  }
  return Object.entries(c as Record<string, unknown>)
    .filter(([, v]) => v === null || v === undefined || (typeof v === "string" && v.includes("TODO")))
    .map(([k]) => `commerce.${k}`);
}

/**
 * El checkout PRINCIPAL del repo (espejo de forge.py `clients_root`): si Studio
 * corre en un worktree (`<repo>/.claude/worktrees/<x>`), los bundles de clientes
 * (gitignored) viven igual en `<repo>/forge/clients` — borrar el worktree no se
 * lleva el estado de una migración.
 */
export function mainRepoRoot(repoRoot: string): string {
  const marker = "/.claude/worktrees/";
  const at = repoRoot.indexOf(marker);
  return at === -1 ? repoRoot : repoRoot.slice(0, at);
}

// ── util ─────────────────────────────────────────────────────────────────────

function asRecord(v: unknown): Record<string, unknown> {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
}

function asStringList(v: unknown): string[] {
  return Array.isArray(v) ? v.map(String) : [];
}
