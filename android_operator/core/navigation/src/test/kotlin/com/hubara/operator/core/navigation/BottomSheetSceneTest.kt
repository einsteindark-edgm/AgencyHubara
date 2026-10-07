package com.hubara.operator.core.navigation

import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheetProperties
import androidx.compose.material3.Text
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.test.getBoundsInRoot
import androidx.compose.ui.test.hasAnyDescendant
import androidx.compose.ui.test.hasTestTag
import androidx.compose.ui.test.isRoot
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.navigation3.runtime.NavEntry
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.annotation.Config

@OptIn(ExperimentalMaterial3Api::class)
@RunWith(AndroidJUnit4::class)
@Config(qualifiers = "w411dp-h914dp")
class BottomSheetSceneTest {
    @get:Rule val compose = createComposeRule()

    // En el emulador (S07, 09-30) la ficha del pedido abrió con el alto que tenía mientras cargaba y no subió al
    // llegar los datos: quedó abajo con solo el encabezado y «Marcar entregado» no se veía.
    @Test fun una_ficha_que_abre_completa_ocupa_la_pantalla_aunque_todavia_este_cargando() {
        val entry = NavEntry("ficha") { Text("Cargando…", Modifier.testTag("contenido")) }
        val scene = BottomSheetScene(
            key = "ficha", previousEntries = emptyList(), overlaidEntries = emptyList(), entry = entry,
            spec = SheetSpec(ModalBottomSheetProperties(), expanded = true), onBack = {},
        )
        compose.setContent { MaterialTheme { scene.content() } }
        compose.waitForIdle()

        val window = compose.onNode(isRoot() and hasAnyDescendant(hasTestTag("contenido"))).getBoundsInRoot()
        val top = compose.onNodeWithTag("contenido").getBoundsInRoot().top
        assertThat(top.value).isLessThan(window.bottom.value * 0.3f)
    }
}
