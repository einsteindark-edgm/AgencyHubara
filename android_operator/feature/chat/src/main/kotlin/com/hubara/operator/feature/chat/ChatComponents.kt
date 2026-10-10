@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.input.TextFieldLineLimits
import androidx.compose.foundation.text.input.TextFieldState
import androidx.compose.material3.Button
import androidx.compose.material3.FilledIconButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.IconButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.repo.OutboxState
import com.hubara.operator.core.data.repo.PendingAction
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.WorkingIndicator
import com.hubara.operator.core.designsystem.SuggestionBubble
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.DeliveryState
import com.hubara.operator.core.model.Message
import com.hubara.operator.core.model.Prominence
import com.hubara.operator.core.model.ShippingForm
import com.hubara.operator.core.model.Suggestion
import com.hubara.operator.core.ui.clockLabel
import com.hubara.operator.core.ui.writtenLabel
import java.time.ZoneId
import kotlinx.collections.immutable.ImmutableList
import kotlinx.coroutines.delay
import androidx.compose.material3.ButtonDefaults

private val BubbleRound = 20.dp
private val BubbleJoin = 6.dp

/**
 * Forma de la burbuja según su lugar en el grupo: redonda por fuera y casi recta donde se pega con la del mismo
 * autor (del lado de quien escribe), como en las apps de mensajería.
 */
private fun bubbleShape(position: BubblePosition, mine: Boolean): RoundedCornerShape {
    val top = if (position == BubblePosition.MIDDLE || position == BubblePosition.LAST) BubbleJoin else BubbleRound
    val bottom = if (position == BubblePosition.MIDDLE || position == BubblePosition.FIRST) BubbleJoin else BubbleRound
    return if (mine) {
        RoundedCornerShape(topStart = BubbleRound, topEnd = top, bottomEnd = bottom, bottomStart = BubbleRound)
    } else {
        RoundedCornerShape(topStart = top, topEnd = BubbleRound, bottomEnd = BubbleRound, bottomStart = bottom)
    }
}

/** Aire arriba de cada burbuja: poco dentro de un grupo, más entre grupos. */
fun bubbleTopGap(position: BubblePosition): Dp =
    if (position == BubblePosition.FIRST || position == BubblePosition.SINGLE) Spacing.md else Spacing.xxs

/** Separador de día: una píldora centrada. */
@Composable
fun DayHeader(label: String, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().padding(top = Spacing.lg, bottom = Spacing.xs), contentAlignment = Alignment.Center) {
        Surface(shape = CircleShape, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
            Text(
                label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = Spacing.md, vertical = Spacing.xs),
            )
        }
    }
}

