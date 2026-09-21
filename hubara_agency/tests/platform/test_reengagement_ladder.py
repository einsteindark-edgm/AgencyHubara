"""Escalera de reactivación (decisión del operador 2026-09-18).

Por CADA ghosting (el ancla es el último inbound del cliente): toques a
+2h, +4h, +8h, +14h y +20h — huecos 2h/2h/4h/6h/6h medidos desde el toque
anterior. Tras el quinto sin respuesta la escalera se agota: no se envía más.

Incidente de origen: runs `01a0b0da`…`01a0b586` — tras el primer gancho el
remarketing nunca volvió a tocar al cliente y el ciclo re-despachaba cada
45 min sin rastro determinista.
"""
from __future__ import annotations

from src.platform.whatsapp.reengagement_ladder import (
    HOT_FIRST_GAP_MS,
    MAX_TEMPLATES_PER_24H,
    ladder_state,
    record_touch,
)

H = 60 * 60 * 1000
T0 = 1_800_000_000_000  # último inbound del cliente


def _meta(touches: list[tuple[int, str]] | None = None, **extra) -> dict:
    return {
        "last_inbound_at_ms": T0,
        "remarketing_touches": [
            {"at_ms": at, "kind": kind} for at, kind in (touches or [])
        ],
        **extra,
    }


def test_primer_toque_no_vence_antes_de_2h():
    state = ladder_state(T0 + 2 * H - 1, _meta())
    assert state.step == 0
    assert state.due is False
    assert state.next_due_at_ms == T0 + 2 * H


def test_primer_toque_vence_a_las_2h():
    assert ladder_state(T0 + 2 * H, _meta()).due is True


def test_gancho_transaccional_adelanta_el_primer_toque_a_30min():
    state = ladder_state(T0 + 30 * 60 * 1000, _meta(), first_gap_ms=HOT_FIRST_GAP_MS)
    assert state.due is True


def test_huecos_2_2_4_6_6_se_miden_desde_el_toque_anterior():
    # toque 1 a +2h → el 2 vence a +4h; toque 2 a +4h → el 3 a +8h;
    # toque 3 a +8h → el 4 a +14h; toque 4 a +14h → el 5 a +20h.
    esperado = [(1, 4), (2, 8), (3, 14), (4, 20)]
    sent: list[tuple[int, str]] = []
    prev_due_h = 2
    for step, due_h in esperado:
        sent.append((T0 + prev_due_h * H, "free_form"))
        state = ladder_state(T0 + due_h * H - 1, _meta(sent))
        assert state.step == step
        assert state.due is False, f"toque {step + 1} vencido antes de +{due_h}h"
        assert ladder_state(T0 + due_h * H, _meta(sent)).due is True
        prev_due_h = due_h


def test_toque_atrasado_por_quiet_hours_corre_el_siguiente():
    # El 4º toque debía salir a +14h pero quiet hours lo atrasó a +20h: el 5º
    # vence 6h DESPUÉS del 4º real (+26h), no a las +20h del plan original.
    sent = [
        (T0 + 2 * H, "free_form"),
        (T0 + 4 * H, "free_form"),
        (T0 + 8 * H, "free_form"),
        (T0 + 20 * H, "free_form"),
    ]
    assert ladder_state(T0 + 25 * H, _meta(sent)).due is False
    assert ladder_state(T0 + 26 * H, _meta(sent)).due is True


def test_tras_cinco_toques_la_escalera_se_agota():
    sent = [(T0 + h * H, "free_form") for h in (2, 4, 8, 14, 20)]
    state = ladder_state(T0 + 40 * H, _meta(sent))
    assert state.exhausted is True
    assert state.due is False


def test_un_inbound_nuevo_reinicia_la_escalera():
    # El cliente respondió DESPUÉS de los toques viejos: ancla nueva, paso 0.
    sent = [(T0 - 30 * H, "free_form"), (T0 - 28 * H, "free_form")]
    state = ladder_state(T0 + 2 * H, _meta(sent))
    assert state.step == 0
    assert state.due is True


def test_la_abstencion_consume_el_peldano():
    # Incidente 01a0b0da: NO_MESSAGE no dejaba rastro → re-despacho cada 45 min.
    state = ladder_state(T0 + 3 * H, _meta([(T0 + 2 * H, "abstained")]))
    assert state.step == 1
    assert state.due is False


def test_tope_de_plantillas_por_dia():
    sent = [(T0 + 2 * H, "template"), (T0 + 4 * H, "template")]
    state = ladder_state(T0 + 8 * H, _meta(sent))
    assert MAX_TEMPLATES_PER_24H == 2
    assert state.template_cap_reached is True
    # 24h después del primero vuelve a haber cupo.
    assert ladder_state(T0 + 26 * H + 1, _meta(sent)).template_cap_reached is False


def test_record_touch_agrega_y_acota():
    metadata = _meta()
    for i in range(60):
        record_touch(metadata, T0 + i, "free_form")
    touches = metadata["remarketing_touches"]
    assert touches[-1] == {"at_ms": T0 + 59, "kind": "free_form"}
    assert len(touches) <= 40
