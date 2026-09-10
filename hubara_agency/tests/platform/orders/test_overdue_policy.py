"""La regla de "retrasada", aislada del reloj.

`overdue` se calcula server-side (premortem 2026-06-11) comparando `due_iso`
contra "hoy". Ese "hoy" era el día **UTC**, que adelanta la frontera a las 19:00
hora Colombia: a partir de las 7 de la tarde, toda entrega agendada para el día
en curso que aún no salió aparecía en rojo bajo "Retrasadas", y "Para hoy"
pasaba a mostrar las de mañana.

`due_iso` es un día calendario que el operador elige a mano
(`metadata.hubara_scheduled_delivery_iso`) — no un instante. Compararlo contra
el día colombiano es la única semántica correcta.

El `hoy` se inyecta para que el borde sea determinista: el proyecto no tiene
freezegun, y un test que dependa de la hora real sólo fallaría entre las 19:00
y la medianoche.
"""

import pytest

from src.platform.orders.medusa_order_query import compute_overdue

# 2026-09-10 20:00 hora Colombia. En UTC ya es el 11 — ahí estaba el bug.
HOY_COLOMBIA = "2026-09-10"
HOY_UTC_A_ESA_HORA = "2026-09-11"


class TestBordeDeLas19h:
    def test_entrega_de_hoy_no_esta_retrasada_a_las_20_00(self):
        """El bug: con el día UTC esta orden se pintaba de rojo cada noche."""
        assert compute_overdue(HOY_COLOMBIA, "preparing", HOY_COLOMBIA) is False

    def test_el_dia_utc_a_esa_misma_hora_la_habria_marcado_retrasada(self):
        """Deja explícito qué cambia: mismo instante, distinto día de corte."""
        assert compute_overdue(HOY_COLOMBIA, "preparing", HOY_UTC_A_ESA_HORA) is True

    def test_entrega_de_ayer_si_esta_retrasada(self):
        assert compute_overdue("2026-09-09", "preparing", HOY_COLOMBIA) is True

    def test_entrega_de_manana_no_esta_retrasada(self):
        assert compute_overdue("2026-09-11", "preparing", HOY_COLOMBIA) is False


class TestReglasQueNoCambian:
    def test_sin_fecha_agendada_no_hay_vencimiento(self):
        assert compute_overdue(None, "preparing", HOY_COLOMBIA) is False

    @pytest.mark.parametrize("estado", ["delivered", "cancelled"])
    def test_estados_terminales_nunca_estan_retrasados(self, estado):
        """Una orden entregada con fecha vencida llegó; no está retrasada."""
        assert compute_overdue("2026-09-01", estado, HOY_COLOMBIA) is False

    @pytest.mark.parametrize(
        "estado", ["new", "preparing", "ready", "shipping"]
    )
    def test_estados_no_terminales_si_cuentan(self, estado):
        assert compute_overdue("2026-09-01", estado, HOY_COLOMBIA) is True

    def test_due_iso_vacio_se_trata_como_sin_fecha(self):
        assert compute_overdue("", "preparing", HOY_COLOMBIA) is False
