package com.hubara.operator.core.sdui

import java.time.LocalDateTime
import java.time.ZoneId
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

fun json(raw: String): JsonElement = Json.parseToJsonElement(raw)

val BOGOTA_TEST: ZoneId = ZoneId.of("America/Bogota")

/** Milisegundos de una hora de Bogotá, para escribir fechas legibles en los tests. */
fun bogota(y: Int, m: Int, d: Int, h: Int = 0, min: Int = 0, s: Int = 0): Long =
    LocalDateTime.of(y, m, d, h, min, s).atZone(BOGOTA_TEST).toInstant().toEpochMilli()

/** El «ahora» fijo de los tests: 1 oct 2026, 3:00 p. m. en Bogotá. */
val NOW = bogota(2026, 10, 1, 15, 0)

val TEST_ENV = Env(nowMs = { NOW }, zone = BOGOTA_TEST)
