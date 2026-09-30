package com.hubara.operator.core.designsystem

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color

// Misma familia de colores que el dashboard (frontend_dashboard/src/index.css): acento azul, rojo para
// lo grave, naranja para «hoy». Sin color dinámico: el operador reconoce los estados por color.

private val Blue = Color(0xFF0A84FF)
private val BlueLight = Color(0xFF0A64D6)
private val Red = Color(0xFFFF453A)
private val Orange = Color(0xFFFF9F0A)
private val Green = Color(0xFF30D158)

@Immutable
data class OperatorColors(
    val grave: Color,
    val hoy: Color,
    val espera: Color,
    val success: Color,
    val bubbleCustomer: Color,
    val bubbleOperator: Color,
    val bubbleBot: Color,
)

private val LightOperator = OperatorColors(
    grave = Color(0xFFD70015), hoy = Color(0xFFC93400), espera = Color(0xFF6E6E73), success = Color(0xFF248A3D),
    bubbleCustomer = Color(0xFFEFEFF4), bubbleOperator = Color(0xFFD6E8FF), bubbleBot = Color(0xFFE9E9EE),
)

private val DarkOperator = OperatorColors(
    grave = Red, hoy = Orange, espera = Color(0xFF98989D), success = Green,
    bubbleCustomer = Color(0xFF2A2A2D), bubbleOperator = Color(0xFF0A5CD1), bubbleBot = Color(0xFF3A3A3C),
)

val LocalOperatorColors = staticCompositionLocalOf { LightOperator }

@Composable
fun OperatorTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val scheme = if (darkTheme) {
        darkColorScheme(primary = Blue, secondary = Blue, error = Red, background = Color(0xFF1A1A1C), surface = Color(0xFF1F1F21))
    } else {
        lightColorScheme(primary = BlueLight, secondary = BlueLight, error = Color(0xFFD70015))
    }
    CompositionLocalProvider(LocalOperatorColors provides if (darkTheme) DarkOperator else LightOperator) {
        MaterialTheme(colorScheme = scheme, content = content)
    }
}

object OperatorTheme {
    val colors: OperatorColors
        @Composable get() = LocalOperatorColors.current
}
