package com.hubara.operator.core.designsystem

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.ExperimentalMaterial3ExpressiveApi
import androidx.compose.material3.MaterialExpressiveTheme
import androidx.compose.material3.MotionScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf

val LocalOperatorColors = staticCompositionLocalOf { LightOperator }

/**
 * Material 3 Expressive con la marca del dashboard: paleta propia, Google Sans Flex, la escala de esquinas de
 * Expressive y el movimiento expresivo (resortes) en todos los componentes de material3.
 */
@OptIn(ExperimentalMaterial3ExpressiveApi::class)
@Composable
fun OperatorTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    CompositionLocalProvider(LocalOperatorColors provides if (darkTheme) DarkOperator else LightOperator) {
        MaterialExpressiveTheme(
            colorScheme = if (darkTheme) DarkScheme else LightScheme,
            motionScheme = MotionScheme.expressive(),
            shapes = OperatorShapes,
            typography = OperatorTypography,
            content = content,
        )
    }
}

object OperatorTheme {
    val colors: OperatorColors
        @Composable get() = LocalOperatorColors.current
}
