# Capability specs — behavior contracts del sistema Hubara

Inspirado en [OpenSpec](https://openspec.dev/) — adaptado al pipeline Archon
de AgencyHubara.

## Qué SÍ es esta carpeta

**La fuente de verdad de QUÉ HACE el sistema**, organizada por capability
(no por plugin, no por layer). Es persistente, versionada en git, y crece
orgánicamente con cada HU shipped.

Cada spec captura:
- **Purpose** — el rol de esta capability en el sistema
- **Requirements** — comportamientos observables (SHALL / MUST / SHOULD — RFC 2119)
- **Scenarios** — ejemplos concretos Gherkin-style (GIVEN / WHEN / THEN)

## Qué NO es esta carpeta

| NO es | Vive en | Diferencia |
|---|---|---|
| Arquitectura / estructura del código | `.claude/skills/hubara-architecture-guide/` | Específica de DEHA layering + R-rules + FSD anti-patterns. La estructura. |
| Refinement per-HU | `hubara_agency/.hubara/refinements/<HU_ID>-tech.md` (ephemeral durante el branch) | Lo que UNA HU quiere, no lo que el sistema hace. |
| Plan de implementación | `$ARTIFACTS_DIR/feature-plan-manifest.yaml` (ephemeral) | Tareas a ejecutar, no comportamiento. |
| Convenciones operacionales | `hubara_agency/.hubara/project-context.md` | Comandos, naming, paths protected. |
| Snapshots históricos de HUs | `hubara_agency/.hubara/archive/<date>-<HU_ID>/` | Memoria institucional. Las specs son el resultado vivo de aplicar deltas de cada archive. |

## Estructura

```
hubara_agency/.hubara/specs/
├── README.md                         # este archivo
├── _index.md                          # lista de todas las capabilities
├── plugins/                           # comportamiento por plugin
│   ├── orders/spec.md
│   ├── chats/spec.md
│   ├── catalog/spec.md
│   ├── eta/spec.md
│   ├── agents_admin/spec.md
│   └── system_map/spec.md
├── agents/                            # comportamiento de workers/agents
│   ├── sales-worker/spec.md
│   └── remarketing-worker/spec.md
├── messaging/spec.md                  # cross-plugin: WhatsApp inbound/outbound + events
├── observability/spec.md              # logging / tracing conventions
└── auth/spec.md                       # autenticación / autorización (cuando aplique)
```

## Formato canónico de un spec

```markdown
# <Capability Name>

## Purpose
<2-4 frases describing what role this capability plays in the system.>

## Requirements

### Requirement: <Behavior Title>
El sistema SHALL/MUST/SHOULD <observable behavior>.

#### Scenario: <Concrete situation>
- GIVEN <precondition>
- WHEN <action or event>
- THEN <expected outcome>
- AND <additional outcome, optional>

#### Scenario: <Edge case>
- GIVEN ...
- WHEN ...
- THEN ...
```

**Reglas:**
- Cada Requirement describe **comportamiento observable**, no implementación interna.
- Cada Requirement DEBE tener al menos 1 Scenario (preferiblemente 2-3 cubriendo happy path + edge cases).
- RFC 2119 keywords (MUST/SHALL/SHOULD/MAY) comunican fuerza del requirement.
- Si un cambio interno NO altera comportamiento observable, NO va a spec — va al refinement de la HU.

## Cómo se mantiene actualizado

1. **Bootstrap inicial** (manual): el operador escribe el spec de una capability leyendo el código vivo.
2. **Per HU**: el `hubara-tech-refiner-archon` produce `$ARTIFACTS_DIR/spec-deltas/<capability>/spec.md` con secciones `## ADDED Requirements`, `## MODIFIED Requirements`, `## REMOVED Requirements`.
3. **Post-merge**: el comando `hubara-archive-hu` (correrá al final del workflow) aplica los deltas a estos specs y mueve los artefactos a `hubara_agency/.hubara/archive/<date>-<HU_ID>/`.

## Quién lee estas specs

| Lector | Cuándo | Para qué |
|---|---|---|
| `hubara-tech-refiner-archon` | Antes de refinar | Entender contrato existente para no contradecirlo |
| `hubara-premortem-archon` | Después de implementer | Fundamentar failure modes en Requirements reales |
| `hubara-reviewer-deha`, `hubara-reviewer-plugin-system` | Multi-agent review | Cross-reference findings contra Requirements |
| `hubara-evaluator-archon` | Pre-PR | Verificar scenario coverage |
| Humanos / agents nuevos | Onboarding | "¿Qué hace este plugin?" sin leer código |

## Anti-patterns a evitar

- ❌ Spec con detalle de implementación ("el método `register_order` llama a `MedusaPort.create_draft`")
- ❌ Spec con nombres de clase Python específicos
- ❌ Requirement sin Scenario
- ❌ Scenario sin GIVEN explícito
- ❌ Duplicar lo que ya está en `hubara-architecture-guide` (R-rules, FSD anti-patterns)
- ❌ Spec de una capability inventada que no tiene código vivo todavía (usar refinement de HU para eso)

## Status del bootstrap

| Capability | Status | Owner |
|---|---|---|
| `plugins/orders` | ✅ initial bootstrap (2026-05-25) | seed |
| `plugins/chats` | ✅ initial bootstrap (2026-05-25) | seed |
| `messaging` | ✅ initial bootstrap (2026-05-25) | seed |
| `agents/sales-worker` | ✅ initial bootstrap (2026-05-25) | seed |
| `plugins/catalog` | ⏳ TODO (Fase A.7 si se decide) | — |
| `plugins/eta` | ⏳ TODO | — |
| `plugins/agents_admin` | ⏳ TODO | — |
| `plugins/system_map` | ⏳ TODO | — |
| `agents/remarketing-worker` | ⏳ TODO | — |
| `observability` | ⏳ TODO | — |
| `auth` | ⏳ TODO (postergado hasta que aplique) | — |

Las pendientes pueden bootstrappearse incrementalmente: cuando una HU
toque la capability X y no exista spec, el `hubara-tech-refiner-archon`
bootstrap inicial inline en el spec-delta (status: `seed_inline`) y el
operador la promociona post-merge.
