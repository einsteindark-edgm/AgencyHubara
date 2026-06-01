# REMARKETING_CADENCE_PLAN — Cadencia multi-touch + timing inteligente (A+B)

> **Estado:** NO empezado. Documento de handoff para atacar en otra sesión.
> **Autor del plan:** sesión de research (2026-05-29). Basado en el *Lead Response
> Management Study* (Oldroyd / MIT / InsideSales, 2007; HBR 2011 "The Short Life
> of Online Sales Leads") + auditoría del código vivo.
> **Scope:** dos features acopladas sobre el agente `remarketing`:
> **A)** cadencia multi-touch (persistencia / "resiliencia"), **B)** timing
> inteligente (quiet hours + franjas-oro).
> **HU asociada:** HU-WA24H-001 **Sprint 4** (la spec ya lo nombra como
> `RemarketingCadenceWorkflow`, hoy TBD).

---

## 0. START HERE (para la sesión que ataque esto)

1. Leé §1 (por qué) y §2 (qué existe hoy) — 5 min.
2. Leé §3 (las dos restricciones duras de WhatsApp) — esto invalida la regla
   literal del estudio ("9-10 intentos") y la reemplaza por "3-4 toques bien
   timed". No la saltees.
3. El diseño está en §4 (A) y §5 (B). Las **decisiones que necesitan al humano**
   están en §8 — resolvelas ANTES de codear (son números de negocio, no técnicos).
4. El mapa archivo-por-archivo está en §6. El plan de tests en §7.
5. Gotchas del codebase que YA nos quemaron están en §9. Leelos o los repetís.
6. Esto NO se mergea sin pasar `pytest -m architecture` + `lint-imports`
   (R-DIP) + actualizar la spec (§10).

**Comando de arranque sugerido (pipeline Archon):**
`archon workflow run hu-hubara-pipeline <issue-url>` — pero antes pegá este doc
en el issue para que el tech-refiner lo consuma.

---

## 1. Por qué (el hallazgo que justifica el trabajo)

El *Lead Response Management Study* probó dos cosas relevantes para nosotros
(números = hechos del estudio, no copia de su texto):

| Hallazgo | Dato | Implicación para Hubara |
|---|---|---|
| **Persistencia** | Subir a ~6 intentos mejora el contacto hasta ~80%. La mayoría se rinde al 1°-2° intento. Pasadas ~20h cada dial extra resta. | Hoy mandamos **1 nudge y nos rendimos** → estamos en el anti-patrón exacto. |
| **Hora del día** | Contactar: 4–6 PM (+114%). Calificar: 8–9 AM y 4–5 PM (+164% vs 1–2 PM). | El nudge actual puede salir a las 3am Colombia (mata quality rating). |
| **Día de semana** | Mié/Jue mejores (+49,7% contacto). Viernes peor. | Sesgar la cadencia hacia Mié/Jue. |
| **Inmediatez** | Responder en 5 vs 30 min → 21× calificar. | Aplica a leads NUEVOS (CTWA), **no** a re-engagement. Out of scope acá. |

**Jerarquía del estudio:** inmediatez ≫ hora del día > día de semana.

**El ángulo "resiliencia en ventas"** que motivó esto = el hallazgo de
persistencia. La traducción a nuestro sistema es: dejar de hacer one-and-done y
construir una secuencia de re-enganche.

---

## 2. Qué existe HOY (estado del código, con refs)

Tres piezas en el worker `remarketing` (`src/plugins/chats/agent/remarketing/`):

### 2.1 `RemarketingWorkflow` (conversacional, in-window) — EXISTE
- **File:** `workflows/remarketing.py` (clase `RemarketingSessionWorkflow`, `@workflow.defn(name="RemarketingWorkflow")`).
- **Trigger:** sales cierra con `tag=INTERESADO` → `manage_conversation_tag` emite
  `schedule_remarketing` → `SalesSessionCompletionEvent(tag=INTERESADO, delay_seconds=...)`
  → manifest transition `sales_to_remarketing_on_interested` → arranca el workflow
  con `start_delay`.
- **Comportamiento:** manda **UN** hook LLM (`build_remarketing_trigger_activity`),
  espera respuesta hasta `_IDLE_TIMEOUT = timedelta(hours=24)` (`remarketing.py:46`),
  y si el cliente responde → handoff a sales. Si no responde en 24h → **timeout
  silencioso, se rinde** (`remarketing.py:225-238`).
