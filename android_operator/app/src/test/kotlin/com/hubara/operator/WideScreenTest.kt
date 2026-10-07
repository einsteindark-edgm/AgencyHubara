package com.hubara.operator

import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import dagger.hilt.android.testing.HiltAndroidRule
import dagger.hilt.android.testing.HiltAndroidTest
import dagger.hilt.android.testing.HiltTestApplication
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

/**
 * Tablet o plegable abierto (≥ 840 dp): la bandeja y el chat van lado a lado. La auditoría de navegación encontró que
 * al pasar a Incendios el panel derecho seguía mostrando el chat de la pestaña Chats (con su composer activo): el
 * operador podía escribir en la conversación equivocada.
 */
@HiltAndroidTest
@Config(application = HiltTestApplication::class, qualifiers = "w1000dp-h700dp")
@RunWith(AndroidJUnit4::class)
class WideScreenTest {
    @get:Rule(order = 0) val hilt = HiltAndroidRule(this)
    @get:Rule(order = 1) val compose = createAndroidComposeRule<MainActivity>()

    companion object {
        init { FakeBackend.start() }
    }

    @Before fun setUp() = hilt.inject()

    private fun shows(text: String) = compose.onAllNodes(hasText(text, substring = true)).fetchSemanticsNodes().isNotEmpty()

    @Test fun cada_pestana_muestra_su_lista_y_su_detalle_sin_mezclarse() {
        compose.waitUntil(10_000) { shows("+57 000 000 0000") }
        // Sin chat elegido, el panel derecho lo dice en vez de quedar en blanco.
        compose.waitUntil(10_000) { shows("Elige un chat") }
        // El incendio nuevo aparece unos segundos y se pliega al chip.
        compose.waitUntil(10_000) { !shows("Sofía pide un humano") }

        compose.onNodeWithText("+57 000 000 0000").performClick()
        compose.waitUntil(10_000) { shows("¿y qué aromas tienen?") }

        compose.onNodeWithText("Incendios").performClick()
        compose.waitUntil(10_000) { shows("Sofía pide un humano") }
        compose.waitForIdle()
        assertThat(shows("¿y qué aromas tienen?")).isFalse()
    }
}
