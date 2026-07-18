# 13 — Paquetes Acktos (`.acktospkg`): export/install entre repos

## Qué soluciona

Los proyectos aliados son **clones forjados** del repo central (`forge/` —
misma topología, motor intacto). Después del forjado los repos **divergen**:
un plugin nuevo (o una versión nueva) creado en el central no tenía cómo
viajar al clon. El formato `acktospkg/1` cierra ese loop: exportás un plugin
y/o un graph agent como un paquete portable y lo instalás en cualquier repo
Hubara-shaped — desde Acktos Studio o por CLI — sin tocar UN solo archivo
central (INV-1 lo garantiza; lo compartido se regenera).

## El formato

```
<name>.acktospkg                  (tar.gz)
├── package.yaml                  # format + name + source{commit,repo} + índice
├── units/plugin-<id>/            # unidad kind=plugin (CLI hubara)
│   ├── unit.yaml                 # id/version/archetype/payload/requires
│   ├── backend/                  # hubara_agency/src/plugins/<id>/
│   ├── frontend/                 # frontend_dashboard/src/plugins/<id>/ (con plugin.yaml)
│   └── tests/                    # hubara_agency/tests/plugins/<id>/
├── units/graphagent-<id>/        # unidad kind=graphagent (CLI GraphAgents)
│   ├── unit.yaml                 # payload file-level + tool_dirs + ports
│   ├── manifests/  graphs/  tools/  tests/  fixtures/
└── checksums.sha256              # integridad por archivo (se verifica al leer)
```

Reglas del formato:

- **Las unidades son self-describing** (`unit.yaml`): el sellado agrega TODO
  lo stageado en `units/` — así un paquete puede llevar unidades de los dos
  sistemas. Cada CLI instala SOLO sus kinds; el kind ajeno le es `foreign`.
- **El TCK instanciado no viaja**: se regenera en el destino con el mismo
  template del scaffolder (`conformance_suite("<id>")`).
- **k8s no viaja** (decisión 2026-07-18 — no se usa).
- **La clausura es automática**: `depends_on` (plugins) y `agent://` refs
  (taskgraphs) entran solos, deps primero. La capability, las tools
  (`uses: <id>@<major>` → `tools/<id>/`), los tests golden/build/tool, los
  fixtures que esos tests referencian **y los ⚡ cases del viewer**
  (`fixtures/cases/*.case.yaml` con `target: agent:<id>`/`flow:<id>` + sus
  `$ref`) viajan con el agente.
- **Export fail-fast**: un manifest que no pasa el modelo tipado
  (`parse_manifest`) no se exporta — el error aparece en el origen, no en el
  certify del destino.
- **Requirements explícitos**: env vars (`${...}` del compose +
  `wiring_intents.env_vars_required`), secrets (`env_secrets[].var`), ports
  (`consumes:`) quedan declarados en el paquete — el instalador los muestra
  como checklist post-install, nunca los adivina.

## CLI

```bash
# repo central (exportar)
cd hubara_agency && uv run python -m src.sdk.cli package plan  marketing --json
cd hubara_agency && uv run python -m src.sdk.cli package build marketing -o dist/marketing.acktospkg
cd GraphAgents   && python3 -m sdk.cli package build order-sentinel -o dist/sentinel.acktospkg

# paquete combinado (plugin + graph agent en UN archivo)
cd GraphAgents   && python3 -m sdk.cli package stage order-sentinel --staging /tmp/stage
cd hubara_agency && uv run python -m src.sdk.cli package build marketing \
    -o dist/combo.acktospkg --staging /tmp/stage

# repo destino (clon forjado)
cd hubara_agency && uv run python -m src.sdk.cli package plan-install combo.acktospkg --json
cd hubara_agency && uv run python -m src.sdk.cli package install      combo.acktospkg
cd GraphAgents   && python3 -m sdk.cli package install combo.acktospkg --root .
```

`--repo` (hubara) / `--root` (GraphAgents) apuntan a cualquier repo/GA-root;
por default operan sobre el repo actual. Salida `--json` estable para Studio.
Exit codes: 0 ok · 2 input inválido/paquete corrupto.

## Acktos Studio

- **`Acktos: Exportar paquete`** — picker multi-select (plugins + graph
  agents del repo abierto) → plan con clausura y requirements → confirmación
  → `.acktospkg` (save dialog; default `dist/`).
- **`Acktos: Instalar paquete`** — elegís el archivo → plan-install de ambos
  CLIs (new/overwrite con **versiones destino → paquete y ⚠ DOWNGRADE**, deps
  faltantes) → confirmación → **rama `acktos/install-<pkg>-<ts>` (forkeada
  SIEMPRE de la default, no del HEAD del operador) → install → codegen
  (`plugins:sync` + `render-compose`) → certify (TCK hubara + check
  GraphAgents) → commit → merge a la default u opción de dejar la rama**
  (+ push opcional). Si la certificación falla, la rama queda sin mergear
  para diagnóstico; si el flujo falla a mitad de camino, Studio ofrece
  **rollback al estado previo**; si el merge conflictúa, se aborta solo
  (nada queda a medio merge). El chequeo de working-tree limpio es solo
  sobre tracked (un `.acktospkg` descargado no bloquea; además está
  gitignoreado y nunca entra al commit).

La UI es piel, los CLIs son músculo (D-10): Studio solo orquesta los verbos
de arriba (`src/packages/packageService.ts`).

## Post-install (manual, lo lista el plan)

1. `ENABLED_PLUGINS` += ids instalados (simetría INV-2).
2. Provisionar env vars/secrets que declara el paquete (SSM/compose env).
3. Templates de Meta / schedules si el plugin los usa (ver su manifest).
4. Suites completas cuando quieras el gate fuerte: `uv run pytest -q` +
   `npm test` + `python3 -m pytest` (GraphAgents).

## Límites conocidos

- El paquete lleva **código y contratos**, no estado (vault, production.yaml,
  credenciales) ni la plataforma (SDK/platform se actualizan por su canal —
  si el destino no tiene el verbo `package`, actualizá la plataforma del clon
  primero).
- Seams cross-sistema nuevos (`vscode-hubara/seams.yaml`) no viajan en el
  paquete — llegan por el canal de plataforma.

## Dónde vive

- Hubara: `src/sdk/packaging.py` + verbos en `src/sdk/cli/__init__.py` ·
  tests `tests/sdk/test_packaging*.py`.
- GraphAgents: `sdk/packaging.py` + verbos en `sdk/cli.py` · tests
  `tests/sdk/test_packaging*.py` (mismo formato, re-implementado — la
  frontera comparte conceptos, no módulos).
- Studio: `vscode-hubara/src/packages/packageService.ts` + comandos
  `acktos.exportPackage` / `acktos.installPackage`.
