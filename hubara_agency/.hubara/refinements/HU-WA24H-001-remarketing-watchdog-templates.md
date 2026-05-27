# HU-WA24H-001 — Watchdog 24h + Templates aprobados para Remarketing

> **Status:** technical refinement (sin código todavía).
> **Plugin:** `chats` (single-plugin).
> **Capabilities afectadas:** `messaging`, `plugins/chats`, `agents/remarketing-worker` (bootstrap incremental).
> **Author:** refinement producido por Claude el 2026-05-26.

---

## §0 Plugin classification

| Campo | Valor |
|---|---|
| `mode` | `single_plugin` |
| `target_plugin` | `chats` |
| `affects_layers` | backend (workflows + activities + platform/whatsapp + state) |
| `frontend_affected` | NO (este HU es backend-only; UI de costos / dashboard llega en HU separada) |
| `capability_specs_to_touch` | `messaging/spec.md` (nuevos requirements), `plugins/chats/spec.md` (extensión), bootstrap `agents/remarketing-worker/spec.md` |

### §0.5 Contexto del lanzamiento (crítico para secuencia)

| Dimensión | Estado actual |
|---|---|
| Tenancy | **Single tenant** — todos los clientes finales son del mismo operador (AgencyHubara). |
| Estado de producción | **Pre-launch** — sales worker se está finalizando. NO hay conversaciones activas todavía. |
| Infra WhatsApp | **Cloud API directa de Meta** (no BSP). Implica: approval de templates vía Business Manager directamente, acceso a beta features (Max Price, Currency Migration) vía formularios Meta, endpoint `/v18.0/{phone_id}/messages` directo. |
| País / moneda | **Colombia** — WABA puede facturarse en USD o COP (decisión separada). |

**Implicaciones para este HU:**
- ❌ NO necesitamos: per-tenant feature flags, broadcast pause cross-tenant, A/B testing de schedules en MVP, multi-currency rate card.
- ✅ Sí necesitamos (y MÁS que un sistema en producción): plan de incident response si quality rating cae en la primera semana, templates redactados por humano (la primera impresión define rating de por vida), Fase 0 operacional (§7.0) ANTES de cualquier código.
- 🕓 Cadencia (§4.9) **NO se construye hasta tener data real** post-launch (4-6 semanas mínimo). El diseño queda especificado pero los Sprints 4-5 se ejecutan recién después de medir el comportamiento del watchdog + tráfico real.

---

## §1 Contexto y motivación

El plugin `chats.remarketing` envía hoy texto libre generado por LLM. La API de WhatsApp Cloud **bloquea cualquier free-form fuera de la ventana de 24h** con error `#131047` ("Re-engagement message"). Sin template message, el agente no puede iniciar conversación con un cliente inactivo — los deals que se enfrían se pierden silenciosamente (el mensaje del agente no llega y se mete a la conversación local del bot un mensaje "fantasma" que nunca el cliente verá).

Adicionalmente, en pricing **per-message** (efectivo jul 2025), un **utility template** dentro de la ventana cuesta **$0** y fuera cuesta **$0.0008**, vs **marketing** que cuesta **$0.0125 siempre**. Mantener la ventana abierta con utility templates legítimos antes de que expire es la diferencia entre conversación gratis vs marketing pagado.

**Objetivo del HU:**
1. Persistir el timestamp del último inbound del cliente para poder computar el cierre de ventana.
2. Construir un **watchdog durable per-conversación** (workflow Temporal) que se dispara 30min antes del cierre de ventana con un deal abierto, dispara un utility template legítimo y se cancela si el cliente responde.
3. Habilitar envío de **template messages aprobados** desde activities — tanto para el watchdog (auto-disparo) como para el agente LLM (decisión consciente cuando está fuera de ventana).
4. Capturar el `pricing` object de los webhooks de delivery status para medir costo real y % de mensajes gratis vs cobrados.
5. **Cost tracking per-mensaje + agregado per-episode** — cruzar el `pricing` object capturado en (4) contra un rate card local (Colombia 2026Q2) para computar costo en cents USD por mensaje y por episodio, persistido para queries de "cuánto costó este cliente / este episodio / esta campaña".
6. **Cadencia adaptativa de re-engagement** (5 attempts en 21 días post-watchdog) basada en el Lead Response Management Study (Oldroyd, McElheran & Elkington — ver §12), con escalation gradual de category, eligibility checks y override por LLM/operador.

**Lo que NO incluye este HU** (HUs separadas):
- Integración con WABA Currency Migration APIs (cambio de billing currency a COP).
- Aplicación al Limited Beta de Max Price.
- Dashboard de costos / quality rating por tenant.
- Cron de quality rating polling (consumido por broadcast pause de cadencia) — HU separada.

---

## §2 Hallazgos del mapeo del stack actual

| # | Hallazgo | Implicación |
|---|---|---|
| H1 | `metadata.json` guarda `last_inbound_message_id` pero **NO `last_inbound_at_ms`** | Hay que agregar el campo en `IngestInboundMessage.execute()` (línea ~190). Ya existe `_now_ms()` en el mismo módulo. |
| H2 | `session_history/store.py::append_user_event` NO persiste timestamp al evento user; sólo el assistant lo guarda | Hay que agregar `timestamp` al user event para auditoría. Hoy el único timestamp accionable vive en `episodes[-1].started_at_ms` (primer inbound del episodio, no el último). |
| H3 | `outbound.py` (20KB) tiene builders para image/audio/video/document/interactive/location/contacts/reaction — **NO tiene `build_template`** | Hay que agregar el builder. Pattern simétrico a `build_image`. |
| H4 | `whatsapp/activities.py` tiene `send_whatsapp_message_activity` (texto) y `send_typing_indicator_activity` — **NO tiene `send_template_message_activity`** | Hay que agregar la activity con su retry policy. |
| H5 | El sistema **YA tiene scheduling de remarketing**: `schedule_remarketing_workflow_activity` programa un workflow con `start_delay=N segundos` (vía dispatcher manifest declarativo, ADR-2026-05-20). El sales workflow lo dispara cuando `decide_ghosting_action` devuelve `schedule_remarketing` después de 2+ ghostings. | Reusar este patrón: el watchdog es otro workflow scheduled, pero con `start_delay = (service_window_expires_at - 30min) - now`. Cancelable. |
| H6 | El dispatcher Level-3 (orchestration declarativa) está implementado. Workflows emiten `CompletionEvent`s, el dispatcher consulta el manifest y rutea por `via=start_workflow / signal / ensure_running`. | El watchdog se dispara como reacción a un evento `ServiceWindowOpening` (cuando se persiste el inbound) y se cancela por un evento `CustomerReplied`. Encaja perfecto. |
| H7 | El dispatcher existente captura webhooks `messages` (inbound). Falta capturar `message_status` (delivery + pricing). | Hay que agregar handler para `statuses[]` del webhook payload Meta. |
| H8 | `flush_pending_ui_intents_activity` ya existe — lee `metadata.json[pending_ui_intents]` y dispatch a `send_*` después del texto del LLM | El template send puede integrarse al mismo patrón: el LLM emite un `ui_intent.kind = "template_message"` con `name + variables`, el flush lo dispatch via `send_template_message_activity`. |
| H9 | Tests architecture protegidos (R-DIP): `tests/architecture/**`, `tests/plugins/test_premortem_invariants.py`, `.importlinter`, spinal files de `platform/` | Hay que asegurar que los nuevos eventos están en `platform/constants.py` (si son cross-plugin) o en `chats/shared/contracts/events.py`. Las activities nuevas en `platform/whatsapp/activities.py` (NO crear módulos paralelos). |
| H10 | Existe `episode_lifecycle.py::EPISODE_TIMEOUT_MS = 14d`. Episodios cierran lazy con TAG `TIMEOUT` cuando un inbound nuevo llega tras 14 días. | El watchdog 24h-window NO reemplaza este timeout — son ortogonales. Watchdog opera dentro del episodio activo; el TIMEOUT cierra el episodio si nada pasó en 14 días. |

---

## §3 Modelo de datos — extensión a `metadata.json`

### §3.1 Campos nuevos

```json
{
  "// existing fields //": "phone_number_id, last_inbound_message_id, active_route, tag, motivo, episodes[], origin, pending_ui_intents, ...",

  "last_inbound_at_ms": 1716700000000,
  "service_window_expires_at_ms": 1716786400000,

  "ctwa_window_expires_at_ms": null,

  "last_outbound": {
    "wa_message_id": "wamid.HBg...",
    "sent_at_ms": 1716700100000,
    "kind": "text" | "template",
    "template_name": null | "quote_ready_utility_v1",
    "pricing": null | {
      "billable": true,
      "pricing_type": "regular" | "free_customer_service" | "free_entry_point",
      "category": "marketing" | "utility" | "authentication" | "service"
    }
  },

  "watchdog": {
    "workflow_id": null | "watchdog-wa_+57300...-ep_003",
    "scheduled_for_ms": null | 1716784600000,
    "fired_at_ms": null | 1716784600000,
    "cancelled_at_ms": null,
    "reason_cancelled": null | "customer_replied" | "deal_closed" | "episode_closed"
  },

  "marketing_msgs_sent_24h": {
    "count": 0,
    "window_starts_at_ms": 1716700000000
  },

  "remarketing_cadence": {
    "episode_id": "ep_003",
    "started_at_ms": 1716700000000,
    "attempts": [
      {
        "attempt_n": 1,
        "scheduled_at_ms": 1716784600000,
        "fired_at_ms": 1716784650000,
        "wa_message_id": "wamid.HBg...",
        "template_name": "quote_ready_utility_v1",
        "category": "utility",
        "pricing_type": "free_customer_service",
        "outcome": "delivered" | "read" | "responded" | "failed" | "pending"
      }
    ],
    "next_attempt_n": 2,
    "next_attempt_fire_at_ms": 1716872400000,
    "next_attempt_workflow_id": "cadence-wa_+57300...-ep_003-2",
    "stopped": false,
    "stop_reason": null | "max_attempts" | "customer_replied" | "episode_closed" | "marketing_cap_hit" | "quality_rating_drop" | "operator_paused"
  }
}
```

### §3.1.b Extensión de `episodes[*]` con cost tracking

El array `metadata.episodes[]` ya existe (ver `src/plugins/chats/agent/sales/use_cases/episode_lifecycle.py`). Cada episodio se extiende con dos campos nuevos: el **log de outbound del episodio** + un **agregado precomputado**.

