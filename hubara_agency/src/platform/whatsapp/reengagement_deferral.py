"""Aplazamiento del cliente — "les escribo la otra semana" PAUSA la escalera.

Incidente (runs 337efe8c / ee3cec91, 2026-09-21): el cliente respondió «Sí,
pero les escribo la otra semana», el bot contestó «aquí estaré»… y la escalera
de reactivación le mandó 4 toques en 24h. Respuesta del cliente: «No más» —
baja definitiva de un lead que iba a volver.

Este módulo lee el aplazamiento DETERMINISTA (sin LLM) y deja en el metadata la
fecha en que el cliente dijo que retoma (`reengagement_deferral.until_ms`, a
las 10:00 hora local). Hasta esa fecha ningún toque proactivo de venta: lo
consultan el pre-filtro del ciclo, la central (`check_reengagement_policy`) y
el watchdog. Vencida la fecha, la escalera vuelve a correr normal.

Sesgo deliberado:
* Solo pausa un aplazamiento CON FECHA ("mañana", "el jueves", "la otra
  semana", "en 15 días", "el otro mes", "la quincena"…) o uno donde el cliente
  dice que ÉL escribe ("yo les escribo cuando vaya a comprar" → 7 días). Un
  "más tarde" / "voy en camino" es del mismo día: la escalera ya espera 2h.
* La fecha sola necesita contexto de aplazamiento (un verbo tipo "escribo",
  "compro", "confirmo") salvo que el mensaje sea SOLO la fecha ("la otra
  semana"). "¿Me llega mañana?" es una pregunta de envío, no un aplazamiento.
* Una cortesía después ("gracias 🙏") no levanta la pausa; retomar la charla
  sí (cualquier otro mensaje de texto).

Puro (R-DET): sin I/O ni reloj — el caller pasa `now_ms` y la zona horaria del
cliente (`resolve_local_timezone`) y persiste el metadata.
"""
import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

DEFERRAL_KEY = "reengagement_deferral"

#: El cliente dio una fecha ("la otra semana", "el jueves").
DEFERRAL_KIND_DATED = "fecha"
#: El cliente dijo que ÉL escribe, sin fecha ("yo les escribo cuando…").
DEFERRAL_KIND_OPEN = "abierto"

#: Pausa de un aplazamiento sin fecha: una semana.
OPEN_DEFERRAL_MS = 7 * 24 * 60 * 60 * 1000

#: Hora local a la que "retoma" un aplazamiento con fecha.
RESUME_HOUR_LOCAL = 10

_TEXT_MAX = 120


@dataclass(frozen=True)
class ReengagementDeferral:
    #: hasta cuándo no se le escribe proactivamente (epoch ms).
    until_ms: int
    #: `DEFERRAL_KIND_DATED` / `DEFERRAL_KIND_OPEN`.
    kind: str


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return " ".join(stripped.split())


# --- Contexto de aplazamiento ------------------------------------------------

#: Verbos/frases que convierten una fecha en aplazamiento ("el jueves TE
#: CONFIRMO", "mañana LO PIDO").
_DEFERRAL_CUE = re.compile(
    r"\b(escrib\w*|avis\w*|confirm\w*|habl\w*|retom\w*|compr\w*|pid\w*|pido|"
    r"mir\w*|revis\w*|piens\w*|pens\w*|decid\w*|cuent\w*|contact\w*|"
    r"paso|pasamos|vemos|luego|despues|ahora no)\b"
)

#: El cliente dice que ÉL vuelve a escribir.
_OPEN_DEFERRAL = re.compile(
    r"\b(te|les|le) (escribo|aviso|confirmo|hablo|cuento)\b"
    r"|\bcuando (vaya|valla) a comprar\b"
)

_SHORT_DATE_ONLY_MAX_WORDS = 4

# --- Expresiones de fecha ----------------------------------------------------

_WEEKDAYS = {
    "lunes": 0,
    "martes": 1,
    "miercoles": 2,
    "jueves": 3,
    "viernes": 4,
    "sabado": 5,
    "domingo": 6,
}
_NUMBER_WORDS = {
    "un": 1, "una": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "diez": 10, "quince": 15,
    "veinte": 20, "treinta": 30,
}

_RE_DAY_AFTER_TOMORROW = re.compile(r"\bpasado manana\b")
#: "mañana" = tomorrow; "en la mañana" / "por la mañana" = en la mañana (hoy).
_RE_TOMORROW = re.compile(r"(?<!\bla )\bmanana\b")
_RE_WEEKEND = re.compile(r"\bfin de semana\b")
_RE_NEXT_WEEK = re.compile(
    r"\b(otra|proxima|siguiente) semana\b|\bsemana (que viene|entrante)\b"
)
_RE_IN_N = re.compile(
    r"\ben (\d+|" + "|".join(_NUMBER_WORDS) + r") (dias?|semanas?|mes|meses)\b"
)
_RE_NEXT_MONTH = re.compile(
    r"\b(otro|proximo|siguiente) mes\b|\bmes (que viene|entrante)\b"
)
_RE_PAYDAY = re.compile(r"\bquincena\b|\bme paguen\b|\bme pagan\b|\bcobre\b")
_RE_END_OF_MONTH = re.compile(r"\b(fin|finales) de mes\b")
_RE_WEEKDAY = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b")


def _last_day(d: date) -> date:
    return d.replace(day=calendar.monthrange(d.year, d.month)[1])


def _first_of_next_month(d: date) -> date:
    return (_last_day(d) + timedelta(days=1)).replace(day=1)


