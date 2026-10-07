@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.feature.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.contentDescription
import com.hubara.operator.core.sdui.Catalog
import com.hubara.operator.core.ui.LocalNativeComponents
import com.hubara.operator.core.ui.NativeProps
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.ElevatedCard
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FilterChipDefaults
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.LinearWavyProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedCard
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.RectangleShape
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import coil3.compose.AsyncImage
import com.hubara.operator.core.designsystem.Avatar
import com.hubara.operator.core.designsystem.EmptyState
import com.hubara.operator.core.designsystem.IconTile
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.SegmentGap
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.designsystem.StatusPill
import com.hubara.operator.core.designsystem.segmentedShape
import com.hubara.operator.core.sdui.Action
import com.hubara.operator.core.sdui.Env
import com.hubara.operator.core.sdui.Node
import com.hubara.operator.core.sdui.Scope
import com.hubara.operator.core.sdui.asText
import com.hubara.operator.core.sdui.truthy
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Lo que todo componente necesita del ViewModel: el reloj, si hay una llamada en curso, los valores atados y los eventos. */
@Immutable
class ScreenHost(
    val env: Env,
    val busy: Boolean,
    val bound: (String) -> JsonElement?,
    val onAction: (Action, Scope) -> Unit,
    val onBind: (String, JsonElement, Boolean) -> Unit,
)

val LocalScreenHost = staticCompositionLocalOf {
    ScreenHost(Env.Default, busy = false, bound = { null }, onAction = { _, _ -> }, onBind = { _, _, _ -> })
}

// ── Filas de la lista perezosa ────────────────────────────────────────────────────────────────────

/**
 * Una fila de la lista de la pantalla. Cada componente de primer nivel es una fila; una `list` de primer nivel se
 * reparte en una fila por elemento (así una lista de 500 pedidos no se compone entera).
 */
@Immutable
sealed interface ScreenRow {
    val key: String
    val contentType: String

    data class One(val node: Node, val scope: Scope) : ScreenRow {
        override val key = node.location
        override val contentType = node.type
    }

    data class Item(val list: Node, val node: Node, val scope: Scope, val index: Int, val count: Int, override val key: String) : ScreenRow {
        override val contentType = "item:${node.type}"
    }
}

fun screenRows(body: List<Node>, scope: Scope, env: Env): List<ScreenRow> = body.flatMap { node ->
    when {
        !node.isVisible(scope, env) -> emptyList()
        node.type == "list" && node.children.isNotEmpty() -> {
            val rows = node.children.filter { it.isVisible(scope, env) }
            rows.mapIndexed { i, child -> ScreenRow.Item(node, child, scope, i, rows.size, "${node.location}#c$i") }
        }
        node.type == "list" -> {
            val items = listItems(node, scope, env)
            val item = node.item
            if (items.isEmpty() || item == null) listOfNotNull(node.empty?.takeIf { it.isVisible(scope, env) }?.let { ScreenRow.One(it, scope) })
            else items.mapIndexed { i, (itemScope, key) -> ScreenRow.Item(node, item, itemScope, i, items.size, "${node.location}#$key") }
        }
        else -> listOf(ScreenRow.One(node, scope))
    }
}

/** Los elementos de una `list` con su alcance (`as` + `index`) y su clave estable. Los que no se ven no cuentan. */
private fun listItems(node: Node, scope: Scope, env: Env): List<Pair<Scope, String>> {
    val raw = node.items(scope, env)
    val limit = node.number("limit")?.toInt()?.coerceAtLeast(0) ?: raw.size
    val seen = HashSet<String>()
    return raw.take(limit).mapIndexedNotNull { i, el ->
        val itemScope = scope.with(node.alias, el).with("index", JsonPrimitive(i))
        if (node.item?.isVisible(itemScope, env) == false) return@mapIndexedNotNull null
        // Una clave repetida tumba la LazyColumn: si se repite, se le suma la posición.
        val key = node.template("key")?.text(itemScope, env)?.takeIf { it.isNotBlank() && seen.add(it) } ?: "i$i"
        itemScope to key
    }
}

/** El espacio antes de una fila: entre bloques 12 dp; dentro de una lista, el de su estilo. */
fun rowGap(row: ScreenRow) = when {
    row is ScreenRow.Item && row.index > 0 -> when (row.list.literal("style")) {
        "cards" -> Spacing.sm
        "plain" -> 0.dp
        else -> SegmentGap
    }
    else -> Spacing.md
}

