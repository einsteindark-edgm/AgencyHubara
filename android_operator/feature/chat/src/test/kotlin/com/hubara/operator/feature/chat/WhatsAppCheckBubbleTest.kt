package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.Column
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.assertCountEquals
import androidx.compose.ui.test.onAllNodesWithContentDescription
import androidx.compose.ui.test.onAllNodesWithText
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.CardIcon
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.MessageCard
import com.hubara.operator.core.model.WhatsAppDelivery
import com.hubara.operator.core.model.WhatsAppStatus
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Caso 2026-10-15: el operador quería saber, con datos de WhatsApp y no del bot, si cada mensaje llegó. Cada
 * burbuja saliente (bot u operador) lleva su doble check: enviado ✓, entregado ✓✓, leído ✓✓ (otro color),
 * fallido con el código de Meta, o «sin confirmación» si WhatsApp no informó nada.
 */
@RunWith(AndroidJUnit4::class)
class WhatsAppCheckBubbleTest {
    @get:Rule val compose = createComposeRule()

    private val bogota = ZoneId.of("America/Bogota")
    private val sentAt = ZonedDateTime.of(2026, 10, 14, 21, 40, 0, 0, bogota).toInstant().toEpochMilli()
    private val statusAt = ZonedDateTime.of(2026, 10, 14, 21, 45, 0, 0, bogota).toInstant().toEpochMilli()

    private fun out(whatsapp: WhatsAppDelivery?, author: Author = Author.BOT, card: MessageCard? = null) = Message(
        key = "w1", author = author, text = "¡Hola!", imageUrl = null, timestampMs = sentAt, card = card, whatsapp = whatsapp,
    )

    private fun show(vararg messages: Message, position: BubblePosition = BubblePosition.SINGLE) {
        compose.setContent {
            OperatorTheme { Column { messages.forEach { MessageBubble(it, zone = bogota, position = position) } } }
        }
    }

    @Test fun el_check_dice_lo_que_informo_whatsapp_y_a_que_hora() {
        show(
            out(WhatsAppDelivery(WhatsAppStatus.SENT, statusAt)),
            out(WhatsAppDelivery(WhatsAppStatus.DELIVERED, statusAt)),
            out(WhatsAppDelivery(WhatsAppStatus.READ, statusAt), author = Author.HUMAN),
        )

        compose.onNodeWithContentDescription("Enviado por WhatsApp 9:45 p. m.").assertExists()
        compose.onNodeWithContentDescription("Entregado por WhatsApp 9:45 p. m.").assertExists()
        compose.onNodeWithContentDescription("Leído por WhatsApp 9:45 p. m.").assertExists()
    }

    @Test fun un_fallido_se_lee_con_el_codigo_de_meta() {
        show(out(WhatsAppDelivery(WhatsAppStatus.FAILED, statusAt, 131047, "Re-engagement message")))

        compose.onNodeWithText("No llegó · 131047").assertExists()
    }

    @Test fun sin_dato_dice_sin_confirmacion_y_no_pinta_check() {
        show(out(WhatsAppDelivery.UNCONFIRMED))

        compose.onNodeWithText("sin confirmación").assertExists()
        compose.onAllNodesWithContentDescription("por WhatsApp", substring = true).assertCountEquals(0)
    }

    @Test fun cada_burbuja_del_grupo_lleva_su_check_aunque_no_sea_la_ultima() {
        show(out(WhatsAppDelivery(WhatsAppStatus.DELIVERED, statusAt)), position = BubblePosition.FIRST)

        compose.onNodeWithContentDescription("Entregado por WhatsApp 9:45 p. m.").assertExists()
    }

    @Test fun la_tarjeta_del_bot_tambien_lo_lleva() {
        val card = MessageCard(component = "products_list", label = "Catálogo", icon = CardIcon.CATALOG, text = "Tenemos:")
        show(out(WhatsAppDelivery(WhatsAppStatus.READ, statusAt), card = card))

        compose.onNodeWithContentDescription("Leído por WhatsApp 9:45 p. m.").assertExists()
    }

    @Test fun los_mensajes_del_cliente_no_llevan_check() {
        show(Message(key = "c1", author = Author.CUSTOMER, text = "hola", imageUrl = null, timestampMs = sentAt))

        compose.onAllNodesWithContentDescription("por WhatsApp", substring = true).assertCountEquals(0)
        compose.onAllNodesWithText("sin confirmación").assertCountEquals(0)
    }
}
