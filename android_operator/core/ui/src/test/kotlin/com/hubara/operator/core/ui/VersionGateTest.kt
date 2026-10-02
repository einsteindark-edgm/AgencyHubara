package com.hubara.operator.core.ui

import androidx.compose.material3.Text
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.designsystem.OperatorTheme
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

/**
 * La configuración del servidor trae la versión mínima de la app: si el backend cambia algo que una versión vieja no
 * entiende, sube ese número y los teléfonos con la vieja piden actualizarse en vez de fallar a medias.
 */
@RunWith(AndroidJUnit4::class)
class VersionGateTest {
    @get:Rule val compose = createComposeRule()

    @Test fun con_una_version_vieja_pide_actualizar_y_lleva_a_la_tienda() {
        var opened = 0
        compose.setContent { OperatorTheme { VersionGate(minVersionCode = 3, appVersionCode = 2, onUpdate = { opened++ }) { Text("la app") } } }
        compose.onNodeWithText("la app").assertDoesNotExist()
        compose.onNodeWithText("Actualizar").performClick()
        assertThat(opened).isEqualTo(1)
    }

    @Test fun con_la_version_al_dia_muestra_la_app() {
        compose.setContent { OperatorTheme { VersionGate(minVersionCode = 2, appVersionCode = 2, onUpdate = {}) { Text("la app") } } }
        compose.onNodeWithText("la app").assertExists()
    }
}
