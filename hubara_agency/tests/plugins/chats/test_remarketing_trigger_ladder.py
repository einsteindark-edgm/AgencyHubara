"""El trigger del gancho sabe QUÉ toque de la escalera es.

Incidente runs `01a0b0da`…`01a0b586`: el trigger pedía siempre "UN único
gancho" y la regla 7 mandaba abstenerse si "el cliente ya respondió a un gancho
anterior" / "la conversación ya está activa", mientras la regla 2 le decía al
LLM que no conocía el tiempo transcurrido. Resultado: `NO_MESSAGE` en el 100%
de los intentos posteriores al primero (28/28 runs muestreados). La capa
determinista YA garantiza que la conversación está dormida — el prompt tiene
que decírselo al LLM.
"""
from __future__ import annotations

from src.plugins.chats.agent.remarketing.prompts import build_remarketing_trigger

_BASE = dict(has_order_draft=False, transcript="Cliente: hola\nAsesor: Hola de nuevo 🌿")


def test_toque_de_seguimiento_declara_numero_y_silencio_verificado():
    text = build_remarketing_trigger(
        "miró Halloween", touch_number=2, total_touches=5, silence_minutes=125, **_BASE
    )
    assert "toque 2 de 5" in text
    assert "2 h" in text  # silencio real, redondeado, para el LLM (uso interno)
    assert "YA verificó" in text


def test_seguimiento_no_permite_abstenerse_por_gancho_previo():
    text = build_remarketing_trigger(
        "miró Halloween", touch_number=3, total_touches=5, silence_minutes=480, **_BASE
    )
    assert "ya respondió a un gancho anterior" not in text
    assert "ya está activa con el asesor" not in text
    # …pero las abstenciones legítimas siguen vivas.
    assert "NO_MESSAGE" in text
    assert "NO vendemos" in text
    assert "NO es motivo de abstención" in text


def test_seguimiento_pide_un_angulo_distinto():
    text = build_remarketing_trigger(
        "miró Halloween", touch_number=2, total_touches=5, silence_minutes=125, **_BASE
    )
    assert "DISTINTO" in text


def test_ultimo_toque_es_un_cierre_amable():
    text = build_remarketing_trigger(
        "miró Halloween", touch_number=5, total_touches=5, silence_minutes=1200, **_BASE
    )
    assert "ÚLTIMO" in text
    assert "puerta abierta" in text


def test_primer_toque_tambien_acota_la_abstencion_por_conversacion_activa():
    # Caso del incidente: gancho viejo → el cliente respondió → volvió el
    # ghosting. Para la escalera nueva este es el toque 1, pero el historial
    # muestra un gancho respondido: no puede ser motivo de abstención.
    text = build_remarketing_trigger(
        "miró Halloween", touch_number=1, total_touches=5, silence_minutes=150, **_BASE
    )
    assert "ya respondió a un gancho anterior" not in text
    assert "YA verificó" in text


def test_sin_numero_de_toque_el_texto_legacy_no_cambia():
    # Histories en vuelo replayean la activity v2 sin los campos nuevos.
    text = build_remarketing_trigger("miró Halloween", **_BASE)
    assert "ya respondió a un gancho anterior" in text
    assert "toque" not in text.split("REGLAS DEL GANCHO")[0]
