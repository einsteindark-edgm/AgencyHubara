package com.hubara.operator.core.data.outbox

import android.content.Context
import androidx.hilt.work.HiltWorker
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import com.hubara.operator.core.data.Clock
import com.hubara.operator.core.data.repo.ChatRepository
import com.hubara.operator.core.data.repo.SuggestionRepository
import com.hubara.operator.core.database.OutboxDao
import com.hubara.operator.core.database.OutboxEntity
import com.hubara.operator.core.model.ActionRef
import com.hubara.operator.core.model.SessionId
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.api.OperatorApi
import com.hubara.operator.core.network.api.OperatorService
import com.hubara.operator.core.network.api.ToolResult
import com.hubara.operator.core.network.api.photoPart
import com.hubara.operator.core.network.dto.HumanMessageRequest
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.serialization.builtins.MapSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import com.hubara.operator.core.model.Template
import com.hubara.operator.core.network.dto.TemplateMessageRequest

/** Programa y cancela los envíos. En la app es WorkManager; en los tests, un fake. */
interface OutboxScheduler {
    fun enqueue(id: String, delayMs: Long)
    fun cancel(id: String)
}

class WorkManagerOutboxScheduler @Inject constructor(@ApplicationContext private val context: Context) : OutboxScheduler {
    override fun enqueue(id: String, delayMs: Long) {
        val request = OneTimeWorkRequestBuilder<SendOutboxWorker>()
            .setInitialDelay(delayMs, TimeUnit.MILLISECONDS)
            .setInputData(workDataOf(SendOutboxWorker.KEY_ID to id))
            .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS)
            .addTag(TAG)
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(workName(id), ExistingWorkPolicy.KEEP, request)
    }

    override fun cancel(id: String) {
        WorkManager.getInstance(context).cancelUniqueWork(workName(id))
    }

    private fun workName(id: String) = "outbox-$id"

    companion object {
        const val TAG = "outbox"
    }
}

/**
 * Los envíos del operador. Tocar una burbuja deja la acción en Room («enviando») con 5 s para
 * deshacer; el texto sale enseguida. El servidor deduplica por `client_action_id`.
 */