@Composable
fun ScreenRowContent(row: ScreenRow, modifier: Modifier = Modifier) {
    val margin = modifier.padding(horizontal = Spacing.margin)
    when (row) {
        is ScreenRow.One -> RenderNode(row.node, row.scope, margin)
        is ScreenRow.Item -> ListEntry(row.list, row.node, row.scope, row.index, row.count, margin)
    }
}

// ── Despacho ─────────────────────────────────────────────────────────────────────────────────────

/** Pinta un nodo del catálogo. Un tipo que esta app no conoce no pinta nada (la CI no deja publicarlo). */
@Composable
fun RenderNode(node: Node, scope: Scope, modifier: Modifier = Modifier) {
    val host = LocalScreenHost.current
    if (!node.isVisible(scope, host.env)) return
    val tap: (() -> Unit)? = node.action?.let { action -> { host.onAction(action, scope) } }
    when (node.type) {
        "column" -> SduiColumn(node, scope, modifier)
        "row" -> SduiRow(node, scope, modifier)
        "grid" -> SduiGrid(node, scope, modifier)
        "card" -> SduiCard(node, scope, modifier, tap)
        "section" -> SduiSection(node, scope, modifier, tap)
        "list" -> SduiList(node, scope, modifier)
        "spacer" -> Spacer(modifier.height((node.number("size") ?: 16.0).dp))
        "divider" -> HorizontalDivider(modifier)
        "text" -> SduiText(node, scope, modifier)
        "icon" -> OperatorIcons.named(node.text("name", scope))?.let {
            Icon(it, contentDescription = null, tint = textTone(node.text("tone", scope)), modifier = modifier.size((node.number("size") ?: 24.0).dp))
        }
        "image" -> SduiImage(node, scope, modifier)
        "avatar" -> {
            val name = node.text("name", scope)
            Avatar(name.ifBlank { null }, node.text("seed", scope).ifBlank { name }, modifier, size = (node.number("size") ?: 40.0).dp)
        }
        "tag" -> {
            val (bg, fg) = toneColors(node.text("tone", scope))
            StatusPill(node.text("text", scope), bg, fg, modifier, icon = OperatorIcons.named(node.text("icon", scope)))
        }
        "list_item" -> SduiListItem(node, scope, modifier, tap)
        "stat" -> SduiStat(node, scope, modifier, tap)
        "key_value" -> Column(modifier.fillMaxWidth()) {
            Text(node.text("label", scope), style = MaterialTheme.typography.labelSmallEmphasized, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text(node.text("value", scope), style = MaterialTheme.typography.bodyLarge)
        }
        "progress" -> SduiProgress(node, scope, modifier)
        "notice" -> SduiNotice(node, scope, modifier, tap)
        "empty" -> EmptyState(
            OperatorIcons.named(node.text("icon", scope)) ?: OperatorIcons.named("info")!!,
            node.text("title", scope), node.text("body", scope), modifier,
        )
        "button" -> SduiButton(node, scope, modifier, tap)
        "chips" -> SduiChips(node, scope, modifier)
        "text_field" -> SduiTextField(node, scope, modifier)
        "switch" -> SduiSwitch(node, scope, modifier)
        "stepper" -> SduiStepper(node, scope, modifier)
        else -> if (Catalog.components[node.type]?.native == true) NativeNode(node, scope, modifier)
    }
}

/**
 * Una pieza nativa de la app (el chat, el aviso de notificaciones): recibe sus propiedades de texto evaluadas y sus
 * propiedades-acción como funciones que ejecutan lo que diga el JSON.
 */
@Composable
private fun NativeNode(node: Node, scope: Scope, modifier: Modifier) {
    val host = LocalScreenHost.current
    val component = LocalNativeComponents.current[node.type] ?: return
    val values = node.props.keys.mapNotNull { key -> node.template(key)?.let { key to it.text(scope, host.env) } }.toMap()
    val actions = node.props.keys.mapNotNull { key -> node.actionProp(key)?.let { action -> key to { host.onAction(action, scope) } } }.toMap()
    component.Content(NativeProps(values, actions), modifier)
}

/** `bind` evaluado: la clave puede salir de un dato (`form.{{v.name}}`, un campo por variable de una plantilla). */
@Composable
private fun Node.bindKey(scope: Scope): String? = template("bind")?.text(scope, LocalScreenHost.current.env)?.takeIf { it.isNotBlank() }

/** El texto evaluado de una propiedad ("" si no está). */
@Composable
private fun Node.text(key: String, scope: Scope): String = template(key)?.text(scope, LocalScreenHost.current.env).orEmpty()

@Composable
private fun Node.flag(key: String, scope: Scope, default: Boolean): Boolean = flag(key, scope, LocalScreenHost.current.env, default)

private fun Node.spacing(default: Double = 8.0) = (number("spacing") ?: default).dp

/** Los hijos visibles, con `weight` aplicado si están en una fila. */
@Composable
private fun RowScope.RowChildren(node: Node, scope: Scope) {
    node.children.forEach { child ->
        RenderNode(child, scope, child.weight?.takeIf { it > 0f }?.let { Modifier.weight(it) } ?: Modifier)
    }
}

// ── Estructura ───────────────────────────────────────────────────────────────────────────────────

@Composable
private fun SduiColumn(node: Node, scope: Scope, modifier: Modifier) {
    val align = when (node.literal("align")) {
        "center" -> Alignment.CenterHorizontally
        "end" -> Alignment.End
        else -> Alignment.Start
    }
    Column(
        modifier.fillMaxWidth().padding((node.number("padding") ?: 0.0).dp),
        verticalArrangement = Arrangement.spacedBy(node.spacing()),
        horizontalAlignment = align,
    ) { node.children.forEach { RenderNode(it, scope) } }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun SduiRow(node: Node, scope: Scope, modifier: Modifier) {
    val gap = node.spacing()
    val horizontal = when (node.literal("arrange")) {
        "center" -> Arrangement.spacedBy(gap, Alignment.CenterHorizontally)
        "end" -> Arrangement.spacedBy(gap, Alignment.End)
        "between" -> Arrangement.SpaceBetween
        else -> Arrangement.spacedBy(gap)
    }
    val vertical = when (node.literal("align")) {
        "top" -> Alignment.Top
        "bottom" -> Alignment.Bottom
        else -> Alignment.CenterVertically
    }
    when {
        node.flag("wrap", scope, false) -> FlowRow(
            modifier.fillMaxWidth(), horizontalArrangement = horizontal, verticalArrangement = Arrangement.spacedBy(gap),
        ) { node.children.forEach { RenderNode(it, scope) } }
        node.flag("scroll", scope, false) -> Row(
            modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(gap), verticalAlignment = vertical,
        ) { node.children.forEach { RenderNode(it, scope) } }
        else -> Row(modifier.fillMaxWidth(), horizontalArrangement = horizontal, verticalAlignment = vertical) { RowChildren(node, scope) }
    }
}

@Composable
private fun SduiGrid(node: Node, scope: Scope, modifier: Modifier) {
    val host = LocalScreenHost.current
    val columns = (node.number("columns") ?: 2.0).toInt().coerceIn(1, 4)
    val gap = node.spacing()
    val visible = node.children.filter { it.isVisible(scope, host.env) }
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(gap)) {
        visible.chunked(columns).forEach { cells ->
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(gap)) {
                cells.forEach { RenderNode(it, scope, Modifier.weight(1f)) }
                repeat(columns - cells.size) { Spacer(Modifier.weight(1f)) }
            }
        }
    }
}

