package com.hubara.operator.widget.hot

import android.appwidget.AppWidgetProviderInfo
import android.content.Context
import androidx.compose.ui.unit.DpSize
import androidx.compose.ui.unit.dp
import androidx.glance.action.actionParametersOf
import androidx.glance.appwidget.testing.unit.assertHasRunCallbackClickAction
import androidx.glance.appwidget.testing.unit.hasStartActivityClickAction
import androidx.glance.appwidget.testing.unit.runGlanceAppWidgetUnitTest
import androidx.glance.testing.unit.hasContentDescription
import androidx.glance.testing.unit.hasText
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.push.WidgetPage
import com.hubara.operator.core.push.WidgetPageKind
import com.hubara.operator.core.push.WidgetPages
import com.hubara.operator.core.push.WidgetRow
import com.hubara.operator.core.push.WidgetSince
import com.hubara.operator.core.push.WidgetTone
import java.time.ZoneId
import org.junit.Test
import org.junit.runner.RunWith
import org.xmlpull.v1.XmlPullParser

/**
 * El widget de la pantalla de inicio (segunda versión, 2026-10-08): grande, pestañas Incendios · Humano · Ventas con
 * su total y la lista de la elegida; compacto (2×2), solo los tres contadores. Cada fila abre su chat y cada contador
 * su pestaña de la app.
 */
@RunWith(AndroidJUnit4::class)
class OperatorWidgetTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    private val sofia = WidgetRow(
        "Sofía pide un humano", "4 mensajes · empeora", "hubara://chat/wa_000000000102",
        initials = "SP", tone = WidgetTone.DANGER, tag = "Grave", since = WidgetSince("último", NOW - 120_000),
    )
    private val andres = WidgetRow(
        "El pedido #41 de Andrés va retrasado", "4 días de retraso", "hubara://order/order_41",
        tone = WidgetTone.DANGER, tag = "Grave",
    )
    private val daniela = WidgetRow("Daniela", "Al día", "hubara://chat/wa_000000000106", initials = "D")
    private val pages = WidgetPages(
        fires = WidgetPage(listOf(sofia, andres), total = 3, updatedMs = NOW),
        human = WidgetPage(listOf(daniela), total = 3, updatedMs = NOW),
    )

    @Test fun grande_las_pestanas_dicen_cuantos_hay_y_la_elegida_muestra_sus_filas() = runGlanceAppWidgetUnitTest {
        setAppWidgetSize(DpSize(320.dp, 240.dp))
        setContext(context)
        provideComposable { WidgetContent(pages, WidgetPageKind.FIRES, NOW, ZONE) }

        onNode(hasText("Incendios 3")).assertExists()
        onNode(hasText("Humano 3")).assertHasRunCallbackClickAction<SelectTabAction>(actionParametersOf(TAB_PARAM to "HUMAN"))
        onNode(hasText("Ventas")).assertExists()  // nunca cargó: sin número
        onNode(hasText("Sofía pide un humano")).assertExists()
        onNode(hasText("SP")).assertExists()
        onNode(hasText("4 mensajes · empeora · último 9:11 a. m.")).assertExists()
        onAllNodes(hasText("Grave")).assertCountEquals(2)
        onNode(hasText("Daniela")).assertDoesNotExist()  // es de otra pestaña
        onNode(hasText("Actualizado 9:13 a. m.")).assertExists()
        onNode(hasContentDescription("Actualizar")).assertHasRunCallbackClickAction<RefreshAction>()
    }

    @Test fun cada_fila_abre_su_chat_y_un_pedido_sin_chat_su_ficha() = runGlanceAppWidgetUnitTest {
        setAppWidgetSize(DpSize(320.dp, 240.dp))
        setContext(context)
        provideComposable { WidgetContent(pages, WidgetPageKind.FIRES, NOW, ZONE) }

        onAllNodes(hasStartActivityClickAction(openIntent(context, "hubara://chat/wa_000000000102"))).assertCountEquals(1)
        onAllNodes(hasStartActivityClickAction(openIntent(context, "hubara://order/order_41"))).assertCountEquals(1)
    }

    @Test fun una_pestana_vacia_lo_dice_y_una_que_nunca_cargo_pide_abrir_la_app() = runGlanceAppWidgetUnitTest {
        setAppWidgetSize(DpSize(320.dp, 240.dp))
        setContext(context)
        provideComposable { WidgetContent(WidgetPages(human = WidgetPage(updatedMs = NOW)), WidgetPageKind.HUMAN, NOW, ZONE) }
        onNode(hasText("Nadie atiende un chat ahora.")).assertExists()
    }

    @Test fun nunca_cargo_pide_abrir_la_app() = runGlanceAppWidgetUnitTest {
        setAppWidgetSize(DpSize(320.dp, 240.dp))
        setContext(context)
        provideComposable { WidgetContent(WidgetPages(), WidgetPageKind.FIRES, NOW, ZONE) }
        onNode(hasText("Abre la app para cargar el widget.")).assertExists()
    }

    @Test fun compacto_2x2_solo_los_contadores_y_cada_uno_abre_su_pestana() = runGlanceAppWidgetUnitTest {
        setAppWidgetSize(DpSize(150.dp, 150.dp))
        setContext(context)
        provideComposable { WidgetContent(pages, WidgetPageKind.FIRES, NOW, ZONE) }

        onNode(hasText("Incendios")).assertExists()
        onNode(hasText("Humano")).assertExists()
        onAllNodes(hasText("3")).assertCountEquals(2)
        onNode(hasText("Sofía pide un humano")).assertDoesNotExist()
        onAllNodes(hasStartActivityClickAction(openIntent(context, "hubara://tab/incendios"))).assertCountEquals(1)
        onAllNodes(hasStartActivityClickAction(openIntent(context, "hubara://tab/chats"))).assertCountEquals(2)
    }

    @Test fun solo_va_en_la_pantalla_de_inicio_nunca_en_la_bloqueada() {
        val xml = context.resources.getXml(R.xml.hot_sales_widget)
        while (xml.next() != XmlPullParser.START_TAG) Unit
        val category = xml.getAttributeIntValue("http://schemas.android.com/apk/res/android", "widgetCategory", -1)
        assertThat(category).isEqualTo(AppWidgetProviderInfo.WIDGET_CATEGORY_HOME_SCREEN)
    }

    private companion object {
        const val NOW = 1_790_000_000_000L  // 21-sep-2026 9:13 a. m. en Bogotá
        val ZONE: ZoneId = ZoneId.of("America/Bogota")
    }
}
