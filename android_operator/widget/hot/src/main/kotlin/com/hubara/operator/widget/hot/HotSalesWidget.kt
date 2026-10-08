package com.hubara.operator.widget.hot

import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.view.View
import android.widget.RemoteViews
import androidx.core.net.toUri
import androidx.core.widget.RemoteViewsCompat
import com.hubara.operator.core.push.AmbientStore
import com.hubara.operator.core.push.HotWidgetUpdater
import com.hubara.operator.core.push.WidgetPage
import com.hubara.operator.core.push.WidgetPageKind
import com.hubara.operator.core.push.WidgetPages
import com.hubara.operator.core.push.WidgetRow
import dagger.Module
import dagger.Provides
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

@EntryPoint
@InstallIn(SingletonComponent::class)
interface WidgetEntryPoint {
    fun ambientStore(): AmbientStore
}

private val ROWS = intArrayOf(R.id.row_0, R.id.row_1, R.id.row_2)

/** Las páginas en el orden en que se deslizan. */
fun pageOrder(pages: WidgetPages): List<Pair<WidgetPageKind, WidgetPage?>> = WidgetPageKind.entries.map { it to pages[it] }

/** El enlace de una fila: la plantilla de la pila (explícita a la app) solo recibe el `hubara://` de esa fila. */
fun rowFillIn(row: WidgetRow): Intent = Intent().apply { row.link?.let { data = it.toUri() } }

/**
 * El intent base de todas las filas: explícito a la actividad de inicio de la app, nunca implícito (skill
 * android-intent-security). El enlace llega por la fila y la app lo vuelve a validar (`DeepLinks.parse`).
 */
fun openTemplate(context: Context): Intent {
    val launch = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: Intent(Intent.ACTION_MAIN)
    return Intent(launch).setPackage(context.packageName)
        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
}

/**
 * Una página: título con cuántas hay, hasta 3 filas (sin mensajes) y en cuál de las 3 va. Una página null nunca
 * cargó: pide abrir la app (recién instalada o tras cerrar sesión).
 */
fun pageViews(context: Context, kind: WidgetPageKind, page: WidgetPage?): RemoteViews {
    val views = RemoteViews(context.packageName, R.layout.widget_page)
    val count = page?.total?.takeIf { it > 0 }
    views.setTextViewText(R.id.page_title, if (count != null) "${kind.title} · $count" else kind.title)
    views.setTextViewText(R.id.page_position, "${kind.ordinal + 1} de ${WidgetPageKind.entries.size}")
    val rows = page?.rows.orEmpty()
    val empty = when {
        page == null -> context.getString(R.string.widget_open_app)
        rows.isEmpty() -> kind.empty
        else -> null
    }
    views.setViewVisibility(R.id.page_empty, if (empty != null) View.VISIBLE else View.GONE)
    views.setTextViewText(R.id.page_empty, empty.orEmpty())
    ROWS.forEachIndexed { i, id ->
        val row = rows.getOrNull(i)
        // Invisible y no fuera: la tarjeta mide lo mismo con 0 o 3 filas.
        views.setViewVisibility(id, if (row != null) View.VISIBLE else View.INVISIBLE)
        if (row != null) {
            views.setTextViewText(id, row.line)
            views.setTextColor(id, context.getColor(if (row.urgent) R.color.widget_urgent else R.color.widget_on_surface))
            if (row.link != null) views.setOnClickFillInIntent(id, rowFillIn(row))
        }
    }
    return views
}

/** El widget entero: la pila con las tres páginas adentro (sin servicio propio) y la plantilla de los toques. */
fun widgetViews(context: Context, appWidgetId: Int, pages: WidgetPages): RemoteViews {
    val root = RemoteViews(context.packageName, R.layout.widget_pages)
    val items = RemoteViewsCompat.RemoteCollectionItems.Builder()
        .setHasStableIds(true)
        .setViewTypeCount(1)
        .apply { pageOrder(pages).forEach { (kind, page) -> addItem(kind.ordinal.toLong(), pageViews(context, kind, page)) } }
        .build()
    RemoteViewsCompat.setRemoteAdapter(context, root, appWidgetId, R.id.pages, items)
    root.setEmptyView(R.id.pages, R.id.pages_empty)
    // MUTABLE solo para que cada fila ponga su enlace (fill-in); el destino es explícito y no se puede cambiar.
    val template = PendingIntent.getActivity(
        context, 0, openTemplate(context), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
    )
    root.setPendingIntentTemplate(R.id.pages, template)
    return root
}

/** Pinta todos los widgets puestos con lo último que dejó el vigía (o nada, tras cerrar sesión). */
suspend fun renderAll(context: Context) {
    val manager = AppWidgetManager.getInstance(context)
    val ids = manager.getAppWidgetIds(ComponentName(context, HotSalesWidgetReceiver::class.java))
    if (ids.isEmpty()) return
    val pages = EntryPointAccessors.fromApplication(context, WidgetEntryPoint::class.java).ambientStore().pages.first()
    ids.forEach { id -> manager.updateAppWidget(id, widgetViews(context, id, pages)) }
}

/**
 * El widget «Operador»: ventas calientes, incendios y humano en una pila que se desliza. Se llama como antes para que
 * los widgets ya puestos sigan andando. Sin exportar (`ManifestSecurityTest`): el sistema igual le entrega
 * APPWIDGET_UPDATE. Los datos los trae el vigía (y los pushes) a `AmbientStore`; acá solo se pintan.
 */
class HotSalesWidgetReceiver : AppWidgetProvider() {
    override fun onUpdate(context: Context, appWidgetManager: AppWidgetManager, appWidgetIds: IntArray) {
        val pending = goAsync()
        CoroutineScope(Dispatchers.Default).launch {
            try {
                renderAll(context.applicationContext)
            } finally {
                pending.finish()
            }
        }
    }
}

@Module
@InstallIn(SingletonComponent::class)
object WidgetModule {
    @Provides fun updater(): HotWidgetUpdater = HotWidgetUpdater { context -> renderAll(context) }
}
