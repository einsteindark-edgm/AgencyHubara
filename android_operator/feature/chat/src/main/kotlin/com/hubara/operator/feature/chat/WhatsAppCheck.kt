package com.hubara.operator.feature.chat

import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.model.WhatsAppDelivery
import com.hubara.operator.core.model.WhatsAppStatus
import com.hubara.operator.core.ui.clockLabel
import java.time.ZoneId

/**
 * El doble check de WhatsApp junto a la hora de una burbuja saliente (caso 2026-10-15). Sale de lo que WhatsApp
 * informó por webhook: ✓ enviado, ✓✓ entregado, ✓✓ en otro color leído. Fallido y «sin confirmación» se LEEN (no
 * dependen del color); TalkBack dice el estado y su hora. [fg] es el color del texto sobre la burbuja.
 */
@Composable
internal fun WhatsAppCheck(delivery: WhatsAppDelivery, fg: Color, zone: ZoneId, modifier: Modifier = Modifier) {
    val at = delivery.atMs?.let { " " + clockLabel(it, zone) }.orEmpty()
    when (delivery.status) {
        WhatsAppStatus.UNCONFIRMED -> Text(
            "sin confirmación", style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.7f), modifier = modifier,
        )
        WhatsAppStatus.FAILED -> Surface(
            shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.errorContainer, modifier = modifier,
        ) {
            Text(
                "No llegó" + (delivery.errorCode?.let { " · $it" } ?: ""),
                style = MaterialTheme.typography.labelSmallEmphasized,
                color = MaterialTheme.colorScheme.onErrorContainer,
                modifier = Modifier.padding(horizontal = 6.dp, vertical = 1.dp),
            )
        }
        else -> {
            val label = when (delivery.status) {
                WhatsAppStatus.SENT -> "Enviado"
                WhatsAppStatus.DELIVERED -> "Entregado"
                else -> "Leído"
            }
            Icon(
                if (delivery.status == WhatsAppStatus.SENT) OperatorIcons.Check else OperatorIcons.DoneAll,
                contentDescription = "$label por WhatsApp$at",
                tint = if (delivery.status == WhatsAppStatus.READ) OperatorTheme.colors.readCheck else fg.copy(alpha = 0.7f),
                modifier = modifier.size(16.dp),
            )
        }
    }
}
