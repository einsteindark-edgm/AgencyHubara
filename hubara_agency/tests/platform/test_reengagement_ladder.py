"""Escalera de reactivación (decisión del operador 2026-09-18).

Por CADA ghosting (el ancla es el último inbound del cliente): toques a
+2h, +4h, +8h, +14h y +20h — huecos 2h/2h/4h/6h/6h medidos desde el toque
anterior. Tras el quinto sin respuesta la escalera se agota: no se envía más.

Incidente de origen: runs `01a0b0da`…`01a0b586` — tras el primer gancho el
remarketing nunca volvió a tocar al cliente y el ciclo re-despachaba cada
45 min sin rastro determinista.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.platform.whatsapp.reengagement_ladder import (
    HOT_FIRST_GAP_MS,
    MAX_TEMPLATES_PER_24H,
    ladder_state,
    next_morning_at_ms,
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


# ── Frecuencia configurable (dashboard Agents → Remarketing → Frecuencia) ──────


def test_max_steps_agota_la_escalera_al_llegar_al_tope_configurado():
    sent = [(T0 + 2 * H, "free_form"), (T0 + 4 * H, "free_form")]
    state = ladder_state(T0 + 5 * H, _meta(sent), max_steps=2)
    assert state.exhausted is True
    assert state.due is False
    assert state.next_due_at_ms is None


def test_max_steps_mayor_que_los_consumidos_no_agota():
    sent = [(T0 + 2 * H, "free_form"), (T0 + 4 * H, "free_form")]
    state = ladder_state(T0 + 8 * H, _meta(sent), max_steps=3)
    assert state.exhausted is False
    assert state.due is True


def test_max_steps_cero_no_deja_salir_ningun_toque():
    state = ladder_state(T0 + 10 * H, _meta(), max_steps=0)
    assert state.exhausted is True
    assert state.due is False


def test_max_steps_no_pasa_de_los_peldaños_de_la_escalera():
    sent = [(T0 + h * H, "free_form") for h in (2, 4, 8, 14, 20)]
    assert ladder_state(T0 + 30 * H, _meta(sent), max_steps=99).exhausted is True
    sent = sent[:4]
    assert ladder_state(T0 + 30 * H, _meta(sent), max_steps=99).exhausted is False


def test_max_steps_aplica_desde_ya_a_quien_ya_recibio_mas_toques():
    # Bajar de 5 a 2: quien ya recibió 3 toques queda agotado en el acto.
    sent = [(T0 + h * H, "free_form") for h in (2, 4, 8)]
    assert ladder_state(T0 + 9 * H, _meta(sent), max_steps=2).exhausted is True


# ── El toque que agenda el siguiente (análisis 2026-10-13) ─────────────────────
# Al cliente que se calló tras el catálogo, el 2º toque a +2h del 1º respondía
# el 4 % (2 de 56); el 3º, a +8h, el doble. El 1º toque agenda el 2º para la
# mañana siguiente: la escalera respeta esa hora en vez del hueco fijo.


def test_un_toque_que_agenda_el_siguiente_manda_sobre_el_hueco():
    meta = _meta()
    meta["remarketing_touches"] = [
        {"at_ms": T0 + 2 * H, "kind": "free_form", "next_at_ms": T0 + 15 * H}
    ]
    assert ladder_state(T0 + 4 * H, meta).due is False, "el hueco de 2h ya no manda"
    state = ladder_state(T0 + 15 * H, meta)
    assert state.due is True
    assert state.next_due_at_ms == T0 + 15 * H


def test_record_touch_guarda_la_hora_del_siguiente():
    meta = _meta()
    record_touch(meta, T0 + 2 * H, "free_form", next_at_ms=T0 + 15 * H)
    assert meta["remarketing_touches"][-1] == {
        "at_ms": T0 + 2 * H, "kind": "free_form", "next_at_ms": T0 + 15 * H
    }
    assert ladder_state(T0 + 15 * H, meta).due is True


# «La mañana siguiente» sin salir de la ventana de 24 h (después el texto
# libre ya no se puede: Meta solo acepta plantilla). Horas de Bogotá.
BOG = ZoneInfo("America/Bogota")


def _bog(day: int, hour: int, minute: int = 0) -> int:
    return int(datetime(2026, 10, day, hour, minute, tzinfo=BOG).timestamp() * 1000)


def test_manana_siguiente_a_las_9():
    # Escribió a las 18:00, el 1er toque salió a las 20:00: el 2º, a las 9:00.
    assert next_morning_at_ms(_bog(13, 20), _bog(14, 18), BOG) == _bog(14, 9)


def test_manana_siguiente_se_adelanta_para_no_salir_de_la_ventana():
    # Escribió a las 10:30: la ventana cierra a las 10:30 del otro día; con 2 h
    # de margen el 2º toque sale a las 8:30, todavía de mañana.
    assert next_morning_at_ms(_bog(13, 12, 30), _bog(14, 10, 30), BOG) == _bog(14, 8, 30)


def test_sin_manana_dentro_de_la_ventana_va_a_la_tarde_del_mismo_dia():
    # Escribió a las 7:00: la mañana siguiente ya no cabe → 19:00 del mismo día.
    assert next_morning_at_ms(_bog(13, 9), _bog(14, 7), BOG) == _bog(13, 19)


def test_la_tarde_respeta_el_hueco_minimo_desde_el_primer_toque():
    # El 1er toque salió a las 18:00 y la mañana no cabe: no antes de las 20:00.
    assert next_morning_at_ms(_bog(13, 18), _bog(14, 9), BOG) == _bog(13, 20)


# Revisión de producción del PR #426 (2026-10-14).


def test_sin_espacio_para_el_hueco_normal_no_se_agenda_nada():
    # Escribió a las 19:00 y el 1er toque salió a las 18:00 del día siguiente:
    # la ventana (con su margen) cierra a las 17:00, ANTES del 1er toque. Antes
    # agendaba el 2º a las 17:00 (−1 h): dos textos libres seguidos. Ahora,
    # hueco normal (la escalera decide, con plantilla si ya cerró).
    assert next_morning_at_ms(_bog(14, 18), _bog(14, 19), BOG) is None


def test_la_tarde_no_cae_en_horas_de_silencio():
    # 1er toque a las 20:30, ventana hasta las 9:00: la tarde sería 22:30.
    assert next_morning_at_ms(_bog(13, 20, 30), _bog(14, 9), BOG) is None


def test_la_manana_sigue_al_horario_permitido_configurado():
    # Ventana con margen hasta las 9:00 del 14. Con el horario de siempre
    # (desde las 8:00) el 2º sale a las 9:00; si Terraform lo corre a las
    # 9:00, la mañana empezaría a las 9:15 y ya no cabe: va a la tarde del 13.
    assert next_morning_at_ms(_bog(13, 12, 30), _bog(14, 11), BOG) == _bog(14, 9)
    assert next_morning_at_ms(_bog(13, 12, 30), _bog(14, 11), BOG, allowed_from=(9, 0)) == _bog(13, 19)
