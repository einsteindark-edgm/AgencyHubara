"""Atribución CTWA como capability de PLATAFORMA (F-SDK-4, caso ads del plan).

El ingest de WhatsApp escribe la atribución (``origin``, ``last_touch``,
``referral_snapshot`` por episodio) en el metadata de cada sesión del vault.
Hasta F-SDK-4, el plugin ``ads`` descubría y parseaba esas sesiones
escaneando el vault A MANO (conocía el layout ``wa_*/metadata.json``). Eso
era un read model compartido sin dueño: CAPI (futuro) necesita EXACTAMENTE el
mismo scan para mandar conversiones con ``ctwa_clid``.

Este módulo es el dueño: **un writer (el ingest), N readers vía
``AttributionReadPort``**. El descubrimiento (glob + mtime-prefilter + parse
tolerante) vive acá; la lógica de NEGOCIO (episodios, clasificación,
buckets) sigue siendo del consumidor.

Nota de v1 (deliberada): ``AttributionSession`` expone ``session_dir`` porque
los consumidores actuales derivan señales del tamaño/mtime del history JSONL
(conteo lazy de mensajes — costo dominante a escala, ver ads/aggregation).
Cuando el segundo consumidor (CAPI) aterrice, esa superficie se estrecha a
métodos (``message_count()``/``last_activity_ms()``) — anotado en
docs/_sdk/07-connectorkit.md para no "descubrirlo".

Los plugins consumen esto vía ``src.sdk.connectorkit`` (jamás importando
este módulo directo — P-28).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

_SESSION_PREFIX = "wa_"

#: Ventana de atribución de una campaña interna de marketing (paridad con la
#: ventana CTWA de Meta): 7 días desde el envío del touch.
CAMPAIGN_ATTRIBUTION_WINDOW_MS = 7 * 24 * 60 * 60 * 1000


def matching_campaign_touch(
    campaign_touches: list[Any] | None,
    at_ms: int | None,
    *,
    window_ms: int = CAMPAIGN_ATTRIBUTION_WINDOW_MS,
) -> dict[str, Any] | None:
    """El `campaign_touch` (plugin marketing) que cubre el instante `at_ms`.

    Writer: la activity `stamp_campaign_touch` del plugin marketing (shape
    ``{campaign_id, campaign_name, sent_at_ms}``). Readers: ads (agrupar
    episodios por campaña interna) y marketing (stats de la campaña). Con
    varios touches en ventana gana el MÁS RECIENTE (last-touch, mismo
    criterio que ``last_touch`` del ingest). Tolerante a shapes rotos.
    """
    if not campaign_touches or not isinstance(at_ms, int):
        return None
    best: dict[str, Any] | None = None
    for touch in campaign_touches:
        # `test`: envío de prueba del operador — deja touch para que el bot
        # sepa qué campaña recibió, pero no es una respuesta/venta atribuible.
        if not isinstance(touch, dict) or touch.get("test"):
            continue
        sent = touch.get("sent_at_ms")
        if not isinstance(sent, int) or not touch.get("campaign_id"):
            continue
        if sent <= at_ms <= sent + window_ms:
            if best is None or sent > best["sent_at_ms"]:
                best = touch
    return best


def _touch_that_opened(
    opened: dict[str, Any], campaign_touches: list[Any] | None
) -> dict[str, Any] | None:
    """El touch que registró el episodio: por el id del mensaje si la marca lo
    trae; si no (marcas de antes de 2026-09-25), por campaña + hora de envío."""
    wamid = opened.get("wa_message_id")
    for touch in campaign_touches or []:
        if not isinstance(touch, dict):
            continue
        if wamid:
            if touch.get("wa_message_id") == wamid:
                return touch
        elif (
            touch.get("campaign_id") == opened.get("campaign_id")
            and touch.get("sent_at_ms") == opened.get("sent_at_ms")
        ):
            return touch
    return None


def attributed_campaign_touch(
    episode: dict[str, Any] | None,
    campaign_touches: list[Any] | None,
    at_ms: int | None,
) -> dict[str, Any] | None:
    """La campaña (touch REAL) a la que se atribuye un episodio, o None.

    Si una respuesta a campaña abrió el episodio (`opened_by_campaign`, lo
    escribe el webhook de chats: el mensaje de campaña que el cliente citó
    o, sin cita, el último envío que no había respondido), es esa campaña: la
    misma decisión que vio el bot. Si lo abrió un envío de PRUEBA → None, aunque
    haya otro envío real en ventana (respondía a la prueba). Sin esa marca
    (episodios viejos) → el último touch real en ventana
    (`matching_campaign_touch`). Readers: ads y marketing (2026-09-25)."""
    opened = episode.get("opened_by_campaign") if isinstance(episode, dict) else None
    if not isinstance(opened, dict) or not opened.get("campaign_id"):
        return matching_campaign_touch(campaign_touches, at_ms)
    if opened.get("test"):
        return None
    touch = _touch_that_opened(opened, campaign_touches)
    if touch is not None:
        return None if touch.get("test") else touch
    # El contacto guarda sus últimas campañas (tope): la marca alcanza.
    return {k: opened[k] for k in ("campaign_id", "campaign_name", "sent_at_ms") if k in opened}


# --- Lo que Meta dice de cada mensaje de campaña (2026-09-25) ---------------
#
# El touch es el registro POR DESTINATARIO de una campaña: además de la
# atribución lleva el id del mensaje (`wa_message_id`, lo escribe el send del
# plugin marketing) y `delivery` (lo escribe el webhook de estados de chats):
#
#     "delivery": {"status": "read", "delivered_at_ms": …, "read_at_ms": …,
#                  "failed_at_ms": …, "error_code": 131049,
#                  "pricing": {billable, pricing_type, category},
#                  "cost_usd_micros": 12500, "rate_card_version": "…"}
#
# Ads lo lee para entregados/leídos/gasto de la campaña (`campaign_delivery`).


def campaign_touch_for_message(
    campaign_touches: list[Any] | None, wa_message_id: str
) -> dict[str, Any] | None:
    """El touch del mensaje de campaña con ese `wa_message_id`, o None si el
    mensaje no es de una campaña (o el touch es de antes de guardar el id)."""
    if not wa_message_id or not campaign_touches:
        return None
    for touch in campaign_touches:
        if isinstance(touch, dict) and touch.get("wa_message_id") == wa_message_id:
            return touch
    return None


@dataclass(frozen=True)
class CampaignDelivery:
    """Lo que se sabe del mensaje de campaña de UN destinatario."""

    delivered: bool = False
    read: bool = False
    #: Meta avisó que no se pudo entregar (y nunca se entregó).
    failed: bool = False
    #: Ya llegó el precio de Meta (`cost_usd_micros` puede ser 0: gratis).
    priced: bool = False
    cost_usd_micros: int | None = None
    #: El touch guarda el id del mensaje (touches de antes de 2026-09-25 no):
    #: sin id no hay forma de saber si se entregó ni cuánto costó.
    has_message_id: bool = False


def _earliest(delivery: dict[str, Any], key: str, at_ms: int) -> None:
    current = delivery.get(key)
    if not isinstance(current, int) or at_ms < current:
        delivery[key] = at_ms


def apply_campaign_delivery(
    touch: dict[str, Any],
    *,
    status: str,
    at_ms: int | None,
    error_code: int | None = None,
    pricing: dict[str, Any] | None = None,
    cost_usd_micros: int | None = None,
    rate_card_version: str | None = None,
) -> bool:
    """Mutates: anota en `touch["delivery"]` un estado de Meta del mensaje.

    - `delivered`/`read` guardan la hora más temprana; leído implica
      entregado (Meta puede mandar `read` antes que `delivered`), y el estado
      nunca retrocede.
    - `failed` guarda la hora y el código de error de Meta.
    - El precio (`pricing` + `cost_usd_micros` + versión de la tarifa) se
      anota una sola vez, con el primer estado que lo trae: un webhook
      duplicado no lo pisa.

    `at_ms`: hora del estado según Meta; sin ella, la del envío. Devuelve
    True si cambió algo (False = webhook repetido o sin nada nuevo)."""
    before = touch.get("delivery") if isinstance(touch.get("delivery"), dict) else {}
    delivery = dict(before)
    when = at_ms if isinstance(at_ms, int) else touch.get("sent_at_ms")
    if isinstance(when, int):
        if status in ("delivered", "read"):
            _earliest(delivery, "delivered_at_ms", when)
        if status == "read":
            _earliest(delivery, "read_at_ms", when)
        if status == "failed":
            _earliest(delivery, "failed_at_ms", when)
    if status == "failed" and error_code is not None and delivery.get("error_code") is None:
        delivery["error_code"] = error_code
    if (
        isinstance(pricing, dict)
        and isinstance(cost_usd_micros, int)
        and not isinstance(delivery.get("cost_usd_micros"), int)
    ):
        delivery["pricing"] = dict(pricing)
        delivery["cost_usd_micros"] = cost_usd_micros
        delivery["rate_card_version"] = rate_card_version
    delivery["status"] = (
        "read" if "read_at_ms" in delivery
        else "delivered" if "delivered_at_ms" in delivery
        else "failed" if "failed_at_ms" in delivery
        else "sent"
    )
    if delivery == before:
        return False
    touch["delivery"] = delivery
    return True


def campaign_delivery(touch: dict[str, Any]) -> CampaignDelivery:
    """Lectura del `delivery` de un touch (tolerante a shapes viejos o rotos)."""
    raw = touch.get("delivery")
    delivery = raw if isinstance(raw, dict) else {}
    read = isinstance(delivery.get("read_at_ms"), int)
    delivered = read or isinstance(delivery.get("delivered_at_ms"), int)
    cost = delivery.get("cost_usd_micros")
    cost = cost if isinstance(cost, int) and not isinstance(cost, bool) else None
    return CampaignDelivery(
        delivered=delivered,
        read=read,
        failed=not delivered and isinstance(delivery.get("failed_at_ms"), int),
        priced=cost is not None,
        cost_usd_micros=cost,
        has_message_id=bool(touch.get("wa_message_id")),
    )


@dataclass(frozen=True)
class AttributionSession:
    """Una sesión WhatsApp con su metadata de atribución parseada.

    ``metadata`` es el dict del ``metadata.json`` TAL CUAL (el contrato de su
    contenido — origin/last_touch/episodes — lo documenta el writer en el
    ingest de chats). No mutar: puede compartirse entre consumidores.
    """

    session_id: str
    session_dir: Path
    metadata: dict[str, Any] = field(repr=False)

    @property
    def phone(self) -> str:
        return self.session_id.removeprefix(_SESSION_PREFIX)


@runtime_checkable
class AttributionReadPort(Protocol):
    """Contrato de lectura del read model de atribución (readers: ads, CAPI)."""

    def scan_sessions(
        self, *, since_ms: int | None = None
    ) -> list[AttributionSession]:  # pragma: no cover — firma estructural
        ...


class FilesystemAttributionStore:
    """Adapter real: lee ``<vault>/wa_*/metadata.json``.

    Semántica de ``since_ms`` (heredada del scan de ads, verificada por sus
    53K de tests de agregación): es un PRE-FILTRO superset por ``mtime`` del
    metadata — crear/avanzar un episodio SIEMPRE reescribe el archivo, así
    que una sesión sin mtime ≥ since no tiene episodios en la ventana y es
    seguro saltearla SIN parsearla. El filtro preciso por episodio lo hace el
    consumidor. Defensivo: si el stat falla, NO se saltea (se parsea).
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _iter_session_dirs(self) -> Iterator[Path]:
        if not self._vault_dir.exists() or not self._vault_dir.is_dir():
            return
        for entry in self._vault_dir.iterdir():
            if entry.is_dir() and entry.name.startswith(_SESSION_PREFIX):
                yield entry

    @staticmethod
    def _touched_since(session_dir: Path, since_ms: int) -> bool:
        try:
            mtime_ms = int((session_dir / "metadata.json").stat().st_mtime * 1000)
        except OSError:
            return True
        return mtime_ms >= since_ms

    @staticmethod
    def _read_metadata(session_dir: Path) -> dict[str, Any] | None:
        path = session_dir / "metadata.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def scan_sessions(self, *, since_ms: int | None = None) -> list[AttributionSession]:
        out: list[AttributionSession] = []
        for session_dir in self._iter_session_dirs():
            if since_ms is not None and not self._touched_since(session_dir, since_ms):
                continue
            metadata = self._read_metadata(session_dir)
            if metadata is not None:
                out.append(
                    AttributionSession(
                        session_id=session_dir.name,
                        session_dir=session_dir,
                        metadata=metadata,
                    )
                )
        return out


class InMemoryAttributionStore:
    """El FAKE oficial del port (regla del ConnectorKit: ningún port sin fake).

    Para tests de consumidores sin filesystem. ``touched_ms`` opcional por
    sesión emula el pre-filtro mtime (misma semántica superset: sesión sin
    entrada en el map NUNCA se saltea).
    """

    def __init__(
        self,
        sessions: list[AttributionSession],
        *,
        touched_ms: dict[str, int] | None = None,
    ) -> None:
        self._sessions = list(sessions)
        self._touched_ms = dict(touched_ms or {})

    def scan_sessions(self, *, since_ms: int | None = None) -> list[AttributionSession]:
        if since_ms is None:
            return list(self._sessions)
        return [
            s
            for s in self._sessions
            if self._touched_ms.get(s.session_id) is None
            or self._touched_ms[s.session_id] >= since_ms
        ]
