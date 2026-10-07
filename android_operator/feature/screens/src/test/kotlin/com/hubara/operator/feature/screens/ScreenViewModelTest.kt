package com.hubara.operator.feature.screens

import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.screens.AppSource
import com.hubara.operator.core.data.screens.NativeActions
import com.hubara.operator.core.data.screens.NativeOutcome
import com.hubara.operator.core.data.screens.ScreenCallError
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.navigation.LiveKey
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.flowOf
import kotlinx.serialization.json.JsonObject
import com.hubara.operator.core.data.screens.ScreenData
import com.hubara.operator.core.data.screens.ScreenDataCache
import com.hubara.operator.core.data.screens.ScreenDocs
import com.hubara.operator.core.data.screens.ServerChanges
import com.hubara.operator.core.model.OrderId
import com.hubara.operator.core.navigation.OrderSheetKey
import com.hubara.operator.core.navigation.ScreenKey
import com.hubara.operator.core.navigation.ScreenSheetKey
import com.hubara.operator.core.sdui.Action
import com.hubara.operator.core.sdui.HttpCall
import com.hubara.operator.core.sdui.ScreenDoc
import com.hubara.operator.core.sdui.Template
import com.hubara.operator.core.sdui.parseScreen
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import org.junit.After
import org.junit.Test

