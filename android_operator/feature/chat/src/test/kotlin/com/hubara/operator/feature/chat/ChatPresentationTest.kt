package com.hubara.operator.feature.chat

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ChatPresentationTest {
    @Test fun etapas_del_embudo_en_palabras() {
        assertThat(stageLabel("etapa_variantes")).isEqualTo("eligiendo variantes")
        assertThat(stageLabel("etapa_cierre")).isEqualTo("cierre")
        assertThat(stageLabel(null)).isEqualTo("sin datos")
        assertThat(stageLabel("etapa_nueva")).isEqualTo("sin datos")
    }

    // El encabezado mostraba solo el número: con nombre de perfil, el nombre arriba y el número junto a quién atiende.
    @Test fun el_encabezado_muestra_el_nombre_y_el_numero_y_sin_nombre_el_numero() {
        assertThat(chatTitle("Laura Prueba", "570000000000")).isEqualTo("Laura Prueba")
        assertThat(chatSubtitle("Laura Prueba", "570000000000", human = true)).isEqualTo("+57 000 000 0000 · Tú atiendes")
        assertThat(chatTitle(null, "570000000000")).isEqualTo("+57 000 000 0000")
        assertThat(chatSubtitle(null, "570000000000", human = false)).isEqualTo("El bot atiende")
    }

    @Test fun la_paleta_solo_ofrece_acciones_que_no_piden_un_producto() {
        val tools = PALETTE.flatMap { it.second }.map { it.first }
        assertThat(tools).containsExactly(
            "present_products", "request_shipping_details", "send_shipping_rates",
            "present_order_confirmation", "send_payment_methods",
        )
        assertThat(tools).containsNoneOf("present_variant_picker", "present_product_detail", "present_product_gallery")
    }
}
