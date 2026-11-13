"""Dónde queda anotado lo que Colossus dijo de cada comprobante de pago.

Sombra (tarjeta 33 del ciclo): el veredicto no cambia nada para el cliente ni
para el equipo; se guarda para compararlo después con lo que el equipo hizo con
el pedido (informe de estrategia).

  <vault>/<sesión>/evals/receipt_checks.jsonl   una fila por comprobante, solo
                                                códigos: cuándo, episodio,
                                                pedido, veredicto, motivo, origen
  <vault>/_receipts/references.jsonl            huella de la referencia del pago
                                                y huella del pedido, para ver un
                                                comprobante repetido entre pedidos

Nada de lo que se lee en el comprobante (monto, cuenta, referencia) se escribe.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def checks_path(vault_dir: Path, session_id: str) -> Path:
    return vault_dir / session_id / "evals" / "receipt_checks.jsonl"


def _references_path(vault_dir: Path) -> Path:
    return vault_dir / "_receipts" / "references.jsonl"


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _rows(path: Path) -> list[dict[str, Any]]:
    """Las filas válidas; una línea cortada o un archivo que no está nunca
    tumban a quien lee."""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict):
            rows.append(data)
    return rows


def append_check(vault_dir: Path, session_id: str, row: dict[str, Any]) -> None:
    _append(checks_path(vault_dir, session_id), row)


def read_checks(vault_dir: Path, session_id: str) -> list[dict[str, Any]]:
    return _rows(checks_path(vault_dir, session_id))


def remember_reference(vault_dir: Path, *, reference: str, order: str) -> None:
    """``reference`` y ``order`` son huellas, nunca los números."""
    _append(_references_path(vault_dir), {"reference": reference, "order": order})


def references_of_other_orders(vault_dir: Path, *, order: str) -> frozenset[str]:
    """Las huellas de referencias ya vistas en un pedido que no es ``order``."""
    return frozenset(
        str(row["reference"])
        for row in _rows(_references_path(vault_dir))
        if row.get("reference") and row.get("order") != order
    )
