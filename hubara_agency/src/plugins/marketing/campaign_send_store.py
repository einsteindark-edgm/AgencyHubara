"""CampaignSendStore — el plan de un envío por lotes y lo que pasó en cada lote.

`<vault>/_campaigns/_sends/<campaign_id>/plan.json` (cabecera) +
`recipients/<inicio>.json` (trozos) + `batches/<start>.json`,
escritura atómica vía SDK. El plan de una campaña de miles de contactos no
cabe en un payload de Temporal (2 MB): vive acá y CampaignSendWorkflowV2 solo
lleva el cursor. Un subdirectorio de `_campaigns/`: `CampaignStore` lista
solo los `*.json` del nivel de arriba, así que nada de esto se ve como una
campaña.
"""
import json
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.plugins.marketing.domain.campaign_send import CampaignBatchResult
from src.plugins.marketing.domain.campaigns import (
    CampaignSendPlan,
    RecipientSendPlan,
    SkippedRecipient,
)
from src.sdk.runtime import atomic_write_json


#: Destinatarios por archivo del plan: un lote lee uno o dos trozos, no el
#: plan entero (miles de contactos, varios MB).
_CHUNK = 500


class CampaignSendStore:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_campaigns" / "_sends"

    def _plan_path(self, campaign_id: str) -> Path:
        return self._dir / campaign_id / "plan.json"

    def _batches_dir(self, campaign_id: str) -> Path:
        return self._dir / campaign_id / "batches"

    def _recipients_dir(self, campaign_id: str) -> Path:
        return self._dir / campaign_id / "recipients"

    def save_plan(self, plan: CampaignSendPlan) -> None:
        """Los destinatarios van en trozos de `_CHUNK` (cada lote lee solo el
        suyo) y la cabecera al final: si existe, el plan está completo."""
        recipients_dir = self._recipients_dir(plan.campaign_id)
        shutil.rmtree(recipients_dir, ignore_errors=True)  # un reintento lo rearma
        recipients_dir.mkdir(parents=True, exist_ok=True)
        header = asdict(plan)
        recipients = header.pop("recipients")
        for first in range(0, len(recipients), _CHUNK):
            atomic_write_json(recipients_dir / f"{first:07d}.json", recipients[first : first + _CHUNK])
        header["recipient_count"] = len(recipients)
        atomic_write_json(self._plan_path(plan.campaign_id), header)

    def _header(self, campaign_id: str) -> dict[str, Any]:
        return json.loads(self._plan_path(campaign_id).read_text(encoding="utf-8"))

    def plan(self, campaign_id: str) -> CampaignSendPlan:
        header = self._header(campaign_id)
        count = header.pop("recipient_count")
        return CampaignSendPlan(
            **{
                **header,
                "recipients": self._read_recipients(campaign_id, 0, count),
                "skipped": [SkippedRecipient(**s) for s in header["skipped"]],
            }
        )

    def find_plan(self, campaign_id: str) -> CampaignSendPlan | None:
        """El plan, o None si nunca se guardó (falló antes de armarlo)."""
        if not self._plan_path(campaign_id).exists():
            return None
        return self.plan(campaign_id)

    def recipients(self, campaign_id: str, start: int, end: int) -> list[RecipientSendPlan]:
        """Los destinatarios `[start, end)`: lee solo los trozos que los tienen.
        FileNotFoundError si el plan no está (nunca se guardó o ya se borró)."""
        count = self._header(campaign_id)["recipient_count"]
        return self._read_recipients(campaign_id, start, min(end, count))

    def _read_recipients(self, campaign_id: str, start: int, end: int) -> list[RecipientSendPlan]:
        out: list[RecipientSendPlan] = []
        for first in range(start - start % _CHUNK, end, _CHUNK):
            path = self._recipients_dir(campaign_id) / f"{first:07d}.json"
            chunk = json.loads(path.read_text(encoding="utf-8"))
            lo = max(start - first, 0)
            hi = min(end - first, len(chunk))
            out.extend(RecipientSendPlan(**r) for r in chunk[lo:hi])
        return out

    def save_batch(self, campaign_id: str, result: CampaignBatchResult) -> None:
        """Idempotente: el lote se guarda por su inicio — reintentarlo lo pisa."""
        directory = self._batches_dir(campaign_id)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / f"{result.start:07d}.json", asdict(result))

    def exists(self, campaign_id: str) -> bool:
        return (self._dir / campaign_id).is_dir()

    def remove(self, campaign_id: str) -> None:
        """Al cerrar el envío: el resultado ya está en la campaña y el plan
        (nombres y números de cada destinatario) no se queda en el vault."""
        shutil.rmtree(self._dir / campaign_id, ignore_errors=True)

    def batches(self, campaign_id: str) -> list[CampaignBatchResult]:
        directory = self._batches_dir(campaign_id)
        if not directory.is_dir():
            return []
        return [
            _batch_from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(directory.glob("*.json"))
        ]


def _batch_from_dict(data: dict[str, Any]) -> CampaignBatchResult:
    return CampaignBatchResult(
        **{**data, "skipped": [SkippedRecipient(**s) for s in data.get("skipped") or []]}
    )
