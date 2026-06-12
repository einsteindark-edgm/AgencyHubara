"""F-SDK-1 — el manifest tipado es fiel al schema y a los manifests reales.

Tres contratos:

1. **Realidad → modelo**: TODO manifest del repo parsea con ``PluginManifest``
   (C0) y declara un ``archetype`` válido (P-29A). Un campo nuevo usado en un
   manifest sin tiparse acá rompe ESTE test, no producción.
2. **Modelo ↔ schema**: los enums (``archetype``, ``via``) viven una vez en
   ``manifest_model.py`` y el ``plugin.schema.yaml`` debe espejarlos EXACTO —
   la clase de drift L-10 (backend agrega valor, contrato de afuera quedó
   viejo) truena acá.
3. **Modelo estricto**: top-level desconocido / id inválido → rechazo con
   mensaje accionable (``ManifestValidationError``).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.platform.plugin_manifest import all_manifests
from src.sdk.diagnostics import DIAGNOSTICS, format_diagnostic, get_diagnostic
from src.sdk.manifest_model import (
    ARCHETYPES,
    VIAS,
    ManifestValidationError,
    parse_manifest,
)

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3]
    / "frontend_dashboard"
    / "src"
    / "plugins"
    / "_schema"
    / "plugin.schema.yaml"
)


def _schema() -> dict:
    return yaml.safe_load(SCHEMA_PATH.read_text(encoding="utf-8"))


# ── 1. Realidad → modelo ────────────────────────────────────────────────────

@pytest.mark.parametrize("plugin_id,manifest", all_manifests())
def test_real_manifest_parses_and_id_matches_dir(plugin_id: str, manifest: dict) -> None:
    typed = parse_manifest(manifest, source=f"plugin {plugin_id!r}")
    assert typed.id == plugin_id, (
        f"manifest id {typed.id!r} ≠ nombre del directorio {plugin_id!r} (P-26)"
    )


@pytest.mark.parametrize("plugin_id,manifest", all_manifests())
def test_real_manifest_declares_valid_archetype(plugin_id: str, manifest: dict) -> None:
    typed = parse_manifest(manifest, source=f"plugin {plugin_id!r}")
    assert typed.archetype in ARCHETYPES, (
        f"[P-29A] plugin {plugin_id!r} sin archetype válido (got "
        f"{typed.archetype!r}). Declará archetype: {' | '.join(ARCHETYPES)} "
        f"en su plugin.yaml — no existe 'custom' (docs/_sdk/05-arquetipos.md)."
    )


# ── 2. Modelo ↔ schema (anti-drift L-10) ───────────────────────────────────

def test_schema_archetype_enum_mirrors_model() -> None:
    schema_enum = _schema()["properties"]["archetype"]["enum"]
    assert tuple(schema_enum) == ARCHETYPES, (
        f"drift modelo↔schema en archetype: schema={schema_enum} vs "
        f"modelo={list(ARCHETYPES)} — actualizá AMBOS en el mismo PR"
    )


def test_schema_via_enum_mirrors_model() -> None:
    workers_props = (
        _schema()["properties"]["agent"]["properties"]["workers"]["items"]["properties"]
    )
    via_enum = workers_props["transitions"]["items"]["properties"]["action"][
        "properties"
    ]["via"]["enum"]
    assert tuple(via_enum) == VIAS, (
        f"drift modelo↔schema en transitions.action.via: schema={via_enum} vs "
        f"modelo={list(VIAS)} — actualizá AMBOS en el mismo PR"
    )


def test_schema_declares_worker_dashboard_block() -> None:
    workers_props = (
        _schema()["properties"]["agent"]["properties"]["workers"]["items"]["properties"]
    )
    assert "dashboard" in workers_props, (
        "el bloque dashboard: de workers desapareció del schema — P-15/P-17 "
        "dependen de él (manifest veraz, INV-3)"
    )


# ── 3. Modelo estricto ──────────────────────────────────────────────────────

def test_unknown_top_level_key_rejected() -> None:
    with pytest.raises(ManifestValidationError, match="campo_inventado"):
        parse_manifest({"id": "x", "version": "0.1.0", "campo_inventado": 1})


def test_bad_id_pattern_rejected() -> None:
    with pytest.raises(ManifestValidationError, match="id"):
        parse_manifest({"id": "Bad-Id", "version": "0.1.0"})


def test_bad_via_rejected() -> None:
    manifest = {
        "id": "x",
        "version": "0.1.0",
        "agent": {
            "workers": [
                {
                    "name": "w",
                    "module": "src.plugins.x.workers.w",
                    "task_queue": "queue-x",
                    "transitions": [
                        {
                            "id": "t",
                            "on_event": "SomethingEvent",
                            "action": {"via": "teleport", "target_workflow": "X"},
                        }
                    ],
                }
            ]
        },
    }
    with pytest.raises(ManifestValidationError, match="via"):
        parse_manifest(manifest)


# ── Diagnósticos: catálogo coherente ────────────────────────────────────────

def test_diagnostics_catalog_is_complete() -> None:
    for code, d in DIAGNOSTICS.items():
        assert code == d.code
        assert d.title and d.fix and d.ref, f"{code}: diagnóstico incompleto"


def test_format_diagnostic_renders_rustc_style() -> None:
    out = format_diagnostic("P-16", "queue ajena: queue-sales-agent", location="src/plugins/eta/workers/eta.py")
    assert "error[P-16]" in out and "fix:" in out and "-->" in out
    assert get_diagnostic("p-16").code == "P-16"  # case-insensitive lookup
