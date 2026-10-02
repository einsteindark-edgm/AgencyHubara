package com.hubara.operator.core.ui

import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.temporal.ChronoUnit

// A mano y no con DateTimeFormatter: los datos de idioma del JVM y de Android no escriben igual «p. m.» ni los
// meses abreviados, y estas etiquetas salen en los tests y en las capturas del CI.
private val WEEKDAYS = listOf("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
private val WEEKDAYS_SHORT = listOf("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
private val MONTHS = listOf(
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)
private val MONTHS_SHORT = listOf("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

private fun date(ms: Long, zone: ZoneId): LocalDate = Instant.ofEpochMilli(ms).atZone(zone).toLocalDate()

/** «3:42 p. m.» */
fun clockLabel(ms: Long, zone: ZoneId): String {
    val t = Instant.ofEpochMilli(ms).atZone(zone)
    val h12 = (t.hour % 12).let { if (it == 0) 12 else it }
    return "$h12:${t.minute.toString().padStart(2, '0')} ${if (t.hour < 12) "a. m." else "p. m."}"
}

/** La hora de la fila de la bandeja: hoy la hora, «Ayer», el día de la semana y luego la fecha. */
fun listTimeLabel(ms: Long, nowMs: Long, zone: ZoneId): String {
    val day = date(ms, zone)
    val today = date(nowMs, zone)
    val days = ChronoUnit.DAYS.between(day, today)
    return when {
        days <= 0L -> clockLabel(ms, zone)
        days == 1L -> "Ayer"
        days < 7L -> WEEKDAYS_SHORT[day.dayOfWeek.value - 1]
        day.year == today.year -> "${day.dayOfMonth} ${MONTHS_SHORT[day.monthValue - 1]}"
        else -> "${day.dayOfMonth} ${MONTHS_SHORT[day.monthValue - 1]} ${day.year}"
    }
}

/** El separador de día del chat: «Hoy», «Ayer», el día de la semana o la fecha. */
fun dayLabel(ms: Long, nowMs: Long, zone: ZoneId): String {
    val day = date(ms, zone)
    val today = date(nowMs, zone)
    val days = ChronoUnit.DAYS.between(day, today)
    return when {
        days <= 0L -> "Hoy"
        days == 1L -> "Ayer"
        days < 7L -> WEEKDAYS[day.dayOfWeek.value - 1]
        day.year == today.year -> "${day.dayOfMonth} de ${MONTHS[day.monthValue - 1]}"
        else -> "${day.dayOfMonth} de ${MONTHS[day.monthValue - 1]} de ${day.year}"
    }
}
