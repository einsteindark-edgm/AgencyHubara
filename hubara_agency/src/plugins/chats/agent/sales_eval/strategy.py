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

import bisect
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from src.plugins.chats.agent.sales_eval.scorecard.bot import episode_bot
from src.plugins.chats.shared import turn_traces

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


def _code(value: Any, default: str) -> str:
    if value is None or value == "":
        return default
    return value if isinstance(value, str) and _CODE.fullmatch(value) else "otro"


@dataclass(frozen=True)
class _Handoff:
    reason: str
    stage: str
    source: str


@dataclass
class _Episode:
    window: str
    bot: str
    turns: int
    handoffs: list[_Handoff] = field(default_factory=list)


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
    keep = (lambda ds: [d for d in ds if since is None or d >= since])
    return _Windows({"actual": sorted(keep(recent)), "base": sorted(keep(older))})


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


def _read_metadata(vault_dir: Path, session_id: str) -> dict[str, Any]:
    from src.sdk.runtime import FilesystemMetadataStore

    try:
        data = FilesystemMetadataStore(vault_dir).read(session_id)
    except Exception:  # noqa: BLE001 — una conversación ilegible no tumba el informe
        return {}
    return data if isinstance(data, dict) else {}


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


def _episodes_of(vault_dir: Path, session_id: str, windows: _Windows) -> list[_Episode]:
    metadata = _read_metadata(vault_dir, session_id)
    raw = [e for e in metadata.get("episodes") or [] if isinstance(e, dict)]
    in_window = [(e, windows.of(e.get("started_at_ms"))) for e in raw]
    if not any(w for _, w in in_window):
        return []
    traces = [t for t in turn_traces.read_traces(vault_dir, session_id) if _is_prod(t)]
    timeline = _Timeline(raw, traces)
    handoffs = _handoffs(metadata, timeline)
    out: list[_Episode] = []
    for episode, window in in_window:
        if window is None:
            continue
        episode_id = str(episode.get("episode_id") or "")
        own = [t for t in traces if t.get("episode_id") == episode_id]
        customer = [t for t in own if t.get("trigger", "customer") == "customer"]
        bot = _BOT_OF_EPISODE.get(episode_bot(customer), "botsito") if customer else "sin_bot"
        out.append(_Episode(window=window, bot=bot, turns=len(customer), handoffs=handoffs.get(episode_id, [])))
    return out


def _counts(values: Iterable[str]) -> dict[str, int]:
    """Conteo ordenado de mayor a menor (empates por nombre)."""
    return dict(sorted(Counter(values).items(), key=lambda kv: (-kv[1], kv[0])))


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


async def build_report(
    vault_dir: Path,
    *,
    days: int = 7,
    base_days: int = 28,
    now: datetime | None = None,
    since: date | None = None,
    order_facts: Any | None = None,
) -> dict[str, Any]:
    """El informe de las dos ventanas (ver el módulo)."""
    now = now or datetime.now(UTC)
    vault_dir = Path(vault_dir)
    windows = _windows(days, base_days, now, since)
    test_numbers = _test_numbers(vault_dir)
    episodes: list[_Episode] = []
    excluded = 0
    for session_dir in _session_dirs(vault_dir, windows.first_day()):
        if session_dir.name in test_numbers:
            excluded += 1
            continue
        episodes.extend(_episodes_of(vault_dir, session_dir.name, windows))

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
    return {
        "generado": now.isoformat(timespec="seconds"),
        "ventanas": {w: _window_info(windows.dates[w]) for w in WINDOWS},
        "muestra": sample,
        "relevos": per_window(_handoff_section),
    }
