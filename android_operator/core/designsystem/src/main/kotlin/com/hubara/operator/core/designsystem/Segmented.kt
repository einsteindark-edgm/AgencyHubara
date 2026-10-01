package com.hubara.operator.core.designsystem

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.unit.dp

/** Espacio entre los renglones de una lista segmentada. */
val SegmentGap = 2.dp

private val Outer = 20.dp
private val Inner = 4.dp

/**
 * La lista segmentada de Material 3 Expressive: renglones sueltos en tarjetas tonales separadas por 2 dp, con las
 * puntas del grupo redondas y las uniones casi rectas.
 */
fun segmentedShape(index: Int, count: Int): Shape = when {
    count <= 1 -> RoundedCornerShape(Outer)
    index == 0 -> RoundedCornerShape(topStart = Outer, topEnd = Outer, bottomStart = Inner, bottomEnd = Inner)
    index == count - 1 -> RoundedCornerShape(topStart = Inner, topEnd = Inner, bottomStart = Outer, bottomEnd = Outer)
    else -> RoundedCornerShape(Inner)
}
