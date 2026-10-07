@file:OptIn(androidx.compose.material3.ExperimentalMaterial3ExpressiveApi::class)

package com.hubara.operator.feature.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fitInside
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.AssistChipDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.TopAppBarScrollBehavior
import androidx.compose.ui.layout.WindowInsetsRulers
import com.hubara.operator.core.designsystem.Avatar
import com.hubara.operator.core.sdui.truthy
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LoadingIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.nestedscroll.nestedScroll
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.hubara.operator.core.designsystem.EmptyState
import com.hubara.operator.core.designsystem.OperatorIcons
import com.hubara.operator.core.designsystem.OperatorTheme
import com.hubara.operator.core.designsystem.Spacing
import com.hubara.operator.core.sdui.Action
import com.hubara.operator.core.sdui.Env
import com.hubara.operator.core.sdui.ScreenDoc
import com.hubara.operator.core.sdui.Scope
import com.hubara.operator.core.sdui.parseScreen
import com.hubara.operator.core.ui.RadarIndicator
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/** Para los tests: la lista de la pantalla (se desliza hasta un componente con `performScrollToNode`). */
const val SCREEN_LIST_TAG = "server_screen_list"

/** Lo que la pantalla le pide al ViewModel (y a la navegación). */
data class ScreenCallbacks(
    val onAction: (Action, Scope) -> Unit = { _, _ -> },
    /** `bind` (`state.x` / `form.x`), el valor y si viene de un campo de texto (se espera a que pare de escribir). */
    val onBind: (String, JsonElement, Boolean) -> Unit = { _, _, _ -> },
    val onRefresh: () -> Unit = {},
    val onRetry: () -> Unit = {},
    val onConfirm: (Boolean) -> Unit = {},
    /** null = es la raíz de una pestaña: sin flecha de atrás. */
    val onBack: (() -> Unit)? = null,
    val onUpdateApp: () -> Unit = {},
)

