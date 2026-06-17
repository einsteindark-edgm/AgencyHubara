# hubara-dev — harness de desarrollo de AgencyHubara

Plugin de Claude Code que empaqueta, como una sola unidad, todo lo necesario
para programar en el monorepo AgencyHubara **bien y de forma determinística**:
un skill de desarrollador, subagents especializados, y hooks que enforzan TDD y
corren los gates. Es la materialización ejecutable de la semilla
`ARCHITECTURE_FINAL_fable.md`.

## Por qué un plugin (y no skills sueltas)

El formato plugin es la forma vigente de Claude Code de bundlear **skills +
agents + hooks + commands** con separación limpia y activación como unidad
(versionable, instalable, compartible). Eso da exactamente lo que un harness
necesita: rules, hooks y agents claramente separados.

```
hubara-dev/
├── .claude-plugin/plugin.json          # manifest
├── skills/hubara-plugin-developer/
│   ├── SKILL.md                        # entry: el método + cuándo leer qué + a quién delegar
│   └── references/                     # ← RULES (progressive disclosure)
│       ├── 00-tdd-law.md               # TDD obligatorio (§3.5) — el corazón
│       ├── 01-hard-rules.md            # qué te frena cada gate (§3)
│       ├── 02-recipes.md               # recetas test-first (§4)
│       ├── 03-command-panel.md         # panel determinístico (§8)
│       ├── 04-lessons.md               # qué NO repetir (§9, L-0..L-15)
│       └── 05-sdk-surface.md           # kits + certificación + CLI (docs/_sdk)
├── agents/                             # ← AGENTS (subagents, contexto aislado)
│   ├── hubara-explorer.md              # mapea un subsistema antes de editar (read-only)
│   ├── hubara-tdd-author.md            # escribe el test rojo primero
│   └── hubara-gate-reviewer.md         # corre §8 + audita §3/§9 antes de cerrar
├── hooks/                              # ← HOOKS (enforcement determinista)
│   ├── hooks.json
│   └── scripts/{inject-rules,tdd-guard,affected-tests}.py · run-gates.sh
├── commands/hubara-gates.md            # /hubara-gates → corre el panel §8
└── README.md
```

**Las reglas no se duplican.** Las referencias son distilaciones operativas que
apuntan a la fuente de verdad (`ARCHITECTURE_FINAL_fable.md`, `docs/_sdk/`). Si
una referencia contradice el código vivo, gana el código vivo — y eso es una
lección nueva para §9.

## Cómo enforza TDD (guiado + rojo/verde)

Tres capas, de menor a mayor fricción:

1. **SessionStart** (`inject-rules.py`) — inyecta la ley TDD en cada sesión del
   proyecto. Siempre activa, barata.
2. **PreToolUse** (`tdd-guard.py`) — al editar código de producción (`src/` que
   no sea test), inyecta un recordatorio fuerte ("¿test rojo primero?"). **No
   bloquea** — un bloqueo duro frena refactors legítimos y la heurística
   test↔código es frágil.
3. **PostToolUse** (`affected-tests.py`) — tras la edición, corre el test
   afectado y devuelve 🔴/🟢. Esto es lo que un hook SÍ puede hacer con
   certeza: cerrar el bucle rojo→verde.

Estos hooks **componen** con los del repo (`pre-bash-cd-check`,
`post-edit-lint`, `stop-arch-gate`, …): se apilan, no se reemplazan.

## Instalación (plugin local vía marketplace del repo)

El repo es su propio marketplace (`.claude-plugin/marketplace.json` en la raíz).
Desde una sesión de Claude Code en el repo, corré los comandos interactivos:

```
/plugin marketplace add .
/plugin install hubara-dev@agencyhubara
```

(Si tu build de Claude Code no tiene plugins habilitados, el fallback es copiar
`skills/` a `.claude/skills/`, `agents/` a `.claude/agents/`, y wirear
`hooks/hooks.json` en `.claude/settings.json`. El plugin es la forma vanguardia;
el fallback funciona en cualquier versión.)

Verificá con `/plugin` (debe listar hubara-dev habilitado) y `/hubara-gates`.

## Uso

El skill `hubara-plugin-developer` dispara solo cuando la tarea toca el repo
(implementar/modificar un plugin, agente, entity/feature, el SDK, manifests, o
correr gates). El bucle es siempre: orientate (delegá en `hubara-explorer` si
no conocés la zona) → **rojo** (escribí el test que falla, o delegá en
`hubara-tdd-author`) → **verde** → **refactor** → verificá (`/hubara-gates` o
`hubara-gate-reviewer`).

## Licencia

Uso interno del proyecto AgencyHubara. No redistribuir sin autorización del
dueño del repo.
