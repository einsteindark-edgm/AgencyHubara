"""Día calendario en hora de Colombia.

El dashboard opera en Colombia. Cortar el día en UTC adelanta la frontera a las
19:00 hora local: entre las 7 de la tarde y la medianoche, "hoy" ya devuelve
mañana. Para `overdue` eso significa que todas las entregas del día en curso
que aún no salieron se pintan de rojo a las 19:00.

Las funciones son puras respecto al instante que reciben; sólo el default
consulta el reloj. Sin eso no habría forma determinista de testear el borde
(el proyecto no tiene freezegun).
"""

from datetime import datetime, timezone

from src.platform.bogota_time import bogota_day_iso, shift_iso_day


class TestBogotaDayIso:
    def test_madrugada_utc_pertenece_al_dia_anterior_en_colombia(self):
        # 2026-09-11T01:00:00Z === 2026-09-10 20:00 en Bogotá (UTC-5).
        instante = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
        assert bogota_day_iso(instante) == "2026-09-10"

    def test_el_borde_exacto_de_las_19_00_colombia_todavia_es_hoy(self):
        # 00:00Z del 11 === 19:00 del 10 en Bogotá: el día colombiano NO cambió.
        instante = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc)
        assert bogota_day_iso(instante) == "2026-09-10"

    def test_la_medianoche_colombiana_si_cambia_el_dia(self):
        # 05:00Z del 11 === 00:00 del 11 en Bogotá.
        instante = datetime(2026, 9, 11, 5, 0, tzinfo=timezone.utc)
        assert bogota_day_iso(instante) == "2026-09-11"

    def test_mediodia_utc_cae_el_mismo_dia(self):
        instante = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        assert bogota_day_iso(instante) == "2026-09-10"

    def test_un_instante_naive_se_interpreta_como_utc(self):
        """El backend mezcla datetimes naive y aware; no explotar por eso."""
        assert bogota_day_iso(datetime(2026, 9, 11, 1, 0)) == "2026-09-10"

    def test_sin_argumento_usa_el_reloj_y_devuelve_un_iso_bien_formado(self):
        hoy = bogota_day_iso()
        assert len(hoy) == 10 and hoy[4] == "-" and hoy[7] == "-"
        # Nunca puede diferir del día UTC en más de un día.
        hoy_utc = datetime.now(timezone.utc).date()
        assert abs((datetime.strptime(hoy, "%Y-%m-%d").date() - hoy_utc).days) <= 1

    def test_no_adelanta_el_dia_a_las_19_00_como_hacia_el_corte_utc(self):
        """Regresión directa del bug: a las 20:00 hora Colombia el día UTC ya
        es el siguiente; el día colombiano no."""
        instante = datetime(2026, 9, 11, 1, 0, tzinfo=timezone.utc)
        assert instante.date().isoformat() == "2026-09-11"  # lo que devolvía antes
        assert bogota_day_iso(instante) == "2026-09-10"  # lo que corresponde


class TestShiftIsoDay:
    def test_resta_cruzando_el_borde_de_mes(self):
        assert shift_iso_day("2026-09-01", -1) == "2026-08-31"

    def test_suma_cruzando_el_borde_de_ano(self):
        assert shift_iso_day("2026-12-31", 1) == "2027-01-01"

    def test_cero_es_identidad(self):
        assert shift_iso_day("2026-09-10", 0) == "2026-09-10"
