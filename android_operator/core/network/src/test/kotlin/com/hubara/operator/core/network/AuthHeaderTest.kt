package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.di.AccessTokenProvider
import com.hubara.operator.core.network.di.ApiConfig
import com.hubara.operator.core.network.di.NetworkModule
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.Request
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * Auditoría de seguridad: el interceptor ponía el token de Cognito en TODA petición del cliente HTTP, y Coil usa el
 * mismo cliente. La primera foto de un CDN ajeno (las miniaturas de Medusa ya están en el modelo) le habría mandado
 * el token. Solo va a nuestro backend.
 */
class AuthHeaderTest {
    private val ours = MockWebServer()
    private val other = MockWebServer()
    private val tokens = object : AccessTokenProvider {
        override fun currentAccessToken() = "token-de-prueba"
    }

    @Before fun setUp() {
        ours.start()
        other.start()
    }

    @After fun tearDown() {
        ours.close()
        other.close()
    }

    @Test fun el_token_va_solo_a_nuestro_backend() {
        val client = NetworkModule.okHttp(tokens, ApiConfig(ours.url("/"), "client-test", "us-east-1"))
        ours.enqueue(MockResponse(code = 200))
        other.enqueue(MockResponse(code = 200))

        client.newCall(Request.Builder().url(ours.url("/api/dashboard/sessions")).build()).execute().close()
        client.newCall(Request.Builder().url(other.url("/miniatura.jpg")).build()).execute().close()

        assertThat(ours.takeRequest().headers["Authorization"]).isEqualTo("Bearer token-de-prueba")
        assertThat(other.takeRequest().headers["Authorization"]).isNull()
    }
}
