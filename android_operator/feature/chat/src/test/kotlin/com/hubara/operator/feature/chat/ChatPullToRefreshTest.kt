package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.performTouchInput
import androidx.compose.ui.test.swipeDown
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
 * Pedido del operador (2026-10-21): «deslizar hacia abajo… que vaya al servidor a traer la información» sin esperar al
 * tiempo real. En el chat abierto, tirar hacia abajo desde arriba del historial vuelve a pedir la conversación.
 */
@RunWith(AndroidJUnit4::class)
class ChatPullToRefreshTest {
    @get:Rule val compose = createComposeRule()

    @Test fun tirar_hacia_abajo_el_historial_lo_pide_al_servidor() {
        var pulls = 0
        val actions = ChatActions(
            onMore = {}, onReactivate = {}, onIntervene = {}, onSend = {}, onSendText = {},
            onUndo = {}, onRetry = {}, onDismiss = {}, onClearError = {}, onPull = { pulls++ },
        )
        val hola = Message("m1", Author.CUSTOMER, "Hola, ¿tienen velas de lavanda?", null, 1_790_000_000_000L)
        compose.setContent {
            OperatorTheme { ChatBody(ChatUiState(messages = persistentListOf(hola), history = HistoryView.MESSAGES), TextFieldState(), actions) }
        }

        compose.onNodeWithTag(CHAT_HISTORY_TAG).performTouchInput { swipeDown() }

        compose.runOnIdle { assertThat(pulls).isEqualTo(1) }
    }
}
