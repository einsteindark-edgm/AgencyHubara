"""Pasada: marca el perfil «interesado sin compra» en las conversaciones de antes.

Pedido del operador (2026-10-20): «luego hacemos una pasada y etiquetamos todos
los que no alcanzaron a estar etiquetados antes del desarrollo», para usar el
perfil como filtro de público en las campañas de WhatsApp. Desde el deploy, el
ingest de Ventas lo marca en vivo (`chats/shared/customer_profiles.py`); esta
pasada lo marca en las conversaciones de antes, con lo que quedó en el vault:

  * lo que leyó Jev en sus mensajes (`<sesión>/evals/decisions.jsonl`, desde el
    2026-10-02): eligió, confirmó, dio datos o aplazó;
  * un pedido a medias en el episodio;
  * la etapa del embudo de sus turnos (`evals/turn_traces.jsonl`): ya eligió;
  * la señal de compra del último mensaje.

Solo el último episodio en que quiso comprar, y solo si no compró desde ese
episodio. Quien ya tiene el perfil no se toca. La fuente queda como
`pasada:<fuente>`; `at_ms` es cuándo mostró la intención (no el día de la
pasada, que queda en `stamped_at_ms`).

Uso (en la caja, dentro del contenedor de la API; devuelve solo números):

    python scripts/backfill_interest_profile.py            # cuenta, no escribe
    python scripts/backfill_interest_profile.py --apply    # escribe

Escribe con `FilesystemMetadataStore.update` (candado y atómico): lo que otro
escritor puso mientras tanto no se pisa. Lee las trazas línea a línea y se
queda solo con lo que usa (la caja tiene 4 GB); una sesión que falla se cuenta
en `errors` y la pasada sigue.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

# Corre como `python scripts/backfill_interest_profile.py` desde hubara_agency/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.platform.state import FilesystemMetadataStore  # noqa: E402
from src.plugins.chats.shared import customer_profiles as cp  # noqa: E402


#: Lo que la pasada usa de cada archivo: (la marca que trae la línea, los campos).
_DECISIONS = (b'"compra"', ("at_ms", "stage", "capability", "by", "value", "answers"))
_TRACES = (b'"stage_out"', ("episode_id", "stage_out", "recorded_at_ms"))


def _rows(path: Path, needle: bytes, fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Las filas de un `.jsonl` que traen `needle`, solo con `fields`, leyendo
    línea a línea: las trazas de un cliente con meses de turnos pesan megas."""
    rows: list[dict[str, Any]] = []
    try:
        with path.open("rb") as handle:
            for line in handle:
                if needle not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append({key: row[key] for key in fields if key in row})
    except OSError:
        return rows
    return rows


def _tagged(metadata: dict[str, Any]) -> bool:
    profiles = metadata.get(cp.PROFILE_KEY)
    return isinstance(profiles, dict) and isinstance(profiles.get(cp.INTERESADO_SIN_COMPRA), dict)


def run(vault_dir: Path, *, apply: bool, now_ms: int) -> dict[str, Any]:
    """Recorre el vault; con `apply`, marca. Devuelve los conteos (sin teléfonos)."""
    vault = Path(vault_dir)
    store = FilesystemMetadataStore(vault)
    summary: dict[str, Any] = {
        "sessions": 0, "already_tagged": 0, "to_tag": Counter(), "by_campaign": Counter(),
        "written": 0, "errors": Counter(),
    }
    for session_dir in sorted(vault.glob("wa_*")):
        if not (session_dir / "metadata.json").exists():
            continue
        summary["sessions"] += 1
        try:
            outcome = _session(store, session_dir, apply=apply, now_ms=now_ms)
        except Exception as exc:  # noqa: BLE001 — una sesión rota no frena la pasada
            summary["errors"][type(exc).__name__] += 1
            continue
        if outcome is None:
            continue
        if outcome == "already_tagged":
            summary["already_tagged"] += 1
            continue
        found, written = outcome
        summary["to_tag"][found.source] += 1
        summary["by_campaign"][str(found.episode.get("sales_campaign") or "sin_campaña")] += 1
        summary["written"] += written
    for key in ("to_tag", "by_campaign", "errors"):
        summary[key] = dict(summary[key])
    summary["applied"] = apply
    return summary


def _session(
    store: FilesystemMetadataStore, session_dir: Path, *, apply: bool, now_ms: int
) -> str | tuple[cp.HistoryInterest, bool] | None:
    """Una sesión: «already_tagged», (lo que encontró, si lo escribió) o None."""
    sid = session_dir.name
    metadata = store.read(sid) or {}
    if _tagged(metadata):
        return "already_tagged"
    decisions = _rows(session_dir / "evals" / "decisions.jsonl", *_DECISIONS)
    traces = _rows(session_dir / "evals" / "turn_traces.jsonl", *_TRACES)
    found = cp.history_interest(metadata, decisions=decisions, traces=traces)
    if found is None:
        return None
    if not apply:
        return found, False

    def _stamp(fresh: dict[str, Any]) -> dict[str, Any] | None:
        # Se vuelve a decidir sobre lo fresco: si el ingest ya lo marcó en
        # vivo, o el cliente compró entretanto, no se toca.
        if _tagged(fresh):
            return None
        again = cp.history_interest(fresh, decisions=decisions, traces=traces)
        if again is None:
            return None
        if not cp.stamp_interest(fresh, now_ms=again.at_ms or now_ms, source=f"pasada:{again.source}", episode=again.episode):
            return None
        fresh[cp.PROFILE_KEY][cp.INTERESADO_SIN_COMPRA]["stamped_at_ms"] = now_ms
        return fresh

    return found, store.update(sid, _stamp) is not None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Escribe el perfil (sin esto, solo cuenta).")
    parser.add_argument("--vault", type=Path, default=None, help="El vault (por defecto, el del entorno).")
    args = parser.parse_args()
    if args.vault is None:
        from src.platform.config import WORKSPACE_VAULT_DIR

        args.vault = Path(WORKSPACE_VAULT_DIR)
    print(json.dumps(run(args.vault, apply=args.apply, now_ms=int(time.time() * 1000)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
