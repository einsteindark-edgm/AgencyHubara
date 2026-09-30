package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SeenCountsTest {
    private fun chat(id: String, inbound: Int) =
        Conversation(SessionId.parse(id)!!, "", "", Route.BOT, 0, null, inbound, null)

    @Test fun la_primera_bandeja_cuenta_como_vista() {
        val s = SeenCounts().withBaseline(listOf(chat("wa_a", 5), chat("wa_b", 2)))
        assertThat(s.baseline).isTrue()
        assertThat(s.unseen(chat("wa_a", 5))).isEqualTo(0)
        assertThat(s.unseen(chat("wa_b", 2))).isEqualTo(0)
        // Lo que llega después sí cuenta.
        assertThat(s.unseen(chat("wa_a", 7))).isEqualTo(2)
    }

    @Test fun un_chat_nuevo_despues_de_la_linea_base_cuenta_todos_sus_mensajes() {
        val s = SeenCounts().withBaseline(listOf(chat("wa_a", 5)))
        assertThat(s.withBaseline(listOf(chat("wa_a", 5), chat("wa_c", 3))).unseen(chat("wa_c", 3))).isEqualTo(3)
    }

    @Test fun abrir_el_chat_lo_deja_en_cero_hasta_que_el_cliente_escriba() {
        val s = SeenCounts().withBaseline(listOf(chat("wa_a", 5))).markSeen(SessionId.parse("wa_a")!!, 7)
        assertThat(s.unseen(chat("wa_a", 7))).isEqualTo(0)
        assertThat(s.unseen(chat("wa_a", 9))).isEqualTo(2)
    }

    @Test fun sin_cambios_devuelve_el_mismo_estado_y_una_bandeja_vacia_no_fija_la_base() {
        assertThat(SeenCounts().withBaseline(emptyList()).baseline).isFalse()
        val s = SeenCounts().withBaseline(listOf(chat("wa_a", 5)))
        assertThat(s.withBaseline(listOf(chat("wa_a", 6)))).isSameInstanceAs(s)
        assertThat(s.markSeen(SessionId.parse("wa_a")!!, 5)).isSameInstanceAs(s)
    }
}
