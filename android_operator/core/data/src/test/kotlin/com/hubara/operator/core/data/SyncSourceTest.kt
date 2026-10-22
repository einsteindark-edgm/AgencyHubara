package com.hubara.operator.core.data

import app.cash.turbine.test
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.sources.SyncSource
import com.hubara.operator.core.data.screens.sources.syncJson
import com.hubara.operator.core.data.sync.LiveSync
import com.hubara.operator.core.data.sync.SyncState
import com.hubara.operator.core.data.sync.SyncStatus
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Test

/**
 * La pantalla «Sincronización» (fuente del teléfono `sync`): dice en palabras si los mensajes llegan en vivo, cuánto
 * tardan y qué se cortó, para que el operador sepa si la app está al día sin mirar el log.
 */
class SyncSourceTest {
    private val now = 1_761_000_000_000L

    private fun JsonElement.str(key: String) = ((this as JsonObject)[key] as JsonPrimitive).content

    @Test fun en_vivo_dice_cuanto_tarda_lo_que_llega_y_lo_que_paso_desde_que_se_abrio() {
        val json = syncJson(
            SyncStatus(
                state = SyncState.LIVE, connectedSinceMs = now - 300_000, lastEventMs = now - 3_000, lagMs = 400, maxLagMs = 2_100,
                events = 1_234, bytes = 1_258_291, cuts = 2, lastError = "sin red", lastErrorMs = now - 120_000,
                lastRefreshMs = now - 60_000, lastRefreshOk = true,
            ),
            now,
        )
        assertThat(json.str("state")).isEqualTo("live")
        assertThat(json.str("live")).isEqualTo("true")
        assertThat(json.str("state_label")).isEqualTo("En vivo")
        assertThat(json.str("tone")).isEqualTo("success")
        assertThat(json.str("detail")).isEqualTo("Conectada desde hace 5 min: lo que llega al servidor se ve al instante.")
        assertThat(json.str("last_event")).isEqualTo("hace 3 s")
        assertThat(json.str("lag")).isEqualTo("0,4 s")
        assertThat(json.str("max_lag")).isEqualTo("2,1 s")
        assertThat(json.str("events")).isEqualTo("1234")
        assertThat(json.str("data")).isEqualTo("1,2 MB")
        assertThat(json.str("cuts")).isEqualTo("2")
        assertThat(json.str("last_error")).isEqualTo("sin red · hace 2 min")
        assertThat(json.str("refresh")).isEqualTo("Bien · hace 1 min")
    }

    @Test fun cortada_dice_por_que_y_cuando_vuelve_a_intentar() {
        val json = syncJson(SyncStatus(state = SyncState.RETRYING, lastError = "sin red", lastErrorMs = now, retryAtMs = now + 12_000), now)
        assertThat(json.str("live")).isEqualTo("false")
        assertThat(json.str("state_label")).isEqualTo("Reconectando…")
        assertThat(json.str("tone")).isEqualTo("warning")
        assertThat(json.str("detail")).isEqualTo("Se cortó (sin red). Vuelve a intentar en 12 s; desliza hacia abajo para intentar ya.")
        assertThat(json.str("last_event")).isEqualTo("Todavía nada")
        assertThat(json.str("lag")).isEqualTo("—")
        assertThat(json.str("data")).isEqualTo("0 KB")
        assertThat(json.str("refresh")).isEqualTo("Todavía no")
    }

    @Test fun conectando_o_en_pausa() {
        assertThat(syncJson(SyncStatus(state = SyncState.CONNECTING), now).str("state_label")).isEqualTo("Conectando…")
        val stopped = syncJson(SyncStatus(state = SyncState.STOPPED, lastRefreshMs = now - 5_000, lastRefreshOk = false), now)
        assertThat(stopped.str("state_label")).isEqualTo("En pausa")
        assertThat(stopped.str("refresh")).isEqualTo("Falló · hace 5 s")
    }

    @Test fun lo_que_dice_avanza_cada_segundo_mientras_se_mira() = runTest {
        var clock = now
        val sync = object : LiveSync {
            override val status = MutableStateFlow(SyncStatus(state = SyncState.LIVE, connectedSinceMs = now, lastEventMs = now))
            override val inboxRenewals = MutableSharedFlow<Boolean>()
            override suspend fun syncNow() = Result.success(Unit)
        }
        SyncSource(sync) { clock }.observe(emptyMap()).test {
            assertThat(awaitItem().str("last_event")).isEqualTo("ahora")
            clock += 7_000
            advanceTimeBy(7_001)
            assertThat(expectMostRecentItem().str("last_event")).isEqualTo("hace 7 s")
            cancelAndIgnoreRemainingEvents()
        }
    }
}
