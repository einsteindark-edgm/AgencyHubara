package com.hubara.operator.core.data

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.AuthState
import com.hubara.operator.core.data.auth.InMemoryTokenStore
import com.hubara.operator.core.data.auth.SessionTokens
import com.hubara.operator.core.network.auth.CognitoClient
import com.hubara.operator.core.network.di.ApiConfig
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Test

class AuthRepositoryTest {
    private val server = MockWebServer()
    private val store = InMemoryTokenStore()
    private var now = 1_000_000L

    @Before fun setUp() = server.start()
    @After fun tearDown() = server.close()

    private fun repo(clientId: String = "client-test") = AuthRepository(
        config = ApiConfig("http://localhost/".toHttpUrl(), clientId, "us-east-1"),
        cognito = CognitoClient(OkHttpClient(), server.url("/"), clientId),
        store = store,
        clock = { now },
    )

    private fun tokens(access: String) =
        MockResponse(code = 200, body = """{"AuthenticationResult":{"AccessToken":"$access","IdToken":"i","RefreshToken":"r","ExpiresIn":3600}}""")

    @Test fun sin_cognito_es_modo_dev_sin_login() = runTest {
        val r = repo(clientId = "")
        r.restore()
        assertThat(r.state.value).isEqualTo(AuthState.DevMode)
        assertThat(r.currentAccessToken()).isNull()
    }

    @Test fun login_guarda_la_sesion_y_la_restaura() = runTest {
        server.enqueue(tokens("a1"))
        val r = repo()
        r.restore()
        assertThat(r.state.value).isEqualTo(AuthState.SignedOut)
        assertThat(r.login("op@example.com", "no-es-real")).isNull()
        assertThat(r.state.value).isEqualTo(AuthState.SignedIn)
        assertThat(r.currentAccessToken()).isEqualTo("a1")

        val otra = repo()
        otra.restore()
        assertThat(otra.state.value).isEqualTo(AuthState.SignedIn)
        assertThat(otra.currentAccessToken()).isEqualTo("a1")
    }

    @Test fun primer_login_pide_contrasena_nueva() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"ChallengeName":"NEW_PASSWORD_REQUIRED","Session":"s1"}"""))
        server.enqueue(tokens("a2"))
        val r = repo()
        r.restore()
        r.login("op@example.com", "temporal")
        assertThat(r.state.value).isEqualTo(AuthState.NeedsNewPassword("op@example.com", "s1"))
        assertThat(r.completeNewPassword("Nueva-Clave-2026!")).isNull()
        assertThat(r.state.value).isEqualTo(AuthState.SignedIn)
    }

    @Test fun un_401_con_token_viejo_no_pide_otro_refresh_si_ya_hay_uno_nuevo() = runTest {
        store.save(SessionTokens("nuevo", "i", "r", now + 3_600_000, "op@example.com"))
        val r = repo()
        r.restore()
        assertThat(r.forceRefresh(rejectedToken = "viejo")).isEqualTo("nuevo")
        assertThat(server.requestCount).isEqualTo(0)
    }

    @Test fun si_el_refresh_falla_se_cierra_la_sesion() = runTest {
        store.save(SessionTokens("a", "i", "r", now + 3_600_000, "op@example.com"))
        server.enqueue(MockResponse(code = 400, body = """{"__type":"NotAuthorizedException"}"""))
        val r = repo()
        r.restore()
        assertThat(r.forceRefresh(rejectedToken = "a")).isNull()
        assertThat(r.state.value).isEqualTo(AuthState.SignedOut)
        assertThat(store.load()).isNull()
    }
}
