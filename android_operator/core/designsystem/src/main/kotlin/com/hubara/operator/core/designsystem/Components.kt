package com.hubara.operator.core.designsystem

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Fondo y texto de un tono de avatar. */
@Composable
private fun avatarColors(tone: Int): Pair<Color, Color> {
    val s = MaterialTheme.colorScheme
    return when (tone) {
        0 -> s.primaryContainer to s.onPrimaryContainer
        1 -> s.tertiaryContainer to s.onTertiaryContainer
        2 -> s.secondaryContainer to s.onSecondaryContainer
        else -> s.surfaceContainerHighest to s.onSurfaceVariant
    }
}

/**
 * Círculo con las iniciales del cliente (o un ícono de persona si solo hay número), con un tono estable por
 * cliente. Decorativo para TalkBack: el nombre ya se lee al lado.
 */
@Composable
fun Avatar(name: String?, key: String, modifier: Modifier = Modifier, size: Dp = 48.dp) {
    val (bg, fg) = avatarColors(avatarTone(key))
    Box(
        modifier.size(size).clip(CircleShape).background(bg).clearAndSetSemantics {},
        contentAlignment = Alignment.Center,
    ) {
        val text = initials(name)
        if (text != null) {
            Text(
                text, color = fg,
                style = OperatorTheme.emphasized.titleMedium.copy(fontSize = (size.value * 0.36f).sp),
            )
        } else {
            Icon(OperatorIcons.Person, contentDescription = null, tint = fg, modifier = Modifier.size(size * 0.5f))
        }
    }
}

/** Etiqueta corta de estado en una píldora tonal (ruta, etapa, pago…). */
@Composable
fun StatusPill(
    text: String,
    container: Color,
    content: Color,
    modifier: Modifier = Modifier,
    icon: ImageVector? = null,
) {
    Row(
        modifier.clip(CircleShape).background(container).heightIn(min = 24.dp).padding(horizontal = 10.dp, vertical = 3.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        icon?.let { Icon(it, contentDescription = null, tint = content, modifier = Modifier.size(14.dp)) }
        Text(text, color = content, style = OperatorTheme.emphasized.labelMedium, maxLines = 1)
    }
}

/** Ícono dentro de una forma tonal: encabeza tarjetas y filas (incendio, orden, aviso). */
@Composable
fun IconTile(
    icon: ImageVector,
    container: Color,
    content: Color,
    modifier: Modifier = Modifier,
    size: Dp = 40.dp,
    shape: Shape = MaterialTheme.shapes.medium,
) {
    Box(modifier.size(size).clip(shape).background(container), contentAlignment = Alignment.Center) {
        Icon(icon, contentDescription = null, tint = content, modifier = Modifier.size(size * 0.55f))
    }
}

/** Pantalla o lista vacía: un ícono grande en una forma tonal, un título y una línea de ayuda. */
@Composable
fun EmptyState(icon: ImageVector, title: String, body: String, modifier: Modifier = Modifier) {
    Column(
        modifier.fillMaxWidth().padding(horizontal = Spacing.xxl, vertical = 48.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(Spacing.md),
    ) {
        IconTile(
            icon, MaterialTheme.colorScheme.secondaryContainer, MaterialTheme.colorScheme.onSecondaryContainer,
            size = 72.dp, shape = ExpressiveShapes.extraLargeIncreased,
        )
        Text(title, style = OperatorTheme.emphasized.titleMedium, textAlign = TextAlign.Center)
        Text(body, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center)
    }
}

@Preview(showBackground = true)
@Composable
private fun ComponentsPreview() = OperatorTheme {
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Avatar("Laura Prueba", "wa_1")
            Avatar("Sofía", "wa_2")
            Avatar(null, "wa_3")
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            StatusPill("Bot", MaterialTheme.colorScheme.tertiaryContainer, MaterialTheme.colorScheme.onTertiaryContainer, icon = OperatorIcons.Bot)
            StatusPill("Grave", OperatorTheme.colors.graveContainer, OperatorTheme.colors.onGraveContainer)
        }
        EmptyState(OperatorIcons.Fire, "No hay incendios", "Todo va bien.")
    }
}
