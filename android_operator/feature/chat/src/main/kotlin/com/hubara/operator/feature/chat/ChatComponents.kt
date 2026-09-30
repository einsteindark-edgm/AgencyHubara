package com.hubara.operator.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.foundation.text.input.TextFieldLineLimits
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import coil3.compose.AsyncImage
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.repo.OutboxState
import com.hubara.operator.core.data.repo.PendingAction
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.SuggestionBubble
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.DeliveryState
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.ShippingForm
import com.hubara.operator.core.model.Prominence
import com.hubara.operator.core.model.Suggestion
import kotlinx.collections.immutable.ImmutableList
import kotlinx.coroutines.delay

@Composable
fun MessageBubble(message: Message, modifier: Modifier = Modifier) {
    if (message.author == Author.SYSTEM) {
        Text(
            message.text.orEmpty(), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            textAlign = TextAlign.Center, modifier = modifier.fillMaxWidth().padding(vertical = 4.dp),
        )
        return
    }
    val mine = message.author != Author.CUSTOMER
    val colors = OperatorTheme.colors
    val bg = when (message.author) {
        Author.HUMAN -> colors.bubbleOperator
        Author.BOT -> colors.bubbleBot
        else -> colors.bubbleCustomer
    }
    Row(modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Column(horizontalAlignment = if (mine) Alignment.End else Alignment.Start) {
            Column(Modifier.widthIn(max = 300.dp).background(bg, RoundedCornerShape(14.dp)).padding(horizontal = 12.dp, vertical = 8.dp)) {
                message.imageUrl?.let { url ->
                    AsyncImage(
                        model = url,
                        contentDescription = if (mine) "Foto enviada" else "Foto que envió el cliente",
                        contentScale = ContentScale.Crop,
                        modifier = Modifier.size(200.dp).clip(RoundedCornerShape(10.dp)),
                    )
                }
                val form = ShippingForm.parse(message.text)
                if (form != null) {
                    ShippingFormCard(form)
                } else {
                    message.text?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyMedium) }
                }
                if (message.imageUrl == null && message.text.isNullOrBlank()) Text("…", style = MaterialTheme.typography.bodyMedium)
            }
            val meta = when {
                message.state == DeliveryState.PENDING -> "enviando…"
                message.state == DeliveryState.FAILED -> "no se envió"
                message.author == Author.BOT -> "bot"
                message.author == Author.HUMAN -> "humano"
                else -> null
            }
            meta?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
        }
    }
}

/** El formulario de envío como ficha (igual que el dashboard web), no como `clave=valor`. */
@Composable
private fun ShippingFormCard(form: ShippingForm) {
    Text("📋 Datos de envío", style = MaterialTheme.typography.labelLarge)
    listOf(
        "Recibe" to form.receiverName,
        "Teléfono" to form.phone,
        "Ciudad" to form.city,
        "Barrio" to form.neighborhood,
        "Dirección" to form.address,
        "Pago" to form.paymentMethod,
    ).forEach { (label, value) ->
        Text("$label: ${value ?: "—"}", style = MaterialTheme.typography.bodyMedium)
    }
}

/** Las burbujas del servidor. La principal va rellena. «+ Más» abre todas las acciones. */
@Composable
fun QuickActionStrip(
    suggestions: ImmutableList<Suggestion>,
    onSend: (Suggestion) -> Unit,
    onEdit: (Suggestion) -> Unit,
    onMore: () -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyRow(
        modifier = modifier.fillMaxWidth(),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(suggestions, key = { it.id }, contentType = { it.prominence }) { s ->
            SuggestionBubble(
                label = s.label,
                primary = s.prominence == Prominence.PRIMARY,
                onClick = { onSend(s) },
                onLongClick = if (s.editable) ({ onEdit(s) }) else null,
                modifier = Modifier.animateItem(),
            )
        }
        item(key = "more") { SuggestionBubble(label = "+ Más", primary = false, onClick = onMore) }
    }
}

@Composable
private fun rememberNow(): Long {
    val now by produceState(System.currentTimeMillis()) {
        while (true) {
            delay(250)
            value = System.currentTimeMillis()
        }
    }
    return now
}

/** Lo que está saliendo: 5 s para deshacer, «enviando» y los que fallaron con su motivo. */
@Composable
fun UndoBar(pending: ImmutableList<PendingAction>, onUndo: (String) -> Unit, onRetry: (String) -> Unit, onDismiss: (String) -> Unit) {
    if (pending.isEmpty()) return
    val now = rememberNow()
    Column(Modifier.fillMaxWidth().padding(horizontal = 12.dp)) {
        pending.forEach { p ->
            Surface(tonalElevation = 3.dp, shape = RoundedCornerShape(10.dp), modifier = Modifier.fillMaxWidth().padding(vertical = 2.dp)) {
                Row(Modifier.padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    when (p.state) {
                        OutboxState.PENDING_UNDO -> {
                            val left = ((p.createdMs + OutboxRepository.UNDO_WINDOW_MS - now) / 1000 + 1).coerceIn(1, 5)
                            // El lector de pantalla anuncia UNA vez qué sale; la cuenta regresiva solo se ve
                            // (si fuera semántica, TalkBack la repetiría cada segundo y la pantalla nunca queda quieta).
                            Text("Enviando «${p.label}»", Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite },
                                style = MaterialTheme.typography.bodyMedium)
                            TextButton(onClick = { onUndo(p.id) }) {
                                Text("Deshacer")
                                Text(" ($left)", Modifier.clearAndSetSemantics {})
                            }
                        }
                        OutboxState.FAILED -> {
                            Text(p.error ?: "No se envió «${p.label}».", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error)
                            TextButton(onClick = { onRetry(p.id) }) { Text("Reintentar") }
                            TextButton(onClick = { onDismiss(p.id) }) { Text("Descartar") }
                        }
                        else -> Text("Enviando «${p.label}»…", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }
    }
}

/** Con el bot en control: la etapa que calculó y el botón para tomar la conversación. */
@Composable
fun BotReadingPanel(stage: String?, busy: Boolean, onIntervene: () -> Unit) {
    Surface(tonalElevation = 1.dp, modifier = Modifier.fillMaxWidth()) {
        Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text("El bot atiende", style = MaterialTheme.typography.titleSmall)
                Text("Etapa: ${stageLabel(stage)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
            Button(onClick = onIntervene, enabled = !busy) { Text("Tomar la conversación") }
        }
    }
}

fun stageLabel(stage: String?): String = when (stage) {
    "etapa_descubrimiento" -> "descubriendo"
    "etapa_variantes" -> "eligiendo variantes"
    "etapa_datos_envio" -> "datos de envío"
    "etapa_cierre" -> "cierre"
    "etapa_postcierre" -> "después de la venta"
    else -> "sin datos"
}

@Composable
fun Composer(state: TextFieldState, enabled: Boolean, onSend: () -> Unit, modifier: Modifier = Modifier) {
    Row(modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        OutlinedTextField(
            state = state,
            enabled = enabled,
            placeholder = { Text("Escribe un mensaje…") },
            lineLimits = TextFieldLineLimits.MultiLine(maxHeightInLines = 5),
            modifier = Modifier.weight(1f),
        )
        TextButton(onClick = onSend, enabled = enabled && state.text.isNotBlank()) { Text("Enviar") }
    }
}