/**
 * Una pantalla del servidor, sin ViewModel (la receta `XRoute` + `XScreen` de la app). Encabezado con el título y las
 * acciones de `topActions`, el cuerpo en una lista perezosa (las `list` de primer nivel se reparten en filas), «tirar
 * para actualizar», el botón flotante de `fab` y el diálogo de las llamadas con `confirm`.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ServerScreen(
    ui: ScreenUi,
    callbacks: ScreenCallbacks,
    snackbar: SnackbarHostState = remember { SnackbarHostState() },
    asSheet: Boolean = false,
    nowMs: () -> Long = System::currentTimeMillis,
) {
    val env = remember(nowMs) { Env(nowMs = nowMs) }
    val scope = ui.scope(nowMs())
    val doc = ui.doc
    val title = doc?.title?.text(scope, env).orEmpty()
    val host = ScreenHost(
        env = env,
        busy = ui.busy,
        bound = { bind -> bound(ui, bind) },
        onAction = callbacks.onAction,
        onBind = callbacks.onBind,
    )

    ui.confirm?.let { c ->
        AlertDialog(
            onDismissRequest = { callbacks.onConfirm(false) },
            title = { Text(c.title) },
            text = c.body?.let { body -> { Text(body) } },
            confirmButton = { TextButton(onClick = { callbacks.onConfirm(true) }) { Text(c.accept) } },
            dismissButton = { TextButton(onClick = { callbacks.onConfirm(false) }) { Text(c.dismiss) } },
        )
    }

    CompositionLocalProvider(LocalScreenHost provides host) {
        if (asSheet) {
            Box(Modifier.fillMaxWidth()) {
                Column(Modifier.fillMaxWidth()) {
                    if (title.isNotBlank()) {
                        Text(
                            title, style = MaterialTheme.typography.titleLargeEmphasized,
                            modifier = Modifier.padding(start = Spacing.lg, end = Spacing.lg, bottom = Spacing.sm),
                        )
                    }
                    Body(ui, doc, scope, callbacks, PaddingValues(bottom = Spacing.xl))
                }
                SnackbarHost(snackbar, Modifier.align(Alignment.BottomCenter))
            }
            return@CompositionLocalProvider
        }
        val scroll = TopAppBarDefaults.pinnedScrollBehavior()
        val fill = doc?.layout == "fill"
        Scaffold(
            modifier = if (fill) Modifier else Modifier.nestedScroll(scroll.nestedScrollConnection),
            topBar = { ScreenTopBar(doc, title, scope, callbacks, if (fill) null else scroll) },
            floatingActionButton = {
                val fab = doc?.fab
                if (ui.phase == ScreenPhase.READY && fab != null && fab.action != null) {
                    // Con contenido propio y no `icon =`/`text =`: esa variante llega a accesibilidad sin texto (gotcha 18).
                    ExtendedFloatingActionButton(onClick = { fab.action?.let { callbacks.onAction(it, scope) } }) {
                        OperatorIcons.named(fab.icon)?.let {
                            Icon(it, contentDescription = null)
                            Spacer(Modifier.width(Spacing.md))
                        }
                        Text(fab.label)
                    }
                }
            },
            snackbarHost = { SnackbarHost(snackbar) },
        ) { inner ->
            if (fill && ui.phase == ScreenPhase.READY && doc != null) {
                // El chat: el cuerpo llena la pantalla y sube con el teclado sin doble relleno (skill edge-to-edge).
                FillBody(doc, scope, Modifier.padding(inner).consumeWindowInsets(inner).fitInside(WindowInsetsRulers.Ime.current))
            } else {
                Box(Modifier.fillMaxSize().padding(top = inner.calculateTopPadding())) {
                    Body(ui, doc, scope, callbacks, PaddingValues(bottom = inner.calculateBottomPadding() + if (doc?.fab != null) 88.dp else Spacing.xl))
                }
            }
        }
    }
}

/**
 * El encabezado: atrás, avatar, título y subtítulo, y las acciones de `topActions` — íconos, chips con texto (el «Pedido
 * #41» del chat) y los renglones del menú «Más opciones». Lo que tiene `visible` en falso no aparece.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ScreenTopBar(doc: ScreenDoc?, title: String, scope: Scope, callbacks: ScreenCallbacks, scroll: TopAppBarScrollBehavior?) {
    val env = LocalScreenHost.current.env
    val actions = doc?.topActions.orEmpty().filter { top -> top.visible.all { it.evaluate(scope, env).truthy } }
    val subtitle = doc?.subtitle?.text(scope, env).orEmpty()
    TopAppBar(
        title = {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(Spacing.md)) {
                doc?.avatar?.let { avatar ->
                    val name = avatar.text(scope, env)
                    Avatar(name.ifBlank { null }, doc.avatarSeed?.text(scope, env)?.ifBlank { null } ?: name, size = 40.dp)
                }
                Column {
                    Text(
                        title, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        style = if (subtitle.isBlank()) MaterialTheme.typography.titleLargeEmphasized else MaterialTheme.typography.titleMediumEmphasized,
                    )
                    if (subtitle.isNotBlank()) {
                        Text(
                            subtitle, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                    }
                }
            }
        },
        navigationIcon = {
            callbacks.onBack?.let { back -> IconButton(onClick = back) { Icon(OperatorIcons.ArrowBack, contentDescription = "Atrás") } }
        },
        actions = {
            RadarIndicator()
            actions.filter { it.style == "icon" }.forEach { top ->
                IconButton(onClick = { top.action?.let { callbacks.onAction(it, scope) } }) {
                    Icon(OperatorIcons.named(top.icon) ?: OperatorIcons.MoreVert, contentDescription = top.label.text(scope, env))
                }
            }
            actions.filter { it.style == "chip" }.forEach { top ->
                AssistChip(
                    onClick = { top.action?.let { callbacks.onAction(it, scope) } },
                    label = { Text(top.label.text(scope, env)) },
                    leadingIcon = OperatorIcons.named(top.icon)?.let { icon ->
                        { Icon(icon, contentDescription = null, modifier = Modifier.size(AssistChipDefaults.IconSize)) }
                    },
                    shape = MaterialTheme.shapes.extraLarge,
                )
            }
            val menu = actions.filter { it.style == "menu" }
            if (menu.isNotEmpty()) {
                var open by remember { mutableStateOf(false) }
                IconButton(onClick = { open = true }) { Icon(OperatorIcons.MoreVert, contentDescription = "Más opciones") }
                DropdownMenu(expanded = open, onDismissRequest = { open = false }, shape = MaterialTheme.shapes.large) {
                    menu.forEach { top ->
                        DropdownMenuItem(
                            text = { Text(top.label.text(scope, env)) },
                            onClick = {
                                open = false
                                top.action?.let { callbacks.onAction(it, scope) }
                            },
                            leadingIcon = OperatorIcons.named(top.icon)?.let { icon -> { Icon(icon, contentDescription = null) } },
                        )
                    }
                }
            }
        },
        scrollBehavior = scroll,
    )
}

/** Cuerpo de una pantalla `fill`: los componentes en columna; el que tiene `weight` se queda con el espacio libre. */
@Composable
private fun FillBody(doc: ScreenDoc, scope: Scope, modifier: Modifier) {
    val env = LocalScreenHost.current.env
    Column(modifier.fillMaxSize()) {
        doc.body.filter { it.isVisible(scope, env) }.forEach { node ->
            RenderNode(node, scope, node.weight?.takeIf { it > 0f }?.let { Modifier.weight(it) } ?: Modifier)
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Body(ui: ScreenUi, doc: ScreenDoc?, scope: Scope, callbacks: ScreenCallbacks, padding: PaddingValues) {
    when {
        ui.phase == ScreenPhase.MISSING -> Problem(
            "No se pudo abrir esta pantalla", "Revisa la conexión y vuelve a intentar.", "Reintentar", callbacks.onRetry,
        )
        ui.phase == ScreenPhase.OUTDATED -> Problem(
            "Actualiza la app", "Esta pantalla necesita una versión más nueva de la app.", "Actualizar", callbacks.onUpdateApp,
        )
        ui.phase == ScreenPhase.LOADING || doc == null || ui.waiting -> Box(Modifier.fillMaxSize().padding(Spacing.xxl), contentAlignment = Alignment.Center) {
            LoadingIndicator()
        }
        ui.broken -> Problem("No se pudieron cargar los datos", "Revisa la conexión y vuelve a intentar.", "Reintentar", callbacks.onRetry)
        else -> {
            // El indicador de «tirar para actualizar» solo cuando lo pidió el operador; las recargas por eventos son calladas.
            var pulling by rememberSaveable { mutableStateOf(false) }
            LaunchedEffect(ui.refreshing) { if (!ui.refreshing) pulling = false }
            val host = LocalScreenHost.current
            val rows = screenRows(doc.body, scope, host.env)
            PullToRefreshBox(
                isRefreshing = pulling && ui.refreshing,
                onRefresh = {
                    pulling = true
                    callbacks.onRefresh()
                },
                modifier = Modifier.fillMaxSize(),
            ) {
                LazyColumn(
                    Modifier.fillMaxSize().testTag(SCREEN_LIST_TAG),
                    contentPadding = PaddingValues(top = Spacing.xs, bottom = padding.calculateBottomPadding()),
                ) {
                    // El espacio va en cada fila: 12 dp entre bloques, 2 dp entre los renglones de una lista segmentada.
                    itemsIndexed(rows, key = { _, row -> row.key }, contentType = { _, row -> row.contentType }) { i, row ->
                        ScreenRowContent(row, Modifier.padding(top = if (i == 0) 0.dp else rowGap(row)))
                    }
                }
            }
        }
    }
}

@Composable
private fun Problem(title: String, body: String, button: String, onClick: () -> Unit) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        EmptyState(OperatorIcons.Error, title, body)
        Button(onClick = onClick, shapes = ButtonDefaults.shapes()) { Text(button) }
    }
}

/** El valor guardado en `state.x` o `form.x`. */
private fun bound(ui: ScreenUi, bind: String): JsonElement? {
    val (where, key) = bind.split('.', limit = 2).takeIf { it.size == 2 }?.let { it[0] to it[1] } ?: return null
    return when (where) {
        "state" -> ui.state[key]
        "form" -> ui.form[key]
        else -> null
    }
}

@Preview(showBackground = true, heightDp = 720)
@Composable
private fun ServerScreenPreview() = OperatorTheme {
    val doc = parseScreen(
        """{"schema": 1, "id": "ventas", "title": "Ventas", "state": {"rango": "today"},
           "data": {"pedidos": {"get": "/api/orders/orders"}},
           "body": [
             {"type": "chips", "bind": "state.rango", "options": [{"value": "today", "label": "Hoy"}, {"value": "7d", "label": "7 días"}]},
             {"type": "grid", "children": [
               {"type": "stat", "label": "Pedidos", "value": "{{pedidos.orders | count}}", "icon": "orders"},
               {"type": "stat", "label": "Ventas", "value": "{{pedidos.orders | sum:'total_cop' | money}}", "tone": "success", "icon": "payments"}]},
             {"type": "section", "title": "Pedidos", "children": [
               {"type": "list", "items": "{{pedidos.orders}}", "as": "p",
                "item": {"type": "list_item", "avatar": "{{p.customer}}", "title": "#{{p.display_id | replace:'#':''}} · {{p.customer}}",
                         "subtitle": "{{p.city}}", "tag": "Nuevo", "trailing": "{{p.total_cop | money}}", "chevron": true}}]}
           ]}""",
    ).doc
    val data = Json.parseToJsonElement(
        """{"orders": [{"display_id": "#41", "customer": "Laura Prueba", "city": "Medellín", "total_cop": 45000},
                       {"display_id": "#42", "customer": "Sofía", "city": "Bogotá", "total_cop": 30000}]}""",
    )
    ServerScreen(ScreenUi(ScreenPhase.READY, doc, data = mapOf("pedidos" to data), state = doc!!.state), ScreenCallbacks())
}
