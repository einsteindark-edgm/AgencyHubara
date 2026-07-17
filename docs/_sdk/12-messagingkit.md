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

**Cómo se usa (plugin `marketing`, campañas directas).** El worker registra
`send_whatsapp_template_activity`; el workflow de envío la invoca por
destinatario. El template DEBE existir en
`src/platform/whatsapp/templates/catalog.yaml` y estar aprobado en Meta
(categoría MARKETING para promos — provisioning:
`infra/whatsapp-provisioning`). El costo estimado sale de
`get_current_rate_card()`; la verdad post-facto la trae el webhook `pricing`.

**Checks.** `tests/platform/test_messagingkit.py` fija cada re-export a su
implementación de platform (regla de oro del SDK).
