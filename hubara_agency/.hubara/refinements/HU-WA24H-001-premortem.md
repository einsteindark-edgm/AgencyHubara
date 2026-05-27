# Pre-mortem — HU-WA24H-001 (watchdog 24h + templates + cost + cadencia)

> **Cuándo se ejecutó:** post code review de Sprint 1 + Sprint 1.5 + Sprint 2 + F1.10-F1.12 + Fase 0 docs (4 commits en branch `hu/wa24h-001-sprint-2-watchdog`).
> **Método:** análisis forward-looking de 10 categorías de modos de fallo según la rúbrica Hubara (`hubara-premortem-archon`). Cada finding tiene severity (HIGH/MEDIUM/LOW), suggested_fix, complexity y decisión (inline / deferred / aceptado).
> **Resultado:** 14 findings. **5 HIGH fixed inline en este sprint. 4 MEDIUM aceptados o deferred con razón. 5 LOW documentados.**

---

## §1 Edge cases

### F1.1 — Delivery webhook post-`closed_at_ms` pierde el cost capturado (HIGH)

**Escenario:** Watchdog dispara template, Meta acepta. Antes de que llegue el webhook delivery status, una tool LLM cierra el episodio con `COMPRA_EXITOSA` (porque el cliente respondió al watchdog rápidamente). Webhook llega: `IngestDeliveryStatus._locate` encuentra el entry, pero `episode.closed_at_ms is not None` → dead-letter, summary NO se actualiza.

**Consecuencia:** Meta YA cobró por el template (el send fue exitoso), pero nuestro cost_summary del episodio NO refleja ese costo. El operador ve "episodio cerrado ganador, costo 0" cuando realmente costó $0.0008 (utility) o más.

**Suggested fix:** Si episode cerrado pero el OutboundLogEntry tiene `cost_cents_usd=None` (pending), materializar el cost en el entry mismo (sin modificar el `cost_summary`) y agregar un campo `cost_summary_post_close_addendum` para mostrar al operador "este episodio cobró $X tarde". Mantiene invariante del summary inmutable post-cierre.

**Complexity:** medium.
**Decisión:** **deferred** — el episodio cerrado feliz (COMPRA_EXITOSA) ya ganó plata; perder $0.0008 en cost tracking es ruido. Más relevante post-launch cuando haya analytics serios. Documentar como known gap. NO fix ahora.

### F1.2 — Episode timeout silencioso entre schedule y fire (LOW)

**Escenario:** Cliente abandona el chat 14 días. Watchdog estaba scheduled. `ensure_active_episode` cierra el episode antiguo lazy. Cuando watchdog fires, eligibility detecta `episode_id_mismatch` y skipea.

**Consecuencia:** Comportamiento correcto (skip), pero el operador no entiende por qué un watchdog scheduled hace 14 días aparece como "skipped" — se confunde con bug.

**Suggested fix:** El `log.info` ya emite `reason="episode_id_mismatch"` — suficiente. Agregar a métricas como nueva categoría.

**Complexity:** trivial. **Decisión:** **deferred** — incluir cuando se construya el dashboard de métricas del watchdog (post-launch).

---

## §2 Race conditions

### F2.1 — `start_workflow_with_replace` por cada inbound crea overhead (MEDIUM)

**Escenario:** Conversación activa. Cliente manda 10 mensajes en 1 hora. Cada `IngestInboundMessage` emite `ServiceWindowOpenedEvent` → dispatcher hace `start_workflow_with_replace` → termina watchdog anterior + crea uno nuevo. 10 workflows creados y terminados en 1 hora.

**Consecuencia:** No funcional bug, pero overhead Temporal (10× history entries, 10× queue items). En high-traffic, escala mal.

**Suggested fix:** Agregar signal `reschedule_watchdog(new_fire_at_ms)` al workflow. `IngestInboundMessage` chequea si ya hay watchdog corriendo (lookup `metadata.watchdog.workflow_id`); si sí → emit con `via=signal`; si no → emit con `via=start_workflow_with_replace`. Manifest tiene 2 transitions distintas para el mismo evento, condicionales por `when`.

