package com.hubara.operator.feature.chat

import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.assertIsEnabled
import androidx.compose.ui.test.assertIsNotEnabled
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.DeliveryState
import com.hubara.operator.core.model.Message
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Con el humano al mando el operador puede mandarle fotos al cliente: las elige, las ve antes de enviar (y quita la
 * que no era), escribe un pie si quiere y toca «Enviar». Con el bot al mando o la ventana de 24 h cerrada no hay cómo.
 */
@RunWith(AndroidJUnit4::class)
class PhotoComposerTest {
    @get:Rule val compose = createComposeRule()

    private var attachTaps = 0
    private val removed = mutableListOf<String>()
    private var sends = 0
    private val actions = ChatActions(
        onMore = {}, onReactivate = {}, onIntervene = {}, onSend = {}, onSendText = { sends++ },
        onUndo = {}, onRetry = {}, onDismiss = {}, onClearError = {},
        onAttach = { attachTaps++ }, onRemoveAttachment = { removed += it },
    )

    private fun show(ui: ChatUiState) = compose.setContent { OperatorTheme { ChatBody(ui, TextFieldState(), actions) } }

    private val human = ChatUiState(humanInControl = true, history = HistoryView.MESSAGES)

    @Test fun con_el_humano_al_mando_se_pueden_adjuntar_fotos() {
        show(human)

        compose.onNodeWithContentDescription("Adjuntar fotos").performClick()

        compose.runOnIdle { assertThat(attachTaps).isEqualTo(1) }
    }

    @Test fun con_el_bot_al_mando_no_hay_como_adjuntar() {
        show(human.copy(humanInControl = false))
        compose.onNodeWithContentDescription("Adjuntar fotos").assertDoesNotExist()
    }

    @Test fun con_la_ventana_cerrada_no_hay_como_adjuntar() {
        show(human.copy(windowOpen = false))
        compose.onNodeWithContentDescription("Adjuntar fotos").assertDoesNotExist()
    }

    @Test fun las_fotos_elegidas_se_ven_antes_de_enviar_y_se_pueden_quitar() {
        show(human.copy(attachments = persistentListOf("content://media/1", "content://media/2")))

        compose.onAllNodesWithContentDescription("Foto para enviar").assertCountEquals(2)
        compose.onNodeWithText("Añade un pie de foto…").assertExists()
        // Sin texto también sale: una foto sola es un mensaje.
        compose.onNodeWithContentDescription("Enviar").assertIsEnabled().performClick()
        compose.onAllNodesWithContentDescription("Quitar foto")[0].performClick()

        compose.runOnIdle {
            assertThat(sends).isEqualTo(1)
            assertThat(removed).containsExactly("content://media/1")
        }
    }

    @Test fun sin_texto_ni_fotos_no_se_envia_nada() {
        show(human)
        compose.onNodeWithText("Escribe un mensaje…").assertExists()
        compose.onNodeWithContentDescription("Enviar").assertIsNotEnabled()
    }

    @Test fun con_el_maximo_de_fotos_no_se_eligen_mas() {
        val full = List(OutboxRepository.MAX_PHOTOS) { "content://media/$it" }.toImmutableList()
        show(human.copy(attachments = full))
        compose.onNodeWithContentDescription("Adjuntar fotos").assertIsNotEnabled()
    }

    // La foto que está saliendo es un archivo del teléfono: se ve en el chat. Una ruta de archivo que llega del
    // servidor, no (las fotos del historial solo se cargan desde nuestro backend, con el token de la sesión).
    @Test fun solo_la_foto_que_esta_saliendo_se_abre_desde_el_telefono() {
        val backend = { path: String? -> path?.takeIf { it.startsWith("/api/") }?.let { "http://10.0.2.2:8000$it" } }
        val sending = Message("outbox:c1", Author.HUMAN, "¿Te gusta?", "file:///data/fotos/c1.jpg", 1L, DeliveryState.PENDING)
        val history = Message("wamid.1", Author.HUMAN, null, "/api/dashboard/media/wa_test_laura/out-1.jpg", 1L)
        val forged = Message("wamid.2", Author.CUSTOMER, null, "file:///data/data/otra/secreto.jpg", 1L)

        assertThat(sending.forDisplay(backend).imageUrl).isEqualTo("file:///data/fotos/c1.jpg")
        assertThat(history.forDisplay(backend).imageUrl).isEqualTo("http://10.0.2.2:8000/api/dashboard/media/wa_test_laura/out-1.jpg")
        assertThat(forged.forDisplay(backend).imageUrl).isNull()
    }

    @Test fun enviar_decide_si_sale_texto_o_fotos_con_su_pie() {
        assertThat(sendPlan("  ", emptyList())).isEqualTo(SendPlan.Nothing)
        assertThat(sendPlan(" Hola ", emptyList())).isEqualTo(SendPlan.Text("Hola"))
        assertThat(sendPlan("", listOf("content://media/1"))).isEqualTo(SendPlan.Photos(listOf("content://media/1"), null))
        assertThat(sendPlan(" Mira ", listOf("content://media/1"))).isEqualTo(SendPlan.Photos(listOf("content://media/1"), "Mira"))
        // WhatsApp corta el pie en 1024: mejor avisar antes que subir la foto y que el envío la rechace.
        assertThat(sendPlan("a".repeat(OutboxRepository.MAX_CAPTION + 1), listOf("content://media/1"))).isEqualTo(SendPlan.CaptionTooLong)
    }
}