```json
"episodes": [
  {
    "episode_id": "ep_003",
    "started_at_ms": 1716700000000,
    "started_inbound_message_id": "wamid....",
    "closed_at_ms": null,
    "closing_tag": null,
    "closing_motivo": null,
    "order_id": null,
    "referral_snapshot": {...},
    "msgs_count_at_start": 0,
    "msgs_count_at_close": null,

    "outbound_messages": [
      {
        "sent_at_ms": 1716700100000,
        "wa_message_id": "wamid.HBg...",
        "kind": "text" | "template",
        "template_name": null | "quote_ready_utility_v1",
        "pricing": {
          "billable": true,
          "pricing_type": "regular" | "free_customer_service" | "free_entry_point",
          "category": "marketing" | "utility" | "authentication" | "service"
        } | null,
        "cost_cents_usd": null | 125,
        "rate_card_version": "co_2026q2_v1" | null
      }
    ],

    "cost_summary": {
      "total_cents_usd": 256,
      "messages_count": 16,
      "messages_billable_count": 4,
      "messages_free_count": 12,
      "by_category": {
        "marketing": {"count": 2, "cents_usd": 250},
        "utility":   {"count": 14, "cents_usd": 6},
        "authentication": {"count": 0, "cents_usd": 0},
        "service":   {"count": 0, "cents_usd": 0}
      },
      "by_pricing_type": {
        "regular":               {"count": 4, "cents_usd": 256},
        "free_customer_service": {"count": 12, "cents_usd": 0},
        "free_entry_point":      {"count": 0, "cents_usd": 0}
      }
    }
  }
]
```

**Decisiones de diseño:**

- **Cents int, no float USD.** Float en finance es bug magnet. `cost_cents_usd: 125` = $1.25. Si después se necesita precisión sub-cent, escalar a milicents en una migración.
- **`cost_cents_usd = null` mientras está pending.** Cuando se manda el mensaje, no sabemos pricing todavía — viene en el webhook `message_status`. Hasta entonces el campo es `null`. La métrica de "costo total del episodio" debe contemplar este null (sumar solo los no-null + reportar `pending_count` separado).
- **`rate_card_version` snapshotead per-mensaje.** Si Meta cambia las tarifas Colombia (jul 1, 2026 anunciado), no queremos recomputar costos históricos con el nuevo rate. El snapshot del versionado del rate card al momento del send garantiza inmutabilidad histórica.
- **`cost_summary` agregado pre-computado** porque queries del dashboard ("cuánto costó este cliente / este episodio") deben ser O(1) sin parsear el log. Se actualiza en cada outbound persist (cuando llega el webhook con pricing) — fuente de verdad sigue siendo `outbound_messages[]`, el summary es derivable.
- **Cap del log inline:** si `outbound_messages` supera 200 entries en un mismo episodio (improbable pero defensivo), se truncan los más viejos y se setea `outbound_messages_truncated: true`. El JSONL del session_history retiene el detalle completo siempre. Esto evita JSONs gigantes en metadata.json.
- **Backfill lazy.** Episodios pre-feature no tienen estos campos. Treat `cost_summary == None` como "desconocido" — no romper queries que asumen presencia.

### §3.2 Tipado Python — DTOs frozen (R-JSON)

Sitio: `src/platform/whatsapp/state.py` (nuevo módulo) — leído tanto por `IngestInboundMessage` como por activities. Mantiene los dataclasses en `platform/whatsapp/` para que `chats/sales/` y `chats/remarketing/` los compartan sin violar R-DIP.

```python
from dataclasses import dataclass, field

@dataclass(frozen=True)
class PricingSnapshot:
    """Snapshot del objeto `pricing` que Meta envía en `message_status`.

    pricing_type:
      * "regular"               — cobra según category
      * "free_customer_service" — dentro de ventana 24h, gratis
      * "free_entry_point"      — dentro de 72h CTWA, gratis
    """
    billable: bool
    pricing_type: str
    category: str  # "marketing" | "utility" | "authentication" | "service"

@dataclass(frozen=True)
class OutboundSnapshot:
    wa_message_id: str
    sent_at_ms: int
    kind: str  # "text" | "template" | "image" | ...
    template_name: str | None
    pricing: PricingSnapshot | None  # llena con delivery webhook

@dataclass(frozen=True)
class WatchdogState:
    workflow_id: str | None
    scheduled_for_ms: int | None
    fired_at_ms: int | None
    cancelled_at_ms: int | None
    reason_cancelled: str | None

@dataclass(frozen=True)
class CadenceAttempt:
    """Una entrada del log de remarketing_cadence.attempts[]."""
    attempt_n: int
    scheduled_at_ms: int
    fired_at_ms: int | None
    wa_message_id: str | None
    template_name: str
    category: str  # "utility" | "marketing"
    pricing_type: str | None
    outcome: str  # "pending" | "delivered" | "read" | "responded" | "failed"

@dataclass(frozen=True)
class RemarketingCadenceState:
    episode_id: str
    started_at_ms: int
    attempts: tuple[CadenceAttempt, ...]
    next_attempt_n: int | None
    next_attempt_fire_at_ms: int | None
    next_attempt_workflow_id: str | None
    stopped: bool
    stop_reason: str | None

@dataclass(frozen=True)
class OutboundLogEntry:
    """Entry de `metadata.episodes[*].outbound_messages[]`. Persistencia de cada mensaje
    outbound enviado en el episodio, con su pricing y costo computado."""
    sent_at_ms: int
    wa_message_id: str
    kind: str  # "text" | "template" | "image" | "interactive_buttons" | ...
    template_name: str | None
    pricing: PricingSnapshot | None
    cost_cents_usd: int | None         # null mientras pricing webhook no llegó
    rate_card_version: str | None      # snapshot del rate card usado al computar el costo

@dataclass(frozen=True)
class CategoryCostBreakdown:
    count: int
    cents_usd: int

@dataclass(frozen=True)
class EpisodeCostSummary:
    """Agregado precomputado de costos del episodio. Recomputable desde
    `outbound_messages[]` pero materializado para queries O(1)."""
    total_cents_usd: int
    messages_count: int
    messages_billable_count: int
    messages_free_count: int
    messages_pending_count: int        # count de outbounds sin pricing webhook todavía
    by_category: dict[str, CategoryCostBreakdown]
    by_pricing_type: dict[str, CategoryCostBreakdown]

@dataclass(frozen=True)
class RateCardEntry:
    cents_per_message: int

@dataclass(frozen=True)
class RateCard:
    version: str                       # "co_2026q2_v1"
    effective_from_ms: int
    country: str                       # "CO"
    currency: str                      # "USD"
    rates: dict[str, RateCardEntry]    # {"marketing": ..., "utility": ..., ...}
```

Persistencia: `FilesystemMetadataStore.write()` ya existe y maneja escritura atómica. Los DTOs se serializan a dict via `dataclasses.asdict` antes de mezclarse al `metadata` general.

### §3.3 Helpers puros (R-STATELESS)

Sitio: `src/platform/whatsapp/window.py` (nuevo módulo).

```python
SERVICE_WINDOW_MS = 24 * 60 * 60 * 1000
CTWA_WINDOW_MS = 72 * 60 * 60 * 1000
WATCHDOG_PRE_EXPIRY_MS = 30 * 60 * 1000  # 30 min antes de cierre

def compute_service_window_expiry(last_inbound_at_ms: int) -> int:
    return last_inbound_at_ms + SERVICE_WINDOW_MS

def is_in_service_window(now_ms: int, metadata: dict) -> bool:
    exp = metadata.get("service_window_expires_at_ms")
    return isinstance(exp, int) and now_ms < exp

def is_in_ctwa_window(now_ms: int, metadata: dict) -> bool:
    exp = metadata.get("ctwa_window_expires_at_ms")
    return isinstance(exp, int) and now_ms < exp

def watchdog_fire_at(metadata: dict) -> int | None:
    exp = metadata.get("service_window_expires_at_ms")
    if not isinstance(exp, int):
        return None
    return exp - WATCHDOG_PRE_EXPIRY_MS
```

### §3.4 Rate card local + helper de cost computation

**Sitio:** `src/platform/whatsapp/rate_cards/co_2026q2_v1.yaml` (versioned, immutable). El composition root (`composition.py`) elige cuál es el "current" rate card por env / config.

```yaml
# rate_cards/co_2026q2_v1.yaml
version: co_2026q2_v1
effective_from_ms: 1717200000000  # Apr 1, 2026
country: CO
currency: USD
rates:
  marketing:                   { cents_per_message: 125 }   # $0.0125
  utility:                     { cents_per_message: 8 }     # $0.0008
  authentication:              { cents_per_message: 8 }
  authentication_international: { cents_per_message: null } # no listado en Colombia
  service:                     { cents_per_message: 0 }     # free-form gratis dentro ventana
```

Cuando Meta actualice rates (jul 1, 2026 anunciado), se crea un nuevo archivo `co_2026q3_v1.yaml` — el viejo NO se modifica, así episodios históricos retienen su rate snapshot.

**Helper puro** (sitio: `src/platform/whatsapp/cost.py`):

```python
def compute_message_cost_cents(
    pricing: PricingSnapshot,
    rate_card: RateCard,
) -> int:
    """Devuelve costo en cents USD para UN mensaje.

    Reglas:
    - pricing.billable == False → 0
    - pricing.pricing_type == "free_customer_service" → 0 (gratis dentro de ventana 24h)
    - pricing.pricing_type == "free_entry_point"      → 0 (gratis dentro de 72h CTWA)
    - pricing.pricing_type == "regular":
        → rate_card.rates[category].cents_per_message
        → si la categoría no está en el rate card (ej. authentication_international null
          en Colombia), retorna 0 + loguea warning (defensivo).
    """
    if not pricing.billable:
        return 0
    if pricing.pricing_type in ("free_customer_service", "free_entry_point"):
        return 0
    entry = rate_card.rates.get(pricing.category)
    if entry is None or entry.cents_per_message is None:
        # log.warning("rate card no tiene category", category=...)
        return 0
    return entry.cents_per_message


def update_episode_cost_summary(
    summary: EpisodeCostSummary | None,
    log_entry: OutboundLogEntry,
) -> EpisodeCostSummary:
    """Acumula un OutboundLogEntry al summary existente. Retorna nuevo dict frozen
    (puro). Usado en el use case `IngestDeliveryStatus` al recibir pricing webhook.

    Si el log_entry todavía tiene cost_cents_usd == None (pricing pendiente),
    incrementa `messages_pending_count`. Cuando llega el webhook y el cost se
    materializa, se llama de nuevo y se hace el delta:
    - pending_count -= 1
    - billable/free_count += 1
    - total_cents_usd += cost
    - by_category[cat].count += 1 + by_category[cat].cents_usd += cost
    """
    ...
```

**Composition factory** (sitio: `src/platform/whatsapp/composition.py`):

```python
@lru_cache(maxsize=1)
def get_current_rate_card() -> RateCard:
    """Lee env var WHATSAPP_RATE_CARD_VERSION (default: 'co_2026q2_v1') y carga el YAML."""
    ...
```

R-STATELESS cumplido: el cache vive en composition, NO module-level en la activity.

---

