package com.hubara.operator.core.designsystem

import androidx.compose.material3.Typography
import androidx.compose.runtime.Immutable
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp

// Google Sans Flex, la letra de Material 3 Expressive (OFL, ver FONT-LICENSE-OFL.txt). Va dentro de la app (148 KB,
// solo latín, eje de peso 1–1000) para que se vea igual sin Google Play Services y en las capturas del CI.
private fun flex(weight: FontWeight) =
    Font(R.font.google_sans_flex, weight, variationSettings = FontVariation.Settings(FontVariation.weight(weight.weight)))

val GoogleSansFlex = FontFamily(
    flex(FontWeight.Normal), flex(FontWeight.Medium), flex(FontWeight.SemiBold), flex(FontWeight.Bold), flex(FontWeight.ExtraBold),
)

private val base = Typography()

private fun TextStyle.flex() = copy(fontFamily = GoogleSansFlex)

/** La escala tipográfica de Material 3 (tamaños e interlineado del estándar) con Google Sans Flex. */
internal val OperatorTypography = Typography(
    displayLarge = base.displayLarge.flex(),
    displayMedium = base.displayMedium.flex(),
    displaySmall = base.displaySmall.flex(),
    headlineLarge = base.headlineLarge.flex(),
    headlineMedium = base.headlineMedium.flex(),
    headlineSmall = base.headlineSmall.flex(),
    titleLarge = base.titleLarge.flex(),
    titleMedium = base.titleMedium.flex(),
    titleSmall = base.titleSmall.flex(),
    bodyLarge = base.bodyLarge.flex(),
    bodyMedium = base.bodyMedium.flex(),
    bodySmall = base.bodySmall.flex(),
    labelLarge = base.labelLarge.flex(),
    labelMedium = base.labelMedium.flex(),
    labelSmall = base.labelSmall.flex(),
)

/**
 * Los estilos «enfatizados» de Material 3 Expressive: mismo tamaño, más peso, para lo que el operador tiene que ver
 * primero (títulos de pantalla, nombre de quien escribió sin leer, montos). En material3 1.4 son internos, por eso
 * viven aquí.
 */
@Immutable
data class EmphasizedTypography(
    val headlineMedium: TextStyle,
    val headlineSmall: TextStyle,
    val titleLarge: TextStyle,
    val titleMedium: TextStyle,
    val titleSmall: TextStyle,
    val bodyLarge: TextStyle,
    val bodyMedium: TextStyle,
    val labelLarge: TextStyle,
    val labelMedium: TextStyle,
    val labelSmall: TextStyle,
)

internal val OperatorEmphasized = with(OperatorTypography) {
    EmphasizedTypography(
        headlineMedium = headlineMedium.copy(fontWeight = FontWeight.SemiBold),
        headlineSmall = headlineSmall.copy(fontWeight = FontWeight.SemiBold),
        titleLarge = titleLarge.copy(fontWeight = FontWeight.SemiBold, fontSize = 24.sp, lineHeight = 30.sp),
        titleMedium = titleMedium.copy(fontWeight = FontWeight.SemiBold),
        titleSmall = titleSmall.copy(fontWeight = FontWeight.SemiBold),
        bodyLarge = bodyLarge.copy(fontWeight = FontWeight.Medium),
        bodyMedium = bodyMedium.copy(fontWeight = FontWeight.Medium),
        labelLarge = labelLarge.copy(fontWeight = FontWeight.Bold),
        labelMedium = labelMedium.copy(fontWeight = FontWeight.Bold),
        labelSmall = labelSmall.copy(fontWeight = FontWeight.Bold, letterSpacing = 0.6.sp),
    )
}
