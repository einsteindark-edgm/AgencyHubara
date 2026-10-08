package com.hubara.operator.widget.hot

import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.net.toUri
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.glance.ColorFilter
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.Image
import androidx.glance.ImageProvider
import androidx.glance.LocalContext
import androidx.glance.LocalSize
import androidx.glance.action.ActionParameters
import androidx.glance.action.actionParametersOf
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.SizeMode
import androidx.glance.appwidget.action.ActionCallback
import androidx.glance.appwidget.action.actionRunCallback
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.appWidgetBackground
import androidx.glance.appwidget.lazy.LazyColumn
import androidx.glance.appwidget.lazy.items
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.state.updateAppWidgetState
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.color.ColorProvider
import androidx.glance.color.DynamicThemeColorProviders
import androidx.glance.currentState
import androidx.glance.layout.Alignment
import androidx.glance.layout.Box
import androidx.glance.layout.Column
import androidx.glance.layout.Row
import androidx.glance.layout.Spacer
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.height
import androidx.glance.layout.padding
import androidx.glance.layout.size
import androidx.glance.layout.width
import androidx.glance.material3.ColorProviders
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import androidx.glance.unit.ColorProvider
import com.hubara.operator.core.designsystem.OperatorPalette
import com.hubara.operator.core.push.AmbientStore
import com.hubara.operator.core.push.HotWidgetUpdater
import com.hubara.operator.core.push.VigiaWorker
import com.hubara.operator.core.push.WidgetPage
import com.hubara.operator.core.push.WidgetPageKind
import com.hubara.operator.core.push.WidgetPages
import com.hubara.operator.core.push.WidgetRow
import com.hubara.operator.core.push.WidgetTone
import com.hubara.operator.core.ui.listTimeLabel
import dagger.Module
import dagger.Provides
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import java.time.ZoneId

@EntryPoint
@InstallIn(SingletonComponent::class)
interface WidgetEntryPoint {
    fun ambientStore(): AmbientStore
}

/** La pestaña elegida en cada widget (estado de Glance, uno por widget puesto). */
private val TAB_KEY = stringPreferencesKey("tab")

/** El parámetro de [SelectTabAction]: el nombre de la [WidgetPageKind]. */
val TAB_PARAM = ActionParameters.Key<String>("tab")

/**
 * Intent explícito a la actividad de inicio con un enlace `hubara://`, que la app vuelve a validar (`DeepLinks.parse`).
 * Nunca un intent implícito que otra app pudiera atender (skill android-intent-security).
 */
fun openIntent(context: Context, link: String): Intent {
    val launch = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: Intent(Intent.ACTION_MAIN)
    return Intent(launch).setPackage(context.packageName).setData(link.toUri())
        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
}

/** Debajo de esto el widget es compacto (2×2): solo los contadores. */
private val COMPACT_WIDTH = 200.dp
private val COMPACT_HEIGHT = 160.dp

/** La marca en Android 11 (sin colores dinámicos); en Android 12+, los del fondo de pantalla (Material You). */
private val BrandColors = ColorProviders(light = OperatorPalette.light, dark = OperatorPalette.dark)

private val DangerContainer = ColorProvider(OperatorPalette.lightStatus.graveContainer, OperatorPalette.darkStatus.graveContainer)
private val OnDangerContainer = ColorProvider(OperatorPalette.lightStatus.onGraveContainer, OperatorPalette.darkStatus.onGraveContainer)
private val WarningContainer = ColorProvider(OperatorPalette.lightStatus.hoyContainer, OperatorPalette.darkStatus.hoyContainer)
private val OnWarningContainer = ColorProvider(OperatorPalette.lightStatus.onHoyContainer, OperatorPalette.darkStatus.onHoyContainer)

/**
 * El widget entero, según el tamaño: compacto (2×2) con los tres contadores, o grande con las pestañas Incendios ·
 * Humano · Ventas y la lista de la elegida. Sin mensajes: va en la pantalla de inicio.
 */
@Composable
fun WidgetContent(pages: WidgetPages, selected: WidgetPageKind, nowMs: Long, zone: ZoneId) {
    val colors = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) DynamicThemeColorProviders else BrandColors
    GlanceTheme(colors = colors) {
        val size = LocalSize.current
        val root = GlanceModifier.fillMaxSize().appWidgetBackground()
            .background(ImageProvider(R.drawable.widget_shape_card), colorFilter = ColorFilter.tint(GlanceTheme.colors.widgetBackground))
        if (size.width < COMPACT_WIDTH || size.height < COMPACT_HEIGHT) {
            Compact(pages, root.padding(8.dp))
        } else {
            Large(pages, selected, nowMs, zone, root.padding(12.dp))
        }
    }
}

// ── Compacto (2×2) ────────────────────────────────────────────────────────────────────────────────

