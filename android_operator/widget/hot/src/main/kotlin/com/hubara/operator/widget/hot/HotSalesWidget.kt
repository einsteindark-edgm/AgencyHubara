package com.hubara.operator.widget.hot

import android.content.Context
import android.content.Intent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import androidx.glance.GlanceId
import androidx.glance.GlanceModifier
import androidx.glance.GlanceTheme
import androidx.glance.action.clickable
import androidx.glance.appwidget.GlanceAppWidget
import androidx.glance.appwidget.GlanceAppWidgetReceiver
import androidx.glance.appwidget.action.actionStartActivity
import androidx.glance.appwidget.provideContent
import androidx.glance.appwidget.updateAll
import androidx.glance.background
import androidx.glance.layout.Column
import androidx.glance.layout.fillMaxSize
import androidx.glance.layout.fillMaxWidth
import androidx.glance.layout.padding
import androidx.glance.text.FontWeight
import androidx.glance.text.Text
import androidx.glance.text.TextStyle
import com.hubara.operator.core.network.dto.HotSaleDto
import com.hubara.operator.core.push.AmbientStore
import com.hubara.operator.core.push.HotWidgetUpdater
import dagger.Module
import dagger.Provides
import dagger.hilt.EntryPoint
import dagger.hilt.InstallIn
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.components.SingletonComponent

@EntryPoint
@InstallIn(SingletonComponent::class)
interface WidgetEntryPoint {
    fun ambientStore(): AmbientStore
}

/** Etapa del embudo en palabras cortas, para el widget. */
fun widgetStage(stage: String?): String = when (stage) {
    "etapa_datos_envio" -> "datos de envío"
    "etapa_cierre" -> "cierre"
    else -> "en curso"
}

fun widgetLine(row: HotSaleDto): String = listOfNotNull(
    row.name?.takeIf { it.isNotBlank() } ?: "Cliente",
    widgetStage(row.stage),
    row.product,
    if (row.risk) "RIESGO" else null,
).joinToString(" · ")

/**
 * Ventas calientes en la pantalla de inicio (Glance funciona en el Android 11 de hoy). Lee lo que dejó el
 * vigía en DataStore; cada fila abre esa conversación en vivo. No muestra mensajes.
 */
class HotSalesWidget : GlanceAppWidget() {
    override suspend fun provideGlance(context: Context, id: GlanceId) {
        val store = EntryPointAccessors.fromApplication(context, WidgetEntryPoint::class.java).ambientStore()
        provideContent {
            val rows by store.hot.collectAsState(initial = emptyList())
            GlanceTheme { Content(context, rows) }
        }
    }

    @Composable
    private fun Content(context: Context, rows: List<HotSaleDto>) {
        Column(GlanceModifier.fillMaxSize().background(GlanceTheme.colors.widgetBackground).padding(12.dp)) {
            Text("Ventas calientes", style = TextStyle(fontWeight = FontWeight.Bold, color = GlanceTheme.colors.onSurface))
            if (rows.isEmpty()) {
                Text("Nada por cerrar ahora.", style = TextStyle(color = GlanceTheme.colors.onSurfaceVariant))
            }
            rows.take(AmbientStore.MAX_WIDGET_ROWS).forEach { row ->
                Text(
                    widgetLine(row),
                    style = TextStyle(color = GlanceTheme.colors.onSurface),
                    maxLines = 1,
                    modifier = GlanceModifier.fillMaxWidth().padding(vertical = 6.dp)
                        .clickable(actionStartActivity(liveIntent(context, row.sessionId))),
                )
            }
        }
    }
}

/**
 * Intent explícito a la actividad de inicio con el link validable `hubara://live/{id}`. Siempre con el paquete
 * propio: nunca un intent implícito que otra app pudiera atender (skill android-intent-security).
 */
fun liveIntent(context: Context, sessionId: String): Intent {
    val launch = context.packageManager.getLaunchIntentForPackage(context.packageName) ?: Intent(Intent.ACTION_MAIN)
    return Intent(launch).setPackage(context.packageName).setData("hubara://live/$sessionId".toUri())
        .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP)
}

class HotSalesWidgetReceiver : GlanceAppWidgetReceiver() {
    override val glanceAppWidget: GlanceAppWidget = HotSalesWidget()
}

@Module
@InstallIn(SingletonComponent::class)
object WidgetModule {
    @Provides fun updater(): HotWidgetUpdater = HotWidgetUpdater { context -> HotSalesWidget().updateAll(context) }
}
