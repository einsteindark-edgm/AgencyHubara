@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.feature.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.StatusPill
import com.hubara.operator.core.model.CardButton
import com.hubara.operator.core.model.CardIcon
import com.hubara.operator.core.model.CardImage
import com.hubara.operator.core.model.CardItem
import com.hubara.operator.core.model.CardLink
import com.hubara.operator.core.model.MessageCard
import com.hubara.operator.core.model.WaFormat

/*
 * Un envío del bot como lo vio el cliente (caso 2026-10-13: el saludo iba dentro del texto del catálogo y la app solo
 * decía, en gris, «El bot envió el catálogo con 2 productos»). Reglas de diseño (Material 3 + mensajería):
 *  - Se lee en dos segundos qué fue: ícono + nombre arriba («Catálogo», «Resumen para confirmar»). Lo decide el
 *    servidor, así un envío nuevo se ve bien sin publicar otra versión de la app.
 *  - Lo que leyó el cliente va ENTERO y con el formato de WhatsApp (`*negrita*`): reconocer, no recordar.
 *  - Lo que traía, con la anatomía de una lista de Material: título a la izquierda, dato (precio, cuántos) a la
 *    derecha, filetes entre filas. Más de [ITEMS_FOLDED] filas se pliegan («Ver los 30»): poca carga a primera vista.
 *  - Los botones del cliente se ven como en su WhatsApp, pero NO se pueden tocar: no son acciones del operador
 *    (sin efecto al tocarlos ni rol de botón). El que tocó lleva «Tocó» en verde: el estado se ve, no se deduce.
 *  - El botón que abre la lista o el formulario va apagado, con su ícono; el enlace sí se abre (48 dp de toque).
 *  - Colores de la burbuja del autor y tonos de esos mismos colores: nada de colores nuevos (contraste 4,5:1).
 */

/** Filas que se ven antes de «Ver los N». */
private const val ITEMS_FOLDED = 6

private val CardPadding = 12.dp

@Composable
internal fun CardIcon.vector(): ImageVector = when (this) {
    CardIcon.CATALOG -> OperatorIcons.Storefront
    CardIcon.LIST -> OperatorIcons.named("apps") ?: OperatorIcons.Inventory
    CardIcon.BUTTONS -> OperatorIcons.named("check_circle") ?: OperatorIcons.Check
    CardIcon.LINK -> OperatorIcons.named("link") ?: OperatorIcons.Chat
    CardIcon.PHOTO -> OperatorIcons.Image
    CardIcon.FORM -> OperatorIcons.Shipping
    CardIcon.ORDER -> OperatorIcons.named("receipt") ?: OperatorIcons.Orders
    CardIcon.CONTACT -> OperatorIcons.Person
    CardIcon.MESSAGE -> OperatorIcons.Chat
}

/** El texto de WhatsApp con su formato: el cliente vio «Hubara» en negrita, no «*Hubara*». */
internal fun waText(text: String): AnnotatedString = buildAnnotatedString {
    WaFormat.spans(text).forEach { span ->
        withStyle(
            SpanStyle(
                fontWeight = if (span.bold) FontWeight.Bold else null,
                fontStyle = if (span.italic) FontStyle.Italic else null,
                textDecoration = if (span.strike) TextDecoration.LineThrough else null,
            ),
        ) { append(span.text) }
    }
}

/**
 * Las fotos y el enlace que se pueden abrir desde el teléfono: `https://` tal cual (el CDN de los productos) o una
 * ruta de nuestro backend resuelta por [mediaUrl] (que lleva el token); lo demás no se muestra.
 */
fun MessageCard.reachable(mediaUrl: (String?) -> String?): MessageCard = copy(
    images = images.mapNotNull { image ->
        val url = if (image.url.startsWith("https://")) image.url else mediaUrl(image.url)
        url?.let { image.copy(url = it) }
    },
    link = link?.takeIf { it.url.startsWith("https://") },
)

