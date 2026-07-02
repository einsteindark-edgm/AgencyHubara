# Refinamiento técnico — Bandeja durable con *watermark* + interrupción de turno + reply-to/vistos

> **PR1 (este refinamiento)**: núcleo determinista (Fase 0 + Fase 1 + Fase 3) **+** reply-to / mark_as_read.
> **Follow-ups (fuera de alcance)**: loop de confirmación de entrega + reenvío ante `failed`/timeout; mover la mutación de episodios/ventana adentro de una activity dueña.

Origen: análisis del run `019f1b65-e6f5-7e14-b49b-907e1047e00e`, sesión `session-wa_573125671604`
(el cliente reenvía ráfagas y el bot solo procesa uno). Causas raíz confirmadas leyendo el código vivo.

---

## 0. Diagnóstico (por qué "solo ve uno")

Hay **dos historiales separados**: el JSONL del dashboard (`FilesystemMessageHistoryStore`, solo display) y la
memoria del LLM (`DefaultConversation` de exoclaw). **El LLM ve el mensaje del turno únicamente por el argumento
`message` de `build_prompt`, que viene del signal coalescido.** → *signal perdido o no coalescido = mensaje que el
bot nunca lee, aunque el dashboard lo muestre.*

| # | Causa raíz | Evidencia | Fase que la mata |
|---|---|---|---|
| ① | Race de arranque: `get_handle → describe → start_workflow → signal` no atómico → `WorkflowAlreadyStartedError` en ingests concurrentes → **signals perdidos** | `load_or_start_sales_session.py:289-329` | 0 (`signal_with_start`) |
| ② | Mensajes que llegan **durante** el turno LLM en vuelo → se difieren a un turno fragmentado; el bot responde el primero ignorando el resto | `sales_session.py:273-291` (turno no interrumpible) | 1 (interrupción) |
| ③ | `metadata.json` read-modify-write racy entre N background tasks concurrentes | ingest muta metadata fuera del workflow | 3 (workflow dueño del orden) |
| ④ | La "cola" (`_pending`) es efímera y sin identidad → no hay forma provable de saber a qué se respondió | `sales_session.py:102` | núcleo (watermark + coverage) |

---

## 1. La idea central: bandeja append-only + *watermark*

Reemplazar `self._pending: list[PendingMessage]` (buffer efímero) por un **log de entrada durable con marca de agua**.

**Invariante (lo que da todo lo pedido):**
> Un inbound del cliente está **respondido** ⇔ `seq ≤ _acked_seq`. Del `_acked_seq+1` en adelante está **pendiente**,
> garantizado. `_acked_seq` **solo avanza cuando el envío al cliente confirma OK.**

Consecuencias:
- **Determinismo del seguimiento**: en cualquier instante se sabe, por `seq`, qué wamids están respondidos y con qué salida.
- **Cero pérdida / cero doble-respuesta**: si el worker crashea a mitad de turno, Temporal reanuda; el rango pendiente
  `(_acked_seq, high_water]` es idéntico → se recomputa igual.
- **Interrupción trivialmente segura**: mientras un turno está en vuelo, el watermark **no** avanzó → cancelar y
  recomputar no puede perder ni duplicar nada.

---

## 2. Contratos / modelo de datos (R-JSON)

Nuevos dataclasses planos JSON-serializables. Ubicación: `src/platform/workflow_helpers.py` (junto a `PendingMessage`,
que se mantiene por compat de replay/coalesce interno).

```python
@dataclass
class InboxMsg:
    seq: int
    wamid: str | None            # message_id de Meta (para reply-to / mark_as_read)
    text: str
    ts_ms: int
    media: list[str] | None = None
    plugin_context: list[str] | None = None
    is_handoff: bool = False     # preserva el path Remarketing→Sales existente

@dataclass
class CoverageRecord:
    response_wamid: str | None   # wamid de la salida del bot (para reenvío/analytics)
    covers_seq_lo: int           # rango contiguo cubierto: (lo..hi] inclusive-hi
    covers_seq_hi: int
    covers_wamids: list[str]
    sent_at_ms: int
```

**Extensión de `SalesSessionInput`** (`sales/contracts.py`) — nuevos campos con default para replay-safety y para que
`continue_as_new` preserve la bandeja:

```python
@dataclass(frozen=True)
class SalesSessionInput:
    session_id: str
    turn_count: int = 0
    runtime_workspace_path: str | None = None
    # NUEVO (watermark-inbox-v1) — todos con default → histories viejas deserializan OK:
    seq_counter: int = 0
    acked_seq: int = 0
    carryover_inbox: list[dict] = field(default_factory=list)   # InboxMsg pendientes (seq > acked)
    coverage_tail: list[dict] = field(default_factory=list)     # últimos N CoverageRecord (auditoría)
```
> `list[dict]` (no `list[InboxMsg]`) para que el frozen dataclass serialice sin converters custom; el workflow
> re-hidrata a `InboxMsg`/`CoverageRecord` al arrancar. Cap de `coverage_tail` a los últimos ~20 (auditoría acotada).

**Firma del signal nuevo** (el ingest ya no manda `send_message` con solo texto):

```python
@workflow.signal
async def enqueue_inbound(
    self, wamid: str | None, text: str, ts_ms: int,
    media: list[str] | None = None, plugin_context: list[str] | None = None,
) -> None:
```
> `send_message` (firma vieja) **se mantiene** como signal handler para runs pre-deploy en vuelo → appendea a la
> bandeja con `wamid=None, ts_ms=workflow-side`. Sin esto, un signal viejo en la cola rompería el replay.

---

## 3. Cambios por archivo

### 3.1 `ingest_inbound_message.py` + `load_or_start_sales_session.py` — Fase 0 (thin + atómico)
- El ingest pasa `wamid` (=`parsed.message_id`) y `ts_ms` a `LoadOrStartSalesSession.execute(...)`.
- Reemplazar el bloque manual `load_or_start_sales_session.py:289-329` por **`signal_with_start`**:

```python
await client.start_workflow(
    HubaraSalesSessionWorkflow.run,
    SalesSessionInput(session_id=session_id, runtime_workspace_path=runtime_path),
    id=f"session-{session_id}",
    task_queue=get_task_queue("chats", "sales"),
    start_signal="enqueue_inbound",
    start_signal_args=[wamid, message, ts_ms, None, plugin_context],
    id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
)
```
> Precedente en repo: `eta_session.py` y `orchestration/dispatcher.py:411-428` ya usan `signal_with_start`.
> Los paths **remarketing** y **humano** (`load_or_start...:159-260`) se dejan **intactos** en PR1 (scope).
> El path plugin-route-registry idem (usa `handle.signal` sobre workflow ya running — no hay race de arranque ahí).

### 3.2 `sales_session.py` — Fases 1 + 3 + núcleo (el grueso)
Todo el bloque nuevo detrás de `workflow.patched("watermark-inbox-v1")`; los runs en vuelo caen al path actual
(`_pending` + debounce legacy) hasta drenarse por idle 1min / continue_as_new.

Estado nuevo en `__init__`: `self._inbox: list[InboxMsg]`, `self._seq_counter`, `self._acked_seq`,
`self._coverage: list[CoverageRecord]` (re-hidratados desde `input` al arrancar `run`).

Loop nuevo (reemplaza `wait_condition + debounce + coalesce`, `sales_session.py:180-272`):

```
while True:
    await wait_condition(lambda: high_water() > self._acked_seq, timeout=idle)   # ghosting igual que hoy
    # (debounce corto opcional: 1 pasada de gracia para agrupar la ráfaga inicial)
    lo, hi = self._acked_seq, high_water()
    batch = [m for m in self._inbox if lo < m.seq <= hi]
    coalesced = coalesce_inbox(batch)                      # concat ordenado por seq + nota de ráfaga
    # mark_as_read del wamid más alto del batch (marca ese y anteriores)
    await execute_activity(mark_as_read_activity, session_id, batch[-1].wamid, ...)  # best-effort

    # --- Fase 1: turno interrumpible ---
    turn = asyncio.ensure_future(run_agent_turn(session, coalesced))
    newer = asyncio.ensure_future(wait_condition(lambda: high_water() > hi))
    await workflow.wait([turn, newer], return_when=FIRST_COMPLETED)
    if not turn.done() and interrupts_used < _MAX_INTERRUPTS:
        turn.cancel()                                      # abandona el LLM en vuelo (costo ya gastado)
        interrupts_used += 1
        continue                                           # NO toca _acked_seq → recomputa (lo, hi)
    result = await turn                                    # si ya llegó al cap, deja terminar

    # send (reply-to al wamid más reciente del batch) + persist
    out = await execute_activity(send_whatsapp_reply_activity,
                                 session_id, result.final_content, batch[-1].wamid, ...)
    # ... (pre_tool_messages, flush UI intents, escalation, redes de seguridad: IGUAL que hoy) ...

    # --- avanzar watermark SOLO tras enviar OK ---
    self._acked_seq = hi
    self._coverage.append(CoverageRecord(out.wa_message_id, lo, hi,
                                         [m.wamid for m in batch if m.wamid], now_ms))
    prune_inbox(self._inbox, self._acked_seq)              # descarta seq <= acked
```