- **Gate de elegibilidad:** ya existe (`check_remarketing_eligibility`,
  `remarketing.py:159-178`) — aborta si `active_route=humano`. **Reusable para B.**

### 2.2 `ServiceWindowWatchdogWorkflow` (template, last-chance) — EXISTE (MOCK)
- **File:** `workflows/watchdog.py`. **Activities:** `activities/watchdog_activities.py`.
- **Comportamiento:** duerme hasta 30 min antes de cerrar la ventana 24h
  (`WATCHDOG_PRE_EXPIRY_MS = 30*60*1000`, `platform/whatsapp/window.py:30`), luego
  re-chequea elegibilidad y dispara **UN** utility template. **Send hoy es MOCK**
  (`watchdog_activities.py:386-418`, `MOCK_WATCHDOG_` prefix) — esperando Sprint 0
  (Meta template approval).
- **Tiene quiet hours** (`watchdog_activities.py:52-111`,
  `_is_quiet_hours_for_session`, 08:00–22:00 local, override por
  `WATCHDOG_QUIET_HOURS_{START,END}`). **Tiene timezone por country-code**
  (`_COUNTRY_CODE_TO_TZ`, `:64-72`). **Esto es la base de B — hay que extraerlo a
  platform.**
- **Cancelación:** signal `cancel_watchdog` desde `CustomerRepliedEvent` y
  `EpisodeClosedEvent` (manifest, `plugin.yaml:113-143`). **Patrón a espejar para A.**

### 2.3 `RemarketingCadenceWorkflow` (multi-touch, días) — **NO EXISTE**
- Solo está nombrado en la spec: `.hubara/specs/agents/remarketing-worker/spec.md:20-22`
  → *"secuencia de 5 attempts en 21 días post-watchdog... con escalation utility → marketing"*.
- `cadences.yaml` (mencionado en la spec `:147`) **no existe todavía** (confirmado
  con `find`). Hay que crearlo.

**Diagnóstico:** la infra de templates + cancelación + quiet hours + eligibility ya
existe (watchdog). La cadencia es, en gran parte, **generalizar el watchdog de "1
toque" a "N toques con schedule"** + extraer lo compartido a platform.

---

## 3. Las dos restricciones duras (NO saltear)

### 3.1 WhatsApp ≠ teléfono. El estudio mide llamadas; nosotros mandamos WhatsApp.
- **Ventana de servicio 24h** (`platform/whatsapp/window.py`): free-form gratis SOLO
  dentro de 24h del último inbound. Fuera → **solo templates aprobados por Meta**.
  La mayoría de los toques de cadencia caen FUERA de la ventana → **son templates**.
- **Ventana CTWA 72h** (`CTWA_WINDOW_MS`): si el lead vino de Click-to-WhatsApp Ads,
  cualquier mensaje es gratis 72h. Aprovechar si está abierta (`is_in_ctwa_window`).
- **Quality rating:** demasiados templates → Meta baja el rating → throttling/ban.
  Esto **invalida los "9-10 intentos" del estudio.** Cap real: ~3-4 toques.
- **Per-Message Pricing (jul 2025):** cada template cuesta. Utility ~$0.0008 USD,
  marketing ~$0.0125 USD en Colombia (ver `templates/catalog.yaml:83`). Modelar costo
  en `usd_micros` (ver memoria `cost_unit_lesson` — NO usar cents_int). Infra:
  `platform/whatsapp/cost`.

**Conclusión:** la *persistencia* del estudio se respeta; los *números* se reemplazan
por una cadencia corta, template-based, con frequency cap y kill-switch.

### 3.2 Persona "mínimamente invasiva"
- El `remarketing/workspace/SOUL.md` + `AGENTS.md` definen un agente
  deliberadamente NO insistente ("misión = un solo gancho, luego STOP"). Una
  cadencia agresiva **contradice la marca**. Resiliencia ≠ cansar.
- **Implicación:** la cadencia es de re-enganche espaciado (días), no de bombardeo.
  Los templates deben dar valor (cotización lista, recordatorio de pago, status),
  no spam. El `AGENTS.md` del remarketing debe actualizarse para reflejar que ahora
  hay una secuencia (hoy dice "un solo gancho").

---

## 4. (A) Diseño: `RemarketingCadenceWorkflow`

### 4.1 Mental model
La cadencia es el **motor de persistencia post-ventana, template-based**.
Complementa (no reemplaza) a las otras dos piezas:

