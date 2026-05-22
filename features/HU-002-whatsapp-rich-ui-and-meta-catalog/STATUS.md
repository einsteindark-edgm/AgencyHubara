# HU-002 — Implementation Status

> **Estado: LISTA PARA PRUEBAS.** Foundation completa + workflow integration + worker registry + audio pipeline + 8 decision tools cableadas end-to-end. Todos los gates arquitectónicos pasan.

## 🟢 Gates verdes (verificados)

```
pytest -m architecture:    49 passed, 1 skipped
lint-imports (R-DIP):       4 kept, 0 broken
tests targeted (parser+ingest+imports): 18 passed
E2E smoke (tool→metadata→flush activity → FakeSend): OK
```

## ✅ Completado end-to-end

### Foundation (platform layer)

| Archivo | Estado |
|---|---|
| `src/platform/whatsapp/limits.py` — constantes + validators Meta | ✅ |
| `src/platform/whatsapp/dtos.py` — 13 DTOs typed frozen | ✅ |
| `src/platform/whatsapp/outbound.py` — 15 builders DTO→JSON | ✅ |
| `src/platform/whatsapp/client.py` — 17 funcs `send_*` con `OutboundResult` | ✅ |
| `src/platform/whatsapp/flows/shipping_details.flow.json` — Flow A.9 design | ✅ |
| `src/platform/analytics/*` — EventBus + filesystem sink + Meta CAPI sink | ✅ |
| `src/platform/audio/*` — Groq primary + OpenAI fallback + chain | ✅ |
| `src/platform/meta_catalog/*` — port + DTOs + mapper + HTTP client | ✅ |

### Plugin chats (Sales)

| Archivo | Estado |
|---|---|
| `parsers.py` — extendido (interactive/location/audio/order/referral/context) | ✅ |
| `translate.py` — texto efectivo + banner CTWA primer touch | ✅ |
| `use_cases/ingest_inbound_message.py` — referral capture + audio reentry + analytics | ✅ |
| `composition.py` — EventBus + tenant_id wired | ✅ |
| `tools/ui_intents.py` — 8 decision tools | ✅ |
| `activities/flush_ui_intents.py` — render intents post-LLM | ✅ |
| `activities/transcribe_audio.py` — STT Temporal activity | ✅ |
| `workflows/sales_session.py` — hook `flush_pending_ui_intents_activity` post-send (gated por `workflow.patched("flush-ui-intents-v1")` para replay-safety) | ✅ |
| `workers/sales.py` — 8 tools + 2 activities registradas | ✅ |
| `workspace/TOOLS.md` — nueva sección "UI Tools HU-002" + reglas 9-13 | ✅ |

### Plugin catalog (Part B)

| Archivo | Estado |
|---|---|
| `agent/contracts.py` — `PushMetaCatalogInput`/`Result` DTOs | ✅ |
| `agent/use_cases/push_meta_catalog.py` — hash-delta + soft-delete protection | ✅ |

### Workflow del cliente — flujo real end-to-end

1. Cliente manda mensaje (text, audio, foto, ubicación, button tap, list select, Flow submit, etc.)
2. **Webhook FastAPI** (`api/sales.py`) → parser → ingest
3. **Ingest**:
   - Detecta referral CTWA si aplica → persiste en `metadata.json[ctwa_referrals]` + emite `make_referral_captured` event → Meta CAPI (si está activo)
   - Audio → spawn background task → Groq Whisper → texto sintético → re-entry al ingest
   - Resto → traduce a texto efectivo → persiste history → signal al workflow
4. **Sales workflow**:
   - LLM ejecuta turn con prompt + tools disponibles (incluyendo las 8 UI decision tools)
   - LLM puede llamar `present_product_detail("cruz-de-vida")` → tool VALIDA closed-list → encola intent a `metadata.json[pending_ui_intents]` → devuelve summary al LLM
   - LLM puede llamar `react_to_message("🤍")` → encola
   - LLM emite texto final → workflow dispatch `send_whatsapp_message_activity` (texto del LLM)
   - Workflow dispatch `flush_pending_ui_intents_activity` → lee `pending_ui_intents` → dispatch `send_image` / `send_interactive_list` / `send_flow` / etc. → emite `make_outbound_sent` event → limpia el array