## §4 Arquitectura del watchdog

### §4.1 Decisión: durable workflow per-conversación vs Temporal Schedule global

| Opción | Pro | Contra | Decisión |
|---|---|---|---|
| (A) **Temporal Schedule cron global** que cada N min scanea todos los `metadata.json` y dispara nudges | Una sola schedule | NO escala (lee TODO el vault), no cancelable per-conversation, lock contention | ❌ |
| (B) **Sleep dentro del RemarketingSessionWorkflow** existente | Ya tiene state | Remarketing workflow no siempre está corriendo cuando se acerca el cierre de ventana (solo cuando `decide_ghosting_action` lo programa). Sales workflow no debería ocuparse del watchdog (separation of concerns) | ❌ |
| (C) **Workflow durable nuevo `ServiceWindowWatchdogWorkflow`, uno por conversación**, programado al persistir cada inbound (via dispatcher manifest) | Determinista, cancelable por signal, escala (Temporal maneja millones de workflows dormidos), aprovecha el dispatcher Level-3 ya existente | Crea un workflow por conversación (overhead de history; mitigable con `continue_as_new` ante turn_count > 50) | ✅ **(C)** |

### §4.2 `ServiceWindowWatchdogWorkflow` — spec

**Sitio:** `src/plugins/chats/agent/remarketing/workflows/watchdog.py`.

**Workflow ID template:** `watchdog-{session_id}-{episode_id}` (uno por episodio del cliente — si el episodio cierra, el watchdog también).

**Task queue:** `queue-chats-remarketing` (reusar la del worker existente).

```python
@dataclass(frozen=True)
class WatchdogInput:
    session_id: str
    episode_id: str
    fire_at_ms: int                  # epoch ms cuando dispara el nudge
    suggested_template_kind: str     # "quote_pending" | "payment_pending" | "order_status" | ...

@workflow.defn(name="ServiceWindowWatchdogWorkflow")
class ServiceWindowWatchdogWorkflow:
    def __init__(self) -> None:
        self._cancelled: bool = False
        self._cancel_reason: str | None = None

    @workflow.signal
    async def cancel_watchdog(self, reason: str) -> None:
        """Cancela el watchdog. Fuentes:
          - cliente respondió (CustomerReplied event → dispatcher signal)
          - deal cerró (close_episode emite event)
          - operador humano tomó el caso (active_route=humano)
        """
        self._cancelled = True
        self._cancel_reason = reason

    @workflow.run
    async def run(self, input: WatchdogInput) -> None:
        # 1. Sleep hasta fire_at_ms - now (Temporal-determinista)
        delta_ms = input.fire_at_ms - workflow.now().timestamp() * 1000
        if delta_ms > 0:
            try:
                await workflow.wait_condition(
                    lambda: self._cancelled,
                    timeout=timedelta(milliseconds=delta_ms),
                )
            except asyncio.TimeoutError:
                pass  # ventana sigue por expirar — proceder con nudge

        if self._cancelled:
            # 2a. Persistir motivo, salir
            await workflow.execute_activity(
                persist_watchdog_outcome_activity,
                args=[input.session_id, "cancelled", self._cancel_reason],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            return

        # 2b. Re-chequear elegibilidad (defensa profunda):
        #     - active_route != humano
        #     - episodio sigue activo
        #     - última ventana todavía dentro de 30min de expirar
        eligibility = await workflow.execute_activity(
            check_watchdog_eligibility_activity,
            args=[input.session_id, input.episode_id],
            start_to_close_timeout=timedelta(seconds=15),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
        if not eligibility.eligible:
            await workflow.execute_activity(
                persist_watchdog_outcome_activity,
                args=[input.session_id, "skipped", eligibility.reason],
                start_to_close_timeout=timedelta(seconds=10),
            )
            return

        # 3. Disparar el template
        result = await workflow.execute_activity(
            send_whatsapp_template_activity,
            args=[
                input.session_id,
                eligibility.resolved_template_name,
                eligibility.resolved_template_variables,
            ],
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )

        # 4. Persistir outcome (fired_at, wa_message_id)
        await workflow.execute_activity(
            persist_watchdog_outcome_activity,
            args=[input.session_id, "fired", result.wa_message_id],
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=2),
        )
```

**Características clave:**
- **Determinista** (R-DET): `workflow.now()` y `workflow.wait_condition` con timeout fijo. NO `datetime.now()` ni `time.sleep`.
- **Cancelable por signal** desde fuera. No por mensaje user directo (el cliente no sabe nada del workflow) — sí por evento `CustomerReplied` que el dispatcher manifest enrutea.
- **Re-chequea elegibilidad antes de disparar** (post-mortem pattern ya usado en `RemarketingSessionWorkflow` línea 159).
- **Idempotente downstream:** si fires dos veces (retry de activity), `send_whatsapp_template_activity` se encarga del dedup vía `wa_message_id`.

### §4.3 Cancelación: ¿quién signala?

Dos rutas declarativas vía manifest (ADR-2026-05-20):

1. **Cliente responde** → `IngestInboundMessage` emite evento `CustomerRepliedEvent(session_id, episode_id)` → manifest transition: `via=signal, target_workflow=ServiceWindowWatchdogWorkflow, signal_handler=cancel_watchdog`.

2. **Episodio cierra** (`COMPRA_EXITOSA` / `RECHAZO` / `CONFIRMADO_SIN_DATOS` / escalation a humano) → `close_episode` emite `EpisodeClosedEvent(session_id, episode_id, closing_tag)` → manifest transition: idem.

Ambos signals usan el mismo `workflow_id` template `watchdog-{session_id}-{episode_id}`. Si no hay un workflow corriendo con ese id, el signal hace noop (Temporal behavior).

### §4.4 Activación: ¿cuándo se programa?

**En `IngestInboundMessage.execute()`** (después de persistir `service_window_expires_at_ms`):

1. Si **NO hay watchdog corriendo** para `(session_id, episode_id)` Y `active_route ∈ {ventas, remarketing}` Y episodio abierto → emitir `ServiceWindowOpenedEvent(session_id, episode_id, fire_at_ms)` → manifest transition: `via=start_workflow_with_replace` (cancela cualquier watchdog previo + arranca uno nuevo).
2. Si **YA hay watchdog corriendo** → emitir el mismo evento, `via=signal` con un handler `reschedule_watchdog(new_fire_at_ms)`. Esto evita arrancar workflows duplicados cuando el cliente manda 3 mensajes seguidos (la ventana se reabre, el watchdog se reagenda).

Detalle del signal `reschedule_watchdog`:

```python
@workflow.signal
async def reschedule_watchdog(self, new_fire_at_ms: int) -> None:
    self._fire_at_ms = new_fire_at_ms
    # El workflow.wait_condition() loop está esperando con timeout;
    # tras esta línea el próximo iter recalcula el delta.
```

Trade-off: vs cancel+restart, signal es más barato (mismo workflow history). Vs sleep largo único, esto permite que múltiples mensajes consecutivos del cliente no creen N workflows.

### §4.5 Template Registry

**Sitio:** `src/platform/whatsapp/templates/registry.py` + `src/platform/whatsapp/templates/catalog.yaml`.

**Catálogo declarativo** (no en código Python — operadores deben poder agregar templates sin redeploy):

```yaml
# catalog.yaml
templates:
  - name: quote_ready_utility_v1
    category: utility
    language: es_CO
    waba_template_name: "quote_ready_utility"  # nombre exacto aprobado por Meta
    variables:
      - name: customer_first_name
        type: string
        max_length: 60
      - name: product_or_quote_label
        type: string
        max_length: 120
    semantics: "Notificar al cliente que su cotización está lista"
    requires_episode_stage: "awaiting_quote"
    triggers_when_window_expiring: true
    fallback_for_categories: ["utility"]

  - name: payment_pending_utility_v1
    category: utility
    language: es_CO
    waba_template_name: "payment_pending_utility"
    variables:
      - name: customer_first_name
        type: string
      - name: order_reference
        type: string
      - name: amount_currency
        type: string
    semantics: "Recordar pago pendiente de orden creada"
    requires_episode_stage: "awaiting_payment"
    triggers_when_window_expiring: true

  - name: order_status_utility_v1
    category: utility
    language: es_CO
    waba_template_name: "order_status_utility"
    variables: [...]
    requires_episode_stage: "post_purchase"
    triggers_when_window_expiring: true

  - name: cart_recovery_marketing_v1
    category: marketing
    language: es_CO
    waba_template_name: "cart_recovery_marketing"
    variables: [...]
    semantics: "Recovery fuera de ventana con incentivo"
    requires_episode_stage: any
    triggers_when_window_expiring: false  # marketing NUNCA por watchdog, sólo por LLM consciente
```

**Loader Python**:

```python
@dataclass(frozen=True)
class TemplateSpec:
    name: str
    category: str
    language: str
    waba_template_name: str
    variables: tuple[TemplateVar, ...]
    semantics: str
    triggers_when_window_expiring: bool
    requires_episode_stage: str | None

def get_template_registry() -> dict[str, TemplateSpec]:
    # @lru_cache(maxsize=1) en composition.py
    ...
```

**Resolución automática del template para el watchdog** (`check_watchdog_eligibility_activity`):

1. Leer `metadata["tag"]` y `metadata["episodes"][-1]` para inferir stage.
2. Filtrar `templates[*]` donde `triggers_when_window_expiring=true` y `requires_episode_stage == stage`.
3. Si hay match exacto → usar.
4. Si no → no disparar watchdog (`eligibility.skipped`).
5. Rellenar variables desde `metadata` (`customer_first_name` desde el primer mensaje, `order_reference` desde `registered_order.order_id`, etc.).

### §4.6 Activity nueva: `send_whatsapp_template_activity`

**Sitio:** `src/platform/whatsapp/activities.py` (junto a `send_whatsapp_message_activity` existente).

```python
@activity.defn(name="send_whatsapp_template_activity")
@with_heartbeat(every=10)
async def send_whatsapp_template_activity(
    session_id: str,
    template_name: str,
    variables: dict[str, str],
) -> OutboundResult:
    """Envia un template aprobado.

    1. Resuelve TemplateSpec desde el registry (R-STATELESS via @lru_cache).
    2. Construye body Meta con `build_template_message` (nuevo en outbound.py).
    3. POST a Cloud API /messages.
    4. Persiste OutboundSnapshot en metadata.json (kind="template", template_name, sin pricing aún — viene por webhook status).
    5. Persiste el evento en el JSONL del session_history (sender=assistant, sender_kind=template, body con variables resueltas).
    6. Retorna OutboundResult(wa_message_id, ok, error).
    """
    ...
```

**Retry policy** (en el workflow caller):
- `maximum_attempts=3`
- Non-retryable: errores 4xx con `error.code ∈ {131008 (template not found), 132012 (template paused), 131049 (per-user marketing cap), 131047 (re-engagement message, no aplicable a template pero defensivo)}`.
- Sí retryable: 5xx, 429 (rate limit con backoff exponencial).

