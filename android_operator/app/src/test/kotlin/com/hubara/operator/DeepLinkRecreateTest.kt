package com.hubara.operator

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.ui.test.hasText
import androidx.compose.ui.test.junit4.createEmptyComposeRule
import androidx.test.core.app.ActivityScenario
import androidx.test.core.app.ApplicationProvider
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
 * La auditoría de navegación encontró que el enlace de una notificación se volvía a aplicar en cada recreación de
 * la actividad (girar, cambiar el tema o el tamaño de letra, volver tras la muerte del proceso): el operador volvía
 * al chat del enlace aunque ya hubiera salido de él.
 */
@HiltAndroidTest
@Config(application = HiltTestApplication::class)
@RunWith(AndroidJUnit4::class)
class DeepLinkRecreateTest {
    @get:Rule(order = 0) val hilt = HiltAndroidRule(this)
    @get:Rule(order = 1) val compose = createEmptyComposeRule()

    companion object {
        init { FakeBackend.start() }
    }

    @Before fun setUp() = hilt.inject()

    private fun shows(text: String) = compose.onAllNodes(hasText(text, substring = true)).fetchSemanticsNodes().isNotEmpty()

    @Test fun el_enlace_de_una_notificacion_no_se_vuelve_a_abrir_al_recrear_la_actividad() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val link = Intent(context, MainActivity::class.java).setData(Uri.parse("hubara://chat/wa_test_laura"))
        ActivityScenario.launch<MainActivity>(link).use { scenario ->
            compose.waitUntil(10_000) { shows("¿y qué aromas tienen?") }

            // El operador sale del chat a la bandeja.
            scenario.onActivity { it.onBackPressedDispatcher.onBackPressed() }
            compose.waitUntil(10_000) { !shows("¿y qué aromas tienen?") && shows("+57 000 000 0000") }

            scenario.recreate()
            compose.waitUntil(10_000) { shows("+57 000 000 0000") }
            compose.waitForIdle()
            assertThat(shows("¿y qué aromas tienen?")).isFalse()
        }
    }
}