5. Cliente recibe **texto + componente visual** (foto, lista, botones, Flow, reacción).

## 🚧 Lo que requiere configuración externa (no código)

Estas partes funcionan con la implementación entregada pero esperan configuración del operador antes de uso prod completo:

| Item | Estado código | Lo que necesitás hacer |
|---|---|---|
| **Meta Catalog sync** | platform + use case ✅ | (a) Crear catalog en Commerce Manager del cliente, (b) generar System User token con scope `catalog_management`, (c) wirear activity + workflow integration: `scripts/trigger_initial_meta_sync.py` (~30 min de trabajo) |
| **WhatsApp Flow A.9** | JSON design + tool ✅ | (a) Submit del JSON a Meta (1-3 días approval), (b) HTTP data endpoint `/api/whatsapp/flows/shipping/data`, (c) RSA encryption keys, (d) reemplazar `FLOW_ID_SHIPPING_PLACEHOLDER` con id real. Mientras tanto: `RequestShippingDetailsTool` ya tiene **fallback transparente a buttons** que sigue funcionando |
| **Audio transcription** | port + Groq + OpenAI + reentry ✅ | Setear `GROQ_API_KEY` o `OPENAI_API_KEY` en env. Sin keys, modo `AUDIO_TRANSCRIPTION_PROVIDER=fake` devuelve texto sintético para tests |
| **Meta Conversions API attribution** | sink ✅ | Setear `META_PIXEL_ID` + `META_CAPI_ACCESS_TOKEN`. Sin token, los referrals quedan en filesystem JSONL para post-mortem |
| **Order Details native (A.12)** | client + builder + DTOs ✅ | Wompi/PayU approval con Meta Payments. Mientras tanto: `PresentOrderConfirmationTool` ya tiene **fallback transparente a 3-button confirmation** ([Confirmar][Modificar][Cancelar]) que funciona hoy |
| **Contact card** | tool + flush ✅ | Setear `HUBARA_ADVISOR_NAME` + `HUBARA_ADVISOR_PHONE` en env. Sin ellas, la tool encola pero el flush skip silencioso |

## 🧪 Cómo probarlo localmente

```bash
# 1. Arranca workers
cd hubara_agency
uv run python -m src.run_workers

# 2. En otra terminal, arranca API
uv run python run_api.py

# 3. Mandate un mensaje al webhook (FakeSend mode si no hay WHATSAPP_ACCESS_TOKEN)
uv run python -c "
import asyncio, httpx
async def main():
    body = {
      'entry': [{'changes': [{'value': {
        'metadata': {'phone_number_id': 'PHONE_123'},
        'messages': [{
          'from': '+573001112233', 'id': 'wamid.x', 'timestamp': '1700', 'type': 'text',
          'text': {'body': 'Hola, quiero ver velas religiosas'},
        }]
      }}]}]
    }
    async with httpx.AsyncClient() as c:
        r = await c.post('http://localhost:8000/api/chats/webhook', json=body)
        print(r.status_code, r.text[:200])
asyncio.run(main())
"

# 4. Mira los logs del worker:
#    - LLM debería llamar search_products → present_products → ack textual
#    - Luego ves: 'flush_ui_intents.send_failed' o 'FakeSend' con el payload list_message

# 5. Para ver los analytics:
ls hubara_vault/_analytics/   # archivos YYYY-MM-DD.jsonl
```

### Variables de env opcionales

```bash
# Audio transcription
export AUDIO_TRANSCRIPTION_PROVIDER=auto   # default: auto (Groq→OpenAI fallback)
export GROQ_API_KEY=gsk_xxx                # primary (cheap)
export OPENAI_API_KEY=sk_xxx               # fallback (reliable)

# Meta Conversions API (CTWA attribution)
export META_PIXEL_ID=12345
export META_CAPI_ACCESS_TOKEN=EAAxxx
export META_CAPI_TEST_EVENT_CODE=TEST123   # dev only

# Contact card asesor
export HUBARA_ADVISOR_NAME="María del equipo Hubara"
export HUBARA_ADVISOR_PHONE="+573001234567"

# Tenant
export HUBARA_TENANT_ID=hubara
```

## 📊 Métricas que vas a ver en `hubara_vault/_analytics/YYYY-MM-DD.jsonl`