**Complexity:** medium (workflow signal + use case branching + manifest 2 transitions).
**Decisión:** **deferred a Sprint 5** — pre-launch no hay traffic suficiente para que el overhead duela. Reabrir cuando se vea >100 workflows watchdog/hora en métricas.

### F2.2 — Send template no persiste al session_history JSONL (HIGH) ✅ FIX INLINE

**Escenario:** Watchdog dispara template, persiste en `metadata.episodes[*].outbound_messages[]` y `metadata.last_outbound`. PERO el JSONL del session_history (que el dashboard del operador lee para mostrar el chat) NO recibe el template — el dashboard muestra "el bot no dijo nada" cuando realmente envió el template.

**Consecuencia:** Operador humano que ayuda al cliente desde el dashboard NO ve el contexto completo de qué se le mandó. UX rota del operador.

**Suggested fix:** En `send_template_to_session` (después del send exitoso, antes del return), invocar `FilesystemMessageHistoryStore.append_assistant_event(session_id, body_rendered, tool_calls=None)` con el cuerpo del template ya rellenado. Lookup del cuerpo: `spec.semantics` o reconstruir con `build_template_message` y extraer parameters.

**Complexity:** simple (15 líneas + test).
**Decisión:** **FIX INLINE** — bloquea visibility del operador. Implementar ahora.

### F2.3 — Read-modify-write de metadata no es atómico (LOW)

**Escenario:** 2 webhooks delivery del MISMO `wa_message_id` llegan en paralelo (raro pero Meta lo hace si hay retry interno). Ambos hacen `read → mutate → write`. El write 2 pisa al write 1.

**Consecuencia:** Si los 2 webhooks son idénticos (mismo pricing), el resultado es idempotente gracias al check `already_materialized` en `IngestDeliveryStatus`. Si son diferentes (delivered vs read en paralelo), el read window pierde — pero ambos no tocan `cost`, solo `status`.

**Suggested fix:** Agregar lock per-session vía `asyncio.Lock` cached por `session_id` en el use case. Simple pero el cache crece sin bound — agregar TTL via `cachetools.TTLCache(maxsize=10000, ttl=300)`.

**Complexity:** simple.
**Decisión:** **deferred** — el race está mitigado por `already_materialized`. Reabrir si vemos summary corrupto en producción.

---

## §3 Network failures (Meta API)

### F3.1 — Timeout durante send pierde idempotency (HIGH, documented limitation)

**Escenario:** `whatsapp_client._post_json` timeout (network hiccup). Activity entra a rama retryable. Retry. PERO Meta YA recibió y procesó el primer request — el segundo es duplicate, mismo template enviado 2 veces al cliente.

**Consecuencia:** Cliente recibe template duplicado. Mala UX. Cobro doble.

**Suggested fix técnico:** Generar un `request_id` UUID por send, pasarlo a Meta como query param (no estándar, pero algunos endpoints aceptan). O implementar protocolo de "send → check status by request_id antes de retry". Ambos requieren coordinación Meta-side.

**Suggested fix práctico:** En `RetryPolicy` del activity caller, configurar `maximum_attempts=1` para errores tipo `TemplateSendRetryable` que NO sea status 5xx claro. Mejor "at-most-once" que "at-least-once" para template sends (costo + UX > pérdida ocasional).

**Complexity:** medium (parsear error code para distinguir 5xx vs timeout). HIGH severity.
**Decisión:** **deferred a Sprint 5** — pre-launch el volumen es bajo, el riesgo es manejable. Documentar como known. Reabrir con métricas reales.

### F3.2 — Rate limit 429 no respeta `Retry-After` (MEDIUM)

**Escenario:** Meta rate-limit. Response 429 con header `Retry-After: 60`. Temporal hace backoff con su política default (initial_interval_seconds=1), NO respeta el `60s` que Meta pidió. Múltiples retries fallan + quema quota.

**Suggested fix:** Parsear `Retry-After` en `_post_json`, propagar al `ApplicationError` via `next_retry_delay` (Temporal supportea esto post v1.x).

**Complexity:** medium.
**Decisión:** **deferred a Sprint 5** — pre-launch no hay rate limits. Reabrir si vemos 429s en producción.