@Composable
private fun SduiCard(node: Node, scope: Scope, modifier: Modifier, tap: (() -> Unit)?) {
    val shape = MaterialTheme.shapes.largeIncreased
    val (bg, fg) = containerColors(node.text("tone", scope))
    val content: @Composable () -> Unit = {
        Column(Modifier.fillMaxWidth().padding((node.number("padding") ?: 16.0).dp), verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
            node.children.forEach { RenderNode(it, scope) }
        }
    }
    val m = modifier.fillMaxWidth()
    when (node.literal("style")) {
        "outlined" -> if (tap != null) OutlinedCard(onClick = tap, m, shape = shape) { content() } else OutlinedCard(m, shape = shape) { content() }
        "elevated" -> if (tap != null) ElevatedCard(onClick = tap, m, shape = shape) { content() } else ElevatedCard(m, shape = shape) { content() }
        else -> {
            val colors = CardDefaults.cardColors(containerColor = bg, contentColor = fg)
            if (tap != null) Card(onClick = tap, m, shape = shape, colors = colors) { content() } else Card(m, shape = shape, colors = colors) { content() }
        }
    }
}

@Composable
private fun SduiSection(node: Node, scope: Scope, modifier: Modifier, tap: (() -> Unit)?) {
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Spacing.sm)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f)) {
                Text(node.text("title", scope), style = MaterialTheme.typography.titleMediumEmphasized)
                node.text("subtitle", scope).takeIf { it.isNotBlank() }?.let {
                    Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (tap != null) TextButton(onClick = tap) { Text(node.text("action_label", scope).ifBlank { "Ver todo" }) }
        }
        node.children.forEach { RenderNode(it, scope) }
    }
}

