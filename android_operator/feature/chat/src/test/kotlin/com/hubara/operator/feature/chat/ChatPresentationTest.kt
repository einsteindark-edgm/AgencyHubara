package com.hubara.operator.feature.chat

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/** El encabezado y la paleta pasaron a pantallas del servidor (chat.json, acciones.json): sus reglas se prueban allá. */
class ChatPresentationTest {
    @Test fun etapas_del_embudo_en_palabras() {
        assertThat(stageLabel("etapa_variantes")).isEqualTo("eligiendo variantes")
        assertThat(stageLabel("etapa_cierre")).isEqualTo("cierre")
        assertThat(stageLabel(null)).isEqualTo("sin datos")
        assertThat(stageLabel("etapa_nueva")).isEqualTo("sin datos")
    }
}