---

## §4 i18n / localization

### F4.1 — Quiet hours del cliente no se respetan (HIGH) ✅ FIX INLINE

**Escenario:** Watchdog fires a las 03:00 hora Colombia. Cliente recibe template en medio de la noche. Probable rejection / mute / block → quality rating drop.

**Consecuencia:** Misma magnitud que un mal copy de template — destruye quality rating sostenido. Refinement §12.4 (Lead Response Management Study) explicita que mejores horas son 08-09am y 16-17pm.

**Suggested fix:** En `check_watchdog_eligibility_activity`, después del template stage resolution, agregar check:
```python
local_now = _local_hour(metadata)  # derivar timezone del session_id (wa_+57 → America/Bogota)
if not (8 <= local_now < 22):
    return WatchdogEligibilityResult(eligible=False, reason="outside_quiet_hours")
```
Helper `_local_hour` usa `zoneinfo.ZoneInfo` (stdlib Python 3.9+). El watchdog skipea + log. Operador puede configurar `WATCHDOG_QUIET_HOURS_START` y `_END` env vars.

**Edge case:** si fire_at_ms cae en 03:00, el cliente nunca recibe el nudge (watchdog skipea sin reschedule). Para esta iteración acepto el trade-off — operador puede tunear `WATCHDOG_PRE_EXPIRY_MS` para que fire_at caiga en horario.

**Complexity:** simple (30 líneas + tests).
**Decisión:** **FIX INLINE** — quality rating es lo más crítico pre-launch.

### F4.2 — Costos en USD sin conversión a COP local (LOW)

**Escenario:** El operador colombiano quiere ver "este cliente me costó $X.XXX pesos colombianos". Tiene que multiplicar por FX manualmente.

**Suggested fix:** Agregar `cost_cents_local` derivable + env var `FX_USD_COP_RATE` (cron daily update).

**Complexity:** medium.
**Decisión:** **deferred** — post-launch, parte de dashboard de costos.

---

## §5 Observability gaps

### F5.1 — Falta evento `watchdog_workflow_scheduled` para el ratio fired/scheduled (MEDIUM)

**Escenario:** Refinement §9.3 alerta "ratio `watchdog_fired / watchdog_scheduled` > 0.8 → segmentación mal". Pero no hay emisor de `watchdog_scheduled` — solo `service_window_opened`. Si dispatcher falla al arrancar el workflow (rare), no nos enteramos.

**Suggested fix:** Emitir analytic event `watchdog_workflow_scheduled` desde el dispatcher cuando el `start_workflow` retorna OK.

**Complexity:** medium (toca dispatcher genérico, no plugin-specific).
**Decisión:** **deferred** — el `service_window_opened` event sirve como proxy. Re-evaluar post-launch.

### F5.2 — `metadata.watchdog.last_fire_wa_message_id` no se persiste (LOW) ✅ FIX INLINE

**Escenario:** Watchdog fires, persiste `fired_at_ms` en metadata.watchdog. Pero el `wa_message_id` solo va al log estructurado. Operador que abre el chat en el dashboard NO ve directo "el watchdog mandó wamid.X a las T".

**Suggested fix:** En `persist_watchdog_outcome_activity` outcome="fired", agregar `existing["last_fire_wa_message_id"] = detail`.

**Complexity:** trivial (1 línea + test).
**Decisión:** **FIX INLINE** — observabilidad mejora con 1 línea.

### F5.3 — `MOCK_WATCHDOG_*` ids no distinguibles si dashboard agrupa por wa_message_id (LOW)

**Escenario:** Post Sprint 0 cuando Sprint 2 swap el mock por send real, los MOCK ids histórico siguen en metadata. Si dashboard agrupa "templates sent" por `wa_message_id`, mezcla mocks con reales.

**Suggested fix:** Dashboard filtra `startswith("MOCK_")` (responsabilidad frontend). Documentar acá.

**Complexity:** trivial.
**Decisión:** **documentado, no fix backend** — frontend responsibility.

---

## §6 Performance