@Singleton
class OutboxRepository @Inject constructor(
    private val dao: OutboxDao,
    private val scheduler: OutboxScheduler,
    private val photos: PhotoPreparer,
    private val clock: Clock,
) {
    suspend fun sendTool(sessionId: SessionId, action: ActionRef, label: String): String {
        val id = UUID.randomUUID().toString()
        dao.upsert(OutboxEntity(
            clientActionId = id, sessionId = sessionId.raw, kind = "tool", text = null, toolName = action.name,
            argsJson = OperatorJson.encodeToString(JsonObject.serializer(), action.args), label = label,
            state = "pending_undo", error = null, createdMs = clock.nowMs(),
        ))
        scheduler.enqueue(id, UNDO_WINDOW_MS)
        return id
    }

    suspend fun sendText(sessionId: SessionId, text: String): String {
        val id = UUID.randomUUID().toString()
        dao.upsert(OutboxEntity(
            clientActionId = id, sessionId = sessionId.raw, kind = "text", text = text, toolName = null,
            argsJson = null, label = text, state = "queued", error = null, createdMs = clock.nowMs(),
        ))
        scheduler.enqueue(id, 0)
        return id
    }

    /**
     * Una foto ya preparada (ver [PhotoPreparer]) con su pie, si lo trae. Sale enseguida, sin deshacer: el operador
     * ya la vio antes de tocar «Enviar». El archivo es de la app y se borra cuando sale o se descarta.
     */
    suspend fun sendPhoto(sessionId: SessionId, photo: File, caption: String?): String {
        val id = UUID.randomUUID().toString()
        dao.upsert(OutboxEntity(
            clientActionId = id, sessionId = sessionId.raw, kind = KIND_PHOTO, text = caption?.trim()?.ifEmpty { null },
            toolName = null, argsJson = PhotoArgs(photo.absolutePath).encode(), label = "Foto", state = "queued",
            error = null, createdMs = clock.nowMs(),
        ))
        scheduler.enqueue(id, 0)
        return id
    }

    /**
     * Las fotos que eligió el operador, cada una en su mensaje (como en WhatsApp); el texto va como pie de la primera
     * que se pudo leer. Las que no se pueden leer no salen y se cuentan.
     */
    suspend fun sendPhotos(sessionId: SessionId, sources: List<String>, caption: String?): PhotoBatch {
        var pendingCaption = caption?.trim()?.ifEmpty { null }
        val sent = ArrayList<String>(sources.size)
        var unreadable = 0
        for (source in sources) {
            val photo = photos.prepare(source)
            if (photo == null) {
                unreadable++
                continue
            }
            sent += sendPhoto(sessionId, photo, pendingCaption)
            pendingCaption = null
        }
        return PhotoBatch(sent, unreadable)
    }

    /** Plantilla para reactivar la conversación (ventana de 24 h cerrada). Sale enseguida, sin deshacer. */
    suspend fun sendTemplate(sessionId: SessionId, template: Template, values: Map<String, String>): String {
        val id = UUID.randomUUID().toString()
        dao.upsert(OutboxEntity(
            clientActionId = id, sessionId = sessionId.raw, kind = "template", text = null, toolName = template.name,
            argsJson = OperatorJson.encodeToString(TEMPLATE_VALUES, values), label = "Plantilla «${template.title}»",
            state = "queued", error = null, createdMs = clock.nowMs(),
        ))
        scheduler.enqueue(id, 0)
        return id
    }

    /** true si se deshizo; false si ya se estaba mandando (demasiado tarde). */
    suspend fun undo(id: String): Boolean {
        val undone = dao.undo(id) > 0
        if (undone) scheduler.cancel(id)
        return undone
    }

    suspend fun retry(id: String) {
        dao.setState(id, "queued")
        scheduler.enqueue(id, 0)
    }

    /** Descartar un envío que falló. Si era una foto, también se borra del teléfono. */
    suspend fun dismiss(id: String) {
        dao.get(id)?.takeIf { it.kind == KIND_PHOTO }?.let { row -> PhotoArgs.decode(row.argsJson)?.let { deletePhoto(it.file) } }
        dao.delete(id)
    }

    companion object {
        const val UNDO_WINDOW_MS = 5_000L
        const val KIND_PHOTO = "photo"

        /** Las que se pueden elegir de una vez. */
        const val MAX_PHOTOS = 10

        /** WhatsApp corta el pie de una foto en 1024 caracteres (el texto solo, en 4096); el backend responde 422. */
        const val MAX_CAPTION = 1024
        internal val TEMPLATE_VALUES = MapSerializer(String.serializer(), String.serializer())
    }
}

sealed interface ProcessResult {
    data object Done : ProcessResult
    data object Retry : ProcessResult
    data object Failed : ProcessResult
}

