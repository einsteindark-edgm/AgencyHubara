"""Informe para el Estratega del ciclo Colossus (2026-11-03).

El Estratega propone cada semana hasta 3 cosas para que Colossus (el bot
nuevo: workflow v2 con Jev) lleve más conversaciones sin el equipo y venda
más. Solo ve cifras agregadas de producción: este informe las arma del vault
y de OrderFacts, sin textos, sin números de clientes y sin ids de
conversación. Compara la ventana reciente (`--dias`) con la de antes
(`--base`), por bot: `colossus` (v2), `botsito` (v1), `mixto` (un episodio
con turnos de los dos) y `sin_bot` (el bot no contestó ningún turno).

Unidad: el episodio de venta (`metadata.json → episodes[]`), ubicado en su
ventana por cuándo empezó (UTC). En producción, dentro del worker `sales_eval`:

    python -m src.plugins.chats.agent.sales_eval.strategy --dias 7 --base 28 [--desde AAAA-MM-DD] [--json]
"""
from __future__ import annotations

import argparse
import asyncio
import bisect
import json
import re
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales.decisions.decision_log import read_decisions
from src.plugins.chats.agent.sales.turn_trace import STAGES, project_stage
from src.plugins.chats.agent.sales_eval.bulk_handoffs import BULK_DATA, jev_team_reader
from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.shared import turn_traces
from src.plugins.chats.shared.receipt_checks import read_checks

BOTS = ("colossus", "botsito", "mixto", "sin_bot")
WINDOWS = ("actual", "base")
_BOT_OF_EPISODE = {"nuevo": "colossus", "actual": "botsito", "mixto": "mixto"}
#: Relevo sin motivo del bot: el equipo tomó la conversación desde el panel o la app.
TEAM_TOOK_OVER = "TOMADO_POR_EL_EQUIPO"
NO_REASON = "SIN_MOTIVO"
#: Lo que pasó antes del primer turno del bot en el episodio.
NO_BOT_TURN = "sin_turnos_del_bot"
_ROUTE_HUMAN = "humano"
#: Solo códigos (motivos, orígenes, capacidades) llegan al informe: un valor con
#: forma de texto libre se cuenta como `otro`, nunca se copia.
_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_:.-]{0,63}$")
#: Relevo de rutina: el bot registró el pedido y el equipo verifica el pago.
PAYMENT_CHECK = "PAYMENT_VERIFICATION_PENDING"
NORTH_STAR = (
    "Conversaciones en las que intervino el equipo (al menos un relevo o un mensaje del equipo) por cada 100 "
    "conversaciones de la ventana. «relevos_sin_verificar_pago_por_100» deja fuera la verificación del pago de un "
    "pedido que ya registró el bot. Bajar es mejor."
)
#: La decisión que anota la objeción de cada mensaje del cliente (paquete
#: ventas-9) y sus tipos (lista cerrada). `NO_OBJECTION`: Jev leyó el mensaje y
#: no había objeción.
OBJECTION_CAPABILITY = "objecion"
OBJECTION_KINDS = ("precio", "envio", "tiempo_entrega", "confianza", "producto")
NO_OBJECTION = "ninguna"
#: Lo que dice la sección mientras no hay ninguna nota en las dos ventanas.
OBJECTIONS = {
    "disponible": False,
    "motivo": (
        "No hay ninguna objeción anotada en las ventanas del informe: la decisión «objecion» (paquete ventas-9) "
        "no ha leído ningún mensaje de estas conversaciones."
    ),
    "faltante": (
        "Promover un paquete que traiga «objecion» (ventas-9 o posterior) y encenderla en sombra por comando. "
        "Los números de prueba no cuentan en este informe."
    ),
}
OBJECTIONS_NOTE = (
    "Por cada mensaje del cliente, Jev anota si puso una objeción y de qué tipo (solo anota: no cambia lo que el "
    "bot responde). «lecturas»: mensajes (no turnos) que Jev leyó, con objeción o sin ella; «sin_lectura»: Jev "
    "contestó sin llegar al umbral o falló (si se demoró más que el tope del ingest no queda anotado). Cuenta "
    "todas las conversaciones de clientes, también las de Botsito. Por tipo, en conversaciones: «siguieron_hacia_la_compra» = después de la primera objeción de ese "
    "tipo la conversación avanzó de etapa; «terminaron_en_pedido» = la conversación tiene un pedido (OrderFacts)."
)
JEV_NOTE = (
    "Solo las decisiones en las que Jev participó (proveedor jev o sombra). «jev_dudo»: Jev no llegó al umbral "
    "y decidió la regla; «valor_duda»: la capacidad respondió «duda». En sombra la duda no queda registrada: "
    "esas decisiones se cuentan en «en_sombra»."
)

