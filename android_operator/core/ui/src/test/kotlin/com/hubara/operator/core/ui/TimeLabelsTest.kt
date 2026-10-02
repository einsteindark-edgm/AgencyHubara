package com.hubara.operator.core.ui

import com.google.common.truth.Truth.assertThat
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Test

/** Las horas como las lee el operador en Colombia: «3:42 p. m.», «Ayer», «lun», «12 sep». */
class TimeLabelsTest {
    private val bogota = ZoneId.of("America/Bogota")
    private fun at(y: Int, mo: Int, d: Int, h: Int, mi: Int) = ZonedDateTime.of(y, mo, d, h, mi, 0, 0, bogota).toInstant().toEpochMilli()
    private val now = at(2026, 9, 30, 18, 5) // miércoles

    @Test fun la_hora_en_formato_de_12_horas() {
        assertThat(clockLabel(at(2026, 9, 30, 15, 42), bogota)).isEqualTo("3:42 p. m.")
        assertThat(clockLabel(at(2026, 9, 30, 0, 7), bogota)).isEqualTo("12:07 a. m.")
        assertThat(clockLabel(at(2026, 9, 30, 12, 0), bogota)).isEqualTo("12:00 p. m.")
    }

    @Test fun en_la_bandeja_hoy_es_la_hora_ayer_es_ayer_la_semana_el_dia_y_luego_la_fecha() {
        assertThat(listTimeLabel(at(2026, 9, 30, 9, 15), now, bogota)).isEqualTo("9:15 a. m.")
        assertThat(listTimeLabel(at(2026, 9, 29, 23, 59), now, bogota)).isEqualTo("Ayer")
        assertThat(listTimeLabel(at(2026, 9, 28, 10, 0), now, bogota)).isEqualTo("lun")
        assertThat(listTimeLabel(at(2026, 9, 24, 10, 0), now, bogota)).isEqualTo("jue")
        assertThat(listTimeLabel(at(2026, 9, 23, 10, 0), now, bogota)).isEqualTo("23 sep")
        assertThat(listTimeLabel(at(2025, 12, 3, 10, 0), now, bogota)).isEqualTo("3 dic 2025")
    }

    @Test fun el_separador_del_chat_nombra_el_dia() {
        assertThat(dayLabel(at(2026, 9, 30, 8, 0), now, bogota)).isEqualTo("Hoy")
        assertThat(dayLabel(at(2026, 9, 29, 8, 0), now, bogota)).isEqualTo("Ayer")
        assertThat(dayLabel(at(2026, 9, 27, 8, 0), now, bogota)).isEqualTo("domingo")
        assertThat(dayLabel(at(2026, 9, 2, 8, 0), now, bogota)).isEqualTo("2 de septiembre")
        assertThat(dayLabel(at(2025, 9, 2, 8, 0), now, bogota)).isEqualTo("2 de septiembre de 2025")
    }
}