@Composable
private fun Compact(pages: WidgetPages, modifier: GlanceModifier) {
    val context = LocalContext.current
    Column(modifier) {
        WidgetPageKind.entries.forEachIndexed { i, kind ->
            if (i > 0) Spacer(GlanceModifier.height(6.dp))
            val total = pages[kind]?.total
            val burning = kind == WidgetPageKind.FIRES && (total ?: 0) > 0
            val bg = if (burning) DangerContainer else GlanceTheme.colors.surfaceVariant
            val fg = if (burning) OnDangerContainer else GlanceTheme.colors.onSurfaceVariant
            Row(
                GlanceModifier.fillMaxWidth().defaultWeight()
                    .background(ImageProvider(R.drawable.widget_shape_pill), colorFilter = ColorFilter.tint(bg))
                    .padding(horizontal = 10.dp)
                    .clickable(actionStartActivity(openIntent(context, kind.link))),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Image(ImageProvider(kind.icon), contentDescription = null, modifier = GlanceModifier.size(16.dp), colorFilter = ColorFilter.tint(fg))
                Spacer(GlanceModifier.width(6.dp))
                Text(kind.title, style = TextStyle(color = fg, fontSize = 13.sp), maxLines = 1, modifier = GlanceModifier.defaultWeight())
                Text(total?.toString() ?: "–", style = TextStyle(color = fg, fontSize = 20.sp, fontWeight = FontWeight.Medium))
            }
        }
    }
}

// ── Grande: pestañas + lista ──────────────────────────────────────────────────────────────────────

@Composable
private fun Large(pages: WidgetPages, selected: WidgetPageKind, nowMs: Long, zone: ZoneId, modifier: GlanceModifier) {
    val page = pages[selected]
    Column(modifier) {
        Row(GlanceModifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            WidgetPageKind.entries.forEach { kind ->
                Tab(kind, pages[kind], kind == selected)
                Spacer(GlanceModifier.width(6.dp))
            }
            Spacer(GlanceModifier.defaultWeight())
            Image(
                ImageProvider(R.drawable.widget_ic_refresh), contentDescription = "Actualizar",
                colorFilter = ColorFilter.tint(GlanceTheme.colors.onSurfaceVariant),
                modifier = GlanceModifier.size(36.dp).padding(6.dp).clickable(actionRunCallback<RefreshAction>()),
            )
        }
        Spacer(GlanceModifier.height(6.dp))
        // La MISMA estructura siempre (la lista existe aunque esté vacía y el aviso va como su único renglón): en
        // Android 11 el launcher reaplica sobre las vistas que ya tiene, y si donde iba un aviso ahora va la lista,
        // la lista no se conecta y queda vacía («Cannot setRemoteViewsAdapter…», lo vio S24).
        val rows = page?.rows.orEmpty()
        val note = when {
            page == null -> "Abre la app para cargar el widget."
            rows.isEmpty() -> selected.empty
            else -> null
        }
        LazyColumn(GlanceModifier.fillMaxWidth().defaultWeight()) {
            if (note != null) item { Note(note) } else items(rows) { row -> RowItem(row, nowMs, zone) }
        }
        Text(
            page?.updatedMs?.takeIf { it > 0 }?.let { "Actualizado ${listTimeLabel(it, nowMs, zone)}" }.orEmpty(),
            style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 11.sp),
            modifier = GlanceModifier.padding(start = 4.dp, top = 4.dp),
        )
    }
}

/** Una pestaña: el nombre y su total (sin total si nunca cargó). Tocarla la elige en ESTE widget. */
@Composable
private fun Tab(kind: WidgetPageKind, page: WidgetPage?, selected: Boolean) {
    val label = page?.total?.let { "${kind.title} $it" } ?: kind.title
    val bg = if (selected) GlanceTheme.colors.secondaryContainer else GlanceTheme.colors.surfaceVariant
    val fg = if (selected) GlanceTheme.colors.onSecondaryContainer else GlanceTheme.colors.onSurfaceVariant
    Text(
        label,
        maxLines = 1,
        style = TextStyle(color = fg, fontSize = 12.sp, fontWeight = if (selected) FontWeight.Medium else FontWeight.Normal),
        modifier = GlanceModifier
            .background(ImageProvider(R.drawable.widget_shape_pill), colorFilter = ColorFilter.tint(bg))
            .padding(horizontal = 10.dp, vertical = 6.dp)
            .clickable(actionRunCallback<SelectTabAction>(actionParametersOf(TAB_PARAM to kind.name))),
    )
}

