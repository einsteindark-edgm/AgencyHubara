package com.hubara.operator.core.data

import androidx.room.Room
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.google.common.truth.Truth.assertThat
import com.hubara.operator.core.data.outbox.OutboxProcessor
import com.hubara.operator.core.data.outbox.OutboxRepository
import com.hubara.operator.core.data.outbox.ProcessResult
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.OutboxState
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.database.OperatorDatabase
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.DeliveryState
import com.hubara.operator.core.model.Route
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.dto.ChatMessageDto
import com.hubara.operator.core.network.dto.OrderRefDto
import com.hubara.operator.core.network.dto.SessionDetailsDto
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonPrimitive
import org.junit.After
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ChatAndOutboxTest {
    private lateinit var db: OperatorDatabase
    private val api = FakeOperatorApi()
    private val scheduler = FakeScheduler()
    private lateinit var chats: ChatRepository
    private lateinit var outbox: OutboxRepository
    private lateinit var processor: OutboxProcessor
    private val laura = SessionId.parse("wa_test_laura")!!

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), OperatorDatabase::class.java)
            .allowMainThreadQueries().build()
        chats = ChatRepository(api, db.conversations(), db.messages(), db.outbox(), db.drafts())
        outbox = OutboxRepository(db.outbox(), scheduler) { 10_000L }
        processor = OutboxProcessor(db.outbox(), api, OperatorService(api), chats, SuggestionRepository(api, db.suggestions()))
        api.details["wa_test_laura"] = SessionDetailsDto(
            sessionId = "wa_test_laura", phoneNumber = "x", activeAgentRoute = "humano",
            serviceWindowExpiresAtMs = 99_999, orderRef = OrderRefDto("order_01HX", "38", "confirmed", 1),
            messages = listOf(ChatMessageDto("user_message", "user", "hola", timestamp = JsonPrimitive("2026-09-29T15:00:00+00:00"))),
        )
    }

    @After fun tearDown() = db.close()

    @Test fun el_detalle_trae_ruta_pedido_ventana_y_mensajes() = runTest {
        chats.refresh(laura)
        val view = chats.observeChat(laura).first()
        assertThat(view.route).isEqualTo(Route.HUMAN)
        assertThat(view.orderRef!!.displayId).isEqualTo("38")
        assertThat(view.windowExpiresAtMs).isEqualTo(99_999)
        assertThat(view.messages.map { it.text }).containsExactly("hola")
    }

    // Caso 2026-10-09: el chat se pinta desde Room; si la marca de «llegó tarde» no se guarda, la
    // burbuja vuelve a mostrar solo la hora de llegada y parece una respuesta a la plantilla.
    @Test fun un_mensaje_que_llego_tarde_conserva_cuando_se_escribio() = runTest {
        api.details["wa_test_laura"] = api.details["wa_test_laura"]!!.copy(
            messages = listOf(
                ChatMessageDto(
                    "user_message", "user", "hola, ¿siguen teniendo velas?",
                    timestamp = JsonPrimitive("2026-10-09T21:35:42+00:00"),
                    sentAt = JsonPrimitive("2026-10-06T17:02:04+00:00"),
                    arrivedAfterWindow = JsonPrimitive(true),
                ),
            ),
        )
        chats.refresh(laura)
        val message = chats.observeChat(laura).first().messages.single()
        assertThat(message.sentAtMs).isEqualTo(1791306124000L)
        assertThat(message.arrivedAfterWindow).isTrue()
    }

    @Test fun el_texto_aparece_como_pendiente_y_desaparece_al_confirmar() = runTest {
        chats.refresh(laura)
        val id = outbox.sendText(laura, "te confirmo el aroma")
        assertThat(scheduler.enqueued).containsExactly(id to 0L)
        val pending = chats.observeChat(laura).first().messages.last()
        assertThat(pending.author).isEqualTo(Author.HUMAN)
        assertThat(pending.state).isEqualTo(DeliveryState.PENDING)

        api.details["wa_test_laura"] = api.details["wa_test_laura"]!!.let {
            it.copy(messages = it.messages + ChatMessageDto("human_message", "assistant", "te confirmo el aroma"))
        }
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)
        assertThat(api.sentMessages.single().clientMessageId).isEqualTo(id)
        val msgs = chats.observeChat(laura).first().messages
        assertThat(msgs.map { it.text }).containsExactly("hola", "te confirmo el aroma").inOrder()
        assertThat(msgs.last().state).isEqualTo(DeliveryState.SENT)
    }

    @Test fun deshacer_dentro_de_los_5_s_no_manda_nada() = runTest {
        val id = outbox.sendTool(laura, ActionRef("present_variant_picker"), "Enviar aromas")
        assertThat(scheduler.enqueued).containsExactly(id to OutboxRepository.UNDO_WINDOW_MS)
        assertThat(chats.observeChat(laura).first().pendingActions.single().state).isEqualTo(OutboxState.PENDING_UNDO)

        assertThat(outbox.undo(id)).isTrue()
        assertThat(scheduler.cancelled).containsExactly(id)
        assertThat(chats.observeChat(laura).first().pendingActions).isEmpty()
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)
        assertThat(api.toolRequests).isEmpty()
    }

    @Test fun ya_no_se_puede_deshacer_cuando_el_envio_empezo() = runTest {
        val id = outbox.sendTool(laura, ActionRef("present_products"), "Enviar productos")
        db.outbox().claim(id)   // el worker ya lo tomó
        assertThat(outbox.undo(id)).isFalse()
        assertThat(scheduler.cancelled).isEmpty()
    }

    @Test fun la_tool_viaja_con_su_client_action_id() = runTest {
        val id = outbox.sendTool(laura, ActionRef("send_payment_methods"), "Medios de pago")
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)
        val (tool, body) = api.toolRequests.single()
        assertThat(tool).isEqualTo("send_payment_methods")
        assertThat(body.clientActionId).isEqualTo(id)
        assertThat(api.calls).contains("suggestions:wa_test_laura")
        // La app avisa que sabe abrir pantallas desde una burbuja: sin eso el backend no le manda «Crear pedido».
        assertThat(api.suggestionFeatures.toSet()).containsExactly("open_screen")
    }

    @Test fun la_plantilla_viaja_con_sus_variables_y_su_client_message_id() = runTest {
        val template = com.hubara.operator.core.model.Template("seguimiento_humano", "Hola {{1}}",
            listOf(com.hubara.operator.core.model.TemplateVariable("cliente", null, 30)), isDefault = true, needsImage = false)
        val id = outbox.sendTemplate(laura, template, mapOf("cliente" to "Laura"))
        assertThat(scheduler.enqueued).containsExactly(id to 0L)
        // Artemis vio «Enviando «Plantilla «human_followup_utility_v1»»»: la barra habla con el nombre legible.
        assertThat(chats.observeChat(laura).first().pendingActions.single().label).isEqualTo("Plantilla «seguimiento humano»")
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)
        val sent = api.sentTemplates.single()
        assertThat(sent.templateName).isEqualTo("seguimiento_humano")
        assertThat(sent.variables).containsExactly("cliente", "Laura")
        assertThat(sent.clientMessageId).isEqualTo(id)
    }

    @Test fun ventana_cerrada_queda_como_fallido_con_mensaje_y_5xx_se_reintenta() = runTest {
        api.runToolCode = 409
        val cerrada = outbox.sendTool(laura, ActionRef("present_products"), "Enviar productos")
        assertThat(processor.process(cerrada)).isEqualTo(ProcessResult.Failed)
        val failed = chats.observeChat(laura).first().pendingActions.single()
        assertThat(failed.state).isEqualTo(OutboxState.FAILED)
        assertThat(failed.error).isEqualTo(OutboxProcessor.WINDOW_CLOSED)

        api.runToolCode = 503
        val caido = outbox.sendTool(laura, ActionRef("present_products"), "Enviar productos")
        assertThat(processor.process(caido)).isEqualTo(ProcessResult.Retry)
        assertThat(db.outbox().get(caido)!!.state).isEqualTo("queued")
    }
}
