# MBA · F0 — resultados de los experimentos (D3.2)

> Planilla viva. Cada experimento tiene hipótesis, cómo se corre, criterio y la
> evidencia real (ecos, wamids, ids de sesión, capturas). Nada acá enciende MBA
> para clientes: todo corre con `ai_audience=ALLOWLISTED_ONLY` y la lista cerrada
> `MBA_CUSTOMER_ALLOWLIST` (Terraform). Roadmap: `MBA_PRODUCTION_ROADMAP.md` §D3.2.

## 0. Estado de partida (2026-09-11)

| Prerrequisito | Estado | Evidencia |
|---|---|---|
| Número onboardeado en Meta (`entity_id` = phone_number_id) | ✅ | `agent_onboarding` 201 (2026-09-10), `agent_eligibility → is_eligible: true` |
| Agente en Meta: `rollout.enabled=false`, `ai_audience=ALLOWLISTED_ONLY`, followup off | ✅ | `mba-status` (CLI) tras el lock |
| Webhook de la app: `messages`, `standby`, `messaging_handovers` | ✅ | `POST /{app}/subscriptions` 200 |
| `agent.yaml` sin problems ni placeholders en prod | ✅ | `GET /api/mba/agents/sales/config` → `problems: []`, `entity_id` resuelto |
| Primer sync de la config a Meta (D2.2) | ⏳ | ver §1 |
| Teléfono de prueba en la allowlist de Meta (D2.3) | ⏳ | +573125671604 ∈ `MBA_CUSTOMER_ALLOWLIST` ✅; en Meta: pendiente |
| Método de pago en el Billing Hub (WhatsApp Manager lo cuenta como tarea obligatoria) | ⏳ | decisión del operador |
| Oído `standby` encendido en Hubara (`MBA_STANDBY_ENABLED=1`, Terraform) | ⏳ | requerido antes de encender el rollout: sin él Hubara no guarda el historial de lo que MBA conversa (D1.4) |
| Rollout encendido para la lista cerrada (D2.3, desde la tab, dos pasos) | ⏳ | decisión del operador |

## 1. Bloqueo actual: connectors "not available for this entity yet"

`GET /{entity_id}/agent_connectors` → 400 `Connectors are not available for this
entity yet. Finish onboarding the agent for this entity, then retry.` El resto
de la config (settings, skills, faq, business_info, ui-skills, allowlist) sí
responde. Impacto: `sync/plan` y `rollout` devuelven 503 `remote_unavailable`
(`kind=rejected`) porque leen los connectors. Ver §"Diagnóstico" abajo.

## 2. Experimentos

| # | Pregunta | Hipótesis | Cómo | Criterio | Resultado | Evidencia |
|---|---|---|---|---|---|---|
| 1 | ¿MBA lee el catálogo del WABA? | Sí: cita productos del catálogo conectado al número | `agent_test` "¿qué velas tienen?" sin connector activo | Cita productos reales sin inventar | ✅ 2026-09-11 | Sin skills ni FAQ sincronizadas (`GET skills/faq → []`), MBA listó **Velón Pinguino $38.000**, **Velón Ciervo $39.000** y **Cubo de corazón $22.000**; los 3 existen en el catálogo Meta del WABA con esos precios exactos (`prod_01KYQYAPD16FJY3F0PPKZYY5NC`, `prod_01KYQYCDRCDARX5YVZPGAE94QM`, `prod_01M0BD583VD8SN20AR9T10CGJE`). 2º turno: repitió el precio correcto del Ciervo y **no inventó** el tiempo de envío a Medellín (ofreció conectar con el equipo). `message_id pfbid0fYbqXQi6z…1789145011031`. Observación: tutea, sin voseo; usa viñetas markdown `*   *Nombre*` (ver exp. 5). |
| 2 | ¿Puebla un carrusel desde el connector? | Sí, con `present-products` + `search_products` | conversación real desde el teléfono de prueba | Llega carrusel con títulos/precios del snapshot | ⏳ | |
| 3 | ¿Cómo agrupa ráfagas? | Responde una vez por ráfaga | 3 mensajes en 5 s desde el teléfono de prueba | Contar ecos en `standby`: 1 o 3 respuestas; latencia inbound→echo | ⏳ | |
| 4 | ¿Una reacción de Hubara toma el hilo? | No debería (no es un mensaje) | `react_to_message` mientras MBA controla | `messaging_handovers` cambia o no | ⏳ | |
| 5 | Tono, voseo, invención de precios | 0 voseos, 0 precios inventados | 10 conversaciones guionadas en `agent_test` (+ `agent_eval` si hace falta) | 0 voseos, 0 precios inventados | ⏳ | |
| 6 | Handoff nativo + `escalate_to_human` | El chat aparece en la bandeja con tag HUMANO y MBA calla | "quiero hablar con alguien" | tag HUMANO en inbox; MBA no responde más | ⏳ | |
| 7 | Orden completa | Orden en Medusa vía `register_order` | flujo hasta `register_order` | Orden en Medusa, tag CONFIRMADO_PAGO_PENDIENTE, comprobante al humano | ⏳ | |
| 8 | Release | MBA vuelve a responder | comprobante verificado + `agent_event` + release | MBA responde de nuevo en el hilo | ⏳ | |
| 9 | Contexto entre episodios | Retoma el hilo viejo si nadie lo cierra | cerrar episodio (pedido registrado) y escribir 2 días después | ¿saluda como nueva o retoma? ¿`agent_event` de cierre silencioso? ¿costo por turno crece con el historial? | ⏳ | |

