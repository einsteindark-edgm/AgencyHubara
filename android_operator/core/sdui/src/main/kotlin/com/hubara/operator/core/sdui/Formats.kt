package com.hubara.operator.core.sdui

import java.math.BigDecimal
import java.math.RoundingMode
import java.time.Instant
import java.time.LocalDate
import java.time.LocalDateTime
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.temporal.ChronoUnit
import java.time.temporal.TemporalAdjusters
import java.time.DayOfWeek
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive

// Formatos de Colombia escritos a mano (como `TimeLabels` de :core:ui): los datos de idioma del JVM y de Android no
// escriben igual los meses ni «p. m.», y estos textos salen en los tests y en las capturas del CI.

private val WEEKDAYS_SHORT = listOf("lun", "mar", "mié", "jue", "vie", "sáb", "dom")

private val MONTHS_SHORT = listOf("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")

/** 1234567.8 → «1.234.567,8»: punto de miles y coma decimal. */
internal fun formatNumber(value: Double, decimals: Int = 0): String {
    val scaled = BigDecimal.valueOf(value).setScale(decimals.coerceIn(0, 6), RoundingMode.HALF_UP)
    val abs = scaled.abs()
    val grouped = abs.toBigInteger().toString().reversed().chunked(3).joinToString(".").reversed()
    val fraction = if (decimals > 0) "," + abs.toPlainString().substringAfter('.', "").padEnd(decimals, '0') else ""
    return (if (scaled.signum() < 0) "-" else "") + grouped + fraction
}

/** Pesos sin centavos («$45.000»); dólares con dos decimales («US$1,50»); otra moneda con su código. */
internal fun formatMoney(value: Double, currency: String = "COP"): String {
    val sign = if (value < 0) "-" else ""
    val abs = kotlin.math.abs(value)
    return when (currency.uppercase()) {
        "COP" -> "$sign$" + formatNumber(abs, 0)
        "USD" -> "${sign}US$" + formatNumber(abs, 2)
        else -> "$sign${currency.uppercase()} " + formatNumber(abs, 2)
    }
}

/**
 * Un instante desde lo que mandan los endpoints: milisegundos (`created_at_ms`), segundos, ISO con zona
 * (`2026-09-28T20:45:00Z`), ISO sin zona (hora de Bogotá) o solo la fecha (`2026-09-28`).
 */
internal fun toInstant(value: JsonElement?, zone: ZoneId): Instant? {
    val primitive = value as? JsonPrimitive ?: return null
    primitive.content.toDoubleOrNull()?.let { n ->
        return Instant.ofEpochMilli(if (kotlin.math.abs(n) > 1e11) n.toLong() else (n * 1000).toLong())
    }
    val s = primitive.content.trim()
    return runCatching { Instant.parse(s) }.getOrNull()
        ?: runCatching { OffsetDateTime.parse(s).toInstant() }.getOrNull()
        ?: runCatching { LocalDateTime.parse(s).atZone(zone).toInstant() }.getOrNull()
        ?: runCatching { LocalDate.parse(s).atStartOfDay(zone).toInstant() }.getOrNull()
}

/** «28 sep» (o «24 dic 2025» si no es de este año). */
internal fun formatDate(instant: Instant, env: Env): String {
    val day = instant.atZone(env.zone).toLocalDate()
    val today = Instant.ofEpochMilli(env.nowMs()).atZone(env.zone).toLocalDate()
    val base = "${day.dayOfMonth} ${MONTHS_SHORT[day.monthValue - 1]}"
    return if (day.year == today.year) base else "$base ${day.year}"
}

/** «3:45 p. m.» */
internal fun formatTime(instant: Instant, env: Env): String {
    val t = instant.atZone(env.zone)
    val h12 = (t.hour % 12).let { if (it == 0) 12 else it }
    return "$h12:${t.minute.toString().padStart(2, '0')} ${if (t.hour < 12) "a. m." else "p. m."}"
}

/** La hora de la fila de la bandeja (como `listTimeLabel` de :core:ui): hoy la hora, «Ayer», el día y la fecha. */
internal fun formatListTime(instant: Instant, env: Env): String {
    val day = instant.atZone(env.zone).toLocalDate()
    val today = Instant.ofEpochMilli(env.nowMs()).atZone(env.zone).toLocalDate()
    val days = ChronoUnit.DAYS.between(day, today)
    return when {
        days <= 0L -> formatTime(instant, env)
        days == 1L -> "Ayer"
        days < 7L -> WEEKDAYS_SHORT[day.dayOfWeek.value - 1]
        else -> formatDate(instant, env)
    }
}

/** «ahora», «hace 5 min», «hace 2 h», «ayer», «hace 3 días» y después la fecha. */
internal fun formatRelative(instant: Instant, env: Env): String {
    val now = Instant.ofEpochMilli(env.nowMs())
    val minutes = ChronoUnit.MINUTES.between(instant, now)
    val day = instant.atZone(env.zone).toLocalDate()
    val today = now.atZone(env.zone).toLocalDate()
    val days = ChronoUnit.DAYS.between(day, today)
    return when {
        days <= 0L && minutes < 1 -> "ahora"
        days <= 0L && minutes < 60 -> "hace $minutes min"
        days <= 0L -> "hace ${minutes / 60} h"
        days == 1L -> "ayer"
        days < 7L -> "hace $days días"
        else -> formatDate(instant, env)
    }
}

/** El comienzo de una ventana de fechas en Bogotá: `today`, `week` (desde el lunes), `month` o `7d` (los últimos 7 días, hoy incluido). */
internal fun windowStart(window: String, env: Env): Instant? {
    val today = Instant.ofEpochMilli(env.nowMs()).atZone(env.zone).toLocalDate()
    val start: LocalDate = when {
        window == "today" -> today
        window == "week" -> today.with(TemporalAdjusters.previousOrSame(DayOfWeek.MONDAY))
        window == "month" -> today.withDayOfMonth(1)
        window.endsWith("d") -> window.dropLast(1).toLongOrNull()?.takeIf { it > 0 }?.let { today.minusDays(it - 1) } ?: return null
        else -> return null
    }
    return start.atStartOfDay(env.zone).toInstant()
}
