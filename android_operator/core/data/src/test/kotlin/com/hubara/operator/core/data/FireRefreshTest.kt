package com.hubara.operator.core.data

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.sync.refreshOnSignal
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Test

class FireRefreshTest {
    // «La espera para mostrar una tarjeta de incendio no se debe hacer»: la primera señal recarga ya.
    // El debounce de antes esperaba 1,5 s de silencio (y con señales seguidas no recargaba nunca).
    @Test fun un_incendio_se_pide_sin_esperar_y_una_rafaga_cuenta_como_una() = runTest {
        val signals = MutableSharedFlow<Unit>(extraBufferCapacity = 1, onBufferOverflow = BufferOverflow.DROP_OLDEST)
        var refreshes = 0
        val job = launch { refreshOnSignal(signals, minGapMs = 1_500) { refreshes++ } }
        runCurrent()

        signals.tryEmit(Unit)
        runCurrent()
        assertThat(refreshes).isEqualTo(1)

        repeat(5) { advanceTimeBy(200); signals.tryEmit(Unit) }
        runCurrent()
        assertThat(refreshes).isEqualTo(1)

        advanceTimeBy(1_500)
        runCurrent()
        assertThat(refreshes).isEqualTo(2)
        job.cancel()
    }
}
