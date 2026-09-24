"""Composición del laboratorio: almacén y lanzador según el entorno.

* `LAB_BUCKET` (Terraform, /hubara/<tenant>/LAB_BUCKET) → `S3LabStore`.
* Sin bucket (o con el placeholder de SSM) y con `LAB_STORE_DIR` → disco
  (desarrollo y tests). Sin ninguno de los dos → `None`: el laboratorio está
  apagado y la API lo dice (503), nunca inventa datos.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from src.platform.config import AWS_REGION
from src.platform.lab.launcher import Boto3LabLauncher, LabBoxLauncher
from src.platform.lab.store import FilesystemLabStore, LabStorePort, S3LabStore


def _real(value: str | None) -> str:
    v = (value or "").strip()
    return "" if v.startswith("PLACEHOLDER") else v


@lru_cache(maxsize=1)
def get_lab_store() -> LabStorePort | None:
    bucket = _real(os.getenv("LAB_BUCKET"))
    if bucket:
        return S3LabStore(bucket, region=os.getenv("AWS_REGION") or AWS_REGION)
    local = _real(os.getenv("LAB_STORE_DIR"))
    return FilesystemLabStore(Path(local)) if local else None


@lru_cache(maxsize=1)
def get_lab_launcher() -> LabBoxLauncher:
    return Boto3LabLauncher()
