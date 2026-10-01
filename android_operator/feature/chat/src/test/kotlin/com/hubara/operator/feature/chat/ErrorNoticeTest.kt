package com.hubara.operator.feature.chat

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

@RunWith(AndroidJUnit4::class)
class ErrorNoticeTest {
    @get:Rule val compose = createComposeRule()

    // La auditoría encontró que el error del chat nunca se borraba («Sin conexión…» seguía aunque volviera la red):
    // el operador lo puede cerrar.
    @Test fun el_aviso_de_error_se_puede_cerrar() {
        var dismissed = 0
        compose.setContent { OperatorTheme { ErrorNotice("Sin conexión: mostrando lo último guardado.", onDismiss = { dismissed++ }) } }

        compose.onNodeWithText("Sin conexión: mostrando lo último guardado.").assertExists()
        compose.onNodeWithContentDescription("Cerrar aviso").performClick()
        assertThat(dismissed).isEqualTo(1)
    }
}
