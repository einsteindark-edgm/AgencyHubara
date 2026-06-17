# 05 · La superficie del SDK (kits, certificación, CLI)

> Digest de `docs/_sdk/` (01–09). Dirección de dependencias: **plugins → sdk →
> platform**. Un plugin importa de `src.sdk`; `src.platform.*` es
> implementación PRIVADA (el ratchet P-28 congela los imports legacy).

## Los kits (importá el de tu ROL, no hay God-module)

| Kit | Rol |
|---|---|
| `src.sdk` (Foundation) | manifest, toggle, protocolos, routing — lo universal |
| `src.sdk.runtime` | vault, metadata store, Temporal client, heartbeat, logging |
| `src.sdk.eventkit` | canal 2: eventos + dispatcher + transitions (cross-worker, durable) |
| `src.sdk.dashboardkit` | canal 1: push al dashboard (bus in-process / SSE, efímero) |
| `src.sdk.agentkit` | workers conversacionales: turn loop + tools + registries |
| `src.sdk.connectorkit` | ports de capability hacia vendors externos |
| `src.sdk.testkit` | el TCK: checks + perfiles de arquetipo + reportes |
| `src.sdk.cli` | `uv run python -m src.sdk.cli` |

Si un plugin necesita algo que no está en el SDK: **agregalo al SDK** con sus 3
patas (check + template/CLI si aplica + doc en `docs/_sdk/`), no importes
platform directo. Receta de drenado: §4.7 (ejemplo: dashboardkit).

## Certificación (niveles C0–C2)

- `none` manifest inválido · `C0` declarado pero algo no existe · `C1` cargable
  pero una P-rule falla · `C2` TCK verde · `C3` conducta (reservado).
- Gates: `cd hubara_agency && <dummies> uv run pytest tests/conformance -q`
  (cada plugin instancia su TCK — P-27) · `… cli certify <id>` escribe
  `.hubara/certification/<id>.json` (gitignored, derivable).
- La certificación gobierna **merge y catálogo, nunca el runtime**.

## El CLI (verbos deterministas)

`cd hubara_agency && uv run python -m src.sdk.cli <verbo>`:

- `check [<id>...]` — compilador rápido (TCK estático, sin red). Exit 0/1.
- `certify [<id>...]` — check + reporte + niveles. Exit 1 si algún plugin < C2.
- `explain <código>` — el diagnóstico de una regla (`P-27`, `C1-DEPS`).
- `graph [--format=mermaid|json]` — el grafo del sistema desde los manifests.
- `create plugin <id> --archetype <a>` — scaffold que NACE C2 + corre su TCK.

Una fuente (`src/sdk/testkit/checks.py`), tres frontends (pytest · reporte ·
CLI). El CLI no implementa reglas: delega en el TestKit.

---
Fuente canónica: `docs/_sdk/` (01–09) + `src/sdk/CLAUDE.md`. Si difiere del
código vivo, gana el código vivo.
