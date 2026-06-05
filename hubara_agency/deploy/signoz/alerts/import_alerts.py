#!/usr/bin/env python3
"""Importa las reglas de alerta JSON de este directorio a una instancia de SigNoz.

Mismo patrón que `../dashboards/import_dashboards.py`: los `.json` de acá son la
fuente de verdad (versionados en git), idempotente (saltea por nombre de alerta),
re-corrible tras un reset del stack de SigNoz.

Uso:
    SIGNOZ_API_KEY=<PAT> [SIGNOZ_URL=http://localhost:8080] \\
        python deploy/signoz/alerts/import_alerts.py

El PAT se crea una vez en la UI: Settings -> API Keys -> New key.
Solo stdlib (urllib).

NOTA: el schema de reglas de SigNoz es sensible a la versión (acá v0.126). La query
de la alerta replica EXACTAMENTE la del widget 'Score promedio por conversación' del
dashboard 05-calidad-llm.json (que sí funciona), así que el formato debería ser
válido. Si la API rechaza el POST, ver README.md para crearla desde la UI (3 clicks).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_URL = os.getenv("SIGNOZ_URL", "http://localhost:8080").rstrip("/")
_KEY = os.getenv("SIGNOZ_API_KEY", "").strip()
_DIR = Path(__file__).resolve().parent


def _request(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{_URL}{path}",
        data=data,
        method=method,
        headers={"SIGNOZ-API-KEY": _KEY, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.status, json.loads(resp.read() or b"null")


def _existing_alert_names() -> set[str]:
    """Nombres de alertas ya creadas (defensivo ante variaciones de shape)."""
    _, listed = _request("GET", "/api/v1/rules")
    data = listed.get("data") if isinstance(listed, dict) else listed
    rules = (data or {}).get("rules") if isinstance(data, dict) else data
    out: set[str] = set()
    for r in rules or []:
        inner = r.get("data") or r
        name = inner.get("alert") or inner.get("alertName")
        if name:
            out.add(name)
    return out


def main() -> None:
    if not _KEY:
        sys.exit("ERROR: seteá SIGNOZ_API_KEY (Settings -> API Keys en la UI de SigNoz).")
    print(f"SigNoz: {_URL}")
    try:
        existing = _existing_alert_names()
    except urllib.error.URLError as exc:
        sys.exit(f"ERROR: no se pudo contactar SigNoz en {_URL}: {exc}")

    created = skipped = errors = 0
    for f in sorted(_DIR.glob("*.json")):
        rule = json.loads(f.read_text(encoding="utf-8"))
        name = rule.get("alert", f.stem)
        if name in existing:
            print(f"  = ya existe (skip): {name}")
            skipped += 1
            continue
        try:
            status, _ = _request("POST", "/api/v1/rules", rule)
            print(f"  + creada [{status}]: {name}")
            created += 1
        except urllib.error.HTTPError as exc:
            print(f"  ! error {exc.code} creando {name!r}: {exc.read()[:300]!r}")
            print("    -> si el schema cambió, creá la alerta desde la UI (ver README.md).")
            errors += 1

    print(f"\nListo: {created} creadas, {skipped} ya existían, {errors} errores.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
