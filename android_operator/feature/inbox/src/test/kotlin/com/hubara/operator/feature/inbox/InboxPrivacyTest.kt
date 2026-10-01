package com.hubara.operator.feature.inbox

import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithContentDescription
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/** La política de datos de Google Play exige la política de privacidad en la ficha Y dentro de la app. */
@RunWith(AndroidJUnit4::class)
class InboxPrivacyTest {
    @get:Rule val compose = createComposeRule()

    @Test fun la_politica_de_privacidad_se_abre_desde_la_bandeja() {
        var opened = 0
        compose.setContent { OperatorTheme { InboxScreen(InboxUiState(), onFilter = {}, onOpen = {}, onOpenPrivacy = { opened++ }) } }
        compose.onNodeWithContentDescription("Más opciones").performClick()
        compose.onNodeWithText("Política de privacidad").performClick()
        assertThat(opened).isEqualTo(1)
    }
}