```
INTERESADO (sales cierra)
   │
   ├─► RemarketingWorkflow ............ toque conversacional in-window (existe; fix delay en B)
   │
   ├─► ServiceWindowWatchdogWorkflow .. 1 template 30min antes de cerrar ventana (existe)
   │
   └─► RemarketingCadenceWorkflow ..... N templates en días, post-ventana (NUEVO ← este plan)
            touch 1 (T+1d) → touch 2 (T+3d) → touch 3 (T+7d) → ... → STOP (T+21d o reply o cierre)
```

> **DECISIÓN ABIERTA D1 (§8):** ¿la cadencia SUBSUME al watchdog (watchdog = touch 0
> de la cadencia) o coexisten? Recomendación: **coexisten** en v1 (menos riesgo,
> el watchdog ya está testeado), pero comparten quiet-hours + eligibility +
> frequency-cap + cost. Documentar la coordinación anti-doble-nudge (§4.6).

### 4.2 Forma del workflow (Temporal)
- **File nuevo:** `src/plugins/chats/agent/remarketing/workflows/cadence.py`.
- **Clase:** `RemarketingCadenceWorkflow`, `@workflow.defn(name="RemarketingCadenceWorkflow")`.
- **Patrón:** sleep-loop con cancelación (idéntico al watchdog, `watchdog.py:105-156`).
  NO hace falta continue-as-new: ~5 toques en 21 días = ~5 timers + ~10 activities,
  history chica. (Si algún día la policy crece a >50 toques, migrar a
  continue-as-new — documentarlo como nota inline.)
- **R-DET crítico:** igual que el watchdog, **NO** `from __future__ import annotations`
  (el DataConverter de Temporal hace `get_type_hints` dentro del sandbox y PEP 563
  rompe los tipos anidados). Usar `workflow.now()` para el tiempo, nunca
  `datetime.now()` / `time` / `random`. Gate todo con `workflow.patched("cadence-v1")`.

**Esqueleto:**
```python
@workflow.defn(name="RemarketingCadenceWorkflow")
class RemarketingCadenceWorkflow:
    def __init__(self) -> None:
        self._cancelled = False
        self._cancel_reason: str | None = None

    @workflow.signal
    async def cancel_cadence(self, payload: dict) -> None:
        # mismo shape que cancel_watchdog (payload dict con "reason")
        ...

    @workflow.run
    async def run(self, input: CadenceInput) -> None:
        if not workflow.patched("cadence-v1"):
            return
        touches = await workflow.execute_activity(resolve_cadence_policy_activity, ...)
        for i, touch in enumerate(touches):
            # 1. dormir hasta el próximo fire (alineado a franja-oro → ver B)
            fire_at_ms = await workflow.execute_activity(
                compute_next_touch_at_activity, args=[input.session_id, touch.offset_ms], ...)
            await self._sleep_until(fire_at_ms)   # wait_condition(cancel, timeout)
            if self._cancelled:
                await persist outcome "cancelled"; return
            # 2. re-chequear elegibilidad (defense-in-depth: pasaron días)
            elig = await workflow.execute_activity(check_cadence_eligibility_activity, ...)
            if not elig.eligible:
                # skip ESTE toque pero seguir la secuencia (o abortar según reason)
                continue / return  # ver D2
            # 3. enviar template (real send post Sprint 0; MOCK hoy)
            await workflow.execute_activity(send_cadence_touch_activity, ...)
            await persist outcome "fired" (touch i)
        # secuencia agotada sin reply → STOP definitivo
```

### 4.3 Contratos (R-JSON) — file nuevo `cadence_contracts.py`
Espejar `watchdog_contracts.py` (frozen dataclasses, **sin** `from __future__`):
```python
@dataclass(frozen=True)
class CadenceInput:
    session_id: str
    episode_id: str
    policy_name: str            # e.g. "default_b2c_v1"
    motivo: str = ""            # heredado del INTERESADO original
    started_at_ms: int = 0      # ancla para offsets relativos

@dataclass(frozen=True)
class CadenceTouch:             # una entrada resuelta de la policy
    index: int
    offset_ms: int             # delay desde started_at (o desde toque previo — ver D3)
    template_kind: str         # "quote_pending" | "payment_pending" | "order_status" | "cart_recovery"
    category: str              # "utility" | "marketing"
    align_golden_hour: bool = True

@dataclass(frozen=True)
class CadenceEligibilityResult: # idéntico shape a WatchdogEligibilityResult
    eligible: bool
    reason: Optional[str] = None
    resolved_template_name: Optional[str] = None
    resolved_template_variables: Optional[dict] = None
```

