"""Frecuencia del remarketing: cuántos toques máximo hace el bot por negocio.

El dashboard (Agents → Remarketing → Frecuencia) elige un número de 0 a N; el
TECHO N lo fija Terraform (`REMARKETING_MAX_TOUCHES`, por tenant) y el
dashboard nunca lo supera. Sin nada guardado manda el techo (= el
comportamiento de siempre, 5 toques).
"""
from __future__ import annotations

import pytest

from src.platform.whatsapp.reengagement_frequency import (
    CEILING_ENV,
    ceiling,
    effective_max_touches,
    read_state,
    set_max_touches,
)
from src.platform.whatsapp.reengagement_ladder import LADDER_GAPS_MS


@pytest.fixture(autouse=True)
def _sin_techo_de_terraform(monkeypatch):
    monkeypatch.delenv(CEILING_ENV, raising=False)


def test_sin_nada_guardado_manda_el_techo_que_es_la_escalera_completa(tmp_path):
    assert effective_max_touches(tmp_path) == len(LADDER_GAPS_MS)


def test_el_techo_sale_de_terraform_y_no_pasa_de_la_escalera(tmp_path, monkeypatch):
    monkeypatch.setenv(CEILING_ENV, "3")
    assert ceiling() == 3
    assert effective_max_touches(tmp_path) == 3
    monkeypatch.setenv(CEILING_ENV, "99")
    assert ceiling() == len(LADDER_GAPS_MS)


def test_techo_ilegible_cae_a_la_escalera_completa(monkeypatch):
    monkeypatch.setenv(CEILING_ENV, "muchos")
    assert ceiling() == len(LADDER_GAPS_MS)


def test_guardar_un_valor_dentro_del_techo_lo_hace_efectivo(tmp_path):
    outcome = set_max_touches(tmp_path, 2, actor="operador", now_ms=1_000)
    assert outcome.applied is True
    assert effective_max_touches(tmp_path) == 2
    state = read_state(tmp_path)
    assert state.max_touches == 2
    assert state.updated_by == "operador"
    assert state.updated_at_ms == 1_000


def test_cero_es_valido_y_apaga_la_reactivacion(tmp_path):
    assert set_max_touches(tmp_path, 0, actor="operador", now_ms=1).applied is True
    assert effective_max_touches(tmp_path) == 0


def test_guardar_por_encima_del_techo_se_rechaza_y_no_cambia_nada(tmp_path, monkeypatch):
    monkeypatch.setenv(CEILING_ENV, "3")
    set_max_touches(tmp_path, 2, actor="a", now_ms=1)
    outcome = set_max_touches(tmp_path, 4, actor="b", now_ms=2)
    assert outcome.applied is False
    assert outcome.reason == "above_ceiling"
    assert effective_max_touches(tmp_path) == 2


@pytest.mark.parametrize("raro", [-1, 2.5, "3", True, None])
def test_valores_que_no_son_un_entero_valido_se_rechazan(tmp_path, raro):
    outcome = set_max_touches(tmp_path, raro, actor="a", now_ms=1)
    assert outcome.applied is False
    assert outcome.reason == "invalid_value"


def test_si_baja_el_techo_lo_guardado_no_lo_pasa(tmp_path, monkeypatch):
    set_max_touches(tmp_path, 5, actor="a", now_ms=1)
    monkeypatch.setenv(CEILING_ENV, "2")
    assert effective_max_touches(tmp_path) == 2


def test_archivo_corrupto_cae_al_techo_sin_romper(tmp_path):
    path = tmp_path / "_rollout" / "remarketing_frequency.json"
    path.parent.mkdir(parents=True)
    path.write_text("{no es json", encoding="utf-8")
    assert effective_max_touches(tmp_path) == len(LADDER_GAPS_MS)
