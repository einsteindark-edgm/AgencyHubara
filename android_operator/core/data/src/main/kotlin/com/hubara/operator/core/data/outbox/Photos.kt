package com.hubara.operator.core.data.outbox

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ImageDecoder
import android.net.Uri
import com.hubara.operator.core.network.OperatorJson
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.util.UUID
import javax.inject.Inject
import kotlin.math.roundToInt
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Lo que salió de un lote de fotos: los envíos que quedaron en el outbox y cuántas no se pudieron leer. */
data class PhotoBatch(val sent: List<String>, val unreadable: Int)

/** Deja una foto elegida por el operador lista para WhatsApp, en un archivo propio de la app. */
interface PhotoPreparer {
    /** null = no se pudo leer (no es una imagen, ya no está, sin permiso). */
    suspend fun prepare(source: String): File?

    /** Borra las que quedaron preparadas (al cerrar sesión). */
    suspend fun clear()
}

/**
 * Lo que la fila de una foto guarda en `argsJson`: dónde está el archivo y, cuando ya se subió, su `attachment_id`
 * (el `media_id` de WhatsApp), para que un reintento no vuelva a subir los bytes.
 */
@Serializable
internal data class PhotoArgs(
    val file: String,
    @SerialName("attachment_id") val attachmentId: String? = null,
) {
    fun encode(): String = OperatorJson.encodeToString(serializer(), this)

    companion object {
        fun decode(raw: String?): PhotoArgs? =
            raw?.let { runCatching { OperatorJson.decodeFromString(serializer(), it) }.getOrNull() }
    }
}

/** La vista previa de una foto que todavía no sale: el archivo local (Coil lo abre por `file://`). */
internal fun photoPreviewUrl(argsJson: String?): String? = PhotoArgs.decode(argsJson)?.let { "file://${it.file}" }

internal fun deletePhoto(path: String) {
    runCatching { File(path).delete() }
}

/** Lado largo máximo de lo que sale: lo que usa WhatsApp para fotos; nunca se agranda una más chica. */
const val PHOTO_MAX_SIDE = 1600

/** Calidad JPEG de lo que sale: una foto de 1600 px queda en ~300–700 KB (el backend acepta hasta 5 MB). */
private const val JPEG_QUALITY = 82

fun photoTargetSize(width: Int, height: Int, maxSide: Int = PHOTO_MAX_SIDE): Pair<Int, Int> {
    val longest = maxOf(width, height)
    if (longest <= maxSide || longest <= 0) return width to height
    val scale = maxSide.toDouble() / longest
    return maxOf(1, (width * scale).roundToInt()) to maxOf(1, (height * scale).roundToInt())
}

/**
 * Decodifica con [ImageDecoder] (aplica la rotación de la cámara y reduce al decodificar, sin cargar los 12 MP),
 * pinta sobre blanco lo transparente y guarda un JPEG nuevo: los metadatos del original (EXIF, ubicación) no pasan.
 */
class AndroidPhotoPreparer @Inject constructor(@ApplicationContext private val context: Context) : PhotoPreparer {
    private val dir get() = File(context.filesDir, DIR)

    override suspend fun prepare(source: String): File? = withContext(Dispatchers.IO) {
        runCatching {
            val decoded = ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, Uri.parse(source))) { decoder, info, _ ->
                val (w, h) = photoTargetSize(info.size.width, info.size.height)
                decoder.setTargetSize(w, h)
                decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
            }
            val opaque = Bitmap.createBitmap(decoded.width, decoded.height, Bitmap.Config.ARGB_8888)
            Canvas(opaque).apply {
                drawColor(Color.WHITE)
                drawBitmap(decoded, 0f, 0f, null)
            }
            decoded.recycle()
            val out = File(dir.apply { mkdirs() }, "${UUID.randomUUID()}.jpg")
            try {
                out.outputStream().use { check(opaque.compress(Bitmap.CompressFormat.JPEG, JPEG_QUALITY, it)) }
            } catch (e: Exception) {
                out.delete()
                throw e
            } finally {
                opaque.recycle()
            }
            out
        }.getOrNull()
    }

    override suspend fun clear() {
        withContext(Dispatchers.IO) { dir.deleteRecursively() }
    }

    companion object {
        const val DIR = "outbox_photos"
    }
}