**Builder en `outbound.py`**:

```python
def build_template_message(
    to: str,
    spec: TemplateSpec,
    variables: dict[str, str],
) -> dict[str, Any]:
    """Construye payload Meta para template send."""
    # Cloud API espera:
    # {
    #   "messaging_product": "whatsapp",
    #   "to": to,
    #   "type": "template",
    #   "template": {
    #     "name": spec.waba_template_name,
    #     "language": {"code": spec.language},
    #     "components": [
    #       {"type": "body", "parameters": [{"type": "text", "text": v} for v in variables_ordered]}
    #     ]
    #   }
    # }
    ...
```

### §4.7 Webhook delivery status — capturar `pricing`

**Sitio:** `src/plugins/chats/api/webhook.py` (ya existe handler de webhooks).

Hoy probablemente sólo procesa `entry[*].changes[*].value.messages[]` (inbound). Hay que agregar:

```python
# entry[*].changes[*].value.statuses[]
for status in payload.get("statuses", []):
    wa_message_id = status["id"]
    delivery_status = status["status"]  # "sent" | "delivered" | "read" | "failed"
    pricing = status.get("pricing")  # objeto {billable, pricing_type, category}

    # Background task: actualizar metadata["last_outbound"]["pricing"]
    await ingest_delivery_status_use_case.execute(
        wa_message_id=wa_message_id,
        delivery_status=delivery_status,
        pricing=pricing,
    )
```

Use case puro `IngestDeliveryStatus`:
1. Buscar conversation que tiene `last_outbound.wa_message_id == wa_message_id` (o por índice secundario en metadata.episodes[*].outbound_messages[*]).
2. Mergear `pricing` snapshot al `last_outbound`.
3. **Computar costo:** invocar `compute_message_cost_cents(pricing, current_rate_card)` + persistir `cost_cents_usd` + `rate_card_version` en el `OutboundLogEntry` correspondiente dentro del episode.
4. **Actualizar `cost_summary` del episodio:** llamar `update_episode_cost_summary(episode.cost_summary, log_entry)` y persistir.
5. Emitir analytic event `wa_delivery_status(wa_message_id, status, pricing_type, category, billable, cost_cents_usd)` — agregando el costo al payload para que dashboards downstream lo consuman directo.

**Edge case crítico:** el webhook `message_status` puede llegar ANTES de que `send_whatsapp_template_activity` haya persistido el OutboundLogEntry (race: Meta envía el status `sent` casi instantáneamente). Solución: el OutboundLogEntry se persiste DENTRO del activity de send, antes de retornar — atomic. Si el webhook llega y no encuentra la entry, se reintenta el use case con backoff (max 3 reintentos en 5s); si sigue sin encontrarla → log + dead-letter al archivo `hubara_vault/_orphan_delivery_statuses.jsonl` para análisis manual.

### §4.8 Tool LLM para envío consciente: `send_template_message`

**Sitio:** `src/plugins/chats/agent/remarketing/tools/send_template.py` (nuevo).

```python
class SendTemplateMessageTool(Tool):
    """El agente remarketing puede elegir un template del registry cuando
    decida que es la jugada correcta (típicamente: fuera de ventana,
    intent de re-engage, segmento elegible).

    Input: {template_name, variables}
    Validation: el template debe existir, las variables deben matchear el spec,
                category == "marketing" requiere flag explícito `confirm_marketing_send=True`.
    Side-effect: encola un `ui_intent.kind = "template_message"` en metadata.
                 `flush_pending_ui_intents_activity` (ya existente) lo dispatch.
    """
```

Esto permite el patrón:
- **Watchdog automático** → utility template gratis/casi-gratis cuando ventana por expirar.
- **LLM consciente** → marketing template ($0.0125) cuando el agente decide que vale la pena el costo (típicamente sólo con segmentos de alto ROI).

### §4.9 Cadencia adaptativa de re-engagement (`RemarketingCadenceWorkflow`)

> Foundamentación científica del diseño: **§12 — Lead Response Management Study (Oldroyd, McElheran & Elkington)**. Lo que sigue son las decisiones técnicas derivadas; el "por qué" vive en §12.

**Diferencia con el watchdog (§4.2):**
- **Watchdog** opera *dentro* de la ventana de 24h cuando todavía hay deal abierto — dispara UN utility template legítimo justo antes del cierre. Cancelable.
- **Cadencia** opera *después* del watchdog si el cliente NO respondió: secuencia de 5 intentos espaciados en ~21 días con escalation gradual de category (utility → marketing) e incentivo (info → oferta → urgency). Cancelable.

Son dos workflows distintos, dos workflow IDs distintos, dos catálogos de templates. Trabajan en cascada.

#### §4.9.1 Schedule de cadencia (default, configurable por tenant)

| Attempt | Trigger (desde último inbound del cliente) | Category | Intent del template | Stop condition |
|---|---|---|---|---|
| **#1 — Watchdog** | t + 23.5h | Utility | Continuidad legítima (cotización lista, pago pendiente, status orden) | Cliente responde → cancel toda la cadencia |
| **#2** | t + 48h (1 día post-watchdog) | Utility (si stage lo permite) o Marketing light | Recordatorio gentil + value-add (FAQ, info útil) | Cliente responde → cancel |
| **#3** | t + 5 días | Marketing | Incentivo light (free shipping, descuento 5-10%) | Cliente responde → cancel |
| **#4** | t + 10 días | Marketing | Incentivo medio (descuento 10-15%) + social proof | Cliente responde → cancel |
| **#5** | t + 21 días | Marketing | Última oferta (descuento mayor + urgency) + "te seguimos en otro momento" | Cliente responde → cancel; o stop natural |
| **Stop** | t + 21 días | — | — | Max 5 attempts. Después no se contacta hasta que el cliente abra ventana de nuevo. |

**Por qué 5 attempts y no 12** (que es lo que recomienda el estudio):
- El estudio original es B2B cold-calling. En WhatsApp B2C el cap global de Meta (2 marketing templates/día/usuario combinados desde todos los businesses) y el quality rating drop si los users marcan spam recomiendan ser **más conservador**.
- Industry benchmarks de WhatsApp B2C (2026) sugieren 3-5 follow-ups en 2-3 semanas como sweet spot conversión/spam.
- Empezamos con 5; si las métricas de attempt #5 muestran conversion >2% sostenido, evaluar extender a 6-7.

#### §4.9.2 `RemarketingCadenceWorkflow` — spec

**Sitio:** `src/plugins/chats/agent/remarketing/workflows/cadence.py`.

**Workflow ID template:** `cadence-{session_id}-{episode_id}` (uno por episodio, NO uno por attempt — el mismo workflow vive las ~3 semanas y orquesta los 5 attempts secuencialmente).

```python
@dataclass(frozen=True)
class CadenceInput:
    session_id: str
    episode_id: str
    cadence_schedule_id: str   # "default_b2c_v1" — del catálogo de cadencias
    started_at_ms: int

@dataclass(frozen=True)
class CadenceScheduleEntry:
    attempt_n: int
    delay_ms_from_start: int   # 48h, 5d, 10d, 21d (excluye watchdog #1)
    category: str              # "utility" | "marketing"
    template_kind: str         # "soft_reminder" | "incentive_light" | "incentive_medium" | "last_chance"

@workflow.defn(name="RemarketingCadenceWorkflow")
class RemarketingCadenceWorkflow:
    def __init__(self) -> None:
        self._cancelled: bool = False
        self._cancel_reason: str | None = None
        self._paused: bool = False
        self._pause_reason: str | None = None

    @workflow.signal
    async def cancel_cadence(self, reason: str) -> None:
        self._cancelled = True
        self._cancel_reason = reason

    @workflow.signal
    async def pause_cadence(self, reason: str) -> None:
        """Pausa sin cancelar (operador humano, quality rating drop). El workflow
        queda dormido hasta resume_cadence o cancel_cadence."""
        self._paused = True
        self._pause_reason = reason

    @workflow.signal
    async def resume_cadence(self) -> None:
        self._paused = False
        self._pause_reason = None

    @workflow.run
    async def run(self, input: CadenceInput) -> None:
        # 1. Load schedule (activity, NO en el body — R-DET)
        schedule = await workflow.execute_activity(
            load_cadence_schedule_activity,
            args=[input.cadence_schedule_id],
            start_to_close_timeout=timedelta(seconds=10),
        )

        # 2. Iterar attempts secuencialmente
        for entry in schedule.entries:  # ya ordenadas por attempt_n
            fire_at_ms = input.started_at_ms + entry.delay_ms_from_start
            now_ms = int(workflow.now().timestamp() * 1000)
            delta_ms = fire_at_ms - now_ms

            # Sleep until fire_at, interrumpible por signal
            if delta_ms > 0:
                try:
                    await workflow.wait_condition(
                        lambda: self._cancelled or not self._paused,
                        timeout=timedelta(milliseconds=delta_ms),
                    )
                except asyncio.TimeoutError:
                    pass

            if self._cancelled:
                await workflow.execute_activity(
                    persist_cadence_outcome_activity,
                    args=[input.session_id, "cancelled", self._cancel_reason],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                return

            # Si está pausado, esperar resume (sin timeout)
            if self._paused:
                await workflow.wait_condition(lambda: not self._paused or self._cancelled)
                if self._cancelled:
                    return

            # Eligibility check + resolve template (activity)
            decision = await workflow.execute_activity(
                check_cadence_attempt_eligibility_activity,
                args=[input.session_id, input.episode_id, entry.attempt_n,
                      entry.category, entry.template_kind],
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

            if decision.action == "skip":
                # Razones típicas: per-user marketing cap, quality rating drop,
                # no eligible template para el stage, fuera de horario permitido
                await workflow.execute_activity(
                    persist_cadence_attempt_activity,
                    args=[input.session_id, entry.attempt_n, "skipped",
                          decision.skip_reason, None, None],
                    start_to_close_timeout=timedelta(seconds=10),
                )
                if decision.stop_cadence:
                    # quality_rating_drop o marketing_cap_hit son terminales
                    return
                continue  # próximo attempt

            # Send template
            result = await workflow.execute_activity(
                send_whatsapp_template_activity,
                args=[input.session_id, decision.resolved_template_name,
                      decision.resolved_variables],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(maximum_attempts=3),
            )

            await workflow.execute_activity(
                persist_cadence_attempt_activity,
                args=[input.session_id, entry.attempt_n,
                      "fired" if result.ok else "failed",
                      None, result.wa_message_id, decision.resolved_template_name],
                start_to_close_timeout=timedelta(seconds=10),
            )

            if not result.ok:
                # Send failed — abortar cadencia (probable issue de templates/quality)
                return

        # Loop terminó natural: persistir "exhausted"
        await workflow.execute_activity(
            persist_cadence_outcome_activity,
            args=[input.session_id, "exhausted", None],
            start_to_close_timeout=timedelta(seconds=10),
        )
```