@Composable
fun MessageBubble(
    message: Message,
    modifier: Modifier = Modifier,
    position: BubblePosition = BubblePosition.SINGLE,
    zone: ZoneId = remember { ZoneId.systemDefault() },
    nowMs: Long = System.currentTimeMillis(),
) {
    if (message.author == Author.SYSTEM) {
        SystemNote(message.text.orEmpty(), modifier)
        return
    }
    val mine = message.author != Author.CUSTOMER
    val colors = OperatorTheme.colors
    val (bg, fg) = when (message.author) {
        Author.HUMAN -> colors.bubbleOperator to colors.onBubbleOperator
        Author.BOT -> colors.bubbleBot to colors.onBubbleBot
        else -> colors.bubbleCustomer to colors.onBubbleCustomer
    }
    val groupStart = position == BubblePosition.FIRST || position == BubblePosition.SINGLE
    val groupEnd = position == BubblePosition.LAST || position == BubblePosition.SINGLE
    val form = remember(message.text) { ShippingForm.parse(message.text) }
    Column(
        modifier.fillMaxWidth().padding(top = bubbleTopGap(position)),
        horizontalAlignment = if (mine) Alignment.End else Alignment.Start,
    ) {
        // Quién escribió, una vez por grupo: el bot y el operador se ven distintos aunque estén del mismo lado.
        if (mine && groupStart) {
            Row(Modifier.padding(horizontal = Spacing.xs, vertical = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                val bot = message.author == Author.BOT
                Icon(
                    if (bot) OperatorIcons.Bot else OperatorIcons.Person, contentDescription = null, modifier = Modifier.size(14.dp),
                    tint = if (bot) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.primary,
                )
                Text(
                    if (bot) "Bot" else "Operador", style = MaterialTheme.typography.labelSmallEmphasized,
                    color = if (bot) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(start = 4.dp),
                )
            }
        }
        Column(
            Modifier.widthIn(max = 300.dp).clip(bubbleShape(position, mine)).background(bg)
                .padding(horizontal = if (message.imageUrl != null) 4.dp else 14.dp, vertical = if (message.imageUrl != null) 4.dp else 9.dp),
        ) {
            message.imageUrl?.let { url ->
                AsyncImage(
                    model = url,
                    contentDescription = if (mine) "Foto enviada" else "Foto que envió el cliente",
                    contentScale = ContentScale.Crop,
                    modifier = Modifier.size(220.dp).clip(RoundedCornerShape(16.dp)),
                )
            }
            val textPad = if (message.imageUrl != null) Modifier.padding(horizontal = 10.dp, vertical = 6.dp) else Modifier
            if (form != null) {
                ShippingFormCard(form, fg, textPad)
            } else {
                message.text?.takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodyLarge, color = fg, modifier = textPad) }
            }
            if (message.imageUrl == null && message.text.isNullOrBlank()) Text("…", style = MaterialTheme.typography.bodyLarge, color = fg)
            // Meta lo entregó tarde: la llegada sola lo hacía parecer una respuesta a lo último que se le mandó
            // (caso 2026-10-09). Va en toda burbuja tardía, no solo al final del grupo.
            val sentAtMs = message.sentAtMs
            if (groupEnd || message.state != DeliveryState.SENT || sentAtMs != null) {
                val arrived = message.timestampMs?.let { clockLabel(it, zone) }
                val meta = when (message.state) {
                    DeliveryState.PENDING -> "enviando…"
                    DeliveryState.FAILED -> "no se envió"
                    DeliveryState.SENT -> if (sentAtMs != null) {
                        writtenLabel(sentAtMs, nowMs, zone) + (arrived?.let { " · llegó $it" } ?: "")
                    } else {
                        arrived
                    }
                }
                meta?.let {
                    Text(
                        it, style = MaterialTheme.typography.labelSmall,
                        color = if (message.state == DeliveryState.FAILED) MaterialTheme.colorScheme.error else fg.copy(alpha = 0.7f),
                        modifier = Modifier.align(Alignment.End).padding(top = 2.dp).then(textPad),
                    )
                }
            }
        }
        if (message.arrivedAfterWindow) SystemNote(WINDOW_CLOSED_NOTE)
    }
}

/** Nota bajo un mensaje que llegó con la ventana de 24 h cerrada (el bot no pudo contestarle). */
internal const val WINDOW_CLOSED_NOTE =
    "El bot no respondió: este mensaje llegó con la ventana de 24 h cerrada. Solo un mensaje nuevo del cliente la abre."

/** Píldora centrada de sistema: eventos del historial y avisos sobre un mensaje. */
@Composable
private fun SystemNote(text: String, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().padding(top = Spacing.sm), contentAlignment = Alignment.Center) {
        Text(
            text, style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant, textAlign = TextAlign.Center,
            modifier = Modifier.widthIn(max = 320.dp).clip(RoundedCornerShape(12.dp))
                .background(MaterialTheme.colorScheme.surfaceContainerLow).padding(horizontal = Spacing.md, vertical = 6.dp),
        )
    }
}

/** El formulario de envío como ficha (igual que el dashboard web), no como `clave=valor`. */
@Composable
private fun ShippingFormCard(form: ShippingForm, fg: Color, modifier: Modifier = Modifier) {
    Column(modifier, verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(OperatorIcons.Shipping, contentDescription = null, tint = fg, modifier = Modifier.size(18.dp))
            Text("Datos de envío", style = MaterialTheme.typography.labelLargeEmphasized, color = fg, modifier = Modifier.padding(start = 6.dp))
        }
        listOf(
            "Recibe" to form.receiverName,
            "Teléfono" to form.phone,
            "Ciudad" to form.city,
            "Barrio" to form.neighborhood,
            "Dirección" to form.address,
            "Pago" to form.paymentMethod,
        ).forEach { (label, value) ->
            Column {
                Text(label, style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.7f))
                Text(value ?: "—", style = MaterialTheme.typography.bodyMedium, color = fg)
            }
        }
    }
}

