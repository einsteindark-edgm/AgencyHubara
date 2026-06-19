# graphagents-dev

Harness de desarrollo (plugin de Claude Code) para **GraphAgents** — el
subsistema de agentes de análisis de datos de Meta Ads, construido con
**LangGraph** (task graphs deterministas) sobre el runtime durable **AgentSpan**,
orquestados por **manifests YAML**.

Es el gemelo de `hubara-dev`, pero para una arquitectura PROPIA y separada: se
apoya en los conceptos del monorepo (manifest-driven, SDK/kits, TCK/certificación,
archetypes, gates, TDD) **sin fusionar el código**. El subsistema vive en
`GraphAgents/`; este plugin lo gobierna.

## Qué bundlea

- **Skill `graphagents-developer`** — el método y el conocimiento (TDD, las reglas
  duras G-*, recetas, el panel de comandos, las lecciones L-#).
- **Subagents** — `graph-explorer` (mapear antes de editar), `graph-tdd-author`
  (escribir el golden rojo), `graph-cert-reviewer` (certificar antes de cerrar).
- **Hooks**:
  | Hook | Cuándo | Qué hace |
  |---|---|---|
  | `inject-rules.py` | SessionStart | Inyecta la ley del harness (TDD + G-DET + punteros) |
  | `tdd-guard.py` | PreToolUse (Edit/Write) | Recuerda el rojo al editar producción de GraphAgents (guía, no bloquea) |
  | `affected-tests.py` | PostToolUse (Edit/Write) | Corre el test afectado (golden/conformance) y reporta |
  | `stop-arch-cert-gate.sh` | Stop | Si tocaste manifests/sdk/grafos, corre **cert + arquitectura** y reporta |
- **Comando `/graphagents-gates [arch\|cert\|graphs\|manifests\|all]`** — el panel
  determinístico on-demand.

## Instalar / sincronizar

El repo `AgencyHubara` ES un marketplace local (`.claude-plugin/marketplace.json`).
Este plugin está registrado ahí como `graphagents-dev` (`source: ./graphagents-dev`).
Tras mergear a `main`, re-sincronizá el marketplace para que la nueva versión y
sus hooks queden activos (la sync se hace desde el panel de plugins de Claude
Code, fuera de esta sesión).

## Bloquear el cierre con panel rojo (opt-in)

Por defecto el hook `Stop` **informa** (no bloquea). Para que un panel rojo de
cert/arquitectura frene el cierre de la sesión y fuerce a resolverlo:

```bash
export GRAPHAGENTS_STOP_GATE_BLOCK=1
```

## Fuente de verdad

`GraphAgents/README.md` (la arquitectura del subsistema) + las `references/` del
skill. Si una doc contradice el código vivo de `GraphAgents/`, gana el código —
y esa contradicción es una lección `L-#` en `references/04-lessons.md`.