/**
 * El ViewModel genérico de una pantalla del servidor: consigue la definición (guardada y después la del servidor),
 * pide sus datos, vuelve a pedirlos cuando cambia un filtro o avisa el servidor, y ejecuta las acciones del catálogo.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class ScreenViewModelTest {

    private val docs = FakeDocs()
    private val data = FakeData()
    private val cache = MemoryCache()
    private val changes = FakeChanges()
    private val effects = mutableListOf<ScreenEffect>()
    private val chatsFlow = MutableStateFlow<JsonElement>(json("""[{"session_id": "wa_000000000101", "title": "Laura"}]"""))
    private var chatsRefresh: Result<Unit> = Result.success(Unit)
    private var refreshes = 0
    private val nativeCalls = mutableListOf<Pair<String, JsonObject>>()
    private var nativeOutcome: NativeOutcome = NativeOutcome.Done
    private val appSources: Map<String, AppSource> = mapOf(
        "conversations" to object : AppSource {
            override fun observe(params: Map<String, String>) = chatsFlow
            override suspend fun refresh(params: Map<String, String>): Result<Unit> { refreshes++; return chatsRefresh }
        },
        "chat" to object : AppSource {
            override fun observe(params: Map<String, String>) = flowOf(json("""{"title": "chat de ${params["session"]}"}"""))
        },
    )
    private val native = NativeActions { name, args -> nativeCalls += name to args; nativeOutcome }

    private fun TestScope.vm(id: String = "ventas", params: Map<String, String> = emptyMap()): ScreenViewModel {
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        return ScreenViewModel(id, params, docs, data, cache, changes, { NOW }, appSources, native).also { vm ->
            backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.state.collect {} }
            backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) { vm.effects.collect { effects += it } }
        }
    }

    @After fun tearDown() = Dispatchers.resetMain()

    @Test fun abre_con_lo_guardado_y_pide_sus_datos() = runTest {
        docs.cached["ventas"] = VENTAS
        data.ok("/api/orders/orders?limit=500", """{"orders": [{"id": "o1"}]}""")
        val vm = vm()
        val ui = vm.state.value
        assertThat(ui.phase).isEqualTo(ScreenPhase.READY)
        assertThat(ui.data["pedidos"]).isEqualTo(json("""{"orders": [{"id": "o1"}]}"""))
        assertThat(ui.waiting).isFalse()
        assertThat(data.paths()).containsExactly("/api/orders/orders?limit=500")
    }

    @Test fun la_version_del_servidor_reemplaza_a_la_guardada_sin_repetir_lo_que_no_cambio() = runTest {
        docs.cached["ventas"] = VENTAS
        docs.fresh["ventas"] = VENTAS.replace("\"Ventas\"", "\"Ventas de hoy\"")
        data.ok("/api/orders/orders?limit=500", """{"orders": []}""")
        val vm = vm()
        assertThat(vm.state.value.doc?.title?.raw).isEqualTo("Ventas de hoy")
        assertThat(data.paths()).hasSize(1)
    }

    @Test fun sin_pantalla_ni_red_dice_que_no_esta_y_se_puede_reintentar() = runTest {
        val vm = vm()
        assertThat(vm.state.value.phase).isEqualTo(ScreenPhase.MISSING)
        docs.fresh["ventas"] = VENTAS
        vm.retry()
        assertThat(vm.state.value.phase).isEqualTo(ScreenPhase.READY)
    }

    @Test fun una_pantalla_de_un_catalogo_mas_nuevo_pide_actualizar_la_app() = runTest {
        docs.fresh["ventas"] = VENTAS.replace("\"schema\": 1,", "\"schema\": 1, \"requires\": 99,")
        val vm = vm()
        assertThat(vm.state.value.phase).isEqualTo(ScreenPhase.OUTDATED)
        assertThat(data.calls).isEmpty()
    }

    @Test fun elegir_un_filtro_vuelve_a_pedir_solo_lo_que_lo_usa() = runTest {
        docs.fresh["ventas"] = FILTRADA
        val vm = vm()
        assertThat(data.paths()).containsExactly("/api/orders/orders?stage=new", "/api/marketing/campaigns")
        vm.onBind("state.etapa", JsonPrimitive("ready"))
        assertThat(vm.state.value.state["etapa"]).isEqualTo(JsonPrimitive("ready"))
        assertThat(data.paths()).containsExactly("/api/orders/orders?stage=new", "/api/marketing/campaigns", "/api/orders/orders?stage=ready")
    }

    @Test fun escribir_en_un_buscador_espera_a_que_pare() = runTest {
        docs.fresh["ventas"] = FILTRADA
        val vm = vm()
        vm.onBind("state.etapa", JsonPrimitive("rea"), typing = true)
        vm.onBind("state.etapa", JsonPrimitive("ready"), typing = true)
        assertThat(data.calls).hasSize(2)
        advanceTimeBy(500)
        runCurrent()
        assertThat(data.paths().last()).isEqualTo("/api/orders/orders?stage=ready")
        assertThat(data.calls).hasSize(3)
    }

    @Test fun escribir_en_el_formulario_no_pide_datos() = runTest {
        docs.fresh["ventas"] = FILTRADA
        val vm = vm()
        vm.onBind("form.guia", JsonPrimitive("https://envia.co/g/1"))
        assertThat(vm.state.value.form["guia"]).isEqualTo(JsonPrimitive("https://envia.co/g/1"))
        assertThat(data.calls).hasSize(2)
    }

    @Test fun un_dato_obligatorio_que_falla_rompe_la_pantalla_y_uno_opcional_no() = runTest {
        docs.fresh["ventas"] = FILTRADA
        data.fail("/api/marketing/campaigns")
        val vm = vm()
        assertThat(vm.state.value.broken).isFalse()
        assertThat(vm.state.value.failed).containsExactly("campanas")

        // Si ya había datos, una falla al recargar no rompe nada: sigue lo que había (como sin red).
        data.fail("/api/orders/orders?stage=new")
        vm.refresh()
        assertThat(vm.state.value.broken).isFalse()
        // Sin nada que mostrar (ni guardado de antes), sí.
        cache.clear()
        assertThat(vm().state.value.broken).isTrue()
    }

    @Test fun sin_red_muestra_lo_ultimo_que_trajo() = runTest {
        docs.fresh["ventas"] = VENTAS
        cache.values["ventas|pedidos|/api/orders/orders?limit=500"] = json("""{"orders": [{"id": "guardado"}]}""")
        data.fail("/api/orders/orders?limit=500")
        val vm = vm()
        assertThat(vm.state.value.data["pedidos"]).isEqualTo(json("""{"orders": [{"id": "guardado"}]}"""))
        assertThat(vm.state.value.broken).isFalse()
    }

    @Test fun un_error_inesperado_no_tumba_la_app() = runTest {
        // En el emulador una API de Java que no existe en Android 11 cerraba la app entera desde una pantalla del
        // servidor. Lo que falle adentro de una pantalla se queda en esa pantalla.
        docs.fresh["ventas"] = VENTAS
        data.responder = { throw IllegalStateException("algo que nadie esperaba") }
        val vm = vm()
        assertThat(vm.state.value.failed).containsExactly("pedidos")
        val call = vm.state.value.doc!!.body.single { it.type == "button" }.action!!
        vm.onAction(call, vm.state.value.scope(NOW).with("item", json("""{"id": "order_01"}""")))
        vm.onConfirm(accepted = true)
        assertThat(effects).contains(ScreenEffect.Message("No se pudo completar."))
        assertThat(vm.state.value.busy).isFalse()
    }

    @Test fun una_fuente_del_telefono_llega_sola_y_tirar_para_actualizar_la_pide_al_backend() = runTest {
        docs.fresh["chats"] = CHATS
        val vm = vm("chats")
        assertThat(vm.state.value.data["chats"]).isEqualTo(json("""[{"session_id": "wa_000000000101", "title": "Laura"}]"""))
        assertThat(refreshes).isEqualTo(1)  // al abrir, como hacía la bandeja nativa
        // Room cambia (llega un mensaje por el SSE): la pantalla se entera sola, sin pedir nada.
        chatsFlow.value = json("""[{"session_id": "wa_000000000101", "title": "Laura"}, {"session_id": "wa_000000000102", "title": "Sofía"}]""")
        assertThat((vm.state.value.data["chats"] as kotlinx.serialization.json.JsonArray)).hasSize(2)
        assertThat(data.calls).isEmpty()

        chatsRefresh = Result.failure(ScreenCallError(0, "Sin conexión con el servidor."))
        vm.refresh()
        assertThat(refreshes).isEqualTo(2)
        // Sin red sigue lo de Room, y la pantalla puede decirlo: {{status.chats.failed}}.
        assertThat(vm.state.value.broken).isFalse()
        val scope = vm.state.value.scope(NOW)
        assertThat(Template.parse("{{status.chats.failed}}").evaluate(scope)).isEqualTo(JsonPrimitive(true))
    }

    @Test fun una_fuente_del_telefono_con_parametros() = runTest {
        docs.fresh["chat"] = """{"schema": 1, "id": "chat", "params": ["session"],
            "data": {"chat": {"app": "chat", "params": {"session": "{{params.session}}"}}}, "body": []}"""
        val vm = vm("chat", mapOf("session" to "wa_000000000101"))
        assertThat(vm.state.value.data["chat"]).isEqualTo(json("""{"title": "chat de wa_000000000101"}"""))
    }

    @Test fun las_acciones_nativas_reciben_sus_argumentos_y_avisan_el_resultado() = runTest {
        docs.fresh["chats"] = CHATS
        val vm = vm("chats", mapOf("session" to "wa_000000000101"))
        val scope = vm.state.value.scope(NOW)
        nativeOutcome = NativeOutcome.Message("Listo")
        vm.onAction(Action.Native("send_tool", json("""{"session": "{{params.session}}", "tool": "present_products", "label": "Enviar productos"}""")), scope)
        assertThat(nativeCalls.single()).isEqualTo("send_tool" to json("""{"session": "wa_000000000101", "tool": "present_products", "label": "Enviar productos"}"""))
        assertThat(effects).contains(ScreenEffect.Message("Listo"))

        nativeOutcome = NativeOutcome.OpenUrl("https://tienda.example/privacidad")
        vm.onAction(Action.Native("open_privacy", null), scope)
        assertThat(effects).contains(ScreenEffect.OpenUrl("https://tienda.example/privacidad"))
    }

    @Test fun la_confirmacion_generica_y_las_condiciones() = runTest {
        docs.fresh["chats"] = CHATS
        val vm = vm("chats")
        val scope = vm.state.value.scope(NOW).with("f", json("""{"opens": "order", "order_id": "order_01", "session_id": "wa_000000000102"}"""))
        val signOut = Action.AskFirst(com.hubara.operator.core.sdui.Confirm(Template.parse("¿Cerrar sesión?"), null, "Cerrar sesión", "Cancelar"), Action.Native("sign_out", null))
        vm.onAction(signOut, scope)
        assertThat(vm.state.value.confirm?.title).isEqualTo("¿Cerrar sesión?")
        assertThat(nativeCalls).isEmpty()
        vm.onConfirm(accepted = true)
        assertThat(nativeCalls.map { it.first }).containsExactly("sign_out")

        val open = Action.If(
            Template.parse("{{f.opens | eq:'order'}}"),
            Action.OpenOrder(Template.parse("{{f.order_id}}")),
            Action.OpenChat(Template.parse("{{f.session_id}}"), live = true),
        )
        vm.onAction(open, scope)
        vm.onAction(open, scope.with("f", json("""{"opens": "chat", "session_id": "wa_000000000102"}""")))
        assertThat(effects.filterIsInstance<ScreenEffect.Open>().map { it.key }).containsExactly(
            OrderSheetKey(OrderId.parse("order_01")!!),
            LiveKey(SessionId.parse("wa_000000000102")!!),
        ).inOrder()
    }

    @Test fun una_llamada_con_confirmacion_no_sale_hasta_que_el_operador_acepta() = runTest {
        docs.fresh["ventas"] = VENTAS
        val vm = vm()
        vm.onBind("form.quien", JsonPrimitive("app"))
        val call = vm.state.value.doc!!.body.single { it.type == "button" }.action!!
        val scope = vm.state.value.scope(NOW).with("item", json("""{"id": "order_01"}"""))

        vm.onAction(call, scope)
        assertThat(vm.state.value.confirm?.title).isEqualTo("¿Confirmar el pago de order_01?")
        assertThat(data.calls.map { it.method }).doesNotContain("PATCH")

        vm.onConfirm(accepted = true)
        val patch = data.calls.single { it.method == "PATCH" }
        assertThat(patch.path).isEqualTo("/api/orders/orders/order_01/confirm-payment")
        assertThat(patch.body).isEqualTo(json("""{"by": "app"}"""))
        assertThat(effects).contains(ScreenEffect.Message("Pago confirmado"))
        // `then: refresh` vuelve a pedir los datos.
        assertThat(data.paths().count { it.startsWith("/api/orders/orders?") }).isEqualTo(2)
        assertThat(vm.state.value.confirm).isNull()
        assertThat(vm.state.value.busy).isFalse()
    }

    @Test fun cancelar_la_confirmacion_no_llama() = runTest {
        docs.fresh["ventas"] = VENTAS
        val vm = vm()
        val call = vm.state.value.doc!!.body.single { it.type == "button" }.action!!
        vm.onAction(call, vm.state.value.scope(NOW).with("item", json("""{"id": "order_01"}""")))
        vm.onConfirm(accepted = false)
        assertThat(data.calls.map { it.method }).doesNotContain("PATCH")
        assertThat(vm.state.value.confirm).isNull()
    }

    @Test fun una_llamada_que_falla_dice_el_motivo_del_backend() = runTest {
        docs.fresh["ventas"] = VENTAS
        data.responder = { call ->
            if (call.method == "PATCH") Result.failure(ScreenCallError(409, "Ese pedido ya está pagado")) else Result.success(JsonNull)
        }
        val vm = vm()
        val call = vm.state.value.doc!!.body.single { it.type == "button" }.action!!
        vm.onAction(call, vm.state.value.scope(NOW).with("item", json("""{"id": "order_01"}""")))
        vm.onConfirm(accepted = true)
        assertThat(effects).contains(ScreenEffect.Message("Ese pedido ya está pagado"))
        assertThat(vm.state.value.busy).isFalse()
    }

    @Test fun navegar_lleva_los_parametros_del_elemento_tocado() = runTest {
        docs.fresh["ventas"] = VENTAS
        val vm = vm()
        val scope = vm.state.value.scope(NOW).with("c", json("""{"id": "mkt-1"}"""))
        vm.onAction(Action.Navigate("campana", mapOf("campaign_id" to Template.parse("{{c.id}}")), sheet = false), scope)
        vm.onAction(Action.Navigate("campana", mapOf("campaign_id" to Template.parse("{{c.id}}")), sheet = true), scope)
        vm.onAction(Action.OpenOrder(Template.parse("order_01")), scope)
        assertThat(effects).containsExactly(
            ScreenEffect.Open(ScreenKey("campana", mapOf("campaign_id" to "mkt-1"))),
            ScreenEffect.Open(ScreenSheetKey("campana", mapOf("campaign_id" to "mkt-1"))),
            ScreenEffect.Open(OrderSheetKey(OrderId.parse("order_01")!!)),
        ).inOrder()
    }

    @Test fun un_chat_o_pedido_invalido_no_navega_y_un_enlace_inseguro_no_se_abre() = runTest {
        docs.fresh["ventas"] = VENTAS
        val vm = vm()
        val scope = vm.state.value.scope(NOW)
        vm.onAction(Action.OpenOrder(Template.parse("{{nada}}")), scope)
        vm.onAction(Action.OpenChat(Template.parse("../x")), scope)
        vm.onAction(Action.OpenUrl(Template.parse("javascript:alert(1)")), scope)
        assertThat(effects.filterIsInstance<ScreenEffect.Open>()).isEmpty()
        assertThat(effects.filterIsInstance<ScreenEffect.OpenUrl>()).isEmpty()
        assertThat(effects.filterIsInstance<ScreenEffect.Message>()).hasSize(3)
    }

    @Test fun un_aviso_del_servidor_refresca_solo_las_fuentes_atadas_a_el() = runTest {
        docs.fresh["ventas"] = FILTRADA
        vm()
        changes.flow.emit("marketing")
        assertThat(data.paths()).containsExactly("/api/orders/orders?stage=new", "/api/marketing/campaigns", "/api/marketing/campaigns")
        changes.flow.emit("chats")
        assertThat(data.calls).hasSize(3)
    }

    @Test fun las_fuentes_con_every_se_renuevan_solo_mientras_la_pantalla_se_ve() = runTest {
        docs.fresh["ventas"] = VENTAS.replace("\"get\": \"/api/orders/orders\"", "\"get\": \"/api/orders/orders\", \"every\": 30")
        val vm = vm()
        vm.setVisible(true)
        advanceTimeBy(30_001)
        assertThat(data.calls).hasSize(2)
        vm.setVisible(false)
        advanceTimeBy(120_000)
        assertThat(data.calls).hasSize(2)
    }

    companion object {
        const val NOW = 1_790_000_000_000L

        fun json(raw: String): JsonElement = Json.parseToJsonElement(raw)

        val VENTAS = """
        {
          "schema": 1, "id": "ventas", "title": "Ventas",
          "data": { "pedidos": { "get": "/api/orders/orders", "query": { "limit": "500" } } },
          "body": [
            { "type": "text", "text": "{{pedidos.orders | count}} pedidos" },
            { "type": "button", "text": "Confirmar pago", "action": {
                "type": "call", "method": "PATCH", "path": "/api/orders/orders/{{item.id}}/confirm-payment",
                "body": { "by": "{{form.quien}}" },
                "confirm": { "title": "¿Confirmar el pago de {{item.id}}?", "accept": "Confirmar" },
                "success": "Pago confirmado", "then": [ { "type": "refresh" } ] } }
          ]
        }
        """.trimIndent()

        val CHATS = """{"schema": 1, "id": "chats", "data": {"chats": {"app": "conversations"}}, "body": []}"""

        val FILTRADA = """
        {
          "schema": 1, "id": "ventas", "state": { "etapa": "new" },
          "data": {
            "pedidos": { "get": "/api/orders/orders", "query": { "stage": "{{state.etapa}}" } },
            "campanas": { "get": "/api/marketing/campaigns", "optional": true, "refreshOn": ["marketing"] }
          },
          "body": [ { "type": "text", "text": "x" } ]
        }
        """.trimIndent()
    }
}

class FakeDocs : ScreenDocs {
    val cached = mutableMapOf<String, String>()
    val fresh = mutableMapOf<String, String>()
    override suspend fun cached(id: String): ScreenDoc? = cached[id]?.let { parseScreen(it).doc }
    override suspend fun fetch(id: String): ScreenDoc? = fresh[id]?.let { parseScreen(it).doc }
}

class FakeData : ScreenData {
    val calls = mutableListOf<HttpCall>()
    private val ok = mutableMapOf<String, JsonElement>()
    private val failing = mutableSetOf<String>()
    var responder: (HttpCall) -> Result<JsonElement> = { call ->
        when {
            call.path in failing -> Result.failure(ScreenCallError(0, "Sin conexión con el servidor."))
            else -> Result.success(ok[call.path] ?: JsonNull)
        }
    }

    fun ok(path: String, body: String) { ok[path] = Json.parseToJsonElement(body) }
    fun fail(path: String) { failing += path }
    fun paths() = calls.filter { it.method == "GET" }.map { it.path }

    override suspend fun execute(call: HttpCall): Result<JsonElement> {
        calls += call
        return responder(call)
    }
}

class MemoryCache : ScreenDataCache {
    val values = mutableMapOf<String, JsonElement>()
    override fun read(key: String) = values[key]
    override fun write(key: String, value: JsonElement) { values[key] = value }
    override fun clear() = values.clear()
}

class FakeChanges : ServerChanges {
    val flow = MutableSharedFlow<String>()
    override val changes = flow
}
