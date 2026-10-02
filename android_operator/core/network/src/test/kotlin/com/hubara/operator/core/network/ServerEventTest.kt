package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.sse.ServerEvent
import com.hubara.operator.core.network.sse.decodeServerEvent
import org.junit.Test

class ServerEventTest {
    @Test fun snapshot_trae_la_bandeja() {
        val e = decodeServerEvent(
            """{"domain":"chats","type":"sessions_snapshot","id":null,"ts_ms":1,
            "payload":{"sessions":[{"session_id":"wa_test_laura","active_agent_route":"humano"},{"session_id":"../x"}]}}""",
        )
        e as ServerEvent.SessionsSnapshot
        assertThat(e.conversations.map { it.sessionId.raw }).containsExactly("wa_test_laura")
    }

    @Test fun cambio_de_una_sesion() {
        val e = decodeServerEvent("""{"domain":"chats","type":"session_updated","id":"wa_test_laura","ts_ms":1}""")
        assertThat((e as ServerEvent.SessionUpdated).sessionId.raw).isEqualTo("wa_test_laura")
    }

    @Test fun ordenes_cambiaron() {
        assertThat(decodeServerEvent("""{"domain":"orders","type":"changed","ts_ms":1}""")).isEqualTo(ServerEvent.OrdersChanged)
        assertThat(decodeServerEvent("""{"domain":"orders","type":"order_updated","id":"order_1","ts_ms":1}""")).isEqualTo(ServerEvent.OrdersChanged)
    }

    @Test fun eventos_desconocidos_o_rotos_no_rompen_nada() {
        assertThat(decodeServerEvent("""{"domain":"ads","type":"run_progress","ts_ms":1}""")).isEqualTo(ServerEvent.Unknown("ads", "run_progress"))
        assertThat(decodeServerEvent("no es json")).isNull()
        assertThat(decodeServerEvent("""{"domain":"chats","type":"session_updated","id":"../x"}""")).isNull()
    }
}
