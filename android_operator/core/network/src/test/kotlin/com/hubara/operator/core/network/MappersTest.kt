package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.FireKind
import com.hubara.operator.core.model.FireSubject
import com.hubara.operator.core.model.OrderStage
import com.hubara.operator.core.model.PaymentState
import com.hubara.operator.core.model.Prominence
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.Severity
import com.hubara.operator.core.network.dto.FiresDto
import com.hubara.operator.core.network.dto.OrderDetailDto
import com.hubara.operator.core.network.dto.SessionDetailsDto
import com.hubara.operator.core.network.dto.SessionsResponse
import com.hubara.operator.core.network.dto.SuggestionsDto
import com.hubara.operator.core.model.SuggestionTone
import org.junit.Test

class MappersTest {
    private val json = OperatorJson

    @Test fun bandeja_con_pedido_y_ruta_humano() {
        val dto = json.decodeFromString<SessionsResponse>(
            """{"sessions":[{"session_id":"wa_test_laura","phone_number":"570000000000","tag":"INTERESADO",
            "motivo":"","active_agent_route":"humano","phone_number_id":null,"pending_payment_order_id":null,
            "order_ref":{"order_id":"order_01HX","display_id":"38","payment":"confirmed","count":2},
            "last_updated_timestamp":1727640000.5,"last_inbound_ms":1727639999000,"inbound_count":3,
            "origin":null,"postponed":null,"campo_nuevo":"se ignora"}]}""",
        )
        val c = dto.sessions.single().toDomain()!!
        assertThat(c.sessionId.raw).isEqualTo("wa_test_laura")
        assertThat(c.route).isEqualTo(Route.HUMAN)
        assertThat(c.lastUpdatedMs).isEqualTo(1727640000500L)
        assertThat(c.inboundCount).isEqualTo(3)
        assertThat(c.orderRef!!.displayId).isEqualTo("38")
        assertThat(c.orderRef!!.payment).isEqualTo(PaymentState.CONFIRMED)
        assertThat(c.orderRef!!.count).isEqualTo(2)
    }

    // La bandeja solo mostraba el número: el backend ya manda el nombre de perfil y lo último que se dijo.
    @Test fun la_bandeja_trae_el_nombre_del_cliente_y_la_vista_previa() {
        val dto = json.decodeFromString<SessionsResponse>(
            """{"sessions":[
              {"session_id":"wa_test_laura","customer_name":"  Laura Prueba ","last_message_preview":"¿Me confirmas el precio?"},
              {"session_id":"wa_test_sin_nombre","customer_name":"   "},
              {"session_id":"wa_test_viejo"}]}""",
        )
        val (laura, blank, old) = dto.sessions.map { it.toDomain()!! }
        assertThat(laura.customerName).isEqualTo("Laura Prueba")
        assertThat(laura.lastMessagePreview).isEqualTo("¿Me confirmas el precio?")
        assertThat(blank.customerName).isNull()
        assertThat(old.customerName).isNull()
        assertThat(old.lastMessagePreview).isNull()
    }

    @Test fun una_sesion_con_id_invalido_se_descarta() {
        val dto = json.decodeFromString<SessionsResponse>("""{"sessions":[{"session_id":"../x"}]}""")
        assertThat(dto.sessions.single().toDomain()).isNull()
    }