### 4.4 Policy file (data, no código) — `cadences.yaml`
- **File nuevo:** `src/platform/whatsapp/templates/cadences.yaml`.
- La spec ya nombra 3 policies: `default_b2c_v1`, `aggressive_low_ticket_v1`,
  `luxury_long_v1` (`spec.md:147`).
- Loader: extender `platform/whatsapp/templates/registry.py` con
  `get_cadence_policy(registry, name) -> list[CadenceTouch]` + factory cacheada en
  `platform/whatsapp/composition.py` (`get_cadence_registry`, `@lru_cache(1)` — R-STATELESS).

**Estructura propuesta (números = DECISIÓN ABIERTA D4):**
```yaml
policies:
  default_b2c_v1:
    max_touches: 4
    total_window_days: 21
    touches:
      - { offset: "1d",  template_kind: quote_pending,   category: utility,   align_golden_hour: true }
      - { offset: "3d",  template_kind: quote_pending,   category: utility,   align_golden_hour: true }
      - { offset: "7d",  template_kind: cart_recovery,   category: marketing, align_golden_hour: true }
      - { offset: "14d", template_kind: cart_recovery,   category: marketing, align_golden_hour: true }
```

### 4.5 Activities (R-STATELESS, todas <10s → sin heartbeat) — file nuevo `cadence_activities.py`
Reusar al máximo lo del watchdog:
| Activity | Qué hace | Reusa de |
|---|---|---|
| `resolve_cadence_policy_activity` | lee `cadences.yaml` → `list[CadenceTouch]` | nuevo + registry |
| `compute_next_touch_at_activity` | epoch ms del próximo fire alineado a franja-oro (B) | **nuevo, ver §5** |
| `check_cadence_eligibility_activity` | route≠humano + episodio abierto + quiet hours + frequency cap + resolver template | **copiar de `check_watchdog_eligibility_activity`** (`watchdog_activities.py:243-378`) |
| `send_cadence_touch_activity` | enviar template (MOCK hoy → `send_whatsapp_template_activity` post Sprint 0) | espejar `send_watchdog_template_activity` |
| `persist_cadence_outcome_activity` | escribir `metadata["cadence"]` (touches firados, cancel, skip) | espejar `persist_watchdog_outcome_activity` |

### 4.6 Frequency cap + anti-doble-nudge (NUEVO en este plan, era out-of-scope en Sprint 2)
- La spec Sprint 2 dice "Frequency capping local per-user — out of scope, confiamos
  en cap global de Meta" (`spec.md:148`). **Para la cadencia esto entra IN SCOPE**
  porque ahora NOSOTROS generamos múltiples toques.
- Implementar en `check_cadence_eligibility_activity`: leer
  `metadata["cadence"]["last_touch_at_ms"]` + `metadata["watchdog"]["fired_at_ms"]`
  y exigir un **min-interval** (e.g. ≥24h entre cualquier template saliente, sea de
  watchdog o cadencia). Esto evita que watchdog + cadencia disparen el mismo día.
- **Kill-switch:** env var `CADENCE_ENABLED` (default false), leído por activity en
  cada invocación (igual que `WATCHDOG_ENABLED`, `watchdog_activities.py:122-134`).
  Permite apagar la cadencia en un incidente de quality rating sin redeploy.

### 4.7 Cancelación (espejar watchdog exactamente)
- **Signal:** `cancel_cadence(payload: dict)` — mismo shape que `cancel_watchdog`
  (`watchdog.py:78-103`). Absorber NOT_FOUND en el dispatcher (ya lo hace, ver
  gotcha §9.2).
- **Workflow id template:** `cadence-{session_id}-{episode_id}` (per-episodio, igual
  que el watchdog).
- **Triggers de cancel:** `CustomerRepliedEvent` + `EpisodeClosedEvent` (ya existen,
  `events.py:135-177`). Agregar 2 transitions por worker en `plugin.yaml` (mirror de
  `*_cancels_watchdog_*`).

