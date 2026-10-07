package com.hubara.operator.core.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.Snapshot
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
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
import java.text.Normalizer
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.persistentListOf
import kotlinx.collections.immutable.toImmutableList
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@RunWith(AndroidJUnit4::class)
@Config(qualifiers = "w360dp-h640dp")
class RadarLayerTest {
    @get:Rule val compose = createComposeRule()

    /** Los ids solo aceptan ASCII: «Lucía» → `wa_test_lucia`. */
    private fun grave(name: String): Fire {
        val slug = Normalizer.normalize(name, Normalizer.Form.NFD).replace(Regex("\\p{M}"), "").lowercase()
        return Fire(
            id = FireId.parse("chat:wa_test_$slug")!!,
            subject = FireSubject.Chat(SessionId.parse("wa_test_$slug")!!),
            severity = Severity.GRAVE, kind = FireKind.WANTS_HUMAN, gettingWorse = false,
            title = "$name pide un humano", subtitle = "11 min sin respuesta", primaryAction = ActionRef("open_chat"),
            decidedBy = "rules", updatedMs = 0,
        )
    }

    private fun layer(radar: () -> ImmutableList<Fire>, floor: RadarFloorState = RadarFloorState(), onExpand: (Boolean) -> Unit = {}) {
        compose.mainClock.autoAdvance = false
        compose.setContent {
            OperatorTheme {
                CompositionLocalProvider(LocalRadarFloor provides floor) {
                    Box(Modifier.fillMaxSize()) {
                        Column(Modifier.fillMaxSize()) {
                            Spacer(Modifier.height(160.dp))
                            RadarFloor { Box(Modifier.fillMaxWidth().height(80.dp).testTag("composer")) }
                        }
                        RadarLayer(
                            radar = radar(), expanded = false, onExpandedChange = onExpand, maxHeight = 600.dp,
                            floor = floor, onOpen = {}, onHide = {},
                        )
                    }
                }
            }
        }
        compose.mainClock.advanceTimeBy(600)
    }

    // Con el teclado abierto, tres tarjetas llegaban hasta el campo de texto en un teléfono pequeño.
    @Test fun las_tarjetas_que_aparecen_solas_nunca_bajan_del_composer() {
        layer({ persistentListOf(grave("Mateo"), grave("Lucía"), grave("Pedro")) })

        val composerTop = compose.onNodeWithTag("composer").getBoundsInRoot().top
        val radarBottom = compose.onNodeWithTag(RadarDefaults.TEST_TAG).getBoundsInRoot().bottom
        assertThat(radarBottom).isAtMost(composerTop)
    }

    @Test fun las_tarjetas_que_aparecen_solas_no_traen_ocultar() {
        layer({ persistentListOf(grave("Mateo")) })

        compose.onNodeWithText("Mateo pide un humano").assertIsDisplayed()
        compose.onNodeWithText("Ocultar").assertDoesNotExist()
    }

    @Test fun si_llegan_mas_de_tres_una_linea_cuenta_el_resto_y_despliega_el_radar() {
        var expandedTo: Boolean? = null
        val cinco = listOf("Mateo", "Lucía", "Pedro", "Sara", "Tomás").map(::grave).toImmutableList()
        var radar: ImmutableList<Fire> by mutableStateOf(persistentListOf())
        layer({ radar }, onExpand = { expandedTo = it })
        cinco.indices.forEach { i ->
            radar = cinco.take(i + 1).toImmutableList()
            Snapshot.sendApplyNotifications()
            compose.mainClock.advanceTimeBy(300)
        }
        compose.mainClock.advanceTimeBy(RadarDefaults.ARM_DELAY_MS + 100)

        compose.onNodeWithText("Tomás pide un humano").assertExists()
        compose.onNodeWithText("Lucía pide un humano").assertDoesNotExist()
        compose.onNodeWithText("Ver 2 más").performClick()
        assertThat(expandedTo).isTrue()
    }

    @Test fun la_rafaga_se_pliega_junta_cuatro_segundos_despues_de_la_ultima_llegada() {
        var radar: ImmutableList<Fire> by mutableStateOf(persistentListOf(grave("Mateo")))
        layer({ radar })
        compose.mainClock.advanceTimeBy(2_000)
        radar = persistentListOf(grave("Mateo"), grave("Lucía"))
        Snapshot.sendApplyNotifications()
        compose.mainClock.advanceTimeBy(RadarDefaults.TRANSIENT_MS - 500)

        compose.onNodeWithText("Mateo pide un humano").assertIsDisplayed()
        compose.onNodeWithText("Lucía pide un humano").assertIsDisplayed()

        compose.mainClock.advanceTimeBy(1_500)
        compose.onNodeWithText("Mateo pide un humano").assertDoesNotExist()
        compose.onNodeWithText("Lucía pide un humano").assertDoesNotExist()
    }
}
