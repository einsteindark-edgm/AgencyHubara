"""CampaignStore — persistencia del historial de campañas en el vault.

`<vault>/_campaigns/<campaign_id>.json`, escritura atómica vía SDK. El vault
es el único estado que sobrevive deploys (PR #183) — el historial de campañas
hereda ese volumen (y su riesgo conocido: sin backups, memoria terraform).
"""
import json
from pathlib import Path
from typing import Any

from src.sdk.runtime import atomic_write_json


class CampaignStore:
    def __init__(self, vault_dir: Path) -> None:
        self._dir = Path(vault_dir) / "_campaigns"

    def _path(self, campaign_id: str) -> Path:
        return self._dir / f"{campaign_id}.json"

    def list_campaigns(self) -> list[dict[str, Any]]:
        if not self._dir.is_dir():
            return []
        out: list[dict[str, Any]] = []
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("id"):
                out.append(data)
        out.sort(key=lambda c: c.get("updated_at_ms") or 0, reverse=True)
        return out

    def get(self, campaign_id: str) -> dict[str, Any] | None:
        path = self._path(campaign_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def save(self, campaign: dict[str, Any]) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self._path(campaign["id"]), campaign)

    def delete(self, campaign_id: str) -> bool:
        path = self._path(campaign_id)
        if not path.exists():
            return False
        path.unlink()
        return True
