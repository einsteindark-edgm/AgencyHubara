# Runbook — WhatsApp Quality Rating Incident Response

> **Audience:** operador AgencyHubara + on-call.
> **Triggered when:** WhatsApp Business Manager reporta el quality rating
> del phone number en **YELLOW (Medium)** o **RED (Low)**. Notificación
> llega por email Meta + Slack alert (configurar en Sprint 0 F0.5).
> **HU related:** HU-WA24H-001 (cost tracking + cadence) — el monitoreo
> del rating informa decisiones de pausa de cadencia automática.

---

## §1 Por qué importa el quality rating

Meta calcula el quality rating en base a:
- **User feedback negativo**: bloqueos, reportes "spam", "no me interesa".
- **Read rate**: % de mensajes outbound que el cliente abre.
- **Response rate**: % de mensajes que el cliente responde.
- **Failed delivery rate**: errores 4xx/5xx persistentes.

Consecuencias por tier (resumen, ver §13 del refinement HU-WA24H-001):

| Tier | Daily unique recipients (outbound fuera de service window) |
|---|---|
| GREEN (HIGH) | escala automático según trayectoria (hasta Tier 4 = unlimited) |
| YELLOW (MEDIUM) | bloqueado para subir de tier; sigue operando al cap actual |
| RED (LOW) | bloqueado para subir; alto riesgo de número phone suspendido si no mejora |
| FLAGGED | suspendido temporal — investigación de Meta |

**Tiempo de recovery típico:** YELLOW → GREEN en 7 días con corrección sostenida.
RED → YELLOW en 2-3 días si la causa root cede. FLAGGED puede ser permanente.

---

## §2 Detección — antes de que sea crítico

### §2.1 Alertas a configurar (F0.5)

- **Email Meta**: en Business Manager → Phone Numbers → seleccionar número → "Notifications" → activar alerts para quality rating changes.
- **Slack alert** (cuando exista el cron del Sprint 4): `quality_rating_polling_activity` corre cada 1h, postea a `#wa-alerts` si el rating baja.
- **Dashboard**: panel con histograma de rating diario por phone number.

### §2.2 Métricas leading (preocuparse ANTES del downgrade)

| Métrica | Threshold de alerta |
|---|---|
| Read rate semanal | < 50% (target > 70%) |
| Response rate a primer turno del agente | < 30% (target > 50%) |
| Ratio `error 131049` (per-user marketing cap) | > 5% del volumen marketing |
| Ratio `error 131008` (template not approved) | > 0 (debería ser 0 en steady state) |
| Frecuencia de marketing templates por user | > 1 cada 48h |

Si alguna sostenida 3 días → revisar templates + cadencia ANTES de que Meta downgrade.

---

## §3 Playbook por tier

### §3.1 YELLOW (Medium) — primer signal

**T+0 (cuando llega la alerta):**

1. **Confirmar el downgrade** en Business Manager (no confiar solo en email — capaz es lag).
2. **Snapshot del estado**: tomar screenshots del rating, messaging tier, daily volumen, error rate breakdown.
3. **Pausar cadencia automática** (cuando exista en Sprint 4) emitiendo evento `QualityRatingDroppedEvent` que el dispatcher rutea a signal `pause_cadence` en TODOS los workflows activos del worker remarketing.
4. **NO suspender el agente sales** — sales opera dentro de ventana 24h donde el rating tiene menor impacto. Pausar sales sería tirar dinero.

**T+30min — diagnóstico:**

5. Revisar `wa_delivery_status` events de los últimos 7 días:
   - `failed_delivery_rate` por categoría. Si marketing dominates → demasiada agresividad en cadencia.
   - `error_code` distribution. `131049` > 5% → segmentación está mal.
   - `pricing.type` mix. Si `regular/marketing` domina sobre `free_customer_service` → estamos operando demasiado fuera de ventana.
6. Revisar `episodes` con `closing_tag = RECHAZO` de los últimos 14 días:
   - ¿Patrón en los templates que se mandaron? ¿Algún copy específico está generando rechazo?
   - ¿Algún segmento (campaña / fuente) tiene >30% rejection rate? Pausarlo manualmente.
7. Sample 10 conversaciones reales con outbound recientes — leerlas. ¿UX del agente está bien? ¿Spam-y? ¿Repetitivo?

**T+2h — acción correctiva:**

8. **Pausar marketing templates problemáticos** en catalog YAML (`triggers_when_window_expiring: false` ya está enforced para marketing — para extra protección, comentar el entry entero).
9. **Bajar cadencia volume**: aumentar `delay_ms_from_start` de cada attempt N en `cadences.yaml` para reducir frecuencia (e.g., default 21d → 30d).
10. **Re-train del agente sales** si las conversaciones muestran issues — actualizar `IDENTITY.md` / `SOUL.md` / `sales_script` skill.
11. **Hablar con clientes recientes que dieron RECHAZO** desde una sesión humana — entender el qué.

**T+24h — verificación:**

