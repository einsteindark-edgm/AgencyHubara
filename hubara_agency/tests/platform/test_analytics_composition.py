"""Tests del composition root de analytics.

Guard anti doble-Lead (2026-07-01): el sink `MetaConversionsAPISink` era un
stub divergente del camino CAPI real (`send_capi_event_activity`):
  * mandaba "Lead" al PRIMER touch (referral capturado) mientras la activity
    manda LeadSubmitted al cierre del episodio → event_ids distintos, Meta
    NO dedupea entre ambos → Leads inflados si se seteaban META_PIXEL_ID +
    META_CAPI_DATASET_ID a la vez.
  * pixel_id vs dataset_id, event_id UUID random (sin idempotencia), sin
    ventana de atribución de 7 días, 0 tests.

Decisión: el ÚNICO camino a Meta CAPI es la activity. El bus de analytics
conserva solo el filesystem sink (auditoría local).
"""
from __future__ import annotations

import importlib

import src.platform.analytics.bus as bus_module
from src.platform.analytics.composition import setup_analytics
from src.platform.analytics.filesystem_sink import FilesystemAnalyticsSink


def _reset_analytics_singletons() -> None:
    """setup_analytics y get_event_bus son lru_cache(1) — limpiar AMBAS caches
    para que cada test parta de cero.

    OJO: NO usar `importlib.reload(bus_module)` acá — reload crea una función
    `get_event_bus` NUEVA (con cache vacía) pero `composition.py` conserva la
    referencia VIEJA importada por valor, cuya cache (con el bus ya poblado
    por tests anteriores) sigue viva → `setup_analytics()` agregaba un SEGUNDO
    sink al bus viejo (assert 2 == 1, dependiente del orden de la corrida).
    """
    import src.platform.analytics.composition as comp

    setup_analytics.cache_clear()
    # La referencia que composition realmente usa + la del módulo bus (pueden
    # divergir si algún test hizo reload antes).
    comp.get_event_bus.cache_clear()
    bus_module.get_event_bus.cache_clear()


class TestSetupAnalytics:
    def test_only_filesystem_sink_even_with_pixel_env(
        self, monkeypatch, tmp_path
    ) -> None:
        """Aunque el operador setee META_PIXEL_ID + META_CAPI_ACCESS_TOKEN,
        el bus NO registra ningún sink que hable con Meta — el único camino
        CAPI es send_capi_event_activity (evita el doble-Lead)."""
        monkeypatch.setenv("META_PIXEL_ID", "1234567890")
        monkeypatch.setenv("META_CAPI_ACCESS_TOKEN", "EAA_FAKE")
        import src.platform.analytics.composition as comp

        monkeypatch.setattr(comp, "WORKSPACE_VAULT_DIR", tmp_path)
        # Reset COMPLETO (cache + bus global): con solo cache_clear(), si otro
        # test de la corrida ya había corrido setup_analytics, el bus global
        # quedaba con su sink y acá se agregaba un SEGUNDO → assert 2 == 1
        # (fallo dependiente del orden — pasaba en aislamiento).
        _reset_analytics_singletons()

        bus = comp.setup_analytics()

        assert len(bus.sinks) == 1
        assert isinstance(bus.sinks[0], FilesystemAnalyticsSink)

    def test_meta_capi_sink_module_is_gone(self) -> None:
        """El módulo del stub fue eliminado — importarlo debe fallar. Si
        alguien lo revive, este test lo frena (la funcionalidad vive en
        src/platform/whatsapp/capi_activity.py)."""
        import pytest

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("src.platform.analytics.meta_capi_sink")