    @Test fun historial_mapea_autores_y_oculta_llamadas_internas() {
        val dto = json.decodeFromString<SessionDetailsDto>(
            """{"session_id":"wa_test_laura","phone_number":"x","tag":"t","motivo":"","memory_content":null,
            "active_agent_route":"ventas","phone_number_id":null,"service_window_expires_at_ms":1727700000000,
            "order_ref":null,"status_history":[],"origin":null,"messages":[
              {"ui_type":"user_message","role":"user","content":"hola","timestamp":"2026-09-29T15:00:00+00:00","wamid":"w1"},
              {"ui_type":"agent_message","role":"assistant","content":"¡Hola!","timestamp":"2026-09-29T15:00:05+00:00"},
              {"ui_type":"agent_message","role":"assistant","content":"soy humano","sender":"human"},
              {"ui_type":"human_message","role":"assistant","content":"también"},
              {"ui_type":"agent_tool_call","role":"assistant","content":null},
              {"ui_type":"tool_execution_result","role":"tool","content":"{}"},
              {"ui_type":"ui_component_sent","role":"assistant","content":"[catálogo enviado]"},
              {"ui_type":"user_message","role":"user","content":null,"image_url":"/api/dashboard/media/wa_test_laura/a.jpg"}
            ]}""",
        )
        val detail = dto.toDomain()!!
        assertThat(detail.route).isEqualTo(Route.BOT)
        assertThat(detail.windowExpiresAtMs).isEqualTo(1727700000000L)
        assertThat(detail.messages.map { it.author }).containsExactly(
            Author.CUSTOMER, Author.BOT, Author.HUMAN, Author.HUMAN, Author.SYSTEM, Author.CUSTOMER,
        ).inOrder()
        assertThat(detail.messages.first().key).isEqualTo("w1")
        assertThat(detail.messages.first().timestampMs).isEqualTo(1790694000000L)
        assertThat(detail.messages.last().imageUrl).isEqualTo("/api/dashboard/media/wa_test_laura/a.jpg")
        assertThat(detail.messages.map { it.key }.toSet()).hasSize(detail.messages.size)
    }

    // Caso 2026-10-09: Meta re-entregó un mensaje de 3 días antes 19 s después de la plantilla
    // del operador; con la hora de llegada parecía su respuesta.
    @Test fun mensaje_que_llego_tarde_trae_cuando_lo_escribio_el_cliente() {
        val dto = json.decodeFromString<SessionDetailsDto>(
            """{"session_id":"wa_test_laura","messages":[
              {"ui_type":"user_message","content":"hola, ¿siguen teniendo velas?","timestamp":"2026-10-09T21:35:42+00:00",
               "sent_at":"2026-10-06T17:02:04+00:00","arrived_after_window":true,"wamid":"w1"},
              {"ui_type":"user_message","content":"a tiempo","timestamp":"2026-10-09T21:36:00+00:00","wamid":"w2"}
            ]}""",
        )
        val (late, onTime) = dto.toDomain()!!.messages
        assertThat(late.sentAtMs).isEqualTo(1791306124000L)
        assertThat(late.arrivedAfterWindow).isTrue()
        assertThat(onTime.sentAtMs).isNull()
        assertThat(onTime.arrivedAfterWindow).isFalse()
    }

    // L-10: un valor raro en un campo nuevo no puede tumbar la sesión entera.
    @Test fun marcas_de_tardio_raras_se_ignoran_sin_romper_el_chat() {
        val dto = json.decodeFromString<SessionDetailsDto>(
            """{"session_id":"wa_test_laura","messages":[
              {"ui_type":"user_message","content":"hola","sent_at":{"x":1},"arrived_after_window":"sí","wamid":"w1"}
            ]}""",
        )
        val message = dto.toDomain()!!.messages.single()
        assertThat(message.sentAtMs).isNull()
        assertThat(message.arrivedAfterWindow).isFalse()
    }

    // Un backend de desarrollo (FakeSend) repite el mismo id en cada envío; una clave repetida
    // colapsa filas en Room y revienta la lista de Compose ("Key was already used").
    @Test fun wamid_repetido_no_repite_la_clave_del_mensaje() {
        val dto = json.decodeFromString<SessionDetailsDto>(
            """{"session_id":"wa_test_laura","messages":[
              {"ui_type":"agent_message","content":"aromas","wamid":"fake-text"},
              {"ui_type":"user_message","content":"lavanda","wamid":"w2"},
              {"ui_type":"agent_message","content":"fotos","wamid":"fake-text"}
            ]}""",
        )
        val keys = dto.toDomain()!!.messages.map { it.key }
        assertThat(keys.toSet()).hasSize(3)
        assertThat(keys.first()).isEqualTo("fake-text")
        // Estable: el historial solo crece al final, así que la misma respuesta da las mismas claves.
        assertThat(dto.toDomain()!!.messages.map { it.key }).isEqualTo(keys)
    }

