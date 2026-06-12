# 05 · Arquetipos — la arquitectura interna obligatoria (P-29)

> Fase F-SDK-2 · Fuente única: `hubara_agency/src/sdk/testkit/archetypes.py`

## Qué problema soluciona

Un template garantiza el día 0; nada impedía que el día 400 el plugin fuera
spaghetti que "implementa los kits" sin forma interna. El arquetipo separa
dos conceptos: **template** (acto generador, un día) vs **arquetipo**
(identidad declarada en el manifest y **auditada de por vida**).

## Cómo funciona

- El manifest declara `archetype: api_only | full_stack | agentic | notifier
  | sync` (P-29A exige presencia; el enum vive en `manifest_model.py` y el
  schema lo espeja).
- Cada arquetipo tiene su **perfil declarativo** (`ArchetypeProfile`):
  superficies del manifest requeridas/prohibidas, flags de coherencia,
  globs de estructura interna. Del MISMO perfil derivan el scaffolder del
  CLI, el check P-29 del TCK y el catálogo (INV-5: el que genera, audita).
- Severidades honestas: `required_*` = **error** (la realidad actual los
  cumple — verificado al clasificar); `advisory_*` = **warning** (la
  dirección de migración, visible en el reporte sin romper CI).
- **No existe `archetype: custom`**: plugin que no encaja ⇒ se agrega/
  extiende un arquetipo EN el SDK con ADR. Cambiar de arquetipo = editar el
  manifest y pasar el perfil nuevo ENTERO.

## Los 5 perfiles

| Arquetipo | Modelo real | Exige | Prohíbe | Advisory (warning) |
|---|---|---|---|---|
| `api_only` | system_map | `api:` | `frontend:`, workers, agentic | `domain/` (api delgada) |
| `full_stack` | orders, ads, agents_admin | `frontend:` + `api:` | agentic | `domain/` |
| `agentic` | chats | workers + `agentic: true` + ≥1 worker con `dashboard:` + `agent/*/workspace` | — | `agent/*/use_cases` |
| `notifier` | eta | workers + `agent/*/activities` | `owns_route` (L-4: notificar ≠ poseer) | — |
| `sync` | catalog | workers + `agent/**/{workflows,activities,use_cases}` | `owns_route`, `dashboard:` en workers, agentic | — |

## Cómo se usa

```yaml
# plugin.yaml
id: reviews
version: 0.1.0
archetype: full_stack   # P-29 lo audita en cada corrida del TCK
```

```bash
# Ver el veredicto del perfil:
cd hubara_agency && uv run pytest "tests/conformance/test_reviews_conformance.py" -q
# o el detalle con fix:
cd hubara_agency && uv run python -m src.sdk.cli check reviews
```

## Cómo evoluciona un perfil (regla de oro + honestidad)

1. Endurecer un advisory a required: PRIMERO migrar los plugins del tipo
   (que el repo real cumpla), DESPUÉS mover el glob de `advisory_globs` a
   `required_globs` — nunca al revés (un gate que rompe main el día que nace
   se revierte y pierde autoridad).
2. Arquetipo nuevo: ADR + perfil acá + template en el CLI + fila en esta
   tabla + caso en el self-test. Las 3+2 patas en el mismo PR.
3. La regla de import intra-plugin (estilo ArchUnit: "domain no importa
   adapters") entra cuando exista la estructura `domain/` migrada —
   PENDIENTE F-SDK-7, anotado acá para no "descubrirlo".
