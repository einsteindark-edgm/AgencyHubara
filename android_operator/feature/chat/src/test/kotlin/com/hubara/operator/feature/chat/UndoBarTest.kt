package com.hubara.operator.feature.chat

import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.compose.ui.test.SemanticsMatcher
import androidx.compose.ui.test.assert
import androidx.compose.ui.test.hasClickAction
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.repo.OutboxState
import com.hubara.operator.core.data.repo.PendingAction
import com.hubara.operator.core.designsystem.OperatorTheme
import kotlinx.collections.immutable.persistentListOf
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class UndoBarTest {
    @get:Rule val compose = createComposeRule()

    // En el emulador la barra entera era región viva y el botón cambiaba de texto cada segundo:
    // TalkBack anunciaba «Deshacer 4, 3, 2…» y uiautomator nunca veía la pantalla quieta.
    @Test fun la_cuenta_regresiva_se_ve_pero_el_lector_de_pantalla_la_anuncia_una_vez() {
        val pending = PendingAction("a1", "Enviar aromas", OutboxState.PENDING_UNDO, System.currentTimeMillis(), null)
        compose.setContent { OperatorTheme { UndoBar(persistentListOf(pending), onUndo = {}, onRetry = {}, onDismiss = {}) } }

        val undo = compose.onNode(hasClickAction()).fetchSemanticsNode()
        assertThat(undo.config[SemanticsProperties.Text].map { it.text }).containsExactly("Deshacer")
        compose.onNodeWithText("Enviando «Enviar aromas»")
            .assert(SemanticsMatcher.expectValue(SemanticsProperties.LiveRegion, LiveRegionMode.Polite))
    }
}
