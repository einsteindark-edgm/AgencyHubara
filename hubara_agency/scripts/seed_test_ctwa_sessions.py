"""Siembra conversaciones CTWA SINTÉTICAS en el vault (histórico de prueba).

Contexto (2026-07-09): para ejercitar el embudo + el análisis con IA hace falta
data de conversaciones NO cerradas atribuidas a una campaña real (las ventas
cerradas ya viven en Medusa vía el backfill). Este script escribe las sesiones
que planifica `src.plugins.ads.synthetic_seed` (estados garantizados contra el
clasificador real): nuevo ×2, activo ×2, calificado, cotizado, perdido, no_reply.

Salvaguardas:
  * TODAS las sesiones llevan `seeded_test: true` → `--clean` las borra.
  * Teléfonos obviamente falsos (wa_5730000009XX) — no colisionan con clientes.
  * Dry-run por default; `--apply` para escribir.
  * OJO: aparecen también en la sección Chats (son sesiones del vault) — es
    esperado; ninguna lleva tag HUMANO (no ensucian la bandeja humana).

USO (container del API en la caja — el vault vive ahí):

    # resolver el ad automáticamente desde la campaña (primer ad):
    python -m scripts.seed_test_ctwa_sessions --campaign-id 120243118818600317 --apply

    # o con un ad explícito:
    python -m scripts.seed_test_ctwa_sessions --ad-id <AD_ID> --apply

    # limpiar TODO lo sembrado (borra sesiones con seeded_test=true):
    python -m scripts.seed_test_ctwa_sessions --clean --apply
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time

from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.logging import setup_logging
from src.plugins.ads.synthetic_seed import build_seed_sessions

setup_logging()


def _resolve_ad_id(campaign_id: str) -> str:
    """Primer ad de la campaña vía Graph (ads_read; token de la conexión sembrada)."""
    import httpx

    from src.plugins.ads.meta.composition import get_token_store

    token = get_token_store().load()
    if token is None:
        raise SystemExit("sin conexión Meta sembrada — pasá --ad-id explícito")
    resp = httpx.get(
        f"https://graph.facebook.com/v25.0/{campaign_id}/ads",
        params={"fields": "id,name", "limit": 1, "access_token": token.access_token},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json().get("data") or []
    if not data:
        raise SystemExit(f"la campaña {campaign_id} no tiene ads — pasá --ad-id")
    logger.info("ad resuelto: {} ({})", data[0]["id"], data[0].get("name"))
    return str(data[0]["id"])


def _clean(apply: bool) -> None:
    removed = 0
    for meta_file in sorted(WORKSPACE_VAULT_DIR.glob("*/metadata.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if meta.get("seeded_test") is True:
            removed += 1
            logger.info("  CLEAN {}", meta_file.parent.name)
            if apply:
                shutil.rmtree(meta_file.parent, ignore_errors=True)
    logger.info("{}: {} sesiones sintéticas", "BORRADAS" if apply else "A BORRAR", removed)
    if not apply:
        logger.info("DRY-RUN — nada borrado. Re-correr con --apply.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ad-id", default="", help="ad id de Meta (atribución de las sesiones)")
    parser.add_argument("--campaign-id", default="", help="resuelve el primer ad de esta campaña")
    parser.add_argument("--apply", action="store_true", help="escribir (default: dry-run)")
    parser.add_argument("--clean", action="store_true", help="borrar sesiones seeded_test")
    args = parser.parse_args()

    if args.clean:
        _clean(args.apply)
        return

    ad_id = args.ad_id or (_resolve_ad_id(args.campaign_id) if args.campaign_id else "")
    if not ad_id:
        raise SystemExit("falta --ad-id o --campaign-id")

    now_ms = int(time.time() * 1000)
    specs = build_seed_sessions(ad_id, now_ms=now_ms)
    for spec in specs:
        key = spec["session_key"]
        exists = (WORKSPACE_VAULT_DIR / key / "metadata.json").exists()
        logger.info(
            "  {} {} (msgs={}, episodio {})",
            "REESCRIBIR" if exists else "SEED",
            key,
            spec["history_msgs"],
            "cerrado" if spec["metadata"]["episodes"][-1]["closed_at_ms"] else "abierto",
        )
        if not args.apply:
            continue
        session_dir = WORKSPACE_VAULT_DIR / key
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "metadata.json").write_text(
            json.dumps(spec["metadata"], indent=2, ensure_ascii=False), encoding="utf-8"
        )
        if spec["history_msgs"]:
            sessions_dir = session_dir / "sessions"
            sessions_dir.mkdir(parents=True, exist_ok=True)
            jsonl = sessions_dir / f"{key}.jsonl"
            lines = [
                json.dumps({"role": "user" if i % 2 == 0 else "assistant",
                            "content": f"[seed] mensaje {i}"})
                for i in range(spec["history_msgs"])
            ]
            jsonl.write_text("\n".join(lines) + "\n", encoding="utf-8")
            if spec["last_inbound_ms"]:
                # el "último mensaje" se deriva del mtime del JSONL — clavarlo
                # al timestamp del spec (así no_reply/activo clasifican bien).
                ts = spec["last_inbound_ms"] / 1000
                os.utime(jsonl, (ts, ts))
    logger.info("{} sesiones sintéticas {}", len(specs), "ESCRITAS" if args.apply else "(dry-run)")
    if not args.apply:
        logger.info("DRY-RUN — nada escrito. Re-correr con --apply.")


if __name__ == "__main__":
    main()