/** Una `list` dentro de otro componente (no perezosa: para listas cortas dentro de una tarjeta o sección). */
@Composable
private fun SduiList(node: Node, scope: Scope, modifier: Modifier) {
    val host = LocalScreenHost.current
    val gap = when (node.literal("style")) {
        "cards" -> Spacing.sm
        "plain" -> 0.dp
        else -> SegmentGap
    }
    // Renglones fijos (un menú): cada hijo con el estilo de la lista.
    if (node.children.isNotEmpty()) {
        val rows = node.children.filter { it.isVisible(scope, host.env) }
        Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(gap)) {
            rows.forEachIndexed { i, child -> ListEntry(node, child, scope, i, rows.size, Modifier) }
        }
        return
    }
    val items = listItems(node, scope, host.env)
    val item = node.item ?: return
    if (items.isEmpty()) {
        node.empty?.let { RenderNode(it, scope, modifier) }
        return
    }
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(gap)) {
        items.forEachIndexed { i, (itemScope, _) -> ListEntry(node, item, itemScope, i, items.size, Modifier) }
    }
}

/**
 * Un elemento de una lista con el estilo de la lista: segmentada (renglones tonales con las puntas del grupo redondas,
 * como la paleta de acciones), tarjetas separadas o plana con divisores.
 */
@Composable
private fun ListEntry(list: Node, item: Node, scope: Scope, index: Int, count: Int, modifier: Modifier) {
    when (list.literal("style")) {
        "plain" -> RenderNode(item, scope, modifier)
        "cards" -> Surface(modifier.fillMaxWidth(), shape = MaterialTheme.shapes.largeIncreased, color = MaterialTheme.colorScheme.surfaceContainerLow) {
            RenderNode(item, scope)
        }
        else -> Surface(
            modifier.fillMaxWidth(),
            shape = segmentedShape(index, count),
            color = MaterialTheme.colorScheme.surfaceContainerLow,
        ) { RenderNode(item, scope) }
    }
}

// ── Contenido ────────────────────────────────────────────────────────────────────────────────────

@Composable
private fun SduiText(node: Node, scope: Scope, modifier: Modifier) {
    val t = MaterialTheme.typography
    val emphasis = node.flag("emphasis", scope, false)
    val style: TextStyle = when (node.text("style", scope)) {
        "display" -> if (emphasis) t.displaySmallEmphasized else t.displaySmall
        "headline" -> if (emphasis) t.headlineSmallEmphasized else t.headlineSmall
        "title" -> if (emphasis) t.titleLargeEmphasized else t.titleLarge
        "subtitle" -> if (emphasis) t.titleMediumEmphasized else t.titleMedium
        "label" -> if (emphasis) t.labelLargeEmphasized else t.labelLarge
        "caption" -> if (emphasis) t.bodySmallEmphasized else t.bodySmall
        else -> if (emphasis) t.bodyLargeEmphasized else t.bodyLarge
    }
    val align = when (node.text("align", scope)) {
        "center" -> TextAlign.Center
        "end" -> TextAlign.End
        else -> TextAlign.Start
    }
    Text(
        node.text("text", scope), modifier, style = style, color = textTone(node.text("tone", scope)), textAlign = align,
        maxLines = node.number("max_lines")?.toInt()?.coerceAtLeast(1) ?: Int.MAX_VALUE, overflow = TextOverflow.Ellipsis,
    )
}

