package com.hubara.operator.core.push

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.core.stringSetPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.hilt.work.HiltWorker
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.ProcessLifecycleOwner
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.ExistingWorkPolicy
import androidx.work.NetworkType
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.config.ServerConfigStore
import com.hubara.operator.core.data.auth.AuthState
import com.hubara.operator.core.data.repo.FireRepository
import com.hubara.operator.core.model.Fire
import com.hubara.operator.core.network.OperatorJson
import com.hubara.operator.core.network.api.OperatorApi
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map

val Context.ambientStore: DataStore<Preferences> by preferencesDataStore(name = "ambient")

/** Estado de «fuera de la app»: las páginas del widget y los incendios ya avisados. */
@Singleton
class AmbientStore @Inject constructor(@ApplicationContext private val context: Context) {
    private val notifiedKey = stringSetPreferencesKey("notified_fires")

    /** Cada página por separado: si una no se pudo renovar, las otras siguen al día. */
    private fun pageKey(kind: WidgetPageKind) = stringPreferencesKey("widget_${kind.name.lowercase()}")

    val pages: Flow<WidgetPages> = context.ambientStore.data.map { prefs ->
        fun read(kind: WidgetPageKind) = prefs[pageKey(kind)]?.let { runCatching { OperatorJson.decodeFromString(WidgetPage.serializer(), it) }.getOrNull() }
        WidgetPages(read(WidgetPageKind.HOT), read(WidgetPageKind.FIRES), read(WidgetPageKind.HUMAN))
    }

    /** Guarda una página con [MAX_WIDGET_ROWS] filas como mucho. */
    suspend fun savePage(kind: WidgetPageKind, page: WidgetPage) {
        val bounded = page.copy(rows = page.rows.take(MAX_WIDGET_ROWS))
        context.ambientStore.edit { it[pageKey(kind)] = OperatorJson.encodeToString(WidgetPage.serializer(), bounded) }
    }

    suspend fun notified(): Set<String> = context.ambientStore.data.first()[notifiedKey].orEmpty()

    /** Al cerrar sesión: ni las páginas del widget (nombres de clientes) ni los avisados quedan en el teléfono. */
    suspend fun clear() {
        context.ambientStore.edit { it.clear() }
    }

    /** Guarda los avisados que siguen vigentes: si un incendio sale y vuelve, vuelve a avisar. */
    suspend fun setNotified(ids: Set<String>) {
        context.ambientStore.edit { it[notifiedKey] = ids }
    }

}

/** Qué incendios graves hay que avisar ahora: los nuevos, no los que ya se avisaron. */
fun firesToNotify(graves: List<Fire>, alreadyNotified: Set<String>): List<Fire> =
    graves.filter { it.id.raw !in alreadyNotified }

/**
 * Una vuelta del vigía: refresca los incendios, las ventas calientes y los chats con humano, actualiza el widget y, con la app en segundo
 * plano, avisa los incendios graves nuevos (con la app abierta no avisa: el radar ya lo muestra). La corren el push
 * (`PushHandler`, en segundos) y [VigiaWorker] (cada 15 minutos, de respaldo si un push no llega).
 */
class Vigia @Inject constructor(
    @ApplicationContext private val context: Context,
    private val auth: AuthRepository,
    private val fires: FireRepository,
    private val api: OperatorApi,
    private val store: AmbientStore,
    private val widget: HotWidgetUpdater,
    private val serverConfig: ServerConfigStore,
) : VigiaPass {

    /** true si quedó al día (o no hay sesión: no hay nada que poner al día); false = reintentar. */
    override suspend fun run(): Boolean {
        // Corre con la app cerrada (tras reiniciar el teléfono o borrar datos): primero a qué servidor ir. Sin esto,
        // sin configuración guardada iba a la dirección de respaldo del build (lo encontró el escenario S12).
        serverConfig.refresh()
        auth.restore()
        val state = auth.state.value
        if (state != AuthState.SignedIn && state != AuthState.DevMode) return true

        // Cada página por su lado: la que no llegó se queda con lo último que trajo.
        runCatching { store.savePage(WidgetPageKind.HOT, hotPage(api.hot().hot)) }
        runCatching { store.savePage(WidgetPageKind.HUMAN, humanPage(api.human())) }
        val firesOk = fires.refresh().isSuccess
        store.savePage(WidgetPageKind.FIRES, firesPage(fires.observeFeed().first()))
        widget.update(context)

        if (!firesOk) return false
        val graves = fires.observeRadar().first()
        val notified = store.notified()
        val foreground = ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
        if (!foreground) firesToNotify(graves, notified).forEach { notifyFire(context, it, graves.size) }
        store.setNotified(graves.map { it.id.raw }.toSet())
        return true
    }
}

/** El vigía en WorkManager: cada 15 minutos (respaldo del push) y cuando un push no alcanzó a atenderse. */
@HiltWorker
class VigiaWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val vigia: Vigia,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = if (vigia.run()) Result.success() else Result.retry()

    companion object {
        private const val NAME = "vigia"
        private const val NAME_NOW = "vigia-push"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<VigiaWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }

        /** Una vuelta apenas haya red. Si ya hay una en curso, va después (lo que cambió mientras tanto no se pierde). */
        fun runSoon(context: Context) {
            val request = OneTimeWorkRequestBuilder<VigiaWorker>()
                .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
                .build()
            WorkManager.getInstance(context).enqueueUniqueWork(NAME_NOW, ExistingWorkPolicy.APPEND_OR_REPLACE, request)
        }
    }
}

/** El widget vive en otro módulo: la app le da cómo actualizarse. */
fun interface HotWidgetUpdater {
    suspend fun update(context: Context)
}
