"""Las versiones publicadas de los paquetes de decisión (premortem 2026-10-02).

* **Una versión publicada no se edita** (PAQUETES_DE_DECISION.md §12, regla
  5): otra pregunta, otro umbral u otro texto = otra versión. Nada lo
  impedía: la traza seguiría diciendo `ventas@1` con otra inteligencia. La
  huella de cada versión queda congelada aquí; cambiarla es a propósito.
* **Una versión publicada corre en el motor de hoy**: SSM puede nombrarla (un
  rollback de la promoción) y no se puede editar; subir `ENGINE_CONTRACT`
  sin seguir aceptando el contrato viejo la dejaba fuera (DB002).
* **El oráculo del paquete es el que usa el código**: un paquete calibrado
  para otro Jev correría contra jev-1.13 sin aviso.
* **Cada cambio que lee el lector del Order Sentinel tiene su veredicto**: una
  opción nueva sin etapa daba KeyError en ejecución (tragado como «Jev cayó»).
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

_PLUGINS = Path(__file__).resolve().parents[2] / "src" / "plugins"
#: forge no viaja a un clon (`copy_exclude`): ahí los paquetes son de otra
#: tienda (forge reescribe su dominio) y sus huellas, las de ese repo.
IN_FORGE_CLONE = not (Path(__file__).resolve().parents[3] / "forge").is_dir()
#: id@versión → sha256 de sus archivos (ruta relativa + contenido, en orden).
PUBLISHED = {
    "ventas@1": "54a57cac9e6764f53cf50689891ab089e38184a0895f6d04744d141b6d6670b9",
    "ventas-2@2": "b2554fcc15954ce093dbb9abfb3bcc363f21d9964ec7f0529b0d30048d5a4320",
    "centinela@1": "59200ee02051d7867a113259a44b396be8ae448c7cec88c2dd61e0f7767285ef",
}


def _bundles() -> dict[str, Path]:
    out: dict[str, Path] = {}
    for head in sorted(_PLUGINS.glob("**/decisions/bundles/*/bundle.yaml")):
        data = yaml.safe_load(head.read_text(encoding="utf-8"))
        out[f"{data['id']}@{data['version']}"] = head.parent
    return out


def _digest(folder: Path) -> str:
    h = hashlib.sha256()
    for path in sorted(folder.rglob("*.yaml")):
        h.update(str(path.relative_to(folder)).encode() + b"\0" + path.read_bytes() + b"\0")
    return h.hexdigest()


@pytest.mark.parametrize("ref", sorted(PUBLISHED))
def test_a_published_version_is_never_edited(ref: str) -> None:
    folder = _bundles().get(ref)
    if folder is None or IN_FORGE_CLONE:
        pytest.skip(f"{ref}: clon de forge (otra tienda, otras huellas)")

    assert _digest(folder) == PUBLISHED[ref], (
        f"{ref} cambió: un paquete publicado es inmutable. Otra pregunta, otro umbral u otro texto = "
        "otra versión (copia la carpeta, cambia id y version). Solo si es a propósito (p. ej. un comentario), "
        f"actualiza la huella: {_digest(folder)}"
    )


@pytest.mark.parametrize("ref", sorted(PUBLISHED))
def test_a_published_version_still_runs_on_this_engine(ref: str) -> None:
    from src.sdk.decisionkit import check_bundle

    folder = _bundles().get(ref)
    if folder is None:
        pytest.skip(f"{ref} no viaja aquí (clon de forge)")

    assert [str(d) for d in check_bundle(folder, folder.parent / "builtins.yaml")] == [], (
        f"{ref} ya no certifica: un motor nuevo sigue corriendo los contratos publicados (SUPPORTED_CONTRACTS)"
    )


def test_every_bundle_version_is_frozen_here() -> None:
    assert set(_bundles()) <= set(PUBLISHED), "un paquete nuevo: agrega su huella a PUBLISHED"


def test_the_sales_bundles_are_calibrated_for_the_oracle_of_the_engine() -> None:
    from src.plugins.chats.agent.sales.decisions.profiles import _raw_profiles

    oracles = {raw["oracle"] for raw in _raw_profiles().values() if raw.get("turn") == "bundle"}
    for ref, folder in _bundles().items():
        if "chats" in folder.parts:
            assert yaml.safe_load((folder / "bundle.yaml").read_text(encoding="utf-8"))["oracle"] in oracles, ref


def test_the_sentinel_bundle_is_calibrated_for_the_oracle_the_reader_asks() -> None:
    from src.plugins.order_sentinel.agent import decisions
    from src.plugins.order_sentinel.agent.cycle.use_cases.readings import ORACLE_PROFILE

    assert decisions.active_bundle().oracle == ORACLE_PROFILE


def test_every_change_the_sentinel_reads_has_its_verdict() -> None:
    from src.plugins.order_sentinel.agent import decisions
    from src.plugins.order_sentinel.agent.cycle.use_cases import readings

    [question] = decisions.active_bundle().capability("cambio").spec.questions

    assert set(question.criteria) == {"nada", "pago", *readings._STAGE_OF}