### 4.8 Trigger de arranque de la cadencia
> **DECISIÓN ABIERTA D5 (§8):** ¿cómo se arranca la cadencia?
> - **Opción 1 (recomendada):** nuevo evento `RemarketingCadenceRequestedEvent(session_id,
>   episode_id, policy_name)` emitido **cuando el watchdog dispara su template** (o
>   cuando la ventana cierra sin reply). Cadencia = "post-watchdog" (alineado con la spec).
> - **Opción 2:** arrancar la cadencia al mismo tiempo que `RemarketingWorkflow` (en
>   `SalesSessionCompletionEvent` tag=INTERESADO) pero con el primer toque scheduleado
>   post-ventana. Una sola fuente de trigger, pero hay que coordinar con el watchdog.
- Wiring: agregar el evento a `events.py` (`__all__`), declararlo en `emits:` de los
  dos workers en `plugin.yaml`, y agregar la transition `start_workflow_with_replace`
  → `RemarketingCadenceWorkflow`.

---

## 5. (B) Diseño: timing inteligente (quiet hours + franjas-oro)

### 5.1 Extraer quiet-hours a platform (refactor compartido)
- **Hoy** vive en `remarketing/activities/watchdog_activities.py:52-111`
  (`_COUNTRY_CODE_TO_TZ`, `_resolve_local_timezone`, `_is_quiet_hours_for_session`).
- **Mover a:** `src/platform/whatsapp/timing.py` (módulo nuevo, puro, sin I/O).
  Exponer: `resolve_local_timezone(session_id)`, `is_quiet_hours(session_id, now_utc,
  *, start, end)`. El watchdog pasa a importarlo de ahí (borra las privadas locales).
- **R-DIP:** `platform/` no importa `plugins/` → OK (el módulo es platform, lo
  consumen los plugins). `lint-imports` debe seguir verde.
