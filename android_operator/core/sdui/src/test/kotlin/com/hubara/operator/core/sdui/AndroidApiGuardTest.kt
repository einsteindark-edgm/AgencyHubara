package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertWithMessage
import java.io.File
import org.junit.Test

/**
 * Los módulos Kotlin puros (`:core:sdui`, `:core:model`) corren en la JVM de los tests, que es Java 21, pero en el
 * teléfono corren sobre Android 11 (minSdk 30), donde faltan APIs de Java 10+. Lint no revisa los módulos JVM, así que
 * esto vigila las que ya nos tumbaron la app: `URLEncoder.encode(texto, Charset)` no existe en Android 11 y la pantalla
 * «Resumen de ventas» se cerraba al pedir sus datos (lo encontró el emulador, S17). Se usa el nombre del charset.
 */
class AndroidApiGuardTest {
    private val root = File(requireNotNull(System.getProperty("screens.dir"))).parentFile

    private val forbidden = listOf(
        Regex("""URLEncoder\.encode\([^)]*,\s*(Charsets\.|StandardCharsets\.)""") to "URLEncoder.encode(…, Charset) es Java 10: usa \"UTF-8\"",
        Regex("""URLDecoder\.decode\([^)]*,\s*(Charsets\.|StandardCharsets\.)""") to "URLDecoder.decode(…, Charset) es Java 10: usa \"UTF-8\"",
    )

    @Test fun ninguna_api_de_java_que_no_exista_en_android_11() {
        val problems = root.walkTopDown()
            .filter { it.isFile && it.extension == "kt" && "/src/main/" in it.path && "/build/" !in it.path }
            .flatMap { file ->
                file.readLines().withIndex().flatMap { (i, line) ->
                    forbidden.filter { (re, _) -> re.containsMatchIn(line) }.map { (_, why) -> "${file.relativeTo(root)}:${i + 1}: $why" }
                }
            }.toList()
        assertWithMessage(problems.joinToString("\n")).that(problems).isEmpty()
    }
}
