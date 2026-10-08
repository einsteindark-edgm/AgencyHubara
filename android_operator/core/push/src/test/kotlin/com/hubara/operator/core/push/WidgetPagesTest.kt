package com.hubara.operator.core.push

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.model.FireId
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.network.dto.HotSaleDto
import com.hubara.operator.core.network.dto.HumanChatDto
import com.hubara.operator.core.network.dto.HumanDto
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.Test
import org.junit.runner.RunWith

/**
 * Lo que muestra el widget de la pantalla de inicio (2026-10-08, segunda versión): pestañas Incendios · Humano · Ventas
 * con su total, y filas con iniciales, una etiqueta de color y una hora FIJA («desde 10:42»), que no se pone vieja como
 * «hace 12 min». Hasta 10 filas por página (la lista se desplaza); ninguna lleva mensajes.
 */
@RunWith(AndroidJUnit4::class)
class WidgetPagesTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test fun las_pestanas_van_incendios_humano_ventas_y_cada_una_abre_su_lugar_en_la_app() {
        assertThat(WidgetPageKind.entries.map { it.title }).containsExactly("Incendios", "Humano", "Ventas").inOrder()
        assertThat(WidgetPageKind.FIRES.link).isEqualTo("hubara://tab/incendios")
        assertThat(WidgetPageKind.HUMAN.link).isEqualTo("hubara://tab/chats")
    }

    @Test fun incendios_graves_primero_con_el_nombre_del_cliente_y_sin_la_espera_relativa() {
        val page = firesPage(
            listOf(
                fire("order:o42", Severity.HOY, "Verificar el pago de Daniela", "Pedido #42 · $95.000",
                    FireSubject.Order(OrderId.parse("order_42"), SessionId.parse("wa_000000000106"))),
                fire("chat:wa_000000000102", Severity.GRAVE, "Sofía pide un humano", "12 min sin respuesta · 4 mensajes",
                    FireSubject.Chat(SessionId.parse("wa_000000000102")!!), worse = true),
                fire("order:o41", Severity.GRAVE, "El pedido #41 de Andrés va retrasado", "4 días de retraso",
                    FireSubject.Order(OrderId.parse("order_41"), null)),
            ),
            names = mapOf("wa_000000000102" to "Sofía Prueba", "wa_000000000106" to "Daniela Prueba"),
            nowMs = NOW,
        )
        assertThat(page.total).isEqualTo(3)
        assertThat(page.updatedMs).isEqualTo(NOW)
        assertThat(page.rows.map { it.title }).containsExactly(
            "Sofía pide un humano", "El pedido #41 de Andrés va retrasado", "Verificar el pago de Daniela",
        ).inOrder()
        assertThat(page.rows[0]).isEqualTo(WidgetRow(
            title = "Sofía pide un humano", detail = "4 mensajes · empeora", link = "hubara://chat/wa_000000000102",
            initials = "SP", tone = WidgetTone.DANGER, tag = "Grave", since = WidgetSince("último", UPDATED),
        ))
        // Un pedido sin chat conocido abre su ficha y no tiene a quién poner iniciales.
        assertThat(page.rows[1].link).isEqualTo("hubara://order/order_41")
        assertThat(page.rows[1].initials).isNull()
        assertThat(page.rows[2].tone).isEqualTo(WidgetTone.WARNING)
        assertThat(page.rows[2].tag).isEqualTo("Hoy")
        assertThat(page.rows.joinToString { it.detail }).doesNotContain("sin respuesta")
    }

    @Test fun humano_pone_primero_a_quien_espera_con_la_hora_desde_que_espera() {
        val page = humanPage(
            HumanDto(total = 12, human = listOf(
                HumanChatDto("wa_000000000102", name = "Sofía", unanswered = 2, waitingSinceMs = NOW - 600_000, lastInboundMs = NOW - 60_000),
                HumanChatDto("wa_000000000101", name = null, unanswered = 0, lastInboundMs = NOW - 3_600_000),
            )),
            nowMs = NOW,
        )
        assertThat(page.total).isEqualTo(12)
        assertThat(page.rows[0]).isEqualTo(WidgetRow(
            title = "Sofía", detail = "2 sin responder", link = "hubara://chat/wa_000000000102",
            initials = "S", tone = WidgetTone.DANGER, tag = "Espera", since = WidgetSince("desde", NOW - 600_000),
        ))
        assertThat(page.rows[1]).isEqualTo(WidgetRow(
            title = "Cliente", detail = "Al día", link = "hubara://chat/wa_000000000101",
            initials = null, since = WidgetSince("último", NOW - 3_600_000),
        ))
    }

    @Test fun ventas_dicen_etapa_producto_y_riesgo_y_la_lista_llega_hasta_diez() {
        val many = List(14) { i -> HotSaleDto("wa_0000000002%02d".format(i), name = "Cliente $i", stage = "etapa_datos_envio", updatedMs = NOW) }
        val page = hotPage(listOf(HotSaleDto("wa_000000000103", name = "Camilo", stage = "etapa_cierre", product = "Duo Zodiacal azul × 2", risk = true, updatedMs = NOW)) + many, nowMs = NOW)
        assertThat(page.total).isEqualTo(15)
        assertThat(page.rows).hasSize(MAX_WIDGET_ROWS)
        assertThat(MAX_WIDGET_ROWS).isEqualTo(10)
        assertThat(page.rows.first()).isEqualTo(WidgetRow(
            title = "Camilo", detail = "cierre · Duo Zodiacal azul × 2", link = "hubara://chat/wa_000000000103",
            initials = "C", tone = WidgetTone.WARNING, tag = "Riesgo", since = WidgetSince("último", NOW),
        ))
        assertThat(page.rows[1].detail).isEqualTo("datos de envío")
        assertThat(page.rows[1].tag).isNull()
    }

    @Test fun el_almacen_guarda_cada_pagina_por_separado_y_cerrar_sesion_las_borra() = runTest {
        val store = AmbientStore(context)
        store.clear()
        assertThat(store.pages.first()).isEqualTo(WidgetPages())  // nunca cargó: el widget pide abrir la app
        store.savePage(WidgetPageKind.HOT, hotPage(listOf(HotSaleDto("wa_000000000103", name = "Camilo", stage = "etapa_cierre")), NOW))
        store.savePage(WidgetPageKind.HUMAN, humanPage(HumanDto(total = 1, human = listOf(HumanChatDto("wa_000000000102", name = "Sofía", unanswered = 2))), NOW))
        val pages = store.pages.first()
        assertThat(pages.hot?.rows?.single()?.title).isEqualTo("Camilo")
        assertThat(pages[WidgetPageKind.HUMAN]?.rows?.single()?.title).isEqualTo("Sofía")
        assertThat(pages.fires).isNull()
        store.clear()
        assertThat(store.pages.first()).isEqualTo(WidgetPages())
    }

    private fun fire(id: String, severity: Severity, title: String, subtitle: String, subject: FireSubject, worse: Boolean = false) = Fire(
        id = FireId.parse(id)!!, subject = subject, severity = severity, kind = FireKind.OTHER, gettingWorse = worse,
        title = title, subtitle = subtitle, primaryAction = ActionRef("open_chat"), decidedBy = "rules", updatedMs = UPDATED,
    )

    private companion object {
        const val NOW = 1_790_000_000_000L
        const val UPDATED = NOW - 120_000
    }
}