12. Verificar rating en Business Manager. Si sigue YELLOW pero ratios mejoraron, esperar 48h más.
13. Si rating bajó a RED → escalar a §3.2.
14. Si rating volvió a GREEN → resume gradual de cadencia con `OperatorResumedCadenceEvent` (NO en bloque — empezar con 1 segmento piloto).

### §3.2 RED (Low) — situación crítica

**T+0:**

1. **Pausa total de outbound business-initiated** (utility + marketing fuera de ventana). Solo el agente sales puede seguir respondiendo a inbound.
2. **Activar incident war room** en Slack `#wa-incident`.
3. **Postmortem inmediato sobre qué cambió las últimas 72h**:
   - ¿Hubo deploy nuevo de templates? Rollback.
   - ¿Hubo cambio en sales/remarketing workflow? Rollback.
   - ¿Hubo cambio de segmentación? Rollback.
   - ¿Hubo onboarding masivo de clientes? Limitar.
4. **Audit del catalog YAML**: ¿algún template fue mis-categorizado por Meta (utility → marketing)? Cross-ref `pricing.category` capturada por webhook vs declarada en catalog. Si hay drift, re-submit templates con copy ajustado.

**T+2h:**

5. **Reducir messaging volume al mínimo de tier actual** — no enviar nada que no sea estrictamente necesario.
6. **Outreach humano** a top 10 conversaciones más recientes que dieron rechazo — apologize + offer.
7. **Posiblemente**: pausar la cadencia indefinidamente hasta que se entienda root cause.

**T+24h:**

8. Si no hay improvement → considerar registrar un phone number alternativo en backup mientras el principal se recupera. Esto es nuclear — solo si hay riesgo de suspension total.

### §3.3 FLAGGED — número suspendido

**T+0:**

1. **STOP TODO outbound** en ese número. La API devuelve `error 131056` u otro 4xx para cada send.
2. **Submit appeal** vía Business Manager → Phone Numbers → "Request review". Meta responde típicamente en 24-72h.
3. **Comunicar a clientes activos** desde número alternativo (si existe) o canal alternativo (email).
4. **Documentar todo** para el appeal: screenshots, logs, ejemplos de conversación, métricas de las últimas 4 semanas.
5. **Si appeal rechazada** → migrar permanentemente a phone number de backup. Pérdida significativa.

---

## §4 Prevención (post-incident)

Después de cualquier incident YELLOW/RED, antes de resumir operaciones:

1. **Postmortem doc** en `.hubara/postmortems/YYYY-MM-DD-quality-rating-drop.md`:
   - Trigger inicial: qué cambió.
   - Timeline: alerta, decisiones, acciones, recovery.
   - Métricas: rating histórico, error codes, response rates, cost impact.
   - Action items: cambios de cadencia, copy de templates, alertas a agregar.
2. **Actualizar este runbook** con lo aprendido.
3. **Adjust thresholds** de §2.2 si los actuales no detectaron el incident a tiempo.
4. **Compartir con el equipo** — un incident NO es secreto, es learning material.

---

## §5 Templates de comunicación durante incident

### §5.1 Mensaje interno (Slack `#wa-alerts`)

```
🔴 WhatsApp Quality Rating downgrade detectado
Phone: <wa_phone_id>
Rating: <prev_tier> → <new_tier>
Detectado: <timestamp>
Acciones tomadas: <list>
Owner del incident: <@operator>
War room: #wa-incident
```

### §5.2 Mensaje a stakeholder ejecutivo (RED)

```
Heads up — el quality rating de nuestro phone WhatsApp principal bajó a
RED el <fecha>. Pausamos outbound business-initiated (marketing /
utility fuera de ventana). Sales sigue operando normal. Tiempo de
recovery estimado: 48-72h con corrección sostenida. Próximo update en
<6h>. Owner: <name>.
```

### §5.3 Mensaje a cliente desde sesión humana post-RECHAZO

```
Hola {{name}}, soy {{op_name}} del equipo de Hubara. Vi que recibiste
varios mensajes nuestros recientemente que no te interesaron. Lamento
si fueron demasiados — quería preguntarte directamente si hay algo
específico que estés buscando, o si preferís que pausemos los mensajes
por un tiempo. Tu feedback nos sirve mucho para mejorar.
```

(Solo enviar este si el cliente respondió en últimos 7 días — la ventana
de servicio debe estar abierta. NO mandar marketing template tras un
RECHAZO reciente: empeoraría el rating.)

---

## §6 Referencias

- HU-WA24H-001 refinement §12 (Lead Response Management Study) — fundamentación de por qué la cadencia debe ser conservadora.
- HU-WA24H-001 refinement §9 (Observabilidad) — qué métricas mantener para detección leading.
- Meta docs — Quality Rating + Messaging Limits: `developers.facebook.com/docs/whatsapp/messaging-limits/`
- Chatarmin — Messaging Limits 2026: práctico para entender behavior de Meta en 2026.