/** El cuerpo de la tarjeta, dentro de la burbuja del autor ([fg] es el color del texto sobre ella). */
@Composable
internal fun CardContent(
    card: MessageCard,
    fg: Color,
    time: String?,
    modifier: Modifier = Modifier,
    /** El doble check de WhatsApp, junto a la hora (2026-10-15). */
    check: (@Composable () -> Unit)? = null,
) {
    Column(modifier.widthIn(min = 240.dp, max = 320.dp)) {
        CardHeader(card, fg)
        card.images.forEach { CardPhoto(it, fg) }
        card.text?.let {
            Text(
                waText(it), style = MaterialTheme.typography.bodyLarge, color = fg,
                modifier = Modifier.padding(horizontal = CardPadding).padding(top = Spacing.sm),
            )
        }
        if (card.items.isNotEmpty()) CardItems(card.items, fg)
        if (card.buttons.isNotEmpty()) Column(Modifier.padding(top = Spacing.sm)) { card.buttons.forEach { CardButtonRow(it, fg) } }
        card.action?.let { CardAction(it, fg) }
        card.link?.let { CardLinkRow(it, fg) }
        if (time != null || check != null) {
            Row(
                Modifier.align(Alignment.End).padding(horizontal = CardPadding, vertical = Spacing.xs),
                horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically,
            ) {
                time?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = fg.copy(alpha = 0.7f)) }
                check?.invoke()
            }
        }
    }
}

@Composable
private fun CardHeader(card: MessageCard, fg: Color) {
    Row(
        Modifier.padding(start = CardPadding, end = CardPadding, top = CardPadding),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
    ) {
        IconTile(
            card.icon.vector(), container = fg.copy(alpha = 0.14f), content = fg,
            size = 28.dp, shape = MaterialTheme.shapes.small,
        )
        Text(
            card.title?.let { "${card.label} · $it" } ?: card.label,
            style = MaterialTheme.typography.labelLargeEmphasized, color = fg,
        )
    }
}

@Composable
private fun CardPhoto(image: CardImage, fg: Color) {
    Column(Modifier.padding(start = CardPadding, end = CardPadding, top = Spacing.sm)) {
        // Mientras carga, o si no carga (sin red, enlace vencido), se ve el ícono de foto sobre su tono: un recuadro
        // vacío parecía roto (lo vio Artemis en S30). La foto, cuando llega, lo tapa.
        Box(
            Modifier.fillMaxWidth().aspectRatio(4f / 3f).clip(MaterialTheme.shapes.large).background(fg.copy(alpha = 0.10f)),
            contentAlignment = Alignment.Center,
        ) {
            Icon(OperatorIcons.Image, contentDescription = null, tint = fg.copy(alpha = 0.45f), modifier = Modifier.size(40.dp))
            AsyncImage(
                model = image.url,
                contentDescription = image.caption?.let { "Foto enviada: $it" } ?: "Foto enviada",
                contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize(),
            )
        }
        image.caption?.let {
            Text(
                waText(it), style = MaterialTheme.typography.bodyMedium, color = fg,
                modifier = Modifier.padding(top = Spacing.xs),
            )
        }
    }
}

@Composable
private fun CardItems(items: List<CardItem>, fg: Color) {
    var open by rememberSaveable(items) { mutableStateOf(false) }
    val shown = if (open || items.size <= ITEMS_FOLDED) items else items.take(ITEMS_FOLDED)
    Column(
        Modifier.padding(start = CardPadding, end = CardPadding, top = Spacing.sm).fillMaxWidth()
            .clip(MaterialTheme.shapes.medium).background(fg.copy(alpha = 0.08f)),
    ) {
        shown.forEachIndexed { i, item ->
            if (i > 0) HorizontalDivider(color = fg.copy(alpha = 0.16f))
            Row(
                Modifier.fillMaxWidth().heightIn(min = 40.dp).padding(horizontal = Spacing.md, vertical = Spacing.sm),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
            ) {
                Text(item.title, style = MaterialTheme.typography.bodyMediumEmphasized, color = fg, modifier = Modifier.weight(1f))
                item.detail?.let { Text(it, style = MaterialTheme.typography.labelLarge, color = fg.copy(alpha = 0.85f)) }
            }
        }
        if (items.size > ITEMS_FOLDED) {
            HorizontalDivider(color = fg.copy(alpha = 0.16f))
            Text(
                if (open) "Ver menos" else "Ver los ${items.size}",
                style = MaterialTheme.typography.labelLargeEmphasized, color = fg,
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    .clickable(role = Role.Button) { open = !open }
                    .padding(horizontal = Spacing.md, vertical = 14.dp),
            )
        }
    }
}

