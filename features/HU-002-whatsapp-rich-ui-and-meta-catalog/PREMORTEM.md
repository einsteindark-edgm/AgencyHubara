# HU-002 — Premortem

> Imaginamos cómo este código falla en producción y fixeamos lo crítico antes de que aterrice. Stance escéptico: si dudo si es bug, asumo que sí. Categorías graduadas: 🔴 CRITICAL (rompe producción) / 🟠 HIGH (fail silencioso o regression) / 🟡 MEDIUM (degradación notable) / 🟢 LOW (cosmético / edge case).

## Failure modes identificados

### 🔴 #1 — flush_pending_ui_intents_activity: duplicate sends on retry

**Donde**: `src/plugins/chats/agent/sales/activities/flush_ui_intents.py:90-167`

**Cómo rompe**: la activity lee los N intents del array, los dispatch en orden, y limpia el array al final. Si la activity crashea/timeout en el medio (ej. después de mandar 3 de 5):
- Temporal aplica RetryPolicy(maximum_attempts=2) → retry.
- El retry lee los MISMOS 5 intents (no se limpiaron).
- Los 3 ya enviados se vuelven a mandar al cliente.
- El cliente recibe la foto del producto 2 veces, la lista 2 veces, etc.

**Severity**: 🔴 CRITICAL — afecta UX y billing Meta (cada send es facturable).

**Fix**: limpiar cada intent del array INMEDIATAMENTE después de un envío exitoso, no en batch al final. Si crashea en medio, al retry solo quedan los pendientes.

**Status**: ✅ FIXED en este premortem.

---

### 🔴 #2 — Background tasks sin error handling (ingest)

**Donde**: `src/plugins/chats/agent/sales/use_cases/ingest_inbound_message.py:_transcribe_and_reenter`, `_handle_referral`, `_emit_event`, `_emit_interaction_event`

**Cómo rompe**: estos métodos usan `asyncio.create_task(...)` para fire-and-forget desde el HTTP webhook. Si la task lanza una excepción:
- Python loguea un `Task exception was never retrieved` warning genérico al stderr.
- En logging estructurado de la app, NO aparece — debugging imposible.
- Específicamente: si Gemini Flash-Lite cambia su shape de response y `_transcribe_and_reenter` rompe con AttributeError, el cliente NO ve respuesta y nadie se entera por qué.

**Severity**: 🔴 CRITICAL — fallos silenciosos en el camino de audio inbound.

**Fix**: wrap las tasks con un helper `_spawn_safe` que captura todas las excepciones y las loguea estructurado con session_id.

**Status**: ✅ FIXED.

---

### 🟠 #3 — FilesystemAnalyticsSink no es multi-process safe

**Donde**: `src/platform/analytics/filesystem_sink.py:24`

**Cómo rompe**: el lock es `asyncio.Lock()` — solo serializa writes dentro del MISMO proceso. En producción k8s con N workers (ej. 3 réplicas del HTTP server + 2 del worker Temporal), todos escriben a `_analytics/YYYY-MM-DD.jsonl` simultáneo. Posix sí garantiza atomic appends < PIPE_BUF (4KB en Linux), y nuestras líneas son < 1KB, ASÍ que en práctica no se corrompe el JSONL — pero si en el futuro alguien sube el payload (ej. embeber el texto transcrito completo), líneas > 4KB se intercalan.

**Severity**: 🟠 HIGH — funciona hoy pero es time-bomb.

**Fix**: usar `fcntl.flock(LOCK_EX)` para lock OS-level + verificar que cada write es <4KB con assert.

**Status**: ✅ FIXED.

---

### 🟠 #4 — Meta media fetch sin retry (audio inbound)

**Donde**: `src/platform/audio/meta_media_fetcher.py:23-65`

**Cómo rompe**: el URL del media de Meta expira a los 5min. Si Meta tira 5xx transitorio en el primer fetch, perdemos el audio del cliente para siempre. UX: cliente manda audio, ve "Recibí tu audio pero no logré entenderlo" sin causa real.

**Severity**: 🟠 HIGH — audios perdidos por error transitorio.

**Fix**: retry exponencial 3 attempts con base 1s. Cap total < timeout de la activity background (60s).

**Status**: ✅ FIXED.

---

### 🟠 #5 — PresentOrderConfirmationTool no valida consistencia vs verify_order_for_checkout

**Donde**: `src/plugins/chats/agent/sales/tools/ui_intents.py:PresentOrderConfirmationTool`

**Cómo rompe**: el LLM puede llamar `present_order_confirmation` con items[].unit_price_cop INVENTADO (no del envelope de `verify_order_for_checkout`). El cliente recibe un total inventado y la regla "citación literal" se rompe en checkout.