**Características clave:**
- **Determinismo** (R-DET): toda la lógica de fechas pasa por activities. El workflow solo orquesta.
- **Cancelable** desde manifest: cliente responde, episodio cierra, operador toma el caso.
- **Pausable**: operador puede pausar cadencia desde dashboard (signal). Útil para "vacaciones" del tenant o para mitigar quality rating drop temporal.
- **Resumible**: post-pause, el cadence workflow sigue desde donde estaba — no reinicia.
- **Schedule declarativo**: `load_cadence_schedule_activity` lee de un YAML/registry. Tenants pueden tener cadence_schedule_id distintos (ej. "default_b2c_v1", "luxury_long_v1", "fast_low_ticket_v1").

#### §4.9.3 Catálogo de cadencias (YAML)

**Sitio:** `src/platform/whatsapp/templates/cadences.yaml`.

```yaml
cadences:
  - id: default_b2c_v1
    description: "WhatsApp B2C — 5 attempts en 21 días, escalation utility→marketing"
    entries:
      - attempt_n: 2
        delay_ms_from_start: 172800000   # 48h
        category: utility
        template_kind: soft_reminder
        fallback_category_if_no_utility: marketing
      - attempt_n: 3
        delay_ms_from_start: 432000000   # 5 días
        category: marketing
        template_kind: incentive_light
      - attempt_n: 4
        delay_ms_from_start: 864000000   # 10 días
        category: marketing
        template_kind: incentive_medium
      - attempt_n: 5
        delay_ms_from_start: 1814400000  # 21 días
        category: marketing
        template_kind: last_chance

  - id: aggressive_low_ticket_v1
    description: "Productos low-ticket, ciclo decisión rápido"
    entries:
      - { attempt_n: 2, delay_ms_from_start: 86400000,  category: marketing, template_kind: incentive_light }
      - { attempt_n: 3, delay_ms_from_start: 259200000, category: marketing, template_kind: incentive_medium }
      - { attempt_n: 4, delay_ms_from_start: 604800000, category: marketing, template_kind: last_chance }

  - id: luxury_long_v1
    description: "Productos high-ticket, ciclo lento, sin agresividad"
    entries:
      - { attempt_n: 2, delay_ms_from_start: 259200000,  category: utility,   template_kind: soft_reminder }
      - { attempt_n: 3, delay_ms_from_start: 1209600000, category: marketing, template_kind: incentive_light }
      - { attempt_n: 4, delay_ms_from_start: 2592000000, category: marketing, template_kind: last_chance }
```

La elección de `cadence_schedule_id` por episodio se hace en `IngestInboundMessage` (o un use case `decide_remarketing_cadence`), basado en tags del catálogo y meta del cliente (ticket size del último producto cotizado, segment, etc.). Default: `default_b2c_v1`.

#### §4.9.4 Activación: ¿cuándo arranca la cadencia?

Lo natural: cuando el **watchdog #1 fires sin respuesta del cliente**, emite un evento `WatchdogFiredWithoutReplyEvent(session_id, episode_id)` después de un timeout post-fire (e.g., 24h sin reply al watchdog).

Manifest transition:
```yaml
- on_event: WatchdogFiredWithoutReplyEvent
  from: chats.remarketing
  via: start_workflow_with_replace
  target_workflow: RemarketingCadenceWorkflow
  workflow_id_template: "cadence-{event.session_id}-{event.episode_id}"
```

#### §4.9.5 Cancelación / pausa de cadencia

Mismo patrón que watchdog: el dispatcher manifest rutea `CustomerRepliedEvent` y `EpisodeClosedEvent` → signal `cancel_cadence`. Adicionalmente:
- `OperatorPausedCadenceEvent` (operador desde dashboard) → signal `pause_cadence`.
- `OperatorResumedCadenceEvent` → signal `resume_cadence`.
- `QualityRatingDroppedEvent` (cron monitor que se agrega como follow-up HU) → signal `pause_cadence` con reason `quality_rating_drop` en TODAS las cadencias activas del tenant (broadcast).

#### §4.9.6 Tool LLM para overrides del agente

**Sitio:** `src/plugins/chats/agent/remarketing/tools/cadence_control.py` (nuevo).

```python
class CadenceControlTool(Tool):
    """Permite al agente remarketing override la cadencia automática.

    Acciones soportadas:
      * stop_cadence(reason)         — frenar TODA la cadencia para esta sesión
      * skip_next_attempt(reason)    — saltear próximo attempt (ej. cliente acaba de ser contactado por otro canal)
      * accelerate_to(attempt_n)     — disparar el attempt_n YA, saltando los previos
      * switch_schedule(schedule_id) — cambiar de "default_b2c_v1" a "aggressive_low_ticket_v1"

    Use cases: el agente detectó intent de compra → stop_cadence. El agente
    detectó que el cliente ya escribió a la competencia → accelerate a
    last_chance directo.
    """
```

Estas acciones emiten signals al `RemarketingCadenceWorkflow` correspondiente vía dispatcher manifest, NO por import directo.

---

## §5 Plugin Manifest extensions

**Sitio:** `src/plugins/chats/plugin.yaml` (asumido — verificar nombre exacto en el repo). El manifest declara `events.emits[]` por worker y `events.consumers[]` con transitions.

```yaml
# plugin: chats
workers:
  - id: sales
    emits:
      # ... existing events ...
      - ServiceWindowOpenedEvent
      - CustomerRepliedEvent
      - EpisodeClosedEvent

  - id: remarketing
    emits:
      # ... existing ...
      - ServiceWindowOpenedEvent
      - CustomerRepliedEvent
      - EpisodeClosedEvent

  - id: watchdog  # nuevo worker — o reusar 'remarketing' como host (decisión §5.1)
    consumers:
      - on_event: ServiceWindowOpenedEvent
        from: chats.sales
        via: start_workflow_with_replace
        target_workflow: ServiceWindowWatchdogWorkflow
        workflow_id_template: "watchdog-{event.session_id}-{event.episode_id}"
        input_mapping:
          session_id: $.session_id
          episode_id: $.episode_id
          fire_at_ms: $.fire_at_ms
          suggested_template_kind: $.suggested_template_kind

      - on_event: ServiceWindowOpenedEvent
        from: chats.remarketing
        via: start_workflow_with_replace
        # idem

      - on_event: CustomerRepliedEvent
        from: chats.sales
        via: signal
        target_workflow: ServiceWindowWatchdogWorkflow
        workflow_id_template: "watchdog-{event.session_id}-{event.episode_id}"
        signal_handler: cancel_watchdog
        signal_args: ["customer_replied"]

      - on_event: EpisodeClosedEvent
        from: chats.sales
        via: signal
        target_workflow: ServiceWindowWatchdogWorkflow
        workflow_id_template: "watchdog-{event.session_id}-{event.episode_id}"
        signal_handler: cancel_watchdog
        signal_args: ["episode_closed"]
```

### §5.1 ¿Nuevo worker o reusar `remarketing`?

| Opción | Pro | Contra |
|---|---|---|
| **Nuevo worker `watchdog`** | Separation of concerns, escalable independiente | Más overhead operativo (un binary worker más en `run_workers.py` y k8s) |
| **Reusar `remarketing`** worker | Cero overhead operativo, mismas task queues, mismo composition | El remarketing worker hace dos cosas (cosa esperable: ambos son re-engagement, comparten 80% de utils) |

**Recomendación:** **Reusar `remarketing` worker** en sprint 1. Registrar el nuevo workflow en `src/plugins/chats/workers/remarketing.py` junto al `RemarketingSessionWorkflow` existente. Si la carga lo justifica luego (>10K conversaciones concurrentes), splittear.

---

## §6 R-rules check

| Rule | Cómo se cumple |
|---|---|
| **R-DET** (workflows deterministas) | `ServiceWindowWatchdogWorkflow` usa `workflow.now()` + `workflow.wait_condition` + Temporal-managed timer. `fire_at_ms` viene como input, no se calcula en el workflow body. |
| **R-JSON** (DTOs frozen JSON-serializables) | `WatchdogInput`, `PricingSnapshot`, `OutboundSnapshot`, `WatchdogState`, `TemplateSpec` — todos `@dataclass(frozen=True)` con tipos primitivos. |
| **R-STATELESS** (activities sin module-level cache) | Template registry vía `composition.py::get_template_registry()` con `@lru_cache(maxsize=1)`. Activities reciben la registry vía DI factory, no via module global. |
| **R-HEARTBEAT** (>10s heartbeat) | `send_whatsapp_template_activity` worst-case ~5s, no necesita. `check_watchdog_eligibility_activity` lee fs + valida — <2s. Sin heartbeat (consistente con `send_whatsapp_message_activity` que también está marcada `@with_heartbeat(every=10)` por simetría). |
| **R-DIP** (no cross-plugin / no platform→plugin / tools no temporalio.client) | `platform/whatsapp/templates/` es shared infra (utility). Workflow vive en `plugins/chats/agent/remarketing/workflows/`. Activities en `platform/whatsapp/activities.py` (donde ya viven las hermanas). Events nuevos en `chats/shared/contracts/events.py` (los importa el dispatcher manifest). Composición vía `composition.py` factories. NO importa `temporalio.client` desde tools. |

**Spinal files a tocar** (requieren ADR + `ARCH_CHANGE_APPROVED=1`):
- `src/platform/constants.py` — agregar `SERVICE_WINDOW_MS`, `WATCHDOG_PRE_EXPIRY_MS` si se vuelven constantes cross-plugin.
- ❌ NO tocar `tests/architecture/**`, `.importlinter`, `src/platform/contracts.py`, `src/platform/registries.py`, `src/platform/tool_extensions.py` — el HU no los necesita.

---

## §7 Plan de migración por fases

### Sprint 0 — Operacional (pre-código, en paralelo a finalizar sales)

> Este sprint NO escribe código pero ES BLOQUEANTE para los siguientes. Hay lead times de Meta que ningún sprint técnico puede acelerar.

