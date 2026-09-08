# ADR 2026-09-08 · El webhook `standby` de Meta Business Agent vive en `chats`

**Estado:** aceptado (D1.4 del roadmap MBA, `MBA_PRODUCTION_ROADMAP.md` §3.1).

## Contexto

Con Meta Business Agent (MBA) activo en el número, Meta envía el webhook
`standby` (campo `field == "standby"`, objeto `value.standby` con `messages`,
`message_echoes` o `statuses`) cuando MBA controla el hilo, y `messages` cuando
lo controla nuestra app. Ambos llegan a la MISMA URL pública
(`POST /api/webhook`), firmados con el mismo `X-Hub-Signature-256`.

Había dos lugares posibles para la rama `standby`:

- (a) el webhook de `chats` (`src/plugins/chats/api/sales.py`);
- (b) un pre-router en `src/platform` que reparta por `field` a cada plugin.

## Decisión

**(a): la rama vive en `chats`.** Razones:

1. `chats` es el dueño del ingress de WhatsApp y de todo lo que `standby`
   escribe: el JSONL de la sesión, `metadata.json` (episodios, ventana de
   servicio, `outbound_messages[]`, `cost_summary`), el dedupe por `wamid`.
   Un pre-router en platform solo movería el `if field ==` de lugar y
   obligaría a publicar por cast lo que ya es local.
2. El manifest de `mba` ya declara `depends_on: [chats]` y anota que "el
   webhook `standby` lo recibe el ingest de chats". `mba` no necesita ver el
   tráfico: sus connector tools escriben por el contrato `session-actions@v1`.
3. P-28 (ratchet SDK): un módulo nuevo de platform con lógica de plugins
   invertiría la dependencia (platform ❌→ plugins, R-DIP).
4. Un solo router público (`PUBLIC_ROUTER` allowlist: `chats.api.sales`,
   `mba.api.connector`) = una sola verificación de firma y un solo gate de
   arquitectura.

## Consecuencias

- `sales.py` reparte por `field`: `standby` → `IngestStandby` (vault, sin
  Temporal) + `IngestDeliveryStatus` para los statuses; `messaging_handovers`
  → 200 y log hasta D1.5; `messages` (o sin `field`) → el path de siempre.
  Un `standby` nunca llega al ingest de Sales (no hay turno del bot).
- El historial LLM de exoclaw (`EXOCLAW_STATE_DIR`) NO se escribe desde el
  API (no monta ese volumen; amnesia del PR #183). La copia completa de la
  conversación queda en el JSONL de la sesión (`wamid` + `sender="mba"`);
  al retomar Hubara el hilo (D1.6) se siembra desde ahí.
- Lo que `chats` necesita de platform para anotar el costo de los ecos y
  reabrir la ventana entra por `src.sdk.messagingkit`
  (`record_outbound_in_active_episode`, `OutboundLogEntry`,
  `compute_service_window_expiry`), con su check y su doc.
- Si algún día otro plugin necesita el tráfico `standby` (p.ej. un
  supervisor de calidad), se publica un evento por `eventkit` desde
  `IngestStandby`, no un segundo webhook.
