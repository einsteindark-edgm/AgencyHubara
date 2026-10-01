package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.config.ServerConfig
import com.hubara.operator.core.network.di.AccessTokenProvider
import com.hubara.operator.core.network.di.ApiConfig
import com.hubara.operator.core.network.di.NetworkModule
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test
import java.util.concurrent.TimeUnit

/**
 * Si el servidor cambia de dirección (nueva IP), la app sigue con la nueva sin reiniciarse ni publicar otra versión:
 * la siguiente llamada ya va al servidor nuevo, con su token, y al viejo no le llega nada más.
 */
class DynamicServerTest {
    private val viejo = MockWebServer()
    private val nuevo = MockWebServer()
    private val tokens = object : AccessTokenProvider {
        override fun currentAccessToken() = "token-de-prueba"
    }

    @Before fun setUp() { viejo.start(); nuevo.start() }
    @After fun tearDown() { viejo.close(); nuevo.close() }

    @Test fun la_siguiente_llamada_va_al_servidor_nuevo_con_su_token() = runTest {
        var current = ServerConfig(viejo.url("/"), "client-test", "us-east-1")
        val config = ApiConfig({ current })
        val client = NetworkModule.okHttp(tokens, config)
        val api = NetworkModule.api(client, config)
        val empty = MockResponse(code = 200, body = """{"sessions":[]}""", headers = okhttp3.Headers.headersOf("Content-Type", "application/json"))
        viejo.enqueue(empty)
        nuevo.enqueue(empty)

        api.sessions()
        current = current.copy(apiBaseUrl = nuevo.url("/"))
        api.sessions()

        assertThat(viejo.requestCount).isEqualTo(1)
        val llego = nuevo.takeRequest(2, TimeUnit.SECONDS)
        assertThat(llego).isNotNull()
        assertThat(llego!!.url.encodedPath).isEqualTo("/api/dashboard/sessions")
        assertThat(llego.headers["Authorization"]).isEqualTo("Bearer token-de-prueba")
    }
}
