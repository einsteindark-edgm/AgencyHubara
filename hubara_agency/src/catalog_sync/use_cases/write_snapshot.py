"""Escritura atomica del snapshot.

Reglas de invariantes:
  1. snapshot.json se escribe ANTES que manifest.json. Si la escritura
     del manifest falla, el snapshot anterior y el nuevo son ambos validos
     (el reader igual sobrevive — `_load_manifest` tolera ausencia).
  2. El directorio `by_handle/` se LIMPIA y reescribe completo en cada
     sync para no dejar handles huerfanos cuando se borran productos.
  3. Cada archivo se escribe a `*.tmp` y luego `os.replace(...)` (atomico
     en POSIX).
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from uuid import uuid4

from src.catalog_sync.contracts import WriteSnapshotInput, WriteSnapshotResult


class WriteSnapshotUseCase:
    async def execute(
        self, input: WriteSnapshotInput
    ) -> WriteSnapshotResult:
        snap_dir = Path(input.snapshot_dir)
        snap_dir.mkdir(parents=True, exist_ok=True)
        by_handle_dir = snap_dir / "by_handle"

        version = uuid4().hex
        bytes_written = 0
        files_written = 0

        # 1) snapshot.json (atomico)
        snap_path = snap_dir / "snapshot.json"
        snap_tmp = snap_dir / "snapshot.json.tmp"
        snap_tmp.write_text(input.products_json, encoding="utf-8")
        os.replace(snap_tmp, snap_path)
        bytes_written += snap_path.stat().st_size
        files_written += 1

        # 2) by_handle/*.json (limpiar y reescribir)
        if by_handle_dir.exists():
            shutil.rmtree(by_handle_dir)
        by_handle_dir.mkdir(parents=True, exist_ok=True)

        products = json.loads(input.products_json)
        for prod in products:
            handle = str(prod["handle"])
            file_path = by_handle_dir / f"{handle}.json"
            tmp_path = by_handle_dir / f"{handle}.json.tmp"
            tmp_path.write_text(json.dumps(prod), encoding="utf-8")
            os.replace(tmp_path, file_path)
            bytes_written += file_path.stat().st_size
            files_written += 1

        # 3) manifest.json (ultimo — atomico)
        manifest = {
            "version": version,
            "fetched_at": input.fetched_at,
            "product_count": input.count,
            "source_etag": input.source_etag,
        }
        manifest_path = snap_dir / "manifest.json"
        manifest_tmp = snap_dir / "manifest.json.tmp"
        manifest_tmp.write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        os.replace(manifest_tmp, manifest_path)
        bytes_written += manifest_path.stat().st_size
        files_written += 1

        return WriteSnapshotResult(
            version=version,
            bytes_written=bytes_written,
            files_written=files_written,
        )