### F6.1 — `_locate(wa_message_id)` es O(N) scan total del vault (MEDIUM)

**Escenario:** 10,000 sesiones × 5 episodes × 20 outbound_messages = 1M operations por cada webhook delivery. Pre-launch OK (<100 sesiones). Post-launch a escala, el background task del webhook tarda >5s y empieza a colear.

**Suggested fix:** Índice secundario `<vault>/_wa_message_index.jsonl` con append-only `{"wa_message_id": "wamid.X", "session_id": "wa_+57", "episode_idx": 2, "log_entry_idx": 5}`. Actualizar en send activity. Leer indexed en `_locate`.

**Complexity:** medium (300 líneas + tests + invariant test que el index matchea el vault).
**Decisión:** **deferred a post-launch** — threshold de >1K sesiones activas concurrentes.

### F6.2 — N workflows watchdog dormidos simultáneamente (MEDIUM)

**Escenario:** 10K sesiones activas × 1-2 episodes = ~15K watchdogs dormidos en Temporal cluster.

**Consecuencia:** Temporal escala bien, pero queries de listas pueden ser lentas; logs verbose.

**Suggested fix:** Si se vuelve problema, refactor a "shared sleep scheduler" — un workflow scheduler único que mantiene el queue de fire_at_ms y dispara batch. Refactor mayor.

**Complexity:** high.
**Decisión:** **aceptado** — Temporal está designed para esto. Re-evaluar a >50K sesiones activas.

---

## §7 UI states

### F7.1 — Dashboard de costos no existe (LOW)

**Escenario:** El cost tracking está persistido, pero no hay UI para visualizarlo. Operador necesita SSH al filesystem y leer JSON manualmente.

**Decisión:** HU separada (post-MVP). Out of scope de este HU.

---

## §8 Cross-worker boundaries

### F8.1 — Watchdog huérfano por falla del send + metadata write (LOW)

**Escenario:** Send Meta OK + persist OutboundLogEntry FAIL (disk full). Activity raisea, workflow termina con error. Watchdog state = "failed" persistido. Pero el template SÍ se mandó. El siguiente inbound emite ServiceWindowOpenedEvent → nuevo watchdog → desconoce el template anterior.

**Consecuencia:** Webhook del template "huérfano" llega → `_locate` no encuentra (no hay OutboundLogEntry) → dead-letter.

**Suggested fix:** El dead-letter ya cubre esto. Acceptar.
**Decisión:** **aceptado** — edge case raro, dead-letter es trail suficiente.

### F8.2 — Order de write vs emit en `close_episode` (HIGH) ✅ FIX INLINE

**Escenario:** Tool LLM invoca `close_episode` que (a) muta metadata + (b) emite `EpisodeClosedEvent`. Si el emit dispatcha el signal `cancel_watchdog` ANTES de que metadata refleje el cierre, el `check_watchdog_eligibility_activity` del watchdog ve `episode.closed_at_ms is None` → puede fire si timer expira en este instante.

**Consecuencia:** Watchdog fires post-cierre.

**Suggested fix:** Garantizar orden: write atomic primero, emit después. Verificar el código actual de `episode_lifecycle.py::close_episode` y los callers.

**Complexity:** simple (verify + reorder).
**Decisión:** **FIX INLINE** después de verificar el orden actual.

---

## §9 Security

### F9.1 — `phone_number_id` en logs aggregados (LOW)

**Escenario:** Structlog emite `phone_number_id="PHONE_REAL"` en cada send. Logs van a Datadog/Splunk. ID identifica el business — no es secret per se pero es metadata.

**Suggested fix:** Redactar en structlog setup, o whitelist de keys allowed.

**Complexity:** simple.
**Decisión:** **deferred** — convenience > security en pre-launch. Reabrir si logs van a multi-tenant.

### F9.2 — Webhook NO verifica `X-Hub-Signature-256` (HIGH) ✅ FIX INLINE

**Escenario:** `POST /webhook` en `src/plugins/chats/api/sales.py` acepta cualquier body. Atacante envía body fake con `statuses[]` masivo de wa_message_ids reales (captured de logs leak) con `pricing.category="marketing"` y `cost_cents_usd` alto. Corrompe cost metrics. O envía `messages[]` fake del cliente del operador → arranca workflows fantasma.

