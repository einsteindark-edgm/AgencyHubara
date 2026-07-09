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


def test_el_lock_esta_commiteado() -> None:
    # Incidente 2 del mismo día (deploy 29057054647): uv.lock estaba en
    # .gitignore — el checkout de CI no lo tenía y el COPY del Dockerfile
    # murió con "not found". Un lock que no viaja en git no pinea nada.
    assert (GA / "uv.lock").exists(), (
        "falta GraphAgents/uv.lock en el checkout — ¿volvió a .gitignore?"
    )


def test_dockerignore_no_excluye_el_lock() -> None:
    # El COPY del lock falla silencioso... en CI (checksum not found) si
    # .dockerignore lo excluye — el guard del Dockerfile solo no alcanza
    # (deploy 29057054647).
    dockerignore = GA / ".dockerignore"
    if dockerignore.exists():
        lines = [
            l.strip()
            for l in dockerignore.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")
        ]
        assert not any("uv.lock" in l for l in lines), (
            ".dockerignore excluye uv.lock — el COPY del Dockerfile no puede "
            f"verlo y el build muere: {lines}"
        )


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
