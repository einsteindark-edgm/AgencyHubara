package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.Message
import kotlinx.collections.immutable.persistentListOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Abrir un chat que el teléfono no tiene guardado: antes se veía vacío (como si el cliente no hubiera escrito nada)
 * hasta que llegaba el historial, y si no llegaba, vacío para siempre.
 */
@RunWith(AndroidJUnit4::class)
class ChatLoadingTest {
    @get:Rule val compose = createComposeRule()

    private var reloads = 0
    private val actions = ChatActions(
        onMore = {}, onReactivate = {}, onIntervene = {}, onSend = {}, onSendText = {},
        onUndo = {}, onRetry = {}, onDismiss = {}, onClearError = {}, onReload = { reloads++ },
    )
    private val current = mutableStateOf(ChatUiState())
    private var started = false

    private fun show(ui: ChatUiState) {
        current.value = ui
        if (!started.also { started = true }) compose.setContent { OperatorTheme { ChatBody(current.value, TextFieldState(), actions) } }
        compose.mainClock.advanceTimeBy(1_000)
        compose.waitForIdle()
    }

    @Test fun el_historial_que_no_esta_guardado_muestra_cargando_y_no_un_chat_vacio() {
        assertThat(historyLoad(HistoryLoad.LOADING, hasMessages = false)).isEqualTo(HistoryView.LOADING)
        show(ChatUiState(history = HistoryView.LOADING))
        compose.onNodeWithContentDescription("Cargando…").assertExists()
    }

    @Test fun si_no_llega_ofrece_reintentar_en_vez_de_cargar_para_siempre() {
        assertThat(historyLoad(HistoryLoad.FAILED, hasMessages = false)).isEqualTo(HistoryView.FAILED)
        show(ChatUiState(history = HistoryView.FAILED))
        compose.onNodeWithContentDescription("Cargando…").assertDoesNotExist()
        compose.onNodeWithText("Reintentar").performClick()
        assertThat(reloads).isEqualTo(1)
    }

    @Test fun con_mensajes_guardados_se_ven_al_instante_aunque_recargue() {
        assertThat(historyLoad(HistoryLoad.LOADING, hasMessages = true)).isEqualTo(HistoryView.MESSAGES)
        assertThat(historyLoad(HistoryLoad.FAILED, hasMessages = true)).isEqualTo(HistoryView.MESSAGES)
        val hola = Message("m1", Author.CUSTOMER, "Hola, ¿tienen velas de lavanda?", null, 1_790_000_000_000L)
        show(ChatUiState(messages = persistentListOf(hola), history = HistoryView.MESSAGES))
        compose.onNodeWithText("Hola, ¿tienen velas de lavanda?").assertExists()
        compose.onNodeWithContentDescription("Cargando…").assertDoesNotExist()
    }

    @Test fun tomar_la_conversacion_muestra_que_trabaja_y_no_se_toca_dos_veces() {
        show(ChatUiState(busy = true, history = HistoryView.MESSAGES))
        compose.onNodeWithText("Tomar la conversación").assertIsNotEnabled()
        compose.onNodeWithContentDescription("Trabajando…").assertExists()
    }
}