@Composable
private fun SduiImage(node: Node, scope: Scope, modifier: Modifier) {
    val url = node.text("url", scope).takeIf { it.startsWith("https://") } ?: return
    val shape: Shape = when (node.literal("shape")) {
        "circle" -> CircleShape
        "square" -> RectangleShape
        else -> MaterialTheme.shapes.large
    }
    AsyncImage(
        model = url,
        contentDescription = node.text("description", scope).ifBlank { null },
        contentScale = ContentScale.Crop,
        modifier = modifier.fillMaxWidth().aspectRatio((node.number("ratio") ?: 1.5).toFloat().coerceIn(0.2f, 5f)).clip(shape),
    )
}

@Composable
private fun SduiListItem(node: Node, scope: Scope, modifier: Modifier, tap: (() -> Unit)?) {
    val muted = MaterialTheme.colorScheme.onSurfaceVariant
    val t = MaterialTheme.typography
    val emphasis = node.flag("emphasis", scope, false)
    Row(
        modifier
            .fillMaxWidth()
            .then(if (tap != null) Modifier.clickable(role = Role.Button, onClick = tap) else Modifier)
            .heightIn(min = 56.dp)
            .padding(horizontal = Spacing.lg, vertical = Spacing.md),
        horizontalArrangement = Arrangement.spacedBy(Spacing.lg),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        val avatar = node.text("avatar", scope)
        val icon = OperatorIcons.named(node.text("icon", scope))
        when {
            node.template("avatar") != null -> Avatar(avatar.ifBlank { null }, node.text("avatar_seed", scope).ifBlank { avatar }, size = 48.dp)
            icon != null -> {
                val (bg, fg) = toneColors(node.text("icon_tone", scope).ifBlank { "secondary" })
                IconTile(icon, bg, fg, size = 40.dp, shape = MaterialTheme.shapes.largeIncreased)
            }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
            node.text("overline", scope).takeIf { it.isNotBlank() }?.let {
                val tone = textTone(node.text("overline_tone", scope))
                Text(it, style = t.labelSmallEmphasized, color = if (tone == Color.Unspecified) muted else tone, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
            Text(
                node.text("title", scope), maxLines = 1, overflow = TextOverflow.Ellipsis,
                style = if (emphasis || node.template("emphasis") == null) t.titleMediumEmphasized else t.titleMedium,
            )
            node.text("subtitle", scope).takeIf { it.isNotBlank() }?.let {
                Text(
                    it, maxLines = 2, overflow = TextOverflow.Ellipsis,
                    style = if (emphasis) t.bodyMediumEmphasized else t.bodyMedium,
                    color = if (emphasis) MaterialTheme.colorScheme.onSurface else muted,
                )
            }
            node.text("tag", scope).takeIf { it.isNotBlank() }?.let {
                val (bg, fg) = toneColors(node.text("tag_tone", scope))
                StatusPill(it, bg, fg, Modifier.padding(top = 2.dp))
            }
            node.text("caption", scope).takeIf { it.isNotBlank() }?.let { caption ->
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    OperatorIcons.named(node.text("caption_icon", scope))?.let { ci ->
                        val tone = textTone(node.text("caption_icon_tone", scope))
                        Icon(ci, contentDescription = null, tint = if (tone == Color.Unspecified) muted else tone, modifier = Modifier.size(14.dp))
                    }
                    Text(caption, style = t.labelMedium, color = muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
        val trailing = node.text("trailing", scope)
        val caption = node.text("trailing_caption", scope)
        val badge = node.text("badge", scope).takeIf { it.isNotBlank() && it != "0" }
        if (trailing.isNotBlank() || caption.isNotBlank() || badge != null) {
            val tone = textTone(node.text("trailing_tone", scope))
            Column(horizontalAlignment = Alignment.End, verticalArrangement = Arrangement.spacedBy(4.dp)) {
                if (trailing.isNotBlank()) Text(trailing, style = t.titleSmallEmphasized, maxLines = 1, color = tone)
                if (caption.isNotBlank()) {
                    Text(caption, style = if (emphasis) t.labelMediumEmphasized else t.labelSmall, color = if (tone == Color.Unspecified) muted else tone, maxLines = 1)
                }
                badge?.let { Badge(it, node.text("badge_label", scope)) }
            }
        }
        if (node.flag("chevron", scope, false)) Icon(OperatorIcons.named("chevron_right")!!, contentDescription = null, tint = muted)
    }
}

/** El número de no leídos: TalkBack (y uiautomator) leen [label], no el número suelto. */
@Composable
private fun Badge(count: String, label: String) {
    Surface(
        shape = CircleShape, color = MaterialTheme.colorScheme.primary, contentColor = MaterialTheme.colorScheme.onPrimary,
        modifier = Modifier.clearAndSetSemantics { if (label.isNotBlank()) contentDescription = label },
    ) {
        Text(count, style = MaterialTheme.typography.labelMediumEmphasized, modifier = Modifier.padding(horizontal = 7.dp, vertical = 1.dp))
    }
}

/** Los pasos de un proceso en una línea (como las etapas del pedido): hechos con ✓, el actual resaltado. */
@Composable
private fun SduiStepper(node: Node, scope: Scope, modifier: Modifier) {
    val steps = options(node, scope)
    val current = node.text("current", scope)
    val reached = steps.indexOfFirst { it.first.asText() == current }
    val colors = MaterialTheme.colorScheme
    val label = node.text("label", scope).ifBlank { steps.getOrNull(reached)?.second.orEmpty() }
    Row(modifier.fillMaxWidth().clearAndSetSemantics { contentDescription = label }) {
        steps.forEachIndexed { i, (_, stepLabel) ->
            val done = i < reached
            val now = i == reached
            Column(Modifier.weight(1f), horizontalAlignment = Alignment.CenterHorizontally) {
                Box(Modifier.fillMaxWidth().height(28.dp), contentAlignment = Alignment.Center) {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Box(Modifier.weight(1f).height(3.dp).background(if (i == 0) Color.Transparent else if (i <= reached) colors.primary else colors.outlineVariant))
                        Box(Modifier.weight(1f).height(3.dp).background(if (i == steps.lastIndex) Color.Transparent else if (i < reached) colors.primary else colors.outlineVariant))
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
                    stepLabel, textAlign = TextAlign.Center, modifier = Modifier.padding(top = 4.dp),
                    style = if (now) MaterialTheme.typography.labelMediumEmphasized else MaterialTheme.typography.labelMedium,
                    color = if (done || now) colors.onSurface else colors.onSurfaceVariant,
                )
            }
        }
    }
}

@Composable
private fun SduiStat(node: Node, scope: Scope, modifier: Modifier, tap: (() -> Unit)?) {
    val (bg, fg) = containerColors(node.text("tone", scope))
    val content: @Composable () -> Unit = {
        Column(Modifier.fillMaxWidth().padding(Spacing.lg), verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
            OperatorIcons.named(node.text("icon", scope))?.let { Icon(it, contentDescription = null, modifier = Modifier.size(20.dp)) }
            Text(node.text("label", scope), style = MaterialTheme.typography.labelLarge, maxLines = 1, overflow = TextOverflow.Ellipsis)
            Text(node.text("value", scope), style = MaterialTheme.typography.headlineSmallEmphasized, maxLines = 1, overflow = TextOverflow.Ellipsis)
            node.text("caption", scope).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.bodySmall) }
        }
    }
    if (tap != null) {
        Surface(onClick = tap, modifier = modifier.fillMaxWidth(), shape = MaterialTheme.shapes.largeIncreased, color = bg, contentColor = fg) { content() }
    } else {
        Surface(modifier.fillMaxWidth(), shape = MaterialTheme.shapes.largeIncreased, color = bg, contentColor = fg) { content() }
    }
}

@Composable
private fun SduiProgress(node: Node, scope: Scope, modifier: Modifier) {
    val color = toneColors(node.text("tone", scope).ifBlank { "primary" }).second.let { fg ->
        if (node.text("tone", scope).isBlank()) MaterialTheme.colorScheme.primary else fg
    }
    Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(Spacing.xs)) {
        node.text("label", scope).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.labelLarge) }
        val value = node.template("value")?.evaluate(scope, LocalScreenHost.current.env)?.asText()?.toFloatOrNull()
        if (value == null) LinearWavyProgressIndicator(Modifier.fillMaxWidth(), color = color)
        else LinearWavyProgressIndicator(progress = { value.coerceIn(0f, 1f) }, modifier = Modifier.fillMaxWidth(), color = color)
    }
}