- `high_water()` = `self._inbox[-1].seq if self._inbox else self._acked_seq`.
- `_MAX_INTERRUPTS` (p.ej. 2): evita loop infinito si el cliente teclea sin parar → al llegar al cap deja terminar y
  el resto se difiere a la próxima vuelta (nunca se pierde, por el invariante).
- **Cancelación de activity**: `run_agent_turn` corre `llm_chat` con `cancellation_type=TRY_CANCEL` (o `ABANDON`);
  al `turn.cancel()`, el workflow deja de esperar el resultado y lo descarta. La llamada LLM en curso puede terminar
  server-side (costo ya incurrido) — **aceptable y documentado**; no hay kill real del proveedor.
- `continue_as_new` (`sales_session.py:724`): ahora serializa `seq_counter/acked_seq/carryover_inbox/coverage_tail`
  a `SalesSessionInput`. Solo cuando `not self._inbox_has_pending()` (no cortar a mitad de ráfaga).

### 3.3 `workflow_helpers.py` — coalesce + nota de ráfaga
- `coalesce_inbox(batch: list[InboxMsg]) -> PendingMessage`: espejo de `coalesce_pending` pero ordenando por `seq`,
  preservando el manejo de `is_handoff` existente (L-12), y **añadiendo la nota de conciencia de ráfaga** al
  `plugin_context` cuando `len(user_msgs) > 1`:

```
[CONTEXTO DE TURNO] El cliente te escribió N mensajes seguidos desde tu última respuesta.
Respondé al conjunto como un solo hilo coherente, sin ignorar ninguno ni contestar solo el último:
  1) "..."  2) "..."  ...
```
> Determinista (sale del inbox ordenado). Va a `plugin_context`, **no** al rol user (no contamina el JSONL/memoria).

### 3.4 `whatsapp/activities.py` + worker registration — reply-to / vistos
Dos activities **nuevas** (nombres nuevos → no altera history de las existentes, L-9):

- `send_whatsapp_reply_activity(session_id, message, reply_to_wamid) -> OutboundResult`
  usa el **path rico** `client.send_text(..., reply_to_message_id=reply_to_wamid)` (que ya existe y devuelve
  `OutboundResult(wa_message_id, ok, error)`). Fragmenta en burbujas por `\n\n` **pero el `context`/quote solo va en
  la PRIMERA burbuja** (citar una vez). Devuelve el wamid de la primera para `CoverageRecord`.
- `mark_as_read_activity(session_id, wamid) -> None`: POST `{messaging_product, status:"read", message_id: wamid}`
  (misma API que `send_typing_indicator`; best-effort, noop si falta wamid/token). Marca ese mensaje y anteriores.