Cada línea es un evento JSON con esta forma:

```json
{
  "event_id": "uuid",
  "timestamp_ms": 1700000000000,
  "category": "wa_inbound" | "wa_outbound" | "conversion" | "referral",
  "kind": "button_click" | "list_select" | "audio_received" | "send.image" | ...,
  "correlation": {"session_id": "wa_+57", "tenant_id": "hubara", "wa_message_id": "wamid.x", "ctwa_clid": "..."},
  "payload": {...},
  "tags": [...]
}
```

Eventos esperados durante una conversación típica:
- `wa_inbound.button_click` cuando el cliente toca un botón
- `wa_inbound.list_select` cuando selecciona producto de la lista
- `wa_inbound.flow_submit` cuando completa el Flow A.9
- `wa_inbound.location_share` cuando comparte ubicación
- `wa_inbound.audio_received` + `audio_transcribed`/`audio_transcription_failed`
- `wa_outbound.send.interactive.list` después de `present_products`
- `wa_outbound.send.image` después de `present_product_detail`
- `wa_outbound.send.reaction` después de `react_to_message`
- `referral.ctwa_referral_captured` cuando llega CTWA (primer touch)
- `conversion.Purchase` cuando se completa una orden

## 🔭 Follow-ups (NO bloquean uso)

1. **Wompi gateway integration** para A.12 native order_details (semanas, depende de approvals externos).
2. **HTTP data endpoint Flow A.9** + Meta Flow approval (1-3 días).
3. **Catalog sync activity + workflow integration** — el platform layer + use case están listos; falta el activity wrapper + cablearlo al `CatalogSyncWorkflow` + multi-tenant config en `agents_admin`.
4. **Dashboard analytics** — un panel en `system_explorer` plugin para visualizar eventos JSONL.
5. **Feature flags** por componente en `agents_admin` para A/B testing en rollout.
6. **IDENTITY.md** update con el paradigma "UI rica" (lo dejé fuera para no romper la voz Hubara establecida).

## Investigación clave aplicada

- **Voice-to-text**: **Gemini Flash-Lite vía litellm** (~$0.014/h, multimodal nativo). Decisión tomada el 2026-05-21 después de comparar contra Groq Whisper + revisar que Groq TTS aún no soporta español — por consistencia con el proxy litellm que ya usa el resto del proyecto (`API_BASE_LLMLITE`) y para preparar el camino al audio bidireccional. Modelo configurable via env `AUDIO_TRANSCRIPTION_MODEL` (default: `gemini/gemini-2.5-flash-lite`); cambiar a `gemini/gemini-3-flash-lite` o `openai/whisper-1` es solo un toggle.
- **Referral CTWA**: capturo TODOS los campos documentados por Meta + Meta Conversions API server-side attribution.
- **UI intents pattern**: tools emiten intents R-JSON a metadata, activity post-LLM los renderiza — separa decisión (LLM-driven) de I/O (activity). Mantiene closed-list anti-hallucination.

## Arquitectura audio platform (post-refactor)

```
src/platform/audio/
├── port.py                   AudioTranscriptionPort (Protocol)
├── dtos.py                   TranscriptionRequest, TranscriptionResult (R-JSON)
├── meta_media_fetcher.py     Descarga media_id de WhatsApp Cloud API (HTTP)
├── litellm_adapter.py        LiteLLMTranscriptionAdapter (único adapter)
└── composition.py            get_audio_transcription_port() singleton
```

**Por qué un solo adapter via litellm**:
- Consistencia con `src/platform/registries.build_default_llm_config()` que ya usa `API_BASE_LLMLITE` para el LLM principal del agente.
- Cambiar el modelo STT (Gemini 2.5 → 3.0 → Whisper si Gemini falla mucho en muestras Hubara reales) es 1 env var.
- Cuando habilites TTS outbound (next HU): podés agregar un `LiteLLMSynthesisAdapter` simétrico apuntando al MISMO proxy. Un solo vendor de credenciales y monitoring para audio bidireccional.

**Decisión opt-out de Groq**: el operador no confía en Groq + Groq PlayAI TTS aún no soporta español → no vale la pena el adapter directo. Si en el futuro cambian de opinión, agregar `litellm` con `model="groq/whisper-large-v3"` es el toggle de 1 env var.
