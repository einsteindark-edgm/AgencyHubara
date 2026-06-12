# ADR-2026-06-12 — Platform SDK: fachada `src/sdk/`, lockdown de superficie y certificación

**Estado:** aceptado (ordenado por el operador; ejecuta `PLATFORM_SDK_PLAN_fable.md`).

## Contexto

Post-refactor F1–F8 los plugins importan `src.platform.*` directo (~100 sitios
medidos). La plataforma no tiene superficie pública declarada: cualquier
refactor interno de `platform/` puede romper plugins, y un plugin nuevo se
crea copiando otro. El plan completo (motivación, fases F-SDK-0..8, riesgos)
vive en [PLATFORM_SDK_PLAN_fable.md](PLATFORM_SDK_PLAN_fable.md); este ADR
registra las decisiones irreversibles y autoriza los cambios a paths
PROTECTED que el plan exige.

## Decisión

1. **Fachada interna, no wheel** — el SDK nace como `hubara_agency/src/sdk/`
   (re-export curado de `src/platform/`) + espejo TS
   `frontend_dashboard/src/shared/sdk/`. Se promueve a package instalable
   solo cuando un segundo repo/tenant lo consuma.
2. **Dirección de dependencias**: `plugins → sdk → platform`. Dos contratos
   nuevos en `.importlinter`: `src.sdk` no importa `src.plugins`;
   `src.platform` no importa `src.sdk`.
3. **Lockdown con ratchet (P-28)** — los imports `src.platform.*` existentes
   en plugins quedan CONGELADOS en una allowlist committeada
   (`tests/architecture/p28_platform_import_allowlist.txt`). Código nuevo
   importa `src.sdk`; cada import drenado se borra de la allowlist (el gate
   exige igualdad exacta en ambas direcciones).
4. **Manifest gana `archetype:`** (F-SDK-1) — campo nuevo en
   `plugin.schema.yaml` (spinal, autorizado acá) clasificando cada plugin:
   `api_only | full_stack | agentic | notifier | sync`. Su check es el perfil
   de conformance P-29 (F-SDK-2).
5. **Certificación gobierna merge y catálogo, NO runtime** — los reportes
   (`.hubara/certification/`, gitignored) alimentan CI y el system-map;
   producción sigue gobernada por `ENABLED_PLUGINS` + boot fail-fast.
6. **CLI como módulo** (`uv run python -m src.sdk.cli`, F-SDK-3) — sin
   `[project.scripts]` porque el proyecto raíz no es package instalable
   (sin build-system); se revisita si se empaqueta el SDK.
7. **ConnectorKit por promoción, no por big-bang** (F-SDK-4) — los 9 ports
   existentes se re-exportan desde `src.sdk.connectorkit`; los adapters
   Medusa NO se mueven físicamente en esta tanda (P-31 congela el estado con
   su propio ratchet de vendors).

## Paths PROTECTED / operator-owned autorizados por este ADR

`hubara_agency/tests/architecture/**` (gates P-27/P-28/P-29/P-31 + allowlists)
· `hubara_agency/.importlinter` (contratos sdk) ·
`frontend_dashboard/src/plugins/_schema/plugin.schema.yaml` (campo
`archetype`) · `.github/workflows/architecture-gates.yml` (job
plugin-certification). El PR lleva label `architecture-change`; las corridas
locales de arch-tests en esta rama usan `ARCH_CHANGE_APPROVED=1`.

## Consecuencias

- (+) `platform/` gana libertad de refactor: el contrato con plugins es el SDK.
- (+) Un gate nuevo en el TestKit upgradea a TODOS los plugins (suite
  instanciada, no copiada).
- (−) Doble nombre transitorio para la misma función (vía platform en código
  viejo, vía sdk en nuevo) hasta drenar la allowlist — el gate P-28 hace el
  estado visible y monotónico.
- Regla de oro extendida vigente: ningún símbolo/protocolo/port/arquetipo
  nuevo del SDK sin (a) check en el TestKit, (b) template/CLI, (c) catálogo.
