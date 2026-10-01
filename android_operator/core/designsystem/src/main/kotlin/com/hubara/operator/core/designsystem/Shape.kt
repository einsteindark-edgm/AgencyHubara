package com.hubara.operator.core.designsystem

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Shapes
import androidx.compose.runtime.Immutable
import androidx.compose.ui.unit.dp

/** Escala de esquinas de Material 3 Expressive: más redonda que la de M3 y con pasos intermedios. */
internal val OperatorShapes = Shapes(
    extraSmall = RoundedCornerShape(4.dp),
    small = RoundedCornerShape(8.dp),
    medium = RoundedCornerShape(12.dp),
    large = RoundedCornerShape(16.dp),
    extraLarge = RoundedCornerShape(28.dp),
)

/** Los pasos de Expressive que `Shapes` de material3 1.4 todavía no expone. */
@Immutable
object ExpressiveShapes {
    val largeIncreased = RoundedCornerShape(20.dp)
    val extraLargeIncreased = RoundedCornerShape(32.dp)
    val extraExtraLarge = RoundedCornerShape(48.dp)
}
