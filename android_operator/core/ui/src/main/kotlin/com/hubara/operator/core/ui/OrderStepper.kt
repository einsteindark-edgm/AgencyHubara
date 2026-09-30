package com.hubara.operator.core.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
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

@Composable
fun OrderStepper(current: OrderStage, modifier: Modifier = Modifier) {
    val reached = STEPS.indexOfFirst { it.first == current }
    Row(
        modifier.fillMaxWidth().semantics { contentDescription = "Etapa: ${stageLabel(current)}" },
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        STEPS.forEachIndexed { i, (_, label) ->
            val done = i <= reached
            Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.width(56.dp)) {
                Box(
                    Modifier.size(12.dp)
                        .background(if (done) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surface, CircleShape)
                        .border(1.dp, MaterialTheme.colorScheme.primary, CircleShape),
                )
                Box(Modifier.height(4.dp))
                Text(label, style = MaterialTheme.typography.labelSmall, color = if (done) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