/** La lógica de un envío, separada del Worker para probarla sin WorkManager. */
@Singleton
class OutboxProcessor @Inject constructor(
    private val dao: OutboxDao,
    private val api: OperatorApi,
    private val service: OperatorService,
    private val chats: ChatRepository,
    private val suggestions: SuggestionRepository,
) {
    suspend fun process(id: String): ProcessResult {
        val row = dao.get(id) ?: return ProcessResult.Done          // se deshizo: no se manda nada
        if (dao.claim(id) == 0) return ProcessResult.Done           // ya salió o lo está mandando otro intento
        val session = SessionId.parse(row.sessionId) ?: return fail(id, "Conversación inválida.")
        return when (row.kind) {
            "text" -> sendText(row, session)
            "template" -> sendTemplate(row, session)
            OutboxRepository.KIND_PHOTO -> sendPhoto(row, session)
            else -> sendTool(row, session)
        }
    }

    suspend fun giveUp(id: String) = fail(id, "No se pudo enviar. Revisa la conexión y vuelve a intentar.")

    private suspend fun sendText(row: OutboxEntity, session: SessionId): ProcessResult = try {
        val response = api.sendMessage(session.raw, HumanMessageRequest(text = row.text, clientMessageId = row.clientActionId))
        when {
            response.isSuccessful -> sent(row, session, refreshSuggestions = false)
            response.code() == 409 -> fail(row.clientActionId, WINDOW_CLOSED)
            response.code() >= 500 || response.code() == 429 -> requeue(row)
            else -> fail(row.clientActionId, "El servidor rechazó el mensaje (${response.code()}).")
        }
    } catch (_: IOException) {
        requeue(row)
    }

    private suspend fun sendTemplate(row: OutboxEntity, session: SessionId): ProcessResult = try {
        val values = runCatching { OperatorJson.decodeFromString(OutboxRepository.TEMPLATE_VALUES, row.argsJson.orEmpty()) }
            .getOrDefault(emptyMap())
        val response = api.sendTemplate(
            session.raw,
            TemplateMessageRequest(templateName = row.toolName.orEmpty(), variables = values, clientMessageId = row.clientActionId),
        )
        when {
            response.isSuccessful -> sent(row, session, refreshSuggestions = true)
            response.code() >= 500 || response.code() == 429 -> requeue(row)
            else -> fail(row.clientActionId, "No se pudo enviar la plantilla (${response.code()}).")
        }
    } catch (_: IOException) {
        requeue(row)
    }

    private suspend fun sendTool(row: OutboxEntity, session: SessionId): ProcessResult {
        val args = runCatching { OperatorJson.decodeFromString(JsonObject.serializer(), row.argsJson.orEmpty()) }
            .getOrDefault(JsonObject(emptyMap()))
        return when (val r = service.runTool(session, ActionRef(row.toolName.orEmpty(), args), row.clientActionId)) {
            is ToolResult.Sent -> sent(row, session, refreshSuggestions = true)
            ToolResult.WindowClosed -> fail(row.clientActionId, WINDOW_CLOSED)
            ToolResult.NotInControl -> fail(row.clientActionId, NOT_IN_CONTROL)
            is ToolResult.Rejected -> fail(row.clientActionId, "No se pudo enviar «${row.label}»: ${r.detail}")
            ToolResult.Transient -> requeue(row)
        }
    }

    /**
     * Una foto sale en dos pasos: se sube (una vez; el `attachment_id` queda en la fila) y se manda con su pie. Si el
     * envío falla, el reintento usa la misma subida y el mismo `client_message_id` (el backend no la manda dos veces).
     */
    private suspend fun sendPhoto(row: OutboxEntity, session: SessionId): ProcessResult {
        val args = PhotoArgs.decode(row.argsJson) ?: return fail(row.clientActionId, PHOTO_MISSING)
        val attachment = args.attachmentId ?: when (val up = upload(row, session, args)) {
            is Upload.Done -> up.attachmentId
            is Upload.Stopped -> return up.result
        }
        return try {
            val response = api.sendMessage(
                session.raw,
                HumanMessageRequest(text = row.text, attachmentId = attachment, clientMessageId = row.clientActionId),
            )
            when {
                response.isSuccessful -> sent(row, session, refreshSuggestions = false).also { deletePhoto(args.file) }
                response.code() == 409 -> conflict(row, errorDetail(response.errorBody()?.string()))
                response.code() == 422 -> {
                    // El backend ya no reconoce esa subida: el siguiente intento la vuelve a subir.
                    dao.setArgs(row.clientActionId, args.copy(attachmentId = null).encode())
                    fail(row.clientActionId, PHOTO_LOST)
                }
                response.code() >= 500 || response.code() == 429 -> requeue(row)
                else -> fail(row.clientActionId, "El servidor rechazó la foto (${response.code()}).")
            }
        } catch (_: IOException) {
            requeue(row)
        }
    }

    private sealed interface Upload {
        data class Done(val attachmentId: String) : Upload
        data class Stopped(val result: ProcessResult) : Upload
    }

    private suspend fun upload(row: OutboxEntity, session: SessionId, args: PhotoArgs): Upload {
        val file = File(args.file)
        val bytes = withContext(Dispatchers.IO) { runCatching { file.readBytes() }.getOrNull() }
            ?: return Upload.Stopped(fail(row.clientActionId, PHOTO_MISSING))
        val response = try {
            api.uploadMedia(session.raw, photoPart(file.name, bytes))
        } catch (_: IOException) {
            return Upload.Stopped(requeue(row))
        }
        val attachment = response.body()?.attachmentId?.takeIf { response.isSuccessful && it.isNotBlank() }
        return when {
            attachment != null -> {
                dao.setArgs(row.clientActionId, args.copy(attachmentId = attachment).encode())
                Upload.Done(attachment)
            }
            response.code() == 409 -> Upload.Stopped(fail(row.clientActionId, NOT_IN_CONTROL))
            response.code() >= 500 || response.code() == 429 || response.isSuccessful -> Upload.Stopped(requeue(row))
            else -> {
                val detail = errorDetail(response.errorBody()?.string()).ifBlank { "el servidor la rechazó (${response.code()})" }
                Upload.Stopped(fail(row.clientActionId, "No se pudo subir la foto: ${detail.take(200)}"))
            }
        }
    }

    /** El 409 del envío de una foto: ventana de 24 h cerrada, la misma foto ya saliendo o el bot al mando. */
    private suspend fun conflict(row: OutboxEntity, detail: String): ProcessResult {
        val text = detail.lowercase()
        return when {
            "ventana" in text -> fail(row.clientActionId, WINDOW_CLOSED)
            "en vuelo" in text -> requeue(row)
            else -> fail(row.clientActionId, NOT_IN_CONTROL)
        }
    }

    /** `{"detail": "…"}` de FastAPI; vacío si trae otra cosa (la lista de validación, HTML de un proxy). */
    private fun errorDetail(body: String?): String =
        runCatching { OperatorJson.parseToJsonElement(body.orEmpty()).jsonObject["detail"]?.jsonPrimitive?.contentOrNull }
            .getOrNull().orEmpty()

    private suspend fun sent(row: OutboxEntity, session: SessionId, refreshSuggestions: Boolean): ProcessResult {
        // Primero el historial nuevo, después se oculta el pendiente: así el mensaje no parpadea.
        chats.refresh(session)
        if (refreshSuggestions) suggestions.refresh(session)
        dao.setState(row.clientActionId, "sent")
        return ProcessResult.Done
    }

    private suspend fun requeue(row: OutboxEntity): ProcessResult {
        dao.setState(row.clientActionId, "queued")
        return ProcessResult.Retry
    }

    private suspend fun fail(id: String, message: String): ProcessResult {
        dao.setState(id, "failed", message)
        return ProcessResult.Failed
    }

    companion object {
        const val WINDOW_CLOSED = "La ventana de 24 h está cerrada. Usa una plantilla para reactivar la conversación."
        const val NOT_IN_CONTROL = "Primero toma la conversación."
        const val PHOTO_MISSING = "La foto ya no está en el teléfono. Descártala y vuelve a elegirla."
        const val PHOTO_LOST = "No se pudo enviar la foto. Toca «Reintentar» para subirla otra vez."
    }
}

@HiltWorker
class SendOutboxWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val processor: OutboxProcessor,
) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val id = inputData.getString(KEY_ID) ?: return Result.failure()
        return when (processor.process(id)) {
            ProcessResult.Done -> Result.success()
            ProcessResult.Failed -> Result.failure()
            ProcessResult.Retry -> if (runAttemptCount < MAX_ATTEMPTS) Result.retry() else {
                processor.giveUp(id)
                Result.failure()
            }
        }
    }

    companion object {
        const val KEY_ID = "client_action_id"
        private const val MAX_ATTEMPTS = 5
    }
}
