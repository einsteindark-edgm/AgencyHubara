package com.hubara.operator.core.push

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
import com.google.android.gms.tasks.Task
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import com.hubara.operator.core.data.auth.AuthRepository
import com.hubara.operator.core.data.auth.AuthState
import dagger.assisted.Assisted
import dagger.assisted.AssistedInject
import dagger.hilt.android.AndroidEntryPoint
import dagger.hilt.android.qualifiers.ApplicationContext
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.suspendCancellableCoroutine

/** Firebase de verdad. Se arranca a mano con las opciones del backend (no hay `google-services.json`). */
class FirebasePushTransport @Inject constructor(@ApplicationContext private val context: Context) : PushTransport {

    override fun start(options: PushOptions): Boolean {
        val wanted = FirebaseOptions.Builder()
            .setProjectId(options.projectId)
            .setApplicationId(options.applicationId)
            .setApiKey(options.apiKey)
            .setGcmSenderId(options.senderId)
            .build()
        // La app por defecto no se puede rearmar en el mismo proceso: si ya arrancó con otro proyecto, las opciones
        // nuevas (ya guardadas) valen desde el próximo arranque.
        FirebaseApp.getApps(context).firstOrNull { it.name == FirebaseApp.DEFAULT_APP_NAME }?.let { return it.options == wanted }
        return runCatching { FirebaseApp.initializeApp(context, wanted) }.isSuccess
    }

    override suspend fun token(): String = FirebaseMessaging.getInstance().token.await()

    override suspend fun deleteToken() {
        FirebaseMessaging.getInstance().deleteToken().await()
    }
}

private suspend fun <T> Task<T>.await(): T = suspendCancellableCoroutine { cont ->
    addOnCompleteListener { task ->
        val error = task.exception
        when {
            error != null -> cont.resumeWithException(error)
            task.isCanceled -> cont.cancel()
            else -> cont.resume(task.result)
        }
    }
}

/**
 * Donde llegan los pushes. Firebase lo llama en un hilo suyo (no el principal) y Android da unos segundos:
 * [PushHandler] mide su tiempo y lo que no alcanza lo deja a WorkManager.
 */
@AndroidEntryPoint
class OperatorMessagingService : FirebaseMessagingService() {
    @Inject lateinit var handler: PushHandler

    override fun onMessageReceived(message: RemoteMessage) {
        runBlocking { handler.handle(message.data) }
    }

    /** El token cambió (o es el primero): el backend lo necesita para avisar. Con red y con sesión, por WorkManager. */
    override fun onNewToken(token: String) = PushRegisterWorker.enqueue(this, token)
}

/** Registra en el backend un token nuevo de Firebase. Sin sesión no hay nada que registrar: lo hará el próximo login. */
@HiltWorker
class PushRegisterWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val auth: AuthRepository,
    private val registrar: PushRegistrar,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        val token = inputData.getString(KEY_TOKEN) ?: return Result.success()
        auth.restore()
        val state = auth.state.value
        if (state != AuthState.SignedIn && state != AuthState.DevMode) return Result.success()
        return if (registrar.register(token) == PushStatus.REGISTERED) Result.success() else Result.retry()
    }

    companion object {
        private const val NAME = "registrar-token-push"
        private const val KEY_TOKEN = "token"

        fun enqueue(context: Context, token: String) {
            val request = OneTimeWorkRequestBuilder<PushRegisterWorker>()
                .setInputData(workDataOf(KEY_TOKEN to token))
                .setConstraints(Constraints(requiredNetworkType = NetworkType.CONNECTED))
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
            // El último token manda: uno viejo que no alcanzó a registrarse ya no sirve.
            WorkManager.getInstance(context).enqueueUniqueWork(NAME, ExistingWorkPolicy.REPLACE, request)
        }
    }
}
