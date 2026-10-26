package com.hubara.operator.core.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import app.cash.turbine.test
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.mapping.toEntity
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.ConversationRepository
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.data.repo.OrderRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.data.sync.SyncEngine
import com.hubara.operator.core.data.sync.SyncState
import com.hubara.operator.core.database.OperatorDatabase
import com.hubara.operator.core.model.Conversation
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.dto.ChatSessionDto
import com.hubara.operator.core.network.sse.EventStream
import com.hubara.operator.core.network.sse.ServerEvent
import com.hubara.operator.core.network.sse.StreamUpdate
import com.hubara.operator.core.network.toDomain
import java.io.IOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.shadows.ShadowLog

/**
 * La sincronización en vivo de la app (caso 2026-10-21: «no está siendo real time… aparece mucho Sin conexión»). Dice
 * en todo momento si está en vivo y cuánto tardan los eventos, guarda los cambios de la bandeja sin tocar lo demás, no
 * se apaga por un evento que no pudo guardar, pide la bandeja por REST mientras no hay tiempo real y deja su rastro en
 * el log (`adb logcat -s HubaraSync`) sin datos de clientes.
 */
@RunWith(AndroidJUnit4::class)
class RealtimeSyncTest {
    private lateinit var db: OperatorDatabase
    private val api = FakeOperatorApi()
    private var now = 1_000_000L
    private lateinit var sync: SyncEngine
    private val laura = SessionId.parse("wa_test_laura")!!
    private val carlos = SessionId.parse("wa_test_carlos")!!

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), OperatorDatabase::class.java)
            .allowMainThreadQueries().build()
        sync = SyncEngine(
            events = EventStream(OkHttpClient(), "http://localhost/".toHttpUrl(), api),
            conversations = ConversationRepository(api, db.conversations()),
            chats = ChatRepository(api, db.conversations(), db.messages(), db.outbox(), db.drafts()),
            suggestions = SuggestionRepository(api, db.suggestions()),
            fires = FireRepository(api, db.fires()) { now },
            orders = OrderRepository(OperatorService(api)),
            clock = { now },
        )
        ShadowLog.clear()
    }

    @After fun tearDown() = db.close()

    private fun row(id: String, preview: String = "Hola"): Conversation =
        ChatSessionDto(id, lastMessagePreview = preview).toDomain()!!

    private suspend fun inbox() = ConversationRepository(api, db.conversations()).observeInbox().first().associateBy { it.sessionId.raw }

    @Test fun los_cambios_tocan_solo_esos_chats_y_sacan_los_que_ya_no_estan() = runTest {
        sync.handle(ServerEvent.SessionsSnapshot(listOf(row("wa_test_laura"), row("wa_test_carlos"), row("wa_test_sofia"))))
        // La ventana de 24 h llega con el detalle del chat, no con la bandeja: un cambio no la borra.
        db.conversations().upsert(listOf(row("wa_test_laura").toEntity().copy(windowExpiresAtMs = 99)))

        sync.handle(ServerEvent.SessionsDelta(listOf(row("wa_test_laura", preview = "¿Me confirmas el precio?")), removed = listOf(carlos)))

        val rows = inbox()
        assertThat(rows.keys).containsExactly("wa_test_laura", "wa_test_sofia")
        assertThat(rows.getValue("wa_test_laura").lastMessagePreview).isEqualTo("¿Me confirmas el precio?")
        assertThat(rows.getValue("wa_test_sofia").lastMessagePreview).isEqualTo("Hola")
        assertThat(db.conversations().get("wa_test_laura")!!.windowExpiresAtMs).isEqualTo(99)
    }

    @Test fun un_cambio_de_la_bandeja_avisa_a_las_pantallas() = runTest {
        sync.changes.test {
            sync.handle(ServerEvent.SessionsDelta(listOf(row("wa_test_laura")), emptyList()))
            assertThat(listOf(awaitItem(), awaitItem())).containsExactly("chats", "fires")
        }
    }

    @Test fun el_estado_dice_si_esta_en_vivo_y_cuanto_tarda_lo_que_llega() = runTest {
        assertThat(sync.status.value.state).isEqualTo(SyncState.STOPPED)
        sync.onUpdate(StreamUpdate.Connecting(0))
        assertThat(sync.status.value.state).isEqualTo(SyncState.CONNECTING)

        now = 2_000_000
        sync.onUpdate(StreamUpdate.Open)
        assertThat(sync.status.value.state).isEqualTo(SyncState.LIVE)
        assertThat(sync.status.value.connectedSinceMs).isEqualTo(2_000_000)

        now = 2_003_000
        sync.onUpdate(StreamUpdate.Received(ServerEvent.SessionsDelta(listOf(row("wa_test_laura")), emptyList()), bytes = 1_200, sentAtMs = 2_002_600))
        with(sync.status.value) {
            assertThat(events).isEqualTo(1)
            assertThat(bytes).isEqualTo(1_200)
            assertThat(lastEventMs).isEqualTo(2_003_000)
            assertThat(lagMs).isEqualTo(400)
        }

        now = 2_004_000
        sync.onUpdate(StreamUpdate.Lost("sin red", retryInMs = 5_000))
        with(sync.status.value) {
            assertThat(state).isEqualTo(SyncState.RETRYING)
            assertThat(lastError).isEqualTo("sin red")
            assertThat(retryAtMs).isEqualTo(2_009_000)
            assertThat(cuts).isEqualTo(1)
            assertThat(connectedSinceMs).isNull()
        }
    }

    @Test fun un_evento_que_no_se_pudo_guardar_no_apaga_el_tiempo_real() = runTest {
        db.close()  // guardar en Room falla

        sync.onUpdate(StreamUpdate.Received(ServerEvent.SessionsSnapshot(listOf(row("wa_test_laura"))), bytes = 10, sentAtMs = null))

        // Antes la excepción salía de `run()` y el tiempo real quedaba apagado hasta mandar la app a segundo plano.
        assertThat(sync.status.value.lastError).startsWith("no se pudo guardar")
    }

    @Test fun lo_que_llega_por_el_tiempo_real_renueva_la_bandeja() = runTest {
        sync.inboxRenewals.test {
            sync.onUpdate(StreamUpdate.Received(ServerEvent.SessionsSnapshot(listOf(row("wa_test_laura"))), 10, null))
            assertThat(awaitItem()).isTrue()
            sync.onUpdate(StreamUpdate.Received(ServerEvent.SessionsDelta(listOf(row("wa_test_laura", "otro")), emptyList()), 10, null))
            assertThat(awaitItem()).isTrue()
        }
    }

    @Test fun tirar_para_actualizar_va_al_servidor_y_avisa_si_no_pudo() = runTest {
        sync.inboxRenewals.test {
            assertThat(sync.syncNow().isSuccess).isTrue()
            assertThat(awaitItem()).isTrue()
            assertThat(sync.status.value.lastRefreshOk).isTrue()

            api.sessionsError = IOException("sin red")
            assertThat(sync.syncNow().isFailure).isTrue()
            assertThat(awaitItem()).isFalse()
            assertThat(sync.status.value.lastRefreshOk).isFalse()
            assertThat(sync.status.value.lastRefreshMs).isEqualTo(now)
        }
    }

    @Test fun sin_tiempo_real_la_bandeja_y_los_chats_abiertos_se_piden_por_rest() = runTest {
        val open = sync.watch(laura)
        val poll = launch { sync.pollWhileDown() }

        advanceTimeBy(SyncEngine.FALLBACK_POLL_MS + 1)
        // Guardar en Room va en otro hilo: se espera de verdad a que pida el chat abierto.
        withContext(Dispatchers.Default) { withTimeout(5_000) { while ("session:wa_test_laura" !in api.calls.toList()) delay(10) } }
        assertThat(api.calls).contains("sessions")

        // En vivo no hace falta: el SSE ya trae todo.
        sync.onUpdate(StreamUpdate.Open)
        api.calls.clear()
        advanceTimeBy(SyncEngine.FALLBACK_POLL_MS + 1)
        assertThat(api.calls).isEmpty()

        poll.cancel()
        open.close()
    }

    @Test fun al_volver_a_la_app_el_tiempo_real_no_espera_a_los_incendios_ni_a_las_ordenes() = runTest {
        api.firesGate = CompletableDeferred()  // los incendios tardan (red lenta)
        val running = launch(Dispatchers.Default) { sync.run() }

        withContext(Dispatchers.Default) { withTimeout(5_000) { while ("sse-ticket" !in api.calls.toList()) delay(10) } }

        assertThat(api.firesGate!!.isCompleted).isFalse()
        running.cancel()
        api.firesGate!!.complete(Unit)
    }

    @Test fun la_sincronizacion_deja_su_rastro_en_el_log_sin_datos_de_clientes() = runTest {
        sync.onUpdate(StreamUpdate.Connecting(0))
        sync.onUpdate(StreamUpdate.Open)
        sync.onUpdate(StreamUpdate.Received(ServerEvent.SessionsSnapshot(listOf(row("wa_test_laura"), row("wa_test_carlos"))), 2_048, now - 300))
        sync.onUpdate(StreamUpdate.Received(ServerEvent.SessionsDelta(listOf(row("wa_test_laura", "otro")), listOf(carlos)), 512, now - 100))
        sync.onUpdate(StreamUpdate.Lost("sin red", 5_000))

        val lines = ShadowLog.getLogsForTag(SyncEngine.LOG_TAG).map { it.msg }
        assertThat(lines.any { it.startsWith("conectando") }).isTrue()
        assertThat(lines.any { it.startsWith("en vivo") }).isTrue()
        assertThat(lines.any { it.startsWith("bandeja completa: 2 chats") && "retraso 300 ms" in it }).isTrue()
        assertThat(lines.any { it.startsWith("cambios: 1 chat") && "1 fuera" in it && "retraso 100 ms" in it }).isTrue()
        assertThat(lines.any { it.startsWith("se cortó: sin red") && "reintento en 5 s" in it }).isTrue()
        assertThat(lines.none { "wa_test" in it }).isTrue()
    }
}
