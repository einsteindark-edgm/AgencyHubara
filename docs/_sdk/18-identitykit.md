# 18 · IdentityKit (quién es el cliente de WhatsApp, PURO)

> Fuente: `src/sdk/identitykit.py` · Check: `tests/platform/test_identitykit.py`
> (identidad + "solo depende del módulo puro `src.platform.whatsapp.user_id`")

## Qué problema soluciona

Desde 2026 un cliente de WhatsApp puede activar un **nombre de usuario**. Si el
negocio no habló con él en los últimos 30 días —justo un lead nuevo de
anuncio—, Meta omite su teléfono en el webhook (`messages[].from`,
`contacts[].wa_id`) y manda solo su id de Meta: `from_user_id` /
`contacts[].user_id`, el *business-scoped user ID* (BSUID), con forma
`CO.9990000000000002` (país ISO, punto, hasta 128 alfanuméricos).

El parser exigía `from` y esos clientes nunca llegaban al bot: Halloween 07–08
oct 2026, Meta contó 8 conversaciones y entraron 5 (las 3 restantes eran estos
clientes; ledger de inbound).

## Cómo funciona

| Concepto | Valor |
|---|---|
| Dirección del cliente | teléfono (`573001234567`) o BSUID **sin el punto** (`CO9990000000000002`) |
| Conversación | `wa_<dirección>` — el punto no se admite en un directorio del vault (`is_vault_session_id`) |
| Envío | `_post_json` (platform) traduce: dirección-BSUID → `"recipient": "CO.9990…"`; teléfono → `"to"` |
| Mismo cliente, con y sin teléfono | `<vault>/_identity/whatsapp_user_ids/<CC><id>` fija su conversación (el primero gana) |
| Mismo cliente que empezó sin teléfono | `<vault>/_identity/whatsapp_phones/<teléfono>` → su conversación `wa_<CC><id>`: lo que después llegue solo con `from` sigue ahí (premortem 2026-10-09). No se escribe si `wa_<teléfono>` ya existe: esa conversación es la del teléfono |

La dirección se distingue de un teléfono porque empieza con 2 letras, y se
reconstruye exacta (el país siempre son 2 letras).

## Superficie

| Símbolo | Rol |
|---|---|
| `address_from_user_id` | `CO.9990…` → `CO9990…`; `None` si no es el BSUID de quien escribe (id "padre" `CC.ENT.…`, traversal, > 128) |
| `is_user_id_address` | ¿la dirección es un BSUID (y no un teléfono)? — para no mostrarlo ni usarlo como teléfono |
| `is_customer_session_id` | `wa_<teléfono 8-15 dígitos>` o `wa_<BSUID sin punto>`: la guarda de las rutas que reciben un `session_id` de un cliente real |

## Por qué es un kit aparte

El parser del webhook lo necesita y el contrato R-DIP #8 (`parsers-pure`, en
`.importlinter`) le prohíbe importar I/O. `messagingkit` trae las activities
de envío (`httpx`, `temporalio`). Mismo patrón que `textkit`.

## Límites de Meta

- Texto libre a un BSUID solo dentro de la ventana de 24 h (131047); fuera, plantilla.
- Las plantillas de autenticación no se pueden enviar a un BSUID (131062).
