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
 * El widget de la pantalla de inicio pasa de «Ventas calientes» a tres páginas deslizables (2026-10-08): ventas
 * calientes, incendios (los graves primero) y los chats que atiende una persona. Máximo 3 filas por página, cada una
 * abre su chat y ninguna lleva mensajes.
 */
@RunWith(AndroidJUnit4::class)
class WidgetPagesTest {
    private val context = ApplicationProvider.getApplicationContext<Context>()

    @Test fun ventas_calientes_dicen_quien_etapa_producto_y_riesgo_y_abren_su_chat() {
        val page = hotPage(List(5) { i -> HotSaleDto("wa_00000000010$i", name = "Cliente $i", stage = "etapa_datos_envio") }
            .let { listOf(HotSaleDto("wa_000000000103", name = "Camilo", stage = "etapa_cierre", product = "Duo Zodiacal azul × 2", risk = true)) + it })
        assertThat(page.total).isEqualTo(6)
        assertThat(page.rows).hasSize(3)
        assertThat(page.rows.first()).isEqualTo(
            WidgetRow("Camilo", "cierre · Duo Zodiacal azul × 2 · RIESGO", "hubara://chat/wa_000000000103", urgent = true),
        )
        assertThat(hotPage(listOf(HotSaleDto("wa_test_x", stage = "etapa_datos_envio"))).rows.single().line).isEqualTo("Cliente · datos de envío")
    }

    @Test fun incendios_van_graves_primero_sin_la_espera_que_se_vuelve_vieja() {
        val page = firesPage(listOf(
            fire("order:o41", Severity.HOY, "Verificar el pago de Daniela", FireSubject.Order(OrderId.parse("order_42"), SessionId.parse("wa_000000000106"))),
            fire("chat:wa_000000000102", Severity.GRAVE, "Sofía pide un humano", FireSubject.Chat(SessionId.parse("wa_000000000102")!!), worse = true),
            fire("order:o40", Severity.GRAVE, "El pedido #41 de Andrés va retrasado", FireSubject.Order(OrderId.parse("order_41"), null)),
        ))
        assertThat(page.rows.map { it.title }).containsExactly(
            "Sofía pide un humano", "El pedido #41 de Andrés va retrasado", "Verificar el pago de Daniela",
        ).inOrder()
        assertThat(page.rows[0]).isEqualTo(WidgetRow("Sofía pide un humano", "grave · empeora", "hubara://chat/wa_000000000102", urgent = true))
        // Sin chat conocido, abre el pedido; con chat, el chat.
        assertThat(page.rows[1].link).isEqualTo("hubara://order/order_41")
        assertThat(page.rows[2].link).isEqualTo("hubara://chat/wa_000000000106")
        assertThat(page.rows.joinToString { it.line }).doesNotContain("sin respuesta")
    }

    @Test fun humano_pone_primero_a_quien_espera_y_cuenta_sin_responder() {
        val page = humanPage(HumanDto(total = 4, human = listOf(
            HumanChatDto("wa_000000000102", name = "Sofía", unanswered = 2),
            HumanChatDto("wa_000000000105", name = null, unanswered = 1),
            HumanChatDto("wa_000000000101", name = "Laura", unanswered = 0),
            HumanChatDto("wa_000000000109", name = "Otra", unanswered = 0),
        )))
        assertThat(page.total).isEqualTo(4)
        assertThat(page.rows.map { it.line }).containsExactly("Sofía · 2 sin responder", "Cliente · 1 sin responder", "Laura · al día").inOrder()
        assertThat(page.rows.map { it.urgent }).containsExactly(true, true, false).inOrder()
        assertThat(page.rows.first().link).isEqualTo("hubara://chat/wa_000000000102")
    }

    @Test fun el_almacen_guarda_cada_pagina_por_separado_y_cerrar_sesion_las_borra() = runTest {
        val store = AmbientStore(context)
        store.clear()
        assertThat(store.pages.first()).isEqualTo(WidgetPages())  // nunca cargó: el widget pide abrir la app
        store.savePage(WidgetPageKind.HOT, hotPage(listOf(HotSaleDto("wa_000000000103", name = "Camilo", stage = "etapa_cierre"))))
        store.savePage(WidgetPageKind.HUMAN, humanPage(HumanDto(total = 1, human = listOf(HumanChatDto("wa_000000000102", name = "Sofía", unanswered = 2)))))
        val pages = store.pages.first()
        assertThat(pages.hot?.rows?.single()?.title).isEqualTo("Camilo")
        assertThat(pages.human?.rows?.single()?.title).isEqualTo("Sofía")
        assertThat(pages.fires).isNull()
        store.clear()
        assertThat(store.pages.first()).isEqualTo(WidgetPages())
    }

    private fun fire(id: String, severity: Severity, title: String, subject: FireSubject, worse: Boolean = false) = Fire(
        id = FireId.parse(id)!!, subject = subject, severity = severity, kind = FireKind.OTHER, gettingWorse = worse,
        title = title, subtitle = "12 min sin respuesta · 4 mensajes", primaryAction = ActionRef("open_chat"),
        decidedBy = "rules", updatedMs = 1_790_000_000_000L,
    )
}