#: Estados del pedido de un episodio según OrderFacts.
ORDER_PAID, ORDER_PLACED, ORDER_CANCELLED, ORDER_TEST, ORDER_UNCONFIRMED = (
    "pagado", "pedido", "cancelado", "de_prueba", "sin_confirmar",
)
#: Comprobantes de pago en sombra (tarjeta 33 del ciclo): lo que Colossus dijo
#: de cada comprobante (`sales/use_cases/receipt_check.py`), contra lo que el
#: equipo hizo con ese pedido. Listas cerradas: lo demás se cuenta como «otro».
RECEIPT_MATCH, RECEIPT_MISMATCH = "coincide", "no_coincide"
RECEIPT_VERDICTS = (RECEIPT_MATCH, RECEIPT_MISMATCH, "ilegible", "sin_pedido")
RECEIPT_REASONS = ("monto", "fecha_vieja", "cuenta_destino", "repetido", "pedido_no_leido")
RECEIPT_EVIDENCE = ("completa", "parcial")
RECEIPT_SOURCES = ("image", "pdf")
#: Con menos pedidos comparados que estos el porcentaje de coincidencia engaña.
RECEIPTS_MIN_TO_MEASURE = 40
RECEIPTS_NOTE = (
    "Sombra: Colossus anota qué diría de cada comprobante y no cambia nada para el cliente ni el equipo. "
    "«contra_el_equipo» compara el último veredicto de cada pedido con lo que el equipo hizo: pagado = lo aceptó, "
    "cancelado = no lo aceptó; un pedido que sigue pendiente queda sin resolver. El porcentaje solo sale con "
    f"{RECEIPTS_MIN_TO_MEASURE} pedidos comparados o más. «coincide_por_evidencia»: completa = monto, cuenta nuestra "
    "y fecha reciente a la vista; parcial = solo cuadró el monto. Los pedidos de prueba no cuentan."
)
RECEIPTS = {
    "disponible": False,
    "motivo": "No hay ningún comprobante de pago anotado en las ventanas del informe.",
    "faltante": (
        "Desplegar la sombra del comprobante (tarjeta 33 del ciclo) y esperar a que lleguen comprobantes. "
        "Los números de prueba no cuentan en este informe."
    ),
}
#: Relevos al por mayor en sombra (tarjeta 34 del ciclo): lo que la decisión
#: `datos_por_mayor` (paquete ventas-10) anotó al relevar, contra lo que el
#: equipo pidió después. `BULK_PERSON`: el cliente pidió hablar con una persona.
BULK_CAPABILITY = "datos_por_mayor"
BULK_PERSON = "persona"
#: Con menos relevos en los que el equipo pidió algún dato, el porcentaje engaña.
BULK_MIN_TO_MEASURE = 12
#: Lo que lee Jev del equipo: sus primeros mensajes después del relevo.
BULK_TEAM_MESSAGES = 6
BULK_HANDOFFS_NOTE = (
    "Sombra: en cada relevo por mayorista, pedido grande o evento de empresa, Colossus anota qué datos ya había "
    "dado el cliente y si pidió una persona; el relevo sale igual. «habria_preguntado»: faltaba algún dato y el "
    "cliente no pidió una persona. «contra_el_equipo»: Jev lee los primeros mensajes del equipo después del relevo "
    "(con lo personal tapado) y dice qué datos le pidió al cliente; sin mensajes del equipo, no pidió ninguno. "
    "«acierto_pct»: de los relevos en que el equipo pidió algún dato, en cuántos Colossus había marcado como "
    f"faltante al menos uno de esos; solo sale con {BULK_MIN_TO_MEASURE} o más. Solo conteos."
)
BULK_HANDOFFS = {
    "disponible": False,
    "motivo": (
        "No hay ningún relevo al por mayor anotado en las ventanas del informe: la decisión «datos_por_mayor» "
        "(paquete ventas-10) no ha leído ningún relevo de estas conversaciones."
    ),
    "faltante": (
        "Promover un paquete que traiga «datos_por_mayor» (ventas-10 o posterior) y encenderla en sombra por "
        "comando. Los números de prueba no cuentan en este informe."
    ),
}
#: Lee lo que escribió el equipo y devuelve qué datos pidió (None: no se pudo).
TeamReader = Callable[[str], Awaitable[frozenset[str] | None]]
_STAGE_RANK = {stage: i for i, stage in enumerate(STAGES)}


def _code(value: Any, default: str) -> str:
    if value is None or value == "":
        return default
    return value if isinstance(value, str) and _CODE.fullmatch(value) else "otro"


@dataclass(frozen=True)
class _Handoff:
    reason: str
    stage: str
    source: str


@dataclass(frozen=True)
class _TeamMessage:
    kind: str
    stage: str
    tool: str | None


#: Lo que mandó el equipo con una herramienta de la app (`operator_tool`).
_TOOL_KIND = {
    "present_products": "catalogo",
    "present_product_detail": "producto",
    "present_product_gallery": "producto",
    "present_variant_picker": "variantes",
    "request_shipping_details": "formulario_de_envio",
    "send_shipping_rates": "tarifas_de_envio",
    "present_order_confirmation": "resumen_del_pedido",
    "send_quick_replies": "botones",
    "send_payment_methods": "datos_de_pago",
    "apply_coupon": "cupon",
}


def _team_kind(event: dict[str, Any]) -> str:
    """El tipo de un mensaje del equipo por sus campos, sin leer el texto."""
    tool = event.get("operator_tool")
    if tool:
        return _TOOL_KIND.get(str(tool), "otra_herramienta")
    if event.get("kind") == "template":
        return "plantilla"
    if event.get("document_url"):
        return "documento"
    if event.get("image_url"):
        return "foto"
    return "texto"


@dataclass(frozen=True)
class _Decision:
    """Una decisión en la que Jev participó (`evals/decisions.jsonl`)."""

    capability: str
    #: `jev_dudo` (Jev no alcanzó el umbral y decidió la regla) o `valor_duda`
    #: (la capacidad resolvió «duda»: cajas, por_mayor, caja_nombrada…).
    doubt: str | None
    #: Por qué decidió la regla cuando no fue duda (falla, tiempo, modelo).
    fallback: str | None
    shadow: bool


def _decision(row: dict[str, Any]) -> _Decision:
    reason = row.get("reason")
    if row.get("value") == "duda":
        doubt: str | None = "valor_duda"
    elif reason == "duda":
        doubt = "jev_dudo"
    else:
        doubt = None
    failed = row.get("by") == "respaldo" and reason != "duda"
    return _Decision(
        capability=_code(row.get("capability"), "sin_capacidad"),
        doubt=doubt,
        fallback=_code(reason, "sin_motivo") if failed else None,
        shadow=row.get("provider") == "sombra",
    )


@dataclass(frozen=True)
class _Objection:
    """Lo que `objecion` anotó de un mensaje del cliente."""

    #: Un tipo de `OBJECTION_KINDS`, `NO_OBJECTION`, o None si Jev no lo leyó.
    kind: str | None
    #: Después de ese mensaje la conversación avanzó de etapa.
    advanced: bool


def _objection_kind(row: dict[str, Any]) -> str | None:
    """El tipo que quedó anotado: en sombra decide la regla (nada) y lo que
    leyó Jev está en `jev`; si Jev decide, en `value`. Solo pasa un tipo de la
    lista: cualquier otro texto se cuenta como no leído, nunca se copia."""
    read = row.get("value") if row.get("by") == "jev" else row.get("jev")
    if isinstance(read, str) and read in OBJECTION_KINDS:
        return read
    if not read and row.get("agree") is True:
        return NO_OBJECTION
    return None


