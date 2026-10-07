package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertWithMessage
import java.io.File
import org.junit.Test

/**
 * La compuerta de las pantallas del servidor: cada archivo de `android_operator/screens/` tiene que poder pintarse
 * ENTERO con esta versión de la app antes de publicarse (lo publica `frontend-deploy.yml` al mergear). Si un archivo
 * usa un componente, una propiedad, un ícono, un filtro o una pantalla que no existe, el PR no pasa.
 *
 * El esquema del editor (`screen.schema.json`) y la referencia (`CATALOGO.md`) salen del catálogo: si cambias el
 * catálogo, regenéralos con `./gradlew :core:sdui:test -PupdateScreens=true`.
 */
class ScreensRepoTest {
    private val dir = File(requireNotNull(System.getProperty("screens.dir")) { "falta screens.dir (lo pone build.gradle.kts)" })
    private val update = System.getProperty("screens.update") == "true"

    private val screenFiles = dir.listFiles { f -> f.extension == "json" && f.name != MANIFEST && !f.name.endsWith(".schema.json") }
        .orEmpty().sortedBy { it.name }

    @Test fun hay_pantallas() {
        assertWithMessage("no encontré pantallas en $dir").that(screenFiles).isNotEmpty()
    }

    @Test fun cada_pantalla_del_repo_se_puede_pintar_entera() {
        val parsed = screenFiles.associateWith { parseScreen(it.readText()) }
        val known = parsed.values.mapNotNull { it.doc }.associate { it.id to it.params }
        val problems = parsed.flatMap { (file, result) ->
            val doc = result.doc
            val own = when {
                doc == null -> result.problems
                doc.id != file.nameWithoutExtension -> result.problems + "«id» es «${doc.id}» pero el archivo se llama ${file.name}: tienen que coincidir."
                else -> result.problems + validateScreen(doc, known)
            }
            own.map { "${file.name}: $it" }
        }
        assertWithMessage("Pantallas con errores:\n" + problems.joinToString("\n")).that(problems).isEmpty()
    }

    /** Las pantallas que los escenarios del emulador ponen en el servidor (`e2e/screens/`) también tienen que valer. */
    @Test fun las_pantallas_de_prueba_del_emulador_tambien() {
        val fixtures = File(dir.parentFile, "e2e/screens").listFiles { f -> f.extension == "json" }.orEmpty().sortedBy { it.name }
        assertWithMessage("no encontré e2e/screens").that(fixtures).isNotEmpty()
        val all = (screenFiles + fixtures).mapNotNull { parseScreen(it.readText()).doc }
        val known = all.associate { it.id to it.params }
        val problems = fixtures.flatMap { file ->
            val result = parseScreen(file.readText())
            val doc = result.doc ?: return@flatMap result.problems.map { "${file.name}: $it" }
            (result.problems + validateScreen(doc, known)).map { "e2e/screens/${file.name}: $it" }
        }
        assertWithMessage("Pantallas de prueba con errores:\n" + problems.joinToString("\n")).that(problems).isEmpty()
    }

    /** El ejemplo completo de la guía (README.md, «Crear una pantalla nueva») tiene que ser una pantalla válida. */
    @Test fun el_ejemplo_de_la_guia_es_una_pantalla_valida() {
        val readme = File(dir, "README.md").readText()
        val example = Regex("```json\\n(\\{\\n  \"\\${'$'}schema\".*?)\\n```", RegexOption.DOT_MATCHES_ALL).find(readme)?.groupValues?.get(1)
        assertWithMessage("no encontré el ejemplo completo en README.md").that(example).isNotNull()
        val parsed = parseScreen(example!!)
        val known = screenFiles.mapNotNull { parseScreen(it.readText()).doc }.associate { it.id to it.params } +
            (parsed.doc?.let { mapOf(it.id to it.params) } ?: emptyMap())
        val problems = parsed.problems + (parsed.doc?.let { validateScreen(it, known) } ?: emptyList())
        assertWithMessage("El ejemplo de README.md tiene errores:\n" + problems.joinToString("\n")).that(problems).isEmpty()
    }

    @Test fun el_manifiesto_apunta_a_pantallas_que_existen() {
        val known = screenFiles.mapNotNull { parseScreen(it.readText()).doc }.associate { it.id to it.params }
        val parsed = parseAppManifest(File(dir, MANIFEST).readText())
        val problems = parsed.problems + (parsed.manifest?.let { validateAppManifest(it, known) } ?: emptyList())
        assertWithMessage("$MANIFEST con errores:\n" + problems.joinToString("\n")).that(problems).isEmpty()
    }

    @Test fun el_esquema_del_editor_y_la_referencia_estan_al_dia() {
        generated().forEach { (name, content) ->
            val file = File(dir, name)
            if (update) file.writeText(content)
            assertWithMessage("$name está desactualizado: corre ./gradlew :core:sdui:test -PupdateScreens=true")
                .that(file.takeIf { it.isFile }?.readText()).isEqualTo(content)
        }
    }

    private fun generated() = mapOf(
        "screen.schema.json" to ScreenSchema.screen(),
        "app.schema.json" to ScreenSchema.app(),
        "CATALOGO.md" to ScreenSchema.reference(),
    )

    private companion object {
        const val MANIFEST = "app.json"
    }
}
