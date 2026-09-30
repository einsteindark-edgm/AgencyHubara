package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class ShippingFormTest {
    // Artemis lo vio en el chat de Camilo: el operador leía `receiver_name=…; payment_method=…` crudo.
    @Test fun el_formulario_de_envio_se_lee_como_datos_y_no_como_codigo() {
        val form = ShippingForm.parse(
            "[datos de envío recibidos] receiver_name=Camilo Prueba; phone=3000000000; city=Medellín; " +
                "neighborhood=Laureles; address=Calle Falsa # 12-34; payment_method=Nequi; flow_token=abc",
        )
        assertThat(form).isEqualTo(
            ShippingForm(
                receiverName = "Camilo Prueba", phone = "3000000000", city = "Medellín",
                neighborhood = "Laureles", address = "Calle Falsa # 12-34", paymentMethod = "Nequi",
            ),
        )
    }

    @Test fun un_mensaje_normal_no_es_formulario() {
        assertThat(ShippingForm.parse("Quiero 2 blancos con sándalo")).isNull()
        assertThat(ShippingForm.parse(null)).isNull()
    }

    @Test fun el_formulario_vacio_sigue_siendo_formulario() {
        assertThat(ShippingForm.parse("[datos de envío recibidos] (sin datos)"))
            .isEqualTo(ShippingForm(null, null, null, null, null, null))
    }
}
