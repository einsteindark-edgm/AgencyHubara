/*
 * Adaptado de la receta oficial de Navigation 3 (github.com/android/nav3-recipes, bottomsheet),
 * Copyright 2025 The Android Open Source Project, Apache License 2.0.
 */
package com.hubara.operator.core.navigation

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.ModalBottomSheetProperties
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.compose.rememberLifecycleOwner
import androidx.navigation3.runtime.NavEntry
import androidx.navigation3.runtime.NavMetadataKey
import androidx.navigation3.runtime.get
import androidx.navigation3.runtime.metadata
import androidx.navigation3.scene.OverlayScene
import androidx.navigation3.scene.Scene
import androidx.navigation3.scene.SceneStrategy
import androidx.navigation3.scene.SceneStrategyScope

@OptIn(ExperimentalMaterial3Api::class)
internal data class BottomSheetScene<T : Any>(
    override val key: T,
    override val previousEntries: List<NavEntry<T>>,
    override val overlaidEntries: List<NavEntry<T>>,
    private val entry: NavEntry<T>,
    private val spec: SheetSpec,
    private val onBack: () -> Unit,
) : OverlayScene<T> {
    override val entries: List<NavEntry<T>> = listOf(entry)

    override val content: @Composable (() -> Unit) = {
        val lifecycleOwner = rememberLifecycleOwner()
        // Una ficha larga abre completa: a media altura la acción principal queda escondida (lo vio Artemis).
        // Y completa desde el primer cuadro: si su alto dependiera del contenido, una ficha que todavía carga
        // abriría baja y no subiría al llegar los datos (lo vio el conductor por guion en S07).
        val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = spec.expanded)
        ModalBottomSheet(onDismissRequest = onBack, sheetState = sheetState, properties = spec.properties) {
            CompositionLocalProvider(LocalLifecycleOwner provides lifecycleOwner) {
                if (spec.expanded) Box(Modifier.fillMaxHeight()) { entry.Content() } else entry.Content()
            }
        }
    }
}

/** Cómo se abre la hoja inferior de una entrada. */
@OptIn(ExperimentalMaterial3Api::class)
data class SheetSpec(val properties: ModalBottomSheetProperties, val expanded: Boolean)

/** Muestra como hoja inferior las entradas marcadas con [bottomSheet] (la ficha de la orden, «+ Más»). */
@OptIn(ExperimentalMaterial3Api::class)
class BottomSheetSceneStrategy<T : Any> : SceneStrategy<T> {
    override fun SceneStrategyScope<T>.calculateScene(entries: List<NavEntry<T>>): Scene<T>? {
        val last = entries.lastOrNull() ?: return null
        val spec = last.metadata[BottomSheetKey] ?: return null
        @Suppress("UNCHECKED_CAST")
        return BottomSheetScene(
            key = last.contentKey as T,
            previousEntries = entries.dropLast(1),
            overlaidEntries = entries.dropLast(1),
            entry = last,
            spec = spec,
            onBack = onBack,
        )
    }

    companion object {
        /** [expanded] = abre completa (fichas con la acción al final); si no, a media altura. */
        fun bottomSheet(expanded: Boolean = false, properties: ModalBottomSheetProperties = ModalBottomSheetProperties()) =
            metadata { put(BottomSheetKey, SheetSpec(properties, expanded)) }

        object BottomSheetKey : NavMetadataKey<SheetSpec>
    }
}
