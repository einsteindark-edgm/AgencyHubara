package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class MessageCardTest {
    @Test fun el_icono_sale_de_la_llave_que_manda_el_servidor() {
        assertThat(CardIcon.fromKey("catalog")).isEqualTo(CardIcon.CATALOG)
        assertThat(CardIcon.fromKey("order")).isEqualTo(CardIcon.ORDER)
        assertThat(CardIcon.fromKey("form")).isEqualTo(CardIcon.FORM)
    }

    @Test fun una_llave_que_la_app_no_conoce_se_ve_como_mensaje() {
        assertThat(CardIcon.fromKey("holograma")).isEqualTo(CardIcon.MESSAGE)
        assertThat(CardIcon.fromKey(null)).isEqualTo(CardIcon.MESSAGE)
    }

    // El cliente vio «Hubara» en negrita, no «*Hubara*» (caso 2026-10-13).
    @Test fun la_negrita_de_whatsapp_se_ve_en_negrita_sin_asteriscos() {
        assertThat(WaFormat.spans("bienvenido a *Hubara*, velas")).containsExactly(
            WaSpan("bienvenido a "), WaSpan("Hubara", bold = true), WaSpan(", velas"),
        ).inOrder()
    }

    @Test fun cursiva_y_tachado() {
        assertThat(WaFormat.spans("_hecho a mano_ y ~\$30.000~")).containsExactly(
            WaSpan("hecho a mano", italic = true), WaSpan(" y "), WaSpan("\$30.000", strike = true),
        ).inOrder()
    }

    @Test fun un_asterisco_suelto_un_formato_partido_o_dentro_de_una_palabra_quedan_como_estan() {
        assertThat(WaFormat.spans("2 * 3 = 6")).containsExactly(WaSpan("2 * 3 = 6"))
        assertThat(WaFormat.spans("*a\nb*")).containsExactly(WaSpan("*a\nb*"))
        assertThat(WaFormat.spans("nombre_apellido@correo.com")).containsExactly(WaSpan("nombre_apellido@correo.com"))
    }
}
