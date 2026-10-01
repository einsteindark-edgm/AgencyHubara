package com.hubara.operator.core.designsystem

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf

val LocalOperatorColors = staticCompositionLocalOf { LightOperator }
val LocalEmphasizedTypography = staticCompositionLocalOf { OperatorEmphasized }

/**
 * Material 3 Expressive con la marca del dashboard: paleta propia, Google Sans Flex, esquinas de Expressive y el
 * resortes de [ExpressiveMotion] en las animaciones propias.
 */
@Composable
fun OperatorTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    CompositionLocalProvider(
        LocalOperatorColors provides if (darkTheme) DarkOperator else LightOperator,
        LocalEmphasizedTypography provides OperatorEmphasized,
    ) {
        MaterialTheme(
            colorScheme = if (darkTheme) DarkScheme else LightScheme,
            shapes = OperatorShapes,
            typography = OperatorTypography,
            content = content,
        )
    }
}

object OperatorTheme {
    val colors: OperatorColors
        @Composable get() = LocalOperatorColors.current

    /** Los estilos enfatizados de Expressive (más peso, mismo tamaño). */
    val emphasized: EmphasizedTypography
        @Composable get() = LocalEmphasizedTypography.current
}
