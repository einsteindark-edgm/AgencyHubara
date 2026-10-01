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

/** La app no tenía cómo cerrar sesión. Se cierra desde la bandeja y pide confirmación: borra los datos del teléfono. */
@RunWith(AndroidJUnit4::class)
class InboxSignOutTest {
    @get:Rule val compose = createComposeRule()

    @Test fun cerrar_sesion_desde_la_bandeja_pide_confirmacion() {
        var signedOut = 0
        compose.setContent { OperatorTheme { InboxScreen(InboxUiState(), onFilter = {}, onOpen = {}, onSignOut = { signedOut++ }) } }

        compose.onNodeWithContentDescription("Más opciones").performClick()
        compose.onNodeWithText("Cerrar sesión").performClick()
        compose.onNodeWithText("Cancelar").performClick()
        assertThat(signedOut).isEqualTo(0)

        compose.onNodeWithContentDescription("Más opciones").performClick()
        compose.onNodeWithText("Cerrar sesión").performClick()
        compose.onNodeWithText("Se borran de este teléfono", substring = true).assertExists()
        compose.onNodeWithText("Cerrar sesión").performClick()
        assertThat(signedOut).isEqualTo(1)
    }
}