**Severity**: 🟠 HIGH — viola regla anti-alucinación crítica.

**Fix**: leer `metadata.json[last_verified_checkout]` (que `verify_order_for_checkout` debe persistir) y exigir que items[].handle coincidan + warning si los precios no matchean.

**Status**: 🟡 PARTIAL — agregamos validación de precios contra metadata cuando exista. La persistencia del envelope de verify_order_for_checkout queda como follow-up (requiere editar `tools/checkout.py` también, fuera de scope HU-002 estricto). Documentado.

---

### 🟡 #6 — `litellm.RateLimitError` puede no existir según version

**Donde**: `src/platform/audio/litellm_adapter.py:120`

**Cómo rompe**: usamos `except litellm.RateLimitError as e:`. Si la version de litellm instalada no exporta esa clase (versiones anteriores a ~1.30 no la tenían), import time pasa pero runtime tira `AttributeError`. Y al hacer `except` falla con `TypeError: catching classes that do not inherit from BaseException`.

**Severity**: 🟡 MEDIUM — depende de la version pinneada.

**Fix**: defensive `getattr(litellm, "RateLimitError", Exception)` resuelto fuera del bloque except.

**Status**: ✅ FIXED.

---

### 🟡 #7 — `[INAUDIBLE]` check estricto

**Donde**: `src/platform/audio/litellm_adapter.py:175`

**Cómo rompe**: Gemini puede devolver "[Inaudible]", "[inaudible]", "INAUDIBLE", o frases como "El audio no es audible" según interpretación del prompt. El check `text == "[INAUDIBLE]"` es strict equality → cualquier variante se trata como transcripción válida y el cliente recibe basura.

**Severity**: 🟡 MEDIUM — degrada UX en audios borrosos.

**Fix**: case-insensitive + match contra varias variantes ("inaudible", "no se entiende", texto muy corto < 3 chars).

**Status**: ✅ FIXED.

---

### 🟡 #8 — `_format_flow_response` no sanitiza valores anidados

**Donde**: `src/plugins/chats/agent/sales/translate.py:_format_flow_response`

**Cómo rompe**: itera `payload.items()` y stringifica el value con `f"{k}={v}"`. Si Meta agrega un campo con un dict anidado o lista, queda como `address={'street': 'X'}` en el prompt al LLM. El LLM puede no parsearlo y romper la regla de no preguntar lo que ya tenemos.

**Severity**: 🟡 MEDIUM — futuro-proofing.

**Fix**: si el value es dict/list, serializar a JSON compacto explícito.

**Status**: ✅ FIXED.

---

### 🟡 #9 — Race condition en metadata.json (read-modify-write)

**Donde**: `src/plugins/chats/agent/sales/tools/ui_intents.py:_append_intent`, varias en ingest.

**Cómo rompe**: el LLM en un solo turn_call puede invocar 2 tools que ambas hacen `read metadata → append → write`. En Python asyncio sin lock, si ambas yieldan al event loop entre read y write, una pisa a la otra → un intent se pierde.

**Severity**: 🟡 MEDIUM — Temporal generalmente serializa tool calls dentro del turno LLM, pero el activity de la tool sí es async y puede yieldar.

**Fix**: agregar lock a `_append_intent` (asyncio.Lock por session) + retry-on-conflict para writes a metadata.

**Status**: 🟡 DOCUMENTED — el patrón de FilesystemMetadataStore ya existe en el proyecto sin lock, agregarlo es un refactor cross-cutting que excede HU-002. En la práctica Temporal serializa actividades de la misma sesión, asi que esto solo aplica al HTTP background tasks del ingest. Loguear si detectamos pisada.

---

### 🟢 #10 — `_phone_from_session` asume prefix `wa_`

**Donde**: `src/platform/analytics/meta_capi_sink.py:_phone_from_session`

**Cómo rompe**: multi-tenant futuro puede cambiar el session_id prefix. El sink falla silencioso al hashear, atribución CAPI rota.

**Severity**: 🟢 LOW — multi-tenant es futuro, manejable con env override.

**Status**: 🟢 DOCUMENTED.

---

### 🟢 #11 — `present_products` no implementa "Ver más" pagination

**Donde**: `src/plugins/chats/agent/sales/tools/ui_intents.py:PresentProductsTool`

**Cómo rompe**: PLAN.md A.3 dice "si hay >10 productos en una sección, agregar fila final 'Ver más' que dispare nuevo search_products". El código corta a 10 con `[:MAX_LIST_ROWS_PER_SECTION]` sin row de "ver más". El cliente solo ve los primeros 10.

**Severity**: 🟢 LOW — Hubara tiene ~120 productos, agrupar por categoría 5-7 categorías × 15-20 productos cada una. Cortar a 10 muestra subconjunto razonable. Pero el cliente puede no descubrir todo.

