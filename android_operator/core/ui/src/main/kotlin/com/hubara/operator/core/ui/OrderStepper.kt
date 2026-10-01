package com.hubara.operator.core.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
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
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.model.OrderStage

private val STEPS = listOf(
    OrderStage.NEW to "Nuevo", OrderStage.PREPARING to "Prep.", OrderStage.READY to "Listo",
    OrderStage.SHIPPING to "Camino", OrderStage.DELIVERED to "Entreg.",
)

fun stageLabel(stage: OrderStage): String = when (stage) {
    OrderStage.NEW -> "Nuevo"
    OrderStage.PREPARING -> "Preparando"
    OrderStage.READY -> "Listo"
    OrderStage.SHIPPING -> "En camino"
    OrderStage.DELIVERED -> "Entregado"
    OrderStage.CANCELLED -> "Cancelado"
}

/**
 * Las cinco etapas del pedido en una línea: hechas con ✓, la actual resaltada, las que faltan en contorno, unidas
 * por un trazo. TalkBack lee solo «Etapa: …» (no las abreviaturas).
 */
@Composable
fun OrderStepper(current: OrderStage, modifier: Modifier = Modifier) {
    val reached = STEPS.indexOfFirst { it.first == current }
    val colors = MaterialTheme.colorScheme
    Row(modifier.fillMaxWidth().clearAndSetSemantics { contentDescription = "Etapa: ${stageLabel(current)}" }) {
        STEPS.forEachIndexed { i, (_, label) ->
            val done = i < reached
            val now = i == reached
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.fillMaxWidth().height(28.dp), contentAlignment = Alignment.Center) {
                    // Trazo hacia el paso anterior y hacia el siguiente.
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(1f).height(3.dp).background(if (i == 0) colors.surface.copy(alpha = 0f) else if (i <= reached) colors.primary else colors.outlineVariant))
                        Box(Modifier.weight(1f).height(3.dp).background(if (i == STEPS.lastIndex) colors.surface.copy(alpha = 0f) else if (i < reached) colors.primary else colors.outlineVariant))
                    }
                    Box(
                        Modifier.size(if (now) 28.dp else 22.dp).clip(CircleShape)
                            .background(if (done || now) colors.primary else colors.surface)
                            .border(2.dp, if (done || now) colors.primary else colors.outlineVariant, CircleShape),
                        contentAlignment = Alignment.Center,
                    ) {
                        if (done) Icon(OperatorIcons.Check, contentDescription = null, tint = colors.onPrimary, modifier = Modifier.size(14.dp))
                        if (now) Box(Modifier.size(10.dp).clip(CircleShape).background(colors.onPrimary))
                    }
                }
                Text(
                    label,
                    style = if (now) MaterialTheme.typography.labelMediumEmphasized else MaterialTheme.typography.labelMedium,
                    color = if (done || now) colors.onSurface else colors.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.padding(top = 4.dp),
                )
            }
        }
    }
}
