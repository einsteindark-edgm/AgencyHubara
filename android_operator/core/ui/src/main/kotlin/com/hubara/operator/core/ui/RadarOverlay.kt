package com.hubara.operator.core.ui

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.SwipeToDismissBox
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberSwipeToDismissBoxState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.compositionLocalOf
import androidx.compose.runtime.key
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.navigationevent.NavigationEventInfo
import androidx.navigationevent.compose.NavigationBackHandler
import androidx.navigationevent.compose.rememberNavigationEventState
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import kotlinx.collections.immutable.ImmutableList

object RadarDefaults {
    /** Tiempo en que una tarjeta recién aparecida no responde al toque. */
    const val ARM_DELAY_MS = 500L

    /** Cuánto se ve la tarjeta de un incendio nuevo antes de plegarse al chip de la barra superior. */
    const val TRANSIENT_MS = 4_000L
}

/** Lo que necesita el chip del radar en la barra superior de cada pantalla. */
@Immutable
data class RadarIndicatorModel(val count: Int, val onExpand: () -> Unit)

val LocalRadarIndicator = compositionLocalOf<RadarIndicatorModel?> { null }

/**
 * El radar plegado: un chip en la barra superior, así nunca tapa contenido ni acciones de la pantalla.
 * Tocarlo despliega la lista.
 */
@Composable
fun RadarIndicator(modifier: Modifier = Modifier) {
    val model = LocalRadarIndicator.current ?: return
    if (model.count == 0) return
    AssistChip(
        onClick = model.onExpand,
        label = { Text(model.count.toString()) },
        leadingIcon = { Icon(Icons.Filled.Warning, contentDescription = null) },
        colors = AssistChipDefaults.assistChipColors(labelColor = OperatorTheme.colors.grave, leadingIconContentColor = OperatorTheme.colors.grave),
        modifier = modifier
            .focusProperties { canFocus = false }
            .semantics { contentDescription = "${model.count} incendios graves. Toca para verlos." },
    )
}

/**
 * El radar desplegado: los incendios graves, debajo del encabezado. Aparece apenas llega uno nuevo
 * (compacto, unos segundos) aunque el operador esté escribiendo, y NO le quita el foco al teclado: vive
 * en la misma ventana (no es un Dialog ni un Popup), no es enfocable, no pide foco y TalkBack lo anuncia
 * de forma cortés. Nunca pasa de [maxHeight]. Swipe = ocultar ese incendio; «Cerrar» lo pliega al chip.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RadarOverlay(
    cards: ImmutableList<Fire>,
    expanded: Boolean,
    maxHeight: Dp,
    onOpen: (FireId) -> Unit,
    onHide: (FireId) -> Unit,
    onCollapse: () -> Unit,
    modifier: Modifier = Modifier,
    compact: Boolean = false,
) {
    val haptics = LocalHapticFeedback.current
    val newest = cards.firstOrNull()?.id
    LaunchedEffect(newest) {
        if (newest != null) haptics.performHapticFeedback(HapticFeedbackType.ContextClick)
    }
    // Atrás pliega la lista desplegada. Todo lo demás usa el retroceso de Navigation 3.
    NavigationBackHandler(
        state = rememberNavigationEventState(currentInfo = NavigationEventInfo.None),
        isBackEnabled = expanded && cards.isNotEmpty(),
        onBackCompleted = onCollapse,
    )

    AnimatedVisibility(
        visible = expanded && cards.isNotEmpty(),
        enter = slideInVertically { -it } + fadeIn(),
        exit = fadeOut(),
        modifier = modifier,
    ) {
        Column(
            modifier = Modifier
                .fillMaxWidth()
                .heightIn(max = maxHeight)
                .padding(horizontal = 12.dp, vertical = 8.dp)
                .focusProperties { canFocus = false }
                .semantics { liveRegion = LiveRegionMode.Polite }
                .verticalScroll(rememberScrollState()),
            verticalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            cards.forEach { fire ->
                key(fire.id) {
                    SwipeToDismissBox(
                        state = rememberSwipeToDismissBoxState(),
                        backgroundContent = {},
                        onDismiss = { onHide(fire.id) },
                    ) {
                        FireCard(
                            fire = fire,
                            onClick = { onOpen(fire.id) },
                            armDelayMs = RadarDefaults.ARM_DELAY_MS,
                            onHide = { onHide(fire.id) },
                            compact = compact,
                        )
                    }
                }
            }
            if (!compact) {
                TextButton(onClick = onCollapse, modifier = Modifier.focusProperties { canFocus = false }) { Text("Cerrar") }
            }
        }
    }
}
