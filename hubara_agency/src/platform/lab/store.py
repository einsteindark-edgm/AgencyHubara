"""`LabStorePort`: el almacén del laboratorio (`bench/`, `orders/`, `runs/`).

Dos adaptadores con el MISMO contrato (tests en tests/platform/lab/):

* `S3LabStore` — el bucket privado `agencyhubara-lab-<cuenta>` (Terraform,
  módulo lab-instance). `put_file` sube con `upload_file` (streaming por
  partes): el banco se exporta ARCHIVO POR ARCHIVO sin cargarlo en memoria,
  porque la caja de producción tiene poca RAM libre (plan §3.7).
* `FilesystemLabStore` — un directorio (tests, desarrollo local).

Las claves son relativas y no pueden salir de la raíz: nada de `..`, `/`
inicial ni segmentos vacíos.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Protocol, runtime_checkable


def check_key(key: str) -> str:
    parts = key.split("/")
    if not key or key.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise ValueError(f"clave inválida para el almacén del laboratorio: {key!r}")
    return key


@runtime_checkable
class LabStorePort(Protocol):
    def put_file(self, key: str, path: Path) -> None: ...

    def put_bytes(self, key: str, data: bytes) -> None: ...

    def get_bytes(self, key: str) -> bytes | None: ...

    def list_keys(self, prefix: str) -> list[str]: ...

    def list_children(self, prefix: str) -> list[str]:
        """Nombres de las "carpetas" inmediatas bajo `prefix` (sin recorrer cada objeto)."""
        ...


class FilesystemLabStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / check_key(key)

    def put_file(self, key: str, path: Path) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

    def put_bytes(self, key: str, data: bytes) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def get_bytes(self, key: str) -> bytes | None:
        target = self._path(key)
        return target.read_bytes() if target.is_file() else None

    def list_keys(self, prefix: str) -> list[str]:
        if not self.root.exists():
            return []
        keys = (p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file())
        return sorted(k for k in keys if k.startswith(prefix))

    def list_children(self, prefix: str) -> list[str]:
        folder = self.root / check_key(prefix.rstrip("/")) if prefix.strip("/") else self.root
        if not folder.is_dir():
            return []
        return sorted(p.name for p in folder.iterdir() if p.is_dir())


class S3LabStore:
    def __init__(self, bucket: str, *, client: Any = None, region: str | None = None) -> None:
        self.bucket = bucket
        self._client = client
        self._region = region

    def _s3(self) -> Any:
        if self._client is None:
            import boto3  # noqa: PLC0415 — perezoso: el import del módulo no exige AWS

            self._client = boto3.client("s3", region_name=self._region) if self._region else boto3.client("s3")
        return self._client

    def put_file(self, key: str, path: Path) -> None:
        self._s3().upload_file(str(path), self.bucket, check_key(key))

    def put_bytes(self, key: str, data: bytes) -> None:
        self._s3().put_object(Bucket=self.bucket, Key=check_key(key), Body=data)

    def get_bytes(self, key: str) -> bytes | None:
        try:
            response = self._s3().get_object(Bucket=self.bucket, Key=check_key(key))
        except Exception as exc:  # noqa: BLE001 — solo "no existe" es None; el resto propaga
            code = getattr(exc, "response", {}).get("Error", {}).get("Code")
            if code in {"NoSuchKey", "404", "NotFound"}:
                return None
            raise
        return response["Body"].read()

    def list_keys(self, prefix: str) -> list[str]:
        keys: list[str] = []
        for page in self._s3().get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(obj["Key"] for obj in page.get("Contents") or [])
        return sorted(keys)

    def list_children(self, prefix: str) -> list[str]:
        prefix = prefix if prefix.endswith("/") else prefix + "/"
        names: list[str] = []
        pages = self._s3().get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix, Delimiter="/")
        for page in pages:
            names.extend(p["Prefix"][len(prefix):].rstrip("/") for p in page.get("CommonPrefixes") or [])
        return sorted(n for n in names if n)