@dataclass(frozen=True)
class _Receipt:
    """Lo que Colossus anotó de un comprobante (solo códigos de lista cerrada)."""

    at_ms: int
    verdict: str
    reason: str | None
    source: str
    evidence: str | None = None


def _closed(value: Any, known: tuple[str, ...]) -> str:
    return value if isinstance(value, str) and value in known else "otro"


def _receipts_by_episode(rows: list[dict[str, Any]]) -> dict[str, list[_Receipt]]:
    """Por el episodio que trae la fila: el comprobante suele llegar con el
    episodio ya cerrado (se cierra al registrar el pedido)."""
    out: dict[str, list[_Receipt]] = {}
    for row in rows:
        episode_id = row.get("episode_id")
        if not isinstance(episode_id, str) or not episode_id:
            continue
        reason, evidence = row.get("reason"), row.get("evidence")
        out.setdefault(episode_id, []).append(_Receipt(
            at_ms=_ms(row.get("at_ms")) or 0,
            verdict=_closed(row.get("verdict"), RECEIPT_VERDICTS),
            reason=_closed(reason, RECEIPT_REASONS) if reason else None,
            source=_closed(row.get("source"), RECEIPT_SOURCES),
            evidence=_closed(evidence, RECEIPT_EVIDENCE) if evidence else None,
        ))
    return out


@dataclass
class _BulkHandoff:
    """Lo que `datos_por_mayor` anotó de un relevo al por mayor."""

    #: Los datos que faltaban, o None si Jev no lo leyó.
    missing: frozenset[str] | None
    person: bool
    #: Mensajes del equipo después del relevo, en el mismo episodio.
    team_after: int
    #: Lo que escribió el equipo, ya tapado (lo lee Jev; nunca llega al informe).
    team_text: str = ""
    #: Lo que el equipo pidió; None = no se pudo leer.
    team_asked: frozenset[str] | None = None


def _bulk_reading(row: dict[str, Any]) -> tuple[frozenset[str] | None, bool]:
    """Lo que quedó anotado: en sombra lo leído por Jev está en `jev` (vacío si
    coincidió con la regla, que no anota nada); si Jev decide, en `value`. Solo
    pasan los códigos de la lista: cualquier otro texto se descarta."""
    read = row.get("value") if row.get("by") == "jev" else row.get("jev")
    if read is None and row.get("agree") is True:
        read = []
    if not isinstance(read, list):
        return None, False
    codes = {c for c in read if isinstance(c, str)}
    return frozenset(codes & set(BULK_DATA)), BULK_PERSON in codes


def _bulk_by_episode(
    rows: list[dict[str, Any]], team: list[dict[str, Any]], timeline: _Timeline,
) -> dict[str, list[_BulkHandoff]]:
    from src.sdk.connectorkit import anonymize_text

    dated_team = [(ms, e) for e in team if (ms := _iso_ms(e.get("timestamp"))) is not None]
    out: dict[str, list[_BulkHandoff]] = {}
    for row in rows:
        if row.get("capability") != BULK_CAPABILITY:
            continue
        at_ms = _ms(row.get("at_ms"))
        episode_id = timeline.episode_at(at_ms) if at_ms is not None else None
        if episode_id is None or at_ms is None:
            continue
        after = sorted(
            ((ms, e) for ms, e in dated_team if ms > at_ms and timeline.episode_at(ms) == episode_id),
            key=lambda item: item[0],
        )
        texts = [str(e.get("content")).strip() for _ms_, e in after[:BULK_TEAM_MESSAGES]
                 if isinstance(e.get("content"), str) and e.get("content").strip()]
        missing, person = _bulk_reading(row)
        out.setdefault(episode_id, []).append(_BulkHandoff(
            missing=missing, person=person, team_after=len(after),
            team_text=anonymize_text("\n".join(texts)) if texts else "",
        ))
    return out


async def _read_team(episodes: list[_Episode], reader: TeamReader | None) -> None:
    """Lo que pidió el equipo en cada relevo que Colossus leyó: sin mensajes
    del equipo, nada; si no, lo lee Jev (una vez más si no se pudo leer). Un
    lector que falla deja el relevo sin comparar, nunca rompe el informe."""
    pending = [h for ep in episodes for h in ep.bulk if h.missing is not None]
    for handoff in pending:
        if not handoff.team_text:
            handoff.team_asked = frozenset()
    to_read = [h for h in pending if h.team_text]
    if not to_read:
        return
    read = reader or jev_team_reader
    for handoff in to_read:
        for _attempt in range(2):
            try:
                asked = await read(handoff.team_text)
            except Exception:  # noqa: BLE001 — Jev caído: el relevo queda sin comparar
                asked = None
            if asked is not None:
                handoff.team_asked = frozenset(asked) & frozenset(BULK_DATA)
                break


@dataclass
class _Episode:
    window: str
    bot: str
    turns: int
    furthest: str = STAGES[0]
    order_id: str | None = None
    handoffs: list[_Handoff] = field(default_factory=list)
    team: list[_TeamMessage] = field(default_factory=list)
    decisions: list[_Decision] = field(default_factory=list)
    objections: list[_Objection] = field(default_factory=list)
    receipts: list[_Receipt] = field(default_factory=list)
    bulk: list[_BulkHandoff] = field(default_factory=list)
    #: Lo que dice OrderFacts del pedido del episodio (None: sin pedido).
    order: str | None = None




def _furthest(stages: Iterable[Any]) -> str:
    known = [s for s in stages if s in _STAGE_RANK]
    return max(known, key=_STAGE_RANK.__getitem__) if known else STAGES[0]


@dataclass
class _Windows:
    dates: dict[str, list[date]] = field(default_factory=dict)

    def of(self, ms: Any) -> str | None:
        if not isinstance(ms, (int, float)) or isinstance(ms, bool):
            return None
        day = datetime.fromtimestamp(ms / 1000, UTC).date()
        return next((w for w, days in self.dates.items() if day in days), None)

    def first_day(self) -> date | None:
        days = [d for ds in self.dates.values() for d in ds]
        return min(days) if days else None