| # | Tarea | Owner | Lead time |
|---|---|---|---|
| **F0.1** | **Redactar copy de los 4 templates utility** con copywriter humano (no LLM): `quote_ready_utility_v1`, `payment_pending_utility_v1`, `order_status_utility_v1`, `welcome_post_ctwa_utility_v1`. Validar que NO incluyan ofertas ni promo (Meta los recategorizaría a marketing). | Operador + copywriter | 2-3 días |
| **F0.2** | **Submit aprobación a Meta** vía Business Manager por cada template (en `es_CO`). | Operador | 24-72h por template (Meta puede demorar) |
| **F0.3** | Redactar + submit 2 templates marketing: `cart_recovery_marketing_v1`, `welcome_back_marketing_v1` (para Sprint 4 cadencia, NO bloquean Sprint 1-3). | Operador | 24-72h |
| **F0.4** | **Confirmar setup Cloud API:** phone_number_id activo, webhook URL apuntando a `hubara_agency/.../webhook`, permissions OK. | Operador + DevOps | 1 día |
| **F0.5** | Setup **alertas de quality rating** en Business Manager (notificación email/Slack si baja a YELLOW o RED). | Operador | 30 min |
| **F0.6** | **Incident response runbook**: definir qué se hace si quality rating cae a YELLOW en la primera semana (pausa de outbound activos, reducción de volumen, revisión de templates). Docs en `hubara_agency/.hubara/runbooks/quality_rating_response.md`. | Operador | 1 día |
| **F0.7** | Decisión: **¿WABA en USD o COP?** Si COP, planear migración (HU separada). Para Sprint 1, mantener en USD si ya está. | Operador | 1 día decisión |

**Gate Sprint 0:** al menos los 4 templates utility de F0.2 aprobados antes de empezar Sprint 1 F1.5. Si Meta tarda más de lo esperado, Sprint 1 se puede arrancar igual con templates mockeados para tests, pero Sprint 2 no puede activarse sin los reales.

### Sprint 1 — Foundation (sin watchdog todavía)

**Goal:** persistir timestamps y agregar template send capability sin cambiar comportamiento del agente.

1. **F1.1** `IngestInboundMessage.execute()` agrega `last_inbound_at_ms` + computa y persiste `service_window_expires_at_ms` (igual a `_now_ms() + 24h`).
2. **F1.2** `session_history/store.py::append_user_event` agrega `timestamp` ISO UTC al evento (simetría con assistant).
3. **F1.3** Detectar `referral.ctwa_clid` en el inbound y persistir `ctwa_window_expires_at_ms = _now_ms() + 72h` (sólo primer touch, no se renueva).
4. **F1.4** Crear `src/platform/whatsapp/window.py` con helpers puros + tests unitarios (test cases: dentro/fuera de ventana, CTWA, watchdog fire_at calc).
5. **F1.5** Crear `src/platform/whatsapp/templates/{registry.py, catalog.yaml}` + `composition.py::get_template_registry()`. Cargar 4 templates iniciales (los 3 utility + 1 marketing del catalog ejemplo).
6. **F1.6** Crear `build_template_message` en `outbound.py` + tests (incluido caso de variables faltantes/extra).
7. **F1.7** Crear `send_whatsapp_template_activity` en `platform/whatsapp/activities.py` + tests con WhatsApp client mockeado. Persistir `OutboundLogEntry` en `metadata.episodes[active].outbound_messages[]` ANTES de retornar (atomicidad para el race del webhook).
8. **F1.8** Crear `src/platform/whatsapp/rate_cards/co_2026q2_v1.yaml` + `src/platform/whatsapp/cost.py` con `compute_message_cost_cents` y `update_episode_cost_summary` + tests unitarios (free dentro ventana, free CTWA, regular marketing/utility, missing category).
9. **F1.9** Crear `composition.py::get_current_rate_card()` con `@lru_cache(maxsize=1)`. Env var `WHATSAPP_RATE_CARD_VERSION` (default `co_2026q2_v1`).
10. **F1.10** Webhook `statuses[]` handler — `IngestDeliveryStatus` use case + integration en api/webhook.py. Persistir `pricing` snapshot + `cost_cents_usd` en `OutboundLogEntry` + actualizar `cost_summary` del episode.
11. **F1.11** Handle race condition: si delivery webhook llega antes que el OutboundLogEntry esté persistido, retry con backoff exponencial 3 veces; si sigue fallando → dead-letter a `hubara_vault/_orphan_delivery_statuses.jsonl`.
12. **F1.12** Tests E2E del flow completo: send template → status webhook delivered → pricing capturado → cost_cents_usd computado → cost_summary actualizado.

**Gate Sprint 1:**
- Todos los tests pasan, `lint-imports` verde.
- Ningún workflow en vuelo se rompe (el nuevo activity solo se agrega).
- Test manual: enviar un template a un número de prueba, verificar que en `metadata.json` aparece `outbound_messages[]` + `cost_summary` actualizado tras unos segundos.

### Sprint 2 — Watchdog en sombra

**Goal:** workflow watchdog funcional pero detrás de feature flag (NO dispara sends todavía).

1. **F2.1** Crear `WatchdogInput` DTO + `ServiceWindowWatchdogWorkflow` workflow + tests de replay determinism.
2. **F2.2** Crear `check_watchdog_eligibility_activity` + `persist_watchdog_outcome_activity`.
3. **F2.3** Agregar `ServiceWindowOpenedEvent`, `CustomerRepliedEvent`, `EpisodeClosedEvent` a `chats/shared/contracts/events.py`.
4. **F2.4** Extender `IngestInboundMessage` para emitir `ServiceWindowOpenedEvent` + `CustomerRepliedEvent` (este último solo si ya había watchdog activo — leer metadata.watchdog.workflow_id).
5. **F2.5** Extender `close_episode` (en episode_lifecycle.py) para emitir `EpisodeClosedEvent`.
6. **F2.6** Extender plugin manifest con las 3 transitions.
7. **F2.7** Registrar workflow + activities en `plugins/chats/workers/remarketing.py`.
8. **F2.8** Feature flag `WATCHDOG_ENABLED=false` en environment — `check_watchdog_eligibility_activity` retorna `eligible=False, reason="feature_flag_off"` cuando está off. **NO bloquea el send activity** — el workflow llega hasta el chequeo de elegibilidad y desiste limpio.

**Gate Sprint 2:**
- Workflow watchdog corriendo en producción con `WATCHDOG_ENABLED=false` (no se dispara aún).
- Logs muestran que se programan watchdogs correctamente en cada inbound.
- Tests de cancelación por signal verdes (cliente responde / episodio cierra).

### Sprint 3 — Activación watchdog

**Goal:** disparar nudges del watchdog reales.

1. **F3.1** Activar `WATCHDOG_ENABLED=true` en tenant pilot (1 cliente).
2. **F3.2** Monitorear `wa_delivery_status` events durante 1 semana — confirmar `pricing.type == "free_customer_service"` para todos los nudges (esperado: gratis dentro de ventana).
3. **F3.3** Si la tasa de respuesta del cliente al nudge es positiva (>15% en pilot) → expandir a todos los tenants.
4. **F3.4** Documentar runbook: cómo agregar templates, cómo desactivar por tenant si quality rating cae.

### Sprint 4 — Cadencia adaptativa (post-watchdog)

**Goal:** secuencia de 5 attempts en 21 días, basada en Lead Response Management Study (§13).

1. **F4.1** Crear `cadences.yaml` con 3 schedules iniciales (`default_b2c_v1`, `aggressive_low_ticket_v1`, `luxury_long_v1`) + `load_cadence_schedule_activity`.
2. **F4.2** Crear DTOs `CadenceInput`, `CadenceScheduleEntry`, `RemarketingCadenceState`, `CadenceAttempt`.
3. **F4.3** Crear `RemarketingCadenceWorkflow` + activities (`check_cadence_attempt_eligibility_activity`, `persist_cadence_attempt_activity`, `persist_cadence_outcome_activity`) + tests de replay determinism.
4. **F4.4** Agregar evento `WatchdogFiredWithoutReplyEvent` + transition en manifest (arranca cadencia si watchdog disparó y el cliente no respondió en 24h).
5. **F4.5** Extender eligibility activity con guards de:
   - per-user marketing cap (heurística local: si vimos error 131049 reciente, skip).
   - quality rating tier (si tier ≤ TIER_1, no enviar marketing).
   - horario permitido (no enviar 22:00-08:00 hora local cliente — derivar del prefijo `wa_+57...` → America/Bogota).
   - escalation utility→marketing solo si stage del episodio NO admite utility.
6. **F4.6** Crear pre-aprobados 4 nuevos templates: `soft_reminder_utility_v1`, `incentive_light_marketing_v1`, `incentive_medium_marketing_v1`, `last_chance_marketing_v1`.
7. **F4.7** Feature flag `CADENCE_ENABLED=false` por tenant. Workflow corre pero activity de send queda no-op cuando flag off.
8. **F4.8** Habilitar pause/resume signals + endpoint dashboard `/api/cadence/{session_id}/pause` y `/resume`.

**Gate Sprint 4:**
- Cadence workflow corriendo en sombra 1 semana (`CADENCE_ENABLED=false`) — sin sends reales pero logs de "qué hubiera mandado".
- Análisis: ¿cuántos attempts hubieran sido skipped por eligibility? ¿cuántos clientes responderían post-watchdog vs cadence?
- Activar `CADENCE_ENABLED=true` en 1 tenant pilot.

### Sprint 5 — Tool LLM + tunning (post-MVP)

1. **F5.1** `CadenceControlTool` para que el agente remarketing override cadencia (stop/skip/accelerate/switch_schedule).
2. **F5.2** A/B test entre `default_b2c_v1` y `aggressive_low_ticket_v1` para identificar mejor baseline.
3. **F5.3** Dashboard de cadencia: timeline de attempts por cliente, conversion funnel por attempt_n.
4. **F5.4** Tuning: si `attempt_5.conversion_rate` <0.5% sostenido, recortar cadencia a 4 attempts. Si >2%, evaluar extender a 6.

---

## §8 Tests

### §8.1 Unit (puro, sin Temporal)

- `tests/platform/whatsapp/test_window.py` — todos los helpers: dentro/fuera, edge case `last_inbound_at_ms = None`, CTWA precedencia.
- `tests/platform/whatsapp/test_template_registry.py` — load catalog, validation de variables, missing template.
- `tests/platform/whatsapp/test_outbound_build_template.py` — payload Meta correcto, variable injection, ordering de components.
- `tests/plugins/chats/test_ingest_inbound_message.py` (extender) — verifica que se persisten `last_inbound_at_ms` + `service_window_expires_at_ms`. Verifica que se emite `ServiceWindowOpenedEvent` cuando episode está activo.
- `tests/platform/whatsapp/test_ingest_delivery_status.py` — persistir `pricing` en metadata, dedup por `wa_message_id`, race del webhook llegando antes del OutboundLogEntry.
- `tests/platform/whatsapp/test_cost.py` (nuevo) — `compute_message_cost_cents` con todos los casos: billable=false, free_customer_service, free_entry_point, regular marketing/utility/auth, missing category con warning. Verifica que `update_episode_cost_summary` mantiene invariantes (`total == sum(by_category)`, `count == billable + free + pending`).
- `tests/platform/whatsapp/test_rate_card.py` — load YAML, versioning, snapshot inmutable.

### §8.2 Workflow (Temporal testing framework)

