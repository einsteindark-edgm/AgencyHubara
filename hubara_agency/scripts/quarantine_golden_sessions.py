"""Saca del vault las sesiones del golden-eval (`wa_golden_<escenario>`) a una
CUARENTENA (`<vault>/_quarantine/golden-<ts>/`).

Contexto (incidente 2026-09-23): el golden suite que lanza el worker sales_eval
(`run_golden_suite_activity` -> `scripts/golden_eval.py`) heredaba el
WORKSPACE_VAULT_DIR real y el runner hacía `setdefault`: cada corrida dejaba sus
sesiones de prueba en el vault de clientes, donde Chats, Orders y los barridos
(reengagement, order_sentinel, ads, marketing) las tratan como conversaciones
reales. El runner ya fuerza su vault temporal; esto limpia lo que quedó antes.

Salvaguardas:
  * Solo directorios de primer nivel `wa_golden_*`: un cliente real es
    `wa_<dígitos>` y nunca matchea.
  * Mover, no borrar: reversible con un `mv` de vuelta, y los readers del vault
    solo miran `wa_*` de primer nivel (la cuarentena es invisible).
  * Dry-run por default (lista cada sesión); `--apply` para mover. Idempotente.

USO (container del API en la caja — el vault vive ahí):

    python -m scripts.quarantine_golden_sessions            # dry-run: lista
    python -m scripts.quarantine_golden_sessions --apply    # mueve a cuarentena
    # deshacer: mv <vault>/_quarantine/golden-<ts>/wa_golden_* <vault>/
"""
from __future__ import annotations

import argparse
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from src.platform.config import WORKSPACE_VAULT_DIR
from src.platform.logging import setup_logging

#: Prefijo de las sesiones del runner (`scripts/golden_eval.py::drive_scenario`).
GOLDEN_SESSION_PREFIX = "wa_golden_"


def find_golden_sessions(vault_dir: Path) -> list[Path]:
    """Directorios `wa_golden_*` de primer nivel del vault (lo que ven los readers)."""
    return sorted(p for p in vault_dir.glob(f"{GOLDEN_SESSION_PREFIX}*") if p.is_dir())


@dataclass(frozen=True)
class QuarantineResult:
    found: int
    moved: int
    target_dir: Path


def quarantine_golden_sessions(
    vault_dir: Path, *, apply: bool, now_ms: int | None = None
) -> QuarantineResult:
    """Mueve las sesiones golden a `<vault>/_quarantine/golden-<ts>/`.

    Sin `apply` solo lista. Idempotente: la segunda pasada no encuentra nada."""
    ts = datetime.fromtimestamp(
        (now_ms if now_ms is not None else int(time.time() * 1000)) / 1000, tz=timezone.utc
    ).strftime("%Y%m%dT%H%M%SZ")
    target_dir = vault_dir / "_quarantine" / f"golden-{ts}"
    sessions = find_golden_sessions(vault_dir)
    moved = 0
    for session_dir in sessions:
        logger.info("  {} {}", "CUARENTENA" if apply else "A CUARENTENA", session_dir.name)
        if not apply:
            continue
        target_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(session_dir), str(target_dir / session_dir.name))
        moved += 1
    return QuarantineResult(found=len(sessions), moved=moved, target_dir=target_dir)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--vault", type=Path, default=WORKSPACE_VAULT_DIR,
                        help="vault a limpiar (default: WORKSPACE_VAULT_DIR)")
    parser.add_argument("--apply", action="store_true", help="mover de verdad (sin esto: dry-run)")
    args = parser.parse_args(argv)

    logger.info("vault: {}", args.vault)
    result = quarantine_golden_sessions(args.vault, apply=args.apply)
    if not args.apply:
        logger.info("A CUARENTENA: {} sesiones golden", result.found)
        logger.info("DRY-RUN — nada movido. Re-correr con --apply.")
        return
    logger.info("MOVIDAS {} de {} sesiones golden a {}", result.moved, result.found, result.target_dir)
    if result.moved:
        logger.info("deshacer: mv {}/{}* {}/", result.target_dir, GOLDEN_SESSION_PREFIX, args.vault)


if __name__ == "__main__":
    setup_logging()
    main()
