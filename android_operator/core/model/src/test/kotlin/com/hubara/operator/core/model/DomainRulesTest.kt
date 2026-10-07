package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class DomainRulesTest {

    private fun fire(id: String, severity: Severity, worse: Boolean = false, updated: Long = 0) = Fire(
        id = FireId.parse(id)!!,
        subject = FireSubject.Chat(SessionId.parse("wa_test_x")!!),
        severity = severity, kind = FireKind.OTHER, gettingWorse = worse,
        title = id, subtitle = "", primaryAction = ActionRef("open_chat"), decidedBy = "rules", updatedMs = updated,
    )

    @Test fun el_radar_muestra_solo_lo_grave_no_oculto_y_primero_lo_que_empeora() {
        val a = fire("chat:wa_a", Severity.GRAVE, worse = false, updated = 1)
        val b = fire("chat:wa_b", Severity.GRAVE, worse = true, updated = 5)
        val c = fire("chat:wa_c", Severity.HOY)
        val d = fire("chat:wa_d", Severity.GRAVE)
        val radar = FireOrdering.radar(listOf(a, b, c, d), hidden = setOf(d.id))
        assertThat(radar.map { it.title }).containsExactly("chat:wa_b", "chat:wa_a").inOrder()
    }

    @Test fun el_feed_ordena_por_gravedad_y_despues_por_antiguedad() {
        val viejo = fire("chat:wa_v", Severity.HOY, updated = 1)
        val nuevo = fire("chat:wa_n", Severity.HOY, updated = 9)
        val grave = fire("chat:wa_g", Severity.GRAVE, updated = 99)
        assertThat(listOf(nuevo, viejo, grave).sortedWith(FireOrdering.FEED).map { it.title })
            .containsExactly("chat:wa_g", "chat:wa_v", "chat:wa_n").inOrder()
    }

    @Test fun cada_etapa_tiene_un_solo_paso_siguiente_y_sus_datos() {
        assertThat(OrderStage.NEW.next()).isEqualTo(OrderStage.PREPARING)
        assertThat(OrderStage.READY.next()).isEqualTo(OrderStage.SHIPPING)
        assertThat(OrderStage.DELIVERED.next()).isNull()
        assertThat(OrderStage.CANCELLED.next()).isNull()
        assertThat(OrderStage.READY.requires).containsExactly(StageInput.PHOTO)
        assertThat(OrderStage.SHIPPING.requires).containsExactly(StageInput.TRACKING_URL, StageInput.SHIPPING_COST)
    }

    @Test fun una_respuesta_vieja_de_burbujas_no_pisa_la_nueva() {
        val sid = SessionId.parse("wa_test_x")!!
        val v2 = SuggestionSet(sid, 2, "rules", null, true, true, emptyList())
        val v1 = v2.copy(version = 1, decidedBy = "vieja")
        assertThat(v2.newerOrSame(v1)).isSameInstanceAs(v2)
        assertThat(v1.newerOrSame(v2)).isSameInstanceAs(v2)
    }

    @Test fun ventana_de_24h() {
        val base = ChatDetail(SessionId.parse("wa_test_x")!!, "", Route.HUMAN, "", 1_000, null, emptyList())
        assertThat(base.windowOpen(nowMs = 999)).isTrue()
        assertThat(base.windowOpen(nowMs = 1_001)).isFalse()
        assertThat(base.copy(windowExpiresAtMs = null).windowOpen(nowMs = 5)).isTrue()
    }
}
