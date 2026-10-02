package com.hubara.operator.core.designsystem

import androidx.compose.material3.ColorScheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import com.google.common.truth.Truth.assertWithMessage
import org.junit.Test
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * La paleta se lee: todo texto sobre su fondo pasa WCAG AA (4,5:1) en claro y en oscuro, y los colores de
 * marca son azules (no el morado base de Material que queda en los roles que nadie definió).
 */
class ThemeContrastTest {

    private fun contrast(a: Color, b: Color): Double {
        val (hi, lo) = max(a.luminance(), b.luminance()) to min(a.luminance(), b.luminance())
        return (hi + 0.05) / (lo + 0.05)
    }

    /** Tono HSV en grados. */
    private fun hue(c: Color): Float {
        val mx = maxOf(c.red, c.green, c.blue)
        val mn = minOf(c.red, c.green, c.blue)
        val d = mx - mn
        if (d == 0f) return 0f
        val h = when (mx) {
            c.red -> ((c.green - c.blue) / d).mod(6f)
            c.green -> (c.blue - c.red) / d + 2f
            else -> (c.red - c.green) / d + 4f
        }
        return h * 60f
    }

    private fun textPairs(s: ColorScheme, o: OperatorColors) = listOf(
        "primary" to (s.primary to s.onPrimary),
        "primaryContainer" to (s.primaryContainer to s.onPrimaryContainer),
        "secondary" to (s.secondary to s.onSecondary),
        "secondaryContainer" to (s.secondaryContainer to s.onSecondaryContainer),
        "tertiary" to (s.tertiary to s.onTertiary),
        "tertiaryContainer" to (s.tertiaryContainer to s.onTertiaryContainer),
        "error" to (s.error to s.onError),
        "errorContainer" to (s.errorContainer to s.onErrorContainer),
        "surface" to (s.surface to s.onSurface),
        "surface/variant" to (s.surface to s.onSurfaceVariant),
        "surfaceContainerHigh/variant" to (s.surfaceContainerHigh to s.onSurfaceVariant),
        "inverseSurface" to (s.inverseSurface to s.inverseOnSurface),
        "grave sobre surface" to (s.surface to o.grave),
        "hoy sobre surface" to (s.surface to o.hoy),
        "success sobre surface" to (s.surface to o.success),
        "graveContainer" to (o.graveContainer to o.onGraveContainer),
        "hoyContainer" to (o.hoyContainer to o.onHoyContainer),
        "successContainer" to (o.successContainer to o.onSuccessContainer),
        "burbuja del cliente" to (o.bubbleCustomer to o.onBubbleCustomer),
        "burbuja del operador" to (o.bubbleOperator to o.onBubbleOperator),
        "burbuja del bot" to (o.bubbleBot to o.onBubbleBot),
    )

    @Test
    fun every_text_role_reads_on_its_background_in_light_and_dark() {
        for ((mode, scheme, colors) in listOf(Triple("claro", LightScheme, LightOperator), Triple("oscuro", DarkScheme, DarkOperator))) {
            for ((name, pair) in textPairs(scheme, colors)) {
                val ratio = contrast(pair.first, pair.second)
                assertWithMessage("$name en $mode: ${"%.2f".format(ratio)}:1").that(ratio).isAtLeast(4.5)
            }
            // Bordes y controles: 3:1 contra la superficie (WCAG 1.4.11).
            assertWithMessage("outline en $mode").that(contrast(scheme.surface, scheme.outline)).isAtLeast(3.0)
        }
    }

    @Test
    fun the_brand_roles_are_blue_not_the_material_baseline_purple() {
        val brandHue = hue(Color(0xFF0A84FF))
        for ((mode, s) in listOf("claro" to LightScheme, "oscuro" to DarkScheme)) {
            listOf(
                "primary" to s.primary, "primaryContainer" to s.primaryContainer,
                "secondary" to s.secondary, "secondaryContainer" to s.secondaryContainer,
                "surfaceTint" to s.surfaceTint,
            ).forEach { (name, c) ->
                assertWithMessage("$name en $mode (tono ${hue(c)}°)").that(abs(hue(c) - brandHue)).isLessThan(25f)
            }
        }
    }
}
