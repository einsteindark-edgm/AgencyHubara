package com.hubara.operator.widget.hot

import android.appwidget.AppWidgetProviderInfo
import android.content.Context
import android.view.View
import android.widget.FrameLayout
import android.widget.StackView
import android.widget.TextView
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.push.WidgetPage
import com.hubara.operator.core.push.WidgetPageKind
import com.hubara.operator.core.push.WidgetPages
import com.hubara.operator.core.push.WidgetRow
import org.junit.Test
import org.junit.runner.RunWith
import org.xmlpull.v1.XmlPullParser

/**
 * El widget de la pantalla de inicio con tres páginas que se deslizan: ventas calientes, incendios y humano.
 * Lo que se ve de cada página, a dónde lleva cada fila y dónde puede ir (nunca la pantalla bloqueada).
 */
@RunWith(AndroidJUnit4::class)
class OperatorWidgetTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    private val fires = WidgetPage(
        rows = listOf(
            WidgetRow("Sofía pide un humano", "grave · empeora", "hubara://chat/wa_000000000102", urgent = true),
            WidgetRow("El pedido #41 de Andrés va retrasado", "grave", "hubara://order/order_41", urgent = true),
        ),
        total = 5,
    )

    private fun View.text(id: Int) = findViewById<TextView>(id)

    @Test fun cada_pagina_dice_que_es_cuantas_hay_y_en_cual_vas() {
        val view = pageViews(context, WidgetPageKind.FIRES, fires).apply(context, FrameLayout(context))
        assertThat(view.text(R.id.page_title).text.toString()).isEqualTo("Incendios · 5")
        assertThat(view.text(R.id.page_position).text.toString()).isEqualTo("2 de 3")
        assertThat(view.text(R.id.row_0).text.toString()).isEqualTo("Sofía pide un humano · grave · empeora")
        assertThat(view.text(R.id.row_1).visibility).isEqualTo(View.VISIBLE)
        assertThat(view.text(R.id.row_2).visibility).isEqualTo(View.INVISIBLE)
        assertThat(view.text(R.id.page_empty).visibility).isEqualTo(View.GONE)
    }

    @Test fun una_pagina_vacia_lo_dice_y_una_que_nunca_cargo_pide_abrir_la_app() {
        val empty = pageViews(context, WidgetPageKind.HUMAN, WidgetPage()).apply(context, FrameLayout(context))
        assertThat(empty.text(R.id.page_title).text.toString()).isEqualTo("Humano")
        assertThat(empty.text(R.id.page_empty).text.toString()).isEqualTo("Nadie atiende un chat ahora.")
        assertThat(empty.text(R.id.row_0).visibility).isEqualTo(View.INVISIBLE)

        val never = pageViews(context, WidgetPageKind.HOT, null).apply(context, FrameLayout(context))
        assertThat(never.text(R.id.page_empty).text.toString()).isEqualTo("Abre la app para cargar el widget.")
    }

    @Test fun cada_fila_abre_su_chat_con_el_enlace_que_la_app_valida() {
        val intent = rowFillIn(fires.rows.first())
        assertThat(intent.dataString).isEqualTo("hubara://chat/wa_000000000102")
        // La plantilla es explícita a la actividad de la app: la fila solo pone el enlace.
        val template = openTemplate(context)
        assertThat(template.`package`).isEqualTo(context.packageName)
    }

    @Test fun el_widget_es_una_pila_de_tres_paginas_que_se_desliza() {
        assertThat(pageOrder(WidgetPages(fires = fires)).map { it.first })
            .containsExactly(WidgetPageKind.HOT, WidgetPageKind.FIRES, WidgetPageKind.HUMAN).inOrder()
        val root = widgetViews(context, appWidgetId = 7, WidgetPages(fires = fires)).apply(context, FrameLayout(context))
        assertThat(root.findViewById<View>(R.id.pages)).isInstanceOf(StackView::class.java)
    }

    @Test fun solo_va_en_la_pantalla_de_inicio_nunca_en_la_bloqueada() {
        val xml = context.resources.getXml(R.xml.hot_sales_widget)
        while (xml.next() != XmlPullParser.START_TAG) Unit
        val category = xml.getAttributeIntValue("http://schemas.android.com/apk/res/android", "widgetCategory", -1)
        assertThat(category).isEqualTo(AppWidgetProviderInfo.WIDGET_CATEGORY_HOME_SCREEN)
    }
}