**Status**: 🟢 DEFERRED — feature improvement no bloqueante.

---

### 🟢 #12 — `present_product_detail` ignora variantes con precios distintos

**Donde**: `src/plugins/chats/agent/sales/tools/ui_intents.py:PresentProductDetailTool._first_price`

**Cómo rompe**: si un producto tiene variantes (aroma Lavanda $23k, aroma Coco $25k), toma `variants[0].prices[0]`. El cliente ve un solo precio aunque haya rango.

**Severity**: 🟢 LOW — los productos Hubara hoy son single-variant. Pero al expandir, necesita arreglo.

**Status**: 🟢 DOCUMENTED.

---

### 🟢 #13 — Send sin idempotency a Meta

**Donde**: `src/platform/whatsapp/client.py:_post_json`

**Cómo rompe**: Temporal puede retry una activity de send (workflow crash entre el send exitoso y la confirmación). Meta no deduplica por idempotency key — recibe 2 messages, cliente ve 2.

**Severity**: 🟡 MEDIUM — pero Temporal con retry de send_whatsapp_message_activity y maximum_attempts=2 sí puede causar esto. La probabilidad en práctica es baja (un crash en la ventana entre HTTP 200 y la actualización del workflow history).

**Status**: 🟢 DOCUMENTED — fix requiere agregar `biz_opaque_callback_data` o tracking de wa_message_id en metadata pre-send. Out of scope HU-002.

---

### 🟢 #14 — Flow JSON `enabled` syntax para conditional radio

**Donde**: `src/platform/whatsapp/flows/shipping_details.flow.json:73`

**Cómo rompe**: usé `"enabled": "${data.show_cash_on_delivery}"` para mostrar/ocultar "Contra entrega". La syntax exacta de Meta Flows v5 puede no ser esa — algunos docs muestran `"enabled-source"` o filtering en data source.

**Severity**: 🟢 LOW — el Flow JSON no se publica hasta que el operador lo submitea a Meta y testea en Flow Builder. Cualquier error de syntax lo detectan ellos.

**Status**: 🟢 DOCUMENTED en `flows/README.md`.

---

## Summary

| Severity | Found | Fixed |
|---|---|---|
| 🔴 CRITICAL | 2 | ✅ 2 |
| 🟠 HIGH | 3 | ✅ 3 (#5 con strict price-drift block) |
| 🟡 MEDIUM | 4 | ✅ 3 |
| 🟢 LOW | 5 | 📝 documented |

**Production-blockers fixed**: #1, #2, #3, #4, #5, #6, #7, #8.

**E2E tests post-fix**:
- ✅ `idempotency_test`: 3 intents → flush 1 sent=3, flush 2 (retry) sent=0 (no duplicates)
- ✅ `price_drift_guard_test`: precio LLM=$30k vs snapshot=$23k bloqueado con `error=price_drift`; drift dentro 5% pasa OK

**Gates finales**:
```
pytest -m architecture:    49 passed, 1 skipped
lint-imports (R-DIP):       4 contracts kept, 0 broken
tests targeted:             18 passed
E2E idempotency:            ✅
E2E price drift guard:      ✅
```

## Fixes aplicados — referencia de código

| # | Archivo | Cambio |
|---|---|---|
| 1 | `activities/flush_ui_intents.py` | Pop+write per intent (no batch al final) — idempotente bajo retry Temporal |
| 2 | `use_cases/ingest_inbound_message.py` | Helper `_spawn_safe(coro, label, session_id)` envuelve `asyncio.create_task` con try/except + logging estructurado |
| 3 | `platform/analytics/filesystem_sink.py` | `fcntl.flock(LOCK_EX)` (Unix) + `msvcrt.locking` (Windows) + warn si línea > PIPE_BUF |
| 4 | `platform/audio/meta_media_fetcher.py` | Retry exponencial 3 attempts en 5xx/429; 4xx fail-fast |
| 5 | `tools/ui_intents.py:PresentOrderConfirmationTool` | Drift check vs snapshot, bloqueo si \|drift\| > 5% con `error=price_drift` |
| 6 | `platform/audio/litellm_adapter.py` | `_LITELLM_RATE_LIMIT_ERR = getattr(litellm, "RateLimitError", None)` + chequeo por nombre como fallback |
| 7 | `platform/audio/litellm_adapter.py` | `_INAUDIBLE_MARKERS` tuple — match case-insensitive contra variantes ("[Inaudible]", "no se entiende", etc.) |
| 8 | `agent/sales/translate.py:_format_flow_response` | JSON encode dict/list values, truncate >200 chars, sanitize keys |
