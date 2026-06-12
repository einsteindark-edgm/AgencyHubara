# 01 · La fachada SDK (`src/sdk/` + `shared/sdk/`) y el lockdown P-28

> Fase F-SDK-0 · ADR-2026-06-12 · Gates: P-28 (ratchet) + 2 contratos import-linter

## Qué problema soluciona

Antes del SDK, los plugins importaban `src.platform.*` directo (223 imports
medidos y congelados). Eso significaba: (a) la plataforma NO podía refactorizar
sus internos sin riesgo de romper plugins; (b) no existía una respuesta única a
"¿qué puedo usar como autor de un plugin?"; (c) cada plugin nuevo copiaba
imports de otro plugin, propagando acoples accidentales.

La fachada declara la **superficie pública**: lo que está en `src.sdk` tiene
contrato de estabilidad; lo que está en `src.platform` es implementación
privada que puede cambiar sin aviso.

## Cómo funciona

- `hubara_agency/src/sdk/` re-exporta la superficie **medida** (la que los
  plugins ya usaban de facto) con el idiom `from x import y as y` (PEP 484
  re-export; además impide que `ruff --fix` pode los imports — lección L-0).
  No contiene lógica propia.
- La dirección del grafo se enforcea en tres piezas:
  1. **`.importlinter`** — `sdk-no-plugins` y `platform-no-sdk` (prohibición
     dura desde el día 0).
  2. **P-28** (`tests/architecture/test_p28_sdk_surface.py`) — ratchet: el
     estado real de imports `src.platform.*` en `src/plugins/` debe ser
     EXACTAMENTE la allowlist committeada
     (`tests/architecture/p28_platform_import_allowlist.txt`). Import nuevo →
     rojo con fix; import drenado → rojo pidiendo borrar la línea (progreso
     visible y monotónico).
  3. El espejo TS `frontend_dashboard/src/shared/sdk/index.ts` — superficie
     canónica del shell (PluginHost, apiClient, SSE) para plugins frontend.

## Cómo se usa

```python
# Foundation (lo que todo plugin necesita):
from src.sdk import get_task_queue, ensure_plugin_enabled, load_manifest

# Kits — importá el que corresponde a tu rol:
from src.sdk.runtime import WORKSPACE_VAULT_DIR, FilesystemMetadataStore
from src.sdk.eventkit import dispatch_event_activity, envelope_for
from src.sdk.agentkit import CONVERSATIONAL_TURN_ACTIVITIES, register_tool_extension
```

```ts
// Frontend:
import { usePluginHost, useSelection, apiClient } from "@/shared/sdk";
```

| Módulo | Qué hay |
|---|---|
| `src.sdk` | manifest (load/all/get_task_queue/get_worker_spec/get_workflow_name/transitions), `validate_enabled` (P-6), `ensure_plugin_enabled` (P-21), routing F6, protocolos `ApiModule`/`WorkerModule`/`ConversationRouteOwner`, errores tipados |
| `src.sdk.runtime` | `WORKSPACE_VAULT_DIR`, `FilesystemMetadataStore`, `atomic_write_json`, `get_temporal_client`, `with_heartbeat`, `setup_logging` |
| `src.sdk.eventkit` | `EventEnvelope`, `envelope_for`, `dispatch_event_activity`, `dispatch_envelope_with_client`, `Transition`/`TransitionAction`, helpers de eventos |
| `src.sdk.agentkit` | `run_agent_turn` + `CONVERSATIONAL_TURN_ACTIVITIES` (spread obligatorio, L-3), `TURN_ENDING_TOOLS`/`PRESENTATIONAL_TOOLS` (L-11), `register_tool_extension`, factories LLM/workspace/tools |
| `@/shared/sdk` (TS) | `usePluginHost`/`useSelection`/`PluginHostProvider`, `apiClient`/`ApiError`, `subscribeSse` |

## Cómo drenar la allowlist P-28 (el trabajo transversal)

1. Elegí un plugin; reemplazá `from src.platform.X import Y` por el
   equivalente `from src.sdk[...] import Y`.
2. Corré `cd hubara_agency && uv run pytest tests/architecture/test_p28_sdk_surface.py -q`
   → te exige borrar las líneas drenadas de la allowlist.
3. Regenerá SOLO si drenaste mucho:
   `cd hubara_agency && uv run python -m tests.architecture.test_p28_sdk_surface`
   (el gate impide que la lista CREZCA — regenerar nunca agrega deuda).

## Cómo extender el SDK (regla de oro)

Símbolo nuevo ⇒ en el MISMO PR: (1) el re-export en el kit correcto (o el
código nuevo si es del SDK mismo), (2) su check en el TestKit, (3) su doc acá
y/o template del CLI. Si te encontrás queriendo exportar algo "por las dudas",
parate: la fachada nace de uso real, no de especulación.
