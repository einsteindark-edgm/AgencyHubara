package com.hubara.operator.core.designsystem

import androidx.compose.material3.Typography
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

/**
 * La escala tipográfica de Material 3 con Google Sans Flex, y los estilos «enfatizados» de Material 3 Expressive
 * (`MaterialTheme.typography.titleMediumEmphasized`…): mismo tamaño, más peso, para lo que el operador tiene que ver
 * primero (títulos de pantalla, quien escribió sin leer, montos).
 */
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
    displayLargeEmphasized = base.displayLarge.flex().copy(fontWeight = FontWeight.SemiBold),
    displayMediumEmphasized = base.displayMedium.flex().copy(fontWeight = FontWeight.SemiBold),
    displaySmallEmphasized = base.displaySmall.flex().copy(fontWeight = FontWeight.SemiBold),
    headlineLargeEmphasized = base.headlineLarge.flex().copy(fontWeight = FontWeight.SemiBold),
    headlineMediumEmphasized = base.headlineMedium.flex().copy(fontWeight = FontWeight.SemiBold),
    headlineSmallEmphasized = base.headlineSmall.flex().copy(fontWeight = FontWeight.SemiBold),
    titleLargeEmphasized = base.titleLarge.flex().copy(fontWeight = FontWeight.SemiBold, fontSize = 24.sp, lineHeight = 30.sp),
    titleMediumEmphasized = base.titleMedium.flex().copy(fontWeight = FontWeight.SemiBold),
    titleSmallEmphasized = base.titleSmall.flex().copy(fontWeight = FontWeight.SemiBold),
    bodyLargeEmphasized = base.bodyLarge.flex().copy(fontWeight = FontWeight.Medium),
    bodyMediumEmphasized = base.bodyMedium.flex().copy(fontWeight = FontWeight.Medium),
    bodySmallEmphasized = base.bodySmall.flex().copy(fontWeight = FontWeight.Medium),
    labelLargeEmphasized = base.labelLarge.flex().copy(fontWeight = FontWeight.Bold),
    labelMediumEmphasized = base.labelMedium.flex().copy(fontWeight = FontWeight.Bold),
    labelSmallEmphasized = base.labelSmall.flex().copy(fontWeight = FontWeight.Bold, letterSpacing = 0.6.sp),
)
