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
| `src.sdk.runtime` | `WORKSPACE_VAULT_DIR`, `FilesystemMetadataStore`, `atomic_write_json`, `is_vault_session_id` (el PISO anti path-traversal de todo id de sesión que llega de afuera —URL, body— y termina en un `Path` del vault: `..` es el padre del vault, `.` el vault mismo, `_analytics`/`_campaigns` no son sesiones → `False`; el router lo chequea ANTES de tocar el filesystem y responde 400. No es política de formato: un plugin puede exigir además `wa_<dígitos>`, siempre con `fullmatch` — el `$` de `match` acepta un salto de línea final. Hallazgo 2026-09-18: `POST /api/orders/vault-orders/%2E%2E/.../retry` registraba en Medusa un pedido leído FUERA del vault. Consumidores: `chats/api/session_guard.py` y `orders/api`. Check: `tests/platform/test_vault_session_id.py`; sin template de CLI — los scaffolds no generan rutas sobre el vault), `get_temporal_client`, `with_heartbeat`, `setup_logging`, `client_ip` (IP real detrás del proxy: la clave de todo límite por IP en routers expuestos), `BoundedTTLCache(ttl_s=, max_entries=)` (el cache en proceso de una capa API: `get`/`put`/`clear`, vencimiento por TTL y tope de entradas, seguro entre hilos. Nunca un `dict` con TTL mirado solo al leer: con una key que cambia por request —un `since_ms` exacto, un set de ids— crece hasta reiniciar el proceso. Incidente 2026-09-25: ~8.1 MB retenidos por request en la API de Ads dejaron la caja de prod sin RAM. Consumidores: `ads/api` y `chats/api/dashboard.py`. Check: `tests/platform/test_ttl_cache.py`; sin template de CLI), `mba_customer_allowed` (lista cerrada de clientes de Meta Business Agent, `MBA_CUSTOMER_ALLOWLIST`, fail-closed: la consultan el oído `standby` de chats y el connector de mba), `mba_standby_enabled` (la flag `MBA_STANDBY_ENABLED` leída en cada llamada), `mba_controls_thread(metadata, session_id)` (¿Meta Business Agent responde en este hilo? flag + lista cerrada + `control_owner`/último inbound por `standby`; fail-safe False — la pregunta única de toda guarda de envío proactivo, D1.7), `is_placeholder` (¿es el placeholder de SSM? un adapter con placeholder no llama a nadie), `CONTROL_OWNER_MBA` / `CONTROL_OWNER_HUBARA` / `CONTROL_OWNERS` (valores de `metadata.control_owner`: quién responde al cliente según `messaging_handovers`; chats lo escribe, mba lo lee) |
| `src.sdk.eventkit` | `EventEnvelope`, `envelope_for`, `dispatch_event_activity`, `dispatch_envelope_with_client`, `Transition`/`TransitionAction`, helpers de eventos |
| `src.sdk.agentkit` | `run_agent_turn` + `CONVERSATIONAL_TURN_ACTIVITIES` (spread obligatorio, L-3), `TURN_ENDING_TOOLS`/`PRESENTATIONAL_TOOLS` (L-11), `register_tool_extension`, factories LLM/workspace/tools, `NO_MESSAGE_SENTINEL`/`is_no_message_abstention` (canal de abstención de un turno proactivo — incidente 2026-07-17, run 019f7234: sin él, "decidir no enviar" se enviaba al cliente como deliberación), `looks_like_admin_leak`/`sanitize_llm_text` (guards deterministas de texto LLM→cliente — runs 5f43bcd0 y 1c9ef231: detector de reporte administrativo + cleanup de wrappers/meta-prefijos; los usan el turn loop y el flush de UI intents; **dentro de un workflow** se llama `looks_like_admin_leak(text, extended=workflow.patched("admin-leak-patterns-v2"))` — el set de patrones es lógica de replay, L-21: patrón nuevo → set nuevo + patch propio, nunca editar el set original), `run_agent_turn(admin_turn=…)` (el caller declara que ningún texto del turno va al cliente: el loop corta en el cierre que la tool declara, `tag_closure.ends_turn`, y no le hace recordar al LLM lo que nunca salió — L-22) |
| `src.sdk.textkit` | guards PUROS del texto LLM→cliente para TOOLS: `sanitize_llm_text`, `keep_customer_safe_sentences`, `breaks_human_persona`, `looks_like_admin_leak` — mismos objetos que `agentkit`, pero SIN Temporal (R-DIP prohíbe `temporalio` en `tools/*.py`); el texto de un `customer_message` se valida en la tool/activity, nunca en el workflow (L-21/L-22) — ver [15-textkit.md](15-textkit.md) |
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
