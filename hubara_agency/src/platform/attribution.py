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
        if not isinstance(touch, dict):
            continue
        sent = touch.get("sent_at_ms")
        if not isinstance(sent, int) or not touch.get("campaign_id"):
            continue
        if sent <= at_ms <= sent + window_ms:
            if best is None or sent > best["sent_at_ms"]:
                best = touch
    return best


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
