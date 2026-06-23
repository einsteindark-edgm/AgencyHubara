# docs/_sdk — documentación del Hubara Platform SDK

> **Para qué existe este directorio.** Cada gran funcionalidad del SDK tiene
> acá su documento: **qué problema soluciona, cómo funciona por dentro y cómo
> se usa**. Esta documentación es la **semilla de los skills** del pipeline
> (un agente que internalice estos docs programa contra el SDK sin romper la
> arquitectura) — mantenerla al día es parte de la regla de oro: ningún
> mecanismo nuevo sin su doc acá.
>
> Plan maestro: [PLATFORM_SDK_PLAN_fable.md](../../PLATFORM_SDK_PLAN_fable.md) ·
> Decisiones: [ADR-2026-06-12-platform-sdk.md](../../ADR-2026-06-12-platform-sdk.md) ·
> Arquitectura vigente: [ARCHITECTURE_FINAL_fable.md](../../ARCHITECTURE_FINAL_fable.md)

## Índice

| Doc | Funcionalidad | Fase |
|---|---|---|
| [01-fachada-sdk.md](01-fachada-sdk.md) | La fachada `src/sdk/` + espejo TS + lockdown P-28 | F-SDK-0 |
| [02-manifest-tipado.md](02-manifest-tipado.md) | `PluginManifest` pydantic + campo `archetype` | F-SDK-1 |
| [03-diagnosticos.md](03-diagnosticos.md) | Catálogo de diagnósticos (código → fix) | F-SDK-1 |
| [04-testkit-certificacion.md](04-testkit-certificacion.md) | TCK instanciado + niveles C0–C3 + reportes | F-SDK-2 |
| [05-arquetipos.md](05-arquetipos.md) | Perfiles de arquetipo (P-29) | F-SDK-2 |
| [06-cli.md](06-cli.md) | CLI `hubara` (check/certify/explain/create/graph) | F-SDK-3 |
| [07-connectorkit.md](07-connectorkit.md) | Ports, fakes, atribución y ratchet P-31 | F-SDK-4 |
| [08-catalogo.md](08-catalogo.md) | Certificación en el system-map (catálogo) | F-SDK-5 |
| [09-dashboard-bus.md](09-dashboard-bus.md) | `dashboardkit` (canal 1): push SSE al dashboard + drena P-28 | F-SDK-6 |
| [10-castkit.md](10-castkit.md) | `castkit` (canal 3): cast HTTP cross-plugin con identidad (auth) | hardening 2026-06-23 |

## El mapa mental en 30 segundos

```
plugins  ──importan──▶  src/sdk (fachada pública: Foundation + kits)
                          │ re-exporta
                          ▼
                        src/platform (implementación PRIVADA)
```

- **Foundation** (`src.sdk`): manifest, toggle, protocolos. Lo que TODO plugin usa.
- **Kits**: `runtime` (vault/Temporal/logging) · `eventkit` (canal 2,
  orquestación) · `dashboardkit` (canal 1, push SSE) ·
  `agentkit` (workers conversacionales) · `connectorkit` (vendors externos) ·
  `testkit` (el TCK).
- **El "compilador"**: `cd hubara_agency && uv run python -m src.sdk.cli check`.
- **La certificación**: `certify` → `.hubara/certification/<id>.json` → CI + catálogo.

## Reglas que NUNCA se negocian acá

1. Código nuevo en plugins importa `src.sdk` — jamás `src.platform.*` (gate
   P-28; los imports viejos están congelados en una allowlist que solo achica).
2. Ningún símbolo entra al SDK sin sus 3 patas: check en TestKit + template/
   CLI + doc en este directorio.
3. El SDK no importa plugins; platform no importa el SDK (import-linter).