def _next_weekday(today: date, weekday: int) -> date:
    """La próxima vez que cae `weekday`, estrictamente después de hoy."""
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def _next_payday(today: date) -> date:
    """Quincena colombiana: el 15 o el último día del mes, el próximo."""
    fifteenth = today.replace(day=15)
    if today < fifteenth:
        return fifteenth
    last = _last_day(today)
    if today < last:
        return last
    return _first_of_next_month(today).replace(day=15)


def _resume_date(norm: str, today: date) -> date | None:
    """La fecha que nombra el texto, o None si no nombra ninguna."""
    if _RE_DAY_AFTER_TOMORROW.search(norm):
        return today + timedelta(days=2)
    if _RE_TOMORROW.search(norm):
        return today + timedelta(days=1)
    if _RE_WEEKEND.search(norm):
        # Sábado; si hoy ya es sábado, el domingo.
        return today + timedelta(days=1) if today.weekday() == 5 else _next_weekday(today, 5)
    if _RE_NEXT_WEEK.search(norm):
        return _next_weekday(today, 0)
    m = _RE_IN_N.search(norm)
    if m:
        raw, unit = m.group(1), m.group(2)
        n = int(raw) if raw.isdigit() else _NUMBER_WORDS[raw]
        days = n * (7 if unit.startswith("semana") else 30 if unit.startswith("mes") else 1)
        return today + timedelta(days=days)
    if _RE_NEXT_MONTH.search(norm):
        return _first_of_next_month(today)
    if _RE_PAYDAY.search(norm):
        return _next_payday(today)
    if _RE_END_OF_MONTH.search(norm):
        last = _last_day(today)
        return last if today < last else _last_day(_first_of_next_month(today))
    m = _RE_WEEKDAY.search(norm)
    if m:
        return _next_weekday(today, _WEEKDAYS[m.group(1)])
    return None


def parse_reengagement_deferral(
    text: str | None, now_ms: int, tz: ZoneInfo
) -> ReengagementDeferral | None:
    """El aplazamiento que expresa `text`, o None si no pausa la escalera."""
    if not text:
        return None
    norm = _normalize(text)
    if not norm:
        return None

    today = datetime.fromtimestamp(now_ms / 1000, tz=tz).date()
    resume = _resume_date(norm, today)
    if resume is not None:
        words = norm.split()
        date_only = len(words) <= _SHORT_DATE_ONLY_MAX_WORDS and "?" not in norm
        if _DEFERRAL_CUE.search(norm) or _OPEN_DEFERRAL.search(norm) or date_only:
            resume_at = datetime(
                resume.year, resume.month, resume.day, RESUME_HOUR_LOCAL, tzinfo=tz
            )
            return ReengagementDeferral(
                until_ms=int(resume_at.timestamp() * 1000), kind=DEFERRAL_KIND_DATED
            )

    if _OPEN_DEFERRAL.search(norm):
        return ReengagementDeferral(
            until_ms=now_ms + OPEN_DEFERRAL_MS, kind=DEFERRAL_KIND_OPEN
        )
    return None


# --- Registro en el metadata -------------------------------------------------

#: Palabras de una cortesía que NO retoma la charla ("gracias 🙏", "listo, dale").
_COURTESY_WORDS = frozenset({
    "ok", "okay", "oki", "listo", "dale", "gracias", "muchas", "mil", "perfecto",
    "bueno", "vale", "va", "claro", "si", "super", "genial", "chevere", "bien",
    "entendido", "igualmente", "bendiciones", "a", "ti", "usted", "ustedes",
    "de", "una", "buena", "buenas", "feliz", "dia", "tarde", "noche", "muy",
    "amable", "con", "gusto", "vea", "pues",
})
_COURTESY_MAX_WORDS = 6


def _is_courtesy(norm: str) -> bool:
    words = re.findall(r"[a-z0-9]+", norm)
    return len(words) <= _COURTESY_MAX_WORDS and all(w in _COURTESY_WORDS for w in words)


def _active(metadata: dict[str, Any], now_ms: int) -> dict[str, Any] | None:
    entry = metadata.get(DEFERRAL_KEY)
    if not isinstance(entry, dict):
        return None
    until = entry.get("until_ms")
    if isinstance(until, bool) or not isinstance(until, int) or now_ms >= until:
        return None
    return entry


def register_reengagement_deferral(
    metadata: dict[str, Any], text: str | None, *, now_ms: int, tz: ZoneInfo
) -> None:
    """Estampa / conserva / levanta la pausa según este inbound (muta in place).

    * Aplazamiento → estampa la fecha. Uno abierto no pisa una fecha que el
      cliente ya dio ("les escribo la otra semana" + "yo les escribo cuando…").
    * Cortesía ("gracias") → la pausa sigue.
    * Cualquier otro texto → el cliente retomó: la pausa se levanta.
    """
    if not text:
        return
    parsed = parse_reengagement_deferral(text, now_ms, tz)
    current = _active(metadata, now_ms)
    if parsed is not None:
        if (
            parsed.kind == DEFERRAL_KIND_OPEN
            and current is not None
            and current.get("kind") == DEFERRAL_KIND_DATED
        ):
            return
        metadata[DEFERRAL_KEY] = {
            "at_ms": now_ms,
            "until_ms": parsed.until_ms,
            "kind": parsed.kind,
            "text": text[:_TEXT_MAX],
        }
        return
    if DEFERRAL_KEY in metadata and not _is_courtesy(_normalize(text)):
        metadata.pop(DEFERRAL_KEY, None)


def reengagement_deferred_until(metadata: dict[str, Any], now_ms: int) -> int | None:
    """Hasta cuándo el cliente pidió que no le escribamos, o None si no hay
    pausa vigente."""
    entry = _active(metadata, now_ms)
    return None if entry is None else entry["until_ms"]
