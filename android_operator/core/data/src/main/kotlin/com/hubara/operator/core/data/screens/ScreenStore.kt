package com.hubara.operator.core.data.screens

import android.content.Context
import com.hubara.operator.core.sdui.AppManifest
import com.hubara.operator.core.sdui.MAX_TABS
import com.hubara.operator.core.sdui.MIN_TABS
import com.hubara.operator.core.sdui.ScreenDoc
import com.hubara.operator.core.sdui.parseAppManifest
import com.hubara.operator.core.sdui.parseScreen
import dagger.hilt.android.qualifiers.ApplicationContext
import java.io.File
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request

/** De dónde bajan las pantallas: `<cloudfront>/mobile/screens/` (o lo que diga `screens_url` en config.json). */
fun interface ScreensBase {
    fun url(): HttpUrl?
}

/** Lo que trae el APK en `assets/screens/` (las pantallas del repo al compilar): sirve sin red desde la primera vez. */
fun interface BundledScreens {
    fun read(file: String): String?
}

/** Lo que el ViewModel de una pantalla necesita para conseguir su definición. */
interface ScreenDocs {
    /** Lo mejor que hay sin red: la última que bajó o la que trae el APK. */
    suspend fun cached(id: String): ScreenDoc?

    /** La del servidor. null si no hay red o llegó inválida (la guardada sigue valiendo). */
    suspend fun fetch(id: String): ScreenDoc?
}

/** Ids de pantalla: minúsculas, números y guion bajo. Arman un nombre de archivo y una URL: nada de `..` ni `/`. */
private val SCREEN_ID = Regex("^[a-z0-9_]{1,64}$")

/**
 * Las pantallas que define el servidor y el manifiesto con las pestañas extra (`app.json`). Las archivos son
 * estáticos y públicos (no llevan datos ni secretos): bajan sin token, con su propio cliente HTTP. Lo que baja se
 * guarda solo si se lee como pantalla válida y con el mismo id: un 404 o la SPA del CDN nunca pisan la buena.
 */
@Singleton
class ScreenStore @Inject constructor(
    @ApplicationContext context: Context,
    private val base: ScreensBase,
    private val bundled: BundledScreens,
) : ScreenDocs {
    private val dir = File(context.filesDir, "screens")
    private val http = OkHttpClient.Builder().callTimeout(10, TimeUnit.SECONDS).build()

    /**
     * Las pestañas extra. Se leen al arrancar y no cambian en caliente (cambiarlas reinicia la navegación): lo que
     * baja [refreshManifest] se aplica la próxima vez que se abre la app.
     */
    val manifest: AppManifest = listOfNotNull(read(MANIFEST), bundled.read(MANIFEST)).firstNotNullOfOrNull(::usableManifest)
        ?: AppManifest(emptyList())

    /** Un manifiesto que la app puede usar: pestañas con id válido y texto, entre MIN_TABS y MAX_TABS. */
    private fun usableManifest(raw: String): AppManifest? {
        val parsed = parseAppManifest(raw).manifest ?: return null
        val tabs = parsed.tabs.filter { SCREEN_ID.matches(it.screen) && it.label.isNotBlank() }.distinctBy { it.screen }.take(MAX_TABS)
        return if (tabs.size >= MIN_TABS) parsed.copy(tabs = tabs) else null
    }

    override suspend fun cached(id: String): ScreenDoc? = withContext(Dispatchers.IO) {
        if (!SCREEN_ID.matches(id)) return@withContext null
        listOfNotNull(read("$id.json"), bundled.read("$id.json")).firstNotNullOfOrNull { parse(id, it) }
    }

    override suspend fun fetch(id: String): ScreenDoc? = withContext(Dispatchers.IO) {
        if (!SCREEN_ID.matches(id)) return@withContext null
        val body = download("$id.json") ?: return@withContext null
        parse(id, body)?.also { write("$id.json", body) }
    }

    /** Baja el manifiesto; true si llegó uno válido (queda guardado para el próximo arranque). */
    suspend fun refreshManifest(): Boolean = withContext(Dispatchers.IO) {
        val body = download(MANIFEST) ?: return@withContext false
        if (usableManifest(body) == null) return@withContext false
        write(MANIFEST, body)
        true
    }

    private fun parse(id: String, raw: String): ScreenDoc? = parseScreen(raw).doc?.takeIf { it.id == id }

    private fun download(file: String): String? {
        val url = base.url()?.resolve(file) ?: return null
        return runCatching {
            http.newCall(Request.Builder().url(url).header("Cache-Control", "no-cache").build()).execute()
                .use { if (it.isSuccessful) it.body.string() else null }
        }.getOrNull()
    }

    private fun read(file: String): String? = File(dir, file).takeIf { it.isFile }?.readText()

    /** Escritura atómica: un corte a mitad no deja un archivo roto. */
    private fun write(file: String, body: String) {
        dir.mkdirs()
        val tmp = File(dir, "$file.tmp")
        tmp.writeText(body)
        tmp.renameTo(File(dir, file))
    }

    private companion object {
        const val MANIFEST = "app.json"
    }
}