    @Test fun burbujas_por_reglas() {
        val dto = json.decodeFromString<SuggestionsDto>(
            """{"session_id":"wa_test_laura","version":7,"decided_by":"rules","stage":"etapa_variantes",
            "window_open":true,"in_control":"human","suggestions":[
              {"id":"present_variant_picker","label":"Enviar aromas","prominence":"primary","editable":true,
               "action":{"name":"present_variant_picker","args":{"product":"duo-zodiacal","attribute":"aroma"}}},
              {"id":"request_shipping_details","label":"Pedir datos de envío","prominence":"normal","editable":false,
               "action":{"name":"request_shipping_details","args":{}}}]}""",
        )
        val set = dto.toDomain()!!
        assertThat(set.version).isEqualTo(7)
        assertThat(set.humanInControl).isTrue()
        assertThat(set.suggestions.map { it.prominence }).containsExactly(Prominence.PRIMARY, Prominence.NORMAL).inOrder()
        assertThat(set.suggestions.first().action.args["product"].toString()).isEqualTo("\"duo-zodiacal\"")
    }

    /** «Crear pedido»: no le manda nada al cliente, abre el formulario (una pantalla del servidor) y va marcada. */
    @Test fun la_burbuja_crear_pedido_abre_una_pantalla_y_va_marcada() {
        val dto = json.decodeFromString<SuggestionsDto>(
            """{"session_id":"wa_test_laura","version":8,"decided_by":"rules","stage":"etapa_cierre",
            "window_open":true,"in_control":"human","suggestions":[
              {"id":"create_order","label":"Crear pedido","prominence":"primary","editable":false,"tone":"order",
               "opens":"crear_pedido","action":{"name":"create_order","args":{}}},
              {"id":"send_payment_methods","label":"Medios de pago","prominence":"normal","editable":false,
               "action":{"name":"send_payment_methods","args":{}}}]}""",
        )
        val (order, payment) = dto.toDomain()!!.suggestions
        assertThat(order.tone).isEqualTo(SuggestionTone.ORDER)
        assertThat(order.opens).isEqualTo("crear_pedido")
        assertThat(payment.tone).isEqualTo(SuggestionTone.NORMAL)
        assertThat(payment.opens).isNull()
    }

    @Test fun incendios_de_chat_y_de_orden_y_tolera_tipos_nuevos() {
        val dto = json.decodeFromString<FiresDto>(
            """{"decided_by":"rules","fires":[
              {"fire_id":"chat:wa_test_sofia","subject":{"kind":"chat","session_id":"wa_test_sofia","order_id":null},
               "severity":"grave","kind":"wants_human","getting_worse":true,"title":"Sofía pide un humano",
               "subtitle":"12 min sin respuesta","primary_action":{"name":"open_chat","args":{"session_id":"wa_test_sofia"}},
               "updated_ms":10},
              {"fire_id":"order:order_01HX","subject":{"kind":"order","session_id":"wa_test_carlos","order_id":"order_01HX"},
               "severity":"hoy","kind":"delayed","getting_worse":false,"title":"#41 · Retrasada","subtitle":"",
               "primary_action":{"name":"open_order","args":{}},"updated_ms":20},
              {"fire_id":"chat:wa_test_n","subject":{"kind":"chat","session_id":"wa_test_n"},"severity":"espera",
               "kind":"tipo_que_no_existe","title":"x","subtitle":"","primary_action":{"name":"open_chat"},"updated_ms":1}
            ]}""",
        )
        val fires = dto.toDomain()
        assertThat(fires).hasSize(3)
        assertThat(fires[0].severity).isEqualTo(Severity.GRAVE)
        assertThat(fires[0].kind).isEqualTo(FireKind.WANTS_HUMAN)
        assertThat(fires[0].gettingWorse).isTrue()
        assertThat((fires[1].subject as FireSubject.Order).orderId!!.raw).isEqualTo("order_01HX")
        assertThat(fires[2].kind).isEqualTo(FireKind.OTHER)
    }

