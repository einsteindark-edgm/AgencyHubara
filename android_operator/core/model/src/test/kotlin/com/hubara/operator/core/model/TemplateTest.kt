package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class TemplateTest {
    private val t = Template(
        name = "seguimiento_humano", body = "Hola {{1}}, te escribe {{2}} de la tienda.",
        variables = listOf(TemplateVariable("cliente", null, 30), TemplateVariable("asesor", null, 20)),
        isDefault = true, needsImage = false,
    )

    @Test fun la_vista_previa_pone_las_variables_en_orden() {
        assertThat(t.preview(mapOf("cliente" to "Laura", "asesor" to "Ana"))).isEqualTo("Hola Laura, te escribe Ana de la tienda.")
        assertThat(t.preview(mapOf("cliente" to "Laura"))).isEqualTo("Hola Laura, te escribe [asesor] de la tienda.")
    }

    // Mismos nombres que el modal «Reactivar conversación» del dashboard web: nada de ids técnicos a la vista.
    @Test fun el_operador_ve_un_nombre_legible_y_no_el_id_tecnico() {
        fun named(n: String, default: Boolean = false) = t.copy(name = n, isDefault = default)
        assertThat(named("human_followup_utility_v1", default = true).label)
            .isEqualTo("Seguimiento del equipo (mensaje libre) · recomendada")
        assertThat(named("order_status_utility_v2").label).isEqualTo("Estado del pedido")
        // Una plantilla nueva sin nombre conocido igual se lee: sin versión, categoría ni guiones bajos.
        assertThat(named("envio_demorado_utility_v3").label).isEqualTo("envio demorado")
        // El título (sin «recomendada») es el que va en «Enviando «Plantilla «…»»».
        assertThat(named("human_followup_utility_v1", default = true).title).isEqualTo("Seguimiento del equipo (mensaje libre)")
    }

    // La vista previa con el hueco vacío muestra qué va ahí, no el nombre técnico de la variable.
    @Test fun el_hueco_vacio_dice_que_va_ahi() {
        val t = Template("human_followup_utility_v1", "Te escribo por {{1}}.",
            listOf(TemplateVariable("followup_message", "Mensaje del operador sobre el tema pendiente", 400)), isDefault = true, needsImage = false)
        assertThat(t.preview(emptyMap())).isEqualTo("Te escribo por [Mensaje del operador sobre el tema pendiente].")
    }

    @Test fun faltantes() {
        assertThat(t.missing(mapOf("cliente" to "Laura", "asesor" to " "))).containsExactly("asesor")
    }
}