- **Cuidado tzdata:** ZoneInfo puede tirar `ZoneInfoNotFoundError` en containers
  alpine — aplicar el MISMO fallback defensivo que `sales/context.py:28-44`
  (FIX #4 de la sesión previa). Para Colombia: `timezone(timedelta(hours=-5))`.

### 5.2 Franjas-oro (lo nuevo del estudio)
- **File:** `src/platform/whatsapp/timing.py` → `next_golden_hour_ms(now_ms, tz, *,
  min_delay_ms, policy) -> int`. **Función pura, unit-testable con `now` inyectado.**
- **Ventanas oro (del estudio):** 08:00–09:00 (calificar) y 16:00–18:00
  (contactar+calificar) hora local. Constantes overridables por env.
- **Sesgo de día:** preferir Mié/Jue; aceptable L-J; **viernes PM evitar**.
  > **DECISIÓN ABIERTA D6 (§8):** ¿fin de semana? El estudio (B2B phone) dice no, pero
  > B2C WhatsApp puede ser distinto. Default conservador: permitir sáb AM, evitar dom.
- **Algoritmo:** dado `now + min_delay`, avanzar al próximo instante que caiga en una
  ventana oro respetando el sesgo de día. Devolver epoch ms.

### 5.3 `compute_next_touch_at_activity` (dónde se usa B)
- **Por qué activity y no en el workflow:** el cálculo necesita `ZoneInfo` y math de
  calendario. El watchdog deliberadamente hace timezone en **activity**, no en el
  workflow (sandbox R-DET). Espejar: el workflow llama la activity, recibe un epoch ms,
  y duerme hasta ahí con `workflow.now()`. Mantiene el sandbox determinista y ZoneInfo
  afuera.
- Input: `(session_id, base_offset_ms)`. Output: `int` epoch ms (ya alineado a oro +
  fuera de quiet hours).

### 5.4 Aplicar timing al `RemarketingWorkflow` actual (el toque conversacional)
- **Problema hoy:** el primer hook puede salir a las 3am (no hay gate de hora).
- **Fix:** en `RemarketingWorkflow.run`, después del gate de elegibilidad existente
  (`remarketing.py:159-178`) y ANTES de encolar el `system_trigger_msg`
  (`remarketing.py:219-222`), agregar: si estamos en quiet hours, **dormir hasta la
  próxima franja-oro** (vía `compute_next_touch_at_activity`) en vez de mandar ya.
  Gate con `workflow.patched("remarketing-golden-hour-v1")` (replay-safe).

### 5.5 Resolver el `delay_seconds` 60s vs "1 hora"
- **Hoy:** `SalesSessionCompletionEvent.delay_seconds` default `= 60` (`events.py:61`)
  y `sales/tools/tags.py:232` emite `"delay_seconds": 60`. El comentario en tags.py
  dice "1 hora". **Discrepancia real.**
- El estudio NO aplica la regla de "5 minutos" a re-engagement (esa es para leads
  nuevos). Para re-enganche, un toque a los 60s se siente robótico (el cliente recién
  salía de la conversación).
- > **DECISIÓN ABIERTA D7 (§8):** valor del primer toque conversacional. Propuesta:
  > +1h a +3h (mismo día, probablemente aún in-window). Cambiar en AMBOS lugares.
  > Si se adopta golden-hour scheduling (§5.4), el delay base importa menos porque el
  > workflow re-alinea a la franja-oro de todos modos.

---

## 6. Mapa archivo-por-archivo

### Crear
| Archivo | Contenido |
|---|---|
| `hubara_agency/src/plugins/chats/agent/remarketing/workflows/cadence.py` | `RemarketingCadenceWorkflow` (§4.2) |
| `hubara_agency/src/plugins/chats/agent/remarketing/cadence_contracts.py` | DTOs frozen (§4.3) |
| `hubara_agency/src/plugins/chats/agent/remarketing/activities/cadence_activities.py` | 5 activities (§4.5) |
| `hubara_agency/src/platform/whatsapp/templates/cadences.yaml` | policies (§4.4) |
| `hubara_agency/src/platform/whatsapp/timing.py` | quiet-hours extraídas + franjas-oro (§5.1, §5.2) |

### Editar
| Archivo | Cambio |
|---|---|
| `.../remarketing/activities/watchdog_activities.py` | borrar quiet-hours privadas → importar de `platform/whatsapp/timing.py` (§5.1) |
| `.../remarketing/activities/__init__.py` | exportar las nuevas cadence activities |
| `.../remarketing/workflows/remarketing.py` | golden-hour gate antes del primer hook (§5.4) |
| `.../chats/shared/contracts/events.py` | `RemarketingCadenceRequestedEvent` + `__all__` (§4.8, según D5) |
| `.../chats/workers/remarketing.py` | registrar `RemarketingCadenceWorkflow` en `workflows=[...]` + las cadence activities en `activities=[...]` |
| `.../platform/whatsapp/templates/registry.py` | `get_cadence_policy(...)` |
| `.../platform/whatsapp/composition.py` | `get_cadence_registry()` `@lru_cache(1)` |
| `.../platform/whatsapp/window.py` | (opcional) constantes de franja-oro si no van en timing.py |
| `frontend_dashboard/src/plugins/chats/plugin.yaml` | `emits:` + transitions de cadencia (arranque + 2 cancel × 2 workers) — ver gotcha §9.4 sobre la ubicación del manifest |
| `sales/tools/tags.py` + `events.py` | resolver `delay_seconds` (§5.5, según D7) |
| `.../remarketing/workspace/AGENTS.md` | actualizar misión: ya no es "un solo gancho" (§3.2) |

### Spec (obligatorio antes de mergear)
| Archivo | Cambio |
|---|---|
| `hubara_agency/.hubara/specs/agents/remarketing-worker/spec.md` | mover `RemarketingCadenceWorkflow` de TBD (`:20-22`) a Requirements + Scenarios (§10) |

---

## 7. Plan de tests

> Recordá: tests NUNCA escriben al vault real (fixture autouse `_isolate_vault_dir`
> en `tests/conftest.py` redirige a `tmp_path`). Comando: `cd hubara_agency && uv run pytest -q`.

### Unit (puros)
- `timing.py`: `next_golden_hour_ms` con `now` inyectado en cada borde (3am → salta a
  8am; 14:00 → salta a 16:00; viernes PM → salta a lunes/mié; sáb/dom según D6).
- `timing.py`: `is_quiet_hours` por country code (57→Bogota, 54→Bs As, desconocido→UTC).
- `timing.py`: fallback tzdata (monkeypatch ZoneInfo → `ZoneInfoNotFoundError`) — copiar
  el patrón del test FIX #4 (`tests/.../test_medusa_order_command.py`).
- `registry.py`: `get_cadence_policy` parsea `cadences.yaml`, offsets en ms correctos.

### Integration (workflow + activities con env temporal de test)
- Cadencia happy path: 4 toques disparan en orden, cada uno alineado a franja-oro.
- Cancel por `CustomerRepliedEvent` a mitad de secuencia → no más toques, outcome
  "cancelled".
- Cancel por `EpisodeClosedEvent` → idem.
- Skip por `active_route=humano` en re-check → no envía, según D2 (skip vs abort).
- Frequency cap: watchdog firó hace 12h → cadencia skipea el toque (<24h min-interval).
- Kill-switch: `CADENCE_ENABLED=false` → eligibility `feature_flag_off`, no envía.
- `RemarketingWorkflow`: primer hook en quiet hours → duerme hasta franja-oro (gate
  `remarketing-golden-hour-v1`).

### Architecture gates (DEBEN pasar)
- `cd hubara_agency && uv run pytest -m architecture`
- `cd hubara_agency && uv run lint-imports` (R-DIP: cadence.py NO importa sibling
  workflows; tools NO importan `temporalio.client`).
- `cd hubara_agency && uv run pytest tests/plugins/` (premortem invariants).
- `tests/architecture/test_events_consistency.py` validará el evento nuevo (frozen,
  termina en `Event`, primer campo `session_id`).

---

## 8. Decisiones que necesitan al humano (resolver ANTES de codear)

| # | Decisión | Opciones | Default sugerido |
|---|---|---|---|
| **D1** | ¿Cadencia subsume al watchdog o coexisten? | subsume / coexisten | coexisten (v1) |
| **D2** | En re-check no-elegible: ¿skip el toque y seguir, o abortar la secuencia? | skip / abort | skip si reason transitorio (quiet hours), abort si terminal (humano/cerrado) |
| **D3** | Offsets ¿desde `started_at` (absolutos) o desde toque previo (relativos)? | absolutos / relativos | absolutos (más simple de razonar) |
| **D4** | Números de la policy `default_b2c_v1` (cuántos toques, qué días) | — | 4 toques: 1d/3d/7d/14d |
| **D5** | Trigger de arranque de la cadencia | evento post-watchdog / junto a remarketing | evento `RemarketingCadenceRequestedEvent` post-watchdog |
| **D6** | ¿Mandar fines de semana? | sí/no/solo sáb AM | solo sáb AM, evitar dom |
| **D7** | Valor del primer toque conversacional (60s → ?) | — | +1h a +3h |
| **D8** | ¿Templates marketing en la cadencia o solo utility? | solo utility / utility→marketing | utility primero, marketing tardío (requiere `confirm_marketing_send`, spec `:139-144`) |

---

## 9. Gotchas del codebase (los que YA nos quemaron)

### 9.1 Determinismo de workflows (R-DET)
- **NO** `from __future__ import annotations` en `cadence.py` ni `cadence_contracts.py`
  (el DataConverter de Temporal hace `get_type_hints` en el sandbox; PEP 563 rompe los
  tipos anidados). Ver el docstring de `watchdog.py:24-29`.
- Tiempo SOLO con `workflow.now()`. ZoneInfo/calendario → activity, nunca en el workflow.
- Todo el body gated con `workflow.patched("cadence-v1")` para rollout sin romper
  in-flight.

### 9.2 Signal a workflow ausente = NOT_FOUND (no crashea)
- Si se signaliza `cancel_cadence` a un workflow que no existe, Temporal tira gRPC
  NOT_FOUND. El dispatcher YA lo absorbe como noop (`_is_not_found`). Documentado en
  `plugin.yaml:104-108`. No re-introducir el crash.

### 9.3 Worker lambda missing import (runtime NameError)
- Al registrar la cadencia en `workers/remarketing.py`, confirmá que TODA clase/activity
  referenciada esté en los `from ... import ...` del top. El cuerpo de un lambda evalúa
  lazy → el `NameError` aparece en runtime de la activity, no al cargar. Detector
  determinista: `cd hubara_agency && uv run ruff check --select F821 src/`.
  (Ver memoria `worker_lambda_missing_import`.)

### 9.4 El manifest vive en el FRONTEND
- `transitions[]` se leen de `frontend_dashboard/src/plugins/<id>/plugin.yaml`
  (no en `hubara_agency/`). Confirmado: `plugin_manifest.py:33,64`
  (`_PLUGINS_MANIFEST_DIR = repo_root/frontend_dashboard/src/plugins`). Editar ahí.

### 9.5 Archon bash node stdout pollution + shell-quoting (si esto va por pipeline)
- Si la implementación pasa por un gate node de Archon: redirigir TODO diagnostic a
  stderr (`>&2`); stdout debe ser SOLO el status canónico single-line (el
  condition-evaluator hace strict `===`). Y en refs `$node.output` sin outer dquotes en
  el RHS. (Ver CLAUDE.md raíz gotchas #7 y #8.)

### 9.6 Cost en usd_micros, no cents
- Cada template cuesta sub-cent. Modelar en `usd_micros` (10^-6 USD). Tests unitarios
  con cents pasan auto-consistentes pero el bug aparece en el CLI/dashboard humano.
  (Ver memoria `cost_unit_lesson`, PR #20.)

### 9.7 Verificar COMPORTAMIENTO, no solo schema
- Esta es una HU de "el sistema EMITE N toques". Los tests deben verificar que los
  templates SE ENVÍAN (o se mockean con el `MOCK_` prefix y se cuentan), no solo que el
  workflow compila. (Ver memoria `backend_behavior_verification` — caso paradigmático:
  tests verdes, feature rota.)

### 9.8 Send real bloqueado por Sprint 0 (Meta template approval)
- El send es MOCK hasta que el operador apruebe los templates en Meta Business Manager
  (24-72h por template). Ver `catalog.yaml:1-22` + runbook
  `meta_template_approval.md`. La cadencia se puede construir + testear con MOCK; el
  swap a `send_whatsapp_template_activity` es one-line (igual que el watchdog,
  `watchdog_activities.py:392-403`).

---

## 10. Actualización de la spec (behavior contract)

Al implementar, mover `RemarketingCadenceWorkflow` de la lista TBD
(`spec.md:20-22`) a Requirements + Scenarios. Mínimo:

- **Requirement: La cadencia respeta route ownership + episodio activo** (igual que
  watchdog/remarketing — abortar si `active_route=humano` o episodio cerrado).
- **Requirement: La cadencia respeta franjas permitidas** (no disparar en quiet hours;
  alinear a franja-oro).
- **Requirement: Frequency cap entre watchdog y cadencia** (min-interval ≥24h entre
  templates salientes).
- **Requirement: La cadencia se cancela on reply / on episode close.**
- **Requirement: Kill-switch global** (`CADENCE_ENABLED`).
- Cada uno con su `#### Scenario:` GIVEN/WHEN/THEN (ver el estilo en
  `spec.md:37-103`).

El `hubara-tech-refiner-archon` escribe deltas en `.hubara/specs/.../spec-deltas/`;
el `hubara-archive-hu` los mergea al cerrar la HU.

---

## 11. Secuenciación sugerida (PRs)

1. **PR1 — B base (refactor):** extraer quiet-hours a `platform/whatsapp/timing.py` +
   `next_golden_hour_ms` + tests unit. Watchdog pasa a importarlo. Sin cambio de
   comportamiento observable. (Bajo riesgo, desbloquea todo lo demás.)
2. **PR2 — B aplicado a remarketing actual:** golden-hour gate en `RemarketingWorkflow`
   + resolver `delay_seconds` (D7). Arregla el nudge-a-las-3am hoy mismo.
3. **PR3 — A esqueleto:** contracts + `cadences.yaml` + registry/composition + las 5
   activities (con send MOCK). Workflow `RemarketingCadenceWorkflow` + registro en worker.
4. **PR4 — A wiring:** evento + transitions en `plugin.yaml` + cancelación. Tests
   integration end-to-end (MOCK send).
5. **PR5 — frequency cap + kill-switch + spec update.**
6. **(post Sprint 0)** swap MOCK → real `send_whatsapp_template_activity`.

---

## 12. Referencias

### Código (paths reales auditados 2026-05-29)
- `hubara_agency/src/plugins/chats/agent/remarketing/workflows/remarketing.py` — conversacional in-window
- `.../remarketing/workflows/watchdog.py` — patrón sleep-loop + cancel a copiar
- `.../remarketing/activities/watchdog_activities.py` — quiet-hours + eligibility + template a reusar
- `.../remarketing/watchdog_contracts.py` — patrón de DTOs frozen
- `hubara_agency/src/platform/whatsapp/window.py` — ventanas 24h/72h, constantes
- `hubara_agency/src/platform/whatsapp/templates/catalog.yaml` — templates (utility + marketing)
- `hubara_agency/src/platform/orchestration/transitions.py` — semántica del manifest
- `frontend_dashboard/src/plugins/chats/plugin.yaml` — manifest real (transitions)
- `hubara_agency/.hubara/specs/agents/remarketing-worker/spec.md` — behavior contract

### Estudio
- Lead Response Management Study (leadresponsemanagement.org/lrm_study)
- MIT/InsideSales study PDF (mortech.com/.../mit_study.pdf)
- HBR 2011 "The Short Life of Online Sales Leads" (Oldroyd, McElheran, Elkington)

### Memorias del proyecto relevantes
- `cost_unit_lesson` (usd_micros), `backend_behavior_verification`, `worker_lambda_missing_import`,
  `whatsapp_business_context`, `remaining_sprints` (Sprint 4 = esta HU).
