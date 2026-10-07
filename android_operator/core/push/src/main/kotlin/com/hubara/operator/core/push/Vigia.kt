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
import com.hubara.operator.core.network.dto.HotSaleDto
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.builtins.ListSerializer

val Context.ambientStore: DataStore<Preferences> by preferencesDataStore(name = "ambient")

/** Estado de «fuera de la app»: las ventas calientes del widget y los incendios ya avisados. */
@Singleton
class AmbientStore @Inject constructor(@ApplicationContext private val context: Context) {
    private val hotKey = stringPreferencesKey("hot_json")
    private val notifiedKey = stringSetPreferencesKey("notified_fires")

    val hot: Flow<List<HotSaleDto>> = context.ambientStore.data.map { prefs ->
        prefs[hotKey]?.let { runCatching { OperatorJson.decodeFromString(HOT_LIST, it) }.getOrNull() }.orEmpty()
    }

    suspend fun saveHot(list: List<HotSaleDto>) {
        context.ambientStore.edit { it[hotKey] = OperatorJson.encodeToString(HOT_LIST, list.take(MAX_WIDGET_ROWS)) }
    }

    suspend fun notified(): Set<String> = context.ambientStore.data.first()[notifiedKey].orEmpty()

    /** Al cerrar sesión: ni ventas calientes ni avisados quedan en el teléfono. */
    suspend fun clear() {
        context.ambientStore.edit { it.clear() }
    }

    /** Guarda los avisados que siguen vigentes: si un incendio sale y vuelve, vuelve a avisar. */
    suspend fun setNotified(ids: Set<String>) {
        context.ambientStore.edit { it[notifiedKey] = ids }
    }

    companion object {
        const val MAX_WIDGET_ROWS = 3
        private val HOT_LIST = ListSerializer(HotSaleDto.serializer())
    }
}

/** Qué incendios graves hay que avisar ahora: los nuevos, no los que ya se avisaron. */
fun firesToNotify(graves: List<Fire>, alreadyNotified: Set<String>): List<Fire> =
    graves.filter { it.id.raw !in alreadyNotified }

/**
 * Una vuelta del vigía: refresca los incendios y las ventas calientes, actualiza el widget y, con la app en segundo
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

        runCatching { store.saveHot(api.hot().hot) }
        widget.update(context)

        if (fires.refresh().isFailure) return false
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
