"""CampaignSendStore — el plan de un envío por lotes y lo que pasó en cada lote.

`<vault>/_campaigns/_sends/<campaign_id>/plan.json` + `batches/<start>.json`,
escritura atómica vía SDK. El plan de una campaña de miles de contactos no
cabe en un payload de Temporal (2 MB): vive acá y CampaignSendWorkflowV2 solo
lleva el cursor. Un subdirectorio de `_campaigns/`: `CampaignStore` lista
solo los `*.json` del nivel de arriba, así que nada de esto se ve como una
campaña.
"""
import json
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


class CampaignSendStore:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_campaigns" / "_sends"

    def _plan_path(self, campaign_id: str) -> Path:
        return self._dir / campaign_id / "plan.json"

    def _batches_dir(self, campaign_id: str) -> Path:
        return self._dir / campaign_id / "batches"

    def save_plan(self, plan: CampaignSendPlan) -> None:
        path = self._plan_path(plan.campaign_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(path, asdict(plan))

    def plan(self, campaign_id: str) -> CampaignSendPlan:
        data = json.loads(self._plan_path(campaign_id).read_text(encoding="utf-8"))
        return CampaignSendPlan(
            **{
                **data,
                "recipients": [RecipientSendPlan(**r) for r in data["recipients"]],
                "skipped": [SkippedRecipient(**s) for s in data["skipped"]],
            }
        )

    def find_plan(self, campaign_id: str) -> CampaignSendPlan | None:
        """El plan, o None si nunca se guardó (falló antes de armarlo)."""
        if not self._plan_path(campaign_id).exists():
            return None
        return self.plan(campaign_id)

    def recipients(self, campaign_id: str, start: int, end: int) -> list[RecipientSendPlan]:
        return self.plan(campaign_id).recipients[start:end]

    def save_batch(self, campaign_id: str, result: CampaignBatchResult) -> None:
        """Idempotente: el lote se guarda por su inicio — reintentarlo lo pisa."""
        directory = self._batches_dir(campaign_id)
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_json(directory / f"{result.start:07d}.json", asdict(result))

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
