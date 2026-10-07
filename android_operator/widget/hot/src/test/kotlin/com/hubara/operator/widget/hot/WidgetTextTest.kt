package com.hubara.operator.widget.hot

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.dto.HotSaleDto
import org.junit.Test

class WidgetTextTest {
    @Test fun cada_fila_dice_quien_etapa_producto_y_riesgo_sin_mensajes() {
        assertThat(widgetLine(HotSaleDto("wa_test_andres", name = "Andrés", stage = "etapa_cierre", product = "Duo Zodiacal azul × 2", risk = true)))
            .isEqualTo("Andrés · cierre · Duo Zodiacal azul × 2 · RIESGO")
        assertThat(widgetLine(HotSaleDto("wa_test_x", stage = "etapa_datos_envio"))).isEqualTo("Cliente · datos de envío")
    }
}
