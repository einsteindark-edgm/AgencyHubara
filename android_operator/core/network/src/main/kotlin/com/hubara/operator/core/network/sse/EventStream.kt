package com.hubara.operator.core.network.sse

import com.hubara.operator.core.network.api.OperatorApi
import kotlin.math.min
import kotlin.random.Random
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.retryWhen
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources

/**
 * El SSE del dashboard. Pide un ticket nuevo en CADA conexión (el ticket es de vida corta y el access
 * token nunca va en la URL, SEC-06) y reconecta de 1 a 30 s con jitter (lección #181).
 */
class EventStream(
    client: OkHttpClient,
    private val baseUrl: HttpUrl,
    private val api: OperatorApi,
) {
    private val sseClient = client.newBuilder().readTimeout(java.time.Duration.ZERO).build()

    fun events(): Flow<ServerEvent> = callbackFlow {
        val ticket = api.sseTicket().ticket
        val url = baseUrl.newBuilder()
            .addPathSegments("api/dashboard/events")
            .addQueryParameter("ticket", ticket)
            .build()
        val source = EventSources.createFactory(sseClient).newEventSource(
            Request.Builder().url(url).header("Accept", "text/event-stream").build(),
            object : EventSourceListener() {
                override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                    decodeServerEvent(data)?.let { trySend(it) }
                }

                override fun onClosed(eventSource: EventSource) {
                    close(IllegalStateException("sse cerrado por el servidor"))
                }

                override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                    close(t ?: IllegalStateException("sse falló: ${response?.code}"))
                }
            },
        )
        awaitClose { source.cancel() }
    }.retryWhen { _, attempt ->
        delay(backoffMs(attempt))
        true
    }

    companion object {
        fun backoffMs(attempt: Long): Long {
            val base = min(30_000L, 1_000L shl min(attempt, 5L).toInt())
            return base / 2 + Random.nextLong(base / 2 + 1)
        }
    }
}
