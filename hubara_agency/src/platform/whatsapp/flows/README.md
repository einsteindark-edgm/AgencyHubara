# WhatsApp Flows — Hubara

**La definición canónica de los Flows NO vive acá.** Vive en
`hubara_agency/docs/whatsapp_flows/*.json`, y se crea/publica con el CLI de
provisioning (`infra/whatsapp-provisioning/whatsapp_provision.py`, paso
`flows`, definiciones en `infra/whatsapp-provisioning/definitions/flows.json`).

| Flow (nombre en Meta) | JSON canónico | SSM key |
|---|---|---|
| Hubara — Datos de envío v2 | `docs/whatsapp_flows/shipping_v2.json` | `META_FLOW_ID_SHIPPING` |

## Cómo funciona en runtime

1. `RequestShippingDetailsTool` (plugin chats/sales) encola un intent
   `shipping_flow` con `flow_action_data` (total, resumen, `payment_options`
   dinámicas con título + descripción).
2. `flush_pending_ui_intents_activity` resuelve el `flow_id` desde env
   `META_FLOW_ID_SHIPPING` y hace `send_flow`. Sin env (o si Meta rechaza),
   cae a la recolección conversacional por texto plano.
3. El cliente completa el form → webhook `interactive.nfm_reply` →
   `translate.py` lo proyecta como `k=v` al prompt del LLM.

## Cambiar un Flow ya publicado

* **Solo opciones de pago** (títulos/descripciones): editar
  `RequestShippingDetailsTool` — el RadioButtonsGroup bindea
  `${data.payment_options}`, no hace falta republicar.
* **Campos del formulario**: editar el JSON canónico, subirlo como flow
  NUEVO (rename `vN+1` en `definitions/flows.json` → el provisioning lo
  crea, publica y resuelve el nuevo `flow_id` al SSM key) o vía Flow
  Builder manual (runbook: `docs/META_CATALOG_SETUP.md` §Fase 13).
  Tras cambiar el flow_id: push a SSM + recrear el worker chats-sales.
