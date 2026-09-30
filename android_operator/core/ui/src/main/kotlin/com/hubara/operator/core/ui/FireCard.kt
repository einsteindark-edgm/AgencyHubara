package com.hubara.operator.core.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
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
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.Severity
import kotlinx.coroutines.delay

@Composable
fun severityColor(severity: Severity): Color = when (severity) {
    Severity.GRAVE -> OperatorTheme.colors.grave
    Severity.HOY -> OperatorTheme.colors.hoy
    Severity.ESPERA -> OperatorTheme.colors.espera
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
 * Tarjeta de un incendio. Con [armDelayMs] > 0 no responde al toque apenas aparece: si el operador
 * estaba escribiendo, un dedo que iba a una tecla no la abre. Nunca toma el foco.
 */
@Composable
fun FireCard(
    fire: Fire,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    armDelayMs: Long = 0,
    onHide: (() -> Unit)? = null,
    compact: Boolean = false,
) {
    var armed by remember(fire.id) { mutableStateOf(armDelayMs <= 0) }
    LaunchedEffect(fire.id) {
        if (!armed) {
            delay(armDelayMs)
            armed = true
        }
    }
    val tone = severityColor(fire.severity)
    Surface(
        shape = RoundedCornerShape(12.dp),
        tonalElevation = 2.dp,
        shadowElevation = 3.dp,
        border = if (fire.severity == Severity.GRAVE) BorderStroke(1.dp, tone) else null,
        modifier = modifier
            .fillMaxWidth()
            .focusProperties { canFocus = false }
            .clickable(role = Role.Button, onClickLabel = "Abrir el caso") { if (armed) onClick() },
    ) {
        Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    "${subjectLabel(fire)} · ${severityLabel(fire.severity)}" + if (fire.gettingWorse) " · EMPEORA" else "",
                    style = MaterialTheme.typography.labelSmall,
                    color = tone,
                    modifier = Modifier.weight(1f),
                )
                if (onHide != null) {
                    TextButton(onClick = onHide, modifier = Modifier.focusProperties { canFocus = false }) { Text("Ocultar") }
                }
            }
            Text(fire.title, style = MaterialTheme.typography.titleSmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
            if (!compact && fire.subtitle.isNotBlank()) {
                Text(fire.subtitle, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}