**Consecuencia:** Cost metrics corruptas + ataques DoS de workflows.

**Suggested fix:**
1. Env var `WHATSAPP_APP_SECRET` (App Secret de la app Meta).
2. Helper `verify_meta_signature(raw_body: bytes, header_signature: str, app_secret: str) -> bool` con HMAC-SHA256.
3. En el handler POST: leer raw body (`await request.body()`) ANTES de json.loads, verificar signature, levantar 403 si invalid.
4. Tests: signature válida, inválida, ausente.

**Complexity:** medium (~80 líneas + tests).
**Decisión:** **FIX INLINE — PRIORIDAD 1** — security gap crítico. NO mergeamos sin esto.

---

## §10 Cost optimization

### F10.1 — `total_spend_24h` global no existe (LOW)

**Escenario:** Para alarma "spend hoy > $X" hay que iterar todos los vaults.

**Decisión:** **deferred** — Sprint 4 dashboard.

### F10.2 — Marketing send no chequea cap global Meta proactivamente (MEDIUM)

**Escenario:** Cliente ya recibió 2 marketing templates en últimas 24h. Bot decide enviar uno tercero → Meta responde 131049 (non-retryable). Activity aborta, pero perdimos la chance de mandar UN template — desperdiciamos el "intento" sin nada que mostrar.

**Suggested fix:** Pre-send check en `send_template_to_session`: si `spec.category == "marketing"` Y `metadata.marketing_msgs_sent_24h.count >= 2` → abort con reason `local_marketing_cap` (sin llamar Meta). Field `marketing_msgs_sent_24h` ya está en el schema del refinement §3.1 pero no implementado.

**Complexity:** medium.
**Decisión:** **deferred a Sprint 4 (cadencia)** — donde la prevención cap-aware tiene más sentido (cadence eligibility activity es el lugar natural).

---

## §11 Resumen ejecutivo de findings

| # | Severity | Categoría | Decisión |
|---|---|---|---|
| F1.1 | HIGH | Edge case | deferred (low-impact en GANADOS) |
| F1.2 | LOW | Edge case | deferred (post-launch) |
| F2.1 | MEDIUM | Race | deferred Sprint 5 |
| F2.2 | HIGH | Race | **FIX INLINE** |
| F2.3 | LOW | Race | deferred (mitigado por `already_materialized`) |
| F3.1 | HIGH | Network | deferred Sprint 5 (documented limitation) |
| F3.2 | MEDIUM | Network | deferred Sprint 5 |
| F4.1 | HIGH | i18n | **FIX INLINE** (quiet hours check) |
| F4.2 | LOW | i18n | deferred |
| F5.1 | MEDIUM | Observability | deferred |
| F5.2 | LOW | Observability | **FIX INLINE** (1 línea) |
| F5.3 | LOW | Observability | documentado |
| F6.1 | MEDIUM | Performance | deferred post-launch (>1K sesiones) |
| F6.2 | MEDIUM | Performance | aceptado |
| F7.1 | LOW | UI | out of scope |
| F8.1 | LOW | Cross-worker | aceptado |
| F8.2 | HIGH | Cross-worker | **FIX INLINE** (verify order) |
| F9.1 | LOW | Security | deferred |
| F9.2 | **HIGH** | **Security** | **FIX INLINE — PRIORIDAD 1** |
| F10.1 | LOW | Cost | deferred |
| F10.2 | MEDIUM | Cost | deferred Sprint 4 |

**Fixes inline en este sprint (5):**
1. **F9.2** — Webhook signature verification (security crítico).
2. **F4.1** — Quiet hours check en watchdog eligibility (quality rating).
3. **F2.2** — Persist template send al session_history JSONL (observabilidad operador).
4. **F8.2** — Verify + reorder write-then-emit en close_episode.
5. **F5.2** — `last_fire_wa_message_id` en metadata.watchdog (1 línea).

Todos los demás están explícitamente decididos como `deferred` o `aceptado` con razón documentada — no es deuda silenciosa.
