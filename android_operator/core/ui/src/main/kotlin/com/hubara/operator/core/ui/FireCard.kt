package com.hubara.operator.core.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.focusProperties
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.Severity
import kotlinx.coroutines.delay

/** Color de texto de una gravedad (sobre la superficie). */
@Composable
fun severityColor(severity: Severity): Color = when (severity) {
    Severity.GRAVE -> OperatorTheme.colors.grave
    Severity.HOY -> OperatorTheme.colors.hoy
    Severity.ESPERA -> OperatorTheme.colors.espera
}

/** Contenedor y contenido tonales de una gravedad (el ícono de la tarjeta). */
@Composable
fun severityContainer(severity: Severity): Pair<Color, Color> = when (severity) {
    Severity.GRAVE -> OperatorTheme.colors.graveContainer to OperatorTheme.colors.onGraveContainer
    Severity.HOY -> OperatorTheme.colors.hoyContainer to OperatorTheme.colors.onHoyContainer
    Severity.ESPERA -> MaterialTheme.colorScheme.surfaceContainerHighest to MaterialTheme.colorScheme.onSurfaceVariant
}

fun severityLabel(severity: Severity): String = when (severity) {
    Severity.GRAVE -> "GRAVE"
    Severity.HOY -> "HOY"
    Severity.ESPERA -> "ESPERA"
}

fun subjectLabel(fire: Fire): String = when (fire.subject) {
    is FireSubject.Chat -> "CHAT"
    is FireSubject.Order -> "ORDEN"
}

/**
 * Tarjeta de un incendio: ícono del tema (chat u orden) en el tono de su gravedad, la línea «CHAT · GRAVE» y el
 * título. Con [armDelayMs] > 0 no responde al toque apenas aparece: si el operador estaba escribiendo, un dedo
 * que iba a una tecla no la abre. Nunca toma el foco. [floating] = encima de otra pantalla (radar): más alta y con
 * sombra para despegarse de lo que tapa.
 */
@Composable
fun FireCard(
    fire: Fire,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    armDelayMs: Long = 0,
    onHide: (() -> Unit)? = null,
    compact: Boolean = false,
    floating: Boolean = false,
) {
    var armed by remember(fire.id) { mutableStateOf(armDelayMs <= 0) }
    LaunchedEffect(fire.id) {
        if (!armed) {
            delay(armDelayMs)
            armed = true
        }
    }
    val (tileBg, tileFg) = severityContainer(fire.severity)
    Surface(
        shape = MaterialTheme.shapes.largeIncreased,
        color = if (floating) MaterialTheme.colorScheme.surfaceContainerHigh else MaterialTheme.colorScheme.surfaceContainerLow,
        shadowElevation = if (floating) 6.dp else 0.dp,
        modifier = modifier
            .fillMaxWidth()
            .focusProperties { canFocus = false }
            .clickable(role = Role.Button, onClickLabel = "Abrir el caso") { if (armed) onClick() },
    ) {
        Row(
            Modifier.padding(start = Spacing.md, end = Spacing.xs, top = Spacing.md, bottom = Spacing.md),
            horizontalArrangement = Arrangement.spacedBy(Spacing.md),
            verticalAlignment = if (compact) Alignment.CenterVertically else Alignment.Top,
        ) {
            IconTile(
                icon = if (fire.subject is FireSubject.Order) OperatorIcons.Orders else OperatorIcons.Chat,
                container = tileBg, content = tileFg, size = if (compact) 40.dp else 44.dp,
            )
            Column(Modifier.weight(1f).padding(end = Spacing.sm), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    "${subjectLabel(fire)} · ${severityLabel(fire.severity)}" + if (fire.gettingWorse) " · EMPEORA" else "",
                    style = MaterialTheme.typography.labelSmallEmphasized,
                    color = severityColor(fire.severity),
                )
                Text(fire.title, style = MaterialTheme.typography.titleSmallEmphasized, maxLines = if (compact) 1 else 2, overflow = TextOverflow.Ellipsis)
                if (!compact && fire.subtitle.isNotBlank()) {
                    Text(
                        fire.subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1, overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            if (onHide != null) {
                TextButton(onClick = onHide, modifier = Modifier.focusProperties { canFocus = false }) { Text("Ocultar") }
            }
        }
    }
}
