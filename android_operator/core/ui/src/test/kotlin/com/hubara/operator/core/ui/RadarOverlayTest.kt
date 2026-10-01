package com.hubara.operator.core.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.material3.TextField
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.assertIsFocused
import androidx.compose.ui.test.hasSetTextAction
import androidx.compose.ui.test.hasText
import androidx.activity.ComponentActivity
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.compose.ui.test.performTextInput
import androidx.compose.ui.unit.dp
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class RadarOverlayTest {
    @get:Rule val compose = createAndroidComposeRule<ComponentActivity>()

    private fun grave(title: String) = Fire(
        id = FireId.parse("chat:wa_test_sofia")!!,
        subject = FireSubject.Chat(SessionId.parse("wa_test_sofia")!!),
        severity = Severity.GRAVE, kind = FireKind.WANTS_HUMAN, gettingWorse = true,
        title = title, subtitle = "12 min sin respuesta", primaryAction = ActionRef("open_chat"),
        decidedBy = "rules", updatedMs = 1,
    )

    @Test fun un_incendio_grave_aparece_sin_quitarle_el_foco_al_campo_de_texto() {
        var cards: ImmutableList<Fire> by mutableStateOf(persistentListOf())
        compose.setContent {
            OperatorTheme {
                Box {
                    var text by remember { mutableStateOf("") }
                    Column { TextField(value = text, onValueChange = { text = it }) }
                    RadarOverlay(cards = cards, expanded = true, compact = true, maxHeight = 600.dp, onOpen = {}, onHide = {}, onCollapse = {})
                }
            }
        }
        compose.onNode(hasSetTextAction()).performClick().performTextInput("te confirmo el")
        compose.onNode(hasSetTextAction()).assertIsFocused()

        cards = persistentListOf(grave("Sofía pide un humano"))
        compose.waitForIdle()

        compose.onNodeWithText("Sofía pide un humano").assertIsDisplayed()
        compose.onNode(hasSetTextAction()).assertIsFocused()
        compose.onNode(hasSetTextAction()).assert(hasText("te confirmo el"))
        compose.onNode(hasSetTextAction()).performTextInput(" aroma")
        compose.onNode(hasSetTextAction()).assert(hasText("te confirmo el aroma"))
    }

    @Test fun un_toque_en_el_primer_medio_segundo_no_abre_el_caso() {
        val opened = mutableListOf<FireId>()
        compose.mainClock.autoAdvance = false
        compose.setContent {
            OperatorTheme { RadarOverlay(cards = persistentListOf(grave("Sofía")), expanded = true, maxHeight = 600.dp, onOpen = { opened += it }, onHide = {}, onCollapse = {}) }
        }
        compose.mainClock.advanceTimeBy(100)
        compose.onNodeWithText("Sofía").performClick()
        assertThat(opened).isEmpty()

        compose.mainClock.advanceTimeBy(RadarDefaults.ARM_DELAY_MS + 50)
        compose.onNodeWithText("Sofía").performClick()
        assertThat(opened).hasSize(1)
    }

    // Atrás pliega la lista que el operador desplegó; las tarjetas que aparecieron solas no se quedan con el gesto
    // (se pliegan solas a los 4 s): atrás sigue siendo atrás para la pantalla de abajo.
    @Test fun atras_pliega_el_radar_desplegado_pero_no_las_tarjetas_que_aparecieron_solas() {
        var collapsed = 0
        var compact by mutableStateOf(true)
        compose.setContent {
            OperatorTheme {
                RadarOverlay(
                    cards = persistentListOf(grave("Sofía pide un humano")), expanded = true, compact = compact,
                    maxHeight = 600.dp, onOpen = {}, onHide = {}, onCollapse = { collapsed++ },
                )
            }
        }
        compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
        compose.waitForIdle()
        assertThat(collapsed).isEqualTo(0)

        compact = false
        compose.waitForIdle()
        compose.runOnUiThread { compose.activity.onBackPressedDispatcher.onBackPressed() }
        compose.waitForIdle()
        assertThat(collapsed).isEqualTo(1)
    }
}
