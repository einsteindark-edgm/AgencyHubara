package com.hubara.operator.core.model

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class IdsTest {

    @Test fun session_id_valido_del_vault() {
        assertThat(SessionId.parse("wa_test_laura")?.raw).isEqualTo("wa_test_laura")
        assertThat(SessionId.parse("wa_+0000")?.raw).isEqualTo("wa_+0000")
    }

    @Test fun session_id_rechaza_traversal_y_formatos_ajenos() {
        assertThat(SessionId.parse("../etc")).isNull()
        assertThat(SessionId.parse("wa_")).isNull()
        assertThat(SessionId.parse("wa_a/b")).isNull()
        assertThat(SessionId.parse("wa_a.b")).isNull()
        assertThat(SessionId.parse("wa_abc\n")).isNull()
        assertThat(SessionId.parse("_analytics")).isNull()
        assertThat(SessionId.parse("wa_" + "a".repeat(121))).isNull()
    }

    @Test fun order_id_acepta_ids_de_medusa_y_rechaza_rutas() {
        assertThat(OrderId.parse("order_01HXYZ")?.raw).isEqualTo("order_01HXYZ")
        assertThat(OrderId.parse("draft_01HXYZ")?.raw).isEqualTo("draft_01HXYZ")
        assertThat(OrderId.parse("38")?.raw).isEqualTo("38")
        assertThat(OrderId.parse("../x")).isNull()
        assertThat(OrderId.parse("")).isNull()
    }

    @Test fun fire_id_por_sujeto() {
        assertThat(FireId.parse("chat:wa_test_sofia")?.raw).isEqualTo("chat:wa_test_sofia")
        assertThat(FireId.parse("order:order_01HX")?.raw).isEqualTo("order:order_01HX")
        assertThat(FireId.parse("chat:../x")).isNull()
    }
}
