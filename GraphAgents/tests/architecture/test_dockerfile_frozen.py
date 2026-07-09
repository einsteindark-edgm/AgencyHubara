"""Guard: la imagen instala EXACTAMENTE lo que pinea uv.lock.

Incidente prod 2026-07-09 (rollout Window Strategist): el Dockerfile copiaba
solo pyproject.toml y corría `uv sync` sin --frozen → cada rebuild resolvía
deps FRESCAS. El de ese día trajo conductor-python 1.5.0 (lock: 1.4.0), cuyo
TaskHandler arranca workers con start method 'spawn' y muere con
"Can't pickle local object 'make_node_worker.<locals>.worker'"
(conductor-oss/conductor-python#264) — NINGÚN agente podía despachar en la
caja. El lock sin --frozen es decorativo.
"""
from __future__ import annotations

from pathlib import Path

GA = Path(__file__).resolve().parents[2]


def test_dockerfile_copia_el_lock_y_sincroniza_frozen() -> None:
    dockerfile = (GA / "Dockerfile").read_text(encoding="utf-8")
    assert "uv.lock" in dockerfile, (
        "el Dockerfile debe COPY uv.lock — sin él, uv sync resuelve fresco "
        "y el pin del lock es decorativo"
    )
    sync_lines = [l for l in dockerfile.splitlines() if "uv sync" in l]
    assert sync_lines, "el Dockerfile debe instalar deps con uv sync"
    for line in sync_lines:
        assert "--frozen" in line, (
            f"uv sync sin --frozen resuelve deps frescas en cada build: {line!r}"
        )