@Composable
private fun SduiNotice(node: Node, scope: Scope, modifier: Modifier, tap: (() -> Unit)?) {
    val tone = node.text("tone", scope).ifBlank { "info" }
    val (bg, fg) = when (tone) {
        "success" -> toneColors("success")
        "warning" -> toneColors("warning")
        "danger" -> toneColors("danger")
        else -> toneColors("secondary")
    }
    val icon = OperatorIcons.named(node.text("icon", scope)) ?: OperatorIcons.named(
        when (tone) {
            "success" -> "check_circle"
            "warning" -> "warning"
            "danger" -> "error"
            else -> "info"
        },
    )!!
    val content: @Composable () -> Unit = {
        Row(Modifier.fillMaxWidth().padding(Spacing.lg), horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
            Icon(icon, contentDescription = null, modifier = Modifier.size(20.dp))
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                node.text("title", scope).takeIf { it.isNotBlank() }?.let { Text(it, style = MaterialTheme.typography.titleSmallEmphasized) }
                Text(node.text("text", scope), style = MaterialTheme.typography.bodyMedium)
            }
        }
    }
    if (tap != null) Surface(onClick = tap, modifier = modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large, color = bg, contentColor = fg) { content() }
    else Surface(modifier.fillMaxWidth(), shape = MaterialTheme.shapes.large, color = bg, contentColor = fg) { content() }
}

