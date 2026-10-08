package com.hubara.operator.core.designsystem

import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color

// Paleta de marca de Material 3, generada con material-color-utilities desde el azul del dashboard
// (#0A84FF, frontend_dashboard/src/index.css): primario azul vivo, secundario azul apagado, terciario violeta
// (el morado del dashboard, que aquí marca al bot) y neutros con un toque de azul. Rojo, naranja y verde son
// colores propios armonizados con el azul, cada uno con su contenedor. Sin color dinámico: el operador
// reconoce los estados por color. ThemeContrastTest exige 4,5:1 a todo texto sobre su fondo.
//
// Para regenerarla (material-color-utilities 0.4.0, JS): DynamicScheme TONAL_SPOT, contraste 0, con paletas
// primaria = TonalPalette(hue 262.8, croma 64), secundaria = (262.8, 20), terciaria = (318.6, 40), neutra = (262.8, 5)
// y neutra variante = (262.8, 10). Rojo/naranja/verde: customColor(#0A84FF, {#FF453A|#FF9F0A|#30D158}, blend=true).

/**
 * Colores de estado que Material no tiene: grave (rojo), hoy (naranja), espera, éxito, y las burbujas del chat
 * por autor. Cada color de texto trae su contenedor y su «on».
 */
@Immutable
data class OperatorColors(
    val grave: Color,
    val hoy: Color,
    val espera: Color,
    val success: Color,
    val graveContainer: Color,
    val onGraveContainer: Color,
    val hoyContainer: Color,
    val onHoyContainer: Color,
    val successContainer: Color,
    val onSuccessContainer: Color,
    val bubbleCustomer: Color,
    val onBubbleCustomer: Color,
    val bubbleOperator: Color,
    val onBubbleOperator: Color,
    val bubbleBot: Color,
    val onBubbleBot: Color,
)

internal val LightScheme = lightColorScheme(
    primary = Color(0xFF005DB8),
    onPrimary = Color(0xFFFFFFFF),
    primaryContainer = Color(0xFFD6E3FF),
    onPrimaryContainer = Color(0xFF00468D),
    inversePrimary = Color(0xFFAAC7FF),
    secondary = Color(0xFF525F77),
    onSecondary = Color(0xFFFFFFFF),
    secondaryContainer = Color(0xFFD6E3FF),
    onSecondaryContainer = Color(0xFF3B475E),
    tertiary = Color(0xFF774E8B),
    onTertiary = Color(0xFFFFFFFF),
    tertiaryContainer = Color(0xFFF6D9FF),
    onTertiaryContainer = Color(0xFF5E3671),
    background = Color(0xFFF9F9FE),
    onBackground = Color(0xFF1A1C1F),
    surface = Color(0xFFF9F9FE),
    onSurface = Color(0xFF1A1C1F),
    surfaceVariant = Color(0xFFDFE2EF),
    onSurfaceVariant = Color(0xFF424751),
    surfaceTint = Color(0xFF005DB8),
    inverseSurface = Color(0xFF2F3034),
    inverseOnSurface = Color(0xFFF1F0F6),
    error = Color(0xFFBA1A1A),
    onError = Color(0xFFFFFFFF),
    errorContainer = Color(0xFFFFDAD6),
    onErrorContainer = Color(0xFF93000A),
    outline = Color(0xFF737782),
    outlineVariant = Color(0xFFC2C6D3),
    scrim = Color(0xFF000000),
    surfaceBright = Color(0xFFF9F9FE),
    surfaceContainer = Color(0xFFEEEDF3),
    surfaceContainerHigh = Color(0xFFE8E7ED),
    surfaceContainerHighest = Color(0xFFE2E2E7),
    surfaceContainerLow = Color(0xFFF4F3F8),
    surfaceContainerLowest = Color(0xFFFFFFFF),
    surfaceDim = Color(0xFFDAD9DF),
)

