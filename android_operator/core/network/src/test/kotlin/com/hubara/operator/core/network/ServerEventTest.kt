package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.network.sse.ServerEvent
import com.hubara.operator.core.network.sse.decodeServerEvent
import com.hubara.operator.core.network.sse.decodeServerFrame
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

    @Test fun la_app_recibe_solo_los_chats_que_cambiaron_y_los_que_ya_no_estan() {
        val e = decodeServerEvent(
            """{"domain":"chats","type":"sessions_delta","id":null,"ts_ms":1,
            "payload":{"sessions":[{"session_id":"wa_test_laura","last_message_preview":"Hola"},{"session_id":"../x"}],
            "removed":["wa_test_carlos","../y"]}}""",
        )
        e as ServerEvent.SessionsDelta
        assertThat(e.conversations.map { it.sessionId.raw }).containsExactly("wa_test_laura")
        assertThat(e.conversations.single().lastMessagePreview).isEqualTo("Hola")
        assertThat(e.removed.map { it.raw }).containsExactly("wa_test_carlos")
        assertThat(decodeServerEvent("""{"domain":"chats","type":"sessions_delta","ts_ms":1}""")).isNull()
    }

    @Test fun cada_evento_dice_cuando_salio_del_servidor() {
        val frame = decodeServerFrame("""{"domain":"orders","type":"changed","ts_ms":1761000000123}""")
        assertThat(frame!!.event).isEqualTo(ServerEvent.OrdersChanged)
        assertThat(frame.sentAtMs).isEqualTo(1761000000123)
        assertThat(decodeServerFrame("""{"domain":"orders","type":"changed"}""")!!.sentAtMs).isNull()
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
