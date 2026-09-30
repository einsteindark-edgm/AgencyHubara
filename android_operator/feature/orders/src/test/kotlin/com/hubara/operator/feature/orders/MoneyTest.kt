package com.hubara.operator.feature.orders

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.OrderStage
import org.junit.Test

class MoneyTest {
    @Test fun pesos_colombianos_sin_decimales() {
        assertThat(cop(179800)).isEqualTo("$179.800")
        assertThat(cop(0)).isEqualTo("$0")
        assertThat(cop(1234567)).isEqualTo("$1.234.567")
    }

    @Test fun el_boton_dice_el_paso_siguiente() {
        assertThat(advanceLabel(OrderStage.NEW)).isEqualTo("Empezar a preparar")
        assertThat(advanceLabel(OrderStage.READY)).isEqualTo("Despachar")
        assertThat(advanceLabel(OrderStage.SHIPPING)).isEqualTo("Marcar entregado")
        assertThat(advanceLabel(OrderStage.DELIVERED)).isNull()
    }

    @Test fun el_costo_del_envio_se_valida() {
        assertThat(parseCop("12.000")).isEqualTo(12000L)
        assertThat(parseCop("12000")).isEqualTo(12000L)
        assertThat(parseCop("")).isNull()
        assertThat(parseCop("abc")).isNull()
        assertThat(parseCop("0")).isNull()
    }
}
