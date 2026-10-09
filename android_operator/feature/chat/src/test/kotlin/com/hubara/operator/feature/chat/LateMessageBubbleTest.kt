package com.hubara.operator.feature.chat

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.Message
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Caso 2026-10-09: Meta re-entregó un saludo escrito 3 días antes, 19 s después de la plantilla del
 * operador. Con la hora de llegada parecía su respuesta y el operador no entendió por qué el bot no contestaba.
 */
@RunWith(AndroidJUnit4::class)
class LateMessageBubbleTest {
    @get:Rule val compose = createComposeRule()

    private val bogota = ZoneId.of("America/Bogota")
    private fun at(d: Int, h: Int, mi: Int) = ZonedDateTime.of(2026, 10, d, h, mi, 0, 0, bogota).toInstant().toEpochMilli()
    private val now = at(9, 16, 40) // viernes

    private fun customer(sentAtMs: Long? = null, arrivedAfterWindow: Boolean = false) = Message(
        key = "w1", author = Author.CUSTOMER, text = "Hola, ¿siguen teniendo velas?", imageUrl = null,
        timestampMs = at(9, 16, 35), sentAtMs = sentAtMs, arrivedAfterWindow = arrivedAfterWindow,
    )

    @Test fun un_mensaje_que_llego_tarde_dice_cuando_lo_escribio_y_cuando_llego() {
        compose.setContent { OperatorTheme { MessageBubble(customer(sentAtMs = at(6, 12, 2)), zone = bogota, nowMs = now) } }

        compose.onNodeWithText("Escrito el martes, 12:02 p. m. · llegó 4:35 p. m.").assertExists()
    }

    @Test fun con_la_ventana_cerrada_explica_por_que_el_bot_no_contesto() {
        compose.setContent {
            OperatorTheme { MessageBubble(customer(sentAtMs = at(6, 12, 2), arrivedAfterWindow = true), zone = bogota, nowMs = now) }
        }

        compose.onNodeWithText(
            "El bot no respondió: este mensaje llegó con la ventana de 24 h cerrada. Solo un mensaje nuevo del cliente la abre.",
        ).assertExists()
    }

    @Test fun un_mensaje_a_tiempo_solo_lleva_su_hora() {
        compose.setContent { OperatorTheme { MessageBubble(customer(), zone = bogota, nowMs = now) } }

        compose.onNodeWithText("4:35 p. m.").assertExists()
        compose.onNodeWithText(WINDOW_CLOSED_NOTE).assertDoesNotExist()
    }
}