internal val DarkScheme = darkColorScheme(
    primary = Color(0xFFAAC7FF),
    onPrimary = Color(0xFF003064),
    primaryContainer = Color(0xFF00468D),
    onPrimaryContainer = Color(0xFFD6E3FF),
    inversePrimary = Color(0xFF005DB8),
    secondary = Color(0xFFBAC7E3),
    onSecondary = Color(0xFF243146),
    secondaryContainer = Color(0xFF3B475E),
    onSecondaryContainer = Color(0xFFD6E3FF),
    tertiary = Color(0xFFE6B5FA),
    onTertiary = Color(0xFF461F59),
    tertiaryContainer = Color(0xFF5E3671),
    onTertiaryContainer = Color(0xFFF6D9FF),
    background = Color(0xFF121317),
    onBackground = Color(0xFFE2E2E7),
    surface = Color(0xFF121317),
    onSurface = Color(0xFFE2E2E7),
    surfaceVariant = Color(0xFF424751),
    onSurfaceVariant = Color(0xFFC2C6D3),
    surfaceTint = Color(0xFFAAC7FF),
    inverseSurface = Color(0xFFE2E2E7),
    inverseOnSurface = Color(0xFF2F3034),
    error = Color(0xFFFFB4AB),
    onError = Color(0xFF690005),
    errorContainer = Color(0xFF93000A),
    onErrorContainer = Color(0xFFFFDAD6),
    outline = Color(0xFF8C919C),
    outlineVariant = Color(0xFF424751),
    scrim = Color(0xFF000000),
    surfaceBright = Color(0xFF38393D),
    surfaceContainer = Color(0xFF1E2024),
    surfaceContainerHigh = Color(0xFF282A2E),
    surfaceContainerHighest = Color(0xFF333539),
    surfaceContainerLow = Color(0xFF1A1C1F),
    surfaceContainerLowest = Color(0xFF0C0E12),
    surfaceDim = Color(0xFF121317),
)

internal val LightOperator = OperatorColors(
    grave = Color(0xFFBD0045), hoy = Color(0xFF954A04), espera = Color(0xFF424751), success = Color(0xFF006C47),
    graveContainer = Color(0xFFFFD9DD), onGraveContainer = Color(0xFF400012),
    hoyContainer = Color(0xFFFFDCC7), onHoyContainer = Color(0xFF311300),
    successContainer = Color(0xFF59FEB5), onSuccessContainer = Color(0xFF002112),
    bubbleCustomer = Color(0xFFE8E7ED), onBubbleCustomer = Color(0xFF1A1C1F),
    bubbleOperator = Color(0xFFD6E3FF), onBubbleOperator = Color(0xFF00468D),
    bubbleBot = Color(0xFFF6D9FF), onBubbleBot = Color(0xFF5E3671),
)

internal val DarkOperator = OperatorColors(
    grave = Color(0xFFFFB2BB), hoy = Color(0xFFFFB787), espera = Color(0xFFC2C6D3), success = Color(0xFF30E19B),
    graveContainer = Color(0xFF910033), onGraveContainer = Color(0xFFFFD9DD),
    hoyContainer = Color(0xFF723600), onHoyContainer = Color(0xFFFFDCC7),
    successContainer = Color(0xFF005234), onSuccessContainer = Color(0xFF59FEB5),
    bubbleCustomer = Color(0xFF282A2E), onBubbleCustomer = Color(0xFFE2E2E7),
    bubbleOperator = Color(0xFF00468D), onBubbleOperator = Color(0xFFD6E3FF),
    bubbleBot = Color(0xFF5E3671), onBubbleBot = Color(0xFFF6D9FF),
)

/**
 * La paleta de la marca para lo que no ve `MaterialTheme`: el widget de la pantalla de inicio (Glance) la usa en Android
 * 11, que no tiene colores dinámicos. Los mismos valores de la app.
 */
object OperatorPalette {
    val light: androidx.compose.material3.ColorScheme get() = LightScheme
    val dark: androidx.compose.material3.ColorScheme get() = DarkScheme
    val lightStatus: OperatorColors get() = LightOperator
    val darkStatus: OperatorColors get() = DarkOperator
}

