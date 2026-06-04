#!/usr/bin/env python3
"""Importa los dashboards JSON de este directorio a una instancia de SigNoz.

Los `.json` de acá son la **fuente de verdad** (versionados en git), pero SigNoz
NO los toma solos: hay que crearlos vía su API. Este script lo hace — **idempotente**
(saltea los que ya existen por título), así que se puede re-correr sin duplicar, y
sirve para **re-crear todos los tableros tras un reset** del stack de SigNoz (cuando
se borra el volume de metadata, los dashboards se pierden — esto los repone).

Uso:
    SIGNOZ_API_KEY=<PAT> [SIGNOZ_URL=http://localhost:8080] \\
        python deploy/signoz/dashboards/import_dashboards.py

El PAT se crea una vez en la UI de SigNoz: Settings → API Keys → New key.
Solo stdlib (urllib) — no requiere instalar nada.
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


def _existing_titles() -> set[str]:
    _, listed = _request("GET", "/api/v1/dashboards")
    rows = listed.get("data") if isinstance(listed, dict) else listed
    out: set[str] = set()
    for x in rows or []:
        inner = x.get("data") or x
        if inner.get("title"):
            out.add(inner["title"])
    return out


def main() -> None:
    if not _KEY:
        sys.exit(
            "ERROR: seteá SIGNOZ_API_KEY (Settings → API Keys en la UI de SigNoz)."
        )
    print(f"SigNoz: {_URL}")
    try:
        existing = _existing_titles()
    except urllib.error.URLError as exc:
        sys.exit(f"ERROR: no se pudo contactar SigNoz en {_URL}: {exc}")

    created = skipped = errors = 0
    for f in sorted(_DIR.glob("*.json")):
        dash = json.loads(f.read_text(encoding="utf-8"))
        title = dash.get("title", f.stem)
        if title in existing:
            print(f"  = ya existe (skip): {title}")
            skipped += 1
            continue
        try:
            status, _ = _request("POST", "/api/v1/dashboards", dash)
            print(f"  + creado [{status}]: {title}")
            created += 1
        except urllib.error.HTTPError as exc:
            print(f"  ! error {exc.code} creando {title!r}: {exc.read()[:200]!r}")
            errors += 1

    print(f"\nListo: {created} creados, {skipped} ya existían, {errors} errores.")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
