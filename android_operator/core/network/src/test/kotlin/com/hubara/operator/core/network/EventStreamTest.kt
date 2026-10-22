package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.sse.EventStream
import com.hubara.operator.core.network.sse.ServerEvent
import com.hubara.operator.core.network.sse.StreamUpdate
import java.time.Duration
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.flow.filterIsInstance
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.take
import kotlinx.coroutines.flow.takeWhile
import kotlinx.coroutines.flow.toList
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.Headers.Companion.headersOf
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Test

/**
 * El tiempo real de la app (caso 2026-10-21: en la app los mensajes llegaban tarde y la web iba al día). La conexión
 * pide solo los cambios de la bandeja, se corta sola si el servidor deja de hablar (ni el latido de 25 s) y, tras una
 * conexión buena, la espera para reconectar vuelve a empezar de cero. «Reconectar ya» no espera.
 */
class EventStreamTest {
    private val server = MockWebServer()
    private lateinit var api: OperatorApi

    @Before fun setUp() {
        server.start()
        api = OperatorService.retrofit(OkHttpClient(), server.url("/")).create(OperatorApi::class.java)
    }

    @After fun tearDown() = server.close()

    private fun ticket() = MockResponse(code = 200, body = """{"ticket":"t1"}""", headers = headersOf("Content-Type", "application/json"))

    private fun sse(body: String) = MockResponse(code = 200, body = body, headers = headersOf("Content-Type", "text/event-stream"))

    private val ordersChanged = "data: {\"domain\":\"orders\",\"type\":\"changed\",\"ts_ms\":5}\n\n"

    private fun stream(stall: Duration = Duration.ofSeconds(60), backoff: (Int) -> Long = { 0L }) =
        EventStream(OkHttpClient(), { server.url("/") }, api, stallTimeout = stall, backoff = backoff)

    @Test fun la_app_pide_solo_los_cambios_de_la_bandeja() = runBlocking {
        server.enqueue(ticket())
        server.enqueue(sse(ordersChanged))

        val received = withTimeout(5_000) { stream().updates().filterIsInstance<StreamUpdate.Received>().first() }

        assertThat(received.event).isEqualTo(ServerEvent.OrdersChanged)
        assertThat(received.sentAtMs).isEqualTo(5)
        assertThat(received.bytes).isGreaterThan(0)
        assertThat(server.takeRequest(1, TimeUnit.SECONDS)!!.url.encodedPath).isEqualTo("/api/dashboard/sse-ticket")
        val events = server.takeRequest(1, TimeUnit.SECONDS)!!
        assertThat(events.url.queryParameter("ticket")).isEqualTo("t1")
        assertThat(events.url.queryParameter("inbox")).isEqualTo("delta")
    }

    @Test fun una_conexion_que_se_queda_callada_se_corta_y_se_vuelve_a_abrir() = runBlocking {
        // Abre bien y después no manda nada, ni el latido: en el celular pasa al cambiar de antena o de red.
        server.enqueue(ticket())
        server.enqueue(
            MockResponse.Builder().code(200).addHeader("Content-Type", "text/event-stream")
                .body(ordersChanged).bodyDelay(10, TimeUnit.SECONDS).build(),
        )
        server.enqueue(ticket())
        server.enqueue(sse(ordersChanged))

        val updates = withTimeout(5_000) {
            stream(stall = Duration.ofMillis(300)).updates().takeWhile { it !is StreamUpdate.Received }.toList()
        }

        assertThat(updates.filterIsInstance<StreamUpdate.Open>()).hasSize(2)
        assertThat(updates.filterIsInstance<StreamUpdate.Lost>()).hasSize(1)
        assertThat(server.requestCount).isEqualTo(4)
    }

    @Test fun tras_una_conexion_buena_la_espera_para_reconectar_vuelve_a_empezar() = runBlocking {
        val waits = mutableListOf<Int>()
        server.enqueue(MockResponse(code = 500))
        server.enqueue(MockResponse(code = 500))
        server.enqueue(ticket())
        server.enqueue(sse(ordersChanged))  // un evento y el servidor cierra
        server.enqueue(MockResponse(code = 500))
        server.enqueue(MockResponse(code = 500))

        withTimeout(5_000) {
            stream(backoff = { failures -> waits += failures; 0L }).updates().filterIsInstance<StreamUpdate.Lost>().take(4).toList()
        }

        // Antes la cuenta nunca volvía a cero: tras unos cortes, cada reconexión esperaba de 15 a 30 s.
        assertThat(waits).containsExactly(0, 1, 0, 1).inOrder()
    }

    @Test fun reconectar_ya_no_espera_la_pausa() = runBlocking {
        server.enqueue(MockResponse(code = 500))
        server.enqueue(ticket())
        server.enqueue(sse(ordersChanged))
        val stream = stream(backoff = { 60_000L })

        val received = withTimeout(5_000) {
            stream.updates()
                .onEach { if (it is StreamUpdate.Lost) stream.reconnectNow() }
                .filterIsInstance<StreamUpdate.Received>()
                .first()
        }

        assertThat(received.event).isEqualTo(ServerEvent.OrdersChanged)
    }

    @Test fun con_la_app_ocupada_guardando_no_se_pierde_ningun_evento() = runBlocking {
        // Con «cambios» un evento perdido deja la bandeja desfasada hasta reconectar: antes, con la cola llena (64), el
        // evento nuevo se descartaba en silencio.
        server.enqueue(ticket())
        server.enqueue(sse((1..200).joinToString("") { "data: {\"domain\":\"marketing\",\"type\":\"campaign_updated\",\"id\":\"c$it\",\"ts_ms\":5}\n\n" }))

        val received = withTimeout(10_000) {
            stream().updates().filterIsInstance<StreamUpdate.Received>()
                .onEach { kotlinx.coroutines.delay(5) }  // guardar en Room tarda
                .take(200).toList()
        }

        assertThat(received).hasSize(200)
    }

    @Test fun cada_corte_dice_por_que_y_cuanto_espera() = runBlocking {
        server.enqueue(MockResponse(code = 503))
        server.enqueue(MockResponse(code = 503))

        val lost = withTimeout(5_000) { stream(backoff = { 1_234L }).updates().filterIsInstance<StreamUpdate.Lost>().first() }

        assertThat(lost.reason).contains("503")
        assertThat(lost.retryInMs).isEqualTo(1_234L)
    }
}