- `tests/plugins/chats/workflows/test_watchdog_workflow.py`:
  - **test_fires_after_timer**: workflow duerme N segundos, activity send se ejecuta una vez.
  - **test_cancel_by_signal_before_fire**: signal `cancel_watchdog("customer_replied")` antes del timer → no se llama el send.
  - **test_reschedule_extends_timer**: signal `reschedule_watchdog(new_fire_at_ms)` antes del fire → nuevo timer.
  - **test_eligibility_skip**: si `check_eligibility` devuelve `False` → no se llama el send, se persiste outcome `skipped`.
  - **test_replay_determinism**: re-run history → mismas activities llamadas en mismo orden.

### §8.3 Functional E2E

- `tests/functional/test_watchdog_e2e.py` (`@pytest.mark.functional`):
  - GIVEN inbound del cliente → ventana abierta + watchdog programado para t+23.5h.
  - WHEN no hay respuesta → watchdog fires → template enviado → log de send capturado.
  - GIVEN cliente responde a t+1h → watchdog cancela → no template enviado.

### §8.4 Architecture / DEHA invariants

- `tests/architecture/test_watchdog_workflow_imports.py` — verifica que el workflow NO importa nada de `temporalio.client`, NO importa siblings (otros plugins), NO usa `datetime.now()`.
- `tests/plugins/test_premortem_invariants.py` (extender) — verifica que el manifest YAML del plugin chats valida (transitions correctas, event names en `__all__`).
- `uv run lint-imports` debe pasar limpio.

---

## §9 Observabilidad

### §9.1 Métricas (analytics events nuevos)

- `service_window_opened(session_id, episode_id, expires_at_ms)`
- `watchdog_scheduled(session_id, episode_id, fire_at_ms, template_kind)`
- `watchdog_cancelled(session_id, episode_id, reason)`
- `watchdog_fired(session_id, episode_id, template_name, wa_message_id)`
- `watchdog_skipped(session_id, episode_id, reason)`  // no eligible
- `wa_delivery_status(wa_message_id, status, pricing_type, category, billable, cost_cents_usd)`
- `wa_template_send_failed(wa_message_id, template_name, error_code, error_message)`
- `episode_cost_updated(session_id, episode_id, total_cents_usd, delta_cents_usd, category)` — cada vez que se materializa el costo de un outbound al recibir webhook.

### §9.2 Structlog log lines

Cada paso del workflow watchdog logea con `session_id` + `episode_id` + `watchdog_workflow_id` como context.

### §9.3 Dashboards / alertas (operativo)

- **Alerta:** ratio `watchdog_fired / watchdog_scheduled` debería ser <0.4 en estado sano (esperamos que la mayoría se cancele por respuesta del cliente). Si sube a >0.8 sostenido, la segmentación está mal (clientes que nunca responden).
- **Alerta:** ratio de delivery status `failed` o `error.code == 131049` (per-user marketing cap) > 5% — quemás quality rating.
- **Métrica clave:** `free_customer_service / total_sends` — qué % del volumen es gratis. Esperado >70%.
- **Cost metrics (nuevas):**
  - `avg_cost_per_episode_cents_usd` — promedio costo por episodio (cerrado y activo). Permite ver "cuánto cuesta convertir un lead". CAC efectivo si se cruza con conversions.
  - `avg_cost_per_won_episode_cents_usd` — costo promedio acotado a `closing_tag == COMPRA_EXITOSA`. La métrica que importa para ROI real.
  - `cost_per_message_p50_p95` — p50 y p95 del cost por mensaje. Si p95 sube de $0.0008 (utility) a $0.0125 (marketing), indica que estás operando más fuera de ventana de lo esperado.
  - `pending_pricing_ratio` — % de outbounds con `cost_cents_usd == null` (esperando webhook). Esperado <2% steady-state; sostener >10% indica problema con webhook delivery.
  - `cost_by_pricing_type_distribution` — desglose del spend por `pricing_type`. El target es que `free_customer_service` domine el count, `regular` domine el cost.

### §9.4 Vault inspección

Cada `metadata.json` tiene ahora `watchdog` y `last_outbound.pricing` — el dashboard puede mostrarlos por sesión.

---

## §10 Capability specs deltas

### §10.1 `messaging/spec.md` (adiciones)

```markdown
### Requirement: Service window tracking

El sistema MUST persistir el timestamp del último inbound (`last_inbound_at_ms`)
y derivar el cierre de ventana (`service_window_expires_at_ms = last_inbound_at_ms + 24h`)
en `metadata.json` cada vez que `IngestInboundMessage` procesa un inbound.

#### Scenario: Primer inbound del día
- GIVEN metadata sin `last_inbound_at_ms`
- WHEN llega un inbound a las 14:00
- THEN `last_inbound_at_ms = 14:00 epoch` y `service_window_expires_at_ms = 14:00 + 24h`

#### Scenario: Inbound consecutivo
- GIVEN `service_window_expires_at_ms = 14:00+24h`
- WHEN llega un inbound a las 18:00
- THEN `last_inbound_at_ms = 18:00 epoch` y `service_window_expires_at_ms = 18:00+24h`
- AND el watchdog workflow asociado recibe signal `reschedule_watchdog`

### Requirement: Template send

El sistema SHALL enviar mensajes template aprobados via `send_whatsapp_template_activity`,
que toma `(session_id, template_name, variables)` y persiste el OutboundSnapshot.

### Requirement: Delivery status capture

El sistema MUST procesar webhook `statuses[]` y persistir `pricing` snapshot
en `metadata.json[last_outbound][pricing]` para tracking de costo real.

### Requirement: Per-message cost computation + per-episode aggregation

El sistema MUST computar costo en cents USD para cada outbound usando el
rate card local versionado, y MUST mantener un `cost_summary` precomputado
por episodio para queries O(1).

#### Scenario: Outbound free dentro de ventana
- GIVEN cliente envió mensaje hace 2h (ventana abierta)
- WHEN bot envía free-form
- AND webhook `message_status` llega con `pricing.type=free_customer_service`
- THEN `OutboundLogEntry.cost_cents_usd = 0`
- AND `cost_summary.messages_free_count += 1`
- AND `cost_summary.total_cents_usd` no cambia

#### Scenario: Outbound marketing fuera de ventana (Colombia)
- GIVEN ventana cerrada hace 2 días
- WHEN bot envía marketing template
- AND webhook llega con `pricing.type=regular, category=marketing`
- AND rate card `co_2026q2_v1` tiene `marketing: 125 cents`
- THEN `OutboundLogEntry.cost_cents_usd = 125`
- AND `OutboundLogEntry.rate_card_version = "co_2026q2_v1"`
- AND `cost_summary.total_cents_usd += 125`
- AND `cost_summary.by_category.marketing.count += 1`
- AND `cost_summary.by_category.marketing.cents_usd += 125`

#### Scenario: Episode cierra → costo congelado
- GIVEN episodio cierra con `COMPRA_EXITOSA`
- WHEN `close_episode` ejecuta
- THEN `cost_summary` queda inmutable (no se modifican entries post-cierre)
- AND posteriores webhooks `message_status` del mismo episodio actualizan el log pero NO el summary (dead-letter al orphan log)
```

### §10.2 `plugins/chats/spec.md` (adición)

```markdown
### Requirement: Service window watchdog

El sistema SHALL programar un workflow `ServiceWindowWatchdogWorkflow` por
episodio activo, que dispara un utility template 30min antes del cierre de
ventana de servicio, salvo que el cliente haya respondido o el episodio haya
cerrado antes (cancelación por signal).
```

### §10.3 Bootstrap `agents/remarketing-worker/spec.md`

(Spec nueva — bootstrapeo incremental cuando el HU toca, según convention del proyecto.)

---

## §11 Out of scope / Open questions