@Composable
private fun CardButtonRow(button: CardButton, fg: Color) {
    HorizontalDivider(color = fg.copy(alpha = 0.16f))
    Row(
        Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(horizontal = CardPadding)
            .then(
                if (button.touched) {
                    Modifier.semantics { contentDescription = "${button.title}, el cliente tocó este botón" }
                } else {
                    Modifier
                },
            ),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.sm, Alignment.CenterHorizontally),
    ) {
        Text(button.title, style = MaterialTheme.typography.labelLargeEmphasized, color = fg)
        if (button.touched) {
            StatusPill(
                "Tocó", container = OperatorTheme.colors.successContainer, content = OperatorTheme.colors.onSuccessContainer,
                icon = OperatorIcons.Check,
            )
        }
    }
}

@Composable
private fun CardAction(label: String, fg: Color) {
    HorizontalDivider(color = fg.copy(alpha = 0.16f), modifier = Modifier.padding(top = Spacing.sm))
    Row(
        Modifier.fillMaxWidth().heightIn(min = 44.dp).padding(horizontal = CardPadding),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.xs, Alignment.CenterHorizontally),
    ) {
        Icon(OperatorIcons.named("chevron_right") ?: OperatorIcons.Check, contentDescription = null, tint = fg.copy(alpha = 0.75f), modifier = Modifier.size(18.dp))
        Text(label, style = MaterialTheme.typography.labelLarge, color = fg.copy(alpha = 0.85f))
    }
}

@Composable
private fun CardLinkRow(link: CardLink, fg: Color) {
    val uri = LocalUriHandler.current
    HorizontalDivider(color = fg.copy(alpha = 0.16f), modifier = Modifier.padding(top = Spacing.sm))
    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp)
            .clickable(role = Role.Button, onClickLabel = "Abrir el enlace") { uri.openUri(link.url) }
            .padding(horizontal = CardPadding),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.xs, Alignment.CenterHorizontally),
    ) {
        Icon(OperatorIcons.named("link") ?: OperatorIcons.Chat, contentDescription = null, tint = fg, modifier = Modifier.size(18.dp))
        Text(link.label, style = MaterialTheme.typography.labelLargeEmphasized, color = fg)
    }
}

/** El cliente tocó un botón: no lo escribió. Se resume en una píldora, no en una burbuja de texto. */
@Composable
internal fun TapChip(title: String, time: String?, modifier: Modifier = Modifier) {
    Row(
        modifier.padding(top = Spacing.sm),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.sm),
    ) {
        Row(
            Modifier.clip(CircleShape).background(OperatorTheme.colors.successContainer)
                .heightIn(min = 32.dp).padding(horizontal = Spacing.md, vertical = Spacing.xs),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(Spacing.xs),
        ) {
            Icon(OperatorIcons.Check, contentDescription = null, tint = OperatorTheme.colors.onSuccessContainer, modifier = Modifier.size(16.dp))
            Text("Tocó «$title»", style = MaterialTheme.typography.labelLargeEmphasized, color = OperatorTheme.colors.onSuccessContainer)
        }
        time?.let { Text(it, style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
    }
}

@Preview(widthDp = 380)
@Composable
private fun CardContentPreview() {
    OperatorTheme {
        Box(Modifier.background(OperatorTheme.colors.bubbleBot)) {
            CardContent(
                MessageCard(
                    component = "order_confirmation", label = "Resumen para confirmar", icon = CardIcon.ORDER,
                    text = "*Resumen de tu pedido*\n• 2× Velón Koala — \$35.000\nTotal: \$82.000 COP",
                    buttons = listOf(CardButton("✅ Confirmar", touched = true), CardButton("✏️ Modificar"), CardButton("❌ Cancelar")),
                ),
                fg = OperatorTheme.colors.onBubbleBot, time = "3:41 p. m.",
            )
        }
    }
}
