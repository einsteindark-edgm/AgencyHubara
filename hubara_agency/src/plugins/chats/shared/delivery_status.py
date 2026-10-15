"""Doble check de WhatsApp: lo que Meta informó de cada mensaje que enviamos.

El estado sale SOLO de los webhooks `statuses[]` de Meta, nunca del bot.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.sdk.identitykit import is_customer_session_id


@dataclass(frozen=True)
class WamidDelivery:
    """Lo que Meta dijo de UN mensaje (un wamid)."""

    status: str
    at_ms: int | None
    #: Código y título del error de Meta en un `failed` (131047, 131026…).
    error_code: int | None = None
    error_title: str | None = None


#: Precedencia sin retroceder: Meta manda los estados desordenados. Un
#: `failed` le gana a `sent` (WhatsApp lo aceptó y después no pudo entregarlo)
#: pero no a `delivered`/`read`: si llegó, llegó.
_RANK = {"sent": 1, "failed": 2, "delivered": 3, "read": 4}


def fold_delivery_statuses(records: Iterable[Mapping[str, Any]]) -> dict[str, WamidDelivery]:
    folded: dict[str, WamidDelivery] = {}
    for record in records:
        if not isinstance(record, Mapping):
            continue
        wamid, status = record.get("wamid"), record.get("status")
        if not isinstance(wamid, str) or not wamid or status not in _RANK:
            continue
        current = folded.get(wamid)
        if current is not None and _RANK[current.status] >= _RANK[status]:
            continue
        folded[wamid] = WamidDelivery(
            status=status,
            at_ms=record.get("at_ms"),
            error_code=record.get("error_code"),
            error_title=record.get("error_title"),
        )
    return folded


def message_delivery(wamids: Sequence[str], folded: Mapping[str, WamidDelivery]) -> dict[str, Any]:
    """El check de UN mensaje del chat, que puede ser N burbujas de WhatsApp.

    Lo que confirmó la burbuja más atrasada (la hora: cuándo la última de ese
    nivel lo alcanzó). Sin estado de Meta para alguna burbuja, ``unconfirmed``:
    nunca se inventa un check.
    """
    bubbles = [folded.get(w) for w in wamids]
    known = [b for b in bubbles if b is not None]
    failed = [b for b in known if b.status == "failed"]
    if not failed and (not known or len(known) < len(bubbles)):
        return {"status": "unconfirmed", "at_ms": None, "error_code": None, "error_title": None}
    # Una burbuja que no llegó manda sobre todo lo demás.
    weakest = _RANK["failed"] if failed else min(_RANK[b.status] for b in known)
    level = [b for b in known if _RANK[b.status] == weakest]
    times = [b.at_ms for b in level if isinstance(b.at_ms, int)]
    return {
        "status": level[0].status,
        "at_ms": max(times) if times else None,
        "error_code": level[0].error_code,
        "error_title": level[0].error_title,
    }


def message_wamids(message: Mapping[str, Any]) -> list[str]:
    """Los wamids de un evento del historial: `wamids` (un texto partido en
    burbujas) o su `wamid` (plantilla, componente, adjunto)."""
    wamids = message.get("wamids")
    if isinstance(wamids, list):
        return [w for w in wamids if isinstance(w, str) and w]
    wamid = message.get("wamid")
    return [wamid] if isinstance(wamid, str) and wamid else []


def annotate_delivery(messages: Iterable[dict[str, Any]], folded: Mapping[str, WamidDelivery]) -> None:
    """Pone `delivery` en cada mensaje SALIENTE del historial (del bot o del
    operador, `role: assistant`). Los del cliente y las llamadas a tools no lo
    llevan."""
    for message in messages:
        if message.get("role") != "assistant" or message.get("tool_calls"):
            continue
        message["delivery"] = message_delivery(message_wamids(message), folded)


#: Un archivo por conversación, al lado de su `metadata.json`.
LOG_FILENAME = "delivery_statuses.jsonl"


class DeliveryStatusLog:
    """Registro append-only de los estados de Meta, por conversación.

    Aparte de `metadata.json` a propósito: no compite por su candado con el
    bot ni toca el costo por episodio, un webhook repetido no hace daño y el
    orden de llegada da igual (el plegado decide). Una línea por estado.
    """

    def __init__(self, vault_dir: Path) -> None:
        self._vault_dir = vault_dir

    def _path(self, session_id: str) -> Path | None:
        # El id sale del webhook: solo conversaciones de clientes, nunca una
        # ruta armada con lo que mande el cuerpo.
        if not is_customer_session_id(session_id):
            return None
        return self._vault_dir / session_id / LOG_FILENAME

    def append(
        self,
        session_id: str,
        *,
        wamid: str,
        status: str,
        at_ms: int | None,
        error_code: int | None = None,
        error_title: str | None = None,
    ) -> bool:
        """``False`` si la conversación no es válida (no escribe nada)."""
        path = self._path(session_id)
        if path is None:
            return False
        record: dict[str, Any] = {"wamid": wamid, "status": status, "at_ms": at_ms}
        if error_code is not None:
            record["error_code"] = error_code
        if error_title:
            record["error_title"] = error_title
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True

    def read(self, session_id: str) -> dict[str, WamidDelivery]:
        """Estado plegado por wamid (vacío si no hay registro). Una línea rota
        (escritura a medias) se saltea."""
        path = self._path(session_id)
        if path is None or not path.exists():
            return {}
        records: list[Any] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    records.append(json.loads(line))
                except ValueError:
                    continue
        return fold_delivery_statuses(records)
