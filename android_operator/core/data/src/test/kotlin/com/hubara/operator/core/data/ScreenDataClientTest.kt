package com.hubara.operator.core.data

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.ScreenCallError
import com.hubara.operator.core.data.screens.ScreenDataClient
import com.hubara.operator.core.network.di.AccessTokenProvider
import com.hubara.operator.core.network.di.ApiConfig
import com.hubara.operator.core.network.di.NetworkModule
import com.hubara.operator.core.sdui.HttpCall
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Before
import org.junit.Test

/** Los datos de una pantalla del servidor se piden a NUESTRO backend con el cliente de siempre (token, backend vigente). */
class ScreenDataClientTest {
    private val server = MockWebServer()
    private lateinit var client: ScreenDataClient

    @Before fun setUp() {
        server.start()
        val config = ApiConfig(server.url("/"), cognitoClientId = "", cognitoRegion = "us-east-1")
        val tokens = object : AccessTokenProvider { override fun currentAccessToken() = "token-del-operador" }
        client = ScreenDataClient(NetworkModule.okHttp(tokens, config))
    }

    @After fun tearDown() = server.close()

    @Test fun pide_al_backend_vigente_con_el_token() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"orders": [{"id": "o1"}]}"""))
        val result = client.execute(HttpCall("GET", "/api/orders/orders?limit=200", null))
        assertThat(result.getOrThrow()).isEqualTo(Json.parseToJsonElement("""{"orders": [{"id": "o1"}]}"""))
        val request = server.takeRequest()
        assertThat(request.url.encodedPath + "?" + request.url.encodedQuery).isEqualTo("/api/orders/orders?limit=200")
        assertThat(request.headers["Authorization"]).isEqualTo("Bearer token-del-operador")
    }

    @Test fun manda_el_cuerpo_en_json() = runTest {
        server.enqueue(MockResponse(code = 204))
        val result = client.execute(HttpCall("PATCH", "/api/orders/orders/o1/confirm-payment", Json.parseToJsonElement("""{"by": "app"}""")))
        assertThat(result.getOrThrow()).isEqualTo(JsonNull)
        val request = server.takeRequest()
        assertThat(request.method).isEqualTo("PATCH")
        assertThat(request.headers["Content-Type"]).startsWith("application/json")
        assertThat(request.body!!.utf8()).isEqualTo("""{"by":"app"}""")
    }

    @Test fun el_error_del_backend_llega_en_espanol_llano() = runTest {
        server.enqueue(MockResponse(code = 409, body = """{"detail": "Campaña en estado 'sent' — ya no es editable"}"""))
        server.enqueue(MockResponse(code = 422, body = """{"detail": [{"loc": ["body", "x"], "msg": "Field required"}]}"""))
        server.enqueue(MockResponse(code = 500, body = "Internal Server Error"))
        val a = client.execute(HttpCall("PUT", "/api/marketing/campaigns/c1", null)).exceptionOrNull() as ScreenCallError
        assertThat(a.code).isEqualTo(409)
        assertThat(a.message).isEqualTo("Campaña en estado 'sent' — ya no es editable")
        val b = client.execute(HttpCall("POST", "/api/x", null)).exceptionOrNull() as ScreenCallError
        assertThat(b.message).isEqualTo("Field required")
        val c = client.execute(HttpCall("GET", "/api/x", null)).exceptionOrNull() as ScreenCallError
        assertThat(c.message).isEqualTo("El servidor respondió 500.")
    }

    @Test fun un_200_con_success_false_es_un_rechazo_y_no_un_exito() = runTest {
        // Así responden los endpoints de pedidos (confirm-payment, stage…) cuando no pueden: 200 + success:false.
        server.enqueue(MockResponse(code = 200, body = """{"success": false, "order_id": "o1", "error_detail": "invalid_state: el pedido aún es borrador."}"""))
        server.enqueue(MockResponse(code = 200, body = """{"success": true, "order_id": "o1"}"""))
        val rechazo = client.execute(HttpCall("PATCH", "/api/orders/orders/o1/confirm-payment", null)).exceptionOrNull() as ScreenCallError
        assertThat(rechazo.message).isEqualTo("el pedido aún es borrador.")
        assertThat(client.execute(HttpCall("PATCH", "/api/orders/orders/o1/confirm-payment", null)).isSuccess).isTrue()
    }

    @Test fun una_ruta_insegura_ni_sale() = runTest {
        val result = client.execute(HttpCall("GET", "https://otro.example/api/x", null))
        assertThat(result.isFailure).isTrue()
        assertThat(server.requestCount).isEqualTo(0)
    }
}