// ── Acciones y entradas ──────────────────────────────────────────────────────────────────────────

@Composable
private fun SduiButton(node: Node, scope: Scope, modifier: Modifier, tap: (() -> Unit)?) {
    val host = LocalScreenHost.current
    val enabled = node.flag("enabled", scope, true) && !host.busy && tap != null
    val full = node.flag("full_width", scope, false)
    val m = if (full) modifier.fillMaxWidth().heightIn(min = 56.dp) else modifier
    val danger = node.text("tone", scope) == "danger"
    val label: @Composable RowScope.() -> Unit = {
        OperatorIcons.named(node.text("icon", scope))?.let {
            Icon(it, contentDescription = null, modifier = Modifier.size(ButtonDefaults.IconSize))
            Spacer(Modifier.width(ButtonDefaults.IconSpacing))
        }
        Text(node.text("text", scope))
    }
    val onClick = { tap?.invoke(); Unit }
    when (node.literal("style")) {
        "tonal" -> FilledTonalButton(onClick = onClick, modifier = m, enabled = enabled, shapes = ButtonDefaults.shapes(), content = label)
        "outlined" -> OutlinedButton(onClick = onClick, modifier = m, enabled = enabled, shapes = ButtonDefaults.shapes(), content = label)
        "text" -> TextButton(onClick = onClick, modifier = m, enabled = enabled, shapes = ButtonDefaults.shapes(), content = label)
        else -> Button(
            onClick = onClick, modifier = m, enabled = enabled, shapes = ButtonDefaults.shapes(),
            colors = if (danger) ButtonDefaults.buttonColors(containerColor = MaterialTheme.colorScheme.error, contentColor = MaterialTheme.colorScheme.onError)
            else ButtonDefaults.buttonColors(),
            content = label,
        )
    }
}

/** Las opciones de `chips`: la lista literal o lo que dé su plantilla. */
@Composable
private fun options(node: Node, scope: Scope): List<Pair<JsonElement, String>> {
    val env = LocalScreenHost.current.env
    val raw = (node.props["options"] as? JsonArray) ?: (node.template("options")?.evaluate(scope, env) as? JsonArray) ?: return emptyList()
    return raw.mapNotNull { el ->
        val o = el as? JsonObject ?: return@mapNotNull null
        val value = o["value"] ?: return@mapNotNull null
        val label = (o["label"] as? JsonPrimitive)?.content?.let { com.hubara.operator.core.sdui.Template.parseOrLiteral(it).text(scope, env) } ?: value.asText()
        value to label
    }
}

@Composable
private fun SduiChips(node: Node, scope: Scope, modifier: Modifier) {
    val host = LocalScreenHost.current
    val bind = node.bindKey(scope) ?: return
    val selected = host.bound(bind)
    val opts = options(node, scope)
    if (node.literal("style") == "segmented") {
        SingleChoiceSegmentedButtonRow(modifier.fillMaxWidth()) {
            opts.forEachIndexed { i, (value, label) ->
                SegmentedButton(
                    selected = selected?.asText() == value.asText(),
                    onClick = { host.onBind(bind, value, false) },
                    shape = SegmentedButtonDefaults.itemShape(i, opts.size),
                ) { Text(label, maxLines = 1) }
            }
        }
    } else {
        Row(modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(Spacing.sm)) {
            opts.forEach { (value, label) ->
                val on = selected?.asText() == value.asText()
                FilterChip(
                    selected = on, onClick = { host.onBind(bind, value, false) }, label = { Text(label) },
                    leadingIcon = if (on) {
                        { Icon(OperatorIcons.Check, contentDescription = null, modifier = Modifier.size(FilterChipDefaults.IconSize)) }
                    } else null,
                    shape = CircleShape,
                )
            }
        }
    }
}

