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
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import com.hubara.operator.core.data.auth.AuthRepository
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
 * El vigía con la app cerrada, hasta que llegue FCM: cada 15 minutos refresca los incendios y las ventas
 * calientes, avisa los incendios graves nuevos y actualiza el widget. Con la app abierta no avisa (el
 * radar ya lo muestra).
 */
@HiltWorker
class VigiaWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val auth: AuthRepository,
    private val fires: FireRepository,
    private val api: OperatorApi,
    private val store: AmbientStore,
    private val widget: HotWidgetUpdater,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        auth.restore()
        val state = auth.state.value
        if (state != AuthState.SignedIn && state != AuthState.DevMode) return Result.success()

        runCatching { store.saveHot(api.hot().hot) }
        widget.update(applicationContext)

        if (fires.refresh().isFailure) return Result.retry()
        val graves = fires.observeRadar().first()
        val notified = store.notified()
        val foreground = ProcessLifecycleOwner.get().lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
        if (!foreground) firesToNotify(graves, notified).forEach { notifyFire(applicationContext, it, graves.size) }
        store.setNotified(graves.map { it.id.raw }.toSet())
        return Result.success()
    }

    companion object {
        private const val NAME = "vigia"

        fun schedule(context: Context) {
            val request = PeriodicWorkRequestBuilder<VigiaWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
                .build()
            WorkManager.getInstance(context).enqueueUniquePeriodicWork(NAME, ExistingPeriodicWorkPolicy.KEEP, request)
        }
    }
}

/** El widget vive en otro módulo: la app le da cómo actualizarse. */
fun interface HotWidgetUpdater {
    suspend fun update(context: Context)
}
