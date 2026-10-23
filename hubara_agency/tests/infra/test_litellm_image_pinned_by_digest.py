"""El proxy LiteLLM se baja por su huella (digest), no solo por la etiqueta.

El 2026-10-21 alguien robó las credenciales de publicación de LiteLLM y subió
la 1.107.0 con malware, también a las etiquetas `latest`, `main-latest` y
`main-stable` de las imágenes: quien hizo `pull` de esas etiquetas esa mañana se
trajo un ladrón de llaves. Nuestro `v1.86.2` no lo tocaron, pero cada deploy
hace `docker compose pull`, así que una etiqueta re-publicada nos llegaría sola
a la caja de prod con todas las llaves en su `.env`. Con `@sha256:` Docker baja
exactamente la imagen que probamos o falla.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]

#: Todos los lugares que levantan el proxy (prod, lab, local, k8s, CI).
_FILES = [
    "infra/compose/docker-compose.prod.yml",
    "infra/compose/lab/docker-compose.lab.yml",
    "hubara_agency/docker-compose.base.yml",
    "hubara_agency/docker-compose.local.yml",
    "hubara_agency/k8s/aws-produccion/litellm-deployment.yaml",
    "exoclaw-temporal/docker-compose.yml",
    ".github/workflows/golden-eval.yml",
]

_IMAGE = re.compile(r"ghcr\.io/berriai/litellm[^\s\"']*")
_PINNED = re.compile(r"ghcr\.io/berriai/litellm:v[\d.]+@sha256:[0-9a-f]{64}")


def _references(path: str) -> list[str]:
    return _IMAGE.findall((_REPO / path).read_text(encoding="utf-8"))


def test_the_guard_sees_every_place_that_runs_the_proxy() -> None:
    for path in _FILES:
        assert _references(path), f"{path} ya no menciona la imagen de LiteLLM: actualizá la lista"


@pytest.mark.parametrize("path", _FILES)
def test_the_litellm_image_is_pinned_by_digest(path: str) -> None:
    for ref in _references(path):
        assert _PINNED.fullmatch(ref), f"{path}: {ref} se baja por etiqueta; fijala con @sha256:"


def test_every_place_runs_the_same_image() -> None:
    digests = {ref.split("@", 1)[-1] for path in _FILES for ref in _references(path)}
    assert len(digests) == 1, f"el proxy corre imágenes distintas según el lugar: {sorted(digests)}"
