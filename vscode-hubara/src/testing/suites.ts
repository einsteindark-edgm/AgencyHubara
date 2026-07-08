export type Lado = "graphagents" | "backend" | "frontend";
export type Tipo = "compiler" | "cert" | "arch" | "golden" | "tools" | "integration" | "unit" | "build";

export interface SuiteDef {
  /** id estable, único — usado como TestItem id y en los selectores de plan. */
  id: string;
  label: string;
  lado: Lado;
  tipo: Tipo;
  /** comando + args; `${py}`/`${pyArgs}` se sustituyen por la config resuelta del lado. */
  command: string[];
  /** si es pytest, se le agrega `--junitxml=<tmp>` y se parsea para granularidad por-test. */
  junit: boolean;
  description: string;
}

/**
 * El inventario de "bundles atómicos" (§2.5 del plan) — un espejo de los
 * comandos que YA corren `/graphagents-gates` y `/hubara-gates`. Los test
 * plans (`*.hubaraplan.yaml`) seleccionan sobre este catálogo por tag o id;
 * NINGÚN comando se reinventa acá, son los mismos gates.
 */
export const SUITES: SuiteDef[] = [
  // ---- GraphAgents (cwd GraphAgents/, python3 — subsistema aislado, sin uv) ----
  {
    id: "graphagents/compiler",
    label: "Compiler (manifests)",
    lado: "graphagents",
    tipo: "compiler",
    command: ["${py}", "-m", "sdk.cli", "check"],
    junit: false,
    description: "python3 -m sdk.cli check — TCK estático de manifests, diagnósticos error[G-x]",
  },
  {
    id: "graphagents/cert",
    label: "Certificación TCK",
    lado: "graphagents",
    tipo: "cert",
    command: ["${py}", "-m", "pytest", "tests/conformance", "-q"],
    junit: true,
    description: "TCK C0–C3 por agente",
  },
  {
    id: "graphagents/tools",
    label: "Tools",
    lado: "graphagents",
    tipo: "tools",
    command: ["${py}", "-m", "pytest", "tests/tools", "-q"],
    junit: true,
    description: "per-tool TCK + golden de implementación",
  },
  {
    id: "graphagents/arch",
    label: "Arquitectura (G-*)",
    lado: "graphagents",
    tipo: "arch",
    command: ["${py}", "-m", "pytest", "tests/architecture", "-q"],
    junit: true,
    description: "reglas G-BIND, G-WIRE, G-CONTRACT, G-DET, ...",
  },
  {
    id: "graphagents/graphs",
    label: "Golden replays",
    lado: "graphagents",
    tipo: "golden",
    command: ["${py}", "-m", "pytest", "tests/graphs", "-q"],
    junit: true,
    description: "G-DET — golden-replay determinista por capability",
  },
  {
    id: "graphagents/integration",
    label: "Integration",
    lado: "graphagents",
    tipo: "integration",
    command: ["${py}", "-m", "pytest", "tests/integration", "-q"],
    junit: true,
    description: "runtime durable + recovery + API del viewer",
  },

  // ---- Hubara backend (cwd hubara_agency/, uv run) ----
  {
    id: "backend/compiler",
    label: "Compiler (plugins)",
    lado: "backend",
    tipo: "compiler",
    // -v: sin él el CLI solo imprime "(N warning/s)" en el status line y los
    // warnings reales quedan invisibles para el parser del Problems panel.
    command: ["${py}", "-m", "src.sdk.cli", "check", "-v"],
    junit: false,
    description: "uv run python -m src.sdk.cli check -v — TCK estático por plugin, error[P-x]",
  },
  {
    id: "backend/cert",
    label: "Certificación TCK",
    lado: "backend",
    tipo: "cert",
    command: ["${py}", "-m", "pytest", "tests/conformance", "-q"],
    junit: true,
    description: "TCK C0–C3 por plugin",
  },
  {
    id: "backend/arch",
    label: "Arquitectura (R-*)",
    lado: "backend",
    tipo: "arch",
    command: ["${py}", "-m", "pytest", "-m", "architecture", "-q"],
    junit: true,
    description: "ratchets P-27/P-28/P-29/P-31 + reglas R-DET/R-JSON/R-STATELESS/R-HEARTBEAT/R-DIP",
  },
  {
    id: "backend/lint-imports",
    label: "lint-imports (R-DIP)",
    lado: "backend",
    tipo: "arch",
    command: ["${pyRun}", "lint-imports"],
    junit: false,
    description: "uv run lint-imports — contratos de dependencia entre capas (import-linter)",
  },
  {
    id: "backend/plugins",
    label: "Invariantes de plugins",
    lado: "backend",
    tipo: "integration",
    command: ["${py}", "-m", "pytest", "tests/plugins", "-q"],
    junit: true,
    description: "premortem invariants del plugin system",
  },

  // ---- Frontend (cwd frontend_dashboard/, npm) ----
  {
    id: "frontend/arch",
    label: "Arquitectura FSD",
    lado: "frontend",
    tipo: "arch",
    command: ["${npm}", "run", "test:arch"],
    junit: false,
    description: "vitest run src/test/architecture — reglas Feature-Sliced",
  },
  {
    id: "frontend/types",
    label: "Tipos (tsc)",
    lado: "frontend",
    tipo: "compiler",
    command: ["${npx}", "tsc", "-b", "--noEmit"],
    junit: false,
    description: "tsc -b --noEmit — sin emitir, solo diagnósticos",
  },
  {
    id: "frontend/unit",
    label: "Unit (vitest)",
    lado: "frontend",
    tipo: "unit",
    // vitest necesita el path explícito (a diferencia de pytest, que recibe
    // `--junitxml=<tmp>` auto-agregado) — `${junit}` lo resuelve el runner
    // al MISMO tmp file que después parsea (junit:true).
    command: ["${npx}", "vitest", "run", "--reporter=junit", "--outputFile=${junit}"],
    junit: true,
    description: "vitest run",
  },
  {
    id: "frontend/build",
    label: "Build",
    lado: "frontend",
    tipo: "build",
    command: ["${npm}", "run", "build"],
    junit: false,
    description: "tsc -b && vite build",
  },
];

export const LADO_LABEL: Record<Lado, string> = {
  graphagents: "GraphAgents",
  backend: "Hubara Backend",
  frontend: "Frontend",
};
