package com.hubara.operator.core.sdui

import com.google.common.truth.Truth.assertThat
import org.junit.Test

/** Cómo una fuente de datos o una llamada se vuelve un pedido HTTP a NUESTRO backend (ruta relativa, valores escapados). */
class RequestTest {

    private val scope = scopeOf(
        "params" to json("""{"id": "mkt a/b", "dotdot": "..", "order": "order_01"}"""),
        "state" to json("""{"etapa": "", "q": "vela de lavanda"}"""),
        "form" to json("""{"guia": "https://envia.co/g/1", "costo": "12.000"}"""),
    )

    private fun source(raw: String) = parseScreen("""{"schema": 1, "id": "x", "data": {"s": $raw}, "body": []}""").doc!!.data.getValue("s")

    @Test fun `los valores van escapados dentro de la ruta`() {
        val call = source("""{"get": "/api/marketing/campaigns/{{params.id}}/stats"}""").resolve(scope, TEST_ENV)!!
        assertThat(call.method).isEqualTo("GET")
        assertThat(call.path).isEqualTo("/api/marketing/campaigns/mkt%20a%2Fb/stats")
        assertThat(call.body).isNull()
    }

    @Test fun `la consulta omite lo vacio y escapa lo demas`() {
        val call = source("""{"get": "/api/orders/orders", "query": {"stage": "{{state.etapa}}", "q": "{{state.q}}", "limit": "200"}}""")
            .resolve(scope, TEST_ENV)!!
        assertThat(call.path).isEqualTo("/api/orders/orders?q=vela+de+lavanda&limit=200")
    }

    @Test fun `un valor no puede salirse de la ruta`() {
        assertThat(source("""{"get": "/api/orders/{{params.dotdot}}/x"}""").resolve(scope, TEST_ENV)).isNull()
        assertThat(source("""{"get": "https://otro.com/api/x"}""").resolve(scope, TEST_ENV)).isNull()
        assertThat(source("""{"get": "/api/../admin"}""").resolve(scope, TEST_ENV)).isNull()
    }

    @Test fun `el cuerpo de una llamada se arma con el formulario y conserva los tipos`() {
        val doc = parseScreen(
            """{"schema": 1, "id": "x", "body": [{"type": "button", "text": "Despachar", "action": {
                 "type": "call", "method": "PATCH", "path": "/api/orders/orders/{{params.order}}/stage",
                 "body": {"stage": "shipping", "tracking_url": "{{form.guia}}", "shipping_cost_cop": "{{form.costo | to_number}}", "notify": true}}}]}""",
        ).doc!!
        val call = (doc.body.single().action as Action.Call).resolve(scope, TEST_ENV)!!
        assertThat(call.method).isEqualTo("PATCH")
        assertThat(call.path).isEqualTo("/api/orders/orders/order_01/stage")
        assertThat(call.body).isEqualTo(
            json("""{"stage": "shipping", "tracking_url": "https://envia.co/g/1", "shipping_cost_cop": 12000, "notify": true}"""),
        )
    }
}
