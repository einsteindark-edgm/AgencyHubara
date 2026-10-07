package com.hubara.operator.core.network

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.model.OrderStage
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.CommandResult
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.api.ToolResult
import com.hubara.operator.core.network.dto.ReturnToBotRequest
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Before
import org.junit.Test

class OperatorServiceTest {
    private val server = MockWebServer()
    private lateinit var service: OperatorService
    private val laura = SessionId.parse("wa_test_laura")!!

    @Before fun setUp() {
        server.start()
        service = OperatorService.create(OkHttpClient(), server.url("/"))
    }

    @After fun tearDown() = server.close()

    @Test fun tool_enviada_manda_client_action_id_y_args() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"sent":true,"tool":"present_variant_picker","client_action_id":"c1","deduplicated":false}"""))
        val action = ActionRef("present_variant_picker", buildJsonObject { put("product", JsonPrimitive("duo-zodiacal")) })
        assertThat(service.runTool(laura, action, clientActionId = "c1")).isEqualTo(ToolResult.Sent(deduplicated = false))
        val req = server.takeRequest()
        assertThat(req.url.encodedPath).isEqualTo("/api/chats/session-actions/wa_test_laura/tools/present_variant_picker")
        assertThat(req.body!!.utf8()).isEqualTo("""{"client_action_id":"c1","args":{"product":"duo-zodiacal"}}""")
    }

    // Artemis lo vio en el emulador: el valor por defecto no se serializaba, el cuerpo salía `{}`
    // y el backend respondía 422 («No se pudo devolver la conversación al bot»).
    @Test fun devolver_al_bot_manda_la_ruta_de_ventas() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"ok":true,"active_route":"ventas","tag":"INTERESADO","motivo":"","terminated_workflows":[]}"""))
        service.api.returnToBot(laura.raw, ReturnToBotRequest(targetRoute = "ventas"))
        val req = server.takeRequest()
        assertThat(req.url.encodedPath).isEqualTo("/api/dashboard/sessions/wa_test_laura/return-to-bot")
        assertThat(req.body!!.utf8()).isEqualTo("""{"target_route":"ventas"}""")
    }

    @Test fun ventana_cerrada_y_sin_control_son_errores_definitivos() = runTest {
        server.enqueue(MockResponse(code = 409, body = """{"error":"window_closed"}"""))
        server.enqueue(MockResponse(code = 409, body = """{"detail":{"error":"not_in_control"}}"""))
        assertThat(service.runTool(laura, ActionRef("send_payment_methods"), "c2")).isEqualTo(ToolResult.WindowClosed)
        assertThat(service.runTool(laura, ActionRef("send_payment_methods"), "c3")).isEqualTo(ToolResult.NotInControl)
    }

    @Test fun errores_del_servidor_se_reintentan_y_los_422_no() = runTest {
        server.enqueue(MockResponse(code = 503, body = "{}"))
        server.enqueue(MockResponse(code = 422, body = """{"detail":"args"}"""))
        assertThat(service.runTool(laura, ActionRef("present_products"), "c4")).isEqualTo(ToolResult.Transient)
        assertThat(service.runTool(laura, ActionRef("present_products"), "c5")).isInstanceOf(ToolResult.Rejected::class.java)
    }

    @Test fun el_rechazo_de_la_tool_trae_su_mensaje() = runTest {
        server.enqueue(MockResponse(code = 422, body = """{"error":"tool_rejected","reason":"missing_variant","message":"Ese producto no tiene aromas para elegir."}"""))
        server.enqueue(MockResponse(code = 422, body = """{"error":"invalid_args","problems":["product"]}"""))
        assertThat(service.runTool(laura, ActionRef("present_variant_picker"), "c6"))
            .isEqualTo(ToolResult.Rejected(422, "Ese producto no tiene aromas para elegir."))
        assertThat(service.runTool(laura, ActionRef("present_variant_picker"), "c7"))
            .isEqualTo(ToolResult.Rejected(422, "faltan datos para esta acción"))
    }

    @Test fun avanzar_etapa_con_guia_y_costo() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"success":true,"order_id":"order_1","current_stage":"shipping","error_detail":null,"audit_id":"a"}"""))
        val r = service.advanceStage(OrderId.parse("order_1")!!, OrderStage.SHIPPING, trackingUrl = "https://guia.example/1", shippingCostCop = 12000)
        assertThat(r).isEqualTo(CommandResult.Ok(OrderStage.SHIPPING))
        val body = server.takeRequest().body!!.utf8()
        assertThat(body).contains("\"stage\":\"shipping\"")
        assertThat(body).contains("\"tracking_url\":\"https://guia.example/1\"")
        assertThat(body).contains("\"shipping_cost\":12000")
    }

    @Test fun transicion_invalida_llega_como_error_legible() = runTest {
        server.enqueue(MockResponse(code = 200, body = """{"success":false,"order_id":"order_1","current_stage":"new","error_detail":"invalid_transition: new → delivered","audit_id":null}"""))
        val r = service.advanceStage(OrderId.parse("order_1")!!, OrderStage.DELIVERED)
        assertThat(r).isEqualTo(CommandResult.Failed("invalid_transition: new → delivered"))
    }
}
