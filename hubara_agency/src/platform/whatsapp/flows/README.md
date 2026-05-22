# WhatsApp Flows — Hubara

Cada `*.flow.json` define un formulario nativo dentro de WhatsApp que el
agente puede mandar al cliente vía `send_flow` (A.9 del PLAN.md HU-002).

## Flows definidos

| Archivo | Propósito | Pantallas | Screen inicial |
|---|---|---|---|
| `shipping_details.flow.json` | Recolección de datos de envío | SHIPPING_DETAILS → CONFIRM | SHIPPING_DETAILS |

## Lifecycle de publicación

1. **Diseñar**: editar el `*.flow.json` (versionar en git).
2. **Validar** localmente con Meta Flow Builder o `playground.whatsapp.com/flows`.
3. **Publicar** via Graph API (`POST /v23.0/{waba_id}/flows`):
   ```bash
   curl -X POST https://graph.facebook.com/v23.0/{waba_id}/flows \
     -H "Authorization: Bearer ${WHATSAPP_ACCESS_TOKEN}" \
     -F "name=shipping_details_v1" \
     -F "categories=[\"OTHER\"]" \
     -F "file=@shipping_details.flow.json"
   ```
4. **Approve**: Meta revisa el JSON. 1-3 días.
5. **Configurar data endpoint**: el Flow llama al endpoint
   `POST /api/whatsapp/flows/shipping/data` para resolver `cities` dinámico
   y validar `show_cash_on_delivery`.
6. **Anotar el `flow_id`** que devuelve Meta en `agents_admin` config
   (`shipping_flow_id`).
7. **Actualizar `RequestShippingDetailsTool`** para usar el `flow_id` real
   (hoy tiene placeholder `FLOW_ID_SHIPPING_PLACEHOLDER`).

## Versionado

Cuando cambies un Flow ya publicado:
- Si el cambio es compatible (cambiar texto, agregar campo opcional):
  re-publicás la misma `name` con nueva version.
- Si rompe schema (sacar campo required, cambiar tipo): publicás como
  `shipping_details_v2` y actualizás `RequestShippingDetailsTool` para
  apuntar al nuevo id.

## Encryption (data endpoint)

El data endpoint debe:
- Validar `X-Hub-Signature-256` con `app_secret`.
- Devolver respuestas cifradas RSA. Generar par de claves, registrar la
  pública en `agents_admin.shipping_flow_public_key`. Privada en secrets
  manager.

Ver Meta docs: https://developers.facebook.com/docs/whatsapp/flows/reference/flowsencryption

## Testing

```bash
# Modo "draft" — envía el Flow en modo preview sin pasar review
flow_action_payload.mode = "draft"
```

Los QA en sandbox sirven para validar layout antes de submit a review.
