package com.hubara.operator.core.network.sse

import com.hubara.operator.core.network.api.OperatorApi
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.time.Duration
import kotlin.math.min
import kotlin.random.Random
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.flow.emitAll
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.onEach
import kotlinx.coroutines.flow.retryWhen
import kotlinx.coroutines.withTimeoutOrNull
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import okio.utf8Size
import retrofit2.HttpException

/** Lo que pasa con la conexión del tiempo real: la sincronización lo usa para su estado y sus logs. */
sealed interface StreamUpdate {
    /** Va a pedir el ticket y abrir la conexión ([failures]: cortes seguidos hasta ahora). */
    data class Connecting(val failures: Int) : StreamUpdate

    /** El servidor aceptó: desde aquí llega todo lo que cambia. */
    data object Open : StreamUpdate

    /** Un evento, con su tamaño y la hora en que salió del servidor (para medir el retraso). */
    data class Received(val event: ServerEvent, val bytes: Int, val sentAtMs: Long?) : StreamUpdate

    /** Se cortó (o no se pudo abrir): por qué y en cuánto se vuelve a intentar. */
    data class Lost(val reason: String, val retryInMs: Long) : StreamUpdate
}

/**
 * El SSE del dashboard. Pide un ticket nuevo en CADA conexión (el ticket es de vida corta y el access token nunca va en
 * la URL, SEC-06) y reconecta de 1 a 30 s con jitter (lección #181). La app pide `inbox=delta`: la bandeja completa al
 * conectar y después solo los chats que cambiaron (con 581 chats, la completa pesa ~380 KB y salía en cada cambio).
 *
 * El servidor manda un latido cada 25 s: si pasa [stallTimeout] sin nada, la conexión está muerta aunque nadie la haya
 * cerrado (en el celular pasa al cambiar de antena o de red) y se abre otra. Tras una conexión buena la espera vuelve a
 * empezar de cero; [reconnectNow] se salta la espera (tirar para actualizar).
 */
class EventStream(
    client: OkHttpClient,
    /** El backend vigente: se lee en cada conexión (si el servidor se muda, la reconexión ya va al nuevo). */
    private val baseUrl: () -> HttpUrl,
    private val api: OperatorApi,
    private val stallTimeout: Duration = STALL_TIMEOUT,
    /** Cuánto esperar después de [Int] cortes seguidos. */
    private val backoff: (Int) -> Long = { backoffMs(it.toLong()) },
) {
    constructor(client: OkHttpClient, baseUrl: HttpUrl, api: OperatorApi) : this(client, { baseUrl }, api)

    private val sseClient = client.newBuilder().readTimeout(stallTimeout).build()
    private val kicks = Channel<Unit>(Channel.CONFLATED)

    /** Los eventos y el estado de la conexión. Reconecta solo: termina únicamente si se cancela. */
    fun updates(): Flow<StreamUpdate> = flow {
        var failures = 0
        emitAll(
            connection { failures }
                .onEach { if (it == StreamUpdate.Open) failures = 0 }
                .retryWhen { cause, _ ->
                    val wait = backoff(failures++)
                    emit(StreamUpdate.Lost(reason(cause), wait))
                    withTimeoutOrNull(wait) { kicks.receive() }
                    true
                },
        )
    }

    /** Si está esperando para reconectar, reconecta ya. Con la conexión abierta no hace nada. */
    fun reconnectNow() {
        kicks.trySend(Unit)
    }

    private fun connection(failures: () -> Int): Flow<StreamUpdate> = callbackFlow {
        trySend(StreamUpdate.Connecting(failures()))
        val ticket = api.sseTicket().ticket
        val url = baseUrl().newBuilder()
            .addPathSegments("api/dashboard/events")
            .addQueryParameter("ticket", ticket)
            .addQueryParameter("inbox", "delta")
            .build()
        val source = EventSources.createFactory(sseClient).newEventSource(
            Request.Builder().url(url).header("Accept", "text/event-stream").build(),
            object : EventSourceListener() {
                override fun onOpen(eventSource: EventSource, response: Response) {
                    kicks.tryReceive()  // un «reconectar ya» de antes ya se cumplió
                    trySend(StreamUpdate.Open)
                }

                override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                    decodeServerFrame(data)?.let { trySend(StreamUpdate.Received(it.event, data.utf8Size().toInt(), it.sentAtMs)) }
                }

                override fun onClosed(eventSource: EventSource) {
                    close(IllegalStateException("el servidor cerró la conexión"))
                }

                override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                    close(t ?: IllegalStateException("HTTP ${response?.code}"))
                }
            },
        )
        awaitClose { source.cancel() }
    }.buffer(Channel.UNLIMITED)  // con la cola llena `trySend` descartaba en silencio: un cambio perdido desfasa la bandeja

    private fun reason(cause: Throwable): String = when (cause) {
        is SocketTimeoutException -> "${stallTimeout.seconds} s sin noticias del servidor"
        is UnknownHostException -> "sin red"
        is HttpException -> "HTTP ${cause.code()} al pedir el ticket"
        else -> cause.message ?: cause.javaClass.simpleName
    }

    companion object {
        /** Dos latidos del servidor (cada 25 s) y un margen. */
        val STALL_TIMEOUT: Duration = Duration.ofSeconds(60)

        fun backoffMs(attempt: Long): Long {
            val base = min(30_000L, 1_000L shl min(attempt, 5L).toInt())
            return base / 2 + Random.nextLong(base / 2 + 1)
        }
    }
}
