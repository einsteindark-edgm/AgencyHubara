package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Prominence
import com.hubara.operator.core.model.Suggestion
import com.hubara.operator.core.model.SuggestionTone
import kotlinx.collections.immutable.persistentListOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Cuando el humano cierra la venta la app sugiere «Crear pedido», como el botón del dashboard: abre el formulario del
 * pedido (una pantalla del servidor) y no le manda nada al cliente por el outbox.
 */
@RunWith(AndroidJUnit4::class)
class OrderBubbleTest {
    @get:Rule val compose = createComposeRule()

    private val sent = mutableListOf<String>()
    private val opened = mutableListOf<String>()
    private val actions = ChatActions(
        onMore = {}, onReactivate = {}, onIntervene = {}, onSend = { sent += it.label }, onSendText = {},
        onUndo = {}, onRetry = {}, onDismiss = {}, onClearError = {}, onOpen = { opened += it },
    )

    @Test fun crear_pedido_abre_el_formulario_y_no_envia_nada() {
        val order = Suggestion(
            id = "create_order", label = "Crear pedido", prominence = Prominence.PRIMARY,
            action = ActionRef("create_order"), editable = false, tone = SuggestionTone.ORDER, opens = "crear_pedido",
        )
        val payment = Suggestion("send_payment_methods", "Medios de pago", Prominence.NORMAL, ActionRef("send_payment_methods"), false)
        val ui = ChatUiState(humanInControl = true, suggestions = persistentListOf(order, payment), history = HistoryView.MESSAGES)
        compose.setContent { OperatorTheme { ChatBody(ui, TextFieldState(), actions) } }

        compose.onNodeWithText("Crear pedido").performClick()
        compose.onNodeWithText("Medios de pago").performClick()

        compose.runOnIdle {
            assertThat(opened).containsExactly("crear_pedido")
            assertThat(sent).containsExactly("Medios de pago")
        }
    }
}
