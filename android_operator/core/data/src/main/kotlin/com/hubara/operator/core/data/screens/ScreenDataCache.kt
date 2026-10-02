package com.hubara.operator.core.data.screens

import android.content.Context
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/**
 * Lo último que trajo cada fuente de datos de una pantalla del servidor (clave: pantalla|fuente|ruta). Sin red la
 * pantalla se ve igual, como la bandeja con Room. Tiene datos de clientes: vive en la caché privada de la app y
 * «Cerrar sesión» la borra.
 */
interface ScreenDataCache {
    fun read(key: String): JsonElement?

    fun write(key: String, value: JsonElement)

    fun clear()
}

@Singleton
class FileScreenDataCache @Inject constructor(@ApplicationContext context: Context) : ScreenDataCache {
    private val dir = File(context.cacheDir, "screen-data")

    override fun read(key: String): JsonElement? =
        runCatching { Json.parseToJsonElement(file(key).readText()) }.getOrNull()

    override fun write(key: String, value: JsonElement) {
        runCatching {
            dir.mkdirs()
            val tmp = File(dir, "${name(key)}.tmp")
            tmp.writeText(value.toString())
            tmp.renameTo(file(key))
        }
    }

    override fun clear() {
        dir.deleteRecursively()
    }

    private fun file(key: String) = File(dir, "${name(key)}.json")

    /** El nombre del archivo es un hash: la ruta puede traer ids y búsquedas que no deben quedar en un nombre. */
    private fun name(key: String): String =
        MessageDigest.getInstance("SHA-256").digest(key.toByteArray()).joinToString("") { "%02x".format(it) }.take(40)
}
