package com.hubara.operator.core.data

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.config.ServerConfigDefaults
import com.hubara.operator.core.data.config.ServerConfigStore
import com.hubara.operator.core.network.config.ServerConfig
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

/**
 * La app aprende del servidor a qué backend ir (`mobile/config.json`) y recuerda la última configuración buena: así
 * arranca sin red y, si la IP cambia, se entera sin publicar otra versión. Lo inválido o la falta de red nunca borran
 * lo que ya funcionaba.
 */
@RunWith(AndroidJUnit4::class)
class ServerConfigStoreTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()
    private val server = MockWebServer()
    private val build = ServerConfig("http://10.0.2.2:9/".toHttpUrl(), "cliente-del-build", "us-east-1", privacyUrl = "https://tienda.example/privacidad")

    @Before fun setUp() = server.start()
    @After fun tearDown() = server.close()

    private fun store(configUrl: String = server.url("/mobile/config.json").toString()) =
        ServerConfigStore(context, ServerConfigDefaults(configUrl, build, allowCleartext = true))

    private fun config(api: String, extra: String = "") =
        MockResponse(code = 200, body = """{"api_base_url": "$api", "cognito_client_id": "cliente-remoto"$extra}""")

    @Test fun aplica_la_configuracion_del_servidor_y_la_recuerda_al_reiniciar() = runTest {
        server.enqueue(config("http://10.0.2.2:8010"))
        val s = store()
        assertThat(s.refresh()).isTrue()
        assertThat(s.current.value.apiBaseUrl.port).isEqualTo(8010)
        assertThat(s.current.value.cognitoClientId).isEqualTo("cliente-remoto")
        // Lo que el servidor no manda sale del build (aquí, la política de privacidad).
        assertThat(s.current.value.privacyUrl).isEqualTo("https://tienda.example/privacidad")

        // Como si la app se reiniciara sin red: arranca con la última buena, sin pedir nada.
        server.close()
        val despues = store()
        assertThat(despues.hasSaved).isTrue()
        assertThat(despues.current.value.apiBaseUrl.port).isEqualTo(8010)
    }

    @Test fun una_configuracion_invalida_o_sin_red_no_borra_la_que_funcionaba() = runTest {
        server.enqueue(config("http://10.0.2.2:8010"))
        server.enqueue(config("http://otro-servidor.example"))  // sin https: ahí iría el token
        server.enqueue(MockResponse(code = 500))
        val s = store()
        s.refresh()
        assertThat(s.refresh()).isFalse()
        assertThat(s.refresh()).isFalse()
        assertThat(s.current.value.apiBaseUrl.port).isEqualTo(8010)
    }

    @Test fun sin_url_de_configuracion_usa_la_del_build_y_no_pide_nada() = runTest {
        val s = store(configUrl = "")
        assertThat(s.refresh()).isFalse()
        assertThat(s.current.value).isEqualTo(build)
        assertThat(server.requestCount).isEqualTo(0)
    }
}