    @Test fun detalle_de_orden() {
        val dto = json.decodeFromString<OrderDetailDto>(
            """{"summary":{"id":"order_01HX","display_id":"38","customer":"Carlos R.","short":"CR","color":"a",
            "phone":null,"city":"Bogotá","channel":"whatsapp","status":"ready","pay_status":"paid","pay_type":"confirmed",
            "items":2,"pieces":3,"total_cop":243800,"currency_code":"cop","is_draft":false,"due_iso":null,"due_time":null,
            "overdue":false,"priority":"normal","agent":"bot","created_at_ms":1,"updated_at_ms":2},
            "items_detail":[{"title":"Duo Zodiacal","sku":null,"quantity":2,"unit_price_cop":89900,"total_cop":179800,
              "variant_label":"azul · vainilla","thumbnail":null,"handle":"duo-zodiacal"}],
            "shipping_address":{"first_name":"Carlos","last_name":"R.","phone":null,"address_1":"Cra 12 # 34-56",
              "address_2":"Chapinero","city":"Bogotá","country_code":"co"},
            "billing_address":null,"subtotal_cop":231800,"shipping_cop":12000,"tax_total_cop":0,"discount_total_cop":0,
            "timeline":[],"payment_method_label":"Transferencia","notes":[],"data_completeness_missing":["tracking_number"]}""",
        )
        val d = dto.toDomain()!!
        assertThat(d.summary.stage).isEqualTo(OrderStage.READY)
        assertThat(d.items.single().variant).isEqualTo("azul · vainilla")
        assertThat(d.address!!.receiver).isEqualTo("Carlos R.")
        assertThat(d.address!!.neighborhood).isEqualTo("Chapinero")
        assertThat(d.missing).containsExactly("tracking_number")
    }

    // Órdenes manda "#41" y la bandeja manda "41": la app pone el # una sola vez ("Pedido ##41" lo vio Artemis).
    @Test fun el_numero_de_pedido_llega_sin_numeral_venga_de_donde_venga() {
        val detail = json.decodeFromString<OrderDetailDto>(
            """{"summary":{"id":"order_01HX","display_id":"#41","customer":"Andrés","status":"preparing","pay_status":"paid"}}""",
        )
        assertThat(detail.toDomain()!!.summary.displayId).isEqualTo("41")
        val sessions = json.decodeFromString<SessionsResponse>(
            """{"sessions":[{"session_id":"wa_test_andres","order_ref":{"order_id":"order_01HX","display_id":"#41"}}]}""",
        )
        assertThat(sessions.sessions.single().toDomain()!!.orderRef!!.displayId).isEqualTo("41")
    }
}

class MediaUrlTest {
    private val config = com.hubara.operator.core.network.di.ApiConfig(
        with(okhttp3.HttpUrl.Companion) { "http://10.0.2.2:8000/".toHttpUrl() }, "", "us-east-1",
    )

    @org.junit.Test fun las_fotos_se_resuelven_contra_el_backend_y_nunca_contra_otro_host() {
        com.google.common.truth.Truth.assertThat(config.mediaUrl("/api/dashboard/media/wa_test_laura/a.jpg"))
            .isEqualTo("http://10.0.2.2:8000/api/dashboard/media/wa_test_laura/a.jpg")
        com.google.common.truth.Truth.assertThat(config.mediaUrl("https://otro.example/robar.jpg")).isNull()
        com.google.common.truth.Truth.assertThat(config.mediaUrl(null)).isNull()
    }
}
