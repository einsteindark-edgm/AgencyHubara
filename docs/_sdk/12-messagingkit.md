# 12 · MessagingKit — decisión y ejecución de envíos WhatsApp

**Qué soluciona.** Un plugin que toca al cliente por WhatsApp necesita tres
cosas sin importar `src.platform` (P-28): decidir SI/CON-QUÉ-COSTO mandar,
derivar el estado del lead, y ejecutar el send por un template aprobado.

**Superficie** (`src.sdk.messagingkit`):

| Símbolo | Rol |
|---|---|
| `evaluate_send`, `SendDecision` | la central de costo/canal (matriz ventana × categoría × rate card) |
| `decide_reengagement`, `LeadState`, `lead_state_from_metadata` | capa funnel de reactivación |
| `get_current_rate_card` | rate card vigente (marketing CO = 12500 usd_micros/msg) |
| `is_quiet_hours_for_session`, `resolve_local_timezone` | quiet hours por sesión |
| `is_service_window_closed` | guard de ventana de servicio 24h |
| `load_reengagement_index`, `update_reengagement_index_entry(/-ies)`, `reengagement_shortlist` | índice incremental de reactivación |
| `send_whatsapp_template_activity` | activity de envío de template aprobado — para registrar en el worker del plugin |
| `send_template_to_session` | la misma lógica pura (sin decorators Temporal) — testeable sin worker |
| `detect_marketing_opt_out` | detector determinista de pedidos de baja ("NO MÁS"/"baja") con campaña reciente — lo consulta el ingest de chats; cumple la promesa de opt-out del template de campañas |
| `send_whatsapp_message_activity`, `send_typing_indicator_activity` | el texto al cliente y el "escribiendo…" de un turno conversacional (el workflow de ventas V2 los agenda sin importar platform; se registran en el worker) |
| `send_capi_event_activity`, `flush_capi_outbox_activity` | el evento de Meta (Lead / Purchase) del cierre del episodio y el flush del outbox de eventos del turno |
| `ladder_state(..., max_steps=)`, `clamp_max_steps` | la escalera de reactivación (5 toques: +2h/+4h/+8h/+14h/+20h); `max_steps` es el tope de toques del negocio (0..5, `None` = todos) |
| `effective_max_touches`, `set_max_touches`, `read_frequency_state`, `frequency_ceiling`, `FrequencyState` | la **frecuencia del remarketing**: cuántos toques máximo hace el bot, configurable desde el dashboard dentro de un techo de Terraform (ver abajo) |

**Cómo se usa (plugin `marketing`, campañas directas).** El worker registra
`send_whatsapp_template_activity`; el workflow de envío la invoca por
destinatario. El template DEBE existir en
`src/platform/whatsapp/templates/catalog.yaml` y estar aprobado en Meta
(categoría MARKETING para promos — provisioning:
`infra/whatsapp-provisioning`). El costo estimado sale de
`get_current_rate_card()`; la verdad post-facto la trae el webhook `pricing`.

## Oído `standby` (D1.4 · Meta Business Agent)

Cuando MBA controla el hilo, Meta manda el webhook `standby` en vez de
`messages`: lo que escribe el cliente, el eco de lo que MBA envió y los
recibos con `pricing`. El use case `IngestStandby` de `chats` persiste eso al
vault sin Temporal, y para hacerlo con la MISMA contabilidad que un send
propio el kit expone:

```python
from src.sdk.messagingkit import (
    OutboundLogEntry,
    compute_service_window_expiry,
    record_outbound_in_active_episode,
)

# eco de MBA → outbound PENDIENTE de pricing en el episodio activo + last_outbound
record_outbound_in_active_episode(
    metadata,
    OutboundLogEntry(sent_at_ms=ts_ms, wa_message_id=wamid, kind="mba_text",
                     template_name=None, pricing=None, cost_usd_micros=None,
                     rate_card_version=None),
)
# inbound del cliente → reabre la ventana de servicio 24h
metadata["service_window_expires_at_ms"] = compute_service_window_expiry(now_ms)
```

El costo real lo materializa después `IngestDeliveryStatus` con el status
(`standby.statuses[]`, mismo `wa_message_id`), así el gasto de MBA cae en el
`cost_summary` del episodio junto al nuestro. Sin episodio activo,
`record_outbound_in_active_episode` solo estampa `last_outbound`.

**Checks.** `tests/platform/test_messagingkit.py` fija cada re-export a su
implementación de platform (regla de oro del SDK).

## Frecuencia del remarketing (dashboard Agents → Remarketing → Frecuencia)

**Qué soluciona.** El número de toques (5) estaba fijo en el código
(`LADDER_GAPS_MS`). Ahora el operador elige **cuántos** (0 a 5) desde el
dashboard. Solo la cantidad: los huecos entre toques no se tocan.

**Cómo funciona.**

- **Techo**: lo declara Terraform por tenant (`tenants.<t>.lab.remarketing_max_touches`,
  default 5 → SSM `REMARKETING_MAX_TOUCHES`). El dashboard elige dentro del
  techo y nunca lo supera; si el techo baja, gana el menor.
- **Valor guardado**: `_rollout/remarketing_frequency.json` en el vault (escritura
  atómica, con quién y cuándo). Sin nada guardado manda el techo. Un archivo
  ilegible cae al techo: nunca deja el remarketing en 0 por accidente.
- **Aplica desde ya**: lo leen en cada decisión (sin cache) la central de envío
  (`check_reengagement_policy`), el snapshot del ciclo y la etiqueta
  `SIN_RESPUESTA`. Bajar de 5 a 2 deja agotado en el acto a quien ya recibió 3+
  toques (queda `SIN_RESPUESTA` tras su gracia = el hueco del último peldaño
  permitido). Con 0 no sale ningún toque.
- **API**: `GET/PUT /api/chats/remarketing/frequency` (contrato
  `remarketing-frequency@v1`, plugin `chats`) y su cast
  `GET/PUT /api/agents/remarketing/frequency` (plugin `agents_admin`). El PUT
  rechaza con 422 lo que no sea un entero ≥ 0 (`invalid_value`) o pase el techo
  (`above_ceiling`).
- **Pura**: `ladder_state` no lee nada externo; el caller (activity) lee el tope
  y lo pasa como `max_steps`.

**Cómo se usa (plugin).**

```python
from src.sdk.messagingkit import effective_max_touches, ladder_state

max_touches = effective_max_touches(WORKSPACE_VAULT_DIR)
ladder = ladder_state(now_ms, metadata, max_steps=max_touches)
```

**Checks.** `tests/platform/test_reengagement_frequency.py` (el almacén y el
techo), `tests/platform/test_reengagement_ladder.py` (`max_steps`),
`tests/infra/test_remarketing_frequency_terraform.py` (el techo nace en
Terraform) y `infra/terraform/platform/tests/lab_config.tftest.hcl`.
