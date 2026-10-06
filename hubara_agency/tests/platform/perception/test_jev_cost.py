"""El costo de Jev queda en la conversación, como el del LLM y el de WhatsApp.

Cada pregunta a Jev (OpenRouter `usage.cost`) se suma al episodio de la
conversación en `metadata.json` → `episodes[].jev_usage = {calls,
cost_usd_micros}` (micro-USD enteros: una pregunta cuesta ~2e-5 USD). Ads lo
lee del vault y lo muestra en la columna «Costo Jev».
"""
from __future__ import annotations

import json
from pathlib import Path

from src.platform.perception.costs import record_jev_cost, usd_to_micros

SID = "wa_573001234567"


def _seed(vault: Path, episodes: list[dict]) -> Path:
    path = vault / SID / "metadata.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"episodes": episodes}), encoding="utf-8")
    return path


def _episodes(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["episodes"]


def test_each_question_adds_its_cost_to_the_open_episode(tmp_path: Path) -> None:
    path = _seed(tmp_path, [
        {"episode_id": "ep_001", "closed_at_ms": 1},
        {"episode_id": "ep_002", "closed_at_ms": None, "llm_usage": {"cost_usd": 0.01}},
    ])

    assert record_jev_cost(SID, 0.00002, vault_dir=tmp_path) is True
    assert record_jev_cost(SID, 0.0000315, vault_dir=tmp_path) is True

    first, second = _episodes(path)
    assert "jev_usage" not in first
    assert second["jev_usage"] == {"calls": 2, "cost_usd_micros": 52}
    assert second["llm_usage"] == {"cost_usd": 0.01}  # lo demás queda igual


def test_after_the_close_it_goes_to_the_last_episode(tmp_path: Path) -> None:
    """Remarketing pregunta sobre una conversación ya cerrada: el costo es de
    esa conversación."""
    path = _seed(tmp_path, [{"episode_id": "ep_001", "closed_at_ms": 1}, {"episode_id": "ep_002", "closed_at_ms": 2}])

    record_jev_cost(SID, 0.00002, vault_dir=tmp_path)

    assert _episodes(path)[1]["jev_usage"] == {"calls": 1, "cost_usd_micros": 20}


def test_a_session_that_is_not_in_the_vault_is_not_created(tmp_path: Path) -> None:
    """La prueba diaria de Jev usa una sesión sintética: no deja carpetas."""
    vault = tmp_path / "vault"
    vault.mkdir()
    assert record_jev_cost("wa_probe_jev", 0.00002, vault_dir=vault) is False
    assert record_jev_cost("../fuera", 0.00002, vault_dir=vault) is False
    assert list(vault.iterdir()) == []


def test_without_cost_or_episodes_nothing_is_written(tmp_path: Path) -> None:
    path = _seed(tmp_path, [])

    assert record_jev_cost(SID, None, vault_dir=tmp_path) is False
    assert record_jev_cost(SID, 0.0, vault_dir=tmp_path) is False
    assert record_jev_cost(SID, 0.00002, vault_dir=tmp_path) is False
    assert _episodes(path) == []


def test_micros_round_to_the_nearest_unit() -> None:
    assert usd_to_micros(0.0000215) == 22
    assert usd_to_micros(0.00002) == 20
    assert usd_to_micros(None) == 0