### Out of scope
- **WABA Currency Migration a COP.** HU separada — requiere crear WABA nuevo y migrar phone.
- **Max Price feature integration.** Limited Beta vía BSP — requiere paso comercial primero.
- **Dashboard de costos / quality rating monitoring.** HU frontend separada.
- **Frequency capping local** (tracking de "este user recibió N marketing templates en 24h"). Hoy delegamos al cap global de Meta (#131049) — si se vuelve problema, HU separada.
- **A/B testing de copies de templates** — feature post-MVP.

### Open questions (a resolver antes/durante implementación)

1. **¿Quién resuelve `suggested_template_kind` en el evento `ServiceWindowOpenedEvent`?**
   - Opción A: `IngestInboundMessage` (el use case) — lee metadata.tag y mapea a template kind.
   - Opción B: `check_watchdog_eligibility_activity` (el workflow lo resuelve fresh al fire).
   - **Recomendación:** B. Más fresco (el tag puede cambiar entre el inbound y el fire 23.5h después).

2. **¿Hay BSP de por medio o se usa Cloud API directa?**
   - Los templates aprobados se gestionan via Meta Business Manager. La API de envío es la misma, pero si hay un BSP intermedio (Twilio / 360dialog), el endpoint cambia. **Necesario confirmar con operador.**

3. **¿Cómo se versiona un template?**
   - El catalog tiene `quote_ready_utility_v1`. Si Meta re-categoriza el template aprobado de utility a marketing, ¿el código del v1 sigue refiriéndose al mismo `waba_template_name`? **Decisión:** sí — el `waba_template_name` es el ID estable, el `v1` del nombre interno permite versionar el copy/semantics local sin re-aprobar Meta.

4. **¿Qué pasa si el cliente está en blackout hours?**
   - Algunos mercados restringen envío comercial nocturno. Meta no enforza — es responsabilidad del business. **Decisión:** out of scope. Si se vuelve issue, agregar `quiet_hours` al catalog y desplazar `fire_at_ms` al próximo horario válido.

5. **¿`continue_as_new` para el watchdog?**
   - El watchdog tiene a lo sumo ~3 signals (reschedules) + 1 fire + 1 cancel. History queda chica. **Decisión:** no necesita continue_as_new.

6. **Si fallan los 3 retries del template send, ¿qué?**
   - `send_whatsapp_template_activity` levanta excepción → workflow termina con error → Temporal lo registra. **Decisión:** persistir outcome `failed` antes de re-raise, para que el dashboard lo muestre. Sin reintentar más (el deal queda como "perdido").

---

## §12 Foundamento — Lead Response Management Study

Esta sección fundamenta las decisiones de §4.9 (cadencia adaptativa) en evidencia académica + benchmarks de la industria. No es relleno: cada parámetro del schedule (cuántos attempts, qué cadencia, qué orden de category) traza directo a un finding citado abajo.

### §12.1 El estudio original

**Autores:** James Oldroyd (MIT Sloan), Kristina McElheran (University of Toronto), David Elkington (InsideSales.com).

**Publicación:** "The Short Life of Online Sales Leads" — Harvard Business Review, marzo 2011. La fase inicial del estudio (también conocida como "Lead Response Management Study" cuando se popularizó la metodología) corrió 2004-2007 en InsideSales.com.

**Dataset:**
- Versión HBR (2011): **2,241 empresas estadounidenses + ~100,000 web-generated leads**.
- Versión original (2007): 15,000+ leads / 100+ companies / 3 años.

**Contexto:** B2B con outbound calling como canal primario. NO es WhatsApp ni B2C, pero los hallazgos sobre persistencia y decay del lead aplican como **principios** — los parámetros concretos hay que recalibrarlos al canal (lo hacemos en §13.4).

### §12.2 Hallazgos clave

| # | Finding | Implicación directa para Hubara |
|---|---|---|
| F1 | **Regla de los 5 minutos**: responder dentro de 5 min vs 30 min = **100x más probable conectar**, 21x más probable calificar | Ya está cubierto: el agente sales responde inmediato al inbound (workflow signal-driven). Reforzar: nunca dejar el primer turno con delay artificial. |
| F2 | **Regla de las 20 horas**: después de 20h, cada llamada adicional **REDUCE** la probabilidad de éxito | Justifica el watchdog (§4.2): el primer touch post-silencio debe caer ANTES del cierre de la ventana de 24h. Si pasaron >20h sin contacto, el siguiente intento es más caro y menos efectivo. |
| F3 | **Persistencia óptima recomendada: ~12 touches en B2B**. En la práctica, las empresas promedian **4.47 touches**. Solo el **9.4% de leads recibe los 12 recomendados**. | Las empresas hoy se rinden demasiado pronto. Si los competidores de los clientes Hubara hacen 1-2 follow-ups, hacer 5 sostenidos ya es ventaja competitiva. |
| F4 | **90% de la probabilidad de contacto se acumula al 6° intento** | El bulk del valor está en los primeros 5-6 intentos. Más allá hay retornos decrecientes fuertes. |
| F5 | **Decay por attempt**: cada call adicional baja la chance de calificar -5.05% y la de cerrar -2% | Convexidad inversa: vale la pena hacer los primeros 5; el #6 y siguientes ya son marginales. Refuerza nuestro corte en 5. |
| F6 | Reps abandonan ~30% de leads tras "unos pocos" intentos. Hacer N+1 contactos extra puede subir lead generation **+70%** | Magnitud del oportunity cost de NO hacer la cadencia completa. |
| F7 | **Mejores días**: miércoles y jueves. **Mejores horas**: 8-9 AM y 4-5 PM (timezone del lead) | Aplica al scheduling del cadence: si el `fire_at_ms` cae en domingo 3am hora del cliente, retrasarlo al próximo miércoles 9am (eligibility activity decide). |

### §12.3 Calibración para WhatsApp B2C (recalibración del paper)

El paper es B2B con cold-call. Para WhatsApp B2C/D2C (Hubara) hay 3 diferencias estructurales:

| Dimensión | B2B cold-call (paper original) | WhatsApp B2C (Hubara) | Implicación |
|---|---|---|---|
| Latencia natural entre touches | Días / semanas | Horas / días | Cadencia más comprimida (~21 días total vs 60-90 del paper). |
| Costo de "intentar otra vez" | Bajo (otra call del SDR) | Variable: $0 utility en ventana / $0.0125 marketing fuera + riesgo quality rating | Cada attempt extra tiene costo financiero. Refuerza cap en 5. |
| Friction al "no" del cliente | Bajo (rep simplemente sigue) | Alto: cliente puede bloquear número, reportar como spam → quality rating drop afecta TODOS los tenants del Business Manager | Eligibility check antes de cada attempt. Pause de toda la cadence si quality rating cae. |

**Benchmarks WhatsApp B2C (industry 2026)**:
- 3-5 mensajes en 7-14 días es el sweet spot para sequences automatizadas.
- 70% de las conversiones requieren 2-4 follow-ups (la mayoría de empresas envía solo 1).
- Cadence típica óptima: respuesta inmediata → seguimiento valor 24h → oferta limitada 72h. Mejora conversion 5% → 19%.
- Conversational commerce (WhatsApp + IA) llega a 45-60% conversion rate vs ~2% email.

### §12.4 Cómo se traduce el paper al schedule `default_b2c_v1` de §4.9

| Decisión del schedule | Trazabilidad al estudio / benchmark |
|---|---|
| **5 attempts max** (vs 12 del paper) | F5 (decay) + benchmark WA B2C (3-5). Margen: medimos en producción si subir a 6 mejora. |
| **Watchdog en t+23.5h** (attempt #1 dentro de ventana) | F2 (20h rule) — primer touch ANTES de que la ventana de 24h cierre, mientras el lead está "vivo". |
| **Attempt #2 en t+48h** | F4 (acumulación de contacto). Espaciado 1 día para no agotar quality rating. |
| **Attempts #3, #4 a 5d y 10d** | Espaciamiento progresivo. Mantiene engagement sin saturar. |
| **Attempt #5 (último) a 21d** | Cierre largo: el paper no es prescriptivo aquí, lo derivamos de benchmarks WA B2C (window típica 2-3 semanas) + costo marketing en COP ($0.0125). |
| **Escalation utility→marketing entre attempts #2-#3** | El costo per-message + el cap global Meta justifican empezar gratis (utility en ventana) y subir gradualmente. |
| **Horario permitido 08-22 hora cliente** | F7 (best hours). No es 8-9 / 16-17 exacto del paper porque B2C es más flexible, pero respetamos quiet hours. |
| **Pause global si quality rating cae** | Específico WhatsApp (no del paper). Protección de la plataforma entera. |
| **Stop cadence al primer reply del cliente** | F1 (response speed) — si el cliente responde, ese es el momento más valioso; toda la cadencia se cancela y el sales workflow toma. |

### §12.5 Tunable por tenant + medible

Cada parámetro del schedule debe ser **medible y tuneable**:

- `default_b2c_v1`, `aggressive_low_ticket_v1`, `luxury_long_v1` (definidos en §4.9.3) son starting points; los operadores pueden crear schedules custom.
- **Métrica de éxito por attempt_n**: `conversion_rate(attempt_n) = customers_who_replied_after_attempt_n / customers_who_received_attempt_n`.
- Si `conversion_rate(attempt_5) < 0.5%` sostenido durante 4 semanas → cortar a 4 attempts (cae bajo costo de oportunidad del paper F5).
- Si `conversion_rate(attempt_5) > 2%` sostenido → evaluar extender a 6 attempts y measure.

### §12.6 Out-of-scope crítico que el paper resalta

- **5-minute rule del primer touch** (F1): ya cubierto. Pero **monitorear**: si tu workflow sales tarda >2 min en responder al primer inbound, perdés 90% del lift. Métrica: `time_to_first_response_ms`.
- **Lead source matters**: el paper diferencia leads de form-fill vs trade-show vs cold. En WhatsApp el equivalente es CTWA vs direct vs web_referral (ya trackeado en `metadata.origin`). HU follow-up: A/B testear cadence schedule por `origin` (CTWA leads pueden tolerar más attempts porque ya mostraron intent fuerte).

### §12.7 Citas / referencias

- Oldroyd, J., McElheran, K., & Elkington, D. (2011). [The Short Life of Online Sales Leads](https://hbr.org/2011/03/the-short-life-of-online-sales-leads). *Harvard Business Review*.
- Lead Response Management Study (Dr. James Oldroyd / MIT Sloan + InsideSales.com, 2007). Executive Summary PDF.
- Industry benchmarks 2026: WhatsApp B2C conversational commerce — 3-5 messages en sequence drip, 70% conversions requieren 2-4 follow-ups, conversational hits 45-60% conversion.

---

## §13 Resumen ejecutivo (TL;DR para stakeholders)

Este HU agrega **cuatro capacidades acopladas** al sistema:

1. **Tracking de la ventana de 24h** de WhatsApp Cloud API (persistir `last_inbound_at_ms` + computar expiración) — sin esto cualquier estrategia de re-engagement está ciega.
2. **Watchdog durable per-conversación + template send** — un workflow Temporal por episodio activo, que duerme hasta 30min antes del cierre de ventana y dispara un utility template legítimo si hay deal abierto. Cancelable cuando el cliente responde.
3. **Cost tracking per-mensaje + agregado per-episodio** — rate card local versionado (Colombia 2026Q2 hoy), helper puro que convierte `pricing` webhook → `cost_cents_usd`, persistencia en `episodes[*].outbound_messages[]` + `cost_summary` agregado para queries O(1) de "cuánto costó este cliente / episodio / venta".
4. **Cadencia adaptativa de re-engagement** (5 attempts en 21 días) fundamentada en el Lead Response Management Study (Oldroyd, McElheran & Elkington, MIT/HBR 2011) recalibrado a WhatsApp B2C Colombia.

**Tamaño total:** 5 sprints de backend (~15-19 días-persona) + Sprint 0 operacional en paralelo.

**Secuencia recomendada (single-tenant, pre-launch):**
- **Sprint 0 (esta semana, no-código)** — aprobar templates con Meta, setup Business Manager, runbook de incident response.
- **Sprint 1 (semanas 1-2)** — foundation: timestamps + template send + cost tracking + webhook handler.
- **Sprint 2 (semanas 3-4)** — watchdog activo desde día 1 (no necesita "en sombra" con un solo tenant).
- **PAUSA semanas 5-8** — medir tráfico real, validar métricas, identificar pain points reales.
- **Sprint 3-4 (mes 3+)** — cadencia adaptativa con datos propios para tunear el schedule.

**Bloqueantes externos:**
- Templates utility aprobados por Meta (24-72h por template — gestionar Sprint 0 en paralelo a Sprint 1 dev).
- Copywriter humano para los templates (NO LLM-generated — la primera impresión define quality rating).
- Decisión moneda WABA (USD vs COP migration).

**Riesgo principal:** mis-categorización de templates por Meta (utility → marketing). Mitigación: el catalog explicita la categoría declarada y el code path no asume — el `pricing` capturado por webhook es la fuente de verdad post-facto, y el cost computation usa esa fuente, no la declarada.

**Quick wins medibles (en orden de aparición temporal):**
- **Post Sprint 1:** `pending_pricing_ratio < 2%` — webhook capture funcionando correctamente.
- **Post Sprint 2:** % de mensajes con `pricing.type == "free_customer_service"` > 70% — watchdog está manteniendo ventanas abiertas.
- **Post Sprint 3 (data en mano):** `avg_cost_per_won_episode_cents_usd` — CAC efectivo de WhatsApp por venta. Este es el KPI rey.
- **Post Sprint 4:** `cadence_attempt_5_response_rate` — valida si la cadencia genera valor incremental o sólo costo.

**Lo crítico no-código:** Sprint 0 NO es opcional. Sin templates aprobados, sin Business Manager configurado y sin runbook de incident response, los sprints técnicos están construyendo sobre arena.