Registro en `chats/workers/sales.py`: **agregar ambas al `activities=[...]`** en el mismo commit (L-3 — si no, muere
en runtime en la primera conversación real con `NotFoundError`). Verificar `F821` (gotcha #6 / worker lambda import).

> El `send_whatsapp_message_activity` legacy se mantiene (lo usan pre_tool_messages y otros paths). PR1 solo cambia
> el send del `final_content` principal al nuevo con reply-to.

---

## 4. Reglas duras aplicables

- **R-DET**: `seq` se asigna en el signal handler (orden de signals garantizado por Temporal) → puro en replay. El
  race turno-vs-newer se resuelve por `workflow.wait` (evento ganador queda en history). Todo gated por `patched`.
- **R-JSON**: `InboxMsg`/`CoverageRecord`/campos nuevos de `SalesSessionInput` son dataclasses planos; carryover viaja
  como `list[dict]`.
- **R-HEARTBEAT**: `llm_chat` ya corre bajo `_LLM_OPTIONS`; para que `TRY_CANCEL` sea observable conviene confirmar
  heartbeat (si no lo tiene, la cancelación igual desengancha el workflow; solo no mata el call remoto).
- **R-DIP**: sin imports cross-agente nuevos; `signal_with_start` por nombre de signal (string), igual que hoy.

---

## 5. Tests por capa (TDD rojo → verde). Cada rojo asserta comportamiento observable.

1. **Race de arranque (Fase 0)** — *rojo que reproduce el bug*: 2 llamadas concurrentes a `LoadOrStartSalesSession`
   sobre un workflow inexistente. Con el código actual, una levanta `WorkflowAlreadyStartedError` y su signal se pierde.
   Verde: con `signal_with_start`, **ambos** inbounds quedan en la bandeja (assert 2 `InboxMsg`). *(fake client / o
   test de integración con Templifake si aplica.)*
2. **Coalesce + watermark determinista** (`workflow_helpers` puro): batch de 4 `InboxMsg` con seq 1..4 → `coalesce_inbox`
   concatena en orden + inyecta la nota de ráfaga; `CoverageRecord` cubre `(0,4]` y los 4 wamids. Cero doble-conteo.
3. **Interrupción segura (Fase 1)** (workflow env / replay): mensaje llega con `seq > hi` mientras el turno corre →
   `_acked_seq` NO avanza, el turno se cancela y se recomputa cubriendo los N mensajes en UNA sola respuesta.
4. **Cap de interrupciones**: ráfaga infinita → tras `_MAX_INTERRUPTS` el turno termina y difiere el resto (assert no
   loop, assert nada perdido).
5. **No pérdida bajo crash** (replay determinismo): pendientes con `seq > acked` sobreviven `continue_as_new`
   (carryover) y un replay del history no cambia `_acked_seq`.
6. **reply-to / vistos** (activity): `send_whatsapp_reply_activity` llama `client.send_text` con
   `reply_to_message_id` = wamid del último inbound, solo en la 1ª burbuja; `mark_as_read_activity` postea `status:read`.
7. **Guard de determinismo**: history pre-patch replayea por el path legacy (`patched()` False) sin `NondeterminismError`.

Panel determinístico antes de cerrar: `/hubara-gates backend` (R-DIP, arquitectura, cert, CLI) + `ruff --select F821`.

---

## 6. Deploy / replay

- Todo detrás de `workflow.patched("watermark-inbox-v1")`; runs `session-*` en vuelo drenan por idle 1min / CAN. No
  requiere terminate masivo (a diferencia de ETA/remarketing) porque el estado durable de la conversación (memoria LLM)
  vive en el store de exoclaw, no en `_pending`.
- Alternativa si se prefiere no arrastrar el path legacy: drenar (`terminate`) los `session-*` en el rollout — pero
  cortaría conversaciones vivas; **no recomendado**. Preferir `patched`.
- Nuevas activities registradas en el worker **en el mismo commit** (L-3).

## 7. Decisiones abiertas (para vos)

- **D1 — [DECIDIDA: al *send OK*].** `_acked_seq` avanza cuando Meta acepta el envío (más simple). El modo estricto
  (avanzar recién al webhook `delivered`) queda para el follow-up de reenvío.
- **D2 — [RESUELTA — reply-to guiado por coverage, NO "citar siempre"].** Citar en cada respuesta es confuso. Regla:
  el reply-to es la manifestación **visible del mapa de coverage del episodio**. Se cita **solo cuando desambigua** —
  cuando el bot responde a un inbound del histórico del episodio que **NO es el inmediatamente anterior** (retomó un
  mensaje viejo aún no cubierto, o hubo mensajes intercalados). En el caso fluido normal (responde ya al último
  mensaje), **no cita** → sin ruido visual. La decisión sale del `CoverageRecord`: si el `seq` del inbound que se está
  respondiendo no es contiguo al último respondido, se quotea ese wamid. *(Interpretación del feedback del operador;
  corregir si el sentido era otro.)*
- **D3 — [DECIDIDA: defaults].** `_MAX_INTERRUPTS = 2` + 1 pasada de debounce corto (~1.5–2s) para agrupar la ráfaga
  de apertura antes del primer turno.