/** Una fila: iniciales (o un ícono), título, detalle con la hora fija y la etiqueta de color. Abre su chat. */
@Composable
private fun RowItem(row: WidgetRow, nowMs: Long, zone: ZoneId) {
    val context = LocalContext.current
    val (container, onContainer) = toneColors(row.tone)
    val detail = listOfNotNull(
        row.detail.takeIf { it.isNotBlank() },
        row.since?.let { "${it.label} ${listTimeLabel(it.ms, nowMs, zone)}" },
    ).joinToString(" · ")
    var modifier = GlanceModifier.fillMaxWidth().padding(vertical = 6.dp, horizontal = 4.dp)
    row.link?.let { modifier = modifier.clickable(actionStartActivity(openIntent(context, it))) }
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Box(
            GlanceModifier.size(32.dp).background(ImageProvider(R.drawable.widget_shape_circle), colorFilter = ColorFilter.tint(container)),
            contentAlignment = Alignment.Center,
        ) {
            val initials = row.initials
            if (initials != null) {
                Text(initials, style = TextStyle(color = onContainer, fontSize = 12.sp, fontWeight = FontWeight.Medium))
            } else {
                Image(ImageProvider(R.drawable.widget_ic_order), contentDescription = null, modifier = GlanceModifier.size(16.dp), colorFilter = ColorFilter.tint(onContainer))
            }
        }
        Spacer(GlanceModifier.width(10.dp))
        Column(GlanceModifier.defaultWeight()) {
            Text(row.title, maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onSurface, fontSize = 13.sp, fontWeight = FontWeight.Medium))
            if (detail.isNotBlank()) {
                Text(detail, maxLines = 1, style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 12.sp))
            }
        }
        row.tag?.let { tag ->
            Spacer(GlanceModifier.width(6.dp))
            Text(
                tag, maxLines = 1,
                style = TextStyle(color = onContainer, fontSize = 11.sp),
                modifier = GlanceModifier
                    .background(ImageProvider(R.drawable.widget_shape_pill), colorFilter = ColorFilter.tint(container))
                    .padding(horizontal = 8.dp, vertical = 2.dp),
            )
        }
    }
}

@Composable
private fun Note(text: String) {
    Box(GlanceModifier.fillMaxWidth().padding(vertical = 24.dp), contentAlignment = Alignment.Center) {
        Text(text, style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant, fontSize = 13.sp))
    }
}

@Composable
private fun toneColors(tone: WidgetTone): Pair<ColorProvider, ColorProvider> = when (tone) {
    WidgetTone.DANGER -> DangerContainer to OnDangerContainer
    WidgetTone.WARNING -> WarningContainer to OnWarningContainer
    WidgetTone.NEUTRAL -> GlanceTheme.colors.secondaryContainer to GlanceTheme.colors.onSecondaryContainer
}

private val WidgetPageKind.icon: Int
    get() = when (this) {
        WidgetPageKind.FIRES -> R.drawable.widget_ic_fire
        WidgetPageKind.HUMAN -> R.drawable.widget_ic_person
        WidgetPageKind.HOT -> R.drawable.widget_ic_cart
    }

// ── Glance ────────────────────────────────────────────────────────────────────────────────────────

/**
 * El widget «Operador». Lee lo que dejó el vigía en `AmbientStore` (los pushes y el vigía lo mantienen al día) y la
 * pestaña elegida de cada widget. El tamaño exacto decide compacto o grande.
 */
class OperatorWidget : GlanceAppWidget() {
    override val sizeMode: SizeMode = SizeMode.Exact

    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val store = EntryPointAccessors.fromApplication(context, WidgetEntryPoint::class.java).ambientStore()
        provideContent {
            val pages by store.pages.collectAsState(initial = WidgetPages())
            val tab = currentState<Preferences>()[TAB_KEY]
            val selected = WidgetPageKind.entries.firstOrNull { it.name == tab } ?: WidgetPageKind.FIRES
            WidgetContent(pages, selected, System.currentTimeMillis(), ZoneId.systemDefault())
        }
    }
}

/** Tocar una pestaña: queda elegida en ESE widget y se repinta. */
class SelectTabAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        val tab = parameters[TAB_PARAM]?.takeIf { name -> WidgetPageKind.entries.any { it.name == name } } ?: return
        updateAppWidgetState(context, glanceId) { it[TAB_KEY] = tab }
        OperatorWidget().update(context, glanceId)
    }
}

/** «Actualizar»: una vuelta del vigía apenas haya red (repinta el widget al terminar). */
class RefreshAction : ActionCallback {
    override suspend fun onAction(context: Context, glanceId: GlanceId, parameters: ActionParameters) {
        VigiaWorker.runSoon(context)
    }
}

/**
 * Se sigue llamando como la primera versión para que los widgets ya puestos no se rompan. Sin exportar
 * (`ManifestSecurityTest`): el sistema igual le entrega APPWIDGET_UPDATE.
 */
class HotSalesWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = OperatorWidget()
}

@Module
@InstallIn(SingletonComponent::class)
object WidgetModule {
    @Provides fun updater(): HotWidgetUpdater = HotWidgetUpdater { context -> OperatorWidget().updateAll(context) }
}
