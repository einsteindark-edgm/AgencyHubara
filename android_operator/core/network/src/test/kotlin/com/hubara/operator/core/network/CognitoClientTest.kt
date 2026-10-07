package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.auth.CognitoClient
import com.hubara.operator.core.network.auth.CognitoOutcome
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Test

class CognitoClientTest {
    private val server = MockWebServer()
    private lateinit var client: CognitoClient

    @Before fun setUp() {
        server.start()
        client = CognitoClient(OkHttpClient(), server.url("/"), clientId = "client-test")
    }

    @After fun tearDown() = server.close()

    @Test fun login_ok_devuelve_tokens_y_manda_el_target_de_aws() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"AuthenticationResult":{"AccessToken":"a","IdToken":"i","RefreshToken":"r","ExpiresIn":3600}}"""))
        val out = client.login("op@example.com", "no-es-real")
        assertThat(out).isEqualTo(CognitoOutcome.Tokens("a", "i", "r", 3600))
        val req = server.takeRequest()
        assertThat(req.headers["X-Amz-Target"]).isEqualTo("AWSCognitoIdentityProviderService.InitiateAuth")
        assertThat(req.headers["Content-Type"]).startsWith("application/x-amz-json-1.1")
        val body = req.body!!.utf8()
        assertThat(body).contains("\"AuthFlow\":\"USER_PASSWORD_AUTH\"")
        assertThat(body).contains("\"ClientId\":\"client-test\"")
    }

    @Test fun primer_login_pide_contrasena_nueva() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"ChallengeName":"NEW_PASSWORD_REQUIRED","Session":"s1","ChallengeParameters":{}}"""))
        assertThat(client.login("op@example.com", "x")).isEqualTo(CognitoOutcome.NewPasswordRequired("s1", "op@example.com"))
    }

    @Test fun credenciales_malas_y_usuario_inexistente_dan_el_mismo_mensaje() = runTest {
        server.enqueue(MockResponse(code = 400, body = """{"__type":"NotAuthorizedException","message":"Incorrect username or password."}"""))
        server.enqueue(MockResponse(code = 400, body = """{"__type":"com.amazon#UserNotFoundException","message":"x"}"""))
        val a = client.login("op@example.com", "x") as CognitoOutcome.Failure
        val b = client.login("otro@example.com", "x") as CognitoOutcome.Failure
        assertThat(a.message).isEqualTo("Email o contraseña incorrectos.")
        assertThat(b.message).isEqualTo(a.message)
    }

    @Test fun refresh_reusa_el_refresh_token_si_no_viene_uno_nuevo() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"AuthenticationResult":{"AccessToken":"a2","IdToken":"i2","ExpiresIn":3600}}"""))
        assertThat(client.refresh("r-viejo")).isEqualTo(CognitoOutcome.Tokens("a2", "i2", "r-viejo", 3600))
        assertThat(server.takeRequest().body!!.utf8()).contains("\"AuthFlow\":\"REFRESH_TOKEN_AUTH\"")
    }

    @Test fun sin_red_no_lanza() = runTest {
        server.close()
        val out = client.login("op@example.com", "x") as CognitoOutcome.Failure
        assertThat(out.code).isEqualTo("network")
    }
}