@Composable
private fun SduiTextField(node: Node, scope: Scope, modifier: Modifier) {
    val host = LocalScreenHost.current
    val bind = node.bindKey(scope) ?: return
    val current = host.bound(bind)
    val initial = node.text("value", scope)
    // El valor inicial se guarda una vez (así también viaja en la llamada aunque el operador no lo toque).
    LaunchedEffect(bind) { if (current == null && initial.isNotEmpty()) host.onBind(bind, JsonPrimitive(initial), false) }
    val keyboard = when (node.literal("keyboard")) {
        "number" -> KeyboardType.Number
        "phone" -> KeyboardType.Phone
        "email" -> KeyboardType.Email
        "url" -> KeyboardType.Uri
        else -> KeyboardType.Text
    }
    val multiline = node.flag("multiline", scope, false)
    val shown = current?.asText() ?: initial
    val maxLength = node.text("max_length", scope).toDoubleOrNull()?.toInt()?.takeIf { it > 0 }
    OutlinedTextField(
        value = shown,
        // Solo cambios de verdad: al perder el foco el campo puede re-emitir el mismo valor y no hay que pedir nada.
        onValueChange = { new ->
            val limited = maxLength?.let { new.take(it) } ?: new
            if (limited != shown) host.onBind(bind, JsonPrimitive(limited), bind.startsWith("state."))
        },
        label = { Text(node.text("label", scope)) },
        placeholder = node.text("placeholder", scope).takeIf { it.isNotBlank() }?.let { p -> { Text(p) } },
        singleLine = !multiline,
        minLines = if (multiline) 3 else 1,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        modifier = modifier.fillMaxWidth(),
    )
}

@Composable
private fun SduiSwitch(node: Node, scope: Scope, modifier: Modifier) {
    val host = LocalScreenHost.current
    val bind = node.bindKey(scope) ?: return
    val on = host.bound(bind).truthy
    Row(
        modifier
            .fillMaxWidth()
            .toggleable(value = on, role = Role.Switch, onValueChange = { host.onBind(bind, JsonPrimitive(it), false) })
            .heightIn(min = 56.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(Spacing.lg),
    ) {
        Column(Modifier.weight(1f)) {
            Text(node.text("label", scope), style = MaterialTheme.typography.bodyLarge)
            node.text("description", scope).takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        Switch(checked = on, onCheckedChange = null)
    }
}

// ── Tonos ────────────────────────────────────────────────────────────────────────────────────────

/** Fondo y texto de una píldora o ícono por tono. */
@Composable
private fun toneColors(tone: String): Pair<Color, Color> {
    val s = MaterialTheme.colorScheme
    val c = OperatorTheme.colors
    return when (tone) {
        "primary" -> s.primaryContainer to s.onPrimaryContainer
        "secondary" -> s.secondaryContainer to s.onSecondaryContainer
        "bot" -> s.tertiaryContainer to s.onTertiaryContainer
        "success" -> c.successContainer to c.onSuccessContainer
        "warning" -> c.hoyContainer to c.onHoyContainer
        "danger" -> c.graveContainer to c.onGraveContainer
        else -> s.surfaceContainerHighest to s.onSurfaceVariant
    }
}

/** Fondo de tarjetas y cifras: neutro un tono arriba de la pantalla; con tono, el contenedor de ese color. */
@Composable
private fun containerColors(tone: String): Pair<Color, Color> =
    if (tone.isBlank() || tone == "neutral") MaterialTheme.colorScheme.surfaceContainerLow to MaterialTheme.colorScheme.onSurface
    else toneColors(tone)

@Composable
private fun textTone(tone: String): Color {
    val s = MaterialTheme.colorScheme
    val c = OperatorTheme.colors
    return when (tone) {
        "muted" -> s.onSurfaceVariant
        "primary" -> s.primary
        "bot" -> s.tertiary
        "success" -> c.success
        "warning" -> c.hoy
        "danger" -> c.grave
        else -> Color.Unspecified
    }
}
