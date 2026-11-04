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
import com.hubara.operator.core.model.Author
import com.hubara.operator.core.model.DeliveryState
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.dto.HumanMessageRequest
import com.hubara.operator.core.network.dto.SessionDetailsDto
import java.io.File
import java.io.IOException
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.runner.RunWith

/**
 * Fotos del operador en una conversación que atiende una persona. Salen en dos pasos, como en el dashboard web:
 * se suben una vez (`POST …/media` → `attachment_id`) y se envían con su pie (`POST …/messages`). Un reintento del
 * envío nunca vuelve a subir los bytes, y lo que el teléfono preparó se borra cuando ya salió o se descarta.
 */
@RunWith(AndroidJUnit4::class)
class PhotoOutboxTest {
    @get:Rule val tmp = TemporaryFolder()

    private lateinit var db: OperatorDatabase
    private val api = FakeOperatorApi()
    private val scheduler = FakeScheduler()
    private lateinit var chats: ChatRepository
    private lateinit var processor: OutboxProcessor
    private val laura = SessionId.parse("wa_test_laura")!!
    private val jpeg = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0x01, 0x02, 0x03)
    private lateinit var photo: File

    private fun outbox(vararg prepared: Pair<String, File>) =
        OutboxRepository(db.outbox(), scheduler, FakePhotoPreparer(prepared.toMap())) { 10_000L }

    @Before fun setUp() {
        db = Room.inMemoryDatabaseBuilder(ApplicationProvider.getApplicationContext(), OperatorDatabase::class.java)
            .allowMainThreadQueries().build()
        chats = ChatRepository(api, db.conversations(), db.messages(), db.outbox(), db.drafts())
        processor = OutboxProcessor(db.outbox(), api, OperatorService(api), chats, SuggestionRepository(api, db.suggestions()))
        api.details["wa_test_laura"] = SessionDetailsDto(sessionId = "wa_test_laura", phoneNumber = "x", activeAgentRoute = "humano")
        photo = tmp.newFile("vela.jpg").apply { writeBytes(jpeg) }
    }

    @After fun tearDown() = db.close()

    @Test fun la_foto_se_sube_y_sale_con_su_pie_y_deja_de_ocupar_el_telefono() = runTest {
        val id = outbox().sendPhoto(laura, photo, "Así queda la vela de lavanda")
        assertThat(scheduler.enqueued).contains(id to 0L)

        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)

        val upload = api.uploads.single()
        assertThat(upload.session).isEqualTo("wa_test_laura")
        assertThat(upload.mime).isEqualTo("image/jpeg")
        assertThat(upload.bytes).isEqualTo(jpeg)
        assertThat(api.sentMessages).containsExactly(
            HumanMessageRequest(text = "Así queda la vela de lavanda", attachmentId = "media-1", clientMessageId = id),
        )
        assertThat(db.outbox().get(id)!!.state).isEqualTo("sent")
        assertThat(photo.exists()).isFalse()
    }

    @Test fun si_el_envio_falla_el_reintento_no_vuelve_a_subir_la_foto() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.sendMessageCode = 503
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Retry)
        assertThat(photo.exists()).isTrue()

        api.sendMessageCode = 200
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)

        assertThat(api.uploads).hasSize(1)
        assertThat(api.sentMessages.map { it.attachmentId to it.clientMessageId }).containsExactly("media-1" to id, "media-1" to id)
        assertThat(api.sentMessages.first().text).isNull()
    }

    @Test fun sin_conexion_al_subir_se_reintenta_despues() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.uploadError = IOException("sin red")
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Retry)
        assertThat(db.outbox().get(id)!!.state).isEqualTo("queued")
        assertThat(api.sentMessages).isEmpty()
    }

    @Test fun whatsapp_caido_al_subir_se_reintenta_despues() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.uploadCode = 502
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Retry)
        assertThat(api.sentMessages).isEmpty()
    }

    @Test fun si_el_bot_volvio_a_atender_la_foto_no_sale_y_dice_por_que() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.uploadCode = 409
        api.uploadBody = """{"detail":"La sesión wa_test_laura no está en ruta humano (active_route='ventas'). Pulsa 'Intervenir' antes de subir archivos."}"""

        assertThat(processor.process(id)).isEqualTo(ProcessResult.Failed)

        assertThat(db.outbox().get(id)!!.error).isEqualTo("Primero toma la conversación.")
        assertThat(api.sentMessages).isEmpty()
    }

    @Test fun una_foto_que_el_servidor_no_acepta_falla_con_su_motivo() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.uploadCode = 415
        api.uploadBody = """{"detail":"Los bytes no son una imagen JPEG/PNG válida."}"""

        assertThat(processor.process(id)).isEqualTo(ProcessResult.Failed)

        assertThat(db.outbox().get(id)!!.error).isEqualTo("No se pudo subir la foto: Los bytes no son una imagen JPEG/PNG válida.")
    }

    @Test fun con_la_ventana_cerrada_la_foto_no_sale() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.sendMessageCode = 409
        api.sendMessageBody = """{"detail":"La ventana de servicio de 24h de WhatsApp está cerrada: no se puede mandar un mensaje libre."}"""

        assertThat(processor.process(id)).isEqualTo(ProcessResult.Failed)

        assertThat(db.outbox().get(id)!!.error).isEqualTo(OutboxProcessor.WINDOW_CLOSED)
    }

    // Dos toques o un reintento mientras el primero sigue saliendo: el backend responde 409 «en vuelo» y el primero
    // termina. Reintentar con el mismo client_message_id es seguro (el backend lo reconoce y no la manda dos veces).
    @Test fun si_la_misma_foto_ya_esta_saliendo_se_reintenta_y_no_se_da_por_perdida() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        api.sendMessageCode = 409
        api.sendMessageBody = """{"detail":"Ese mensaje ya se está enviando (request en vuelo). Espera unos segundos antes de reintentar."}"""

        assertThat(processor.process(id)).isEqualTo(ProcessResult.Retry)
    }

    // El backend ya no reconoce la subida (422: el vínculo media_id → foto se perdió): reintentar la vuelve a subir.
    @Test fun si_el_servidor_perdio_la_subida_reintentar_la_sube_otra_vez() = runTest {
        val box = outbox()
        val id = box.sendPhoto(laura, photo, null)
        api.sendMessageCode = 422
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Failed)

        api.sendMessageCode = 200
        box.retry(id)
        assertThat(processor.process(id)).isEqualTo(ProcessResult.Done)

        assertThat(api.uploads).hasSize(2)
        assertThat(api.sentMessages.last().attachmentId).isEqualTo("media-2")
    }

    @Test fun la_foto_que_ya_no_esta_en_el_telefono_falla_sin_subir_nada() = runTest {
        val id = outbox().sendPhoto(laura, photo, null)
        photo.delete()

        assertThat(processor.process(id)).isEqualTo(ProcessResult.Failed)

        assertThat(api.uploads).isEmpty()
        assertThat(db.outbox().get(id)!!.error).isEqualTo(OutboxProcessor.PHOTO_MISSING)
    }

    @Test fun la_foto_que_sale_se_ve_en_el_chat_y_si_falla_se_puede_reintentar_o_descartar() = runTest {
        val box = outbox()
        val id = box.sendPhoto(laura, photo, "¿Te gusta este color?")

        val sending = chats.observeChat(laura).first()
        val bubble = sending.messages.single { it.key == "outbox:$id" }
        assertThat(bubble.author).isEqualTo(Author.HUMAN)
        assertThat(bubble.imageUrl).isEqualTo("file://${photo.absolutePath}")
        assertThat(bubble.text).isEqualTo("¿Te gusta este color?")
        assertThat(bubble.state).isEqualTo(DeliveryState.PENDING)
        // Mientras sale, la burbuja ya lo dice: la barra de abajo no repite «Enviando «Foto»…».
        assertThat(sending.pendingActions).isEmpty()

        api.uploadCode = 409
        processor.process(id)
        val failed = chats.observeChat(laura).first()
        assertThat(failed.messages.single { it.key == "outbox:$id" }.state).isEqualTo(DeliveryState.FAILED)
        val action = failed.pendingActions.single()
        assertThat(action.id).isEqualTo(id)
        assertThat(action.state).isEqualTo(OutboxState.FAILED)
        assertThat(action.error).isEqualTo("Primero toma la conversación.")

        box.dismiss(id)
        assertThat(db.outbox().get(id)).isNull()
        assertThat(photo.exists()).isFalse()
    }

    @Test fun varias_fotos_salen_cada_una_en_su_mensaje_y_el_texto_va_con_la_primera() = runTest {
        val otra = tmp.newFile("otra.jpg").apply { writeBytes(jpeg) }
        val box = outbox("content://media/1" to photo, "content://media/2" to otra)

        val batch = box.sendPhotos(laura, listOf("content://media/1", "content://media/2"), "Estas son las dos")

        assertThat(batch.sent).hasSize(2)
        assertThat(batch.unreadable).isEqualTo(0)
        val rows = batch.sent.map { db.outbox().get(it)!! }
        assertThat(rows.map { it.text }).containsExactly("Estas son las dos", null).inOrder()
        assertThat(scheduler.enqueued.map { it.first }).containsExactlyElementsIn(batch.sent)
    }

    @Test fun la_foto_que_no_se_puede_leer_no_se_lleva_el_texto() = runTest {
        val box = outbox("content://media/2" to photo)

        val batch = box.sendPhotos(laura, listOf("content://media/roto", "content://media/2"), "Mira esta")

        assertThat(batch.unreadable).isEqualTo(1)
        assertThat(db.outbox().get(batch.sent.single())!!.text).isEqualTo("Mira esta")
    }
}
