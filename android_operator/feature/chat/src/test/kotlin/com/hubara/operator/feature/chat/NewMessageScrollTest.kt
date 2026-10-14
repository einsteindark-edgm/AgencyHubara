package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsNotDisplayed
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.hasScrollToIndexAction
import androidx.compose.ui.test.performScrollToIndex
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.Message
import kotlinx.collections.immutable.toImmutableList
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Lo encontró el escenario S30 (2026-10-13): con el chat abierto al final, lo que llegaba (el catálogo del bot, la
 * respuesta del cliente) quedaba debajo del borde — la lista se anclaba al mensaje que ya se veía — y el operador no
 * se enteraba. Como en toda app de mensajería: si estás al final, lo nuevo se ve; si subiste a leer, no te mueve.
 */
@RunWith(AndroidJUnit4::class)
class NewMessageScrollTest {
    @get:Rule val compose = createComposeRule()

    private val actions = ChatActions(
        onMore = {}, onReactivate = {}, onIntervene = {}, onSend = {}, onSendText = {},
        onUndo = {}, onRetry = {}, onDismiss = {}, onClearError = {},
    )

    private fun msg(i: Int, author: Author = Author.CUSTOMER) = Message(
        key = "m$i", author = author, imageUrl = null, timestampMs = 1_791_900_000_000L + i * 600_000L,
        text = "Mensaje $i: una línea larga para que la conversación no quepa entera en la pantalla del teléfono",
    )

    @Test fun con_el_chat_al_final_lo_que_llega_se_ve() {
        var messages by mutableStateOf((1..12).map { msg(it) })
        compose.setContent {
            OperatorTheme {
                ChatBody(ChatUiState(messages = messages.toImmutableList(), history = HistoryView.MESSAGES), TextFieldState(), actions)
            }
        }
        compose.onNodeWithText("Mensaje 12", substring = true).assertIsDisplayed()

        messages = messages + msg(13, Author.BOT)

        compose.onNodeWithText("Mensaje 13", substring = true).assertIsDisplayed()
    }

    @Test fun si_subio_a_leer_lo_nuevo_no_lo_mueve() {
        var messages by mutableStateOf((1..12).map { msg(it) })
        compose.setContent {
            OperatorTheme {
                ChatBody(ChatUiState(messages = messages.toImmutableList(), history = HistoryView.MESSAGES), TextFieldState(), actions)
            }
        }
        // Sube a leer: en la lista invertida, el índice 0 es lo más nuevo y los altos, lo viejo.
        compose.onNode(hasScrollToIndexAction()).performScrollToIndex(10)
        compose.onNodeWithText("Mensaje 12", substring = true).assertIsNotDisplayed()

        messages = messages + msg(13, Author.BOT)

        compose.onNodeWithText("Mensaje 13", substring = true).assertIsNotDisplayed()
    }
}