## 3. Cómo se recoge la evidencia

- **Ecos y statuses de MBA** (exp. 3, 6, 8): llegan por el campo `standby` del webhook → con `MBA_STANDBY_ENABLED=1` el plugin `chats` los persiste en la sesión del cliente (vault `sessions/<key>`); `wamid` y `timestamp` de cada eco salen de ahí. Sin el flag, se ven solo en `docker logs hubara-prod-api-1 | grep standby`.
- **Quién tiene el hilo** (exp. 4, 6, 8): `GET /api/mba/sessions/{key}/control` (D1.5/D1.6) y los `messaging_handovers` en los logs.
- **Connector calls** (exp. 2, 7): logs `[mba.connector]` con `session_key`, latencia y resultado.
- **Simulador** (exp. 1, 5): tab "Agent test" del canvas (D2.4) o `POST /api/mba/agents/sales/test`; no factura ni toca hilos reales.
- **Costo** (exp. 9): tokens por turno según Billing Hub / respuesta de `agent_test` cuando la exponga; hoy MBA cobra $2 por 1M tokens.

## 4. Diagnóstico y bitácora

**2026-09-11 · connectors gateados por Meta.** Tras el `agent_onboarding` (201, 2026-09-10 ~20:55 Bogotá) todos los endpoints de configuración responden salvo `agent_connectors`: 400 `Connectors not available for this entity` / "Finish onboarding the agent for this entity, then retry". Verificado 16 h después: sigue igual. La doc oficial (troubleshooting, quickstart, connectors) no describe este estado; el OpenAPI de onboarding dice que el onboarding "schedules async jobs for data preparation". Hipótesis, en orden: (1) la configuración del número en **WhatsApp Manager → pestaña Meta Business Agent** (`https://business.facebook.com/latest/whatsapp_manager/business_ai?business_id=<BUSINESS_ID>&asset_id=<WABA_ID>`) es el "onboarding" que falta del lado UI (la doc la lista como Paso 1 y ahí se aceptan los Términos); (2) los jobs asíncronos de Meta aún no habilitaron connectors para la entidad. Acción: el operador abre esa pestaña y completa lo que pida; después `mba-status` + `GET agent_connectors`. Efecto hoy: `sync/plan` y `rollout` devuelven 503 `remote_unavailable` (`kind=rejected`) porque leen los connectors; el simulador y el resto de la config funcionan.

**2026-09-11 · verificación contra la referencia de connectors.** La página `reference/configure/connectors` (leída en su versión markdown, `.md`) y su OpenAPI v2.0.0 dicen: `POST /{entity_id}/agent_connectors` con `name`, `description`, `base_url`, `auth_type` obligatorios; `auth_config.api_key.headers[{field_name, value, prefix?}]` para `API_KEY`; `connector_protocol` HTTP por defecto; `requires_certificate` opcional. Nuestro body (sección `connector` de `GET /api/mba/agents/sales/config`) cumple campo por campo, y las tools mandan `name`, `description`, `request_definition{method, path, …}`, `user_auth_required` (los 4 obligatorios). Autorización: connectors, skills, settings y tools exigen lo MISMO (`whatsapp_business_messaging` o la capability enterprise 3p) → no es un problema de permisos. La referencia no documenta ningún estado "not available" ni prerrequisito: el gate es de estado de la entidad en Meta. En WhatsApp Manager la fila "Conectores" solo enlaza a esa referencia (no provisiona nada) y "Herramientas" queda gris hasta que exista un connector. **Decisión (PR #272):** el sync tolera el gate (connector + tools quedan `skip: connectors_unavailable`, el resto se sincroniza; el panel de rollout muestra el check fallido, fail-closed) para completar las tareas "Preguntas frecuentes" y "Aptitudes" de la página y comprobar si con eso Meta considera el onboarding "terminado".

**2026-09-11 · "Método de pago" no es opcional en WhatsApp Manager.** La página cuenta 5 tareas obligatorias: información del negocio, preguntas frecuentes, aptitudes, método de pago y permitir que responda. La doc: "los mensajes no se envían a menos que tu cuenta tenga un método de pago". Corrige la suposición del roadmap (D3.1: "no requerido con ALLOWLISTED_ONLY"): antes de encender el rollout para la lista cerrada hace falta un método de pago en el Billing Hub (decisión del operador).

**2026-09-11 · business_info ya viene poblado.** Meta pre-llenó `business_description` con la descripción del perfil de WhatsApp Business ("🕯️decorativas y aromáticas que armonizan…"); el resto de campos vacíos. El sync (D2.2) lo va a reemplazar por el bloque de `agent.yaml` (business_info es bloque completo): esperado, no es drift.
