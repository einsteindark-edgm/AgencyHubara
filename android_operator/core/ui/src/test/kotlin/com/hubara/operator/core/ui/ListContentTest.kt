package com.hubara.operator.core.ui

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/**
 * La auditoría encontró que Incendios decía «No hay incendios. Todo va bien.» antes de que llegara la primera
 * respuesta del servidor (la lista arranca vacía). Una lista vacía solo es «vacía» después de recargar bien.
 */
class ListContentTest {
    @Test fun vacia_antes_de_la_primera_recarga_es_cargando() {
        assertThat(listContent(isEmpty = true, refreshed = false, failed = false)).isEqualTo(ListContent.LOADING)
    }

    @Test fun vacia_despues_de_recargar_bien_es_vacia_y_si_fallo_es_error() {
        assertThat(listContent(isEmpty = true, refreshed = true, failed = false)).isEqualTo(ListContent.EMPTY)
        assertThat(listContent(isEmpty = true, refreshed = true, failed = true)).isEqualTo(ListContent.ERROR)
    }

    @Test fun con_datos_guardados_se_muestran_aunque_la_recarga_no_haya_vuelto_o_haya_fallado() {
        assertThat(listContent(isEmpty = false, refreshed = false, failed = false)).isEqualTo(ListContent.LIST)
        assertThat(listContent(isEmpty = false, refreshed = true, failed = true)).isEqualTo(ListContent.LIST)
    }
}