/** Las burbujas del servidor. La principal va rellena. «Más» abre todas las acciones. */
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
        contentPadding = PaddingValues(horizontal = Spacing.md, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
    ) {
        items(suggestions, key = { it.id }, contentType = { it.prominence }) { s ->
            SuggestionBubble(
                label = s.label,
                primary = s.prominence == Prominence.PRIMARY,
                onClick = { onSend(s) },
                onLongClick = if (s.editable) ({ onEdit(s) }) else null,
                icon = if (s.prominence == Prominence.PRIMARY) OperatorIcons.Bolt else null,
                modifier = Modifier.animateItem(),
            )
        }
        item(key = "more") { SuggestionBubble(label = "Más", primary = false, onClick = onMore, icon = OperatorIcons.Add) }
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
    Column(Modifier.fillMaxWidth().padding(horizontal = Spacing.md, vertical = Spacing.xs), verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
        pending.forEach { p ->
            // Como un snackbar: superficie inversa, se despega del chat sin tapar el composer.
            Surface(
                shape = MaterialTheme.shapes.largeIncreased,
                color = MaterialTheme.colorScheme.inverseSurface,
                contentColor = MaterialTheme.colorScheme.inverseOnSurface,
                shadowElevation = 2.dp,
                modifier = Modifier.fillMaxWidth(),
            ) {
                Row(Modifier.padding(start = Spacing.lg, end = Spacing.xs, top = 2.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically) {
                    val action = MaterialTheme.colorScheme.inversePrimary
                    when (p.state) {
                        OutboxState.PENDING_UNDO -> {
                            // El lector de pantalla anuncia UNA vez qué sale; la cuenta regresiva solo se ve
                            // (si fuera semántica, TalkBack la repetiría cada segundo y la pantalla nunca queda quieta).
                            Text("Enviando «${p.label}»", Modifier.weight(1f).semantics { liveRegion = LiveRegionMode.Polite },
                                style = MaterialTheme.typography.bodyMedium)
                            UndoButton(p, action, onUndo)
                        }
                        OutboxState.FAILED -> {
                            Text(p.error ?: "No se envió «${p.label}».", Modifier.weight(1f).padding(vertical = Spacing.sm),
                                style = MaterialTheme.typography.bodySmall)
                            TextButton(onClick = { onRetry(p.id) }) { Text("Reintentar", color = action) }
                            TextButton(onClick = { onDismiss(p.id) }) { Text("Descartar", color = action) }
                        }
                        else -> Text("Enviando «${p.label}»…", Modifier.weight(1f).padding(vertical = 14.dp), style = MaterialTheme.typography.bodyMedium)
                    }
                }
            }
        }
    }
}

/** El botón con la cuenta regresiva: el reloj de 250 ms recompone solo esto, no toda la barra. */
@Composable
private fun UndoButton(p: PendingAction, color: Color, onUndo: (String) -> Unit) {
    val now = rememberNow()
    val left = ((p.createdMs + OutboxRepository.UNDO_WINDOW_MS - now) / 1000 + 1).coerceIn(1, 5)
    TextButton(onClick = { onUndo(p.id) }) {
        Text("Deshacer", color = color)
        Text(" ($left)", Modifier.clearAndSetSemantics {}, color = color)
    }
}

/** Con el bot en control: la etapa que calculó y el botón para tomar la conversación. */
@Composable
fun BotReadingPanel(stage: String?, busy: Boolean, onIntervene: () -> Unit) {
    Surface(
        shape = MaterialTheme.shapes.largeIncreased,
        color = MaterialTheme.colorScheme.tertiaryContainer,
        contentColor = MaterialTheme.colorScheme.onTertiaryContainer,
        modifier = Modifier.fillMaxWidth().padding(horizontal = Spacing.md, vertical = Spacing.xs),
    ) {
        Row(Modifier.padding(Spacing.md), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
            IconTile(OperatorIcons.Bot, MaterialTheme.colorScheme.tertiary, MaterialTheme.colorScheme.onTertiary, shape = CircleShape)
            Column(Modifier.weight(1f)) {
                Text("El bot atiende", style = MaterialTheme.typography.titleSmallEmphasized)
                Text("Etapa: ${stageLabel(stage)}", style = MaterialTheme.typography.bodySmall)
            }
            Button(onClick = onIntervene, shapes = ButtonDefaults.shapes(), enabled = !busy) {
                // Esperando al backend: muestra que trabaja (y deshabilitado no se toca dos veces).
                if (busy) {
                    WorkingIndicator()
                    Spacer(Modifier.width(ButtonDefaults.IconSpacing))
                }
                Text("Tomar la conversación")
            }
        }
    }
}

/** Ventana de 24 h cerrada: solo se puede escribir con una plantilla. */
@Composable
fun WindowClosedCard(onReactivate: () -> Unit) {
    Surface(
        shape = MaterialTheme.shapes.largeIncreased,
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.fillMaxWidth().padding(Spacing.md),
    ) {
        Column(Modifier.padding(Spacing.lg), verticalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Row(horizontalArrangement = Arrangement.spacedBy(Spacing.md), verticalAlignment = Alignment.CenterVertically) {
                IconTile(OperatorIcons.Schedule, OperatorTheme.colors.hoyContainer, OperatorTheme.colors.onHoyContainer)
                Text(
                    "La ventana de 24 h está cerrada: para escribirle hay que reactivar la conversación con una plantilla.",
                    style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f),
                )
            }
            Button(onClick = onReactivate, shapes = ButtonDefaults.shapes(), modifier = Modifier.fillMaxWidth()) { Text("Reactivar con plantilla") }
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

/** El campo para escribir: una píldora tonal y el botón redondo de enviar, que solo se enciende con texto. */
@Composable
fun Composer(state: TextFieldState, enabled: Boolean, onSend: () -> Unit, modifier: Modifier = Modifier) {
    val hasText by remember(state) { derivedStateOf { state.text.isNotBlank() } }
    Row(
        modifier.fillMaxWidth().padding(start = Spacing.md, end = Spacing.sm, top = Spacing.xs, bottom = Spacing.sm),
        verticalAlignment = Alignment.Bottom,
        horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
    ) {
        TextField(
            state = state,
            enabled = enabled,
            placeholder = { Text("Escribe un mensaje…") },
            lineLimits = TextFieldLineLimits.MultiLine(maxHeightInLines = 5),
            shape = RoundedCornerShape(28.dp),
            colors = TextFieldDefaults.colors(
                focusedIndicatorColor = Color.Transparent,
                unfocusedIndicatorColor = Color.Transparent,
                disabledIndicatorColor = Color.Transparent,
                focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
            ),
            modifier = Modifier.weight(1f),
        )
        FilledIconButton(
            onClick = onSend,
            enabled = enabled && hasText,
            modifier = Modifier.padding(bottom = 4.dp).size(48.dp),
            colors = IconButtonDefaults.filledIconButtonColors(),
        ) { Icon(OperatorIcons.Send, contentDescription = "Enviar") }
    }
}

/** Un error del chat (red, tomar, devolver), con la opción de cerrarlo. */
@Composable
fun ErrorNotice(message: String, onDismiss: () -> Unit, modifier: Modifier = Modifier) {
    Surface(
        shape = MaterialTheme.shapes.largeIncreased,
        color = MaterialTheme.colorScheme.errorContainer,
        contentColor = MaterialTheme.colorScheme.onErrorContainer,
        modifier = modifier.fillMaxWidth().padding(horizontal = Spacing.md, vertical = Spacing.xs),
    ) {
        Row(Modifier.padding(start = Spacing.md), verticalAlignment = Alignment.CenterVertically) {
            Icon(OperatorIcons.Error, contentDescription = null, modifier = Modifier.size(18.dp))
            Text(message, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f).padding(horizontal = Spacing.sm))
            IconButton(onClick = onDismiss) { Icon(OperatorIcons.Close, contentDescription = "Cerrar aviso") }
        }
    }
}