def _windows(days: int, base_days: int, now: datetime, since: date | None) -> _Windows:
    today = now.astimezone(UTC).date()
    recent = [today - timedelta(days=i) for i in range(max(1, days))]
    start = min(recent)
    older = [start - timedelta(days=i) for i in range(1, max(0, base_days) + 1)]

    def keep(days_: list[date]) -> list[date]:
        return sorted(d for d in days_ if since is None or d >= since)

    return _Windows({"actual": keep(recent), "base": keep(older)})


def _session_dirs(vault_dir: Path, first_day: date | None) -> list[Path]:
    from src.sdk.identitykit import is_customer_session_id

    if first_day is None or not vault_dir.exists():
        return []
    cutoff = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC).timestamp()
    out: list[Path] = []
    for entry in vault_dir.iterdir():
        meta = entry / "metadata.json"
        try:
            if entry.is_dir() and is_customer_session_id(entry.name) and meta.stat().st_mtime >= cutoff:
                out.append(entry)
        except OSError:
            continue
    return sorted(out)


def _read_metadata(vault_dir: Path, session_id: str) -> dict[str, Any] | None:
    """El metadata tal como está, o None si no se puede leer. No pasa por
    `FilesystemMetadataStore`: ese avisa un documento dañado con un ERROR que
    nombra la conversación (el número del cliente), y la salida de este
    informe llega al Estratega."""
    try:
        data = json.loads((vault_dir / session_id / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _test_numbers(vault_dir: Path) -> frozenset[str]:
    """Los números de prueba del operador (control «Bot nuevo»): no son clientes."""
    from src.plugins.chats.agent.sales.decisions.rollout_store import read_state

    try:
        return frozenset(read_state(vault_dir).test_numbers)
    except Exception:  # noqa: BLE001 — sin control legible, nadie es de prueba
        return frozenset()


def _is_prod(trace: dict[str, Any]) -> bool:
    return str(trace.get("source") or "prod") == "prod"


def _ms(value: Any) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class _Timeline:
    """Ubica un momento en su episodio (el último que ya había empezado, como
    `enrich_turn_trace`) y en la etapa en que iba: la salida del último turno
    del bot que ya había empezado."""

    def __init__(self, episodes: list[dict[str, Any]], traces: list[dict[str, Any]]) -> None:
        dated = sorted((ms, str(e.get("episode_id") or "")) for e in episodes
                       if (ms := _ms(e.get("started_at_ms"))) is not None)
        self._starts = [ms for ms, _ in dated]
        self._ids = [eid for _, eid in dated]
        self._turns: dict[str, list[tuple[int, str]]] = {}
        for t in traces:
            start = _ms(t.get("turn_started_ms")) or _ms(t.get("recorded_at_ms"))
            if start is not None:
                self._turns.setdefault(str(t.get("episode_id") or ""), []).append((start, str(t.get("stage_out") or "")))
        for turns in self._turns.values():
            turns.sort()

    def episode_at(self, at_ms: int) -> str | None:
        i = bisect.bisect_right(self._starts, at_ms)
        return self._ids[i - 1] if i else None

    def stage_at(self, episode_id: str, at_ms: int) -> str:
        turns = self._turns.get(episode_id) or []
        i = bisect.bisect_right(turns, (at_ms, "\uffff"))
        return (turns[i - 1][1] or NO_BOT_TURN) if i else NO_BOT_TURN

    def advanced_after(self, episode_id: str, at_ms: int) -> bool:
        """¿Algún turno posterior dejó la conversación en una etapa más
        adelante que la que tenía en ese momento? (Antes del primer turno del
        bot, la primera etapa.)"""
        turns = self._turns.get(episode_id) or []
        i = bisect.bisect_right(turns, (at_ms, "\uffff"))
        before = _STAGE_RANK.get(turns[i - 1][1], 0) if i else 0
        return any(_STAGE_RANK.get(stage, -1) > before for _, stage in turns[i:])


def _handoffs(metadata: dict[str, Any], timeline: _Timeline) -> dict[str, list[_Handoff]]:
    """Cada paso a la ruta del equipo (`status_history`), por episodio. Una
    entrada que llega con la conversación ya en el equipo no es otro relevo."""
    out: dict[str, list[_Handoff]] = {}
    with_team = False
    for entry in metadata.get("status_history") or []:
        if not isinstance(entry, dict):
            continue
        route = entry.get("active_route")
        human = route == _ROUTE_HUMAN or (not route and entry.get("tag") == "HUMANO")
        if human and not with_team:
            at = entry.get("timestamp")
            at_ms = int(at * 1000) if isinstance(at, (int, float)) and not isinstance(at, bool) else None
            episode_id = timeline.episode_at(at_ms) if at_ms is not None else None
            if episode_id is not None and at_ms is not None:
                source = _code(entry.get("source"), "bot")
                default = TEAM_TOOK_OVER if source == "dashboard_intervene" else NO_REASON
                out.setdefault(episode_id, []).append(_Handoff(
                    reason=_code(entry.get("reason_category"), default),
                    stage=timeline.stage_at(episode_id, at_ms),
                    source=source,
                ))
        if human:
            with_team = True
        elif route:
            with_team = False
    return out


def _iso_ms(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return int((moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp() * 1000)


def _team_events(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    """Los mensajes del equipo del historial del chat (`sender: human`)."""
    path = vault_dir / session_id / "sessions" / f"{session_id}.jsonl"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        if '"human"' not in line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("sender") == "human":
            out.append(event)
    return out


def _team_messages(events: list[dict[str, Any]], timeline: _Timeline) -> dict[str, list[_TeamMessage]]:
    out: dict[str, list[_TeamMessage]] = {}
    for event in events:
        at_ms = _iso_ms(event.get("timestamp"))
        episode_id = timeline.episode_at(at_ms) if at_ms is not None else None
        if episode_id is None or at_ms is None:
            continue
        tool = event.get("operator_tool")
        out.setdefault(episode_id, []).append(_TeamMessage(
            kind=_team_kind(event),
            stage=timeline.stage_at(episode_id, at_ms),
            tool=_code(tool, "otro") if tool else None,
        ))
    return out


def _decisions_by_episode(rows: list[dict[str, Any]], timeline: _Timeline) -> dict[str, list[_Decision]]:
    out: dict[str, list[_Decision]] = {}
    for row in rows:
        if row.get("capability") in (OBJECTION_CAPABILITY, BULK_CAPABILITY):
            # Solo anotan y tienen su sección: aquí inflarían el total.
            continue
        at_ms = _ms(row.get("at_ms"))
        episode_id = timeline.episode_at(at_ms) if at_ms is not None else None
        if episode_id is not None:
            out.setdefault(episode_id, []).append(_decision(row))
    return out


def _objections_by_episode(rows: list[dict[str, Any]], timeline: _Timeline) -> dict[str, list[_Objection]]:
    out: dict[str, list[_Objection]] = {}
    for row in rows:
        if row.get("capability") != OBJECTION_CAPABILITY:
            continue
        at_ms = _ms(row.get("at_ms"))
        episode_id = timeline.episode_at(at_ms) if at_ms is not None else None
        if episode_id is not None and at_ms is not None:
            out.setdefault(episode_id, []).append(
                _Objection(kind=_objection_kind(row), advanced=timeline.advanced_after(episode_id, at_ms))
            )
    return out


def _episodes_of(vault_dir: Path, metadata: dict[str, Any], session_id: str, windows: _Windows) -> list[_Episode]:
    raw = [e for e in metadata.get("episodes") or [] if isinstance(e, dict)]
    in_window = [(e, windows.of(e.get("started_at_ms"))) for e in raw]
    if not any(w for _, w in in_window):
        return []
    traces = [t for t in turn_traces.read_traces(vault_dir, session_id) if _is_prod(t)]
    timeline = _Timeline(raw, traces)
    handoffs = _handoffs(metadata, timeline)
    team_events = _team_events(vault_dir, session_id)
    team = _team_messages(team_events, timeline)
    rows = read_decisions(vault_dir, session_id)
    bulk = _bulk_by_episode(rows, team_events, timeline)
    decisions = _decisions_by_episode(rows, timeline)
    objections = _objections_by_episode(rows, timeline)
    receipts = _receipts_by_episode(read_checks(vault_dir, session_id))
    out: list[_Episode] = []
    for episode, window in in_window:
        if window is None:
            continue
        episode_id = str(episode.get("episode_id") or "")
        own = [t for t in traces if t.get("episode_id") == episode_id]
        customer = [t for t in own if t.get("trigger", "customer") == "customer"]
        bot = _BOT_OF_EPISODE.get(episode_bot(customer), "botsito") if customer else "sin_bot"
        out.append(_Episode(
            window=window, bot=bot, turns=len(customer),
            furthest=_furthest([*(t.get("stage_out") for t in own), project_stage(episode)]),
            order_id=str(episode["order_id"]) if episode.get("order_id") else None,
            handoffs=handoffs.get(episode_id, []), team=team.get(episode_id, []),
            decisions=decisions.get(episode_id, []), objections=objections.get(episode_id, []),
            receipts=receipts.get(episode_id, []), bulk=bulk.get(episode_id, []),
        ))
    return out


def _counts(values: Iterable[str]) -> dict[str, int]:
    """Conteo ordenado de mayor a menor (empates por nombre)."""
    return dict(sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0])))


async def _resolve_orders(episodes: list[_Episode], order_facts: Any) -> None:
    """El pedido de cada episodio, de OrderFacts (gotcha 13): el vault solo
    guarda el vínculo conversación → `order_id`."""
    ids = {ep.order_id for ep in episodes if ep.order_id}
    if not ids:
        return
    if order_facts is None:
        from src.sdk.connectorkit import get_order_facts_port

        order_facts = get_order_facts_port()
    snapshot = await order_facts.get_facts(ids)
    for ep in episodes:
        if not ep.order_id:
            continue
        fact = snapshot.facts.get(ep.order_id)
        if fact is None:
            ep.order = ORDER_UNCONFIRMED if ep.order_id in snapshot.unresolved else None
        elif fact.is_test:
            ep.order = ORDER_TEST
        elif fact.stage == "cancelled":
            ep.order = ORDER_CANCELLED
        else:
            ep.order = ORDER_PAID if fact.counts_as_revenue else ORDER_PLACED


def _per_100(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _funnel_section(episodes: list[_Episode]) -> dict[str, Any]:
    placed = [ep for ep in episodes if ep.order in (ORDER_PAID, ORDER_PLACED)]
    lost = [ep for ep in episodes if ep.order not in (ORDER_PAID, ORDER_PLACED, ORDER_UNCONFIRMED)]
    furthest = Counter(ep.furthest for ep in episodes)
    return {
        "conversaciones": len(episodes),
        "por_etapa_mas_lejana": _counts(ep.furthest for ep in episodes),
        "llegaron_a": {
            stage: sum(n for s, n in furthest.items() if _STAGE_RANK[s] >= _STAGE_RANK[stage]) for stage in STAGES
        },
        "sin_pedido_se_quedan_en": _counts(ep.furthest for ep in lost),
        "pedidos": {
            "con_pedido": len(placed),
            "pagados": sum(1 for ep in episodes if ep.order == ORDER_PAID),
            "cancelados": sum(1 for ep in episodes if ep.order == ORDER_CANCELLED),
            "de_prueba": sum(1 for ep in episodes if ep.order == ORDER_TEST),
            "sin_confirmar": sum(1 for ep in episodes if ep.order == ORDER_UNCONFIRMED),
        },
        "conversion_a_pedido_pct": _per_100(len(placed), len(episodes)),
    }


def _jev_section(episodes: list[_Episode]) -> dict[str, Any]:
    items = [d for ep in episodes for d in ep.decisions]
    by_capability: dict[str, dict[str, int]] = {}
    for d in items:
        row = by_capability.setdefault(d.capability, {
            "decisiones": 0, "dudas": 0, "jev_dudo": 0, "valor_duda": 0, "respaldo_por_falla": 0, "en_sombra": 0,
        })
        row["decisiones"] += 1
        if d.doubt:
            row["dudas"] += 1
            row[d.doubt] += 1
        row["respaldo_por_falla"] += d.fallback is not None
        row["en_sombra"] += d.shadow
    # Primero las que más dudan; a igual número, la que duda más seguido.
    ordered = sorted(
        by_capability.items(), key=lambda kv: (-kv[1]["dudas"], -kv[1]["dudas"] / kv[1]["decisiones"], kv[0])
    )
    return {
        "decisiones": len(items),
        "dudas": sum(1 for d in items if d.doubt),
        "por_capacidad": dict(ordered),
        "respaldo_por_motivo": _counts(d.fallback for d in items if d.fallback),
    }


def _objections_section(episodes: list[_Episode]) -> dict[str, Any]:
    notes = [o for ep in episodes for o in ep.objections]
    real = [o.kind for o in notes if o.kind in OBJECTION_KINDS]
    by_kind: dict[str, dict[str, int]] = {}
    for kind, messages in _counts(real).items():
        # Por conversación, desde la primera objeción de ese tipo.
        firsts = [(ep, first) for ep in episodes
                  if (first := next((o for o in ep.objections if o.kind == kind), None)) is not None]
        by_kind[kind] = {
            "mensajes": messages,
            "conversaciones": len(firsts),
            "siguieron_hacia_la_compra": sum(1 for _, first in firsts if first.advanced),
            "terminaron_en_pedido": sum(1 for ep, _ in firsts if ep.order in (ORDER_PAID, ORDER_PLACED)),
        }
    return {
        "lecturas": sum(1 for o in notes if o.kind is not None),
        "sin_lectura": sum(1 for o in notes if o.kind is None),
        "con_objecion": len(real),
        "conversaciones_con_objecion": sum(1 for ep in episodes if any(o.kind in OBJECTION_KINDS for o in ep.objections)),
        "por_tipo": by_kind,
    }


def _objections(by_window: dict[str, Any]) -> dict[str, Any]:
    """La sección, o lo que falta si `objecion` no anotó nada en las ventanas."""
    noted = any(s["lecturas"] or s["sin_lectura"] for bots in by_window.values() for s in bots.values())
    if not noted:
        return dict(OBJECTIONS)
    return {"disponible": True, "nota": OBJECTIONS_NOTE, "tipos": list(OBJECTION_KINDS), **by_window}


def _receipts_section(episodes: list[_Episode]) -> dict[str, Any]:
    notes = [r for ep in episodes if ep.order != ORDER_TEST for r in ep.receipts]
    compared = agreed = said_match_team_refused = said_mismatch_team_accepted = unresolved = 0
    for ep in episodes:
        judged = [r for r in ep.receipts if r.verdict in (RECEIPT_MATCH, RECEIPT_MISMATCH)]
        if not judged or ep.order == ORDER_TEST:
            continue
        if ep.order not in (ORDER_PAID, ORDER_CANCELLED):
            unresolved += 1
            continue
        # Un pedido, un veredicto: el del último comprobante que mandó.
        said_match = max(judged, key=lambda r: r.at_ms).verdict == RECEIPT_MATCH
        accepted = ep.order == ORDER_PAID
        compared += 1
        agreed += said_match == accepted
        said_match_team_refused += said_match and not accepted
        said_mismatch_team_accepted += accepted and not said_match
    enough = compared >= RECEIPTS_MIN_TO_MEASURE
    return {
        "comprobantes": len(notes),
        "suficientes": enough,
        "por_veredicto": _counts(r.verdict for r in notes),
        "por_motivo": _counts(r.reason for r in notes if r.reason),
        "por_origen": _counts(r.source for r in notes),
        "coincide_por_evidencia": _counts(r.evidence or "otro" for r in notes if r.verdict == RECEIPT_MATCH),
        "contra_el_equipo": {
            "pedidos_comparados": compared,
            "coincidencias": agreed,
            "dijo_coincide_y_el_equipo_no_acepto": said_match_team_refused,
            "dijo_no_coincide_y_el_equipo_acepto": said_mismatch_team_accepted,
            "sin_resolver": unresolved,
        },
        "coincidencia_pct": _per_100(agreed, compared) if enough else None,
    }


def _receipts(by_window: dict[str, Any]) -> dict[str, Any]:
    """La sección, o lo que falta si no hay ningún comprobante anotado."""
    if not any(s["comprobantes"] for bots in by_window.values() for s in bots.values()):
        return dict(RECEIPTS)
    return {"disponible": True, "nota": RECEIPTS_NOTE, "minimo_para_medir": RECEIPTS_MIN_TO_MEASURE, **by_window}


def _bulk_section(episodes: list[_Episode]) -> dict[str, Any]:
    items = [h for ep in episodes for h in ep.bulk]
    read = [h for h in items if h.missing is not None]
    compared = [h for h in read if h.team_asked is not None]
    team_asked = [h for h in compared if h.team_asked]
    marked = [h for h in team_asked if h.missing and h.team_asked and (h.missing & h.team_asked)]
    enough = len(team_asked) >= BULK_MIN_TO_MEASURE
    return {
        "relevos": len(items),
        "leidos": len(read),
        "sin_lectura": len(items) - len(read),
        "con_algun_faltante": sum(1 for h in read if h.missing),
        "faltaba_por_dato": {d: sum(1 for h in read if h.missing and d in h.missing) for d in BULK_DATA},
        "habria_preguntado": sum(1 for h in read if h.missing and not h.person),
        "pidio_persona": sum(1 for h in read if h.person),
        "mensajes_del_equipo_despues": sum(h.team_after for h in items),
        "terminaron_en_pedido": sum(
            1 for ep in episodes for _h in ep.bulk if ep.order in (ORDER_PAID, ORDER_PLACED)
        ),
        "contra_el_equipo": {
            "relevos_leidos": len(compared),
            "sin_mensajes_del_equipo": sum(1 for h in read if not h.team_text),
            "equipo_pidio_algun_dato": len(team_asked),
            "equipo_pidio_por_dato": {
                d: sum(1 for h in compared if h.team_asked and d in h.team_asked) for d in BULK_DATA
            },
            "colossus_lo_habia_marcado": len(marked),
        },
        "suficientes": enough,
        "acierto_pct": _per_100(len(marked), len(team_asked)) if enough else None,
    }


def _bulk_handoffs(by_window: dict[str, Any]) -> dict[str, Any]:
    """La sección, o lo que falta si `datos_por_mayor` no anotó ningún relevo."""
    if not any(s["relevos"] for bots in by_window.values() for s in bots.values()):
        return dict(BULK_HANDOFFS)
    return {
        "disponible": True, "nota": BULK_HANDOFFS_NOTE, "datos": list(BULK_DATA),
        "minimo_para_medir": BULK_MIN_TO_MEASURE, **by_window,
    }


def _north_star_section(episodes: list[_Episode]) -> dict[str, Any]:
    n = len(episodes)
    handoffs = [h for ep in episodes for h in ep.handoffs]
    intervened = sum(1 for ep in episodes if ep.handoffs or ep.team)
    return {
        "conversaciones": n,
        "con_intervencion": intervened,
        "por_100": _per_100(intervened, n),
        "relevos_por_100": _per_100(len(handoffs), n),
        "relevos_sin_verificar_pago_por_100": _per_100(sum(1 for h in handoffs if h.reason != PAYMENT_CHECK), n),
        "mensajes_del_equipo_por_100": _per_100(sum(len(ep.team) for ep in episodes), n),
    }


def _north_star(by_window: dict[str, Any]) -> dict[str, Any]:
    def change(bot: str) -> float | None:
        now, before = by_window["actual"][bot]["por_100"], by_window["base"][bot]["por_100"]
        return round(now - before, 1) if now is not None and before is not None else None

    return {"definicion": NORTH_STAR, **by_window, "cambio_por_100": {bot: change(bot) for bot in BOTS}}


def _team_section(episodes: list[_Episode]) -> dict[str, Any]:
    items = [m for ep in episodes for m in ep.team]
    return {
        "mensajes": len(items),
        "conversaciones": sum(1 for ep in episodes if ep.team),
        "por_tipo": _counts(m.kind for m in items),
        "por_etapa": _counts(m.stage for m in items),
        "herramientas_de_la_app": _counts(m.tool for m in items if m.tool),
    }


def _handoff_section(episodes: list[_Episode]) -> dict[str, Any]:
    items = [h for ep in episodes for h in ep.handoffs]
    by_reason_stage: dict[str, list[str]] = {}
    for h in items:
        by_reason_stage.setdefault(h.reason, []).append(h.stage)
    return {
        "total": len(items),
        "conversaciones": sum(1 for ep in episodes if ep.handoffs),
        "por_motivo": _counts(h.reason for h in items),
        "por_etapa": _counts(h.stage for h in items),
        "por_origen": _counts(h.source for h in items),
        "por_motivo_y_etapa": {r: _counts(stages) for r, stages in sorted(by_reason_stage.items())},
    }


def _per_bot(value: int = 0) -> dict[str, int]:
    return dict.fromkeys(BOTS, value)


def _window_info(days: list[date]) -> dict[str, Any]:
    return {
        "dias": len(days),
        "desde": min(days).isoformat() if days else None,
        "hasta": max(days).isoformat() if days else None,
    }


def _funnel(by_window: dict[str, Any], episodes: list[_Episode]) -> dict[str, Any]:
    unconfirmed = sum(1 for ep in episodes if ep.order == ORDER_UNCONFIRMED)
    out: dict[str, Any] = {"etapas": list(STAGES), "fuente_pedidos": "OrderFacts", **by_window}
    if unconfirmed:
        out["aviso"] = (
            f"Medusa no confirmó {unconfirmed} pedido(s): no cuentan como venta ni como conversación sin pedido; "
            "la conversión puede estar por debajo."
        )
    return out


async def build_report(
    vault_dir: Path,
    *,
    days: int = 7,
    base_days: int = 28,
    now: datetime | None = None,
    since: date | None = None,
    order_facts: Any | None = None,
    team_reader: TeamReader | None = None,
) -> dict[str, Any]:
    """El informe de las dos ventanas (ver el módulo). `team_reader`: quien lee
    lo que pidió el equipo tras un relevo al por mayor (Jev, por defecto)."""
    now = now or datetime.now(UTC)
    vault_dir = Path(vault_dir)
    windows = _windows(days, base_days, now, since)
    test_numbers = _test_numbers(vault_dir)
    episodes: list[_Episode] = []
    excluded = unreadable = 0
    for session_dir in _session_dirs(vault_dir, windows.first_day()):
        if session_dir.name in test_numbers:
            excluded += 1
            continue
        metadata = _read_metadata(vault_dir, session_dir.name)
        if metadata is None:
            unreadable += 1
            continue
        episodes.extend(_episodes_of(vault_dir, metadata, session_dir.name, windows))

    await _resolve_orders(episodes, order_facts)
    await _read_team(episodes, team_reader)

    def per_window(section: Any) -> dict[str, Any]:
        return {
            w: {bot: section([e for e in episodes if e.window == w and e.bot == bot]) for bot in BOTS}
            for w in WINDOWS
        }

    sample: dict[str, Any] = {}
    for window in WINDOWS:
        conversations, turns = _per_bot(), _per_bot()
        for ep in (e for e in episodes if e.window == window):
            conversations[ep.bot] += 1
            turns[ep.bot] += ep.turns
        sample[window] = {"conversaciones": conversations, "turnos": turns}
    sample["excluidas_numeros_de_prueba"] = excluded
    sample["sesiones_ilegibles"] = unreadable
    return {
        "generado": now.isoformat(timespec="seconds"),
        "ventanas": {w: _window_info(windows.dates[w]) for w in WINDOWS},
        "muestra": sample,
        "relevos": per_window(_handoff_section),
        "equipo": per_window(_team_section),
        "embudo": _funnel(per_window(_funnel_section), episodes),
        "jev_dudas": {"nota": JEV_NOTE, **per_window(_jev_section)},
        "objeciones": _objections(per_window(_objections_section)),
        "comprobantes": _receipts(per_window(_receipts_section)),
        "relevos_por_mayor": _bulk_handoffs(per_window(_bulk_section)),
        "intervenciones_por_100": _north_star(per_window(_north_star_section)),
    }


_BOT_LABEL = {"colossus": "Colossus", "botsito": "Botsito", "mixto": "Mixto", "sin_bot": "Sin bot"}


def _top(counts: dict[str, int], n: int = 3) -> str:
    return ", ".join(f"{k} {v}" for k, v in list(counts.items())[:n]) or "—"


def render(report: dict[str, Any]) -> str:
    """El informe en Markdown, en español llano (solo cifras)."""
    w = report["ventanas"]
    star = report["intervenciones_por_100"]
    lines = [
        f"# Informe para el Estratega · {w['actual']['desde']} a {w['actual']['hasta']}",
        "",
        f"Comparado con {w['base']['desde']} a {w['base']['hasta']}.",
        "",
        "## Intervenciones por cada 100 conversaciones",
        "",
        "| Bot | Conversaciones | Ahora | Antes | Cambio | Relevos sin pago | Mensajes del equipo |",
        "|---|---|---|---|---|---|---|",
    ]
    for bot in BOTS:
        now, before = star["actual"][bot], star["base"][bot]
        lines.append(
            f"| {_BOT_LABEL[bot]} | {now['conversaciones']} | {now['por_100']} | {before['por_100']} "
            f"| {star['cambio_por_100'][bot]} | {now['relevos_sin_verificar_pago_por_100']} "
            f"| {now['mensajes_del_equipo_por_100']} |"
        )
    lines += ["", "## Por bot (ventana actual)", ""]
    for bot in ("colossus", "botsito"):
        handoffs = report["relevos"]["actual"][bot]
        team = report["equipo"]["actual"][bot]
        funnel = report["embudo"]["actual"][bot]
        doubts = report["jev_dudas"]["actual"][bot]
        lines += [
            f"### {_BOT_LABEL[bot]}",
            f"- Relevos: {handoffs['total']} ({_top(handoffs['por_motivo'])}); por etapa: {_top(handoffs['por_etapa'])}",
            f"- Mensajes del equipo: {team['mensajes']} ({_top(team['por_tipo'])})",
            f"- Conversión a pedido: {funnel['conversion_a_pedido_pct']} %; sin pedido se quedan en: "
            f"{_top(funnel['sin_pedido_se_quedan_en'])}",
            f"- Dudas de Jev: {doubts['dudas']} de {doubts['decisiones']} decisiones "
            f"({_top({k: v['dudas'] for k, v in doubts['por_capacidad'].items() if v['dudas']})})",
            "",
        ]
    objections = report["objeciones"]
    if objections["disponible"]:
        now = objections["actual"]["colossus"]
        kinds = now["por_tipo"]
        lines.append(
            f"Objeciones (Colossus, ahora): {now['con_objecion']} en {now['lecturas']} mensajes leídos "
            f"({_top({k: v['mensajes'] for k, v in kinds.items()}, n=len(OBJECTION_KINDS))}); "
            f"en {sum(v['siguieron_hacia_la_compra'] for v in kinds.values())} de "
            f"{sum(v['conversaciones'] for v in kinds.values())} casos el cliente siguió hacia la compra."
        )
    else:
        lines.append(f"Objeciones: no disponible. {objections['motivo']}")
    receipts = report["comprobantes"]
    if receipts["disponible"]:
        now = receipts["actual"]["colossus"]
        cross = now["contra_el_equipo"]
        measured = (
            f"coincidencia {now['coincidencia_pct']} %" if now["suficientes"]
            else f"faltan {RECEIPTS_MIN_TO_MEASURE - cross['pedidos_comparados']} pedidos comparados para medir el porcentaje"
        )
        lines.append(
            f"Comprobantes (Colossus, ahora, en sombra): {now['comprobantes']} ({_top(now['por_veredicto'], n=4)}); "
            f"en {cross['coincidencias']} de {cross['pedidos_comparados']} pedidos dijo lo mismo que el equipo; "
            f"dijo «coincide» y el equipo no aceptó el pago: {cross['dijo_coincide_y_el_equipo_no_acepto']}; {measured}."
        )
    else:
        lines.append(f"Comprobantes: no disponible. {receipts['motivo']}")
    bulk = report["relevos_por_mayor"]
    if bulk["disponible"]:
        now = bulk["actual"]["colossus"]
        team = now["contra_el_equipo"]
        measured = (
            f"acierto {now['acierto_pct']} %" if now["suficientes"]
            else f"faltan {BULK_MIN_TO_MEASURE - team['equipo_pidio_algun_dato']} relevos para medir el porcentaje"
        )
        lines.append(
            f"Relevos al por mayor (Colossus, ahora, en sombra): {now['relevos']}; faltaba algún dato en "
            f"{now['con_algun_faltante']}; habría preguntado en {now['habria_preguntado']}; pidió una persona en "
            f"{now['pidio_persona']}; en {team['colossus_lo_habia_marcado']} de {team['equipo_pidio_algun_dato']} "
            f"el equipo pidió un dato que Colossus había marcado como faltante; {measured}."
        )
    else:
        lines.append(f"Relevos al por mayor: no disponible. {bulk['motivo']}")
    if report["embudo"].get("aviso"):
        lines.append(f"Aviso: {report['embudo']['aviso']}")
    return "\n".join(lines) + "\n"


def main(argv: Iterable[str] | None = None) -> int:
    from src.plugins.chats.agent.sales_eval.evals.composition import get_vault_dir

    parser = argparse.ArgumentParser(description="Informe agregado para el Estratega del ciclo Colossus.")
    parser.add_argument("--dias", type=int, default=7, help="días de la ventana actual (incluye hoy, UTC)")
    parser.add_argument("--base", type=int, default=28, help="días de la ventana de comparación, justo antes")
    parser.add_argument("--desde", type=date.fromisoformat, default=None, help="no contar antes de esta fecha")
    parser.add_argument("--json", action="store_true", help="el informe como JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = asyncio.run(build_report(Path(get_vault_dir()), days=args.dias, base_days=args.base, since=args.desde))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
