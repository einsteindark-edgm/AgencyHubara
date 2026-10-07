package com.hubara.operator.core.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.Stable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.RadarBurst
import kotlinx.collections.immutable.ImmutableList
import kotlinx.collections.immutable.toImmutableList
import kotlinx.coroutines.delay

/** Borde superior de lo que el radar que aparece solo nunca tapa: en el chat, deshacer, burbujas y composer. */
@Stable
class RadarFloorState {
    /** En px de la ventana; null si la pantalla actual no marcó piso. */
    var topPx: Float? by mutableStateOf(null)
}

val LocalRadarFloor = staticCompositionLocalOf<RadarFloorState?> { null }

/** Marca [content] como el piso del radar: las tarjetas que aparecen solas nunca bajan de su borde superior. */
@Composable
fun RadarFloor(modifier: Modifier = Modifier, content: @Composable ColumnScope.() -> Unit) {
    val floor = LocalRadarFloor.current
    DisposableEffect(floor) { onDispose { floor?.topPx = null } }
    Column(modifier.onGloballyPositioned { floor?.topPx = it.boundsInWindow().top }, content = content)
}

/**
 * La capa del radar encima de cualquier pantalla. Desplegada muestra todo el radar; plegada, los graves que van
 * llegando aparecen solos unos segundos y se pliegan al chip de la barra superior. Si llegan varios seguidos se
 * juntan (el más nuevo arriba, hasta [RadarBurst.MAX_VISIBLE] y «Ver N más») y nunca bajan del [RadarFloor]:
 * el operador sigue viendo lo que escribe.
 */
@Composable
fun RadarLayer(
    radar: ImmutableList<Fire>,
    expanded: Boolean,
    onExpandedChange: (Boolean) -> Unit,
    maxHeight: Dp,
    floor: RadarFloorState,
    onOpen: (FireId) -> Unit,
    onHide: (FireId) -> Unit,
    modifier: Modifier = Modifier,
) {
    var burst by remember { mutableStateOf(RadarBurst()) }
    LaunchedEffect(radar, expanded) { burst = burst.onRadar(radar, expanded) }
    LaunchedEffect(burst.arrivals) {
        if (burst.fires.isNotEmpty()) {
            delay(RadarDefaults.TRANSIENT_MS)
            burst = burst.folded()
        }
    }
    // Desplegado a pedido puede tapar el composer (el operador lo pidió); lo que aparece solo, nunca.
    var topPx by remember { mutableFloatStateOf(0f) }
    val floorTopPx = floor.topPx
    val density = LocalDensity.current
    val bound = if (expanded || floorTopPx == null) {
        maxHeight
    } else {
        minOf(maxHeight, with(density) { (floorTopPx - topPx).toDp() } - RadarDefaults.FLOOR_GAP).coerceAtLeast(0.dp)
    }
    val transient = burst.visible().toImmutableList()
    Box(modifier.onGloballyPositioned { topPx = it.boundsInWindow().top }) {
        RadarOverlay(
            cards = if (expanded) radar else transient,
            expanded = expanded || transient.isNotEmpty(),
            compact = !expanded,
            maxHeight = bound,
            overflow = if (expanded) 0 else burst.overflow(),
            onOpen = { id ->
                burst = burst.folded()
                onOpen(id)
            },
            onHide = onHide,
            onCollapse = {
                burst = burst.folded()
                onExpandedChange(false)
            },
            onExpand = {
                burst = burst.folded()
                onExpandedChange(true)
            },
        )
    }
}
