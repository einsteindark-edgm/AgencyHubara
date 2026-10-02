package com.hubara.operator.core.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.ScreenStore
import java.io.File
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Las pantallas del servidor bajan de `<cloudfront>/mobile/screens/<id>.json`. La app arranca con lo mejor que tiene
 * sin red (la última que bajó o la que trae el APK) y la renueva; algo inválido nunca reemplaza a lo que funcionaba.
 */
@RunWith(AndroidJUnit4::class)
class ScreenStoreTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val server = MockWebServer()
    private val bundled = mapOf(
        "ventas.json" to screen("ventas", "Ventas (del APK)"),
        "app.json" to """{"schema": 1, "tabs": [{"screen": "chats", "label": "Chats", "icon": "chat"}, {"screen": "mas", "label": "Más", "icon": "apps"}]}""",
    )

    @Before fun setUp() {
        server.start()
        File(context.filesDir, "screens").deleteRecursively()
    }

    @After fun tearDown() = server.close()

    private fun store(base: Boolean = true) =
        ScreenStore(context, { if (base) server.url("/mobile/screens/") else null }, { bundled[it] })

    @Test fun sin_red_usa_la_que_trae_el_apk() = runTest {
        assertThat(store().cached("ventas")?.title?.raw).isEqualTo("Ventas (del APK)")
        assertThat(store().cached("no_existe")).isNull()
    }

    @Test fun baja_la_del_servidor_y_la_recuerda() = runTest {
        server.enqueue(MockResponse(code = 200, body = screen("ventas", "Ventas (del servidor)")))
        val s = store()
        assertThat(s.fetch("ventas")?.title?.raw).isEqualTo("Ventas (del servidor)")
        assertThat(server.takeRequest().url.encodedPath).isEqualTo("/mobile/screens/ventas.json")

        // Como si la app se reiniciara sin red.
        server.close()
        assertThat(store().cached("ventas")?.title?.raw).isEqualTo("Ventas (del servidor)")
    }

    @Test fun lo_invalido_no_reemplaza_lo_que_funcionaba() = runTest {
        server.enqueue(MockResponse(code = 200, body = screen("ventas", "Buena")))
        server.enqueue(MockResponse(code = 200, body = "<html>el CDN devolvió la SPA</html>"))
        server.enqueue(MockResponse(code = 200, body = screen("otra", "Id que no corresponde")))
        server.enqueue(MockResponse(code = 404))
        val s = store()
        s.fetch("ventas")
        repeat(3) { assertThat(s.fetch("ventas")).isNull() }
        assertThat(s.cached("ventas")?.title?.raw).isEqualTo("Buena")
    }

    @Test fun un_id_raro_no_toca_disco_ni_red() = runTest {
        assertThat(store().fetch("../config")).isNull()
        assertThat(store().cached("../../shared_prefs/x")).isNull()
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test fun las_pestanas_nuevas_se_aplican_al_siguiente_arranque() = runTest {
        val s = store()
        assertThat(s.manifest.tabs.map { it.screen }).containsExactly("chats", "mas").inOrder()

        server.enqueue(MockResponse(code = 200, body = """{"schema": 1, "tabs": [{"screen": "chats", "label": "Chats", "icon": "chat"},
            {"screen": "ventas", "label": "Ventas", "icon": "bar_chart"}]}"""))
        assertThat(s.refreshManifest()).isTrue()
        // En caliente no cambia (cambiar pestañas reinicia la navegación)…
        assertThat(s.manifest.tabs.map { it.screen }).containsExactly("chats", "mas").inOrder()
        // …al siguiente arranque, sí.
        assertThat(store().manifest.tabs.map { it.screen }).containsExactly("chats", "ventas").inOrder()
    }

    @Test fun un_manifiesto_sin_pestanas_suficientes_no_deja_la_app_sin_barra() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"schema": 1, "tabs": [{"screen": "chats", "label": "Chats", "icon": "chat"}]}"""))
        val s = store()
        assertThat(s.refreshManifest()).isFalse()
        assertThat(store().manifest.tabs.map { it.screen }).containsExactly("chats", "mas").inOrder()
    }

    @Test fun sin_direccion_de_pantallas_no_pide_nada() = runTest {
        assertThat(store(base = false).fetch("ventas")).isNull()
        assertThat(server.requestCount).isEqualTo(0)
    }

    private fun screen(id: String, title: String) =
        """{"schema": 1, "id": "$id", "title": "$title", "body": [{"type": "text", "text": "Hola"}]}"""
}
